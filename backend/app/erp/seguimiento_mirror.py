"""Seguimiento (app) — ESPEJO bidireccional BoHub ↔ hoja de Drive (Fase 2).

BoHub es la fuente de verdad y la hoja «Seguimiento (app)» es una proyección que
se sincroniza en LOS DOS sentidos, casada por id (columna técnica «id», la
última y oculta). Cada pasada del reconcile (botón «Actualizar hoja de Drive» o
el bucle de `worker-sync`):

  1. Lee la hoja y la compara con el SNAPSHOT (lo que escribió la pasada
     anterior) → sabe qué ha tocado una persona.
  2. Mezcla según la PROPIEDAD POR COLUMNA (abajo) y lee de vuelta a BoHub lo
     que le corresponde.
  3. Valida e ingiere las filas tecleadas a mano (Origen = MANUAL); las que no
     validan se marcan («⚠ revisar: …») y NO se ingieren hasta corregirlas.
  4. Casa el histórico con `seguimiento_legacy` por id (bootstrap por contenido
     la primera vez) y lee de vuelta sus ediciones.
  5. Detecta filas borradas a mano: las de BoHub se vuelven a poner siempre (se
     regeneran); las manuales/del histórico se borran LÓGICAMENTE en BoHub
     (recuperables), salvo un borrado MASIVO, que se trata como accidente y se
     restaura.
  6. Tras escribir la hoja, guarda el snapshot nuevo.

Propiedad por columna en las filas de BoHub:

  - «regla Tracking» (BoHub rellena si está vacía; un valor manual manda y se
    lee de vuelta): Cliente, Factura, Factura enviada, Tracking, Nº serie ·
    WhiteRIP. Se leen a `seguimiento_overrides` (NUNCA a los campos del pedido
    que alimentan FACTUSOL / cobro / workflows), salvo Tracking: se escribe en
    `order.tracking_number` SOLO si el pedido no tiene envío Genei (si lo tiene,
    manda Genei y la celda va protegida) — así un tecleo nunca rompe el casado
    del webhook.
  - Nota / Incidencia: BoHub rellena el motivo del bloqueo SOLO si no hay nota
    manual; la manual manda y BoHub nunca la pisa. No se lee de vuelta a la
    pantalla (vive en la hoja).
  - El resto, BLOQUEADAS (solo BoHub): cualquier edición se revierte en la
    siguiente pasada (y la columna va protegida en la hoja).

Las filas manuales son editables enteras (la fusión de #466 sigue igual). El
histórico manual se conserva: solo se le estampa su id y se leen de vuelta sus
ediciones.

Nada de aquí hace commit: el llamador confirma SOLO si la escritura en la hoja
fue bien (si falla, se deshace todo y la pasada siguiente lo repite).
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.erp.models import (
    Order,
    SeguimientoLegacy,
    SeguimientoManual,
    SeguimientoOverride,
    SeguimientoSnapshot,
)
from app.erp.models.seguimiento_mirror import KIND_LEGACY, KIND_MANUAL, KIND_ORDER
from app.erp.seguimiento import (
    ID_INDEX,
    PEDIDOS_DATE_COLUMNS,
    SEGUIMIENTO_COLUMNS_V2,
    parse_sheet_date,
)

logger = logging.getLogger(__name__)

_COL = {name: i for i, name in enumerate(SEGUIMIENTO_COLUMNS_V2)}
_NOTA = _COL["Nota / Incidencia"]
_NUMERO = _COL["Nº pedido"]
_CLIENTE = _COL["Cliente"]
_FECHA = _COL["Fecha"]
_IMPORTE = _COL["Importe"]
_TRACKING = _COL["Tracking"]
_FACTURA_ENVIADA = _COL["Factura enviada"]
#: Columnas de DATOS (todo menos la «id» técnica).
_DATA_WIDTH = ID_INDEX

# --- propiedad por columna (filas de BoHub) -----------------------------------

#: «regla Tracking»: BoHub rellena si está vacía; lo manual manda y se lee de
#: vuelta a BoHub.
REGLA_TRACKING: tuple[str, ...] = (
    "Cliente", "Factura", "Factura enviada", "Tracking", "Nº serie · WhiteRIP",
)
#: «libre con relleno»: BoHub rellena si no hay nada manual; lo manual manda.
LIBRES: tuple[str, ...] = ("Nota / Incidencia",)
EDITABLES: tuple[str, ...] = (*REGLA_TRACKING, *LIBRES)
#: Solo BoHub (se protegen en la hoja). Incluye la «id».
BLOQUEADAS: tuple[str, ...] = tuple(c for c in SEGUIMIENTO_COLUMNS_V2 if c not in EDITABLES)

#: Columna de la hoja → clave del override (= clave del dict de fila de BoHub).
#: Tracking no va aquí: se lee de vuelta al pedido (si no hay envío Genei).
OVERRIDE_COLUMNS: dict[str, str] = {
    "Cliente": "cliente",
    "Factura": "factura",
    "Factura enviada": "factura_enviada",
    "Nº serie · WhiteRIP": "serie_whiterip",
    "Nota / Incidencia": "nota_incidencia",
}
#: Overrides que también ve la pantalla de Seguimiento (y el Excel). La Nota
#: solo vive en la hoja.
SCREEN_OVERRIDES: tuple[str, ...] = ("cliente", "factura", "factura_enviada", "serie_whiterip")
#: Overrides que son FECHAS (se guardan en ISO si se entienden).
_DATE_OVERRIDES = ("factura_enviada",)

# --- marca «⚠ revisar» (filas que no validan) --------------------------------

REVISAR_PREFIX = "⚠ revisar:"
_REVISAR_RE = re.compile(r"\s*\[⚠ revisar:[^\]]*\]")

# --- borrado masivo -----------------------------------------------------------

#: Si desaparecen de golpe más filas manuales/del histórico que esto, no es una
#: persona borrando: es una hoja vaciada o una lectura rota → se RESTAURAN.
MASS_DELETE_MIN = 20
MASS_DELETE_RATIO = 0.05

_SHEETS_EPOCH = datetime(1899, 12, 30).date()


def _texto(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _ahora() -> datetime:
    return datetime.now(UTC)


def strip_revisar(nota: Any) -> str:
    """La nota sin las marcas «[⚠ revisar: …]» de la app (el texto del usuario
    y las demás marcas quedan tal cual)."""
    return _REVISAR_RE.sub("", _texto(nota)).strip()


def add_revisar(nota: Any, problemas: list[str]) -> str:
    """La nota con UNA marca «[⚠ revisar: …]» con todo lo que falla."""
    base = strip_revisar(nota)
    marca = f"[{REVISAR_PREFIX} {'; '.join(problemas)}]"
    return f"{base} {marca}".strip()


def _fila_id(fila: list[Any]) -> str:
    return _texto(fila[ID_INDEX]) if len(fila) > ID_INDEX else ""


def _con_id(fila: list[Any], row_id: str) -> list[Any]:
    """La fila con su id en la columna técnica (sin tocar ninguna celda de
    datos; lo tecleado más allá de la última columna se conserva)."""
    r = list(fila)
    if len(r) <= ID_INDEX:
        r = r + [""] * (ID_INDEX - len(r)) + [row_id]
    else:
        r[ID_INDEX] = row_id
    return r


def _canon(value: Any) -> str:
    """Valor de celda comparable entre lecturas: el número 121, 121.0 y «121»
    valen lo mismo; el texto, sin espacios en los extremos."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return repr(value)
    return _texto(value)


