"""AgileCRM REST client.

AgileCRM authenticates with **HTTP Basic** where the username is the
operator's AgileCRM login email and the password is the API key. The
email is **not** secret and lives in
`integration_accounts.auth_identifier` (plain text); the API key lives
in `integration_accounts.api_key_encrypted` (Fernet).

A previous iteration stored both as a single `email:api_key` string in
the encrypted column. The client still accepts that legacy format with
a `DeprecationWarning` so existing deploys keep working until the
operator re-saves the credentials via the new UI.

AgileCRM's REST API defaults to **XML** unless the request asks for
JSON explicitly via `Accept: application/json`; this client forces the
header at httpx construction time so every request gets it.

API base URL is per-tenant on AgileCRM, e.g. `https://acme.agilecrm.com`;
the operator stores it in `integration_accounts.api_base_url`.

Rate limits (rough): AgileCRM Free tier ≈ 200 req/h. The base
`IntegrationHTTPClient` already honours `Retry-After` on 429 so a
saturated quota just slows the sync down; the job is idempotent.
"""
from __future__ import annotations

import base64
import logging
import os
import warnings
from typing import Any

import httpx

from app.integrations.errors import (
    IntegrationAuthError,
    IntegrationClientError,
    IntegrationServerError,
)
from app.integrations.http_client import IntegrationHTTPClient

logger = logging.getLogger(__name__)

USER_AGENT = "CRMBO-Media-CRM/1.0 (mqeurope-png/crmbomedia)"

DEFAULT_PAGE_SIZE = int(os.environ.get("AGILECRM_PAGE_SIZE", "50") or "50")
MAX_PAGE_SIZE = 100

# Headers we want on every outbound call. They live on
# `httpx.AsyncClient.headers` (set in `__aenter__`) so per-request
# overrides still take precedence.
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": USER_AGENT,
}


