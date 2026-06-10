"""Materialise past Brevo campaign events into `activity_events`.

When the live webhook is configured AFTER campaigns have already been
sent, the CRM has aggregated stats but no per-recipient timeline for
those historical campaigns. This script reads the existing campaign
cache and drives the asynchronous Brevo export flow (one process per
campaign × per recipientsType) to materialise one `activity_events`
row per (recipient, recipientsType).

Idempotent: a second run inserts zero new rows. Dedup rides on the
UNIQUE `(system, account_id, external_id)` constraint, where
`external_id` encodes `(campaign, recipient, event)`.

Webhooks never create contacts; the backfill follows the same rule.
Recipient emails that don't match any CRM contact are counted as
`contacts_unknown` and skipped.

# Runtime expectations

Each campaign triggers 5 Brevo export processes (`openers`,
`clickers`, `softBouncers`, `hardBouncers`, `unsubscribed`) that the
worker polls until completed. Brevo's export queue has no SLA but
empirically each process resolves between 10 seconds and a few
minutes:

  - Best case:   ~30 s × 5 buckets × N campaigns  (≈ 2.5 min / camp.)
  - Worst case:  ~5 min × 5 buckets × N campaigns (≈ 25 min / camp.)
  - Typical:     ~1-2 min total per campaign

For an account with 60-80 historical campaigns expect a 3-5 h run.
Worst case (a campaign hitting the 30 min timeout) is rare but
possible — that bucket is skipped, the run continues.

Recommended invocation: run overnight via `nohup` so SSH disconnects
don't kill the job and the polling stays uninterrupted:

    docker compose --env-file .env.production exec -d api bash -c \\
      "nohup python scripts/backfill_brevo_email_history.py \\
        --account-id default \\
        > /tmp/backfill_$(date +%Y%m%d_%H%M%S).log 2>&1 &"

Tail the log to monitor progress; the script prints one final JSON
summary line. Re-run safely if it gets interrupted — every event
already inserted lands in `events_skipped_existing`.

For an ad-hoc small run with the foreground attached:

    docker compose --env-file .env.production exec api \\
        python scripts/backfill_brevo_email_history.py \\
            --account-id default --max-campaigns 10

This is a **one-shot** operation — once the historical backfill has
landed, the live webhook covers everything going forward.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.session import get_engine  # noqa: E402
from app.integrations.brevo.historical_backfill import (  # noqa: E402
    backfill_account_campaigns,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_brevo_email_history")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--account-id",
        default="default",
        help="Brevo account_id (matches integration_accounts.account_id).",
    )
    parser.add_argument(
        "--max-campaigns",
        type=int,
        default=None,
        help="Cap the run at the N most recent sent campaigns.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print what would be processed without writing anything. "
            "Counts every recipient as 'would_insert' and rolls the "
            "transaction back at the end."
        ),
    )
    args = parser.parse_args()

    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    with factory() as session:
        if args.dry_run:
            session.begin()
            stats = backfill_account_campaigns(
                session,
                account_id=args.account_id,
                max_campaigns=args.max_campaigns,
            )
            session.rollback()
            stats["dry_run"] = True
        else:
            stats = backfill_account_campaigns(
                session,
                account_id=args.account_id,
                max_campaigns=args.max_campaigns,
            )

    summary = {
        key: stats[key]
        for key in (
            "campaigns_processed",
            "campaigns_skipped",
            "events_inserted_total",
            "events_skipped_total",
            "contacts_unknown_total",
        )
        if key in stats
    }
    summary["errors"] = len(stats.get("errors") or [])
    if args.dry_run:
        summary["dry_run"] = True
    logger.info("Backfill complete: %s", json.dumps(summary, default=str))
    if stats.get("errors"):
        logger.warning("First errors: %s", stats["errors"][:5])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
