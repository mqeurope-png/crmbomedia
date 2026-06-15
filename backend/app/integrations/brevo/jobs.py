"""Brevo sync jobs — read direction (Brevo → CRM).

One handler registered into `app.workers.jobs.OPERATIONS`:

- `brevo:sync_contacts` — paginate `/contacts` (optionally
  `modifiedSince` for delta runs) and upsert each row. Same
  consolidation story as AgileCRM: a Brevo contact whose email
  already exists in the CRM (e.g. imported from AgileCRM) gets an
  additional `external_references` row instead of a duplicate
  contact.

The write direction (push targets) lives in `sync_targets.py` to keep
this module focused on the pull.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.brevo.client import BrevoClient
from app.integrations.brevo.mapper import (
    brevo_external_id,
    map_brevo_contact_to_internal,
)
from app.integrations.contact_merge import keep_first_origin, merge_external_dates
from app.models.crm import (
    Company,
    Contact,
    ContactTag,
    EmailUnsubscribe,
    EmailUnsubscribeScope,
    ExternalReference,
    ExternalSystem,
    SyncLog,
    Tag,
)
from app.models.integration_settings import IntegrationAccount
from app.services.company_extraction import (
    extract_company_domain,
    normalise_domain,
)
from app.workers.jobs import OPERATIONS, SyncOutcome
from app.workers.queues import redis_connection

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
MAX_CONTACTS_PER_SYNC = 50_000
#: Redis lock TTL — generous enough for a full first sync; expires on
#: its own so a crashed worker doesn't deadlock the account.
SYNC_LOCK_TTL_SECONDS = 3600


def _load_account(session: Session, account_id: str) -> IntegrationAccount:
    account = session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.system == ExternalSystem.BREVO,
            IntegrationAccount.account_id == account_id,
        )
    )
    if account is None:
        raise ValueError(f"Brevo account {account_id!r} not configured")
    return account


def _acquire_lock(name: str, *, ttl: int = SYNC_LOCK_TTL_SECONDS) -> bool:
    """SETNX-style lock so two workers never run the same account's
    sync concurrently. Returns False when someone else holds it."""
    conn = redis_connection()
    return bool(conn.set(name, "1", nx=True, ex=ttl))


def _release_lock(name: str) -> None:
    try:
        redis_connection().delete(name)
    except Exception:  # noqa: BLE001 - lock expires by TTL anyway
        logger.warning("brevo.lock release failed for %s", name)


def _last_successful_sync_at(
    session: Session, account_id: str
) -> datetime | None:
    """Most recent finished successful read sync for this account —
    the delta watermark. A small overlap window (5 min) is subtracted
    by the caller so clock skew between us and Brevo can't drop rows."""
    return session.scalar(
        select(func.max(SyncLog.finished_at)).where(
            SyncLog.system == ExternalSystem.BREVO,
            SyncLog.account_id == account_id,
            SyncLog.operation == "sync_contacts",
            SyncLog.status == "success",
        )
    )


def resolve_brevo_company(
    session: Session,
    attributes: dict[str, Any],
    *,
    fallback_email: str | None = None,
) -> str | None:
    """Sprint Empresas. Resolve (or create) the company a Brevo
    contact belongs to. Tries the explicit EMPRESA + CIF + WEB
    attributes first, then falls back to the email domain.

    Returns the Company id, or None when neither path yields a
    usable company (e.g. free-mail contact with no Brevo
    `EMPRESA` field).
    """

    def _attr(key: str) -> str | None:
        raw = attributes.get(key) or attributes.get(key.upper())
        if raw is None:
            return None
        s = str(raw).strip()
        return s or None

    empresa = _attr("EMPRESA")
    cif = _attr("CIF")
    web = _attr("WEB")
    domain = normalise_domain(web)

    company: Company | None = None
    if empresa:
        if cif:
            company = session.scalar(
                select(Company).where(Company.tax_id == cif)
            )
        if company is None and domain:
            company = session.scalar(
                select(Company).where(Company.domain == domain)
            )
        if company is None:
            refs_payload = {"brevo": {"empresa": empresa}}
            company = Company(
                name=empresa,
                website=web,
                domain=domain,
                tax_id=cif,
                address_line=_attr("ADDRESS"),
                city=_attr("CIUDAD"),
                state=_attr("PROVINCIA"),
                postal_code=_attr("CODIGO_POSTAL"),
                country=_attr("PAIS"),
                region=_attr("PAIS_REGION"),
                source="brevo",
                external_references_json=json.dumps(refs_payload),
            )
            session.add(company)
            session.flush()
        return company.id

    # Fall back to the email domain. Free-mail addresses return
    # None and the contact stays company-less.
    email_domain = extract_company_domain(fallback_email)
    if not email_domain:
        return None
    company = session.scalar(
        select(Company).where(Company.domain == email_domain)
    )
    if company is None:
        from app.services.company_extraction import (  # noqa: PLC0415
            derive_company_name_from_domain,
        )

        company = Company(
            name=derive_company_name_from_domain(email_domain),
            domain=email_domain,
            source="auto-domain",
        )
        session.add(company)
        session.flush()
    return company.id


