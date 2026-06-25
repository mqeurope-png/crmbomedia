"""Sprint-Push-CRM-Brevo — handlers RQ del push CRM → Brevo.

Tres entry points:

1. `push_contact_to_brevo(contact_id)` — RQ task per-contacto. Lo
   encola el listener `after_commit` (cambio de owner) y el periodic
   runner. Resuelve mapping, hace upsert en Brevo, mueve de lista si
   procede, marca `brevo_contact_id` y `brevo_last_synced_at`.

2. `remove_contact_from_brevo(contact_id, reason)` — RQ task que
   desuscribe el contacto de TODAS las listas mapeadas. NO borra el
   contacto en Brevo: preserva histórico de campañas.

3. `periodic_push_check(session, sync_log)` — handler OPERATIONS
   registrado en `brevo:periodic_push`. Cada hora detecta contactos
   con owner sin `brevo_contact_id` y encola `push_contact` en chunks.

El listener `_after_commit` del módulo `services/brevo_push` llama a
`enqueue_push_contact` / `enqueue_remove_from_brevo` para no acoplar
el repository de assignments con RQ.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.integrations.brevo.client import BrevoClient
from app.integrations.brevo.mapper import map_internal_contact_to_brevo
from app.integrations.errors import IntegrationDuplicateError, IntegrationError
from app.models.crm import Contact, ExternalSystem, SyncLog
from app.models.integration_settings import IntegrationAccount
from app.services import brevo_push as _service
from app.workers.jobs import OPERATIONS, SyncOutcome
from app.workers.queues import queue_name, redis_connection

logger = logging.getLogger(__name__)

DEFAULT_PUSH_INTERVAL_HOURS = 1
DEFAULT_PERIODIC_CHUNK = 100
DEFAULT_BACKFILL_CHUNK = 50

PUSH_LOCK_KEY = "brevo:periodic_push:heartbeat"


# ---------------------------------------------------------------------------
# Enqueue helpers (called from services/brevo_push after_commit listener)
# ---------------------------------------------------------------------------


def _enqueue(callable_: Any, *args: Any) -> None:
    from rq import Queue  # noqa: PLC0415

    queue = Queue(
        queue_name("brevo", "push_contact"),
        connection=redis_connection(),
    )
    queue.enqueue(callable_, *args)


def enqueue_push_contact(*, contact_id: str) -> None:
    """Pone `brevo:push_contact` en la cola para el contacto dado. El
    listener after_commit + el periodic runner usan este punto."""
    try:
        _enqueue(push_contact_to_brevo, contact_id)
        logger.info("brevo.push_contact enqueued contact_id=%s", contact_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "brevo.push_contact enqueue failed contact_id=%s: %s",
            contact_id, exc,
        )


def enqueue_remove_from_brevo(
    *, contact_id: str, reason: str = "owner_removed"
) -> None:
    try:
        _enqueue(remove_contact_from_brevo, contact_id, reason)
        logger.info(
            "brevo.remove_from_brevo enqueued contact_id=%s reason=%s",
            contact_id, reason,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "brevo.remove_from_brevo enqueue failed contact_id=%s: %s",
            contact_id, exc,
        )


# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------


def _resolve_brevo_account(session: Session) -> IntegrationAccount | None:
    """Bart confirmó (sprint) que la instalación productiva tiene UNA
    cuenta Brevo activa. Si en el futuro hay más, este helper se
    extiende con `mapping.brevo_account_id` — por ahora cogemos la
    primera live."""
    from app.models.integration_settings import IntegrationMode  # noqa: PLC0415

    stmt = (
        select(IntegrationAccount)
        .where(
            IntegrationAccount.system == ExternalSystem.BREVO,
            IntegrationAccount.enabled.is_(True),
            IntegrationAccount.mode == IntegrationMode.LIVE,
        )
        .order_by(IntegrationAccount.created_at.asc())
        .limit(1)
    )
    return session.scalar(stmt)


# ---------------------------------------------------------------------------
# push_contact_to_brevo
# ---------------------------------------------------------------------------


def push_contact_to_brevo(contact_id: str) -> None:
    """RQ entry point. Abre su propia sesión y commitea."""
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        try:
            _push_one(session, contact_id)
            session.commit()
        except Exception:
            session.rollback()
            raise


def _push_one(session: Session, contact_id: str) -> None:
    contact = session.get(Contact, contact_id)
    if contact is None:
        logger.info("brevo.push_contact skip contact_id=%s reason=not_found", contact_id)
        return

    ok, skip_reason = _service.should_push(contact)
    if not ok:
        logger.info(
            "brevo.push_contact skip contact_id=%s reason=%s",
            contact_id, skip_reason,
        )
        return

    mapping = _service.get_mapping(session, contact.owner_user_id)
    if mapping is None:
        logger.warning(
            "brevo.push_contact skip contact_id=%s reason=no_mapping owner=%s",
            contact_id, contact.owner_user_id,
        )
        return

    account = _resolve_brevo_account(session)
    if account is None:
        logger.warning(
            "brevo.push_contact skip contact_id=%s reason=no_brevo_account",
            contact_id,
        )
        return

    target_list_id = int(mapping.brevo_list_id)
    mapped_lists = _service.mapped_list_ids(session)
    email = contact.email
    payload = map_internal_contact_to_brevo(contact)

    async def _drive() -> tuple[str, list[int], str]:
        """Devuelve (action, lists_removed_from, brevo_id)."""
        async with BrevoClient(session, account.account_id) as client:
            existing: dict[str, Any] | None = None
            try:
                existing = await client.get_contact(email)
            except IntegrationError as exc:
                # 404 → no existe. Cualquier otro error: rethrow.
                if getattr(exc, "status_code", None) != 404:
                    raise
                existing = None

            removed_from: list[int] = []
            if existing:
                current_lists = [
                    int(lid) for lid in (existing.get("listIds") or [])
                ]
                # Quitar de listas mapeadas a OTROS users (move). NUNCA
                # tocar listas que no estén en `mapped_lists` — el
                # operador puede tener al contacto suscrito manualmente
                # a una newsletter y no es asunto del CRM.
                for lid in current_lists:
                    if lid in mapped_lists and lid != target_list_id:
                        try:
                            await client.remove_contacts_from_list(lid, [email])
                            removed_from.append(lid)
                        except IntegrationError as exc:
                            logger.warning(
                                "brevo.push_contact remove_from_list failed "
                                "list=%s email=%s: %s",
                                lid, email, exc,
                            )
                if target_list_id in current_lists:
                    action = "already_in_list"
                else:
                    await client.add_contacts_to_list(target_list_id, [email])
                    action = "added_to_list" if not removed_from else "moved"
                brevo_id = str(existing.get("id") or "")
                return action, removed_from, brevo_id

            # Brand-new
            try:
                created = await client.create_contact(
                    {
                        "email": email,
                        "attributes": payload["attributes"],
                        "listIds": [target_list_id],
                        "updateEnabled": False,
                    }
                )
                brevo_id = str(created.get("id") or "")
            except IntegrationDuplicateError:
                # Race: existía cuando hicimos POST aunque get_contact
                # falló. Caemos al path de añadir a la lista.
                await client.add_contacts_to_list(target_list_id, [email])
                fetched = await client.get_contact(email)
                brevo_id = str(fetched.get("id") or "")
                return "added_to_list", removed_from, brevo_id
            return "created", removed_from, brevo_id

    try:
        action, removed_from, brevo_id = asyncio.run(_drive())
    except Exception as exc:  # noqa: BLE001 - audit + rethrow para RQ retry
        record_event(
            session,
            action=Action.BREVO_CONTACT_PUSH_FAILED,
            target_type="contact",
            target_id=contact_id,
            metadata={
                "error": str(exc),
                "owner_user_id": contact.owner_user_id,
                "list_id": target_list_id,
            },
        )
        session.commit()
        raise

    contact.brevo_contact_id = brevo_id or contact.brevo_contact_id or "synced"
    contact.brevo_last_synced_at = datetime.now(UTC)

    record_event(
        session,
        action=Action.BREVO_CONTACT_PUSHED,
        target_type="contact",
        target_id=contact_id,
        metadata={
            "list_id": target_list_id,
            "action": action,
            "removed_from_lists": removed_from,
            "owner_user_id": contact.owner_user_id,
        },
    )


# ---------------------------------------------------------------------------
# remove_contact_from_brevo
# ---------------------------------------------------------------------------


def remove_contact_from_brevo(
    contact_id: str, reason: str = "owner_removed"
) -> None:
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        try:
            _remove_one(session, contact_id, reason)
            session.commit()
        except Exception:
            session.rollback()
            raise


def _remove_one(session: Session, contact_id: str, reason: str) -> None:
    contact = session.get(Contact, contact_id)
    if contact is None or not contact.email:
        return

    account = _resolve_brevo_account(session)
    if account is None:
        logger.warning(
            "brevo.remove_from_brevo skip contact_id=%s reason=no_brevo_account",
            contact_id,
        )
        return

    email = contact.email
    mapped_lists = _service.mapped_list_ids(session)
    if not mapped_lists:
        return

    async def _drive() -> list[int]:
        async with BrevoClient(session, account.account_id) as client:
            try:
                existing = await client.get_contact(email)
            except IntegrationError as exc:
                if getattr(exc, "status_code", None) == 404:
                    return []
                raise
            current_lists = [
                int(lid) for lid in (existing.get("listIds") or [])
            ]
            removed: list[int] = []
            for lid in current_lists:
                if lid in mapped_lists:
                    try:
                        await client.remove_contacts_from_list(lid, [email])
                        removed.append(lid)
                    except IntegrationError as exc:
                        logger.warning(
                            "brevo.remove_from_brevo failed list=%s email=%s: %s",
                            lid, email, exc,
                        )
            return removed

    removed = asyncio.run(_drive())

    record_event(
        session,
        action=Action.BREVO_CONTACT_REMOVED,
        target_type="contact",
        target_id=contact_id,
        metadata={
            "list_ids": removed,
            "reason": reason,
            "email": email,
        },
    )


# ---------------------------------------------------------------------------
# Periodic push runner
# ---------------------------------------------------------------------------


def periodic_push_check(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Heartbeat: encola `push_contact` para todo contacto con owner
    pero sin `brevo_contact_id`. LIMIT por chunk para no saturar
    Brevo (rate limit 400 req/min). Self-reschedule."""
    _ = sync_log
    chunk = _int_env("BREVO_PUSH_PERIODIC_CHUNK", DEFAULT_PERIODIC_CHUNK)
    ids = list(
        session.scalars(
            _service.unsynced_contacts_query(session, limit=chunk)
        )
    )
    enqueued = 0
    for cid in ids:
        try:
            enqueue_push_contact(contact_id=cid)
            enqueued += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "brevo.periodic_push enqueue failed contact_id=%s: %s", cid, exc
            )
    schedule_periodic_push()
    return SyncOutcome(
        records_processed=enqueued,
        metadata={"detected": len(ids), "enqueued": enqueued},
    )


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


def schedule_periodic_push() -> None:
    hours = _int_env("BREVO_PUSH_INTERVAL_HOURS", DEFAULT_PUSH_INTERVAL_HOURS)
    interval = timedelta(hours=hours)
    try:
        from rq import Queue  # noqa: PLC0415

        conn = redis_connection()
        if not conn.set(
            PUSH_LOCK_KEY, "1", nx=True, ex=int(interval.total_seconds()) - 30
        ):
            return
        try:
            Queue(
                queue_name("brevo", "periodic_push"),
                connection=conn,
            ).enqueue_in(interval, _periodic_push_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("brevo.periodic_push scheduling failed: %s", exc)
            conn.delete(PUSH_LOCK_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("brevo.periodic_push redis unreachable: %s", exc)


def _periodic_push_runner() -> None:
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        fake = SyncLog(
            system="brevo", operation="periodic_push", status="running"
        )
        periodic_push_check(session, fake)


# Registro del operation handler para que `run_sync_job` lo encuentre
# si alguien lo dispara desde /api/brevo/sync.
OPERATIONS["brevo:periodic_push"] = periodic_push_check
