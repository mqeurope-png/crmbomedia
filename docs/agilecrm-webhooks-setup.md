# AgileCRM real-time webhooks

The cron-based delta sync still runs every 12 h as a safety net, but
operators that want sub-minute latency between an AgileCRM contact
change and the BoHub CRM hook it up via per-account webhooks.

## How it works

1. Admin generates a per-account secret in `/admin/integrations`.
2. The CRM exposes `POST /api/webhooks/agilecrm/{account_id}/incoming?token=<secret>`.
3. AgileCRM is configured to POST to that URL on `add_contact`,
   `update_contact`, `delete_contact`.
4. The receiver answers `202` quickly and hands processing to an RQ
   worker (`agilecrm:webhook` queue).
5. The worker reuses the existing sync upsert — including the
   assignment-rules engine fire-on-create — so a brand-new contact
   lands assigned to its primary commercial within seconds.

## Per-account setup (repeat for each of the 9 AgileCRM tenants)

1. **CRM side — generate the URL**
   - Open `https://bo-crm.<your-host>/admin/integrations`.
   - Click the AgileCRM tab.
   - Expand the account card (e.g. `artisjet-europe`).
   - In the **Webhook real-time** section click
     **Generar URL de webhook**.
   - Copy the full URL that appears. It is shown only once —
     if you lose it you must `Regenerar` and update Agile again.

2. **Agile side — register the webhook**
   - Sign in to the AgileCRM tenant
     (e.g. `https://artisjet-europe.agilecrm.com`).
   - Go to **Admin Settings → Integrations → Webhooks → New Webhook**.
   - Paste the URL from the CRM.
   - Tick the events:
     - `add_contact`
     - `update_contact`
     - `delete_contact`
   - **Save**.

3. **Smoke test**
   - Create a contact in Agile.
   - In the CRM, open `/admin/integrations`, expand the same account
     card. Stats inside the **Webhook real-time** section should show
     `1 hoy` and the "Último evento" timestamp should match.
   - Open the contacts list and confirm the new lead appears, assigned
     to its commercial via the assignment-rules engine.

Repeat for every Agile account you want to push to real-time.

## Operational notes

- **Cron fallback** — the periodic 12 h sync stays armed
  (`AGILECRM_SYNC_INTERVAL_HOURS=12`). Even if Agile drops a webhook
  the next periodic delta picks it up.
- **Status indicator** — the card shows 🟢 active vs. 🟡 stale
  (no events in 7 days). Stale usually means Agile no longer points at
  the right URL (rotated secret, URL change, etc.).
- **Rotation** — `Regenerar secret` invalidates the previous URL
  immediately. Update Agile within minutes of clicking it or you lose
  events.
- **Disable** — `Desactivar` clears the secret server-side. The
  endpoint returns `200 + status=skipped` for every subsequent delivery
  so Agile does not retry forever.
- **Audit log** — every received / processed / failed / skipped event
  is recorded in `audit_logs` with `target_type=webhook_event` and the
  full payload in `webhook_events.payload_json` (truncated to 64 KB).
- **Rate limiting** — 500 events per minute per source IP by default;
  configurable with `WEBHOOK_RATE_LIMIT_PER_MIN`.

## Replays / troubleshooting

Webhook deliveries land in the `webhook_events` table regardless of
outcome. To replay a failed event from psql/mysql:

```sql
-- Find the failed event:
SELECT id, event_type, error_summary
FROM webhook_events
WHERE system = 'agilecrm'
  AND account_id = 'artisjet-europe'
  AND status = 'failed'
ORDER BY received_at DESC
LIMIT 10;

-- Reset and re-enqueue from a worker shell:
--   from app.integrations.agilecrm.webhooks import process_agilecrm_webhook_job
--   process_agilecrm_webhook_job("<id>")
```

A future PR will surface the same operation through the admin UI.
