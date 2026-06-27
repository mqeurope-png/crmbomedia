"""Sprint-Backfill-Gmail — orchestración del backfill histórico.

PR-Fix-Backfill-Gmail-Arquitectura. La V1 (#235) iteraba
(user × alias × contact) → 1 query Gmail por par → 360k llamadas
para Bart (6 × 3 × 20k). El job se quedaba colgado >10min en
`status=running, total_processed=0`. Job `0c0d0859-...` reportado
2026-06-25.

Refactor: invertir la iteración. UNA query por alias trae TODOS
los mensajes del alias (in + out, cualquier remitente/destinatario)
en la ventana de meses. Los contactos se matchean LOCALMENTE
contra un índice `{email_lower: contact}` cargado una vez por job.

Volumen real: ~18 queries paginadas (6 users × 3 aliases) +
get_message por mensaje. Tiempos: minutos en lugar de horas. Sin
producto cartesiano con 20k contactos.

Dos modos sobre la misma cola `gmail:backfill_historic`:

- `estimate`: get(format=metadata), suma adjunto sizes, no escribe.
  Solo cuenta mensajes que matchean contactos del CRM.
- `execute`: get(format=full), persiste a email_messages + threads,
  baja adjuntos si `include_attachments`.

Ambos modos:

- Saltan users con Gmail desconectado / scope expirado — marcan
  `needs_reconnect=True` en el breakdown.
- Cooperan con `gmail_backfill_jobs.status='cancelling'`: chequean
  el flag cada 100 mensajes.
- Heartbeat cada 100 mensajes: commit progreso + actualizar
  `updated_at`.
- Dedup por `gmail_message_id` antes de tocar la DB.
- Backoff exponencial 1s/2s/4s/8s con max 3 retries en 429/5xx.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from email.utils import getaddresses
from pathlib import Path
from typing import Any, Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.gmail.client import GmailClient
from app.integrations.gmail.service import (
    GmailNotConnectedError,
    GmailScopeMissingError,
    _client_for,
    _extract_bodies,
    _get_or_create_thread,
    _index_headers,
    _parse_date,
)
from app.models.crm import (
    Contact,
    EmailDirection,
    EmailMessage,
    EmailMessageAttachment,
    GmailBackfillJob,
    GmailBackfillMode,
    GmailBackfillStatus,
    User,
    UserEmailAliasPref,
)

logger = logging.getLogger(__name__)

#: Volume mount point para los adjuntos descargados por el backfill.
#: docker-compose monta el named volume `crmbo_email_attachments` aquí
#: en api + worker-sync para que los dos containers vean los mismos
#: ficheros — el worker escribe y el endpoint de download los sirve.
ATTACHMENT_ROOT = Path(
    os.environ.get("CRMBO_ATTACHMENT_ROOT", "/var/lib/crmbo/attachments")
)

#: Cuántos mensajes procesar antes de un heartbeat (commit + cancel
#: check + log). 100 balance entre overhead de commits y latencia
#: para que un cancel surta efecto rápido.
PROGRESS_COMMIT_EVERY = 100

#: PR-Fix-Backfill-Gmail-Cero-Importados. Si en modo `execute` se
#: procesan ≥ este número de mensajes sin importar NI saltar
#: ninguno, el matching está roto. Marcar el job FAILED en vez de
#: completarlo silenciosamente — el segundo escenario de Bart
#: (job 3f20f554) corrió 2h "completado" con 0 imports y consumió
#: cuota Gmail sin avisar.
ZERO_IMPORTS_TRIPWIRE = 1000

#: Tamaño de página al listar mensajes Gmail. Gmail caps la lista a
#: 500 por petición; 100 deja margen para que el operator vea
#: progreso fino en logs.
LIST_PAGE_SIZE = 100

#: Backoff exponencial cuando Gmail responde 429 / 5xx. 3 retries
#: máximo — al cuarto fallo seguido, devolvemos el error al caller.
BACKOFF_DELAYS = (1.0, 2.0, 4.0, 8.0)

_FILENAME_BAD = re.compile(r"[\\/\x00]")

T = TypeVar("T")


def _safe_filename(raw: str | None) -> str:
    name = (raw or "").strip()
    if not name:
        return "attachment"
    name = name.replace("..", "_")
    name = _FILENAME_BAD.sub("_", name)
    return name[:200] or "attachment"


# ---------------------------------------------------------------------------
# Single query per alias
# ---------------------------------------------------------------------------


def _build_alias_query(alias_email: str, months_back: int) -> str:
    """`(from:alias OR to:alias) newer_than:Nm`. Cubre inbound y
    outbound en una sola query — el match con el contacto del CRM se
    hace en memoria con los headers del mensaje."""
    safe = alias_email.replace('"', "")
    return f'(from:"{safe}" OR to:"{safe}") newer_than:{months_back}m'


def _is_transient_error(exc: BaseException) -> bool:
    """Best-effort detection de 429 / 5xx para reintento. La librería
    `google-api-python-client` levanta `HttpError` con `resp.status`;
    si la importación falla en tests, fallback a substring del
    repr."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is not None:
        return status in {429, 500, 502, 503, 504}
    text = repr(exc).lower()
    return any(s in text for s in ("429", "503", "rate", "quotaexceeded"))


