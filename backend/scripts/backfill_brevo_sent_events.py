"""Backfill retroactivo de eventos `email.sent` de campañas Brevo.

PR-Fix-Sent-Backfill. El filtro "Enviados" (`brevo_campaign_interaction`
action=sent) devuelve 0 para campañas antiguas porque:

  1. El webhook en vivo solo crea `email.sent` desde que Brevo está
     suscrito al evento `sent` (config del dashboard, ver PR body).
  2. El backfill histórico por export (`historical_backfill`) deriva
     delivered/opened/clicked/bounced/spam del CSV pero NUNCA emite
     `email.sent`.

Este script recupera los `email.sent` perdidos: para cada destinatario
real de la campaña (leído del export de Brevo, `recipientsType=all`) crea
una fila `activity_events` con `event_type='email.sent'`, matcheando el
contacto por email. Idempotente (external_id determinista); `--dry-run`
solo cuenta.

NO es una migración Alembic — se ejecuta manualmente tras el deploy.

Uso:
    python -m scripts.backfill_brevo_sent_events --campaign-id 1234 --dry-run
    python -m scripts.backfill_brevo_sent_events --campaign-id 1234
    python -m scripts.backfill_brevo_sent_events --all-campaigns --dry-run
    python -m scripts.backfill_brevo_sent_events --all-campaigns
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.integrations.brevo.client import BrevoClient
from app.integrations.brevo.historical_backfill import (
    SENT_STATUSES,
    _external_id,
    _fetch_campaign_export,
    _normalise_email,
)
from app.integrations.errors import IntegrationError
from app.models.brevo import BrevoCampaignCache
from app.models.crm import ActivityEvent, Contact

log = logging.getLogger("backfill_brevo_sent_events")
logging.basicConfig(level=logging.INFO, format="%(message)s")

SENT_EVENT_TYPE = "email.sent"


def backfill_sent_for_campaign(
    session: Session,
    *,
    brevo_campaign_id: int,
    account_id: str,
    recipient_emails: list[str],
    sent_at: datetime,
    dry_run: bool = False,
) -> dict[str, int]:
    """Crea `email.sent` para cada destinatario que matchee un Contact.

    Idempotente: la clave `backfill:{cid}:{email}:email.sent` (misma que
    `historical_backfill`) sobre la UNIQUE (system, account_id,
    external_id) evita duplicar. `dry_run` cuenta pero no escribe.
    """
    counts = {
        "recipients": len(recipient_emails),
        "matched": 0,
        "created": 0,
        "skipped_existing": 0,
        "unmatched_email": 0,
    }
    # Normaliza + dedupe emails de entrada.
    emails: list[str] = []
    seen: set[str] = set()
    for raw in recipient_emails:
        email = _normalise_email(raw)
        if not email or email in seen:
            continue
        seen.add(email)
        emails.append(email)
    if not emails:
        return counts

    contact_by_email = {
        (c.email or "").strip().lower(): c.id
        for c in session.scalars(
            select(Contact).where(func.lower(Contact.email).in_(emails))
        )
    }
    now = datetime.now(UTC)
    for email in emails:
        contact_id = contact_by_email.get(email)
        if contact_id is None:
            counts["unmatched_email"] += 1
            continue
        counts["matched"] += 1
        external_id = _external_id(brevo_campaign_id, email, SENT_EVENT_TYPE)
        exists = session.scalar(
            select(ActivityEvent.id).where(
                ActivityEvent.system == "brevo",
                ActivityEvent.account_id == account_id,
                ActivityEvent.external_id == external_id,
            )
        )
        if exists is not None:
            counts["skipped_existing"] += 1
            continue
        counts["created"] += 1
        if not dry_run:
            session.add(
                ActivityEvent(
                    contact_id=contact_id,
                    system="brevo",
                    account_id=account_id,
                    external_id=external_id,
                    event_type=SENT_EVENT_TYPE,
                    campaign_brevo_id=brevo_campaign_id,
                    metadata_json=json.dumps(
                        {
                            "source": "backfill_sent_events",
                            "recipient_email": email,
                            "campaign_brevo_id": brevo_campaign_id,
                        },
                        default=str,
                    ),
                    occurred_at=sent_at,
                    synced_at=now,
                )
            )
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return counts


def _fetch_recipient_emails(session: Session, campaign: BrevoCampaignCache) -> list[str]:
    """Lee los destinatarios reales de la campaña vía el export de Brevo
    (`recipientsType=all`). Devuelve los emails de la columna `Email_ID`."""

    async def _drive() -> list[dict[str, str]]:
        async with BrevoClient(session, campaign.brevo_account_id) as client:
            rows, error = await _fetch_campaign_export(
                client, campaign.brevo_campaign_id
            )
            if error:
                log.warning("  export error: %s", error)
            return rows

    csv_rows = asyncio.run(_drive())
    emails = [str(r.get("Email_ID") or "") for r in csv_rows]
    return [e for e in emails if e]


def _campaigns_to_process(
    session: Session, *, campaign_id: int | None, all_campaigns: bool
) -> list[BrevoCampaignCache]:
    stmt = select(BrevoCampaignCache).where(
        BrevoCampaignCache.status.in_(SENT_STATUSES)
    )
    if campaign_id is not None:
        stmt = stmt.where(BrevoCampaignCache.brevo_campaign_id == campaign_id)
    elif not all_campaigns:
        return []
    return list(session.scalars(stmt))


def run(*, campaign_id: int | None, all_campaigns: bool, dry_run: bool) -> dict[str, int]:
    engine = get_engine()
    total = {
        "campaigns": 0, "recipients": 0, "matched": 0,
        "created": 0, "skipped_existing": 0, "unmatched_email": 0,
    }
    with Session(engine) as session:
        campaigns = _campaigns_to_process(
            session, campaign_id=campaign_id, all_campaigns=all_campaigns
        )
        if not campaigns:
            log.info("No hay campañas enviadas que procesar.")
            return total
        for campaign in campaigns:
            log.info(
                "Campaña %s (brevo_id=%s) — %s",
                campaign.name, campaign.brevo_campaign_id,
                "DRY-RUN" if dry_run else "aplicando",
            )
            try:
                emails = _fetch_recipient_emails(session, campaign)
            except IntegrationError as exc:
                log.warning("  Brevo error, se salta: %s", exc.message)
                continue
            sent_at = campaign.sent_at or campaign.created_at_brevo or datetime.now(UTC)
            counts = backfill_sent_for_campaign(
                session,
                brevo_campaign_id=campaign.brevo_campaign_id,
                account_id=campaign.brevo_account_id,
                recipient_emails=emails,
                sent_at=sent_at,
                dry_run=dry_run,
            )
            log.info(
                "  destinatarios=%d matched=%d creados=%d ya_existían=%d sin_contacto=%d",
                counts["recipients"], counts["matched"], counts["created"],
                counts["skipped_existing"], counts["unmatched_email"],
            )
            total["campaigns"] += 1
            for k in ("recipients", "matched", "created", "skipped_existing", "unmatched_email"):
                total[k] += counts[k]
    log.info(
        "TOTAL: campañas=%d creados=%d ya_existían=%d sin_contacto=%d%s",
        total["campaigns"], total["created"], total["skipped_existing"],
        total["unmatched_email"], "  (dry-run, nada escrito)" if dry_run else "",
    )
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--campaign-id", type=int, help="brevo_campaign_id concreto")
    group.add_argument("--all-campaigns", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(
        campaign_id=args.campaign_id,
        all_campaigns=args.all_campaigns,
        dry_run=args.dry_run,
    )
