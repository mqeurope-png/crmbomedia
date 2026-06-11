"""Google Calendar integration — per-user OAuth + task→event sync.

Surfaces exposed to the rest of the app:

- `oauth` — build the OAuth Flow, generate the consent URL, exchange
  the authorization code for tokens.
- `service` — high-level user-facing operations (connect, disconnect,
  set calendar, sync task to calendar).
- `client.GoogleCalendarClient` — thin wrapper around
  `googleapiclient.discovery.build` that handles auto-refresh of the
  access token and shields the rest of the app from the upstream API.

The module never logs tokens. Refresh-token loss is treated as a
graceful disconnect: the row is dropped and the user re-authenticates
on the next attempt.
"""
