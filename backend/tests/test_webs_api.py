"""El índice de las webs: la búsqueda (MySQL y la de los tests) y la API
`/api/erp/webs` con las formas de `websApi.ts`. Nada sale a la red."""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.fuentes_externas import WebIndiceEntrada, WebIndicePasada, WebIndiceRegla
from app.services.web_forms import sitios as registro_sitios
from app.services.webs import busqueda, pasadas
from app.services.webs.reglas_partida import CATEGORIAS_FUERA, PAGINAS_FUERA
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_webs_comun import registro_con

AHORA = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with fabrica() as s:
        seed_test_users(s)
    yield fabrica
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(factory: sessionmaker, monkeypatch: pytest.MonkeyPatch,
         ) -> Generator[TestClient, None, None]:
    registro = registro_con("boprint", "fluxlasers")
    monkeypatch.setattr(registro_sitios, "cargar", lambda _session: registro)

    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def fila(s: Session, **datos: Any) -> WebIndiceEntrada:
    base: dict[str, Any] = {
        "sitio": "fluxlasers", "dominio": "fluxlasers.eu", "tipo": "producto", "idioma": "es",
        "url": f"https://fluxlasers.eu/producto/{datos.get('wp_id', 1)}/", "titulo": "",
        "texto": "", "visto_en": AHORA,
    }
    base.update(datos)
    entrada = WebIndiceEntrada(**base)
    s.add(entrada)
    s.flush()
    return entrada


def sembrar(s: Session) -> dict[str, WebIndiceEntrada]:
    filas = {
        "beambox": fila(s, wp_id=3949, titulo="Beambox II 55W",
                        texto="Cortadora láser CO2.\n\nCámara de alta resolución y autofoco.\n\n"
                              "Área de trabajo de 600 x 375 mm.",
                        precio_web=Decimal("3049.59"), precio_web_texto="3,049.59€ (+ IVA)",
                        precio_iva="sin_iva", moneda="EUR",
                        imagen="https://fluxlasers.eu/a.jpg",
                        galeria_json='["https://fluxlasers.eu/b.jpg"]',
                        categorias_json='["Cortadoras Láser"]'),
        "hexa": fila(s, wp_id=1901, titulo="Flux Hexa 60W",
                     texto="La Hexa corta más rápido. Comparada con la Beambox es el doble."),
        "pagina": fila(s, wp_id=111, tipo="pagina", titulo="Preguntas frecuentes",
                       texto="¿Qué grosor corta la Beambox? Hasta 10 mm de madera.\n\n"
                             "Las impresoras UV no son de FLUX."),
        "aleman": fila(s, wp_id=2001, idioma="de", titulo="Beambox II",
                       texto="Laserschneider Beambox mit Kamera."),
        "uv": fila(s, sitio="boprint", dominio="boprint.net", wp_id=364,
                   titulo="Impresora UV Led 5000U", texto="Impresión UV sobre objetos A3."),
        "ida": fila(s, wp_id=5001, titulo="Beambox retirada", texto="Beambox antigua.",
                    ido_en=AHORA),
    }
    s.commit()
    return filas


# --- búsqueda ----------------------------------------------------------------------------------


def test_la_consulta_de_mysql_usa_match_against_con_las_columnas_del_indice() -> None:
    filtros = busqueda._filtros("es", None, "producto")  # noqa: SLF001
    sql = busqueda.consulta_mysql(["beambox", "camara", "uv"], filtros, todas=True)
    compilado = sql.compile(dialect=mysql.dialect())
    texto = str(compilado)
    # Las MISMAS columnas que el índice FULLTEXT `ft_web_indice_titulo_texto`.
    assert "MATCH (web_indice.titulo, web_indice.texto) AGAINST (%s IN BOOLEAN MODE)" in texto
    assert "web_indice.ido_en IS NULL" in texto
    assert "lower(web_indice.titulo) LIKE" in texto           # «uv»: 2 letras, por LIKE
    assert "+beambox* +camara*" in compilado.params.values()
    assert "%uv%" in compilado.params.values()
    assert "ORDER BY MATCH" in texto
    alguna = busqueda.consulta_mysql(["beambox", "camara"], filtros, todas=False)
    assert "beambox* camara*" in alguna.compile(dialect=mysql.dialect()).params.values()
    from app.models.fuentes_externas import WebIndiceEntrada as Modelo

    indice = next(i for i in Modelo.__table__.indexes if i.name == "ft_web_indice_titulo_texto")
    assert [c.name for c in indice.columns] == ["titulo", "texto"]


