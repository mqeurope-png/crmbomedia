"""BoHub ERP Fase D · D-1-fix2 — descarga del albarán vía mu-plugin `bohub-albaran`.

Se inyecta un `httpx.MockTransport`: sin red real. El cliente se construye por
`__new__` para saltar `WooCredentials.from_account` (que descifraría Fernet).
El token del mu-plugin se controla con monkeypatch sobre los settings."""
from __future__ import annotations

import httpx
import pytest

from app.core.config import get_settings
from app.integrations.woocommerce.client import (
    WooCredentials,
    WooError,
    WooHTTPClient,
)


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    # Por defecto el token está configurado; los tests que prueban el caso
    # vacío lo sobreescriben.
    monkeypatch.setattr(get_settings(), "woocommerce_albaran_token", "tok-123")


def _client(handler) -> WooHTTPClient:
    client = WooHTTPClient.__new__(WooHTTPClient)
    client.account = None
    client.creds = WooCredentials(
        base_url="https://shop.example", consumer_key="ck", consumer_secret="cs",
    )
    client._transport = httpx.MockTransport(handler)
    return client


def test_get_packing_slip_pdf_uses_mu_plugin_endpoint():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"%PDF-1.5 oficial",
                              headers={"content-type": "application/pdf"})

    pdf, filename = _client(handler).get_packing_slip_pdf(123)
    assert pdf.startswith(b"%PDF") and filename == "albaran-123.pdf"
    assert "bohub_albaran=packing-slip" in seen["url"]
    assert "order_id=123" in seen["url"]
    assert "token=tok-123" in seen["url"]


def test_get_packing_slip_pdf_raises_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    with pytest.raises(WooError, match="401"):
        _client(handler).get_packing_slip_pdf(9)


def test_get_packing_slip_pdf_raises_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(WooError, match="404"):
        _client(handler).get_packing_slip_pdf(9)


def test_get_packing_slip_pdf_raises_on_text_error():
    # 200 pero text/plain (mensaje de error del mu-plugin) → no es PDF.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="PDF Invoices no activo",
                              headers={"content-type": "text/plain"})

    with pytest.raises(WooError, match="no activo"):
        _client(handler).get_packing_slip_pdf(9)


def test_get_packing_slip_pdf_falls_back_on_timeout(monkeypatch):
    # Sin sleeps reales en el retry.
    monkeypatch.setattr(WooHTTPClient, "_sleep_backoff", staticmethod(lambda a: None))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(WooError, match="timeout|red"):
        _client(handler).get_packing_slip_pdf(9)


def test_get_packing_slip_pdf_missing_token_skips_network(monkeypatch):
    monkeypatch.setattr(get_settings(), "woocommerce_albaran_token", "")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no debe llamarse al mu-plugin sin token")

    with pytest.raises(WooError, match="no configurado"):
        _client(handler).get_packing_slip_pdf(9)
