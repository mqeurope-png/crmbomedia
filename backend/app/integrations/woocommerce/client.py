"""Cliente HTTP WooCommerce (REST API v3) — Fase B PR B-2.

Multi-tienda: se instancia POR cuenta de `integration_accounts` (una fila
por tienda: boprint / artisjet / flux). Los secretos CK/CS se persisten
cifrados con Fernet y se descifran on-demand.

Auth: HTTP Basic sobre HTTPS con Consumer Key/Secret.
Base: `{base_url}/wp-json/wc/v3/`. Paginación por headers
`X-WP-Total(Pages)`, máx 100/página.
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
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


_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _to_iso8601_datetime(value: str) -> str:
    """Normaliza una fecha para los parámetros de fecha de WC v3
    (`after`/`before`/`modified_after`), que exigen ISO 8601 CON hora.

    - "2026-07-04"          → "2026-07-04T00:00:00"
    - "2026-07-04T09:30:00" → sin cambios (ya lleva hora)
    - "2026-07-04 09:30:00" → "2026-07-04T09:30:00" (normaliza el espacio)

    Un valor no reconocible se devuelve tal cual (que Woo lo valide y su
    mensaje aparezca en el log gracias al fix del cuerpo en la excepción).
    """
    v = (value or "").strip()
    if not v:
        return v
    if _DATE_ONLY_RE.match(v):
        return f"{v}T00:00:00"
    if " " in v and "T" not in v:
        return v.replace(" ", "T", 1)
    return v


def _extract_pdf(resp: httpx.Response) -> bytes | None:
    """Extrae los bytes del PDF de una respuesta del plugin: acepta el binario
    directo (Content-Type application/pdf o cuerpo que empieza por `%PDF-`) o
    un JSON con el PDF en base64. Devuelve None si no parece un PDF válido."""
    content = resp.content or b""
    ctype = resp.headers.get("content-type", "").lower()
    if content[:5] == b"%PDF-":
        return content
    if "application/pdf" in ctype and content:
        return content
    if "application/json" in ctype:
        try:
            data = resp.json()
        except ValueError:
            return None
        b64 = None
        if isinstance(data, dict):
            b64 = data.get("pdf") or data.get("pdf_base64") or data.get("data")
        if isinstance(b64, str) and b64:
            try:
                raw = base64.b64decode(b64, validate=False)
            except (ValueError, binascii.Error):
                return None
            if raw[:5] == b"%PDF-":
                return raw
    return None


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

    def __init__(
        self, account: IntegrationAccount,
        *, transport: httpx.BaseTransport | None = None,
    ):
        self.account = account
        self.creds = WooCredentials.from_account(account)
        # Inyectable en tests (MockTransport); None → salida real a red.
        self._transport = transport

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
            # WC v3 es estricto: `after`/`before`/`modified_after` exigen
            # ISO 8601 CON hora. "2026-07-04" → 400; hay que enviar
            # "2026-07-04T00:00:00".
            params["after"] = _to_iso8601_datetime(since)
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

    # --- albarán (mu-plugin `bohub-albaran` de la tienda WP) ------------------

    def get_packing_slip_pdf(self, order_id: int) -> tuple[bytes, str]:
        """Descarga el albarán OFICIAL del plugin *PDF Invoices & Packing Slips*
        (free) a través del **mu-plugin `bohub-albaran`** instalado en la tienda.

        Flujo real (D-1-fix2): `mu-plugin → (fallback en la capa superior)
        reportlab`. El mu-plugin expone un endpoint público autenticado por
        token compartido:

            GET {store}/?bohub_albaran=packing-slip&order_id={id}&token={TOKEN}

        El token vive en `settings.woocommerce_albaran_token` (env
        `WOOCOMMERCE_ALBARAN_TOKEN`), el MISMO que Bart pone hardcodeado en el
        `bohub-albaran.php` de las 3 tiendas.

        Devuelve `(pdf_bytes, filename)` si el mu-plugin entrega el PDF. Lanza
        `WooError` si el token no está configurado, el mu-plugin no está
        instalado (404), el token es rechazado (401), hay timeout, o la
        respuesta no es un PDF — en cualquiera de esos casos el llamante
        (`fetch_albaran_from_woo`) genera un albarán propio con `albaran_pdf`.
        """
        from app.core.config import get_settings  # noqa: PLC0415

        token = get_settings().woocommerce_albaran_token
        if not token:
            raise WooError(
                "WOOCOMMERCE_ALBARAN_TOKEN no configurado; se usa el albarán "
                "generado por el CRM.",
                status=503,
            )
        filename = f"albaran-{order_id}.pdf"
        resp = self._raw_get(f"{self.creds.base_url}/", params={
            "bohub_albaran": "packing-slip",
            "order_id": order_id,
            "token": token,
        }, timeout=20.0)
        if resp is None:
            raise WooError(
                f"mu-plugin albarán: sin respuesta (timeout/red) — pedido {order_id}.",
                status=504,
            )
        if resp.status_code == 401:
            raise WooError(
                "mu-plugin albarán: token rechazado (401) en esta tienda.",
                status=401,
            )
        if resp.status_code == 404:
            raise WooError(
                "mu-plugin albarán: no instalado o PDF Invoices inactivo (404).",
                status=404,
            )
        if resp.status_code < 400:
            pdf = _extract_pdf(resp)
            if pdf is not None:
                logger.info("woocommerce albarán %s vía mu-plugin", order_id)
                return pdf, filename
        # text/plain u otro cuerpo → mensaje de error del mu-plugin.
        raise WooError(
            f"mu-plugin albarán ({resp.status_code}): {(resp.text or '')[:200]}",
            status=resp.status_code or 502,
        )

    def _raw_get(
        self, url: str, *, params: dict[str, Any] | None = None, timeout: float = 30.0,
    ) -> httpx.Response | None:
        """GET autenticado que devuelve la respuesta cruda (sin parsear JSON),
        para descargar binarios. None si la red falla tras los reintentos."""
        attempt = 0
        while True:
            attempt += 1
            try:
                with httpx.Client(timeout=timeout, transport=self._transport,
                                  follow_redirects=True) as c:
                    return c.get(
                        url, params=params or {},
                        auth=(self.creds.consumer_key, self.creds.consumer_secret),
                    )
            except httpx.TransportError as exc:
                if attempt > MAX_RETRIES:
                    logger.warning("woocommerce _raw_get red KO: %s", exc)
                    return None
                self._sleep_backoff(attempt)

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
                with httpx.Client(timeout=30.0, transport=self._transport) as c:
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
                # Incluimos el cuerpo de la respuesta en el mensaje (no solo
                # en `.body`) para que el error de Woo aparezca directo en el
                # log del worker — WC v3 devuelve {code, message} útil.
                raise WooError(
                    f"{method} {path} → {resp.status_code}: {resp.text[:500]}",
                    status=resp.status_code, body=resp.text,
                )
            return resp.json()

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        logger.info("woocommerce retry en %.1fs (intento %d)", wait, attempt)
        time.sleep(wait)