def test_la_consulta_se_sanea() -> None:
    assert busqueda.palabras_de('+Beambox -"hexa" (UV) ~A3* @x <> de la') == [
        "beambox", "hexa", "uv", "a3", "x"]
    assert busqueda.palabras_de("   ") == []
    assert busqueda.palabras_de("la de el") == []


def test_buscar_con_like_todas_las_palabras_y_el_titulo_pesa_mas(factory: sessionmaker) -> None:
    with factory() as s:
        filas = sembrar(s)
        hallados = busqueda.buscar(s, "Beambox", idioma="es")
        ids = [f.id for f in hallados]
        assert ids[0] == filas["beambox"].id                     # en el título
        assert filas["ida"].id not in ids                        # lo ido no sale
        assert filas["aleman"].id not in ids                     # otro idioma
        assert {filas["hexa"].id, filas["pagina"].id} <= set(ids)
        # Todas las palabras: «Beambox cámara» solo casa con la Beambox.
        assert [f.id for f in busqueda.buscar(s, "beambox cámara")] == [filas["beambox"].id]
        # Ninguna tiene todas: las que tienen alguna.
        assert {f.id for f in busqueda.buscar(s, "hexa zzzzz")} == {filas["hexa"].id}
        # Las cortas («UV», «A3») también, por LIKE.
        assert [f.id for f in busqueda.buscar(s, "UV A3")] == [filas["uv"].id]
        assert [f.id for f in busqueda.buscar(s, "beambox", tipo="pagina")] == [
            filas["pagina"].id]
        assert [f.id for f in busqueda.buscar(s, "impresora", sitio="boprint")] == [
            filas["uv"].id]
        assert busqueda.buscar(s, "") == []


def test_el_fragmento_es_el_parrafo_que_importa_recortado_y_con_su_url(
        factory: sessionmaker) -> None:
    with factory() as s:
        filas = sembrar(s)
        largo = fila(s, wp_id=9, tipo="pagina", titulo="Guía",
                     texto="\n\n".join([f"Párrafo {i} de relleno sin nada." * 5 for i in range(30)]
                                       + ["El autofoco de la Beambox se calibra así." * 3]))
        s.commit()
        guia = next(f for f in busqueda.buscar(s, "autofoco calibra", max_chars=300)
                    if f.id == largo.id)
        assert "autofoco" in guia.fragmento and len(guia.fragmento) <= 301
        assert "Párrafo 0" not in guia.fragmento
        beambox = busqueda.fragmento_como_dict(busqueda.buscar(s, "Beambox II 55W")[0])
    assert beambox == {
        "id": filas["beambox"].id, "sitio": "fluxlasers", "dominio": "fluxlasers.eu",
        "tipo": "producto", "idioma": "es", "titulo": "Beambox II 55W",
        "url": "https://fluxlasers.eu/producto/3949/",
        "fragmento": beambox["fragmento"], "imagen": "https://fluxlasers.eu/a.jpg",
        "galeria": ["https://fluxlasers.eu/b.jpg"], "categorias": ["Cortadoras Láser"],
        "precio_web": 3049.59, "precio_web_texto": "3,049.59€ (+ IVA)",
        "precio_iva": "sin_iva", "moneda": "EUR",
    }


