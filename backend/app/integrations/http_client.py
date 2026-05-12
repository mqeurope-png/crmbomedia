"""Shared async HTTP client for outbound calls to external systems.

Every connector (AgileCRM, Brevo, Freshdesk, FactuSOL) builds on top of
this class. Responsibilities:

- Load the account row from `integration_accounts` and decrypt its API
  key via the existing crypto helper. The plaintext lives only inside
  the client instance.
- Wrap `httpx.AsyncClient` with sane defaults: configurable timeout
  (`INTEGRATION_HTTP_TIMEOUT_SECONDS`, default 30) and retries with
  exponential backoff (`INTEGRATION_HTTP_MAX_RETRIES`, default 3).
  Retry-After-aware 429 handling.
- Translate every failure mode into an explicit subclass of
  `IntegrationError` so the connector can react without parsing raw
  status codes.
- Emit one audit row per call (`integration.api_call`) with the HTTP
  method, path, status code and duration. **Never** logs the body.
- Bump `api_key_last_used_at` on each successful call.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.audit import Action, record_event
from app.core.crypto import decrypt
from app.db.session import get_engine
from app.integrations.errors import (
    IntegrationAuthError,
    IntegrationClientError,
    IntegrationNetworkError,
    IntegrationRateLimitError,
    IntegrationServerError,
)
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount
from app.repositories.integration_settings import touch_api_key_use

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 3
# Cap on Retry-After: a remote that asks us to wait 10 minutes is
# better served by failing the job and letting RQ reschedule than by
# blocking a worker for that long.
RETRY_AFTER_HARD_CAP_SECONDS = 60.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _debug_enabled() -> bool:
    return os.environ.get("INTEGRATION_HTTP_DEBUG", "").lower() in {"1", "true", "yes", "on"}


def _mask_secret(value: str) -> str:
    """Compact mask for sensitive header values.

    Keeps the first 12 and last 4 characters so an operator can spot
    whether the credential changed between deploys without seeing the
    full secret in the logs. Strings shorter than 16 characters are
    fully redacted because there's no safe truncation that protects
    them.
    """
    if not value:
        return ""
    if len(value) < 20:
        return "***"
    return f"{value[:12]}...{value[-4:]}"


def _sanitize_headers(headers: object) -> dict[str, str]:
    """Return a copy of `headers` with `Authorization` (and other
    obvious secrets) masked. Accepts any mapping-like (httpx.Headers,
    dict, list of tuples) so it works on both request and response."""
    sensitive = {"authorization", "x-api-key", "apikey", "x-auth-token"}
    masked: dict[str, str] = {}
    if hasattr(headers, "items"):
        pairs: list[tuple[Any, Any]] = list(headers.items())  # type: ignore[attr-defined]
    else:
        try:
            pairs = list(headers)  # type: ignore[arg-type, call-overload]
        except TypeError:
            return {}
    for key, value in pairs:
        key_s = str(key)
        if key_s.lower() in sensitive:
            masked[key_s] = _mask_secret(str(value))
        else:
            masked[key_s] = str(value)
    return masked


@dataclass
class IntegrationResponse:
    """Thin facade over `httpx.Response`. The connector gets the parsed
    body via `json` and the raw httpx response via `raw` for unusual
    cases (streaming, custom headers)."""

    status_code: int
    json: Any
    text: str
    headers: dict[str, str]
    raw: httpx.Response


class IntegrationHTTPClient:
    """Per-account HTTP client. Use as an async context manager:

        async with IntegrationHTTPClient(session, "agilecrm", "es") as client:
            data = await client.get("/api/v1/contacts")
    """

    def __init__(
        self,
        session: Session,
        system: str | ExternalSystem,
        account_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str | None = "Bearer",
    ) -> None:
        system_enum = ExternalSystem(system) if isinstance(system, str) else system
        account = session.scalar(
            select(IntegrationAccount).where(
                IntegrationAccount.system == system_enum,
                IntegrationAccount.account_id == account_id,
            )
        )
        if account is None:
            raise IntegrationAuthError(
                f"Integration account {system_enum.value}/{account_id} does not exist",
                system=system_enum.value,
                account_id=account_id,
            )
        self._session = session
        self._account = account
        self.system: str = system_enum.value
        self.account_id: str = account_id
        # Allow the caller to provide the API key + base URL inline so
        # tests don't need to populate the account row with a real key.
        self._api_key = (
            api_key
            if api_key is not None
            else (decrypt(account.api_key_encrypted) if account.api_key_encrypted else None)
        )
        self.base_url: str = (base_url or account.api_base_url or "").rstrip("/")
        self.timeout = httpx.Timeout(
            timeout_seconds
            if timeout_seconds is not None
            else _env_float("INTEGRATION_HTTP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        )
        self.max_retries = int(
            max_retries
            if max_retries is not None
            else _env_float("INTEGRATION_HTTP_MAX_RETRIES", DEFAULT_MAX_RETRIES)
        )
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> IntegrationHTTPClient:
        headers: dict[str, str] = {}
        if self._api_key:
            headers[self._auth_header] = (
                f"{self._auth_scheme} {self._api_key}" if self._auth_scheme else self._api_key
            )
        self._client = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=self.timeout
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, url: str, **kwargs: Any) -> IntegrationResponse:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> IntegrationResponse:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> IntegrationResponse:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> IntegrationResponse:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> IntegrationResponse:
        return await self.request("DELETE", url, **kwargs)

    async def request(self, method: str, url: str, **kwargs: Any) -> IntegrationResponse:
        if self._client is None:
            raise RuntimeError(
                "IntegrationHTTPClient must be entered via `async with` before use."
            )
        client = self._client
        debug = _debug_enabled()

        async def _do() -> httpx.Response:
            if debug:
                # Build the request without sending so we can introspect
                # the final URL + merged headers (httpx merges client
                # defaults with per-request kwargs); then send the same
                # request via the client's `send`. This guarantees the
                # log reflects exactly what hits the wire.
                request_obj = client.build_request(method, url, **kwargs)
                logger.info(
                    "integration.http.request method=%s url=%s headers=%s",
                    request_obj.method,
                    str(request_obj.url),
                    _sanitize_headers(request_obj.headers),
                )
                return await client.send(request_obj)
            return await client.request(method, url, **kwargs)

        retry = AsyncRetrying(
            stop=stop_after_attempt(max(self.max_retries, 1)),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception(_should_retry),
            reraise=True,
        )

        started = time.monotonic()
        response: httpx.Response | None = None
        try:
            async for attempt in retry:
                with attempt:
                    try:
                        response = await _do()
                    except httpx.NetworkError as exc:
                        raise IntegrationNetworkError(
                            f"Network error talking to {self.system}/{self.account_id}: {exc}",
                            system=self.system,
                            account_id=self.account_id,
                        ) from exc
                    except httpx.TimeoutException as exc:
                        raise IntegrationNetworkError(
                            f"Timeout talking to {self.system}/{self.account_id}",
                            system=self.system,
                            account_id=self.account_id,
                        ) from exc
                    self._maybe_raise_for_status(response)
        except RetryError as exc:  # pragma: no cover - reraise=True is the default path
            raise exc.last_attempt.exception() from exc  # type: ignore[misc]

        assert response is not None
        duration_ms = int((time.monotonic() - started) * 1000)
        self._record_call(
            method=method, url=url, status_code=response.status_code, duration_ms=duration_ms
        )
        self._touch_account_use()

        try:
            json_body: Any = response.json() if response.content else None
        except ValueError:
            json_body = None
        return IntegrationResponse(
            status_code=response.status_code,
            json=json_body,
            text=response.text,
            headers=dict(response.headers),
            raw=response,
        )

    def _maybe_raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        body_snippet = (response.text or "")[:512]
        # Debug-only: full response details for the operator chasing a
        # `500 from agilecrm/...` that succeeds with curl. Up to 2000
        # chars of body so a JSON error payload fits without flooding
        # the log.
        if _debug_enabled():
            logger.error(
                "integration.http.response_error status=%s headers=%s body=%s",
                status,
                _sanitize_headers(response.headers),
                (response.text or "")[:2000],
            )
        if status in (401, 403):
            self._mark_account_credential_error()
            record_event(
                self._session,
                action=Action.INTEGRATION_AUTH_FAILED,
                target_type="integration_account",
                target_id=self._account.id,
                metadata={
                    "system": self.system,
                    "account_id": self.account_id,
                    "status_code": status,
                },
            )
            self._session.commit()
            raise IntegrationAuthError(
                "Authentication failed against the remote API",
                system=self.system,
                account_id=self.account_id,
                status_code=status,
                body=body_snippet,
            )
        if status == 429:
            retry_after_raw = response.headers.get("retry-after")
            retry_after: float | None = None
            if retry_after_raw:
                try:
                    retry_after = float(retry_after_raw)
                except ValueError:
                    retry_after = None
            raise _RetryableRateLimit(
                "Rate limited by the remote",
                system=self.system,
                account_id=self.account_id,
                status_code=429,
                body=body_snippet,
                sleep_seconds=retry_after,
                retry_after_seconds=retry_after,
            )
        if 400 <= status < 500:
            raise IntegrationClientError(
                f"{status} from {self.system}/{self.account_id}",
                system=self.system,
                account_id=self.account_id,
                status_code=status,
                body=body_snippet,
            )
        # 5xx
        raise IntegrationServerError(
            f"{status} from {self.system}/{self.account_id}",
            system=self.system,
            account_id=self.account_id,
            status_code=status,
            body=body_snippet,
        )

    def _record_call(
        self, *, method: str, url: str, status_code: int, duration_ms: int
    ) -> None:
        path = urlsplit(url).path or url
        record_event(
            self._session,
            action=Action.INTEGRATION_API_CALL,
            target_type="integration_account",
            target_id=self._account.id,
            metadata={
                "system": self.system,
                "account_id": self.account_id,
                "method": method,
                "url_path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
        )
        self._session.commit()

    def _mark_account_credential_error(self) -> None:
        self._account.credential_status = "error"
        self._session.flush()

    def _touch_account_use(self) -> None:
        touch_api_key_use(self._session, self._account)
        self._session.commit()


class _RetryableRateLimit(IntegrationRateLimitError):
    """Sentinel raised on 429 to drive the tenacity retry loop. After
    the retry budget is exhausted the same exception surfaces to the
    caller as a plain `IntegrationRateLimitError`. Sleeps for the
    (capped) Retry-After hint inside `__init__` so the next attempt
    actually leaves the remote enough breathing room."""

    def __init__(self, *args: object, sleep_seconds: float | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        if sleep_seconds and sleep_seconds > 0:
            time.sleep(min(sleep_seconds, RETRY_AFTER_HARD_CAP_SECONDS))


def _should_retry(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (_RetryableRateLimit, IntegrationServerError, IntegrationNetworkError),
    )


async def request_with_session(
    system: str,
    account_id: str,
    method: str,
    url: str,
    *,
    session: Session | None = None,
    **kwargs: Any,
) -> IntegrationResponse:
    """Convenience wrapper that opens a SQLAlchemy session, builds the
    client, runs one request and tears the client down. Long-running
    loops should instantiate the client once and reuse it inside
    `async with`."""
    owns_session = session is None
    if owns_session:
        session = Session(get_engine())
    try:
        async with IntegrationHTTPClient(session, system, account_id) as client:  # type: ignore[arg-type]
            return await client.request(method, url, **kwargs)
    finally:
        if owns_session and session is not None:
            session.close()
