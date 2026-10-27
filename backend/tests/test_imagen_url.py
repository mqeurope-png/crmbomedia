"""Imagen por URL en el editor: antes de insertar, HTTPS, 200 con
`Content-Type: image/*`, nunca la red interna, y aviso si pesa más de 1 MB.
La URL que entra es la que sale (enlazada en caliente: no se guarda ni se
sube a ningún sitio). Sin red: la web de fuera es un `MockTransport` y el DNS
un resolutor falso."""
from __future__ import annotations

from collections.abc import Callable, Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.api import imagen_url
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users

PUBLICA = "93.184.216.34"
FOTO = "https://cdn.boprint.net/wp-content/uploads/2026/10/ador-taller.jpg"
Manejador = Callable[[httpx.Request], httpx.Response]


def _resolver(ip: str):  # noqa: ANN202
    def resolver(host, port, **_kw):  # noqa: ANN001, ANN202
        return [(2, 1, 6, "", (ip, port))]
    return resolver


class _Web:
    """La web de fuera simulada: apunta cada petición (método y URL)."""

    def __init__(self, manejador: Manejador) -> None:
        self.manejador = manejador
        self.peticiones: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.peticiones.append((request.method, str(request.url)))
        return self.manejador(request)

    def cliente(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), follow_redirects=False)


def _comprobar(url: str, web: _Web, ip: str = PUBLICA) -> dict:
    return imagen_url.comprobar(url, cliente=web.cliente(), resolver=_resolver(ip))


def _imagen(peso: int, tipo: str = "image/jpeg") -> Manejador:
    def manejador(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": tipo, "content-length": str(peso)},
                              content=b"" if request.method == "HEAD" else b"x" * peso)
    return manejador


