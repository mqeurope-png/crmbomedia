"""GeneiClient (API v2) con `httpx.MockTransport` — sin red real.

Cubre: login + token 15 d, re-login ante 401, `agencies/prices` (query),
crear/leer/eliminar envío, etiqueta (base64 JSON / PDF binario / ZPL), y la
construcción desde una fila `Carrier` con credenciales cifradas.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.erp.integrations.genei.client import (
    GeneiClient,
    GeneiConfigError,
    GeneiError,
    payment_url_of,
    shipment_code_of,
)
from app.erp.models.carriers import Carrier


def _client(handler, **over) -> GeneiClient:
    kwargs = dict(
        base_url="https://apiv2.genei.es",
        username="sat@bomedia.es",
        password="s3cret",
        default_address_id="1304422",
        transport=httpx.MockTransport(handler),
    )
    kwargs.update(over)
    return GeneiClient(**kwargs)


def _ok(payload) -> httpx.Response:
    return httpx.Response(200, json=payload)


# --- login / token ----------------------------------------------------------


def test_login_caches_token_and_sends_bearer():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/v2/login":
            body = json.loads(request.content)
            assert body == {"username": "sat@bomedia.es", "password": "s3cret"}
            assert "authorization" not in {k.lower() for k in request.headers}
            return _ok({"token": "tok-abc"})
        assert request.headers["Authorization"] == "Bearer tok-abc"
        return _ok([])

    c = _client(handler)
    c.agency_prices(is_warehouse=False, iso_country_origin="ES", iso_country_destination="ES")
    # Login una vez + la llamada; el token dura ~15 días.
    assert calls == ["POST /api/v2/login", "GET /api/v2/agencies/prices"]
    assert c.token_valid_seconds() > 13 * 24 * 3600


def test_token_is_reused_between_calls():
    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/api/v2/login":
            logins += 1
            return _ok({"token": "tok-1"})
        return _ok({})

    c = _client(handler)
    c.get_shipment("A1")
    c.get_shipment("A2")
    assert logins == 1  # no re-login si el token sigue vigente


def test_relogin_on_401_once():
    tokens = iter(["old", "new"])
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": next(tokens)})
        seen_auth.append(request.headers.get("Authorization", ""))
        # La primera llamada con el token viejo da 401; tras re-login, 200.
        if request.headers.get("Authorization") == "Bearer old":
            return httpx.Response(401, json={"message": "expired"})
        return _ok({"shipmentCode": "S9"})

    c = _client(handler)
    out = c.get_shipment("S9")
    assert out["shipmentCode"] == "S9"
    assert seen_auth == ["Bearer old", "Bearer new"]


def test_login_without_token_raises():
    c = _client(lambda r: _ok({"nope": True}))
    with pytest.raises(GeneiError):
        c.login()


def test_error_surfaces_status_and_body():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": "t"})
        return httpx.Response(422, text="agencia no factible")

    with pytest.raises(GeneiError) as exc:
        _client(handler).create_shipment({"agencyId": 1})
    assert exc.value.status == 422
    assert "no factible" in exc.value.body


# --- prices -----------------------------------------------------------------


def test_agency_prices_builds_query_and_returns_list():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": "t"})
        captured.update(dict(request.url.params))
        return _ok([{"agencyId": 10, "price": 4.5}, {"agencyId": 11, "price": 5.9}])

    prices = _client(handler).agency_prices(
        is_warehouse=False, iso_country_origin="ES", iso_country_destination="FR",
        postal_code_origin="08201", postal_code_destination="75001",
        town_origin="Sabadell", town_destination="Paris",
        packages=[{"weight": 0.5, "height": 2, "width": 10, "length": 10}],
    )
    assert [p["agencyId"] for p in prices] == [10, 11]
    assert captured["isWarehouse"] == "false"
    assert captured["isoCountryOrigin"] == "ES"
    assert captured["isoCountryDestination"] == "FR"
    assert captured["postalCodeDestination"] == "75001"
    # `packages[]` como array bracket, con isBox OBLIGATORIO (el bug de prod).
    assert captured["packages[0][weight]"] == "0.5"
    assert captured["packages[0][height]"] == "2"
    assert captured["packages[0][length]"] == "10"
    assert captured["packages[0][isBox]"] == "false"


def test_agency_prices_raises_on_status_zero_envelope():
    # Genei devuelve HTTP 200 con {status:0, errors:[...]} en validación. Antes
    # se colaba como lista vacía («0 agencias siempre»); ahora se eleva.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": "t"})
        return _ok({"status": 0, "message": "Error validacion",
                    "errors": ["\"packages\" failed: Invalid bultos array format"]})

    with pytest.raises(GeneiError) as exc:
        _client(handler).agency_prices(
            is_warehouse=False, iso_country_origin="ES", iso_country_destination="ES",
            packages=[{"weight": 1, "height": 15, "width": 15, "length": 20}],
        )
    assert "Invalid bultos" in str(exc.value)


def test_agency_prices_success_envelope_status_one():
    # Éxito real: {status:1, data:[...agencias...]} (con id_agencia/importe).
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": "t"})
        return _ok({"status": 1, "message": "", "errors": [],
                    "data": [{"id_agencia": "2", "importe": 4.23,
                              "nombre_completo_agencia": "Correos Dom-Dom",
                              "domicilio_domicilio": 1}]})

    prices = _client(handler).agency_prices(
        is_warehouse=False, iso_country_origin="ES", iso_country_destination="ES",
        packages=[{"weight": 1, "height": 15, "width": 15, "length": 20}],
    )
    assert prices[0]["id_agencia"] == "2"


def test_agency_prices_unwraps_data_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": "t"})
        return _ok({"data": [{"agencyId": 1}]})

    prices = _client(handler).agency_prices(
        is_warehouse=False, iso_country_origin="ES", iso_country_destination="ES",
    )
    assert prices == [{"agencyId": 1}]


# --- crear / leer / eliminar ------------------------------------------------


def test_create_shipment_returns_code_and_payment_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": "t"})
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["externalShippingCode"] == "BOPRIN-99917"
        return _ok({"shipmentCode": "GEN123", "paymentUrl": "https://pay/x", "status": 7})

    created = _client(handler).create_shipment({
        "agencyId": 10, "externalShippingCode": "BOPRIN-99917",
        "notificationUrl": "https://bohub/webhooks/genei",
    })
    assert shipment_code_of(created) == "GEN123"
    assert payment_url_of(created) == "https://pay/x"
    assert created["status"] == 7


def test_shipment_code_helper_tolerates_snake_case_and_nesting():
    assert shipment_code_of({"codigo_envio": "X1"}) == "X1"
    assert shipment_code_of({"data": {"shipmentCode": "X2"}}) == "X2"
    assert shipment_code_of({}) is None


def test_get_shipment_and_delete():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": "t"})
        if request.method == "DELETE":
            assert request.url.path == "/api/v2/shipments/GEN123"
            return _ok({"deleted": True})
        return _ok({"shipmentCode": "GEN123", "estado": 5, "codigo_seguimiento": "TRK-9"})

    c = _client(handler)
    got = c.get_shipment("GEN123")
    assert got["estado"] == 5 and got["codigo_seguimiento"] == "TRK-9"
    assert c.delete_shipment("GEN123") == {"deleted": True}


# --- etiqueta ---------------------------------------------------------------


def test_get_label_base64_json():
    pdf = b"%PDF-1.7 etiqueta"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": "t"})
        return _ok({"etiqueta": base64.b64encode(pdf).decode(), "format": "pdf"})

    label = _client(handler).get_label("GEN123")
    assert label.content == pdf
    assert label.kind == "pdf"
    assert label.filename == "Etiqueta_GEN123.pdf"
    assert label.mime_type == "application/pdf"


def test_get_label_binary_pdf():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": "t"})
        return httpx.Response(200, content=b"%PDF-1.4 binaria",
                              headers={"content-type": "application/pdf"})

    label = _client(lambda r: handler(r)).get_label("GEN9")
    assert label.kind == "pdf" and label.content.startswith(b"%PDF")


def test_get_label_zpl():
    zpl = b"^XA^FO50,50^A0N,50,50^FDHola^FS^XZ"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": "t"})
        return httpx.Response(200, content=zpl,
                              headers={"content-type": "application/octet-stream"})

    label = _client(handler).get_label("GEN9")
    assert label.kind == "zpl"
    assert label.filename.endswith(".zpl")


def test_get_label_401_reauths():
    # El primer token (t1) da 401 en /label → re-login (t2) y reintento.
    tokens = iter(["t1", "t2"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            return _ok({"token": next(tokens)})
        if request.headers.get("Authorization") == "Bearer t1":
            return httpx.Response(401)
        return httpx.Response(200, content=b"%PDF-1.5", headers={"content-type": "application/pdf"})

    label = _client(handler).get_label("GEN9")
    assert label.kind == "pdf"


# --- from_carrier / config --------------------------------------------------


def test_from_carrier_decrypts_credentials():
    carrier = Carrier(
        name="Genei", code="genei", has_api=True,
        api_base_url="https://apiv2.genei.es",
        api_credentials_encrypted=GeneiClient.encode_credentials("sat@bomedia.es", "pw"),
        default_address_id="1304422",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/login":
            body = json.loads(request.content)
            assert body == {"username": "sat@bomedia.es", "password": "pw"}
            return _ok({"token": "t"})
        return _ok({})

    c = GeneiClient.from_carrier(carrier, transport=httpx.MockTransport(handler))
    assert c.default_address_id == "1304422"
    c.login()
    assert c.token_valid_seconds() > 0


def test_from_carrier_without_credentials_raises():
    carrier = Carrier(name="Genei", code="genei", has_api=True,
                      api_base_url="https://apiv2.genei.es")
    with pytest.raises(GeneiConfigError):
        GeneiClient.from_carrier(carrier)


def test_missing_base_url_or_creds_raise():
    with pytest.raises(GeneiConfigError):
        GeneiClient(base_url="", username="a", password="b")
    with pytest.raises(GeneiConfigError):
        GeneiClient(base_url="https://x", username="", password="")


def test_encode_credentials_roundtrip_and_no_plaintext():
    # Password larga y distintiva a propósito: comprobar «no en claro» con «pw»
    # (2 letras) era frágil — el base64 del cifrado puede contener «pw» por azar
    # (~2-3% de las veces, IV aleatorio de Fernet) y volvía el test flaky. Con un
    # valor largo la coincidencia es astronómicamente improbable.
    enc = GeneiClient.encode_credentials("user@x", "S3cr3t-Passw0rd-ZQ9")
    assert "user@x" not in enc and "S3cr3t-Passw0rd-ZQ9" not in enc  # cifrado, no en claro
    creds = GeneiClient._decode_credentials(enc)
    assert creds == {"username": "user@x", "password": "S3cr3t-Passw0rd-ZQ9"}


# --- pago interno (PR-2): token fresco + pay/transactions --------------------


def test_pay_transaction_gets_fresh_token_and_pays():
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        calls.append((path, params))
        if path == "/api/v2/login":
            return _ok({"token": "tok-abc"})
        if path == "/api/v2/payments/token":
            return _ok({"status": 1, "data": "pay.jwt.token", "errors": []})
        if path.startswith("/api/v2/payments/pay/transactions/"):
            return _ok({"status": 1, "message": "Paid", "errors": []})
        return httpx.Response(404)

    c = _client(handler)
    c.pay_transaction("16157377")
    # Pide un token FRESCO de la pasarela de saldo (pg=4)…
    token_call = next(c for p, c in calls if p == "/api/v2/payments/token")
    assert token_call == {"pg": "4"}
    # …y paga esa transacción con ese token.
    pay_path, pay_params = next(
        (p, c) for p, c in calls if p.startswith("/api/v2/payments/pay/transactions/")
    )
    assert pay_path == "/api/v2/payments/pay/transactions/16157377"
    assert pay_params == {"payment_token": "pay.jwt.token"}


def test_pay_transaction_surfaces_rejection():
    # Un rechazo (sin saldo) llega como envoltorio status:0 → GeneiError, no se
    # traga: el botón «Pagar y tramitar» enseña el motivo.
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/login":
            return _ok({"token": "tok-abc"})
        if path == "/api/v2/payments/token":
            return _ok({"status": 1, "data": "tok", "errors": []})
        return _ok({"status": 0, "message": "Error", "errors": ["Saldo insuficiente"]})

    c = _client(handler)
    with pytest.raises(GeneiError) as exc:
        c.pay_transaction("999")
    assert "Saldo insuficiente" in str(exc.value)
