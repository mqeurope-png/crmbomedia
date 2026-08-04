"""BoHub ERP Fase D · PR D-1 — descarga del albarán PDF del plugin Woo.

Se inyecta un `httpx.MockTransport`: sin red real. El cliente se construye por
`__new__` para saltar `WooCredentials.from_account` (que descifraría Fernet)."""
from __future__ import annotations

import base64

import httpx
import pytest

from app.integrations.woocommerce.client import (
    WooCredentials,
    WooError,
    WooHTTPClient,
)


def _client(handler) -> WooHTTPClient:
    client = WooHTTPClient.__new__(WooHTTPClient)
    client.account = None
    client.creds = WooCredentials(
        base_url="https://shop.example", consumer_key="ck", consumer_secret="cs",
    )
    client._transport = httpx.MockTransport(handler)
    return client


def test_packing_slip_via_rest_pdf_binary():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "wcpdf/v1/documents/packing-slip/123" in str(request.url)
        return httpx.Response(200, content=b"%PDF-1.5 real",
                              headers={"content-type": "application/pdf"})

    pdf, filename = _client(handler).get_packing_slip_pdf(123)
    assert pdf.startswith(b"%PDF")
    assert filename == "albaran-123.pdf"


def test_packing_slip_rest_json_base64():
    def handler(request: httpx.Request) -> httpx.Response:
        if "wcpdf/v1" in str(request.url):
            b64 = base64.b64encode(b"%PDF-1.7 x").decode()
            return httpx.Response(200, json={"pdf": b64})
        return httpx.Response(404)

    pdf, _ = _client(handler).get_packing_slip_pdf(5)
    assert pdf.startswith(b"%PDF")


def test_packing_slip_uses_order_key_public_url():
    """D-1-fix1: REST 404 → acceso público con order_key (access_key)."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "wcpdf/v1" in url:
            return httpx.Response(404)
        if "wpo_wcpdf_document=packing-slip" in url and "access_key=wc_order_k" in url:
            seen["url"] = url
            return httpx.Response(200, content=b"%PDF-1.4 pub",
                                  headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    pdf, _ = _client(handler).get_packing_slip_pdf(9, order_key="wc_order_k")
    assert pdf.startswith(b"%PDF")
    assert "access_key=wc_order_k" in seen["url"]


def test_packing_slip_raises_when_no_plugin_and_no_order_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(WooError, match="order_key"):
        _client(handler).get_packing_slip_pdf(7)


def test_packing_slip_rejects_non_pdf_html():
    # Una página HTML de login (200) NO cuenta como PDF.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>login</html>",
                              headers={"content-type": "text/html"})

    with pytest.raises(WooError):
        _client(handler).get_packing_slip_pdf(1)