def _with_backoff(fn: Callable[[], T], *, label: str = "gmail-call") -> T:
    """Ejecuta `fn` con backoff exponencial 1s/2s/4s/8s. Máximo 3
    retries — al cuarto fallo seguido propagamos al caller."""
    last_exc: BaseException | None = None
    for attempt, delay in enumerate(BACKOFF_DELAYS):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_transient_error(exc):
                raise
            if attempt == len(BACKOFF_DELAYS) - 1:
                break
            logger.warning(
                "gmail.backfill %s transient error (%s), sleeping %.1fs",
                label, type(exc).__name__, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _iter_alias_messages(
    client: GmailClient, alias_email: str, months_back: int
) -> tuple[list[dict[str, Any]], int | None]:
    """Pagina la query del alias y devuelve `(message_ids, total_hint)`.

    `total_hint` viene del `resultSizeEstimate` de la primera página
    (Gmail lo trae aproximado pero sirve para el heartbeat
    `total_estimated`). Si la paginación se rompe a mitad, se loguea y
    se devuelven los que tenemos."""
    query = _build_alias_query(alias_email, months_back)
    msgs: list[dict[str, Any]] = []
    page_token: str | None = None
    total_hint: int | None = None
    page_idx = 0
    while True:
        page_idx += 1
        try:
            page = _with_backoff(
                lambda token=page_token: client.list_messages(
                    query=query,
                    page_size=LIST_PAGE_SIZE,
                    page_token=token,
                ),
                label=f"list_messages[{alias_email} p{page_idx}]",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gmail.backfill list_messages failed alias=%s page=%d: %s",
                alias_email, page_idx, exc,
            )
            break
        for m in page.get("messages") or []:
            mid = m.get("id")
            if mid:
                msgs.append(m)
        if total_hint is None:
            estimate = page.get("resultSizeEstimate")
            if estimate is not None:
                total_hint = int(estimate)
        page_token = page.get("nextPageToken")
        logger.info(
            "gmail.backfill list page alias=%s page=%d cumulative=%d",
            alias_email, page_idx, len(msgs),
        )
        if not page_token:
            break
    return msgs, total_hint


def _walk_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = [payload]
    while queue:
        part = queue.pop()
        out.append(part)
        for child in part.get("parts") or []:
            queue.append(child)
    return out


def _is_attachment_part(part: dict[str, Any]) -> bool:
    """PR-Fix-Backfill-Gmail-Tras-Validación bug 3. Clasificación
    única usada por `run_estimate` Y `run_execute` para decidir si
    una parte de Gmail cuenta como adjunto.

    Decisión (opción A del spec): **inline images cuentan**. Ocupan
    espacio real en BD/disco (firmas corporativas con logo embebido
    pueden ser cientos de KB) y el operador debe verlas reflejadas
    en el estimate para no llevarse sorpresas al ejecutar.

    Criterio: la parte cuenta como adjunto si tiene un
    `body.attachmentId` no-vacío Y un tamaño > 0. Esto incluye
    tanto attachments tradicionales (Content-Disposition:
    attachment con filename explícito) como inline images
    referenciadas por `Content-ID` desde el HTML del cuerpo. Sin
    `attachmentId` la parte es body text/html y no se descarga.
    """
    body = part.get("body") or {}
    if not body.get("attachmentId"):
        return False
    try:
        size = int(body.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return size > 0


# ---------------------------------------------------------------------------
# Resource loading
# ---------------------------------------------------------------------------


#: PR-Fix-Backfill-Gmail-Cero-Importados. Modos del nuevo parámetro
#: `aliases_scope` para acotar volumen y rate-limit risk.
ALIASES_SCOPE_PRIMARY = "primary_only"
ALIASES_SCOPE_ALL_VISIBLE = "all_visible"
DEFAULT_ALIASES_SCOPE = ALIASES_SCOPE_PRIMARY


def _iter_aliases(
    session: Session,
    user_id: str,
    *,
    scope: str = DEFAULT_ALIASES_SCOPE,
) -> list[UserEmailAliasPref]:
    """Aliases de un user, filtrados por el scope solicitado.

    - `primary_only` (default): solo el alias marcado como
      `is_default=True`. Si no hay default explícito, fallback al
      email primario del User (`users.email`) — devolvemos un
      `UserEmailAliasPref` sintético en memoria para no requerir
      una fila pre-existente.
    - `all_visible`: todos los aliases con `is_allowed=True` (el
      comportamiento histórico del backfill).
    """
    if scope == ALIASES_SCOPE_ALL_VISIBLE:
        return list(
            session.scalars(
                select(UserEmailAliasPref).where(
                    UserEmailAliasPref.user_id == user_id,
                    UserEmailAliasPref.is_allowed.is_(True),
                )
            )
        )
    # primary_only
    default_alias = session.scalar(
        select(UserEmailAliasPref).where(
            UserEmailAliasPref.user_id == user_id,
            UserEmailAliasPref.is_allowed.is_(True),
            UserEmailAliasPref.is_default.is_(True),
        )
    )
    if default_alias is not None:
        return [default_alias]
    # PR-Fix-Backfill-Gmail-Tras-Validación bug 6. Bart reportó que él
    # y Eduard no aparecían en el desglose del estimate aunque tienen
    # Gmail conectado y aliases marcados como `is_allowed=True`.
    # Causa: ningún alias suyo tenía `is_default=True` (la flag se
    # sincroniza solo cuando el operador visita GET /api/emails/
    # aliases tras el sprint de display name), así que el fallback
    # caía al `user.email` del CRM — que para admins suele ser
    # `mqeurope@gmail.com` o un email administrativo SIN historial
    # Gmail real. Resultado: query Gmail con 0 matches.
    #
    # Fix: si hay aliases `is_allowed=True` pero ninguno `is_default`,
    # usamos el PRIMERO (orden por `alias_email` para determinismo).
    # Sigue siendo más estrecho que `all_visible` (1 alias en vez de
    # N) pero captura el caso real de un user que tiene `bart@
    # bomedia.net` flaggeado allowed sin haber tocado el default.
    #
    # PR-OAuth-Permisos-Admin Item 13. Preferimos el alias visible cuyo
    # email coincida con `users.email` — suele ser el buzón real del
    # comercial. Solo si no existe caemos al primer is_allowed por orden
    # alfabético.
    user = session.get(User, user_id)
    allowed = list(
        session.scalars(
            select(UserEmailAliasPref)
            .where(
                UserEmailAliasPref.user_id == user_id,
                UserEmailAliasPref.is_allowed.is_(True),
            )
            .order_by(UserEmailAliasPref.alias_email.asc())
        )
    )
    if allowed:
        chosen = allowed[0]
        if user is not None and user.email:
            user_email_lower = user.email.strip().lower()
            for a in allowed:
                if a.alias_email.strip().lower() == user_email_lower:
                    chosen = a
                    break
        logger.info(
            "gmail.backfill aliases_scope=primary_only user=%s no "
            "is_default — using fallback alias=%s",
            user_id, chosen.alias_email,
        )
        return [chosen]
    # Fallback final — sin aliases registrados. Sintetizamos uno con
    # `user.email` para que al menos intentemos algo (poco
    # probabilidad de match si el operador no usa este email en
    # Gmail, pero el log de abajo lo deja claro). `user` ya cargado arriba.
    if user is None or not user.email:
        return []
    logger.warning(
        "gmail.backfill aliases_scope=primary_only user=%s has zero "
        "UserEmailAliasPref rows — falling back to user.email=%s",
        user_id, user.email,
    )
    synth = UserEmailAliasPref(
        user_id=user_id,
        alias_email=user.email,
        is_allowed=True,
        is_default=True,
    )
    return [synth]


class _UserRef:
    """PR-OAuth-Google-Unificado. Adaptador mínimo: el backfill iteraba
    integraciones per-user; ahora hay UNA cuenta org compartida, así que
    iteramos los USERS del CRM (cada uno con sus aliases) y el client es
    siempre el org. Solo necesita `.user_id` para que el loop existente
    (que llama `_client_for(session, integ.user_id)` + `_iter_aliases`)
    siga funcionando sin tocar `run_execute` / `run_estimate`."""

    __slots__ = ("user_id",)

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


def _iter_connected_users(session: Session) -> list[_UserRef]:
    """PR-OAuth-Google-Unificado. Devuelve los users del CRM a procesar.

    Gateado por la integración ORG: si no está conectada / no activa →
    lista vacía (todos skipean). Si activa → todos los users activos del
    CRM, para iterar sus aliases Send-As (per-user) con el client org."""
    from app.integrations.google_calendar.service import (  # noqa: PLC0415
        get_org_integration,
    )

    org = get_org_integration(session)
    if org is None or getattr(org, "status", "active") != "active":
        logger.info(
            "gmail.handler: SKIP all — org integration not active "
            "(status=%s)",
            getattr(org, "status", "none"),
        )
        return []
    user_ids = list(
        session.scalars(
            select(User.id).where(User.is_active.is_(True))
        )
    )
    return [_UserRef(uid) for uid in user_ids]


def _build_contact_index(session: Session) -> dict[str, Contact]:
    """`{email.lower(): contact}` para match O(1) durante la iteración.
    Cargado UNA vez por job — los 20k contactos viven en RAM
    (≈3MB) sin pegar a la DB en cada mensaje."""
    out: dict[str, Contact] = {}
    rows = session.scalars(
        select(Contact).where(
            Contact.email.is_not(None),
            Contact.is_active.is_(True),
        )
    )
    for c in rows:
        if c.email:
            key = c.email.strip().lower()
            if key:
                # Spec congelada: Bart confirma que no hay duplicados
                # de email entre contactos. Si por bug existieran,
                # log warning + nos quedamos con el primero (orden
                # arbitrario de la query).
                if key in out:
                    logger.warning(
                        "gmail.backfill duplicate contact email=%s ids=[%s,%s]",
                        key, out[key].id, c.id,
                    )
                    continue
                out[key] = c
    return out


def _extract_participants(headers_map: dict[str, str]) -> list[str]:
    """Devuelve todos los emails (from + to + cc) lowercase."""
    out: list[str] = []
    for hdr in ("from", "to", "cc"):
        raw = headers_map.get(hdr) or ""
        for _, addr in getaddresses([raw]):
            if addr:
                out.append(addr.strip().lower())
    return out


def _match_contact(
    headers_map: dict[str, str],
    alias_lower: str,
    index: dict[str, Contact],
) -> Contact | None:
    """Encuentra el contacto del CRM que aparece como el "otro lado"
    del mensaje. Si el alias propio aparece en from y to (envío a sí
    mismo), lo saltamos. Si hay múltiples contactos (CC), tomamos el
    primero — Bart confirmó que el primary recipient es el dueño de
    la conversación."""
    for email in _extract_participants(headers_map):
        if email == alias_lower:
            continue
        contact = index.get(email)
        if contact is not None:
            return contact
    return None


# ---------------------------------------------------------------------------
# Job lifecycle helpers
# ---------------------------------------------------------------------------


def _start_running(session: Session, job: GmailBackfillJob) -> bool:
    """Transition a RUNNING. Si el job ya estaba en CANCELLING al
    arrancar (race: admin cancela entre encolar y dequeuar), respetamos
    el cancel. Devuelve False si ya no hay que correr nada."""
    if job.status == GmailBackfillStatus.CANCELLING.value:
        job.status = GmailBackfillStatus.CANCELLED.value
        job.finished_at = datetime.now(UTC)
        session.commit()
        return False
    job.status = GmailBackfillStatus.RUNNING.value
    job.started_at = datetime.now(UTC)
    session.commit()
    return True


def _check_cancel(session: Session, job: GmailBackfillJob) -> bool:
    """Refresh status from DB y, si está CANCELLING, finaliza limpio."""
    session.refresh(job, attribute_names=["status"])
    if job.status == GmailBackfillStatus.CANCELLING.value:
        job.status = GmailBackfillStatus.CANCELLED.value
        job.finished_at = datetime.now(UTC)
        session.commit()
        logger.info("gmail.backfill cancelled job=%s", job.id)
        return True
    return False


def _heartbeat(
    session: Session, job: GmailBackfillJob, *, force: bool = False
) -> None:
    """Cada 100 mensajes (o forzado): commit del progreso + bump
    `updated_at`. El frontend pinta esa columna en la UI para que
    Bart vea que el job está vivo. Hace cancel-check en la misma
    pasada."""
    if (
        not force
        and (job.total_processed or 0) % PROGRESS_COMMIT_EVERY != 0
    ):
        return
    job.updated_at = datetime.now(UTC)
    session.commit()


# ---------------------------------------------------------------------------
# Estimate mode
# ---------------------------------------------------------------------------


def run_estimate(session: Session, job: GmailBackfillJob) -> None:
    """Modo `estimate`. 1 query por alias → metadata por mensaje →
    cuenta solo los que matchean contacto + suma adjunto sizes.

    NOTA al lector que esté diagnosticando "imported=0 en BD":
    estimate NUNCA escribe a `email_messages` ni incrementa
    `total_imported`. Esos counters se mueven solo en `run_execute`.
    Si un job en estado COMPLETED tiene `mode='estimate'` + counters
    a 0, eso es esperado — el desglose útil vive en `result_json`.
    """
    if not _start_running(session, job):
        return
    config = json.loads(job.config_json or "{}")
    months_back = int(config.get("months_back", 36))
    aliases_scope = str(config.get("aliases_scope", DEFAULT_ALIASES_SCOPE))
    contact_index = _build_contact_index(session)
    integrations = _iter_connected_users(session)
    if not contact_index:
        logger.warning(
            "gmail.backfill.estimate ABORT job=%s contact_index_empty",
            job.id,
        )
        job.status = GmailBackfillStatus.FAILED.value
        job.error_summary = (
            "Sin contactos con email en el CRM — el matching no puede "
            "operar contra cero filas. Revisa que la tabla `contacts` "
            "tenga al menos una fila con `email IS NOT NULL`."
        )
        job.finished_at = datetime.now(UTC)
        session.commit()
        return
    logger.info(
        "gmail.backfill.estimate started job=%s mode=estimate "
        "users=%d contacts=%d aliases_scope=%s months_back=%d",
        job.id, len(integrations), len(contact_index),
        aliases_scope, months_back,
    )

    breakdown: dict[str, dict[str, Any]] = {}
    total_emails = 0
    total_attachments_count = 0
    total_attachments_bytes = 0

    for integ in integrations:
        if _check_cancel(session, job):
            return
        user = session.get(User, integ.user_id)
        if user is None:
            continue
        user_row = breakdown.setdefault(
            integ.user_id,
            {
                "user_id": integ.user_id,
                "email": user.email,
                "emails": 0,
                "attachments_count": 0,
                "attachments_mb": 0.0,
                "needs_reconnect": False,
            },
        )
        try:
            client = _client_for(session, integ.user_id)
        except (GmailNotConnectedError, GmailScopeMissingError) as exc:
            logger.warning(
                "gmail.backfill skip user=%s: %s", integ.user_id, exc
            )
            user_row["needs_reconnect"] = True
            continue
        aliases = _iter_aliases(session, integ.user_id, scope=aliases_scope)
        logger.info(
            "gmail.backfill.estimate user=%s email=%s aliases_in_scope=%d",
            integ.user_id, user.email, len(aliases),
        )
        if not aliases:
            continue

        for alias in aliases:
            if _check_cancel(session, job):
                return
            alias_lower = alias.alias_email.strip().lower()
            msgs, total_hint = _iter_alias_messages(
                client, alias.alias_email, months_back
            )
            # PR-Fix-Backfill-Gmail-Tras-Validación bug 4. `resultSize
            # Estimate` de Gmail es notoriamente impreciso (puede
            # quedarse en 200 cuando la query trae miles). Usamos el
            # conteo real de mensajes paginados — `len(msgs)` ya
            # tiene el total exacto del alias tras consumir todas las
            # páginas.
            job.total_estimated = (job.total_estimated or 0) + len(msgs)
            if total_hint is not None and total_hint != len(msgs):
                logger.info(
                    "gmail.backfill resultSizeEstimate hint=%d vs "
                    "actual=%d alias=%s",
                    total_hint, len(msgs), alias.alias_email,
                )
            _heartbeat(session, job, force=True)

            alias_matched_at_start = user_row["emails"]
            for m in msgs:
                if _check_cancel(session, job):
                    return
                job.total_processed += 1
                _heartbeat(session, job)
                # PR-Fix-Backfill-Gmail-Cero-Importados. Log explícito
                # cada 100 mensajes — el _heartbeat solo conmuta el
                # commit, no emite log visible. Sin esto Bart se
                # quedaba 2h sin saber si el job estaba vivo.
                if job.total_processed % PROGRESS_COMMIT_EVERY == 0:
                    logger.info(
                        "gmail.backfill.estimate progress alias=%s "
                        "processed=%d matched_this_alias=%d",
                        alias.alias_email,
                        job.total_processed,
                        user_row["emails"] - alias_matched_at_start,
                    )
                mid = m.get("id")
                try:
                    meta = _with_backoff(
                        lambda mid=mid, c=client: c.get_message_metadata(mid),
                        label=f"get_metadata[{mid}]",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "gmail.backfill metadata failed mid=%s: %s", mid, exc
                    )
                    job.total_errors += 1
                    continue
                headers_map = _index_headers(
                    meta.get("payload", {}).get("headers", [])
                )
                contact = _match_contact(
                    headers_map, alias_lower, contact_index
                )
                if contact is None:
                    continue  # ningún contacto del CRM participa
                user_row["emails"] += 1
                total_emails += 1
                # PR-Fix-Backfill-Gmail-Tras-Validación bug 3.
                # `_is_attachment_part` es el MISMO criterio que usa
                # `_import_one` en execute — antes la versión
                # estimate filtraba por `part.get("filename")` y se
                # saltaba las inline images, mientras execute usaba
                # `body.attachmentId` y SÍ las guardaba. Resultado:
                # estimate decía "0 adjuntos" pero execute creaba
                # filas en email_message_attachments. Ahora coinciden.
                for part in _walk_parts(meta.get("payload") or {}):
                    if _is_attachment_part(part):
                        size = int((part.get("body") or {}).get("size") or 0)
                        total_attachments_count += 1
                        total_attachments_bytes += size
                        user_row["attachments_count"] += 1
                        user_row["attachments_mb"] += size / (1024 * 1024)
            logger.info(
                "gmail.backfill.estimate alias_done user=%s alias=%s "
                "msgs_seen=%d matched=%d",
                integ.user_id, alias.alias_email, len(msgs), user_row["emails"],
            )

    estimated_minutes = round(total_emails / 50 / 60, 1) if total_emails else 0.0
    result = {
        "total_emails": total_emails,
        "total_attachments_count": total_attachments_count,
        "total_attachments_size_mb": round(
            total_attachments_bytes / (1024 * 1024), 1
        ),
        "estimated_storage_gb": round(
            total_attachments_bytes / (1024 ** 3), 2
        ),
        "estimated_duration_minutes": estimated_minutes,
        "per_user_breakdown": list(breakdown.values()),
        "months_back": months_back,
    }
    job.result_json = json.dumps(result)
    job.status = GmailBackfillStatus.COMPLETED.value
    job.finished_at = datetime.now(UTC)
    session.commit()
    logger.info(
        "gmail.backfill.estimate done job=%s emails=%d attach_mb=%.1f",
        job.id, total_emails, total_attachments_bytes / (1024 * 1024),
    )


# ---------------------------------------------------------------------------
# Execute mode
# ---------------------------------------------------------------------------


def run_execute(session: Session, job: GmailBackfillJob) -> None:
    """Modo `execute`. Misma iteración invertida que estimate, pero
    persiste a DB y baja adjuntos."""
    if not _start_running(session, job):
        return
    config = json.loads(job.config_json or "{}")
    months_back = int(config.get("months_back", 36))
    include_attachments = bool(config.get("include_attachments", True))
    max_attachment_mb = int(config.get("max_attachment_size_mb", 25))
    max_attachment_bytes = max_attachment_mb * 1024 * 1024
    aliases_scope = str(config.get("aliases_scope", DEFAULT_ALIASES_SCOPE))

    contact_index = _build_contact_index(session)
    integrations = _iter_connected_users(session)
    if not contact_index:
        logger.warning(
            "gmail.backfill.execute ABORT job=%s contact_index_empty",
            job.id,
        )
        job.status = GmailBackfillStatus.FAILED.value
        job.error_summary = (
            "Sin contactos con email en el CRM — el matching no puede "
            "operar contra cero filas."
        )
        job.finished_at = datetime.now(UTC)
        session.commit()
        return
    logger.info(
        "gmail.backfill.execute started job=%s mode=execute "
        "users=%d contacts=%d aliases_scope=%s months_back=%d "
        "include_attachments=%s",
        job.id, len(integrations), len(contact_index),
        aliases_scope, months_back, include_attachments,
    )
    errors_by_user: dict[str, str] = {}
    users_skipped: list[str] = []

    for integ in integrations:
        if _check_cancel(session, job):
            return
        try:
            client = _client_for(session, integ.user_id)
        except (GmailNotConnectedError, GmailScopeMissingError) as exc:
            errors_by_user[integ.user_id] = str(exc)
            users_skipped.append(integ.user_id)
            continue
        aliases = _iter_aliases(session, integ.user_id, scope=aliases_scope)
        logger.info(
            "gmail.backfill.execute user=%s aliases_in_scope=%d",
            integ.user_id, len(aliases),
        )
        if not aliases:
            continue

        for alias in aliases:
            if _check_cancel(session, job):
                return
            alias_lower = alias.alias_email.strip().lower()
            msgs, total_hint = _iter_alias_messages(
                client, alias.alias_email, months_back
            )
            if total_hint is not None:
                job.total_estimated = (job.total_estimated or 0) + total_hint
                _heartbeat(session, job, force=True)

            imported_at_start = job.total_imported
            skipped_at_start = job.total_skipped
            for m in msgs:
                if _check_cancel(session, job):
                    return
                _import_one(
                    session,
                    client=client,
                    job=job,
                    owner_user_id=integ.user_id,
                    alias_lower=alias_lower,
                    gmail_message_id=m.get("id"),
                    gmail_thread_id=m.get("threadId") or m.get("id"),
                    contact_index=contact_index,
                    include_attachments=include_attachments,
                    max_attachment_bytes=max_attachment_bytes,
                )
                _heartbeat(session, job)
                # PR-Fix-Backfill-Gmail-Cero-Importados. Log visible
                # cada 100 mensajes — el operador ve motion en
                # `docker compose logs worker-sync` sin esperar al
                # alias_done final.
                if job.total_processed % PROGRESS_COMMIT_EVERY == 0:
                    logger.info(
                        "gmail.backfill.execute progress alias=%s "
                        "processed=%d imported=%d skipped=%d errors=%d",
                        alias.alias_email,
                        job.total_processed,
                        job.total_imported,
                        job.total_skipped,
                        job.total_errors,
                    )
            logger.info(
                "gmail.backfill.execute alias_done user=%s alias=%s "
                "msgs_seen=%d imported_now=%d skipped_now=%d "
                "imported_total=%d skipped_total=%d errors_total=%d",
                integ.user_id, alias.alias_email, len(msgs),
                job.total_imported - imported_at_start,
                job.total_skipped - skipped_at_start,
                job.total_imported, job.total_skipped, job.total_errors,
            )

    # PR-Fix-Backfill-Gmail-Cero-Importados. Tripwire: si procesamos
    # ≥1000 mensajes y NI uno importó NI uno saltó, el matching está
    # roto (case-sensitive, parse incorrecto, index vacío, etc.).
    # Mejor un FAILED ruidoso que un COMPLETED engañoso que Bart
    # tarda 2h en descubrir.
    if (
        job.total_processed >= ZERO_IMPORTS_TRIPWIRE
        and job.total_imported == 0
        and job.total_skipped == 0
    ):
        logger.warning(
            "gmail.backfill.execute tripwire job=%s processed=%d "
            "imported=0 skipped=0 — matching likely broken",
            job.id, job.total_processed,
        )
        job.status = GmailBackfillStatus.FAILED.value
        job.error_summary = (
            f"Procesados {job.total_processed} mensajes sin importar "
            "ni saltar ninguno. El matching contact↔message está "
            "roto: posible bug en `_match_contact` (case, parse de "
            "headers, índice vacío). Revisa logs INFO de "
            "`gmail.backfill.execute progress` para confirmar."
        )
        job.finished_at = datetime.now(UTC)
        session.commit()
        return

    result = {
        "users_processed": len(integrations) - len(users_skipped),
        "users_skipped": users_skipped,
        "errors_by_user": errors_by_user,
        "months_back": months_back,
        "include_attachments": include_attachments,
        "aliases_scope": aliases_scope,
    }
    job.result_json = json.dumps(result)
    job.status = GmailBackfillStatus.COMPLETED.value
    job.finished_at = datetime.now(UTC)
    session.commit()


def _import_one(
    session: Session,
    *,
    client: GmailClient,
    job: Any,
    owner_user_id: str,
    alias_lower: str,
    gmail_message_id: str | None,
    gmail_thread_id: str,
    contact_index: dict[str, Contact],
    include_attachments: bool,
    max_attachment_bytes: int,
    imported_via: str = "historic_backfill",
) -> None:
    if not gmail_message_id:
        return
    job.total_processed += 1

    # Dedup ANTES del get(full) — la unique constraint también lo
    # garantiza pero ahorra una request Gmail.
    existing = session.scalar(
        select(EmailMessage).where(
            EmailMessage.gmail_account_user_id == owner_user_id,
            EmailMessage.gmail_message_id == gmail_message_id,
        )
    )
    if existing is not None:
        job.total_skipped += 1
        return

    try:
        raw = _with_backoff(
            lambda: client.get_message(gmail_message_id),
            label=f"get_message[{gmail_message_id}]",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "gmail.backfill get_message failed mid=%s: %s",
            gmail_message_id, exc,
        )
        job.total_errors += 1
        return

    headers_map = _index_headers(raw.get("payload", {}).get("headers", []))
    contact = _match_contact(headers_map, alias_lower, contact_index)
    if contact is None:
        # Mensaje del alias pero contra alguien que no es contacto del
        # CRM → skip silencioso. NO auto-creamos contactos (spec).
        job.total_skipped += 1
        return

    from_header = headers_map.get("from") or ""
    to_header = headers_map.get("to") or ""
    cc_header = headers_map.get("cc")
    subject = headers_map.get("subject")
    sent_at = _parse_date(headers_map.get("date")) or datetime.now(UTC)
    from_addresses = getaddresses([from_header])
    from_name = from_addresses[0][0] if from_addresses else None
    from_email = from_addresses[0][1] if from_addresses else ""
    to_emails = [addr for _, addr in getaddresses([to_header]) if addr]
    cc_emails = (
        [addr for _, addr in getaddresses([cc_header])] if cc_header else None
    )
    body_text, body_html = _extract_bodies(raw.get("payload", {}))

    direction = (
        EmailDirection.INBOUND
        if (from_email or "").strip().lower() == (contact.email or "").strip().lower()
        else EmailDirection.OUTBOUND
    )

    thread = _get_or_create_thread(
        session,
        gmail_account_user_id=owner_user_id,
        gmail_thread_id=gmail_thread_id,
        initiated_by_user_id=owner_user_id,
        contact_id=contact.id,
        subject=subject,
        first_message_at=sent_at,
        participants=[from_email, *to_emails],
    )

    parts_with_files: list[dict[str, Any]] = []
    attachments_meta: list[dict[str, Any]] = []
    for part in _walk_parts(raw.get("payload", {})):
        if _is_attachment_part(part):
            parts_with_files.append(part)
            attachments_meta.append(
                {
                    "filename": part.get("filename") or "",
                    "mime_type": part.get("mimeType"),
                    "size": int((part.get("body") or {}).get("size") or 0),
                }
            )

    # PR-Fix-Backfill-Gmail-Tras-Validación bug 2. La persistencia del
    # mensaje + adjuntos se aísla en SAVEPOINT + try/except: un solo
    # email que pete (body 200KB que excede LONGTEXT pre-migración,
    # attachment con caracteres raros en el filename, conexión DB
    # transient) NO debe tirar abajo el job entero ni revertir los
    # mensajes que ya estaban flusheados pero no commit-eados.
    # `begin_nested()` emite un SAVEPOINT — `rollback()` solo borra
    # los inserts de ESTE mensaje, no los anteriores.
    savepoint = session.begin_nested()
    try:
        message = EmailMessage(
            thread_id=thread.id,
            gmail_message_id=gmail_message_id,
            gmail_account_user_id=owner_user_id,
            direction=direction,
            from_email=from_email,
            from_name=from_name,
            to_emails_json=json.dumps(to_emails),
            cc_emails_json=json.dumps(cc_emails) if cc_emails else None,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            snippet=raw.get("snippet"),
            sent_at=sent_at,
            contact_id=contact.id,
            imported_via=imported_via,
            imported_at=datetime.now(UTC),
            attachments_json=(
                json.dumps(attachments_meta) if attachments_meta else None
            ),
        )
        session.add(message)
        thread.last_message_at = max(thread.last_message_at, sent_at)
        thread.message_count = (thread.message_count or 0) + 1
        session.flush()

        if include_attachments and parts_with_files:
            _download_attachments(
                session,
                client=client,
                message=message,
                parts=parts_with_files,
                contact_id=contact.id,
                gmail_message_id=gmail_message_id,
                max_attachment_bytes=max_attachment_bytes,
            )

        savepoint.commit()
        job.total_imported += 1
    except Exception as exc:  # noqa: BLE001
        # Rollback del SAVEPOINT — los mensajes anteriores en la
        # misma transacción siguen vivos. Sin esto un row corrupto
        # (body con UTF inválido, tamaño de columna, etc.) revertía
        # también los mensajes que SÍ se habían persistido en el
        # mismo batch.
        savepoint.rollback()
        logger.warning(
            "gmail.backfill _import_one failed mid=%s contact_id=%s "
            "owner=%s err=%s: %s",
            gmail_message_id,
            contact.id,
            owner_user_id,
            type(exc).__name__,
            exc,
        )
        job.total_errors += 1


def _download_attachments(
    session: Session,
    *,
    client: GmailClient,
    message: EmailMessage,
    parts: list[dict[str, Any]],
    contact_id: str,
    gmail_message_id: str,
    max_attachment_bytes: int,
) -> None:
    base_dir = ATTACHMENT_ROOT / contact_id / gmail_message_id
    base_dir.mkdir(parents=True, exist_ok=True)
    for part in parts:
        size = int((part.get("body") or {}).get("size") or 0)
        if size <= 0:
            continue
        if max_attachment_bytes and size > max_attachment_bytes:
            logger.info(
                "gmail.backfill skip attachment too big mid=%s size=%d cap=%d",
                gmail_message_id, size, max_attachment_bytes,
            )
            continue
        att_id = (part.get("body") or {}).get("attachmentId")
        if not att_id:
            continue
        try:
            resp = _with_backoff(
                lambda att_id=att_id: client.get_attachment(
                    message_id=gmail_message_id, attachment_id=att_id
                ),
                label=f"get_attachment[{att_id}]",
            )
            data = resp.get("data") or ""
            binary = base64.urlsafe_b64decode(data.encode())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gmail.backfill attachment download failed mid=%s att=%s: %s",
                gmail_message_id, att_id, exc,
            )
            continue
        filename = _safe_filename(part.get("filename"))
        target = base_dir / filename
        idx = 1
        while target.exists():
            stem, ext = os.path.splitext(filename)
            target = base_dir / f"{stem}_{idx}{ext}"
            idx += 1
        target.write_bytes(binary)
        rel_path = target.relative_to(ATTACHMENT_ROOT).as_posix()
        session.add(
            EmailMessageAttachment(
                message_id=message.id,
                filename=filename,
                mime_type=part.get("mimeType"),
                size_bytes=size,
                storage_path=rel_path,
                gmail_attachment_id=att_id,
                created_at=datetime.now(UTC),
            )
        )


# ---------------------------------------------------------------------------
# RQ entry point
# ---------------------------------------------------------------------------


#: Estados terminales del job. Si al recoger el job de la queue está
#: en uno de estos, el handler se salta el procesamiento — protege
#: contra duplicados en cola RQ tras retries/clics duplicados o jobs
#: cancelados manualmente vía SQL/UI (caso real 2026-06-26 reportado
#: por Bart: job `ce82696c…` con status=cancelled procesado 50 min
#: por el worker después).
_TERMINAL_STATUSES = frozenset(
    {
        GmailBackfillStatus.CANCELLED.value,
        GmailBackfillStatus.FAILED.value,
        GmailBackfillStatus.COMPLETED.value,
    }
)


def run_backfill(job_id: str) -> None:
    """RQ entry. Abre sesión, lee el row, dispatcha por modo.

    Robustez:
    - Si el job no existe (row borrado) → log warning + return.
    - Si el job ya está en estado terminal → log info + return sin
      tocar nada (protección bug 7 PR-Tras-Validación).
    - Cualquier excepción no manejada en estimate/execute → status=
      FAILED + error_summary con tipo + mensaje (no queda colgado en
      running, bug 2 PR-Tras-Validación).
    """
    import traceback  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        job = session.get(GmailBackfillJob, job_id)
        if job is None:
            logger.warning("gmail.backfill job not found id=%s", job_id)
            return
        if job.status in _TERMINAL_STATUSES:
            logger.info(
                "gmail.backfill skip already-finalized job=%s status=%s",
                job_id, job.status,
            )
            return
        try:
            if job.mode == GmailBackfillMode.ESTIMATE.value:
                run_estimate(session, job)
            elif job.mode == GmailBackfillMode.EXECUTE.value:
                run_execute(session, job)
            else:
                job.status = GmailBackfillStatus.FAILED.value
                job.error_summary = f"Unknown mode: {job.mode}"
                job.finished_at = datetime.now(UTC)
                session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("gmail.backfill crashed job=%s", job_id)
            # Mantener el row con el typename + mensaje + tail del
            # traceback — el operador puede correlacionar con los logs
            # del worker sin re-ejecutar.
            tb_tail = "\n".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)[-3:]
            )
            error_summary = (
                f"{type(exc).__name__}: {exc}\n\n--- traceback ---\n{tb_tail}"
            )
            try:
                session.rollback()
                job = session.get(GmailBackfillJob, job_id)
                if job is not None and job.status not in _TERMINAL_STATUSES:
                    job.status = GmailBackfillStatus.FAILED.value
                    job.error_summary = error_summary[:2000]
                    job.finished_at = datetime.now(UTC)
                    session.commit()
            except Exception:  # noqa: BLE001
                # No queremos enmascarar la excepción original — si la
                # transacción de rescate falla, hay un problema más
                # serio (conexión DB caída) y un re-raise rebotaría el
                # job a RQ. Loggear y devolver — el job queda en
                # running pero el operador ya ve el error en el log.
                logger.exception(
                    "gmail.backfill failed to persist FAILED status job=%s",
                    job_id,
                )


