"""Cliente Sprint 0 de la REST API de WooCommerce (v3) — solo LECTURA.

PROTOTIPO DE DESCUBRIMIENTO — NO producción. WooCommerce es la fuente de
pedidos online + estado de pago del ERP. Bomedia tiene varias tiendas
(mbolasers.com, artisjet-europe.com, fluxlasers.es); se empieza por
mbolasers.com.

API bien conocida y estable (https://woocommerce.github.io/woocommerce-rest-api-docs/):
  - Base: {store}/wp-json/wc/v3/
  - Auth sobre HTTPS: HTTP Basic con Consumer Key/Secret (generados en
    WP admin → WooCommerce → Ajustes → Avanzado → REST API, permiso Read).
  - Paginación: ?page=N&per_page=M (max 100) + headers X-WP-Total /
    X-WP-TotalPages.
  - Rate limit: WooCommerce no impone uno propio; lo puede imponer el
    hosting (LiteSpeed/WAF). Retry conservador ante 429/5xx.

Credenciales por entorno (.env.local; en el MVP → integration_accounts
cifradas, una fila por tienda):
  WOO_MBOLASERS_BASE_URL / WOO_MBOLASERS_CONSUMER_KEY / _CONSUMER_SECRET
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0


class WooError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = (body or "")[:2000]


@dataclass
class WooClient:
    """Cliente de lectura para UNA tienda. El prefijo de entorno permite
    instanciar otras tiendas sin tocar código: WooClient(prefix="WOO_ARTISJET")."""

    prefix: str = "WOO_MBOLASERS"
    base_url: str = field(default="")
    consumer_key: str = field(default="", repr=False)
    consumer_secret: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or os.environ.get(f"{self.prefix}_BASE_URL", "")).rstrip("/")
        self.consumer_key = self.consumer_key or os.environ.get(f"{self.prefix}_CONSUMER_KEY", "")
        self.consumer_secret = (
            self.consumer_secret or os.environ.get(f"{self.prefix}_CONSUMER_SECRET", "")
        )
        if not (self.base_url and self.consumer_key and self.consumer_secret):
            raise WooError(
                f"Credenciales {self.prefix}_* incompletas — rellenar .env.local "
                "(Bart genera Consumer Key/Secret en WP admin)."
            )

    # --- lecturas -------------------------------------------------------------

    def list_orders(
        self, *, status: str | None = None, since: str | None = None,
        per_page: int = 20, page: int = 1,
    ) -> list[dict[str, Any]]:
        """Pedidos filtrados por estado y fecha (`since` ISO8601 → param
        `after`). Estados Woo: pending, processing, on-hold, completed,
        cancelled, refunded, failed."""
        params: dict[str, Any] = {"per_page": per_page, "page": page, "orderby": "date"}
        if status:
            params["status"] = status
        if since:
            params["after"] = since
        return self._get("/orders", params=params)

    def get_order(self, order_id: int) -> dict[str, Any]:
        """Pedido completo: line_items (con SKU), shipping, billing,
        customer_id, payment_method, meta_data."""
        return self._get(f"/orders/{order_id}")

    def get_customer(self, customer_id: int) -> dict[str, Any]:
        return self._get(f"/customers/{customer_id}")

    def list_products(self, *, per_page: int = 100, page: int = 1) -> list[dict[str, Any]]:
        """Para la conciliación SKU: id, sku, name, price, stock_quantity."""
        return self._get("/products", params={"per_page": per_page, "page": page})

    def iter_all_products(self) -> list[dict[str, Any]]:
        """Todos los productos paginando (para el script de conciliación)."""
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.list_products(per_page=100, page=page)
            if not batch:
                return out
            out.extend(batch)
            page += 1

    # --- transporte -----------------------------------------------------------

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/wp-json/wc/v3{path}"
        attempt = 0
        while True:
            attempt += 1
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.get(
                        url, params=params or {},
                        auth=(self.consumer_key, self.consumer_secret),
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
                    f"GET {path} → {resp.status_code}",
                    status=resp.status_code, body=resp.text,
                )
            return resp.json()

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        logger.info("woocommerce retry en %.1fs (intento %d)", wait, attempt)
        time.sleep(wait)
