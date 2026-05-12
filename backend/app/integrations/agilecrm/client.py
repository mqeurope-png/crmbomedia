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

from app.integrations.errors import IntegrationAuthError, IntegrationClientError
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
        # AgileCRM's cursor pagination convention: if the page returned
        # exactly `page_size` items there's likely another page. The
        # `cursor` value for the next call is the ID of the last item.
        next_cursor: str | None = None
        if len(items) >= size and items:
            tail = items[-1]
            if isinstance(tail, dict):
                next_cursor = str(tail.get("id")) if tail.get("id") is not None else None
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

    async def count_contacts(self) -> int:
        """Return the total contact count for this account. AgileCRM
        exposes `/dev/api/contacts/count` as plain text; we accept both
        text and JSON responses for robustness."""
        response = await self.get("/dev/api/contacts/count")
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
            return 0


async def _close(client: httpx.AsyncClient | None) -> None:  # pragma: no cover - helper
    """Tiny helper kept so tests can mirror the production teardown
    pattern when they construct a client without the context manager."""
    if client is not None:
        await client.aclose()