def enqueue_backfill(job_id: str) -> None:
    from rq import Queue  # noqa: PLC0415

    from app.workers.queues import queue_name, redis_connection  # noqa: PLC0415

    queue = Queue(
        queue_name("gmail", "backfill_historic"),
        connection=redis_connection(),
        default_timeout=14_400,
    )
    queue.enqueue(run_backfill, job_id, job_timeout=14_400)


# ---------------------------------------------------------------------------
# PR-Auto-Backfill-Gmail-Por-Contacto — mini-backfill por contacto
# ---------------------------------------------------------------------------

#: Etiqueta `imported_via` para los rows traídos por el backfill
#: per-contact. Distinta de 'historic_backfill' (admin masivo) para que
#: las queries operativas distingan el origen.
IMPORTED_VIA_PER_CONTACT = "per_contact_backfill"

#: Ventana por defecto (meses) para el mini-backfill per-contact. La
#: spec lo fija en 12 — un año de histórico cubre el caso "lead que ya
#: hablé con él hace meses" sin disparar volúmenes de años.
DEFAULT_PER_CONTACT_MONTHS = 12

#: Tope blando de mensajes por contacto. Un solo contacto no debería
#: traer miles de mensajes; si los trae cortamos para respetar el
#: presupuesto de ~30s/contacto de la spec y no acaparar cuota Gmail.
PER_CONTACT_MAX_MESSAGES = 500


