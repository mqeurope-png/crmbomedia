"""BoHub ERP Fase C PR C-1 — cliente FACTUSOL (auth + retry + token cache).

Sin red: se inyecta un httpx.MockTransport que simula la API DELSOL.
"""
from __future__ import annotations

import base64
import json
import time
from unittest.mock import patch

import httpx
import pytest

from app.integrations.factusol.client import FactusolClient, FactusolError


def _make_jwt(exp_offset: int = 3600, role: str = "AdminUser") -> str:
    def _seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    header = _seg({"alg": "HS256", "typ": "JWT"})
    payload = _seg({"exp": int(time.time()) + exp_offset, "role": role})
    return f"{header}.{payload}.sig"


def _client(handler) -> FactusolClient:
    return FactusolClient(
        base_url="https://api.sdelsol.test",
        codigo_fabricante="1626", codigo_cliente="22870",
        base_datos_cliente="3FS003", password="secret",
        default_ejercicio="2026",
        transport=httpx.MockTransport(handler),
    )


def _login_response(token: str) -> httpx.Response:
    return httpx.Response(200, json={"resultado": token, "respuesta": "OK"})


# --- auth -------------------------------------------------------------------


def test_login_returns_token_and_caches_it():
    logins = {"n": 0}
    token = _make_jwt()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            logins["n"] += 1
            # El password viaja en base64.
            body = json.loads(request.content)
            assert body["password"] == base64.b64encode(b"secret").decode()
            assert body["codigoFabricante"] == "1626"
            return _login_response(token)
        return httpx.Response(200, json={"registros": []})

    c = _client(handler)
    assert c.authenticate() == token
    # Dos llamadas de datos reutilizan el token (login una sola vez).
    c.load_table("F_CLI")
    c.load_table("F_ART")
    assert logins["n"] == 1
    assert c.token_valid_seconds() > 0
    assert c.token_claims().get("role") == "AdminUser"


def test_login_without_ok_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"respuesta": "ERROR", "resultado": ""})

    c = _client(handler)
    with pytest.raises(FactusolError):
        c.authenticate()


def test_401_on_data_call_reauthenticates_and_retries():
    state = {"logins": 0, "data_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            state["logins"] += 1
            return _login_response(_make_jwt())
        state["data_calls"] += 1
        # El primer intento de datos devuelve 401 (token caducado en vuelo).
        if state["data_calls"] == 1:
            return httpx.Response(401, json={"error": "token expired"})
        return httpx.Response(200, json={"registros": [{"CODCLI": "1"}]})

    c = _client(handler)
    rows = c.load_table("F_CLI")
    assert rows == [{"CODCLI": "1"}]
    assert state["logins"] == 2          # re-autenticó tras el 401
    assert state["data_calls"] == 2


def test_token_near_expiry_is_renewed():
    state = {"logins": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            state["logins"] += 1
            # exp dentro del margen de 30s → la siguiente llamada renueva.
            return _login_response(_make_jwt(exp_offset=10))
        return httpx.Response(200, json={"registros": []})

    c = _client(handler)
    c.authenticate()               # login #1, token casi caducado
    c.load_table("F_CLI")          # _ensure_token ve el margen → login #2
    assert state["logins"] == 2


# --- retry / errores --------------------------------------------------------


def test_5xx_retries_with_backoff_then_succeeds():
    state = {"data_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        state["data_calls"] += 1
        if state["data_calls"] <= 2:
            return httpx.Response(503, text="upstream down")
        return httpx.Response(200, json={"registros": [{"CODCLI": "9"}]})

    c = _client(handler)
    with patch("app.integrations.factusol.client.time.sleep"):
        rows = c.load_table("F_CLI")
    assert rows == [{"CODCLI": "9"}]
    assert state["data_calls"] == 3


def test_4xx_raises_without_retry():
    state = {"data_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        state["data_calls"] += 1
        return httpx.Response(400, text='{"error":"bad filtro"}')

    c = _client(handler)
    with pytest.raises(FactusolError) as exc:
        c.load_table("F_CLI", filtro="bad")
    assert exc.value.status == 400
    assert state["data_calls"] == 1      # sin reintento en 4xx


def test_write_record_posts_registro_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    c = _client(handler)
    c.write_record("F_CLI", {"CODCLI": "60000", "PCOCLI": "Test"})
    assert captured["path"] == "/registros/escribirRegistro"
    assert captured["body"]["tabla"] == "F_CLI"
    assert captured["body"]["registro"]["CODCLI"] == "60000"
    assert captured["body"]["ejercicio"] == "2026"
