"""Cliente HTTP WooCommerce (REST API v3) — Fase B PR B-2.

Multi-tienda: se instancia POR cuenta de `integration_accounts` (una fila
por tienda: boprint / artisjet / flux). Los secretos CK/CS se persisten
cifrados con Fernet y se descifran on-demand.

Auth: HTTP Basic sobre HTTPS con Consumer Key/Secret.
Base: `{base_url}/wp-json/wc/v3/`. Paginación por headers
`X-WP-Total(Pages)`, máx 100/página.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.crypto import decrypt
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0


class WooError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = (body or "")[:2000]


@dataclass
class WooCredentials:
    base_url: str
    consumer_key: str
    consumer_secret: str

    @classmethod
    def from_account(cls, account: IntegrationAccount) -> WooCredentials:
        missing = [
            f for f in ("base_url", "consumer_key_encrypted", "consumer_secret_encrypted")
            if not getattr(account, f, None)
        ]
        if missing:
            raise WooError(
                f"Cuenta WooCommerce {account.account_id!r} incompleta: {missing}",
            )
        return cls(
            base_url=account.base_url.rstrip("/"),
            consumer_key=decrypt(account.consumer_key_encrypted),
            consumer_secret=decrypt(account.consumer_secret_encrypted),
        )


class WooHTTPClient:
    """Cliente síncrono (los jobs viven en RQ, no hace falta async).
    Se construye con la fila de `integration_accounts` — el descifrado
    solo vive en memoria de esta instancia."""

    def __init__(self, account: IntegrationAccount):
        self.account = account
        self.creds = WooCredentials.from_account(account)

    def _url(self, path: str) -> str:
        return f"{self.creds.base_url}/wp-json/wc/v3{path}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: dict[str, Any]) -> Any:
        return self._request("POST", path, json=json)

    def put(self, path: str, json: dict[str, Any]) -> Any:
        return self._request("PUT", path, json=json)

    def list_orders(
        self, *, status: str = "processing", since: str | None = None,
        per_page: int = 50, page: int = 1,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": per_page, "page": page, "orderby": "date"}
        if status:
            params["status"] = status
        if since:
            params["after"] = since
        return self.get("/orders", params=params)

    def get_order(self, order_id: int) -> dict[str, Any]:
        return self.get(f"/orders/{order_id}")

    def get_customer(self, customer_id: int) -> dict[str, Any]:
        return self.get(f"/customers/{customer_id}")

    def update_order(self, order_id: int, data: dict[str, Any]) -> dict[str, Any]:
        return self.put(f"/orders/{order_id}", json=data)

    def list_products(self, *, per_page: int = 100, page: int = 1) -> list[dict[str, Any]]:
        return self.get("/products", params={"per_page": per_page, "page": page})

    def iter_all_products(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.list_products(per_page=100, page=page)
            if not batch:
                return out
            out.extend(batch)
            page += 1

    # --- transporte -----------------------------------------------------------

    def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        url = self._url(path)
        attempt = 0
        while True:
            attempt += 1
            try:
                with httpx.Client(timeout=30.0) as c:
                    resp = c.request(
                        method, url, params=params or {}, json=json,
                        auth=(self.creds.consumer_key, self.creds.consumer_secret),
                    )
            except httpx.TransportError as exc:
                if attempt > MAX_RETRIES:
                    raise WooError(f"Error de red: {exc}") from exc
                self._sleep_backoff(attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt <= MAX_RETRIES:
                self._sleep_backoff(attempt)
                continue
            if resp.status_code >= 400:
                raise WooError(
                    f"{method} {path} → {resp.status_code}",
                    status=resp.status_code, body=resp.text,
                )
            return resp.json()

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        logger.info("woocommerce retry en %.1fs (intento %d)", wait, attempt)
        time.sleep(wait)