class _PerContactCounter:
    """Contador en memoria con la misma interfaz que `GmailBackfillJob`
    consume `_import_one` (total_processed / imported / skipped /
    errors). Evitamos crear una fila `gmail_backfill_jobs` por contacto
    — el mini-backfill es fire-and-forget, su resultado vive en logs."""

    def __init__(self) -> None:
        self.total_processed = 0
        self.total_imported = 0
        self.total_skipped = 0
        self.total_errors = 0


def _build_per_contact_query(
    contact_email: str, alias_email: str, months_back: int
) -> str:
    """`(from:contact to:alias) OR (from:alias to:contact) newer_than:Nm`.

    Más estrecha que la query del backfill masivo (que trae TODO el
    alias): aquí solo queremos la conversación entre ESTE contacto y el
    alias del user, en ambas direcciones."""
    c = contact_email.replace('"', "")
    a = alias_email.replace('"', "")
    return (
        f'(from:"{c}" to:"{a}") OR (from:"{a}" to:"{c}") '
        f"newer_than:{months_back}m"
    )


def run_backfill_per_contact(
    contact_id: str,
    months_back: int = DEFAULT_PER_CONTACT_MONTHS,
    triggered_by_user_id: str | None = None,
) -> None:
    """Mini-backfill del histórico Gmail de UN contacto.

    Reusa la infraestructura del backfill masivo (`_iter_aliases`,
    `_client_for`, `_import_one`) pero acota la búsqueda a la
    conversación contacto↔alias. Fire-and-forget: sin fila de job, el
    resumen vive en logs (`gmail.per_contact_backfill.*`).

    Edge cases (spec):
    - Contacto borrado mientras el job estaba en cola → log + return.
    - Contacto sin email → warning + return.
    - OAuth expirado de un user → skip ese user, sigue con los demás.
    - gmail_message_id duplicado → skip (dedup en `_import_one`).
    """
    from app.db.session import get_engine  # noqa: PLC0415

    start = time.monotonic()
    with Session(get_engine()) as session:
        contact = session.get(Contact, contact_id)
        if contact is None:
            logger.info(
                "gmail.per_contact_backfill skip contact_id=%s — borrado "
                "antes de ejecutar el job",
                contact_id,
            )
            return
        contact_email = (contact.email or "").strip()
        if not contact_email:
            logger.warning(
                "gmail.per_contact_backfill skip contact_id=%s — sin email",
                contact_id,
            )
            return
        contact_email_lower = contact_email.lower()

        logger.info(
            "gmail.per_contact_backfill.start contact_id=%s email=%s "
            "months_back=%d triggered_by=%s",
            contact_id, contact_email_lower, months_back, triggered_by_user_id,
        )

        # Índice de un solo contacto — `_import_one` solo matcheará este
        # contacto, nunca otro que aparezca en CC de un thread.
        single_index: dict[str, Contact] = {contact_email_lower: contact}
        counter = _PerContactCounter()
        integrations = _iter_connected_users(session)

        for integ in integrations:
            try:
                client = _client_for(session, integ.user_id)
            except (GmailNotConnectedError, GmailScopeMissingError) as exc:
                logger.info(
                    "gmail.per_contact_backfill skip_user contact_id=%s "
                    "user=%s reason=%s",
                    contact_id, integ.user_id, type(exc).__name__,
                )
                continue
            aliases = _iter_aliases(session, integ.user_id)
            for alias in aliases:
                alias_lower = alias.alias_email.strip().lower()
                if alias_lower == contact_email_lower:
                    continue
                query = _build_per_contact_query(
                    contact_email, alias.alias_email, months_back
                )
                try:
                    page = _with_backoff(
                        lambda q=query, c=client: c.list_messages(
                            query=q, page_size=LIST_PAGE_SIZE
                        ),
                        label=f"per_contact_list[{integ.user_id}]",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "gmail.per_contact_backfill list failed contact_id=%s "
                        "user=%s: %s",
                        contact_id, integ.user_id, exc,
                    )
                    continue
                msgs = list(page.get("messages") or [])
                page_token = page.get("nextPageToken")
                # Paginar el resto de páginas hasta el tope blando.
                while page_token and len(msgs) < PER_CONTACT_MAX_MESSAGES:
                    try:
                        page = _with_backoff(
                            lambda t=page_token, q=query, c=client: c.list_messages(
                                query=q, page_size=LIST_PAGE_SIZE, page_token=t
                            ),
                            label=f"per_contact_list[{integ.user_id}]",
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "gmail.per_contact_backfill page failed "
                            "contact_id=%s user=%s: %s",
                            contact_id, integ.user_id, exc,
                        )
                        break
                    msgs.extend(page.get("messages") or [])
                    page_token = page.get("nextPageToken")

                matched_before = counter.total_imported
                for m in msgs[:PER_CONTACT_MAX_MESSAGES]:
                    _import_one(
                        session,
                        client=client,
                        job=counter,
                        owner_user_id=integ.user_id,
                        alias_lower=alias_lower,
                        gmail_message_id=m.get("id"),
                        gmail_thread_id=m.get("threadId") or m.get("id"),
                        contact_index=single_index,
                        include_attachments=False,
                        max_attachment_bytes=0,
                        imported_via=IMPORTED_VIA_PER_CONTACT,
                    )
                session.commit()
                logger.info(
                    "gmail.per_contact_backfill.alias_processed "
                    "contact_id=%s user=%s alias=%s seen=%d matched=%d",
                    contact_id, integ.user_id, alias.alias_email,
                    len(msgs), counter.total_imported - matched_before,
                )

        duration = time.monotonic() - start
        logger.info(
            "gmail.per_contact_backfill.completed contact_id=%s "
            "total_imported=%d skipped=%d errors=%d duration=%.1fs",
            contact_id, counter.total_imported, counter.total_skipped,
            counter.total_errors, duration,
        )


def enqueue_backfill_per_contact(
    contact_id: str,
    months_back: int = DEFAULT_PER_CONTACT_MONTHS,
    triggered_by_user_id: str | None = None,
) -> None:
    """Encola el mini-backfill en la cola `gmail:backfill_per_contact`.

    Best-effort: si Redis no está disponible (tests / dev sin worker),
    NO ejecutamos inline — el per-contact no es crítico para la
    creación del contacto y un fallo de Redis no debe tirar el POST
    /contacts. Loggea y devuelve."""
    from rq import Queue  # noqa: PLC0415

    from app.workers.queues import queue_name, redis_connection  # noqa: PLC0415

    try:
        queue = Queue(
            queue_name("gmail", "backfill_per_contact"),
            connection=redis_connection(),
            default_timeout=300,
        )
        queue.enqueue(
            run_backfill_per_contact,
            contact_id,
            months_back,
            triggered_by_user_id,
            job_timeout=300,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "gmail.per_contact_backfill enqueue failed contact_id=%s: %s",
            contact_id, exc,
        )