def reconcile_brevo_unsubscribe(
    session: Session,
    *,
    contact_id: str,
    payload: dict[str, Any],
) -> bool:
    """Sprint Empresas — sub-PR 2/4. Materialise an `EmailUnsubscribe`
    row whenever the Brevo payload flags the contact as opted out
    (the `emailBlacklisted` boolean OR a truthy `EMAILABLE_UNSUBSCRIBED`
    custom attribute).

    Idempotent: skips when a row with `(contact_id, source='brevo')`
    already exists for the marketing scope. Returns True when a
    new row was added.
    """
    if not contact_id:
        return False
    attributes = payload.get("attributes") or {}
    if not isinstance(attributes, dict):
        attributes = {}
    blacklisted = bool(payload.get("emailBlacklisted"))
    custom_unsub = attributes.get("EMAILABLE_UNSUBSCRIBED") or attributes.get(
        "emailable_unsubscribed"
    )
    is_unsub = blacklisted or bool(
        str(custom_unsub or "").strip().lower()
        in {"1", "true", "yes", "si", "sí"}
    )
    if not is_unsub:
        return False

    existing = session.scalar(
        select(EmailUnsubscribe).where(
            EmailUnsubscribe.contact_id == contact_id,
            EmailUnsubscribe.source == "brevo",
            EmailUnsubscribe.scope == EmailUnsubscribeScope.MARKETING,
        )
    )
    if existing is not None:
        return False

    token = secrets.token_urlsafe(32)
    session.add(
        EmailUnsubscribe(
            contact_id=contact_id,
            scope=EmailUnsubscribeScope.MARKETING,
            source="brevo",
            token=token,
            unsubscribed_at=datetime.now(UTC),
            metadata_json=json.dumps(
                {
                    "brevo_external_id": str(payload.get("id") or ""),
                    "emailBlacklisted": blacklisted,
                    "custom_unsubscribed": bool(custom_unsub),
                },
                default=str,
            ),
        )
    )
    return True


