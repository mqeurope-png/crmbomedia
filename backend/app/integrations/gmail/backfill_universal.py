"""CRM-GMAIL-BACKFILL — reprocesar el histórico de Gmail con captura universal.

El backfill original (PR #246) solo guardó mails de remitentes que ya eran
contacto del CRM. CRM-GMAIL (#329) retiró ese filtro para el flujo real-time,
pero el histórico anterior sigue sin los mails «huérfanos». Este módulo recorre
un rango de fechas y guarda TODO mail dirigido a un alias ACTIVO del CRM
(`user_email_aliases`), sea o no de un contacto conocido — reutilizando
`service._persist_message` (misma semántica que el push en tiempo real).

CRM-BACKFILL-SENT: la label SENT entra en el default — los mails ENVIADOS
desde Gmail directo se guardan con `direction=outbound`.

Captura de SALIDA universal (10/10/2026): lo que está en SENT se guarda
SIEMPRE, sea cual sea el From. Del 20/07 al 10/10/2026 los enviados desde
direcciones sin registrar en `user_email_aliases` se descartaron en silencio
(866 + 204 mensajes de Bart, entre ellos una oferta completa a un lead). El
informe dice cuántos enviados recupera —o recuperaría, en seco— por remitente
y por usuario atribuido, para mirarlo antes de escribir.

Ritmo y reanudación (10/10/2026): el recorrido va a un paso configurable
(`GMAIL_BACKFILL_RPS`, `ritmo.Ritmo`), reintenta con espera creciente cuando
Gmail pide parar (`403 rateLimitExceeded`, `429`, `5xx`) y, si aun así no
puede seguir, **devuelve el informe** con lo procesado, marcado como incompleto
y con un punto de reanudación (`checkpoint`: etiqueta, página, ids ya hechos
de esa página y fecha más antigua vista) para continuar donde iba. Un mensaje
que falla se cuenta y se lista; no tumba el recorrido. Antes la excepción de
cuota subía hasta arriba y mataba el proceso a los veinte segundos, sin informe.

Se invoca desde `python -m app.integrations.gmail_watch backfill_universal`
(en primer plano) o como job `universal` de `gmail_backfill_jobs`
(`run_universal_job`, cola `gmail:backfill_historic`, worker-gmail).
Idempotente: los mails ya guardados se saltan por dedupe (unique
`(gmail_account_user_id, gmail_message_id)`), así que re-ejecutar con la misma
fecha solo importa lo que faltaba.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy import update as _sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.gmail.backfill import is_not_found_error as _is_not_found
from app.integrations.gmail.ritmo import (
    CuotaGmailAgotada,
    Ritmo,
    descripcion_corta,
    ritmo_desde_ajustes,
)
from app.models.crm import EmailDirection, EmailMessage, GmailBackfillJob, User
from app.services.email_aliases import active_alias_map

logger = logging.getLogger(__name__)

# Etiqueta de origen de los rows importados por este backfill (columna
# email_messages.imported_via), distinta de 'incoming_realtime'.
BACKFILL_IMPORTED_VIA = "historic_backfill_universal"

LABELS_POR_DEFECTO: tuple[str, ...] = ("INBOX", "SPAM", "SENT")
#: Tope del dry-run del job (el CLI tiene el suyo, 500): tres meses de
#: enviados de una cuenta caben de sobra. Al llegar se para con punto de
#: reanudación, así que un seco largo puede hacerse a trozos.
DRY_RUN_LIMIT_JOB = 5000
#: Entradas que se conservan en cada desglose del `result_json`.
TOPE_DESGLOSE = 100
#: Entradas por desglose dentro del checkpoint (vive en `result_json`, TEXT:
#: no puede crecer sin límite con los miles de direcciones que descarta un
#: repaso de INBOX).
TOPE_CHECKPOINT = 500
#: Mensajes con error que se listan en el informe (el resto, en el log).
TOPE_FALLOS = 50

_DESGLOSES = ("discard_by_alias", "enviados_por_remitente", "enviados_por_usuario")


def _recortar(desglose: dict[str, int], tope: int = TOPE_DESGLOSE) -> dict[str, int]:
    if len(desglose) <= tope:
        return dict(desglose)
    orden = sorted(desglose.items(), key=lambda kv: kv[1], reverse=True)
    recortado = dict(orden[:tope])
    resto = orden[tope:]
    recortado[f"(otras {len(resto)} direcciones)"] = sum(c for _, c in resto)
    return recortado


@dataclass
class BackfillReport:
    since: date
    until: date
    labels: list[str]
    dry_run: bool
    imported_linked: int = 0
    imported_orphan: int = 0
    spam: int = 0
    # CRM-BACKFILL-SENT — mensajes capturados con direction=outbound
    # (label SENT). Subconjunto de linked+orphan.
    outbound: int = 0
    skipped_dedupe: int = 0
    skipped_no_alias: int = 0
    # CRM-ADJUNTOS-PURGE — mensajes marcados gmail_status='deleted_gmail'
    # (404 de Gmail con --purge-not-found; aquí solo cubre carreras entre
    # list y get — el purge efectivo del histórico lo hace
    # backfill_attachments, que itera mensajes de la BD).
    purged_not_found: int = 0
    skipped_ndr: int = 0
    errors: int = 0
    # {alias_descartado: nº de mails que iban ahí y NO está configurado}
    discard_by_alias: dict[str, int] = field(default_factory=dict)
    # Enviados importados (o que se importarían, en seco) por remitente y
    # por usuario al que se atribuyen: lo que Bart mira antes de escribir.
    enviados_por_remitente: dict[str, int] = field(default_factory=dict)
    enviados_por_usuario: dict[str, int] = field(default_factory=dict)
    cancelado: bool = False
    duration_seconds: float = 0.0
    # Parada antes de tiempo (cuota agotada, error al listar, Ctrl+C, tope del
    # seco): los recuentos son de lo procesado hasta `hasta_donde`.
    incompleto: bool = False
    tope_seco_alcanzado: bool = False
    motivo_parada: str | None = None
    hasta_donde: str | None = None
    # Este informe continúa uno anterior (punto de reanudación).
    reanudado: bool = False
    # Mensajes sueltos que fallaron: [{id, motivo}], hasta TOPE_FALLOS.
    fallos: list[dict[str, str]] = field(default_factory=list)
    # Lo que costó en Gmail: peticiones hechas y esperas por cuota/5xx.
    peticiones: int = 0
    esperas: int = 0
    segundos_esperando: float = 0.0

    @property
    def total_processed(self) -> int:
        return (
            self.imported_linked
            + self.imported_orphan
            + self.skipped_dedupe
            + self.skipped_no_alias
            + self.skipped_ndr
            + self.errors
        )

    @property
    def imported(self) -> int:
        return self.imported_linked + self.imported_orphan

    @property
    def skipped(self) -> int:
        return self.skipped_dedupe + self.skipped_no_alias + self.skipped_ndr

    @property
    def terminado(self) -> bool:
        return not (self.incompleto or self.cancelado)

    def progress_line(self) -> str:
        return (
            f"  … {self.total_processed} procesados "
            f"(link {self.imported_linked} / orphan {self.imported_orphan} / "
            f"enviados {self.outbound} / dedupe {self.skipped_dedupe} / "
            f"no-alias {self.skipped_no_alias})"
        )

    def _como_datos(self, tope: int) -> dict[str, Any]:
        datos = asdict(self)
        datos["since"] = self.since.isoformat()
        datos["until"] = self.until.isoformat()
        for clave in _DESGLOSES:
            datos[clave] = _recortar(datos[clave], tope)
        return datos

    def como_dict(self) -> dict[str, Any]:
        """Para `gmail_backfill_jobs.result_json` (columna TEXT): los
        desgloses se recortan a las entradas con más mensajes y el resto se
        agrupa, que un repaso de INBOX de un buzón «catch-all» puede
        descartar a miles de direcciones distintas."""
        datos = self._como_datos(TOPE_DESGLOSE)
        datos["total_processed"] = self.total_processed
        datos["imported"] = self.imported
        datos["skipped"] = self.skipped
        datos["terminado"] = self.terminado
        return datos

    def para_checkpoint(self) -> dict[str, Any]:
        """Los recuentos que viajan en el punto de reanudación, con los
        desgloses más anchos que en el informe final (`TOPE_CHECKPOINT`) para
        que al continuar se pierda lo menos posible. La entrada «(otras N
        direcciones)» que crea el recorte sigue como una más al reanudar."""
        return self._como_datos(TOPE_CHECKPOINT)

    @classmethod
    def desde_checkpoint(
        cls, datos: dict[str, Any] | None, *,
        since: date, until: date, labels: Sequence[str], dry_run: bool,
    ) -> BackfillReport:
        """El informe que deja un checkpoint, para seguir acumulando. Rango,
        labels y modo mandan los de la llamada; las marcas de parada se
        limpian (vuelve a estar en marcha)."""
        fijos = {"since", "until", "labels", "dry_run"}
        permitidos = {f.name for f in fields(cls)} - fijos
        valores = {k: v for k, v in (datos or {}).items() if k in permitidos}
        informe = cls(since=since, until=until, labels=list(labels), dry_run=dry_run, **valores)
        informe.reanudado = True
        informe.incompleto = False
        informe.tope_seco_alcanzado = False
        informe.motivo_parada = None
        informe.hasta_donde = None
        informe.cancelado = False
        return informe

    def render(self) -> str:
        mins, secs = divmod(int(self.duration_seconds), 60)
        hours, mins = divmod(mins, 60)
        dur = f"{hours}h {mins:02d}min" if hours else f"{mins}min {secs:02d}s"
        bar = "━" * 41
        if self.cancelado:
            estado = "CANCELADO"
        elif self.incompleto:
            estado = "INCOMPLETO"
        else:
            estado = "completo"
        lines = [
            f"Backfill {'(DRY-RUN) ' if self.dry_run else ''}{estado}"
            f"{' (reanudado)' if self.reanudado else ''}. "
            f"Periodo: {self.since.isoformat()} → {self.until.isoformat()}. "
            f"Labels: {', '.join(self.labels)}.",
            bar,
            f"Total procesados:            {self.total_processed:>8}",
            f"├── Importados con contacto: {self.imported_linked:>8}",
            f"├── Importados huérfanos:    {self.imported_orphan:>8}",
            f"├── Enviados (outbound):     {self.outbound:>8}",
            f"├── Marcados como spam:      {self.spam:>8}",
            f"├── Descartados por dedupe:  {self.skipped_dedupe:>8} (ya existían)",
            f"└── Descartados por alias:   {self.skipped_no_alias:>8} "
            f"(entrantes a alias no configurado)",
        ]
        if self.purged_not_found:
            lines.append(
                f"Marcados como borrados en Gmail: {self.purged_not_found}"
            )
        if self.skipped_ndr:
            lines.append(
                f"    (NDR/bounce ignorados:   {self.skipped_ndr:>8})"
            )
        lines.append(f"Errores:                     {self.errors:>8}")
        lines.append(
            f"Peticiones a Gmail:          {self.peticiones:>8} "
            f"(esperas por cuota: {self.esperas}, {self.segundos_esperando:.0f} s)"
        )
        lines.append(f"Duración:                    {dur:>8}")
        lines.append(bar)
        if not self.terminado:
            lines.append("")
            lines.append(
                f"⚠ Se paró antes de tiempo: {self.motivo_parada or 'cancelado'}."
            )
            lines.append(f"  Llegó hasta: {self.hasta_donde or '(sin posición)'}.")
            lines.append(
                "  Los recuentos son de lo procesado hasta ahí; el recorrido puede "
                "reanudarse donde se quedó."
            )
        if self.fallos:
            lines.append("")
            lines.append(f"Mensajes con error ({self.errors}):")
            for fallo in self.fallos:
                lines.append(f"  {fallo.get('id', '?'):<20} {fallo.get('motivo', '')}")
            if self.errors > len(self.fallos):
                lines.append(
                    f"  … y {self.errors - len(self.fallos)} más "
                    "(en el log, gmail.backfill.get_failed)"
                )
        if self.enviados_por_remitente:
            verbo = "se recuperarían" if self.dry_run else "recuperados"
            lines.append("")
            lines.append(f"Enviados que {verbo}, por remitente:")
            for remitente, count in sorted(
                self.enviados_por_remitente.items(), key=lambda kv: kv[1], reverse=True
            ):
                lines.append(f"  {remitente:<40} {count}")
            lines.append(f"Enviados que {verbo}, por usuario atribuido:")
            for usuario, count in sorted(
                self.enviados_por_usuario.items(), key=lambda kv: kv[1], reverse=True
            ):
                lines.append(f"  {usuario:<40} {count}")
        if self.discard_by_alias:
            lines.append("")
            lines.append("Alias que descartaron mails entrantes (baja por número):")
            for alias, count in sorted(
                self.discard_by_alias.items(), key=lambda kv: kv[1], reverse=True
            ):
                lines.append(
                    f"  {alias:<32} {count} mails descartados "
                    f"(no está en /admin/users)"
                )
            lines.append("")
            lines.append(
                "→ Añade esos alias en /admin/users y vuelve a ejecutar con la "
                "MISMA fecha para reprocesar solo esos (el resto se salta por "
                "dedupe)."
            )
        return "\n".join(lines)


def _load_seen(session: Session) -> set[str]:
    """IDs de Gmail ya almacenados en CUALQUIER cuenta (dedupe barato
    pre-fetch). Hay un solo buzón: un envío del compositor guardado bajo
    otro usuario trae el mismo id que su copia en SENT."""
    from app.integrations.gmail.service import ids_guardados_en_el_buzon  # noqa: PLC0415

    return ids_guardados_en_el_buzon(session)


def _build_query(since: date, until: date) -> str:
    # Gmail `after:` es inclusivo; `before:` es exclusivo → +1 día para que
    # `until` sea inclusivo.
    before = until + timedelta(days=1)
    return f"after:{since:%Y/%m/%d} before:{before:%Y/%m/%d}"


def _anotar_enviado(
    report: BackfillReport, result: EmailMessage, emails_por_usuario: dict[str, str],
) -> None:
    report.outbound += 1
    remitente = (result.from_email or "").strip().lower() or "(sin remitente)"
    report.enviados_por_remitente[remitente] = (
        report.enviados_por_remitente.get(remitente, 0) + 1
    )
    usuario = emails_por_usuario.get(result.created_by_user_id or "", "(sin atribuir)")
    report.enviados_por_usuario[usuario] = report.enviados_por_usuario.get(usuario, 0) + 1


def _anotar_fallo(report: BackfillReport, mid: str, exc: BaseException) -> None:
    """Un mensaje suelto que falló: se cuenta y, hasta el tope, se lista."""
    report.errors += 1
    if len(report.fallos) < TOPE_FALLOS:
        report.fallos.append({"id": mid, "motivo": descripcion_corta(exc)})


def _fecha_de(raw: dict[str, Any]) -> date | None:
    """Día del `internalDate` (ms desde epoch) de un mensaje completo."""
    ms = raw.get("internalDate")
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


@dataclass
class _Posicion:
    """Dónde está el recorrido: la etiqueta en curso, la página (1-based, la
    que apunta `token_actual`), los ids de esa página ya despachados y el día
    más antiguo visto en la etiqueta (para seguir por fecha si el token
    guardado ya no vale)."""

    label: str | None = None
    token_actual: str | None = None
    pagina: int = 0
    hechos: set[str] = field(default_factory=set)
    fecha_mas_antigua: date | None = None

    def nueva_etiqueta(self, label: str) -> None:
        self.label = label
        self.token_actual = None
        self.pagina = 1
        self.hechos = set()
        self.fecha_mas_antigua = None

    def ver(self, fecha: date | None) -> None:
        if fecha is not None and (
            self.fecha_mas_antigua is None or fecha < self.fecha_mas_antigua
        ):
            self.fecha_mas_antigua = fecha


def describir_checkpoint(cp: dict[str, Any] | None) -> str:
    """Una línea legible de un punto de reanudación («etiqueta SENT, página
    3, mensajes hasta el 2026-08-14»)."""
    if not cp:
        return "(sin punto de reanudación)"
    informe = cp.get("informe") or {}
    if informe.get("hasta_donde"):
        return str(informe["hasta_donde"])
    partes = []
    if cp.get("label"):
        partes.append(f"etiqueta {cp['label']}, página {cp.get('pagina') or 1}")
    if cp.get("fecha_mas_antigua"):
        partes.append(f"mensajes hasta el {cp['fecha_mas_antigua']}")
    if cp.get("labels_hechas"):
        partes.append("etiquetas terminadas: " + ", ".join(cp["labels_hechas"]))
    return "; ".join(partes) or "al principio"


def run_backfill_universal(
    session: Session,
    *,
    user_id: str,
    since: date,
    until: date,
    dry_run: bool = False,
    dry_run_limit: int = 500,
    purge_not_found: bool = False,
    # CRM-BACKFILL-SENT: SENT en el default — trae también los mails
    # ENVIADOS desde Gmail directo (direction=outbound).
    labels: Sequence[str] = LABELS_POR_DEFECTO,
    batch_size: int = 100,
    alias_map: dict[str, str] | None = None,
    sleep_between_pages: float = 0.0,
    progress: Callable[[str], None] | None = None,
    on_progress: Callable[[BackfillReport], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    ritmo: Ritmo | None = None,
    reanudar: dict[str, Any] | None = None,
    on_checkpoint: Callable[[dict[str, Any], BackfillReport], None] | None = None,
) -> BackfillReport:
    """Recorre el histórico y persiste (o cuenta, en dry-run) los mails a alias
    activos y TODO lo enviado. Reutiliza `service._persist_message` con
    `emit_activity=False` (no re-dispara workflows ni ensucia timelines con
    correo viejo).

    Las llamadas a Gmail van por `ritmo` (paso + reintentos; por defecto el de
    la configuración). **Nunca sube una excepción de Gmail**: si tras los
    reintentos Gmail sigue negando la cuota, o falla un `list`, o llega el
    tope del seco, o el operador interrumpe, devuelve el informe con
    `incompleto=True`, `motivo_parada` y `hasta_donde`. Un `get` que falla se
    cuenta en `errors`/`fallos` y el recorrido sigue.

    `on_progress` recibe el informe parcial cada 100 mensajes examinados (el
    job lo vuelca en sus contadores); `cancel_check` se consulta en cada
    página y, si devuelve True, el informe vuelve con `cancelado=True`.

    `on_checkpoint(punto, informe)` se llama al terminar cada página y al
    parar; `reanudar` es ese punto para continuar donde se quedó (misma
    llamada: rango, labels y modo). Si Gmail rechaza el `pageToken` guardado,
    la etiqueta sigue por fecha (`until` = el día más antiguo ya visto; el
    dedupe cubre el solape)."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    emit = progress or (lambda _msg: None)
    if alias_map is None:
        alias_map = active_alias_map(session)
    if not alias_map and any(lbl != "SENT" for lbl in labels):
        # Lo ENVIADO se captura sin alias; lo entrante no (su gate los
        # necesita), así que un repaso de INBOX/SPAM sin alias no tiene sentido.
        raise ValueError(
            "Sin alias activos en user_email_aliases. Configura los alias en "
            "/admin/users antes de reprocesar la entrada (solo SENT no los necesita)."
        )
    emails_por_usuario: dict[str, str] = {
        uid: email for uid, email in session.execute(select(User.id, User.email)).all()
    }
    if ritmo is None:
        ritmo = ritmo_desde_ajustes()

    client = gmail_service._client_for(session, user_id)
    labels = [str(lbl) for lbl in labels]
    if reanudar:
        report = BackfillReport.desde_checkpoint(
            reanudar.get("informe"), since=since, until=until, labels=labels, dry_run=dry_run,
        )
        examined = int(reanudar.get("examinados") or 0)
        labels_hechas = [str(lbl) for lbl in (reanudar.get("labels_hechas") or [])]
    else:
        report = BackfillReport(since=since, until=until, labels=labels, dry_run=dry_run)
        examined = 0  # mensajes a los que se les pidió get_message (post-dedupe)
        labels_hechas = []
    # Lo acumulado en pasadas anteriores; los contadores del `ritmo` son de esta.
    # El tope del seco cuenta por pasada: relanzar sigue con el siguiente trozo.
    examinados_al_empezar = examined
    duracion_previa = report.duration_seconds
    peticiones_previas = report.peticiones
    esperas_previas = report.esperas
    segundos_previos = report.segundos_esperando
    seen = _load_seen(session)
    started = time.monotonic()
    pos = _Posicion()
    reanudacion_pendiente = bool(reanudar and reanudar.get("label"))

    def _actualizar_totales() -> None:
        report.duration_seconds = duracion_previa + (time.monotonic() - started)
        report.peticiones = peticiones_previas + ritmo.peticiones
        report.esperas = esperas_previas + ritmo.esperas
        report.segundos_esperando = segundos_previos + ritmo.segundos_esperando

    def _donde() -> str:
        partes: list[str] = []
        if pos.label:
            partes.append(f"etiqueta {pos.label}, página {pos.pagina}")
            if pos.fecha_mas_antigua:
                partes.append(f"mensajes hasta el {pos.fecha_mas_antigua.isoformat()}")
        if labels_hechas:
            partes.append("etiquetas terminadas: " + ", ".join(labels_hechas))
        return "; ".join(partes) or "antes de listar nada"

    def _checkpoint() -> dict[str, Any]:
        return {
            "version": 1,
            "labels_hechas": list(labels_hechas),
            "label": pos.label,
            "page_token": pos.token_actual,
            "pagina": pos.pagina,
            "hechos_en_pagina": sorted(pos.hechos),
            "fecha_mas_antigua": (
                pos.fecha_mas_antigua.isoformat() if pos.fecha_mas_antigua else None
            ),
            "examinados": examined,
            "informe": report.para_checkpoint(),
        }

    def _guardar_punto() -> None:
        if on_checkpoint is not None:
            _actualizar_totales()
            on_checkpoint(_checkpoint(), report)

    def _parar(motivo: str, *, tope: bool = False) -> BackfillReport:
        """Parada antes de tiempo: lo persistido hasta aquí se consolida (lo
        que ya está hecho no se vuelve a pedir al reanudar), y el informe sale
        marcado como incompleto con su punto de reanudación."""
        if not dry_run:
            session.commit()
        report.incompleto = True
        report.tope_seco_alcanzado = tope
        report.motivo_parada = motivo
        report.hasta_donde = _donde()
        _guardar_punto()
        _actualizar_totales()
        return report

    def _cancelar() -> BackfillReport:
        if not dry_run:
            session.commit()
        report.cancelado = True
        report.hasta_donde = _donde()
        _guardar_punto()
        _actualizar_totales()
        return report

    try:
        for label in labels:
            if label in labels_hechas:
                continue
            pos.nueva_etiqueta(label)
            query = _build_query(since, until)
            token_reanudado = False
            if reanudacion_pendiente and reanudar is not None and reanudar.get("label") == label:
                reanudacion_pendiente = False
                pos.token_actual = reanudar.get("page_token") or None
                pos.pagina = max(1, int(reanudar.get("pagina") or 1))
                pos.hechos = {str(m) for m in (reanudar.get("hechos_en_pagina") or [])}
                fecha_cp = reanudar.get("fecha_mas_antigua")
                pos.fecha_mas_antigua = date.fromisoformat(str(fecha_cp)) if fecha_cp else None
                token_reanudado = pos.token_actual is not None
            while True:
                if cancel_check is not None and cancel_check():
                    return _cancelar()
                try:
                    page = ritmo.llamar(
                        lambda q=query, lbl=label, token=pos.token_actual: client.list_messages(
                            query=q,
                            page_size=batch_size,
                            page_token=token,
                            label_ids=[lbl],
                        ),
                        etiqueta=f"list {label} p{pos.pagina}",
                    )
                except CuotaGmailAgotada as exc:
                    return _parar(str(exc))
                except Exception as exc:  # noqa: BLE001
                    if token_reanudado:
                        # El pageToken guardado ya no vale: la etiqueta sigue
                        # por fecha, desde el día más antiguo ya visto (el
                        # solape de ese día lo absorbe el dedupe).
                        logger.warning(
                            "gmail.backfill.resume_token_rechazado label=%s (%s): "
                            "se sigue por fecha hasta %s",
                            label, descripcion_corta(exc), pos.fecha_mas_antigua or until,
                        )
                        token_reanudado = False
                        pos.token_actual = None
                        pos.hechos = set()
                        query = _build_query(since, pos.fecha_mas_antigua or until)
                        continue
                    logger.warning(
                        "gmail.backfill.list_failed label=%s page=%d",
                        label, pos.pagina, exc_info=True,
                    )
                    return _parar(f"error al listar {label}: {descripcion_corta(exc)}")
                token_reanudado = False
                for stub in page.get("messages", []):
                    mid = stub.get("id")
                    if not mid or mid in pos.hechos:
                        continue
                    if mid in seen:
                        report.skipped_dedupe += 1
                        pos.hechos.add(mid)
                        continue
                    try:
                        raw = ritmo.llamar(
                            lambda m=mid: client.get_message(m), etiqueta=f"get {mid}",
                        )
                    except CuotaGmailAgotada as exc:
                        return _parar(str(exc))
                    except Exception as exc:  # noqa: BLE001
                        # CRM-ADJUNTOS-PURGE — 404 entre list y get (mensaje
                        # borrado en Gmail en la ventana). Con el flag, si el
                        # mensaje YA está en nuestra BD lo marcamos huérfano.
                        if purge_not_found and _is_not_found(exc):
                            result_marked = session.execute(
                                _sa_update(EmailMessage)
                                .where(EmailMessage.gmail_message_id == mid)
                                .values(gmail_status="deleted_gmail")
                            )
                            session.commit()
                            if result_marked.rowcount:
                                report.purged_not_found += 1
                                # Al dedupe: que las pasadas de otras labels no
                                # re-marquen (ni re-llamen a Gmail) por este mid.
                                seen.add(mid)
                                pos.hechos.add(mid)
                                continue
                        # Un mensaje suelto que falla no tumba el recorrido:
                        # se cuenta, se lista y se sigue con el siguiente.
                        logger.warning(
                            "gmail.backfill.get_failed msg=%s", mid, exc_info=True
                        )
                        _anotar_fallo(report, mid, exc)
                        pos.hechos.add(mid)
                        continue

                    examined += 1
                    pos.ver(_fecha_de(raw))
                    # El mensaje «es nuestro» si llegó a un alias activo (inbound),
                    # si lo envió un alias activo, o si está en SENT: lo que sale
                    # del buzón conectado es correo escrito por alguien de la
                    # casa, sea cual sea el From. Solo la ENTRADA pasa por el
                    # gate de alias.
                    delivered = gmail_service.compute_delivered_to(raw, alias_map)
                    sender_alias = gmail_service.sender_alias_of(raw, alias_map)
                    enviado = label == "SENT" or gmail_service.es_mensaje_enviado(raw)
                    if delivered is None and sender_alias is None and not enviado:
                        report.skipped_no_alias += 1
                        key = gmail_service.primary_recipient(raw) or "(desconocido)"
                        report.discard_by_alias[key] = (
                            report.discard_by_alias.get(key, 0) + 1
                        )
                    else:
                        if enviado and "SENT" not in (raw.get("labelIds") or []):
                            # Listado bajo SENT pero el full no trae la label (raro):
                            # que `_persist_message` lo vea como enviado igual.
                            raw = {**raw, "labelIds": [*(raw.get("labelIds") or []), "SENT"]}
                        try:
                            if dry_run:
                                result = gmail_service._persist_message(
                                    session,
                                    user_id=user_id,
                                    raw=raw,
                                    gmail_thread_id=raw.get("threadId", ""),
                                    alias_map=alias_map,
                                    dry_run=True,
                                    emit_activity=False,
                                    imported_via=BACKFILL_IMPORTED_VIA,
                                )
                            else:
                                with session.begin_nested():
                                    result = gmail_service._persist_message(
                                        session,
                                        user_id=user_id,
                                        raw=raw,
                                        gmail_thread_id=raw.get("threadId", ""),
                                        alias_map=alias_map,
                                        dry_run=False,
                                        emit_activity=False,
                                        imported_via=BACKFILL_IMPORTED_VIA,
                                    )
                        except IntegrityError:
                            # Carrera con el unique → ya existía. El savepoint se
                            # deshizo solo; contamos dedupe y seguimos.
                            report.skipped_dedupe += 1
                            pos.hechos.add(mid)
                            continue
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "gmail.backfill.persist_failed msg=%s",
                                mid,
                                exc_info=True,
                            )
                            _anotar_fallo(report, mid, exc)
                            pos.hechos.add(mid)
                            continue

                        if result is None:
                            report.skipped_ndr += 1
                        else:
                            if result.contact_id:
                                report.imported_linked += 1
                            else:
                                report.imported_orphan += 1
                            if result.direction == EmailDirection.OUTBOUND:
                                _anotar_enviado(report, result, emails_por_usuario)
                            if result.is_spam:
                                report.spam += 1
                            seen.add(mid)
                    pos.hechos.add(mid)

                    if examined % 100 == 0:
                        emit(report.progress_line())
                        if on_progress is not None:
                            on_progress(report)
                    if dry_run and examined - examinados_al_empezar >= dry_run_limit:
                        return _parar(
                            f"alcanzado el tope del seco ({dry_run_limit} mensajes "
                            "examinados en esta pasada); reanuda para seguir con el "
                            "siguiente trozo o sube el tope",
                            tope=True,
                        )

                if not dry_run:
                    session.commit()
                siguiente = page.get("nextPageToken")
                if not siguiente:
                    labels_hechas.append(label)
                    _guardar_punto()
                    break
                pos.token_actual = siguiente
                pos.pagina += 1
                pos.hechos = set()
                _guardar_punto()
                if sleep_between_pages:
                    time.sleep(sleep_between_pages)
    except KeyboardInterrupt:
        return _parar("interrumpido por el operador")

    _actualizar_totales()
    return report


