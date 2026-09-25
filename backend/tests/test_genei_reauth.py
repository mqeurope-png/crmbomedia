"""Genei — re-autenticación automática del token (sin volver a meter la password).

El token de sesión de Genei caduca. BoHub debe renovarlo SOLO con las
credenciales guardadas (cifradas) y reintentar la llamada, sin que nadie tenga
que ir a Ajustes. Si las credenciales son malas de verdad: error claro
«revisa credenciales de Genei», sin bucle ni martilleo a Genei.

Todo con `httpx.MockTransport` (sin red) y sin pagos reales.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Generator
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.erp.api.genei as genei_api
import app.erp.integrations.genei.client as genei_client
import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.api.genei import GENEI_ADAPTER
from app.erp.integrations.genei.client import (
    AUTH_FAILURE_BACKOFF_SECONDS,
    SHARED_TOKEN_CACHE,
    TOKEN_MAX_AGE_SECONDS,
    GeneiAuthError,
    GeneiClient,
    GeneiConfigError,
    GeneiError,
)
from app.erp.integrations.genei.config import GeneiConfig
from app.erp.models import Order
from app.erp.models.carriers import Carrier
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users

PASSWORD = "S3cr3t-Genei-Pw-77"
BASE = "https://apiv2.genei.es"


def _jwt(exp: float) -> str:
    """JWT sin firmar con `exp` (el cliente solo lee el payload)."""
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'none'})}.{b64({'exp': int(exp)})}.sig"


def _carrier(password: str = PASSWORD, **over) -> Carrier:
    kw = dict(
        name="Genei", code="genei", has_api=True, adapter_class=GENEI_ADAPTER,
        api_base_url=BASE, default_address_id="1304422",
        api_credentials_encrypted=GeneiClient.encode_credentials("sat@bomedia.es", password),
    )
    kw.update(over)
    return Carrier(**kw)


class FakeGeneiApi:
    """Genei de mentira: emite tokens, y rechaza los que se marcan caducados."""

    def __init__(self) -> None:
        self.logins: list[dict] = []
        self.calls: list[tuple[str, str]] = []   # (ruta, token usado)
        self.expired: set[str] = set()
        self.login_response: httpx.Response | None = None
        self.expiry_style = "401"                  # 401 | 403 | envelope
        self._n = 0

    def issue(self) -> str:
        self._n += 1
        return f"{_jwt(time.time() + 15 * 24 * 3600)}{self._n}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/login":
            self.logins.append(json.loads(request.content))
            if self.login_response is not None:
                return self.login_response
            return httpx.Response(200, json={"token": self.issue()})
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        self.calls.append((path, token))
        if token in self.expired:
            if self.expiry_style == "403":
                return httpx.Response(403, json={"message": "Forbidden"})
            if self.expiry_style == "envelope":
                # Genei responde HTTP 200 también en error.
                return httpx.Response(200, json={
                    "status": 0, "message": "Token expired", "errors": ["Token expired"],
                })
            return httpx.Response(401, json={"message": "Unauthenticated."})
        if path.startswith("/api/v2/shipments/"):
            return httpx.Response(200, json={"status": 1, "data": {
                "codigo_envio": "GEN1", "estado": 5, "codigo_seguimiento": "TRK-1",
                "nombre_agencia": "Ctt Premium",
            }, "errors": []})
        if path == "/api/v2/agencies/prices":
            return httpx.Response(200, json={
                "status": 0, "message": "Error validacion",
                "errors": ["Invalid bultos array format"],
            })
        return httpx.Response(200, json={"status": 1, "data": {}, "errors": []})


@pytest.fixture()
def genei() -> FakeGeneiApi:
    return FakeGeneiApi()


def _client_for(carrier: Carrier, fake: FakeGeneiApi) -> GeneiClient:
    """Lo que hace cada petición HTTP de BoHub: un cliente nuevo desde el carrier."""
    return GeneiClient.from_carrier(carrier, transport=httpx.MockTransport(fake.handler))


# --- token caducado → re-login con las credenciales guardadas ----------------


@pytest.mark.parametrize("style", ["401", "403", "envelope"])
def test_token_caducado_reautentica_con_credenciales_guardadas_y_reintenta(genei, style):
    carrier = _carrier()
    genei.expiry_style = style
    # 1ª petición: login y llamada.
    first = _client_for(carrier, genei).get_shipment("GEN1")
    assert first["codigo_envio"] == "GEN1"
    assert len(genei.logins) == 1
    old_token = genei.calls[-1][1]

    # Genei da por caducado el token (sesión). La siguiente petición de BoHub
    # (otro cliente, como otra request) reutiliza el cacheado → rechazo → login
    # solo, con las credenciales GUARDADAS, y reintento: sin intervención.
    genei.expired.add(old_token)
    out = _client_for(carrier, genei).get_shipment("GEN1")
    assert out["codigo_seguimiento"] == "TRK-1"
    assert len(genei.logins) == 2
    assert genei.logins[-1] == {"username": "sat@bomedia.es", "password": PASSWORD}
    tokens = [t for _p, t in genei.calls]
    assert tokens[-2] == old_token and tokens[-1] != old_token

    # Y el token nuevo queda cacheado: la siguiente no vuelve a hacer login.
    _client_for(carrier, genei).get_shipment("GEN1")
    assert len(genei.logins) == 2


def test_el_token_se_comparte_entre_peticiones(genei):
    carrier = _carrier()
    for _ in range(3):
        _client_for(carrier, genei).get_shipment("GEN1")
    assert len(genei.logins) == 1           # antes: un login en CADA petición


def test_se_renueva_solo_antes_de_su_edad_maxima(genei, monkeypatch):
    carrier = _carrier()
    _client_for(carrier, genei).get_shipment("GEN1")
    later = time.time() + TOKEN_MAX_AGE_SECONDS + 60
    monkeypatch.setattr(genei_client.time, "time", lambda: later)
    _client_for(carrier, genei).get_shipment("GEN1")
    assert len(genei.logins) == 2           # renovado sin esperar a un rechazo


def test_token_a_punto_de_caducar_se_renueva_antes_de_usarlo(genei):
    carrier = _carrier()
    # Genei da un token que caduca en 10 min (< margen de seguridad).
    genei.login_response = httpx.Response(200, json={"token": _jwt(time.time() + 600)})
    _client_for(carrier, genei).get_shipment("GEN1")
    genei.login_response = None
    _client_for(carrier, genei).get_shipment("GEN1")
    assert len(genei.logins) == 2


def test_error_de_datos_no_provoca_relogin(genei):
    # «Invalid bultos…» es un error de los DATOS, no del token: ni re-login ni
    # reintento; el error llega tal cual.
    client = _client_for(_carrier(), genei)
    with pytest.raises(GeneiError) as exc:
        client.agency_prices(is_warehouse=False, iso_country_origin="ES",
                             iso_country_destination="ES")
    assert "Invalid bultos" in str(exc.value)
    assert len(genei.logins) == 1
    assert [p for p, _t in genei.calls] == ["/api/v2/agencies/prices"]


def test_token_nuevo_tambien_rechazado_no_entra_en_bucle(genei):
    carrier = _carrier()
    genei.expired = _AlwaysExpired()
    with pytest.raises(GeneiError) as exc:
        _client_for(carrier, genei).get_shipment("GEN1")
    assert exc.value.status == 401
    # Un login inicial + UNO de renovación; dos intentos de la llamada. Nada más.
    assert len(genei.logins) == 2
    assert len(genei.calls) == 2


class _AlwaysExpired(set):
    def __contains__(self, item) -> bool:  # noqa: D105
        return True


# --- credenciales inválidas de verdad: error claro, sin bucle -----------------


def test_credenciales_invalidas_error_claro_y_sin_martillear(genei, monkeypatch):
    carrier = _carrier()
    genei.login_response = httpx.Response(401, json={"message": "Invalid credentials"})
    with pytest.raises(GeneiAuthError) as exc:
        _client_for(carrier, genei).get_shipment("GEN1")
    assert "revisa las credenciales de Genei" in str(exc.value)
    assert "Invalid credentials" in str(exc.value)
    assert len(genei.logins) == 1
    assert genei.calls == []                 # sin token no se llama a nada

    # Las siguientes peticiones fallan AL MOMENTO con el mismo aviso: ni un
    # login más contra Genei (no se bloquea la cuenta ni se pide la password).
    for _ in range(3):
        with pytest.raises(GeneiAuthError):
            _client_for(carrier, genei).get_shipment("GEN1")
    assert len(genei.logins) == 1

    # Pasado el bloqueo, lo vuelve a intentar solo (por si era un fallo puntual).
    later = time.time() + AUTH_FAILURE_BACKOFF_SECONDS + 1
    monkeypatch.setattr(genei_client.time, "time", lambda: later)
    genei.login_response = None
    assert _client_for(carrier, genei).get_shipment("GEN1")["codigo_envio"] == "GEN1"
    assert len(genei.logins) == 2


def test_credenciales_invalidas_en_envoltorio_status_0(genei):
    # Genei puede responder HTTP 200 con status:0 también en el login.
    genei.login_response = httpx.Response(200, json={
        "status": 0, "message": "Usuario o contraseña incorrectos", "errors": [],
    })
    with pytest.raises(GeneiAuthError) as exc:
        _client_for(_carrier(), genei).get_shipment("GEN1")
    assert "Usuario o contraseña incorrectos" in str(exc.value)


def test_probar_conexion_salta_el_bloqueo(genei):
    carrier = _carrier()
    genei.login_response = httpx.Response(401, json={"message": "Invalid credentials"})
    with pytest.raises(GeneiAuthError):
        _client_for(carrier, genei).get_shipment("GEN1")
    genei.login_response = None
    _client_for(carrier, genei).login(force=True)
    assert len(genei.logins) == 2
    # Y desbloquea: las llamadas normales ya funcionan.
    assert _client_for(carrier, genei).get_shipment("GEN1")["codigo_envio"] == "GEN1"


def test_cambiar_credenciales_no_hereda_el_bloqueo(genei):
    genei.login_response = httpx.Response(401, json={"message": "Invalid credentials"})
    with pytest.raises(GeneiAuthError):
        _client_for(_carrier("vieja"), genei).get_shipment("GEN1")
    genei.login_response = None
    out = _client_for(_carrier("nueva-buena"), genei).get_shipment("GEN1")
    assert out["codigo_envio"] == "GEN1"


def test_login_5xx_es_pasajero_no_bloquea(genei):
    carrier = _carrier()
    genei.login_response = httpx.Response(503, text="<html>down</html>")
    with pytest.raises(GeneiError) as exc:
        _client_for(carrier, genei).get_shipment("GEN1")
    assert not isinstance(exc.value, GeneiAuthError)
    genei.login_response = None
    assert _client_for(carrier, genei).get_shipment("GEN1")["codigo_envio"] == "GEN1"


def test_fallo_de_red_es_genei_error_claro():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = GeneiClient.from_carrier(_carrier(), transport=httpx.MockTransport(boom))
    with pytest.raises(GeneiError) as exc:
        client.get_shipment("GEN1")
    assert "No se pudo conectar con Genei" in str(exc.value)


def test_credenciales_ilegibles_por_cambio_de_clave_es_error_de_config():
    other = Fernet(Fernet.generate_key())
    carrier = _carrier(api_credentials_encrypted=other.encrypt(b'{"username":"a"}').decode())
    with pytest.raises(GeneiConfigError) as exc:
        GeneiClient.from_carrier(carrier)
    assert "vuelve a guardarlas" in str(exc.value)


# --- el pago no cambia: solo reintenta ante un 401 HTTP -----------------------


def test_pago_no_reintenta_por_mensajes_de_token_de_pago(genei):
    """«Pagar y tramitar» sigue igual: un `status:0` que habla del token DE PAGO
    no dispara re-login ni un segundo intento de pago."""
    pays: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/login":
            genei.logins.append({})
            return httpx.Response(200, json={"token": genei.issue()})
        if path == "/api/v2/payments/token":
            return httpx.Response(200, json={"status": 1, "data": "pay.jwt", "errors": []})
        pays.append(path)
        return httpx.Response(200, json={
            "status": 0, "message": "Error", "errors": ["payment_token expired"],
        })

    client = GeneiClient.from_carrier(_carrier(), transport=httpx.MockTransport(handler))
    with pytest.raises(GeneiError):
        client.pay_transaction("123")
    assert len(pays) == 1
    assert len(genei.logins) == 1


# --- nada secreto en logs ni en el estado -------------------------------------


def test_ni_password_ni_token_en_logs_ni_en_el_estado(genei, caplog):
    caplog.set_level(logging.DEBUG)
    carrier = _carrier()
    _client_for(carrier, genei).get_shipment("GEN1")
    genei.expired.add(genei.calls[-1][1])
    _client_for(carrier, genei).get_shipment("GEN1")
    genei.login_response = httpx.Response(401, json={"message": "Invalid credentials"})
    SHARED_TOKEN_CACHE.clear()
    with pytest.raises(GeneiAuthError):
        _client_for(carrier, genei).get_shipment("GEN1")

    tokens = {t for _p, t in genei.calls if t}
    assert tokens
    assert PASSWORD not in caplog.text
    assert not any(t in caplog.text for t in tokens)
    status = GeneiClient.auth_status_of(carrier)
    assert status["state"] == "error"
    assert not any(t in json.dumps(status) for t in tokens)
    assert PASSWORD not in json.dumps(status)


# --- endpoints: Ajustes («Probar conexión», estado) y flujo real --------------


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def api(session_factory, genei) -> Generator[TestClient, None, None]:
    """La API con el cliente Genei REAL (caché compartida) sobre MockTransport."""
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    real = lambda carrier: GeneiClient.from_carrier(  # noqa: E731
        carrier, transport=httpx.MockTransport(genei.handler))
    with patch.object(genei_api, "build_client", real):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


def _seed(s: Session, *, webhook: bool = False) -> str:
    cfg = GeneiConfig(webhook_base_url="https://api.bohub.example" if webhook else "")
    s.add(_carrier(config_json=cfg.to_json()))
    o = Order(order_number="ALB-2-200038", preparation_status="packed",
              payment_status="paid", transport_status="label_created",
              external_source="manual")
    o.packing_json = json.dumps({"genei": {"shipment_code": "GEN1", "state_bucket": "ready"}})
    s.add(o)
    s.commit()
    return o.id


def test_actualizar_estado_sobrevive_a_la_caducidad_del_token(api, session_factory, genei):
    with session_factory() as s:
        oid = _seed(s)
    h = auth_headers(api)
    assert api.post(f"/api/erp/orders/{oid}/genei/refresh", headers=h).status_code == 200
    genei.expired.add(genei.calls[-1][1])            # caduca la sesión en Genei
    r = api.post(f"/api/erp/orders/{oid}/genei/refresh", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["tracking"] == "TRK-1"
    assert len(genei.logins) == 2                     # renovado solo


def test_credenciales_malas_codigo_propio_y_mensaje_claro(api, session_factory, genei):
    with session_factory() as s:
        oid = _seed(s)
    genei.login_response = httpx.Response(401, json={"message": "Invalid credentials"})
    r = api.post(f"/api/erp/orders/{oid}/genei/refresh", headers=auth_headers(api))
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["code"] == "genei_auth_failed"
    assert "revisa las credenciales de Genei" in detail["detail"]


def test_probar_conexion_y_estado_en_ajustes(api, session_factory, genei):
    with session_factory() as s:
        _seed(s)
    h = auth_headers(api)
    before = api.get("/api/erp/genei/config", headers=h).json()
    assert before["auth"]["state"] == "unknown"

    r = api.post("/api/erp/genei/test-connection", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["detail"] is None
    assert body["auth"]["state"] == "ok"
    assert body["auth"]["token_valid_until"]
    assert "token\":" not in json.dumps(body).replace("token_valid_until", "")

    genei.login_response = httpx.Response(401, json={"message": "Invalid credentials"})
    bad = api.post("/api/erp/genei/test-connection", headers=h).json()
    assert bad["ok"] is False
    assert "revisa las credenciales de Genei" in bad["detail"]
    after = api.get("/api/erp/genei/config", headers=h).json()
    assert after["auth"]["state"] == "error"
    assert PASSWORD not in json.dumps(after)


def test_guardar_ajustes_conserva_el_secreto_del_webhook(api, session_factory):
    """Antes, cada «Guardar» en Ajustes → Envíos regeneraba el secreto del
    webhook: los envíos ya creados (con el secreto viejo en su notificationUrl)
    recibían 401 y su estado dejaba de actualizarse."""
    with session_factory() as s:
        _seed(s, webhook=True)
    h = auth_headers(api)
    # Activa el webhook (genera el secreto).
    api.put("/api/erp/genei/config", headers=h,
            json={"webhook_base_url": "https://api.bohub.example"})

    def secret() -> str | None:
        with session_factory() as s:
            carrier = s.scalar(select(Carrier).where(Carrier.code == "genei"))
            return GeneiClient.webhook_secret_of(carrier.api_credentials_encrypted)

    original = secret()
    assert original
    # Un «Guardar» normal (la tarjeta siempre manda el email) y otro que vuelve a
    # meter la password: el secreto NO cambia.
    api.put("/api/erp/genei/config", headers=h, json={
        "username": "sat@bomedia.es", "preferred_couriers": {"ES": ["GLS"]},
    })
    assert secret() == original
    api.put("/api/erp/genei/config", headers=h, json={
        "username": "sat@bomedia.es", "password": "otra-pass",
    })
    assert secret() == original
    # El webhook con el secreto original sigue entrando.
    r = api.post(f"/api/webhooks/genei?token={original}", json={"status": 1, "data": {
        "codigo_envio": "GEN1", "codigo_envio_externo": "ALB-2-200038", "estado": 1,
    }})
    assert r.status_code == 200, r.text


def test_guardar_credenciales_desbloquea_el_login(api, session_factory, genei):
    with session_factory() as s:
        oid = _seed(s)
    h = auth_headers(api)
    genei.login_response = httpx.Response(401, json={"message": "Invalid credentials"})
    assert api.post(f"/api/erp/orders/{oid}/genei/refresh", headers=h).status_code == 502
    # La persona corrige la password en Ajustes y guarda: se reintenta ya.
    genei.login_response = None
    api.put("/api/erp/genei/config", headers=h,
            json={"username": "sat@bomedia.es", "password": "la-buena"})
    r = api.post(f"/api/erp/orders/{oid}/genei/refresh", headers=h)
    assert r.status_code == 200, r.text