def upsert_brevo_contact(
    session: Session,
    *,
    account_id: str,
    payload: dict[str, Any],
    list_names: dict[int, str] | None = None,
) -> tuple[str, str]:
    """Insert or update one internal contact for a Brevo payload.
    Returns `(action, contact_id)` with action ∈ {created, updated,
    skipped}."""
    external_id = brevo_external_id(payload)
    if not external_id:
        raise ValueError("Brevo payload missing 'id'")

    record, ref_extras = map_brevo_contact_to_internal(
        payload, account_id, list_names=list_names
    )
    email = record.get("email")
    tag_names: list[str] = record.pop("tag_names", []) or []
    # Sprint Empresas — resolve / create the company before we
    # apply the record so a new Contact picks up `company_id` on
    # the first INSERT.
    attributes = payload.get("attributes") if isinstance(payload, dict) else None
    company_id = resolve_brevo_company(
        session,
        attributes if isinstance(attributes, dict) else {},
        fallback_email=email,
    )
    if company_id is not None:
        record["company_id"] = company_id

    # 1. Existing reference for THIS account → update in place.
    ref = session.scalar(
        select(ExternalReference).where(
            ExternalReference.system == ExternalSystem.BREVO,
            ExternalReference.account_id == account_id,
            ExternalReference.external_id == external_id,
        )
    )
    if ref is not None:
        contact = session.get(Contact, ref.contact_id)
        if contact is None:  # orphan ref; treat as new below
            session.delete(ref)
            session.flush()
        else:
            _apply_update(contact, record)
            _apply_ref_extras(ref, ref_extras)
            _sync_list_tag_delta(
                session,
                contact_id=contact.id,
                account_id=account_id,
                desired_names=tag_names,
            )
            reconcile_brevo_unsubscribe(
                session, contact_id=contact.id, payload=payload
            )
            session.flush()
            return ("updated", contact.id)

    # 2. Same email exists from another system → consolidate.
    if email:
        existing = session.scalar(
            select(Contact).where(func.lower(Contact.email) == email)
        )
        if existing is not None:
            session.add(
                _build_ref(account_id, external_id, existing.id, ref_extras)
            )
            _apply_update(existing, record, allow_email_overwrite=False)
            _sync_list_tag_delta(
                session,
                contact_id=existing.id,
                account_id=account_id,
                desired_names=tag_names,
            )
            reconcile_brevo_unsubscribe(
                session, contact_id=existing.id, payload=payload
            )
            session.flush()
            return ("updated", existing.id)

    # 3. Brand-new contact. A Brevo row without a usable email is
    # functionally unreachable for marketing — skip instead of
    # creating an empty shell.
    if not email:
        logger.warning(
            "brevo.sync skipping contact without usable email external_id=%s",
            external_id,
        )
        return ("skipped", "")

    contact = Contact(**record)
    session.add(contact)
    session.flush()
    session.add(_build_ref(account_id, external_id, contact.id, ref_extras))
    _sync_list_tag_delta(
        session,
        contact_id=contact.id,
        account_id=account_id,
        desired_names=tag_names,
    )
    reconcile_brevo_unsubscribe(
        session, contact_id=contact.id, payload=payload
    )
    session.flush()
    return ("created", contact.id)


def _build_ref(
    account_id: str,
    external_id: str,
    contact_id: str,
    extras: dict[str, Any],
) -> ExternalReference:
    ref = ExternalReference(
        system=ExternalSystem.BREVO,
        account_id=account_id,
        external_id=external_id,
        contact_id=contact_id,
    )
    _apply_ref_extras(ref, extras)
    return ref


def _apply_ref_extras(ref: ExternalReference, extras: dict[str, Any]) -> None:
    if not extras:
        return
    if extras.get("external_created_at") is not None:
        ref.external_created_at = extras["external_created_at"]
    if extras.get("external_updated_at") is not None:
        ref.external_updated_at = extras["external_updated_at"]
    if extras.get("origin_detail"):
        ref.origin_detail = extras["origin_detail"]
    if extras.get("metadata"):
        ref.metadata_json = json.dumps(extras["metadata"], default=str)


def _apply_update(
    contact: Contact,
    record: dict[str, Any],
    *,
    allow_email_overwrite: bool = True,
) -> None:
    # Shared merge policy across connectors: first origin wins, oldest
    # external creation, newest external update. Both helpers pop their
    # keys so the generic loop can't overwrite them.
    keep_first_origin(contact, record)
    merge_external_dates(contact, record)
    for key, value in record.items():
        if value in (None, "") and key != "tags":
            continue
        if key == "email" and not allow_email_overwrite:
            continue
        setattr(contact, key, value)