# ---------------------------------------------------------------------------
# Job `universal` de gmail_backfill_jobs (worker-gmail, cola backfill_historic)
# ---------------------------------------------------------------------------


def _volcar_contadores(job: GmailBackfillJob, report: BackfillReport) -> None:
    job.total_processed = report.total_processed
    job.total_imported = report.imported
    job.total_skipped = report.skipped
    job.total_errors = report.errors


def _json_o_vacio(texto: str | None) -> dict[str, Any]:
    if not texto:
        return {}
    try:
        datos = json.loads(texto)
    except ValueError:
        return {}
    return datos if isinstance(datos, dict) else {}


def checkpoint_del_job(job: GmailBackfillJob) -> dict[str, Any] | None:
    """El punto de reanudación que dejó un job `universal` parado antes de
    tiempo (en `result_json`), o None si terminó entero."""
    cp = _json_o_vacio(job.result_json).get("checkpoint")
    return cp if isinstance(cp, dict) and cp.get("label") else None


def run_universal_job(session: Session, job: GmailBackfillJob) -> None:
    """Modo `universal`: el backfill universal acotado por fechas sobre la
    cuenta Gmail de la organización, con `dry_run` para ver en seco cuántos
    enviados recuperaría por remitente y por usuario. Config (`config_json`):
    `{since, until, labels, dry_run, dry_run_limit, rps?}`. El informe entero va
    a `result_json`. Relanzable: lo ya guardado se salta por dedupe.

    Si el recorrido se para antes de tiempo (cuota agotada tras los
    reintentos, error al listar), el job queda FAILED con el motivo en
    `error_summary` y el punto de reanudación en `result_json.checkpoint`;
    `POST /api/admin/gmail/backfill/{id}/resume` lo vuelve a encolar y este
    mismo runner continúa donde iba. El tope del seco también deja punto de
    reanudación, pero el job queda COMPLETED (no es un fallo)."""
    from app.integrations.gmail.backfill import (  # noqa: PLC0415
        _check_cancel,
        _start_running,
    )
    from app.integrations.gmail.service import (  # noqa: PLC0415
        GmailNotConnectedError,
        GmailScopeMissingError,
    )
    from app.integrations.google_calendar.service import (  # noqa: PLC0415
        get_org_integration,
    )
    from app.models.crm import GmailBackfillStatus  # noqa: PLC0415

    if not _start_running(session, job):
        return
    config = json.loads(job.config_json or "{}")
    since = date.fromisoformat(str(config["since"]))
    until = date.fromisoformat(str(config.get("until") or date.today().isoformat()))
    labels = [str(lbl).upper() for lbl in (config.get("labels") or LABELS_POR_DEFECTO)]
    dry_run = bool(config.get("dry_run"))
    dry_run_limit = int(config.get("dry_run_limit") or DRY_RUN_LIMIT_JOB)
    rps = config.get("rps")
    reanudar = checkpoint_del_job(job)

    def _fallar(motivo: str) -> None:
        job.status = GmailBackfillStatus.FAILED.value
        job.error_summary = motivo
        job.finished_at = datetime.now(UTC)
        session.commit()

    org = get_org_integration(session)
    if org is None or org.status != "active" or not org.connected_by_user_id:
        _fallar("No hay integración Google de la organización activa: conéctala "
                "desde /account antes de rellenar.")
        return
    user_id = org.connected_by_user_id
    logger.info(
        "gmail.backfill.universal started job=%s since=%s until=%s labels=%s dry_run=%s "
        "reanuda=%s",
        job.id, since, until, labels, dry_run, describir_checkpoint(reanudar),
    )

    def _progreso(parcial: BackfillReport) -> None:
        _volcar_contadores(job, parcial)
        job.updated_at = datetime.now(UTC)
        session.commit()

    ultimo_punto: dict[str, Any] = {}

    def _punto(cp: dict[str, Any], parcial: BackfillReport) -> None:
        ultimo_punto["cp"] = cp
        _volcar_contadores(job, parcial)
        job.result_json = json.dumps(
            {"en_curso": True, "checkpoint": cp, "hasta_donde": describir_checkpoint(cp)},
            ensure_ascii=False,
        )
        job.updated_at = datetime.now(UTC)
        session.commit()

    try:
        report = run_backfill_universal(
            session,
            user_id=user_id,
            since=since,
            until=until,
            dry_run=dry_run,
            dry_run_limit=dry_run_limit,
            labels=labels,
            ritmo=ritmo_desde_ajustes(None if rps is None else float(rps)),
            reanudar=reanudar,
            on_progress=_progreso,
            on_checkpoint=_punto,
            cancel_check=lambda: _check_cancel(session, job),
        )
    except (ValueError, GmailNotConnectedError, GmailScopeMissingError) as exc:
        _fallar(str(exc))
        return
    if not dry_run:
        session.commit()
    _volcar_contadores(job, report)
    resultado = report.como_dict()
    if not report.terminado and ultimo_punto.get("cp"):
        resultado["checkpoint"] = ultimo_punto["cp"]
    job.result_json = json.dumps(resultado, ensure_ascii=False)
    ahora = datetime.now(UTC)
    if report.cancelado:
        # `_check_cancel` ya dejó el job en CANCELLED; solo guardamos lo hecho.
        job.finished_at = job.finished_at or ahora
    elif report.incompleto and not report.tope_seco_alcanzado:
        job.status = GmailBackfillStatus.FAILED.value
        job.error_summary = (
            f"{report.motivo_parada}. Llegó hasta: {report.hasta_donde}. "
            f"Reanudable con POST /api/admin/gmail/backfill/{job.id}/resume."
        )
        job.finished_at = ahora
    else:
        job.status = GmailBackfillStatus.COMPLETED.value
        job.error_summary = None
        job.finished_at = ahora
    session.commit()
    logger.info(
        "gmail.backfill.universal done job=%s dry_run=%s terminado=%s imported=%d "
        "outbound=%d dedupe=%d errors=%d peticiones=%d esperas=%d",
        job.id, dry_run, report.terminado, report.imported, report.outbound,
        report.skipped_dedupe, report.errors, report.peticiones, report.esperas,
    )
