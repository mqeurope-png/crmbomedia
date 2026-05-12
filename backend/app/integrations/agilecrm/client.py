"""AgileCRM REST client.

AgileCRM authenticates with HTTP Basic where the username is the
operator's AgileCRM login email and the password is the API key. We
store both in `integration_accounts.api_key_encrypted` as a single
string `"<email>:<api_key>"`; the client splits on the first colon at
construction time. This keeps the schema unchanged at the cost of a
documented convention (see `docs/integrations-architecture.md`
"AgileCRM").

API base URL is per-tenant on AgileCRM, e.g. `https://acme.agilecrm.com`;
the operator stores it in `integration_accounts.api_base_url`.

Rate limits (rough): AgileCRM Free tier ≈ 200 req/h. The base
`IntegrationHTTPClient` already honours `Retry-After` on 429 so a
saturated quota just slows the sync down; the job is idempotent.
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.integrations.errors import IntegrationAuthError, IntegrationClientError
from app.integrations.http_client import IntegrationHTTPClient

USER_AGENT = "CRMBO-Media-CRM/1.0 (mqeurope-png/crmbomedia)"

DEFAULT_PAGE_SIZE = int(os.environ.get("AGILECRM_PAGE_SIZE", "50") or "50")
MAX_PAGE_SIZE = 100


class AgileCRMClient(IntegrationHTTPClient):
    """`IntegrationHTTPClient` subclass that knows AgileCRM's endpoints.

    Construct with the same `(session, system='agilecrm', account_id)`
    contract; the constructor parses the stored credential string and
    sets up Basic auth + the User-Agent header on the underlying
    `httpx.AsyncClient` created by `__aenter__`."""

    def __init__(self, session, account_id: str, **kwargs: Any) -> None:
        # `auth_scheme=None` means "don't prepend Bearer"; we'll set
        # the Authorization header manually because the base client
        # otherwise tries to interpret the credential as a token.
        super().__init__(
            session,
            "agilecrm",
            account_id,
            auth_header="Authorization",
            auth_scheme=None,
            **kwargs,
        )
        if not self._api_key:
            raise IntegrationAuthError(
                "AgileCRM account has no API credential configured",
                system=self.system,
                account_id=account_id,
            )
        if ":" not in self._api_key:
            raise IntegrationAuthError(
                "AgileCRM credential must be stored as 'email:api_key'",
                system=self.system,
                account_id=account_id,
            )
        email, _, api_key = self._api_key.partition(":")
        self._email = email.strip()
        self._raw_api_key = api_key.strip()
        if not self._email or not self._raw_api_key:
            raise IntegrationAuthError(
                "AgileCRM credential must include both an email and an API key",
                system=self.system,
                account_id=account_id,
            )
        encoded = base64.b64encode(
            f"{self._email}:{self._raw_api_key}".encode()
        ).decode("ascii")
        # Overwrite the parent's `_api_key` so `__aenter__` builds the
        # correct `Authorization: Basic ...` header instead of leaking
        # the raw `email:api_key` string.
        self._api_key = f"Basic {encoded}"

    async def __aenter__(self) -> AgileCRMClient:  # type: ignore[override]
        client = await super().__aenter__()
        # Set the User-Agent on the live httpx.AsyncClient; the parent
        # already configured Authorization.
        if self._client is not None:
            self._client.headers["User-Agent"] = USER_AGENT
            self._client.headers["Accept"] = "application/json"
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
        # JSON int / dict / plain text — handle the three flavours.
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