def _sync_list_tag_delta(
    session: Session,
    *,
    contact_id: str,
    account_id: str,
    desired_names: list[str],
) -> None:
    """Reconcile `brevo-list:*` auto-tags sourced from this account.
    Mirrors the AgileCRM `_sync_tag_delta` semantics: only assignments
    with `source == brevo:<account>` are touched, so manual tags and
    other connectors' tags survive."""
    source = f"brevo:{account_id}"
    desired_normalized = {name.lower(): name for name in desired_names}

    existing_rows = list(
        session.scalars(
            select(ContactTag).where(
                ContactTag.contact_id == contact_id,
                ContactTag.source == source,
            )
        )
    )
    existing_ids = {row.tag_id for row in existing_rows}
    normalized_by_id = {
        tag.id: tag.name_normalized
        for tag in session.scalars(select(Tag).where(Tag.id.in_(existing_ids)))
    } if existing_ids else {}

    for row in existing_rows:
        normalized = normalized_by_id.get(row.tag_id)
        if normalized is None or normalized not in desired_normalized:
            session.delete(row)

    for normalized, original in desired_normalized.items():
        tag = session.scalar(select(Tag).where(Tag.name_normalized == normalized))
        if tag is None:
            tag = Tag(name=original, name_normalized=normalized)
            session.add(tag)
            session.flush()
        link = session.get(
            ContactTag, {"contact_id": contact_id, "tag_id": tag.id}
        )
        if link is None:
            session.add(
                ContactTag(contact_id=contact_id, tag_id=tag.id, source=source)
            )


# ---------------------------------------------------------------------------
# sync_contacts handler
# ---------------------------------------------------------------------------


def sync_brevo_contacts(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Worker handler: paginate Brevo contacts and upsert each one.

    Delta by default — only contacts modified since the last
    successful run (minus a 5-minute overlap) are fetched. A payload
    flag `{"full_sync": true}` forces the full walk; the UI's
    "Resincronizar todo" button sends it.
    """
    account_id = sync_log.account_id or ""
    _load_account(session, account_id)

    payload_meta: dict[str, Any] = {}
    if sync_log.metadata_json:
        try:
            decoded = json.loads(sync_log.metadata_json)
            if isinstance(decoded, dict):
                payload_meta = decoded.get("payload") or decoded
        except (ValueError, TypeError):
            payload_meta = {}
    full_sync = bool(payload_meta.get("full_sync"))

    lock_name = f"brevo:sync_contacts:{account_id}"
    if not _acquire_lock(lock_name):
        return SyncOutcome(
            records_failed=1,
            error_summary=(
                "Otro sync de esta cuenta Brevo ya está en ejecución; "
                "reintenta cuando termine."
            ),
        )

    modified_since: str | None = None
    if not full_sync:
        watermark = _last_successful_sync_at(session, account_id)
        if watermark is not None:
            if watermark.tzinfo is None:
                watermark = watermark.replace(tzinfo=UTC)
            modified_since = (
                (watermark - timedelta(minutes=5)).isoformat()
            )

    created = 0
    updated = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    async def _drive() -> None:
        nonlocal created, updated, skipped, failed
        async with BrevoClient(session, account_id) as client:
            # One upfront fetch of the list catalogue so membership →
            # tag mapping never needs a per-contact API call.
            lists_body = await client.list_lists(limit=50, offset=0)
            list_names = {
                int(item.get("id")): str(item.get("name") or item.get("id"))
                for item in lists_body.get("lists") or []
                if item.get("id") is not None
            }
            offset = 0
            total_seen = 0
            while total_seen < MAX_CONTACTS_PER_SYNC:
                page = await client.list_contacts(
                    limit=PAGE_SIZE,
                    offset=offset,
                    modified_since=modified_since,
                )
                contacts = page["contacts"]
                if not contacts:
                    break
                for item in contacts:
                    total_seen += 1
                    try:
                        action, _cid = upsert_brevo_contact(
                            session,
                            account_id=account_id,
                            payload=item,
                            list_names=list_names,
                        )
                        if action == "created":
                            created += 1
                        elif action == "updated":
                            updated += 1
                        else:
                            skipped += 1
                    except Exception as exc:  # noqa: BLE001 - row-level isolation
                        failed += 1
                        if len(errors) < 100:
                            errors.append(
                                f"contact id={item.get('id')}: {exc}"
                            )
                session.commit()
                offset += PAGE_SIZE
                if len(contacts) < PAGE_SIZE:
                    break

    try:
        asyncio.run(_drive())
    finally:
        _release_lock(lock_name)

    return SyncOutcome(
        records_processed=created + updated,
        records_skipped=skipped,
        records_failed=failed,
        error_summary="\n".join(errors) if errors else None,
        metadata={
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "full_sync": full_sync,
            "modified_since": modified_since,
        },
    )


OPERATIONS["brevo:sync_contacts"] = sync_brevo_contacts
