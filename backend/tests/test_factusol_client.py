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

from app.integrations.factusol.client import (
    PATH_CARGA_TABLA,
    FactusolClient,
    FactusolError,
    _rows_to_dicts,
)


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
        return httpx.Response(200, json={"resultado": None, "respuesta": "OK"})

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
        return httpx.Response(200, json={"resultado": [[{"columna": "CODCLI", "dato": "1"}]],
                                          "respuesta": "OK"})

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
        return httpx.Response(200, json={"resultado": None, "respuesta": "OK"})

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
        return httpx.Response(200, json={"resultado": [[{"columna": "CODCLI", "dato": "9"}]],
                                          "respuesta": "OK"})

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


def test_data_paths_are_configurable_without_code_change():
    """Las rutas de datos son sobreescribibles por env, por si DELSOL las
    cambia: corregirlas no exige redeploy de código."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        seen.append(request.url.path)
        return httpx.Response(200, json={"resultado": None, "respuesta": "OK"})

    c = FactusolClient(
        base_url="https://api.sdelsol.test",
        codigo_fabricante="1626", codigo_cliente="22870",
        base_datos_cliente="3FS003", password="secret",
        transport=httpx.MockTransport(handler),
        path_load_table="/Descubierto/CargarTabla",
        path_write_record="/Descubierto/EscribirRegistro",
        path_update_record="/Descubierto/ActualizarRegistro",
        path_delete_records="/Descubierto/BorrarRegistros",
    )
    c.load_table("F_CLI")
    c.write_record("F_CLI", {"CODCLI": "1"})
    c.update_record("F_CLI", {"CODCLI": "1", "TELCLI": "600"})
    c.delete_records("F_CLI", "CODCLI='1'")
    assert seen == [
        "/Descubierto/CargarTabla",
        "/Descubierto/EscribirRegistro",
        "/Descubierto/ActualizarRegistro",
        # delete es GET con path params {ejercicio}/{tabla}/{filtro}
        "/Descubierto/BorrarRegistros/2026/F_CLI/CODCLI='1'",
    ]


def test_default_data_paths_used_when_no_override():
    """Sin override se usan los defaults confirmados (`/admin/*`)."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        seen.append(request.url.path)
        return httpx.Response(200, json={"resultado": None, "respuesta": "OK"})

    c = _client(handler)
    c.load_table("F_CLI")
    assert seen == [PATH_CARGA_TABLA]


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
    assert captured["path"] == "/admin/EscribirRegistro"
    assert captured["body"]["tabla"] == "F_CLI"
    assert captured["body"]["ejercicio"] == "2026"
    # `registro` va como array de {columna, dato}, no como dict plano.
    assert captured["body"]["registro"] == [
        {"columna": "CODCLI", "dato": "60000"},
        {"columna": "PCOCLI", "dato": "Test"},
    ]


# --- formato real de la API (C-1-fix1) --------------------------------------


def test_rows_to_dicts_parses_nested_column_dato_shape():
    """CargaTabla devuelve filas como listas de {columna, dato}."""
    resultado = [
        [{"columna": "CODCLI", "dato": 1}, {"columna": "NOFCLI", "dato": "Cliente"}],
        [{"columna": "CODCLI", "dato": 2}, {"columna": "NOFCLI", "dato": "Otro"}],
    ]
    assert _rows_to_dicts(resultado) == [
        {"CODCLI": 1, "NOFCLI": "Cliente"},
        {"CODCLI": 2, "NOFCLI": "Otro"},
    ]


def test_rows_to_dicts_handles_null_when_no_rows():
    """Sin filas la API devuelve `resultado: null`, no `[]`."""
    assert _rows_to_dicts(None) == []
    assert _rows_to_dicts([]) == []


def test_load_table_sends_ejercicio_tabla_filtro_only():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"resultado": None, "respuesta": "OK"})

    c = _client(handler)
    c.load_table("F_CLI", filtro="1=1 ORDER BY CODCLI LIMIT 5")
    assert captured["path"] == "/admin/CargaTabla"
    assert captured["body"] == {
        "ejercicio": "2026", "tabla": "F_CLI",
        "filtro": "1=1 ORDER BY CODCLI LIMIT 5",
    }


def test_load_table_empty_filter_becomes_1_equals_1():
    """Un filtro vacío hace que la API devuelva null → se manda `1=1`."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"resultado": None, "respuesta": "OK"})

    c = _client(handler)
    c.load_table("F_CLI", filtro="")
    assert captured["body"]["filtro"] == "1=1"


def test_unauthorized_arrives_as_http_200_and_triggers_reauth():
    """La API señala el token caducado con HTTP 200 + respuesta=Unauthorized
    (no con un 401). Debe re-autenticar y reintentar igualmente."""
    state = {"logins": 0, "data_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            state["logins"] += 1
            return _login_response(_make_jwt())
        state["data_calls"] += 1
        if state["data_calls"] == 1:
            return httpx.Response(200, json={"resultado": "", "respuesta": "Unauthorized"})
        return httpx.Response(200, json={
            "resultado": [[{"columna": "CODCLI", "dato": 7}]], "respuesta": "OK",
        })

    c = _client(handler)
    rows = c.load_table("F_CLI")
    assert rows == [{"CODCLI": 7}]
    assert state["logins"] == 2 and state["data_calls"] == 2


def test_persistent_unauthorized_raises_after_one_reauth():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        return httpx.Response(200, json={"resultado": "", "respuesta": "Unauthorized"})

    c = _client(handler)
    with pytest.raises(FactusolError, match="token rechazado"):
        c.load_table("F_CLI")


def test_bd_no_existe_raises_clear_error():
    """Ejercicio sin base de datos → respuesta=BDNoExiste con HTTP 200."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        return httpx.Response(200, json={"resultado": None, "respuesta": "BDNoExiste"})

    c = _client(handler)
    with pytest.raises(FactusolError, match="ejercicio sin base de datos"):
        c.load_table("F_CLI", ejercicio="2020")


def test_delete_records_is_get_with_path_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["raw_path"] = request.url.raw_path.decode()
        return httpx.Response(200, json={"resultado": "", "respuesta": "OK"})

    c = _client(handler)
    c.delete_records("F_LFA", "CODLFA='1042'")
    assert captured["method"] == "GET"
    assert captured["path"] == "/admin/BorrarRegistros/2026/F_LFA/CODLFA='1042'"
    # El filtro viaja URL-encoded: `=`, comillas o espacios romperían el path.
    assert captured["raw_path"] == (
        "/admin/BorrarRegistros/2026/F_LFA/CODLFA%3D%271042%27"
    )


def test_update_record_sends_pk_inside_registro_array():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/Autenticar":
            return _login_response(_make_jwt())
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"resultado": "", "respuesta": "OK"})

    c = _client(handler)
    c.update_record("F_CLI", {"CODCLI": 12345, "TELCLI": "600000000"})
    assert captured["path"] == "/admin/ActualizarRegistro"
    assert captured["body"]["registro"] == [
        {"columna": "CODCLI", "dato": 12345},
        {"columna": "TELCLI", "dato": "600000000"},
    ]