class AgileCRMClient(IntegrationHTTPClient):
    """`IntegrationHTTPClient` subclass that knows AgileCRM's endpoints.

    Construct with the same `(session, account_id)` contract. The
    constructor reads the account's `auth_identifier` + decrypted API
    key, builds Basic auth, and forces `Accept/Content-Type:
    application/json` so AgileCRM doesn't fall back to XML."""

    def __init__(self, session, account_id: str, **kwargs: Any) -> None:
        # `auth_scheme=None` tells the parent not to prepend "Bearer"
        # — we'll compose the `Authorization: Basic <b64>` header
        # ourselves once we have both halves of the credential.
        super().__init__(
            session,
            "agilecrm",
            account_id,
            auth_header="Authorization",
            auth_scheme=None,
            **kwargs,
        )
        identifier, api_key = self._resolve_credential(account_id)
        self._email = identifier
        self._raw_api_key = api_key
        encoded = base64.b64encode(
            f"{identifier}:{api_key}".encode()
        ).decode("ascii")
        # Overwrite the parent's `_api_key` so `__aenter__` builds the
        # correct `Authorization: Basic ...` header instead of leaking
        # the raw credentials into the header value.
        self._api_key = f"Basic {encoded}"

    def _resolve_credential(self, account_id: str) -> tuple[str, str]:
        """Return `(email, api_key)`. Prefers
        `auth_identifier` + decrypted `api_key_encrypted`; falls back
        to the legacy `email:api_key` blob in the encrypted column with
        a deprecation warning so existing deploys keep working until
        re-saved.
        """
        identifier = (self._account.auth_identifier or "").strip()
        secret = (self._api_key or "").strip()

        if identifier and secret and ":" not in secret:
            return identifier, secret

        if identifier and secret and ":" in secret:
            # Both fields set AND the legacy blob is present. Trust the
            # explicit identifier and use the whole secret as the key
            # — operators sometimes paste `email:key` into the API key
            # field even after providing the email separately. Strip
            # the leading "email:" if it matches the identifier.
            prefix = f"{identifier}:"
            if secret.startswith(prefix):
                return identifier, secret[len(prefix):]
            return identifier, secret

        if not identifier and secret and ":" in secret:
            warnings.warn(
                "AgileCRM account uses the legacy 'email:api_key' format in "
                "the encrypted column. Move the email to `auth_identifier` "
                "via the integrations admin UI — the legacy format will be "
                "rejected in a future release.",
                DeprecationWarning,
                stacklevel=3,
            )
            email, _, api_key = secret.partition(":")
            email = email.strip()
            api_key = api_key.strip()
            if not email or not api_key:
                raise IntegrationAuthError(
                    "AgileCRM legacy credential is malformed (expected 'email:api_key')",
                    system=self.system,
                    account_id=account_id,
                )
            return email, api_key

        raise IntegrationAuthError(
            "AgileCRM necesita auth_identifier (email) y api_key. Configura "
            "ambos en /admin/integrations.",
            system=self.system,
            account_id=account_id,
        )

    async def __aenter__(self) -> AgileCRMClient:  # type: ignore[override]
        client = await super().__aenter__()
        # The parent's __aenter__ already populated the Authorization
        # header. Force the JSON / User-Agent defaults so every request
        # — including those that don't pass an explicit `headers`
        # kwarg — picks them up. Direct dict update so per-request
        # kwargs can still override them.
        if self._client is not None:
            self._client.headers.update(DEFAULT_HEADERS)
        return client  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def list_contacts(
        self,
        *,
        page_size: int | None = None,
        cursor: str | None = None,
        order_by: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch one page of contacts. Returns the list and the
        next-page cursor (None when exhausted). AgileCRM responds with
        a JSON array; cursor pagination is driven by the `cursor` query
        param + `page_size` (max ~100 per AgileCRM docs)."""
        size = min(page_size or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
        params: dict[str, Any] = {"page_size": size}
        if cursor:
            params["cursor"] = cursor
        if order_by:
            params["order_by"] = order_by
        response = await self.get("/dev/api/contacts", params=params)
        items = response.json if isinstance(response.json, list) else []
        # AgileCRM ships an opaque `cursor` field on each contact in the
        # response (a base64-looking GAE datastore continuation token,
        # NOT the contact's id). The last item in a full page carries
        # the cursor that the next page request should pass back; when
        # the page underflows OR the last item has no cursor field, we
        # are at the end of the dataset.
        next_cursor: str | None = None
        if len(items) >= size and items:
            tail = items[-1]
            if isinstance(tail, dict):
                cursor_value = tail.get("cursor")
                if isinstance(cursor_value, str) and cursor_value:
                    next_cursor = cursor_value
        return items, next_cursor

    async def get_contact(self, external_id: str) -> dict[str, Any] | None:
        try:
            response = await self.get(f"/dev/api/contacts/{external_id}")
        except IntegrationClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        if isinstance(response.json, dict):
            return response.json
        return None

    async def delete_contact(self, external_id: str) -> None:
        try:
            await self.delete(f"/dev/api/contacts/{external_id}")
        except IntegrationClientError as exc:
            if exc.status_code == 404:
                return
            raise

    # ------------------------------------------------------------------
    # Per-contact sub-resources (notes, tasks, activities)
    # ------------------------------------------------------------------
    #
    # AgileCRM's sub-endpoints follow a few variants depending on the
    # tenant's plan. We default to the documented paths and silently
    # treat 404 as "no rows" — typical for fresh contacts. Any other
    # 4xx/5xx bubbles up via `IntegrationClientError` /
    # `IntegrationServerError`; the sync job's per-contact try/except
    # downgrades that to a warning so one flaky sub-resource never
    # aborts the whole import.

    async def list_contact_notes(self, contact_id: str) -> list[dict[str, Any]]:
        """Notes attached to one contact. Endpoint:
        `GET /dev/api/contacts/{id}/notes`."""
        return await self._list_subresource(f"/dev/api/contacts/{contact_id}/notes")

    async def list_contact_tasks(self, contact_id: str) -> list[dict[str, Any]]:
        """Tasks attached to one contact. Endpoint:
        `GET /dev/api/tasks/contact/{id}`. (`/api/contacts/{id}/tasks`
        is a documented alternative but ships task ids only — this one
        returns the full task payload.)"""
        return await self._list_subresource(f"/dev/api/tasks/contact/{contact_id}")

    async def list_contact_activities(self, contact_id: str) -> list[dict[str, Any]]:
        """Timeline events for one contact. Endpoint:
        `GET /dev/api/activities/contact/{id}`. Returns AgileCRM's
        timeline rows (EMAIL_SENT, FORM_FILL, NOTE, …)."""
        return await self._list_subresource(
            f"/dev/api/activities/contact/{contact_id}"
        )

    async def _list_subresource(self, path: str) -> list[dict[str, Any]]:
        """Shared helper for the per-contact sub-resources. AgileCRM
        responds with a top-level JSON array; 404 is treated as an empty
        list so a brand-new contact (no notes/tasks/activities yet)
        doesn't raise."""
        try:
            response = await self.get(path)
        except IntegrationClientError as exc:
            if exc.status_code == 404:
                return []
            raise
        if isinstance(response.json, list):
            return [item for item in response.json if isinstance(item, dict)]
        return []

    async def count_contacts(self) -> int | None:
        """Return the total contact count for this account, or `None`
        when AgileCRM refuses to answer.

        AgileCRM's documented `GET /dev/api/contacts/count` endpoint is
        flaky across tenants — some installations 400 with no clear
        reason, others return plain text, others JSON. We try the
        documented endpoint first; on any 4xx or 5xx we fall back to
        `None` so callers (notably `purge_agilecrm_quota`) can skip the
        purge gracefully instead of erroring the whole job.

        The plain-text / JSON-int / JSON-dict parsing layers are kept
        in priority order because real installations have been seen
        returning each of the three shapes.
        """
        try:
            response = await self.get("/dev/api/contacts/count")
        except (IntegrationClientError, IntegrationServerError):
            # The base client already audited the call and applied
            # retries. There's nothing useful left to do with a count
            # endpoint that won't reply — let the caller decide.
            return None
        if isinstance(response.json, int):
            return response.json
        if isinstance(response.json, dict):
            count = response.json.get("count")
            if isinstance(count, int):
                return count
        text = response.text.strip().strip('"')
        try:
            return int(text)
        except ValueError:
            return None


async def _close(client: httpx.AsyncClient | None) -> None:  # pragma: no cover - helper
    """Tiny helper kept so tests can mirror the production teardown
    pattern when they construct a client without the context manager."""
    if client is not None:
        await client.aclose()