def _nunca(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no debía salir ninguna petición: {request.method} {request.url}")


# --- la comprobación -----------------------------------------------------------------------


def test_http_no_se_inserta_y_se_dice_por_que() -> None:
    web = _Web(_nunca)
    r = _comprobar("http://cdn.boprint.net/foto.jpg", web)
    assert r["ok"] is False and r["codigo"] == "no_https"
    assert "https://" in r["motivo"] and r["url"] == "http://cdn.boprint.net/foto.jpg"
    assert web.peticiones == []


def test_https_con_imagen_se_inserta_tal_cual_con_un_solo_head() -> None:
    web = _Web(_imagen(123_456))
    r = _comprobar(FOTO, web)
    assert r == {"ok": True, "url": FOTO, "content_type": "image/jpeg", "bytes": 123_456,
                 "pesada": False, "aviso": None}
    # Solo se ha preguntado (HEAD): no se ha descargado la imagen.
    assert web.peticiones == [("HEAD", FOTO)]


@pytest.mark.parametrize(("url", "ip"), [
    ("https://localhost/foto.jpg", PUBLICA),
    ("https://127.0.0.1/foto.jpg", PUBLICA),
    ("https://10.0.0.7/foto.jpg", PUBLICA),
    ("https://172.18.0.3/foto.jpg", PUBLICA),          # la red de Docker
    ("https://192.168.1.20/foto.jpg", PUBLICA),
    ("https://169.254.169.254/latest/meta-data", PUBLICA),
    ("https://api/foto.jpg", PUBLICA),                 # un servicio de Docker por su nombre
    ("https://imagenes.example.com/foto.jpg", "10.1.2.3"),    # dominio que resuelve dentro
    ("https://imagenes.example.com/foto.jpg", "127.0.0.1"),
])
def test_la_red_interna_no_se_admite_venga_de_donde_venga(url: str, ip: str) -> None:
    web = _Web(_nunca)
    r = _comprobar(url, web, ip)
    assert r["ok"] is False and r["codigo"] == "red_interna", r
    assert r["url"] == url and r["motivo"]
    assert web.peticiones == []


def test_una_redireccion_a_la_red_interna_tampoco() -> None:
    def manejador(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://127.0.0.1/admin.png"})

    r = _comprobar(FOTO, _Web(manejador))
    assert r["ok"] is False and r["codigo"] == "red_interna"


def test_un_404_no_responde() -> None:
    web = _Web(lambda request: httpx.Response(404, headers={"content-type": "text/html"}))
    r = _comprobar(FOTO, web)
    assert r["ok"] is False and r["codigo"] == "no_responde" and "404" in r["motivo"]


def test_una_pagina_web_no_es_una_imagen() -> None:
    web = _Web(lambda request: httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<html></html>"))
    r = _comprobar("https://boprint.net/ador/", web)
    assert r["ok"] is False and r["codigo"] == "no_es_imagen"
    assert "text/html" in r["motivo"]


@pytest.mark.parametrize("metodo_tipo", ["HEAD", "GET"])
@pytest.mark.parametrize("tipo", ['text/html<img src=x onerror="alert(1)">',
                                  "application/" + "x" * 80])
def test_el_tipo_que_dice_la_web_solo_se_ensena_si_es_un_tipo_normal(
    metodo_tipo: str, tipo: str,
) -> None:
    """El `Content-Type` es de la web de fuera (DATO): una cabecera rara no
    llega tal cual al motivo que pinta el editor."""
    def manejador(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD" and metodo_tipo == "GET":
            return httpx.Response(405)
        return httpx.Response(200, headers={"content-type": tipo}, content=b"<html></html>")

    r = _comprobar("https://boprint.net/ador/", _Web(manejador))
    assert r["ok"] is False and r["codigo"] == "no_es_imagen"
    assert "(es otro tipo)" in r["motivo"], r["motivo"]
    assert "<" not in r["motivo"] and "xxxx" not in r["motivo"]


def test_mas_de_un_mega_se_inserta_pero_se_avisa() -> None:
    peso = 2_400_000
    r = _comprobar(FOTO, _Web(_imagen(peso)))
    assert r["ok"] is True and r["pesada"] is True and r["bytes"] == peso
    assert r["aviso"] == ("La imagen pesa 2,3 MB: se insertará igual, pero el correo tardará en "
                          "cargar.")


def test_si_el_head_no_vale_se_mide_con_un_get() -> None:
    def manejador(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(200, headers={"content-type": "image/png"},
                              stream=httpx.ByteStream(b"p" * 5000))   # sin Content-Length

    web = _Web(manejador)
    r = _comprobar(FOTO, web)
    assert r["ok"] is True and r["content_type"] == "image/png" and r["bytes"] == 5000
    assert r["pesada"] is False and r["url"] == FOTO
    assert web.peticiones == [("HEAD", FOTO), ("GET", FOTO)]


def test_una_imagen_enorme_se_corta_al_medirla_y_se_avisa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imagen_url, "MAX_BYTES_MEDIR", 2048)

    def manejador(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-type": "image/jpeg"})   # sin tamaño
        return httpx.Response(200, headers={"content-type": "image/jpeg"},
                              stream=httpx.ByteStream(b"j" * 10_000))

    r = _comprobar(FOTO, _Web(manejador))
    assert r["ok"] is True and r["pesada"] is True and r["bytes"] is None
    assert "se insertará igual" in r["aviso"]


def test_si_tarda_demasiado_se_dice() -> None:
    def manejador(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=request)

    r = _comprobar(FOTO, _Web(manejador))
    assert r["ok"] is False and r["codigo"] == "tiempo_agotado"


@pytest.mark.parametrize(("url", "codigo"), [
    ("", "url_invalida"),
    ("cdn.boprint.net/foto.jpg", "url_invalida"),
    ("https://cdn.boprint.net:8443/foto.jpg", "puerto_no_permitido"),
    ("https://" + "a" * 2100 + ".com/foto.jpg", "url_invalida"),
    ("https://cdn.boprint\t.net/foto.jpg", "url_invalida"),     # un tabulador en medio
])
def test_lo_que_no_es_un_enlace_valido(url: str, codigo: str) -> None:
    r = _comprobar(url, _Web(_nunca))
    assert r["ok"] is False and r["codigo"] == codigo


def test_un_dominio_con_tildes_se_comprueba_como_lo_pide_httpx() -> None:
    """`straße` se resuelve como `xn--strae-oqa` (lo que pide httpx), no como
    `strasse` (lo que haría el DNS de Python): si esa forma apunta dentro, no."""
    def hostil(host, port, **_kw):  # noqa: ANN001, ANN202
        ip = "127.0.0.1" if host.startswith("xn--") else PUBLICA
        return [(2, 1, 6, "", (ip, port))]

    web = _Web(_nunca)
    r = imagen_url.comprobar("https://straße.example.com/foto.jpg", cliente=web.cliente(),
                             resolver=hostil)
    assert r["ok"] is False and r["codigo"] == "red_interna"
    assert web.peticiones == []


def test_un_dominio_que_no_existe() -> None:
    def no_resuelve(host, port, **_kw):  # noqa: ANN001, ANN202
        import socket  # noqa: PLC0415

        raise socket.gaierror("no existe")

    r = imagen_url.comprobar("https://no-existe.example/foto.jpg", cliente=_Web(_nunca).cliente(),
                             resolver=no_resuelve)
    assert r["ok"] is False and r["codigo"] == "no_resuelve"


# --- la API ----------------------------------------------------------------------------------


@pytest.fixture()
def http() -> Generator[TestClient, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)

    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def test_la_api_responde_siempre_200_con_el_veredicto(
    http: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    web = _Web(_imagen(1_500_000))
    monkeypatch.setattr(imagen_url, "_RED_PRUEBAS",
                        {"cliente": web.cliente(), "resolver": _resolver(PUBLICA)})
    cabeceras = auth_headers(http, "admin")
    r = http.post("/api/emails/imagen-url/comprobar", headers=cabeceras, json={"url": FOTO})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["url"] == FOTO and r.json()["pesada"] is True
    r = http.post("/api/emails/imagen-url/comprobar", headers=cabeceras,
                  json={"url": "http://cdn.boprint.net/foto.jpg"})
    assert r.status_code == 200 and r.json()["codigo"] == "no_https"
    # El DNS (falso) dice que el dominio está dentro: no se pide nada.
    monkeypatch.setattr(imagen_url, "_RED_PRUEBAS",
                        {"cliente": _Web(_nunca).cliente(), "resolver": _resolver("172.17.0.2")})
    r = http.post("/api/emails/imagen-url/comprobar", headers=cabeceras, json={"url": FOTO})
    assert r.status_code == 200 and r.json()["codigo"] == "red_interna"


def test_la_api_responde_200_tambien_con_lo_que_httpx_no_acepta(
    http: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un tabulador copiado en medio del enlace: `urlsplit` lo quita y httpx lo
    rechaza. Es `url_invalida` (200), no un 500."""
    monkeypatch.setattr(imagen_url, "_RED_PRUEBAS",
                        {"cliente": _Web(_nunca).cliente(), "resolver": _resolver(PUBLICA)})
    url = "https://exa\tmple.com/a.png"
    r = http.post("/api/emails/imagen-url/comprobar", headers=auth_headers(http, "admin"),
                  json={"url": url})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": False, "url": url, "codigo": "url_invalida",
                        "motivo": "La dirección no es válida."}


def test_la_api_es_del_crm_el_taller_no_la_usa(http: TestClient) -> None:
    r = http.post("/api/emails/imagen-url/comprobar", headers=auth_headers(http, "sat"),
                  json={"url": FOTO})
    assert r.status_code == 403
    assert http.post("/api/emails/imagen-url/comprobar", json={"url": FOTO}).status_code == 401