def canon_datos(fila: list[Any]) -> list[str]:
    """Las celdas de DATOS de una fila (sin la «id»), canónicas y sin los huecos
    del final — la API de Sheets recorta las celdas vacías finales."""
    celdas = [_canon(c) for c in list(fila)[:_DATA_WIDTH]]
    while celdas and not celdas[-1]:
        celdas.pop()
    return celdas


def _valores_bohub(row: dict[str, Any]) -> list[Any]:
    """La fila tal como la pintaría BoHub SIN nada manual (sus valores
    originales, antes de aplicar overrides)."""
    from app.erp.drive_managed import dates_to_serial  # noqa: PLC0415
    from app.erp.seguimiento import row_to_pedidos_values  # noqa: PLC0415

    base = dict(row)
    base.update(row.get("bohub") or {})
    return dates_to_serial([row_to_pedidos_values(base)], PEDIDOS_DATE_COLUMNS)[0]


def _clave_legacy(fila: list[Any]) -> tuple[str, str]:
    """(Nº, Cliente) de una fila del histórico, para el casado de reserva."""
    num = fila[_NUMERO] if len(fila) > _NUMERO else ""
    cli = fila[_CLIENTE] if len(fila) > _CLIENTE else ""
    return _canon(num).casefold(), _canon(cli).casefold()


def _mismo(col: int, a: Any, b: Any) -> bool:
    """¿La hoja dice lo mismo que el snapshot en esa columna? Fechas por su
    valor; lo demás, exacto (corregir solo mayúsculas también es editar)."""
    if col in PEDIDOS_DATE_COLUMNS:
        return _a_iso(a) == _a_iso(b)
    if col == _NOTA:
        return strip_revisar(a) == strip_revisar(b)
    return _canon(a) == _canon(b)


