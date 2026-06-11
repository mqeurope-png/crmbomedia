"""Gmail API integration — send + watch + history.

Surfaces exposed to the rest of the app:

- `client.GmailClient` — per-user facade over `googleapiclient`
  with token auto-refresh (shared with the calendar client).
- `service` — high-level helpers used by the API and worker
  layers (send_email, process_history, watch_mailbox).
- `webhook` — Pub/Sub push receiver glued to the API router.

The module never logs tokens. Quota errors surface through the
existing `GoogleAuthExpiredError` so the calling site can react
the same way it would for a calendar 401.
"""
