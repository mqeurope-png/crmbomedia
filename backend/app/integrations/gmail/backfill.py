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
    UserGoogleIntegration,
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
    # Fallback — usuario sin default explícito. Sintetizamos un
    # alias en memoria con el email principal del User.
    user = session.get(User, user_id)
    if user is None or not user.email:
        return []
    synth = UserEmailAliasPref(
        user_id=user_id,
        alias_email=user.email,
        is_allowed=True,
        is_default=True,
    )
    return [synth]


def _iter_connected_users(
    session: Session,
) -> list[UserGoogleIntegration]:
    """Users con Gmail conectado. El client lookup decide después si
    falta el scope (raise GmailScopeMissingError → skip + needs_reconnect)."""
    return list(
        session.scalars(
            select(UserGoogleIntegration).where(
                UserGoogleIntegration.scopes.is_not(None),
            )
        )
    )


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
            if total_hint is not None:
                # Cumulativo conservador — sumamos hints de aliases.
                job.total_estimated = (job.total_estimated or 0) + total_hint
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
                for part in _walk_parts(meta.get("payload") or {}):
                    if part.get("filename"):
                        size = int((part.get("body") or {}).get("size") or 0)
                        if size > 0:
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
    job: GmailBackfillJob,
    owner_user_id: str,
    alias_lower: str,
    gmail_message_id: str | None,
    gmail_thread_id: str,
    contact_index: dict[str, Contact],
    include_attachments: bool,
    max_attachment_bytes: int,
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
        if part.get("filename"):
            parts_with_files.append(part)
            attachments_meta.append(
                {
                    "filename": part.get("filename"),
                    "mime_type": part.get("mimeType"),
                    "size": int((part.get("body") or {}).get("size") or 0),
                }
            )

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
        imported_via="historic_backfill",
        imported_at=datetime.now(UTC),
        attachments_json=json.dumps(attachments_meta) if attachments_meta else None,
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

    job.total_imported += 1


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


def run_backfill(job_id: str) -> None:
    """RQ entry. Abre sesión, lee el row, dispatcha por modo."""
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        job = session.get(GmailBackfillJob, job_id)
        if job is None:
            logger.warning("gmail.backfill job not found id=%s", job_id)
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
            job.status = GmailBackfillStatus.FAILED.value
            job.error_summary = str(exc)[:2000]
            job.finished_at = datetime.now(UTC)
            session.commit()


def enqueue_backfill(job_id: str) -> None:
    from rq import Queue  # noqa: PLC0415

    from app.workers.queues import queue_name, redis_connection  # noqa: PLC0415

    queue = Queue(
        queue_name("gmail", "backfill_historic"),
        connection=redis_connection(),
        default_timeout=14_400,
    )
    queue.enqueue(run_backfill, job_id, job_timeout=14_400)
