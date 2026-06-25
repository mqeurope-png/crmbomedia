"""Sprint-Push-CRM-Brevo — orchestrador del push reverso (CRM → Brevo).

Bart (2026-06-25): el sync Brevo era unidireccional (lee de Brevo).
Quiere también el inverso: contactos del CRM con owner asignado se
suben a Brevo en la lista correspondiente al owner. Cambia el owner
→ se mueve de lista. Quitan el owner → se desuscribe de listas
gestionadas por CRM (no se borra el contacto de Brevo — preserva
histórico de campañas).

Este módulo concentra:

- **CRUD del mapping** owner→lista (`get_mapping`, `upsert_mapping`,
  `delete_mapping`, `list_all_mappings`, `mapped_list_ids`).

- **Detector de cambios de owner**: `_owner_change_listener` —
  SQLAlchemy event en `Contact.owner_user_id` antes de cada flush.
  Captura el (contact_id, old_owner, new_owner) en `session.info`.

- **Enqueuer post-commit**: listener `after_commit` que drena los
  eventos acumulados y encola `brevo:push_contact` o
  `brevo:remove_from_brevo`. Encolar ANTES del commit es una race —
  el worker podría procesar antes de que se vea la mutación.

- **`should_push(contact)`**: gate único — sin owner o sin email no
  se sube. El handler también lo comprueba como defensa.

Los handlers RQ viven en `app/integrations/brevo/push_jobs.py` y se
registran en el dispatch_table via import side-effect (mismo patrón
que el resto de integraciones)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.brevo import BrevoUserListMapping
from app.models.crm import Contact

logger = logging.getLogger(__name__)

#: Key bajo el cual acumulamos los eventos de cambio de owner pendientes
#: en `session.info`. Cada entrada es un tuple
#: `(contact_id, old_owner_user_id, new_owner_user_id)`.
_SESSION_EVENTS_KEY = "brevo_push_owner_events"


# ---------------------------------------------------------------------------
# Mapping CRUD
# ---------------------------------------------------------------------------


def get_mapping(
    session: Session, user_id: str
) -> BrevoUserListMapping | None:
    return session.get(BrevoUserListMapping, user_id)


def list_all_mappings(session: Session) -> list[BrevoUserListMapping]:
    return list(
        session.scalars(
            select(BrevoUserListMapping).order_by(BrevoUserListMapping.user_id)
        )
    )


def mapped_list_ids(session: Session) -> set[int]:
    """Set de TODOS los `brevo_list_id` que cualquier mapping usa. El
    handler de move quita el contacto de las listas mapeadas que NO son
    la del owner actual — pero preserva listas no gestionadas por CRM."""
    return set(
        session.scalars(select(BrevoUserListMapping.brevo_list_id))
    )


def upsert_mapping(
    session: Session,
    *,
    user_id: str,
    brevo_list_id: int,
    brevo_list_name: str | None,
) -> BrevoUserListMapping:
    existing = session.get(BrevoUserListMapping, user_id)
    now = datetime.now(UTC)
    if existing is not None:
        existing.brevo_list_id = brevo_list_id
        existing.brevo_list_name = brevo_list_name
        existing.updated_at = now
        session.flush()
        return existing
    row = BrevoUserListMapping(
        user_id=user_id,
        brevo_list_id=brevo_list_id,
        brevo_list_name=brevo_list_name,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def delete_mapping(session: Session, user_id: str) -> bool:
    existing = session.get(BrevoUserListMapping, user_id)
    if existing is None:
        return False
    session.delete(existing)
    session.flush()
    return True


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def should_push(contact: Contact) -> tuple[bool, str | None]:
    """`(yes, skip_reason)`. Reason = código corto para audit/log."""
    if not contact.owner_user_id:
        return False, "no_owner"
    if not contact.email:
        return False, "no_email"
    if not contact.is_active:
        return False, "inactive"
    return True, None


# ---------------------------------------------------------------------------
# Owner-change detector + after-commit enqueue
# ---------------------------------------------------------------------------


def record_owner_change(
    session: Session,
    contact_id: str,
    old_owner: str | None,
    new_owner: str | None,
) -> None:
    """Llamado desde `repositories/assignments.recompute_primary_cache`
    cuando detecta que el primary cambió. Acumula el evento en
    `session.info` para drenarse en after_commit. NO encolamos
    inline porque la transacción puede revertirse — el worker no debe
    ver mutaciones que aún no están commiteadas."""
    if old_owner == new_owner:
        return
    bucket: list[tuple[str, str | None, str | None]]
    bucket = session.info.setdefault(_SESSION_EVENTS_KEY, [])
    bucket.append((contact_id, old_owner, new_owner))


def _drain_events(session: Session) -> list[tuple[str, str | None, str | None]]:
    return list(session.info.pop(_SESSION_EVENTS_KEY, []))


def _after_commit(session: Session) -> None:
    """Drena `session.info[_SESSION_EVENTS_KEY]` y encola los jobs RQ
    correspondientes. Cualquier excepción se loguea pero no propaga —
    el sync periódico recoge cualquier contacto que se nos haya
    escapado (filtro `brevo_contact_id IS NULL`)."""
    events = _drain_events(session)
    if not events:
        return
    # Import diferido para evitar ciclo
    # (push_jobs → workers → services → push_jobs).
    from app.integrations.brevo import push_jobs  # noqa: PLC0415

    for contact_id, old_owner, new_owner in events:
        try:
            if new_owner is None:
                push_jobs.enqueue_remove_from_brevo(
                    contact_id=contact_id, reason="owner_removed"
                )
            else:
                push_jobs.enqueue_push_contact(contact_id=contact_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "brevo_push after_commit enqueue failed contact=%s old=%s new=%s: %s",
                contact_id, old_owner, new_owner, exc,
            )


def _after_rollback(session: Session) -> None:
    """Si la transacción aborta, descartamos eventos pendientes — no
    queremos encolar jobs sobre un cambio que no llegó a disco."""
    session.info.pop(_SESSION_EVENTS_KEY, None)


_LISTENERS_INSTALLED = False


def install_listeners() -> None:
    """Idempotente. Llamado desde app.main al startup. Tests pueden
    llamarlo también desde una fixture porque la segunda llamada es
    no-op."""
    global _LISTENERS_INSTALLED
    if _LISTENERS_INSTALLED:
        return
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_rollback", _after_rollback)
    _LISTENERS_INSTALLED = True


# ---------------------------------------------------------------------------
# Backfill helpers
# ---------------------------------------------------------------------------


def unsynced_contacts_query(session: Session, *, limit: int | None = None):
    """Query para contactos con owner asignado que AÚN no se han
    subido a Brevo. Lo consume el periodic push runner y el endpoint
    de backfill manual."""
    stmt = (
        select(Contact.id)
        .where(
            Contact.owner_user_id.is_not(None),
            Contact.brevo_contact_id.is_(None),
            Contact.email.is_not(None),
            Contact.is_active.is_(True),
        )
        .order_by(Contact.created_at.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt
