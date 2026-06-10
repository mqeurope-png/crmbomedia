"""Brevo (ex-Sendinblue) REST v3 client.

Auth: a single `api-key` header (no Bearer scheme). The key lives in
`integration_accounts.api_key_encrypted` (Fernet) like every other
connector; `IntegrationHTTPClient` decrypts it on construction.

Base URL is fixed (`https://api.brevo.com/v3`) unless the account row
overrides `api_base_url` (useful for tests / future EU sovereign
endpoints).

Rate limits: Brevo replies 429 + `Retry-After`. The parent client
already honours the header with exponential fallback and 3 retries,
so this subclass adds nothing — just keep individual jobs paced when
they loop over thousands of contacts.

Duplicate handling: `POST /contacts` returns 400 with
`code=duplicate_parameter` when the email already exists. That's an
expected branch during push syncs, so it surfaces as the dedicated
`IntegrationDuplicateError` for callers to catch and convert into an
update.
"""
from __future__ import annotations

import logging
from typing import Any

from app.integrations.errors import (
    IntegrationClientError,
    IntegrationDuplicateError,
)
from app.integrations.http_client import IntegrationHTTPClient

logger = logging.getLogger(__name__)

BREVO_BASE_URL = "https://api.brevo.com/v3"
DEFAULT_PAGE_SIZE = 50
LIST_BATCH_SIZE = 100  # Brevo accepts up to ~150 emails per list call

#: Per-endpoint `limit` ceilings. Brevo rejects anything above with
#: `400 {"code":"out_of_range","message":"Limit exceeds max value"}`
#: instead of silently clamping, so the client clamps before sending.
#: Verified against production during the first segments import (the
#: 100-row page on /contacts/segments crashed the job).
SEGMENTS_MAX_PAGE_SIZE = 50
CONTACTS_MAX_PAGE_SIZE = 1000


def _clamp_limit(limit: int, maximum: int) -> int:
    return max(1, min(limit, maximum))

DEFAULT_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
}


class BrevoClient(IntegrationHTTPClient):
    """Same `(session, account_id)` contract as the AgileCRM client."""

    def __init__(self, session, account_id: str, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", BREVO_BASE_URL)
        super().__init__(
            session,
            "brevo",
            account_id,
            auth_header="api-key",
            auth_scheme=None,
            **kwargs,
        )

    async def __aenter__(self) -> BrevoClient:
        await super().__aenter__()
        assert self._client is not None
        self._client.headers.update(DEFAULT_HEADERS)
        return self

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def list_contacts(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        modified_since: str | None = None,
    ) -> dict[str, Any]:
        """GET /contacts → {"contacts": [...], "count": int}."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if modified_since:
            params["modifiedSince"] = modified_since
        response = await self.get("/contacts", params=params)
        body = response.json or {}
        return {
            "contacts": body.get("contacts") or [],
            "count": int(body.get("count") or 0),
        }

    async def get_contact(self, identifier: str) -> dict[str, Any]:
        response = await self.get(f"/contacts/{identifier}")
        return response.json or {}

    async def create_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /contacts. Raises `IntegrationDuplicateError` when the
        email is already registered so the push engine can fall back
        to an update."""
        try:
            response = await self.post("/contacts", json=payload)
        except IntegrationClientError as exc:
            if _is_duplicate_error(exc):
                raise IntegrationDuplicateError(
                    f"Brevo contact already exists: {payload.get('email')!r}",
                    system=self.system,
                    account_id=self.account_id,
                    status_code=exc.status_code,
                    body=exc.body,
                ) from exc
            raise
        return response.json or {}

    async def update_contact(
        self, identifier: str, payload: dict[str, Any]
    ) -> None:
        """PUT /contacts/{id-or-email}. Brevo returns 204 on success."""
        await self.put(f"/contacts/{identifier}", json=payload)

    async def delete_contact(self, identifier: str) -> None:
        await self.delete(f"/contacts/{identifier}")

    # ------------------------------------------------------------------
    # Lists + folders
    # ------------------------------------------------------------------

    async def list_lists(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> dict[str, Any]:
        response = await self.get(
            "/contacts/lists", params={"limit": limit, "offset": offset}
        )
        body = response.json or {}
        return {
            "lists": body.get("lists") or [],
            "count": int(body.get("count") or 0),
        }

    async def get_list(self, list_id: int) -> dict[str, Any]:
        response = await self.get(f"/contacts/lists/{list_id}")
        return response.json or {}

    async def create_list(
        self, name: str, folder_id: int | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if folder_id is not None:
            payload["folderId"] = folder_id
        else:
            # Brevo requires a folderId; resolve the first folder
            # lazily when the caller didn't pin one.
            folders = await self.list_folders()
            first = (folders.get("folders") or [{}])[0]
            payload["folderId"] = int(first.get("id") or 1)
        response = await self.post("/contacts/lists", json=payload)
        return response.json or {}

    async def add_contacts_to_list(
        self, list_id: int, emails: list[str]
    ) -> dict[str, Any]:
        response = await self.post(
            f"/contacts/lists/{list_id}/contacts/add",
            json={"emails": emails},
        )
        return response.json or {}

    async def remove_contacts_from_list(
        self, list_id: int, emails: list[str]
    ) -> dict[str, Any]:
        response = await self.post(
            f"/contacts/lists/{list_id}/contacts/remove",
            json={"emails": emails},
        )
        return response.json or {}

    async def list_folders(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> dict[str, Any]:
        response = await self.get(
            "/contacts/folders", params={"limit": limit, "offset": offset}
        )
        body = response.json or {}
        return {
            "folders": body.get("folders") or [],
            "count": int(body.get("count") or 0),
        }

    # ------------------------------------------------------------------
    # Segments (Sprint Brevo follow-up — mirrored as static CRM segments)
    # ------------------------------------------------------------------

    async def list_segments(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> dict[str, Any]:
        """GET /contacts/segments → {"segments": [...], "count": int}.

        Brevo's API doesn't expose the segment rule tree (filter
        logic lives in the UI), so the CRM imports each segment as a
        mirror: name + count + the periodic refresh of its member
        list via `get_segment_contacts`.

        The endpoint hard-caps `limit` at 50 (`out_of_range` above);
        the clamp keeps a sloppy caller from crashing the sync."""
        response = await self.get(
            "/contacts/segments",
            params={
                "limit": _clamp_limit(limit, SEGMENTS_MAX_PAGE_SIZE),
                "offset": offset,
            },
        )
        body = response.json or {}
        return {
            "segments": body.get("segments") or [],
            "count": int(body.get("count") or 0),
        }

    async def get_segment_contacts(
        self,
        segment_id: int,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Paginated membership of a Brevo segment.

        Production lesson (debt-closure Bug 4): there is NO
        `/contacts/segments/{id}/contacts` route in Brevo v3 — it
        404s with `Invalid route/method passed`. The supported way to
        read a segment's membership is the generic contacts listing
        filtered by `segmentId`:

            GET /contacts?segmentId={id}&limit=...&offset=...

        If Brevo ever rejects the filter on an account (the param is
        comparatively recent), the caller in `segments.py` degrades
        gracefully — it keeps the previous membership and surfaces
        the limitation in the mirror's description instead of wiping
        the segment."""
        response = await self.get(
            "/contacts",
            params={
                "segmentId": segment_id,
                "limit": _clamp_limit(limit, CONTACTS_MAX_PAGE_SIZE),
                "offset": offset,
            },
        )
        body = response.json or {}
        return {
            "contacts": body.get("contacts") or [],
            "count": int(body.get("count") or 0),
        }

    # ------------------------------------------------------------------
    # Email templates (Sprint B+D §M)
    # ------------------------------------------------------------------

    async def list_email_templates(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> dict[str, Any]:
        response = await self.get(
            "/smtp/templates", params={"limit": limit, "offset": offset}
        )
        body = response.json or {}
        return {
            "templates": body.get("templates") or [],
            "count": int(body.get("count") or 0),
        }

    async def get_email_template(self, template_id: int) -> dict[str, Any]:
        response = await self.get(f"/smtp/templates/{template_id}")
        return response.json or {}

    async def create_email_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.post("/smtp/templates", json=payload)
        return response.json or {}

    async def update_email_template(
        self, template_id: int, payload: dict[str, Any]
    ) -> None:
        await self.put(f"/smtp/templates/{template_id}", json=payload)

    async def delete_email_template(self, template_id: int) -> None:
        await self.delete(f"/smtp/templates/{template_id}")

    async def send_test_template(
        self, template_id: int, email_to: list[str]
    ) -> None:
        await self.post(
            f"/smtp/templates/{template_id}/sendTest",
            json={"emailTo": email_to},
        )

    # ------------------------------------------------------------------
    # Email campaigns (Sprint B+D §N)
    # ------------------------------------------------------------------

    async def list_email_campaigns(
        self,
        *,
        status: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        response = await self.get("/emailCampaigns", params=params)
        body = response.json or {}
        return {
            "campaigns": body.get("campaigns") or [],
            "count": int(body.get("count") or 0),
        }

    async def get_email_campaign(self, campaign_id: int) -> dict[str, Any]:
        response = await self.get(f"/emailCampaigns/{campaign_id}")
        return response.json or {}

    async def create_email_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.post("/emailCampaigns", json=payload)
        return response.json or {}

    async def update_email_campaign(
        self, campaign_id: int, payload: dict[str, Any]
    ) -> None:
        await self.put(f"/emailCampaigns/{campaign_id}", json=payload)

    async def delete_email_campaign(self, campaign_id: int) -> None:
        await self.delete(f"/emailCampaigns/{campaign_id}")

    async def send_email_campaign_now(self, campaign_id: int) -> None:
        await self.post(f"/emailCampaigns/{campaign_id}/sendNow")

    async def send_test_email_campaign(
        self, campaign_id: int, email_to: list[str]
    ) -> None:
        await self.post(
            f"/emailCampaigns/{campaign_id}/sendTest",
            json={"emailTo": email_to},
        )

    async def schedule_email_campaign(
        self, campaign_id: int, scheduled_at: str
    ) -> None:
        """Brevo schedules via PUT with `scheduledAt` ISO8601."""
        await self.put(
            f"/emailCampaigns/{campaign_id}",
            json={"scheduledAt": scheduled_at},
        )

    async def update_campaign_status(
        self, campaign_id: int, status: str
    ) -> None:
        """PUT /emailCampaigns/{id}/status — e.g. `suspended` to cancel
        a scheduled send, `draft` to fully unschedule."""
        await self.put(
            f"/emailCampaigns/{campaign_id}/status",
            json={"status": status},
        )

    async def get_campaign_stats(self, campaign_id: int) -> dict[str, Any]:
        """Stats ride along on the campaign detail response."""
        return await self.get_email_campaign(campaign_id)

    async def get_campaign_recipients_stats(
        self,
        campaign_id: int,
        event_type: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> dict[str, Any]:
        """GET /emailCampaigns/{id}/{event_type} — opens, clicks, …"""
        response = await self.get(
            f"/emailCampaigns/{campaign_id}/{event_type}",
            params={"limit": limit, "offset": offset},
        )
        return response.json or {}

    # ------------------------------------------------------------------
    # Senders (Sprint B+D §O)
    # ------------------------------------------------------------------

    async def list_senders(self) -> list[dict[str, Any]]:
        response = await self.get("/senders")
        body = response.json or {}
        return body.get("senders") or []


def _is_duplicate_error(exc: IntegrationClientError) -> bool:
    """Brevo signals an existing email as 400 + code
    `duplicate_parameter`. The body sometimes only carries the human
    message, so match both."""
    if exc.status_code != 400:
        return False
    body = (exc.body or "").lower()
    return "duplicate_parameter" in body or "already exist" in body