def test_fragmentos_de_lo_elegido_en_su_orden_y_sin_lo_ido(factory: sessionmaker) -> None:
    with factory() as s:
        filas = sembrar(s)
        elegidos = [filas["pagina"].id, "no-existe", filas["ida"].id, filas["beambox"].id]
        salida = busqueda.fragmentos_de(s, elegidos, consulta="grosor madera")
        assert [f.id for f in salida] == [filas["pagina"].id, filas["beambox"].id]
        assert salida[0].fragmento.startswith("¿Qué grosor corta la Beambox?")
        assert busqueda.fragmentos_de(s, []) == []


# --- API ---------------------------------------------------------------------------------------


def test_buscar_lo_usa_el_redactor_y_cualquiera_con_erp(http: TestClient,
                                                        factory: sessionmaker) -> None:
    with factory() as s:
        sembrar(s)
    for rol in ("admin", "user", "sat"):
        r = http.get("/api/erp/webs/buscar", params={"q": "Beambox", "idioma": "es"},
                     headers=auth_headers(http, rol))
        assert r.status_code == 200, (rol, r.text)
        items = r.json()["items"]
        assert items and items[0]["titulo"] == "Beambox II 55W"
        assert items[0]["precio_iva"] == "sin_iva" and items[0]["precio_web"] == 3049.59
    r = http.get("/api/erp/webs/buscar", params={"q": "x", "tipo": "otra"},
                 headers=auth_headers(http, "admin"))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "tipo_no_valido"
    assert http.get("/api/erp/webs/buscar", params={"q": "x"},
                    headers=auth_headers(http, "viewer")).status_code == 403