def _a_iso(value: Any) -> str:
    """Una celda de fecha → ISO («» si vacía; el texto tal cual si no se
    entiende)."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (_SHEETS_EPOCH + timedelta(days=int(math.floor(value)))).isoformat()
    parsed = parse_sheet_date(value)
    return parsed.isoformat() if parsed else _texto(value)


def _override_value(col_name: str, raw: Any) -> tuple[str, bool]:
    """(valor a guardar, ¿válido?). Una fecha se guarda en ISO si se entiende;
    si no, se guarda el texto tal cual (no se pierde) pero NO vale."""
    if col_name == "Factura enviada":
        iso = _a_iso(raw)
        if not iso:
            return "", True
        try:
            datetime.fromisoformat(iso)
            return iso, True
        except ValueError:
            return iso, False
    return _texto(raw), True


def _es_iso(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


# --- overrides (lectura de vuelta a BoHub) ------------------------------------


def load_overrides(
    session: Session, order_ids: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    """{order_id: {clave: valor}} de los valores escritos a mano."""
    q = select(SeguimientoOverride)
    if order_ids is not None:
        if not order_ids:
            return {}
        q = q.where(SeguimientoOverride.order_id.in_(order_ids))
    out: dict[str, dict[str, str]] = {}
    for ov in session.scalars(q):
        out.setdefault(ov.order_id, {})[ov.column_key] = ov.value
    return out


def apply_overrides_to_rows(session: Session, rows: list[dict[str, Any]]) -> None:
    """Pantalla / Excel / hoja: aplica a cada fila de BoHub lo escrito a mano en
    sus columnas editables (Cliente, Factura, Factura enviada, Nº serie ·
    WhiteRIP). Guarda los valores de BoHub en `row["bohub"]` (para poder volver
    a ellos si se borra el valor manual) y la lista de columnas a mano en
    `row["overrides"]`. La Nota manual NO se aplica aquí: solo va a la hoja."""
    try:
        overrides = load_overrides(session)
    except Exception:  # noqa: BLE001 — sin la tabla (BD sin migrar) no se rompe la vista
        logger.warning("seguimiento: no se pudieron leer los overrides", exc_info=True)
        return
    for row in rows:
        row["bohub"] = {k: row.get(k) for k in (*SCREEN_OVERRIDES, "nota_incidencia")}
        ov = overrides.get(str(row.get("id") or ""), {})
        aplicadas: list[str] = []
        for key in SCREEN_OVERRIDES:
            if key not in ov:
                continue
            valor = ov[key]
            if key in _DATE_OVERRIDES and valor and not _es_iso(valor):
                continue  # una fecha que no se entiende no entra en la pantalla
            row[key] = (valor or None) if key in _DATE_OVERRIDES else valor
            aplicadas.append(key)
        row["overrides"] = aplicadas


# --- validación de filas manuales ---------------------------------------------


def validar_manual(fila: list[Any]) -> list[str]:
    """Lo que impide ingerir una fila tecleada a mano (lista vacía = válida):

      - falta Nº pedido Y Cliente (no se sabría qué es);
      - una columna de fecha con algo que no es una fecha;
      - un Importe que no es un número.

    Una fila sin Fecha SÍ vale (#466: sube arriba para que no se pierda)."""
    from app.erp.drive_managed import _importe  # noqa: PLC0415

    problemas: list[str] = []
    if not _texto(fila[_NUMERO]) and not _texto(fila[_CLIENTE]):
        problemas.append("falta Nº pedido o Cliente")
    for col in PEDIDOS_DATE_COLUMNS:
        v = fila[col] if len(fila) > col else ""
        if v in (None, "") or (isinstance(v, (int, float)) and not isinstance(v, bool)):
            continue
        if parse_sheet_date(v) is None:
            problemas.append(f"{SEGUIMIENTO_COLUMNS_V2[col]} no es una fecha")
    imp = fila[_IMPORTE] if len(fila) > _IMPORTE else ""
    if _texto(imp) and _importe(imp) is None:
        problemas.append("Importe no es un número")
    return problemas


# --- el reconcile ---------------------------------------------------------------


@dataclass
class Espejo:
    """Estado de UNA pasada del reconcile. Se crea al principio de
    `push_managed_tabs`, se alimenta en cada fase y guarda el snapshot al final."""

    session: Session
    dry_run: bool = False
    snapshot: dict[str, tuple[str, list[Any]]] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=lambda: {
        "bootstrap": False,
        "ediciones_leidas": 0,
        "overrides_guardados": 0,
        "overrides_borrados": 0,
        "tracking_leidos": 0,
        "tracking_genei_ignorados": 0,
        "manuales_nuevas": 0,
        "manuales_invalidas": 0,
        "historico_ids_asignados": 0,
        "historico_editadas": 0,
        "historico_nuevas": 0,
        "historico_duplicados_suprimidos": 0,
        "borradas": 0,
        "restauradas": 0,
        "borrado_masivo": False,
        "historico_no_encontradas": 0,
    })
    #: ids leídos en la hoja en esta pasada (cualquier zona).
    ids_leidos: set[str] = field(default_factory=set)
    #: tipo de cada id que se va a PINTAR (para el snapshot).
    tipos: dict[str, str] = field(default_factory=dict)
    #: filas de BoHub (por id) con envío Genei: su Tracking va protegido.
    genei_ids: set[str] = field(default_factory=set)
    #: overrides vigentes de las filas de BoHub de esta pasada.
    _overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    #: registros del histórico por el id que llevan en la hoja.
    _legacy_por_sheet_id: dict[str, SeguimientoLegacy] = field(default_factory=dict)

    # -- carga ---------------------------------------------------------------

    @classmethod
    def cargar(cls, session: Session, *, dry_run: bool = False) -> Espejo:
        esp = cls(session=session, dry_run=dry_run)
        try:
            for s in session.scalars(select(SeguimientoSnapshot)):
                try:
                    vals = json.loads(s.values_json or "[]")
                except (TypeError, ValueError):
                    vals = []
                esp.snapshot[s.row_id] = (s.kind, vals if isinstance(vals, list) else [])
        except Exception:  # noqa: BLE001 — BD sin migrar: espejo en modo arranque
            logger.warning("seguimiento: sin snapshot del espejo", exc_info=True)
        esp.stats["bootstrap"] = not esp.snapshot
        return esp

    # -- 1) filas de BoHub: lectura de vuelta ----------------------------------

    def leer_filas_bohub(
        self, valores: list[list[Any]], rows: list[dict[str, Any]],
        completados: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Detecta lo que una persona editó en las columnas editables de las
        filas de BoHub (hoja ≠ snapshot), lo lee de vuelta a BoHub y devuelve las
        filas (copias) listas para pintar con lo manual aplicado.

        Sin snapshot de esa fila (primera pasada, fila nueva) NO se infiere nada:
        no hay con qué comparar, así que se pinta lo de BoHub."""
        from app.erp.drive_managed import (  # noqa: PLC0415
            cabecera_de,
            es_fila_completado,
            is_manual_row,
            is_separator,
            live_zone,
            realinear_fila,
            static_block,
        )

        todas = [*rows, *completados]
        ids_bohub = {str(r.get("id") or "") for r in todas} - {""}
        self.genei_ids = {str(r.get("id")) for r in todas if r.get("envio_genei")}

        header = cabecera_de(valores)
        en_hoja: dict[str, list[Any]] = {}
        for cruda in live_zone(valores):
            fila = realinear_fila(cruda, header)
            rid = _fila_id(fila)
            if rid:
                self.ids_leidos.add(rid)
            if rid in ids_bohub and not is_manual_row(fila):
                en_hoja.setdefault(rid, fila)
        for cruda in static_block(valores):
            fila = list(cruda)
            if is_separator(fila):
                continue
            rid = _fila_id(fila)
            if rid:
                self.ids_leidos.add(rid)
            if rid in ids_bohub and es_fila_completado(fila):
                en_hoja.setdefault(rid, fila)

        tracking_nuevos: dict[str, str] = {}
        previos = load_overrides(self.session, sorted(en_hoja)) if en_hoja else {}
        por_id = {str(r.get("id")): r for r in todas if r.get("id")}
        for rid, fila in en_hoja.items():
            snap = self.snapshot.get(rid)
            if snap is None or snap[0] != KIND_ORDER or not snap[1]:
                continue
            anterior = snap[1]
            de_bohub = _valores_bohub(por_id[rid])
            for nombre in EDITABLES:
                col = _COL[nombre]
                ahora = fila[col] if len(fila) > col else ""
                antes = anterior[col] if len(anterior) > col else ""
                if _mismo(col, ahora, antes):
                    continue
                # Blindaje contra un snapshot desfasado (p. ej. la pasada anterior
                # escribió la hoja pero no llegó a confirmar): si la columna NO
                # tiene valor manual y la hoja dice lo mismo que BoHub AHORA, eso
                # lo escribió BoHub — no una persona. Así un cambio de BoHub nunca
                # se convierte en un «valor manual» pegajoso.
                clave = OVERRIDE_COLUMNS.get(nombre)
                sin_manual = clave is None or clave not in previos.get(rid, {})
                if sin_manual and _mismo(col, ahora, de_bohub[col]):
                    continue
                self.stats["ediciones_leidas"] += 1
                if nombre == "Tracking":
                    nuevo = self._leer_tracking(rid, ahora)
                    if nuevo is not None:
                        tracking_nuevos[rid] = nuevo
                    continue
                self._leer_override(rid, OVERRIDE_COLUMNS[nombre], nombre, ahora)

        self._overrides = load_overrides(self.session, sorted(ids_bohub)) if ids_bohub else {}
        return (
            [self._pintar(r, tracking_nuevos) for r in rows],
            [self._pintar(r, tracking_nuevos) for r in completados],
        )

    def _leer_tracking(self, order_id: str, valor: Any) -> str | None:
        """Tracking manual → `order.tracking_number`, SOLO sin envío Genei (con
        Genei manda Genei). Vacío no borra: BoHub vuelve a rellenar."""
        from app.erp.integrations.genei.service import (  # noqa: PLC0415
            shipment_code_of_order,
        )

        nuevo = _texto(valor)[:64]
        if not nuevo:
            return None
        order = self.session.get(Order, order_id)
        if order is None:
            return None
        if order_id in self.genei_ids or shipment_code_of_order(order):
            self.stats["tracking_genei_ignorados"] += 1
            return None
        if nuevo == (order.tracking_number or ""):
            return nuevo
        self.stats["tracking_leidos"] += 1
        if not self.dry_run:
            from app.core.audit import record_event  # noqa: PLC0415

            anterior = order.tracking_number
            order.tracking_number = nuevo
            record_event(
                self.session, action="erp.order_tracking_updated", target_type="order",
                target_id=order.id, actor=None, actor_email="drive-sheet",
                metadata={"order_number": order.order_number, "tracking_number": nuevo,
                          "anterior": anterior, "source": "seguimiento_drive"},
            )
        return nuevo

    def _leer_override(self, order_id: str, clave: str, nombre: str, valor: Any) -> None:
        guardar, _valido = _override_value(nombre, valor)
        if clave == "nota_incidencia":
            guardar = strip_revisar(valor)
        existente = self.session.scalar(select(SeguimientoOverride).where(
            SeguimientoOverride.order_id == order_id,
            SeguimientoOverride.column_key == clave,
        ))
        if not guardar:
            # Vacío = «que BoHub vuelva a rellenar»: se quita el valor manual.
            if existente is not None:
                self.stats["overrides_borrados"] += 1
                if not self.dry_run:
                    self.session.delete(existente)
                    self.session.flush()
            return
        self.stats["overrides_guardados"] += 1
        if self.dry_run:
            return
        if existente is None:
            self.session.add(SeguimientoOverride(
                order_id=order_id, column_key=clave, value=guardar,
            ))
        else:
            existente.value = guardar
        self.session.flush()

    def _pintar(self, row: dict[str, Any], tracking_nuevos: dict[str, str]) -> dict[str, Any]:
        """Copia de la fila con lo manual aplicado sobre los valores de BoHub."""
        out = dict(row)
        originales = row.get("bohub") or {}
        for key in (*SCREEN_OVERRIDES, "nota_incidencia"):
            if key in originales:
                out[key] = originales[key]
        rid = str(row.get("id") or "")
        problemas: list[str] = []
        for key, valor in self._overrides.get(rid, {}).items():
            out[key] = valor
            if key in _DATE_OVERRIDES and valor and not _es_iso(valor):
                problemas.append("Factura enviada no es una fecha")
        if rid in tracking_nuevos:
            out["tracking"] = tracking_nuevos[rid]
        if problemas:
            out["nota_incidencia"] = add_revisar(out.get("nota_incidencia"), problemas)
        if rid:
            self.tipos[rid] = KIND_ORDER
        return out

    # -- 2) filas manuales: validar, marcar, ingerir ---------------------------

    def procesar_manuales(
        self, manuales: list[list[Any]], ids_pedidos: set[str],
    ) -> list[list[Any]]:
        """Cada fila tecleada a mano (ya fusionada por #466): se valida. Si vale,
        recibe id (el suyo, el del pedido con el que casa, o uno nuevo) y se
        ingiere en `seguimiento_manual`. Si no vale, se marca «⚠ revisar: …» y
        NO se ingiere (si ya estaba ingerida, BoHub conserva su última versión
        válida); la fila sigue en la hoja tal cual, sin perder nada."""
        existentes = {m.row_id: m for m in self.session.scalars(select(SeguimientoManual))}
        vistos: set[str] = set()
        out: list[list[Any]] = []
        for original in manuales:
            fila = _con_id(original, _fila_id(original))
            fila[_NOTA] = strip_revisar(fila[_NOTA])
            rid = _fila_id(fila)
            if rid in vistos:        # id duplicado (fila copiada): cuenta como nueva
                rid = ""
            if rid:
                self.ids_leidos.add(rid)
            problemas = validar_manual(fila)
            if problemas:
                fila[_NOTA] = add_revisar(fila[_NOTA], problemas)
                fila = _con_id(fila, rid)
                self.stats["manuales_invalidas"] += 1
            else:
                if not rid:
                    rid = str(uuid4())
                    self.stats["manuales_nuevas"] += 1
                fila = _con_id(fila, rid)
                self._ingerir_manual(existentes, rid, fila,
                                     rid if rid in ids_pedidos else None)
            if rid:
                vistos.add(rid)
                # Una fila manual que lleva el id de un pedido (casada, #466) ES la
                # fila de ese pedido en la hoja: se trata como manual (editable
                # entera, sin proteger), no como fila de BoHub.
                self.tipos[rid] = KIND_MANUAL
            out.append(fila)
        return out

    def _ingerir_manual(
        self, existentes: dict[str, SeguimientoManual], rid: str,
        fila: list[Any], order_id: str | None,
    ) -> None:
        if self.dry_run:
            return
        valores = json.dumps(list(fila), ensure_ascii=False, default=str)
        m = existentes.get(rid)
        if m is None:
            m = SeguimientoManual(row_id=rid, order_id=order_id, values_json=valores)
            self.session.add(m)
            existentes[rid] = m
            return
        if m.values_json != valores:
            m.values_json = valores
        if m.order_id != order_id:
            m.order_id = order_id
        if m.deleted_at is not None:
            m.deleted_at = None

    # -- 3) histórico: id + lectura de vuelta + deduplicado -------------------

    def legacy_activo(self) -> bool:
        """El espejo del histórico arranca cuando el histórico ya está importado
        en `seguimiento_legacy` (Fase 1). Sin eso, el histórico se conserva tal
        cual (como antes)."""
        try:
            return bool(self.session.scalar(select(func.count(SeguimientoLegacy.id))))
        except Exception:  # noqa: BLE001
            return False

    def procesar_historico(
        self, filas: list[list[Any]], ids_vivos: set[str],
    ) -> list[list[Any]]:
        """Casa cada fila del histórico con su registro de `seguimiento_legacy`
        (por id; la primera vez, por contenido), le estampa el id, lee de vuelta
        sus ediciones y quita las que son el MISMO pedido que ya se pinta arriba
        (los `confirmed` cuyo pedido está vivo/completado: sin duplicar)."""
        registros = list(self.session.scalars(
            select(SeguimientoLegacy).order_by(SeguimientoLegacy.row_index)
        ))
        por_sheet_id: dict[str, SeguimientoLegacy] = {}
        sheet_id_de: dict[str, str] = {}
        for rec in registros:
            sid = rec.id
            if rec.match_status == "confirmed" and rec.matched_order_id \
                    and rec.matched_order_id not in por_sheet_id:
                sid = rec.matched_order_id
            por_sheet_id[sid] = rec
            sheet_id_de[rec.id] = sid

        reclamados: set[str] = set()
        por_contenido: dict[tuple[str, ...], list[SeguimientoLegacy]] = {}
        por_clave: dict[tuple[str, str], list[SeguimientoLegacy]] = {}
        for rec in registros:
            try:
                raw = json.loads(rec.raw_json or "[]")
            except (TypeError, ValueError):
                raw = []
            por_contenido.setdefault(tuple(canon_datos(raw)), []).append(rec)
            clave = (_canon(rec.numero_raw).casefold(), _canon(rec.cliente_raw).casefold())
            if any(clave):  # misma clave que `_clave_legacy` sobre la fila de la hoja
                por_clave.setdefault(clave, []).append(rec)

        def _libre(lista: list[SeguimientoLegacy]) -> SeguimientoLegacy | None:
            return next((r for r in lista if r.id not in reclamados), None)

        siguiente_idx = (max((r.row_index for r in registros), default=-1) + 1)
        out: list[list[Any]] = []
        for fila in filas:
            rid = _fila_id(fila)
            rec = por_sheet_id.get(rid) if rid else None
            if rec is not None and rec.id in reclamados:
                rec = None       # id duplicado en la hoja: la segunda, como nueva
            if rec is None:
                rec = (_libre(por_contenido.get(tuple(canon_datos(fila)), []))
                       or _libre(por_clave.get(_clave_legacy(fila), [])))
                if rec is not None:
                    self.stats["historico_ids_asignados"] += 1
            if rec is None:
                rec = self._legacy_nueva(fila, siguiente_idx)
                siguiente_idx += 1
                sheet_id_de[rec.id] = rec.id
            reclamados.add(rec.id)
            sid = sheet_id_de.get(rec.id, rec.id)
            self.ids_leidos.add(sid)
            self._leer_legacy(rec, fila)
            if sid in ids_vivos or (rec.matched_order_id and rec.matched_order_id in ids_vivos
                                    and rec.match_status == "confirmed"):
                self.stats["historico_duplicados_suprimidos"] += 1
                continue
            self.tipos[sid] = KIND_LEGACY
            out.append(_con_id(fila, sid))
        self._legacy_por_sheet_id = por_sheet_id
        # Registros que no aparecen en la hoja (borrados antes de activar el
        # espejo, o que no se han podido casar): ni se pintan ni se borran; se
        # informa para poder revisarlos.
        self.stats["historico_no_encontradas"] = sum(
            1 for r in registros if r.id not in reclamados and r.deleted_at is None)
        return out

    def _legacy_nueva(self, fila: list[Any], idx: int) -> SeguimientoLegacy:
        """Una fila añadida a mano en el histórico (sin registro): se ingiere."""
        self.stats["historico_nuevas"] += 1
        rec = SeguimientoLegacy(
            id=str(uuid4()), row_index=idx,
            numero_raw=_texto(fila[_NUMERO] if len(fila) > _NUMERO else "")[:64],
            cliente_raw=_texto(fila[_CLIENTE] if len(fila) > _CLIENTE else "")[:300],
            fecha_raw=_canon(fila[_FECHA] if len(fila) > _FECHA else "")[:64],
            raw_json=json.dumps(list(fila)[:_DATA_WIDTH], ensure_ascii=False, default=str),
            match_status="synthetic",
            match_note="añadida a mano en el histórico de la hoja (espejo)",
        )
        if not self.dry_run:
            self.session.add(rec)
        return rec

    def _leer_legacy(self, rec: SeguimientoLegacy, fila: list[Any]) -> None:
        """Lee de vuelta a BoHub lo editado a mano en una fila del histórico."""
        if self.dry_run:
            return
        if rec.deleted_at is not None:
            rec.deleted_at = None          # volvió a la hoja (p. ej. deshacer)
        try:
            raw = json.loads(rec.raw_json or "[]")
        except (TypeError, ValueError):
            raw = []
        if canon_datos(raw) == canon_datos(fila):
            return
        self.stats["historico_editadas"] += 1
        rec.raw_json = json.dumps(list(fila)[:_DATA_WIDTH], ensure_ascii=False, default=str)
        rec.numero_raw = _texto(fila[_NUMERO] if len(fila) > _NUMERO else "")[:64]
        rec.cliente_raw = _texto(fila[_CLIENTE] if len(fila) > _CLIENTE else "")[:300]
        rec.fecha_raw = _canon(fila[_FECHA] if len(fila) > _FECHA else "")[:64]

    # -- 4) borrados a mano ----------------------------------------------------

    def borrados(self, ids_pintados: set[str]) -> tuple[list[list[Any]], list[list[Any]]]:
        """Filas manuales/del histórico que estaban en la hoja (snapshot) y ya no
        están: borradas a mano → borrado LÓGICO en BoHub. Si son demasiadas de
        golpe (hoja vaciada / lectura rota), NO se borra nada y se RESTAURAN.
        Devuelve (manuales a restaurar, histórico a restaurar). Las filas de BoHub
        no pasan por aquí: se regeneran siempre."""
        previas = {rid: kind for rid, (kind, _v) in self.snapshot.items()
                   if kind in (KIND_MANUAL, KIND_LEGACY)}
        faltan = [rid for rid in previas
                  if rid not in self.ids_leidos and rid not in ids_pintados]
        if not faltan:
            return [], []
        umbral = max(MASS_DELETE_MIN, math.ceil(MASS_DELETE_RATIO * len(previas)))
        manuales = {m.row_id: m for m in self.session.scalars(
            select(SeguimientoManual).where(SeguimientoManual.row_id.in_(faltan)))}
        legacy_por_sid = self._legacy_por_sheet_id

        if len(faltan) > umbral:
            self.stats["borrado_masivo"] = True
            self.stats["restauradas"] = len(faltan)
            logger.warning(
                "seguimiento: %d filas manuales/del histórico desaparecieron de golpe "
                "(umbral %d): se tratan como accidente y se RESTAURAN", len(faltan), umbral)
            rest_man: list[list[Any]] = []
            rest_leg: list[list[Any]] = []
            for rid in faltan:
                if rid in manuales:
                    fila = json.loads(manuales[rid].values_json or "[]")
                    rest_man.append(_con_id(fila, rid))
                    self.tipos[rid] = KIND_MANUAL
                elif rid in legacy_por_sid:
                    rec = legacy_por_sid[rid]
                    fila = json.loads(rec.raw_json or "[]")
                    rest_leg.append(_con_id(fila, rid))
                    self.tipos[rid] = KIND_LEGACY
            return rest_man, rest_leg

        ahora = _ahora()
        for rid in faltan:
            self.stats["borradas"] += 1
            if self.dry_run:
                continue
            if rid in manuales and manuales[rid].deleted_at is None:
                manuales[rid].deleted_at = ahora
            elif rid in legacy_por_sid and legacy_por_sid[rid].deleted_at is None:
                legacy_por_sid[rid].deleted_at = ahora
        return [], []

    # -- 5) snapshot -----------------------------------------------------------

    def guardar_snapshot(self, grid: list[list[Any]]) -> None:
        """Tras escribir la hoja: la foto nueva, por id. Las filas de BoHub y
        las manuales guardan sus valores (para detectar ediciones); las del
        histórico solo su presencia (sus ediciones se comparan con
        `seguimiento_legacy`)."""
        if self.dry_run:
            return
        from app.erp.drive_managed import es_cabecera, is_separator  # noqa: PLC0415

        nuevas: dict[str, tuple[str, str]] = {}
        for fila in grid:
            if not fila or is_separator(list(fila)) or es_cabecera(fila):
                continue
            rid = _fila_id(fila)
            if not rid or rid in nuevas:
                continue
            kind = self.tipos.get(rid)
            if kind is None:
                continue
            valores = "[]" if kind == KIND_LEGACY else json.dumps(
                list(fila), ensure_ascii=False, default=str)
            nuevas[rid] = (kind, valores)

        actuales = {s.row_id: s for s in self.session.scalars(select(SeguimientoSnapshot))}
        for rid, (kind, valores) in nuevas.items():
            s = actuales.get(rid)
            if s is None:
                self.session.add(SeguimientoSnapshot(row_id=rid, kind=kind, values_json=valores))
            elif s.kind != kind or s.values_json != valores:
                s.kind, s.values_json = kind, valores
        sobran = [rid for rid in actuales if rid not in nuevas]
        if sobran:
            self.session.execute(delete(SeguimientoSnapshot).where(
                SeguimientoSnapshot.row_id.in_(sobran)))
        self.session.flush()


# --- protección de la hoja (rangos protegidos + validación + marca naranja) -----

#: Descripción (y prefijo) de las protecciones del espejo: así cada pasada
#: localiza y sustituye las SUYAS sin tocar las que haya puesto una persona.
PROTECT_DESC_PREFIX = "BoHub · espejo"
PROTECT_DESC = f"{PROTECT_DESC_PREFIX}: columnas bloqueadas (solo BoHub)"
PROTECT_DESC_GENEI = f"{PROTECT_DESC_PREFIX}: Tracking de Genei (solo BoHub)"
#: Regla de formato condicional: fila en naranja si su Nota lleva «⚠ revisar».
#: (Fórmulas por API: nombres en inglés y comas, sea cual sea el idioma.)
REVISAR_FORMULA = '=REGEXMATCH($R2,"\\[⚠ revisar")'
_NARANJA = {"red": 1.0, "green": 0.8, "blue": 0.6}


def _es_regla_revisar(regla: dict[str, Any]) -> bool:
    """¿Es la regla naranja del espejo (por su fórmula)?"""
    cond = (regla.get("booleanRule") or {}).get("condition") or {}
    return any(v.get("userEnteredValue") == REVISAR_FORMULA for v in cond.get("values") or [])


def _tramos(cols: list[int]) -> list[tuple[int, int]]:
    """Índices de columna → tramos contiguos [inicio, fin)."""
    out: list[tuple[int, int]] = []
    for c in sorted(cols):
        if out and out[-1][1] == c:
            out[-1] = (out[-1][0], c + 1)
        else:
            out.append((c, c + 1))
    return out


def _grupos(filas: list[int]) -> list[tuple[int, int]]:
    """Índices de fila → grupos contiguos [inicio, fin)."""
    return _tramos(filas)


#: Tramos de columnas bloqueadas (en las filas de BoHub).
_TRAMOS_BLOQUEADOS = _tramos([_COL[c] for c in BLOQUEADAS])


def _listas_cerradas() -> dict[int, list[str]]:
    """Columnas de valor cerrado → sus valores válidos (para el desplegable de las
    filas manuales; en las de BoHub van protegidas)."""
    from app.erp.seguimiento import (  # noqa: PLC0415
        COBRO_LABELS,
        NO_APLICA,
        PREPARACION_LABELS,
        SITUACION_LABELS,
        envio_vocabulary,
    )

    return {
        _COL["Situación"]: list(SITUACION_LABELS.values()),
        _COL["Preparación"]: [*PREPARACION_LABELS.values(), NO_APLICA, "—"],
        # Transporte + escaneo real del transportista («Pendiente de entrada en
        # red», «En reparto»…), el vocabulario con el que BoHub rellena Envío.
        _COL["Envío"]: [*envio_vocabulary(), NO_APLICA, "—"],
        _COL["Cobro"]: list(dict.fromkeys(COBRO_LABELS.values())),
    }


def protection_requests(
    grid: list[list[Any]], tipos: dict[str, str], genei_ids: set[str],
    *, meta: dict[str, Any] | None = None, service_email: str | None = None,
) -> list[dict[str, Any]]:
    """Peticiones `batchUpdate` que gobiernan la edición de la hoja:

      1. Quita las protecciones y la regla naranja que puso el espejo la vez
         anterior (por su descripción / fórmula) — nunca las de una persona.
      2. Protege las columnas BLOQUEADAS de las filas de BoHub (y el Tracking de
         las que tienen envío Genei): solo las escribe la cuenta de servicio.
         Las filas manuales y del histórico quedan abiertas enteras.
      3. Validación (sin rechazar: marca) en la zona viva: desplegables en
         Situación / Preparación / Envío / Cobro y fecha válida en las fechas;
         el histórico queda sin validación.
      4. Regla de formato condicional: fila en naranja si su Nota lleva
         «⚠ revisar».

    Google SIEMPRE deja editar un rango protegido al PROPIETARIO de la hoja: para
    él la protección no bloquea, pero cada pasada revierte lo bloqueado."""
    from app.erp.drive_managed import is_separator  # noqa: PLC0415

    meta = meta or {}
    req: list[dict[str, Any]] = []
    for pr in meta.get("protected_ranges") or []:
        propia = str(pr.get("description") or "").startswith(PROTECT_DESC_PREFIX)
        if propia and pr.get("id") is not None:
            req.append({"deleteProtectedRange": {"protectedRangeId": pr["id"]}})
    reglas = meta.get("conditional_formats") or []
    propias = [i for i, r in enumerate(reglas) if _es_regla_revisar(r)]
    for idx in sorted(propias, reverse=True):   # de atrás adelante: índices válidos
        req.append({"deleteConditionalFormatRule": {"sheetId": None, "index": idx}})

    filas_bohub: list[int] = []
    filas_genei: list[int] = []
    fin_viva = len(grid)
    for i, fila in enumerate(grid):
        if i == 0:
            continue
        if is_separator(list(fila)):
            fin_viva = min(fin_viva, i)
            continue
        rid = _fila_id(fila)
        if rid and tipos.get(rid) == KIND_ORDER:
            filas_bohub.append(i)
            if rid in genei_ids:
                filas_genei.append(i)

    editores = {"editors": {"users": [service_email]}} if service_email else {}

    def _proteger(r0: int, r1: int, c0: int, c1: int, desc: str) -> dict[str, Any]:
        return {"addProtectedRange": {"protectedRange": {
            "range": {"sheetId": None, "startRowIndex": r0, "endRowIndex": r1,
                      "startColumnIndex": c0, "endColumnIndex": c1},
            "description": desc, "warningOnly": False, **editores,
        }}}

    for r0, r1 in _grupos(filas_bohub):
        for c0, c1 in _TRAMOS_BLOQUEADOS:
            req.append(_proteger(r0, r1, c0, c1, PROTECT_DESC))
    for r0, r1 in _grupos(filas_genei):
        req.append(_proteger(r0, r1, _TRACKING, _TRACKING + 1, PROTECT_DESC_GENEI))

    # Validación: en la zona viva (filas 1..fin_viva); fuera, se limpia.
    if fin_viva > 1:
        for col, valores in _listas_cerradas().items():
            req.append({"setDataValidation": {
                "range": {"sheetId": None, "startRowIndex": 1, "endRowIndex": fin_viva,
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "rule": {"condition": {"type": "ONE_OF_LIST",
                                       "values": [{"userEnteredValue": v} for v in valores]},
                         "strict": False, "showCustomUi": True},
            }})
        for col in PEDIDOS_DATE_COLUMNS:
            req.append({"setDataValidation": {
                "range": {"sheetId": None, "startRowIndex": 1, "endRowIndex": fin_viva,
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "rule": {"condition": {"type": "DATE_IS_VALID"}, "strict": False},
            }})
    req.append({"setDataValidation": {"range": {
        "sheetId": None, "startRowIndex": max(fin_viva, 1),
        "startColumnIndex": 0, "endColumnIndex": len(SEGUIMIENTO_COLUMNS_V2),
    }}})

    # Marca naranja de las filas «⚠ revisar» (se ve en cuanto aparece la marca).
    req.append({"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": None, "startRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": len(SEGUIMIENTO_COLUMNS_V2)}],
        "booleanRule": {
            "condition": {"type": "CUSTOM_FORMULA",
                          "values": [{"userEnteredValue": REVISAR_FORMULA}]},
            "format": {"backgroundColor": _NARANJA},
        },
    }}})
    return req
