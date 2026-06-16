"""Exception hierarchy for the integration HTTP client.

Connector code catches `IntegrationError` (the base) when it wants to
treat every failure uniformly; more specific subclasses let a caller
distinguish between "the remote credentials are bad" (no retry), "the
remote rate-limited us" (backoff), and "the remote is down" (retry).
"""
from __future__ import annotations


class IntegrationError(Exception):
    """Base class for every error raised by `IntegrationHTTPClient`."""

    def __init__(
        self,
        message: str,
        *,
        system: str | None = None,
        account_id: str | None = None,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.system = system
        self.account_id = account_id
        self.status_code = status_code
        # `body` is a short snippet (truncated by the caller) for
        # diagnostics; never log full payloads here because they may
        # contain customer data.
        self.body = body

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        bits = [self.message]
        if self.system:
            bits.append(f"system={self.system}")
        if self.account_id:
            bits.append(f"account={self.account_id}")
        if self.status_code:
            bits.append(f"status={self.status_code}")
        return " ".join(bits)


class IntegrationSkipped(IntegrationError):
    """La cuenta no puede sincronizarse pero NO es un fallo: está
    deshabilitada por el operador o pendiente de configurar. El
    handler en `app/workers/jobs.py` mapea esta excepción a
    `SyncStatus.SKIPPED` en vez de `FAILED`, para que la UI muestre
    "Saltada" en gris y no haga ruido como un error real."""


class IntegrationAuthError(IntegrationError):
    """401/403 from the remote — the stored credentials are bad. The HTTP
    client flips `credential_status='error'` on the account before
    raising this so the operator sees it in the UI."""


class IntegrationRateLimitError(IntegrationError):
    """429 from the remote, beyond the client's retry budget."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        system: str | None = None,
        account_id: str | None = None,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(
            message,
            system=system,
            account_id=account_id,
            status_code=status_code,
            body=body,
        )
        self.retry_after_seconds = retry_after_seconds


class IntegrationClientError(IntegrationError):
    """Any other 4xx — bad request, not found, conflict. Not retried."""


class IntegrationDuplicateError(IntegrationClientError):
    """The remote rejected a create because the resource already exists
    (e.g. Brevo's `duplicate_parameter` on POST /contacts). Callers in
    push flows catch this and fall back to an update."""


class IntegrationServerError(IntegrationError):
    """5xx that survived the retry budget."""


class IntegrationNetworkError(IntegrationError):
    """`httpx.NetworkError` / DNS / connection refused beyond retries."""