def test_estado_por_web_y_tipo(http: TestClient, factory: sessionmaker) -> None:
    with factory() as s:
        sembrar(s)
        s.add(WebIndicePasada(origen="programada", estado="ok", resumen_json='{"boprint": '
                              '{"estado": "ok", "productos": 1}}', pendientes_json="[]",
                              inicio=AHORA, fin=AHORA))
        s.commit()
    r = http.get("/api/erp/webs/estado", headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    datos = r.json()
    por_web = {f["sitio"]: f for f in datos["filas"]}
    assert [f["sitio"] for f in datos["filas"]] == ["boprint", "fluxlasers"]
    flux = por_web["fluxlasers"]
    assert flux["dominio"] == "fluxlasers.eu"
    assert (flux["pagina"], flux["entrada"], flux["producto"], flux["idos"]) == (1, 0, 3, 1)
    assert flux["idiomas"] == ["de", "es"] and flux["ultima"].startswith("2026-10-27")
    assert por_web["boprint"]["producto"] == 1 and por_web["boprint"]["dominio"] == "boprint.net"
    assert datos["total"] == {"pagina": 1, "entrada": 0, "producto": 4}
    assert datos["ultima_pasada"]["estado"] == "ok"
    assert datos["ultima_pasada"]["resumen"]["boprint"]["productos"] == 1
    assert datos["en_curso"] is None
    for rol in ("user", "sat"):
        assert http.get("/api/erp/webs/estado",
                        headers=auth_headers(http, rol)).status_code == 403


def test_reindexar_encola_y_no_recorre_en_la_peticion(
        http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    encolados: list[str] = []
    monkeypatch.setattr(pasadas, "_encolar", encolados.append)
    cabeceras = auth_headers(http, "admin")
    r = http.post("/api/erp/webs/reindexar", headers=cabeceras)
    assert r.status_code == 200, r.text
    pasada = r.json()
    assert pasada["estado"] == "en_cola" and pasada["origen"] == "manual"
    assert pasada["pendientes"] == ["boprint", "fluxlasers"] and pasada["resumen"] == {}
    assert set(pasada) == {"id", "origen", "estado", "inicio", "fin", "pendientes", "resumen",
                           "error", "creado_en"}
    assert encolados == [pasada["id"]]
    # Otra vez con esa en cola: la misma.
    otra = http.post("/api/erp/webs/reindexar", headers=cabeceras).json()
    assert otra["id"] == pasada["id"] and encolados == [pasada["id"]]
    estado = http.get("/api/erp/webs/estado", headers=cabeceras).json()
    assert estado["en_curso"]["id"] == pasada["id"]
    assert http.post("/api/erp/webs/reindexar",
                     headers=auth_headers(http, "user")).status_code == 403


def test_reglas_crud_y_validacion(http: TestClient, factory: sessionmaker) -> None:
    cabeceras = auth_headers(http, "admin")
    r = http.get("/api/erp/webs/reglas", headers=cabeceras)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    # Con la tabla vacía se siembran las de partida, para poder editarlas.
    assert len(items) == len(PAGINAS_FUERA) + len(CATEGORIAS_FUERA)
    assert set(items[0]) == {"id", "tipo", "patron", "sitio", "activo", "nota", "posicion"}
    nueva = http.post("/api/erp/webs/reglas", headers=cabeceras, json={
        "tipo": "categoria_fuera", "patron": "Ersatzteile", "nota": "repuestos en alemán"})
    assert nueva.status_code == 201, nueva.text
    regla = nueva.json()
    assert regla["patron"] == "Ersatzteile" and regla["activo"] is True and regla["sitio"] is None
    por_id = http.post("/api/erp/webs/reglas", headers=cabeceras, json={
        "tipo": "producto_incluir_id", "patron": " 0042 ", "sitio": "fluxlasers"})
    assert por_id.status_code == 201 and por_id.json()["patron"] == "42"
    for malo, codigo in (
        ({"tipo": "pagina_excluir_slug", "patron": "legal("}, "patron_no_valido"),
        ({"tipo": "producto_excluir_id", "patron": "abc"}, "patron_no_valido"),
        ({"tipo": "pagina_excluir_id", "patron": ""}, "patron_no_valido"),
        ({"tipo": "otra_cosa", "patron": "x"}, "tipo_no_valido"),
        ({"tipo": "categoria_fuera", "patron": "x", "sitio": "no-existe"}, "sitio_no_valido"),
    ):
        r = http.post("/api/erp/webs/reglas", headers=cabeceras, json=malo)
        assert r.status_code == 400, (malo, r.text)
        assert r.json()["detail"]["code"] == codigo
    cambio = http.patch(f"/api/erp/webs/reglas/{regla['id']}", headers=cabeceras,
                        json={"activo": False, "patron": "Ersatzteil", "posicion": 7})
    assert cambio.status_code == 200, cambio.text
    assert (cambio.json()["activo"], cambio.json()["patron"], cambio.json()["posicion"]) == (
        False, "Ersatzteil", 7)
    slug = next(i for i in items if i["tipo"] == "pagina_excluir_slug")
    assert http.patch(f"/api/erp/webs/reglas/{slug['id']}", headers=cabeceras,
                      json={"patron": "[sin cerrar"}).status_code == 400
    assert http.delete(f"/api/erp/webs/reglas/{regla['id']}",
                       headers=cabeceras).json() == {"ok": True}
    assert http.delete(f"/api/erp/webs/reglas/{regla['id']}",
                       headers=cabeceras).status_code == 404
    with factory() as s:
        assert s.query(WebIndiceRegla).filter_by(patron="42", sitio="fluxlasers").count() == 1
    assert http.get("/api/erp/webs/reglas",
                    headers=auth_headers(http, "user")).status_code == 403


def test_la_maquina_sale_antes_que_sus_accesorios(factory: sessionmaker) -> None:
    """Buscar «Beambox II» trae primero la máquina, no el accesorio que la
    nombra ni la página de tienda que la repite veinte veces."""
    with factory() as s:
        tienda = fila(s, wp_id=327, tipo="pagina", titulo="Tienda",
                      texto="\n".join(["Módulo de cámara – Beambox II"] * 20))
        accesorio = fila(s, wp_id=4233, titulo="Extension Pocket Add-on – Beambox II",
                         texto="Amplía la Beambox II. " * 6)
        maquina = fila(s, wp_id=3949, titulo="Beambox II 55W",
                       texto="La Beambox II corta y graba. " * 3)
        s.commit()
        orden = [f.id for f in busqueda.buscar(s, "Beambox II")]
    assert orden[0] == maquina.id
    assert orden.index(accesorio.id) < orden.index(tienda.id)
