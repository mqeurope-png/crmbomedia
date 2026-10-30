"""El histórico de ventas de FACTUSOL dentro de BoHub: «¿quién compró qué?»
(30/10/2026).

FACTUSOL no se toca: `FactusolFalso` responde `load_table` desde un diccionario
{ejercicio: {tabla: filas}}, y un ejercicio que no está responde como la API
real (`BDNoExiste` → «sin base de datos»). Clientes, artículos y documentos
son FIGURANTES.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.session import get_session
from app.erp import historico, historico_job
from app.erp.models import (
    ErpSettings,
    HistoricoDocumento,
    HistoricoEjercicio,
    HistoricoFamilia,
    HistoricoLinea,
)
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID
from app.integrations.factusol.client import FactusolError
from app.main import app
from app.models.crm import Base, Company
from tests._test_helpers import auth_headers, seed_test_users

# --- figurantes --------------------------------------------------------------------------

CLI_TALLER = "4101"       # Talleres Figurante S.L., vinculado a una empresa de BoHub
CLI_ROTULOS = "4102"      # Rótulos de Ejemplo S.C., sin empresa en BoHub
MAQUINA = "1712"          # «Láser Figurante 1390»
TINTA = "2207"            # «Tinta de ejemplo cian 1 l»
DESCATALOGADA = "0917"    # solo en el catálogo de 2017: «Plotter Antiguo X-70»


def _fac(codigo: int, cli: str, nombre: str, fecha: str, serie: str = "5") -> dict:
    return {"TIPFAC": serie, "CODFAC": codigo, "FECFAC": fecha, "CLIFAC": cli,
            "CNOFAC": nombre, "REFFAC": f"Pedido figurante {codigo}"}


def _lfa(codigo: int, pos: int, art: str, desc: str, can: float, pre: float,
         serie: str = "5") -> dict:
    return {"TIPLFA": serie, "CODLFA": codigo, "POSLFA": pos, "ARTLFA": art,
            "DESLFA": desc, "CANLFA": can, "PRELFA": pre, "TOTLFA": round(can * pre, 2)}


def _alb(codigo: int, cli: str, nombre: str, fecha: str, serie: str = "5") -> dict:
    return {"TIPALB": serie, "CODALB": codigo, "FECALB": fecha, "CLIALB": cli,
            "CNOALB": nombre}


def _lal(codigo: int, pos: int, art: str, desc: str, can: float, serie: str = "5") -> dict:
    return {"TIPLAL": serie, "CODLAL": codigo, "POSLAL": pos, "ARTLAL": art,
            "DESLAL": desc, "CANLAL": can, "PRELAL": 10, "TOTLAL": can * 10}


def _art(codart: str, desc: str, fam: str) -> dict:
    return {"CODART": codart, "DESART": desc, "FAMART": fam, "EQUART": ""}


def datos_figurantes() -> dict[str, dict[str, list[dict]]]:
    return {
        "2026": {
            "F_FAC": [
                _fac(260001, CLI_TALLER, "Talleres Figurante S.L.", "2026-03-10T00:00:00"),
                _fac(260002, CLI_ROTULOS, "Rótulos de Ejemplo S.C.", "2026-05-02T00:00:00"),
            ],
            "F_LFA": [
                _lfa(260001, 1, MAQUINA, "Láser Figurante 1390", 1, 9000),
                _lfa(260001, 2, "", "Portes incluidos. Garantía 2 años.", 0, 0),
                _lfa(260002, 1, TINTA, "Tinta de ejemplo cian 1 l", 6, 40),
                _lfa(260002, 2, MAQUINA, "Láser Figurante 1390", 1, 8800),
            ],
            # El albarán de la factura 260002 (la misma venta) y uno sin facturar.
            "F_ALB": [
                _alb(26001, CLI_ROTULOS, "Rótulos de Ejemplo S.C.", "2026-05-01T00:00:00"),
                _alb(26002, "4103", "Cliente Solo Albarán", "2026-06-01T00:00:00"),
            ],
            "F_LAL": [
                _lal(26001, 1, MAQUINA, "Láser Figurante 1390", 1),
                _lal(26002, 1, MAQUINA, "Láser Figurante 1390", 2),
            ],
            "F_ART": [
                _art(MAQUINA, "Láser Figurante 1390", "LAS"),
                _art(TINTA, "Tinta de ejemplo cian 1 l", "TIN"),
            ],
            "F_FAM": [{"CODFAM": "LAS", "DESFAM": "Láseres"},
                      {"CODFAM": "TIN", "DESFAM": "Tintas"},
                      {"CODFAM": "PLO", "DESFAM": "Plotters"}],
        },
        "2017": {
            "F_FAC": [
                _fac(170001, CLI_TALLER, "Talleres Figurante S.L.", "2017-04-20T00:00:00", "1"),
                # La fecha imposible que hay en FACTUSOL.
                _fac(170002, CLI_ROTULOS, "Rótulos de Ejemplo S.C.", "1900-01-01T00:00:00", "1"),
            ],
            "F_LFA": [
                _lfa(170001, 1, DESCATALOGADA, "PLOTTER X70", 1, 3000, "1"),
                _lfa(170001, 2, MAQUINA, "LASER 1390", 1, 7000, "1"),
                _lfa(170002, 1, "", "Ref. pedido figurante 77", 0, 0, "1"),
                _lfa(170002, 2, MAQUINA, "LASER 1390", 2, 7000, "1"),
            ],
            "F_ALB": [], "F_LAL": [],
            "F_ART": [
                _art(DESCATALOGADA, "Plotter Antiguo X-70", "PLO"),
                _art(MAQUINA, "Láser 1390 (2017)", "LAS"),
            ],
        },
    }


class FactusolFalso:
    def __init__(self, datos: dict[str, dict[str, list[dict]]]) -> None:
        self.datos = datos
        self.llamadas: list[tuple[str, str]] = []

    def load_table(self, tabla: str, filtro: str = "1=1", ejercicio: str | None = None,
                   **_: Any) -> list[dict]:
        self.llamadas.append((tabla, str(ejercicio)))
        if str(ejercicio) not in self.datos:
            raise FactusolError(
                "POST /api/consulta/CargaTabla → ejercicio sin base de datos en FACTUSOL "
                "(respuesta='BDNoExiste')", status=200, body='{"respuesta":"BDNoExiste"}')
        return [dict(r) for r in self.datos[str(ejercicio)].get(tabla, [])]


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(ErpSettings(id=ERP_SETTINGS_SINGLETON_ID, factusol_default_ejercicio="2026"))
        seed.add(Company(name="Talleres Figurante", factusol_company_id=CLI_TALLER))
        seed.commit()
    yield factory
    engine.dispose()


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def cargado(session_factory) -> sessionmaker:
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(datos_figurantes()), ["2026", "2017"],
                         en_curso="2026")
    return session_factory


def _recuentos(s: Session) -> tuple[int, int]:
    return (s.scalar(select(func.count(HistoricoDocumento.id))),
            s.scalar(select(func.count(HistoricoLinea.id))))


# --- la carga ----------------------------------------------------------------------------


def test_volcar_dos_veces_no_duplica(session_factory) -> None:
    falso = FactusolFalso(datos_figurantes())
    with session_factory() as s:
        primera = historico.cargar_ejercicio(s, falso, "2026")
        antes = _recuentos(s)
        segunda = historico.cargar_ejercicio(s, falso, "2026")
        assert _recuentos(s) == antes == (4, 6)
    assert primera["estado"] == segunda["estado"] == "cargado"
    assert (segunda["facturas"], segunda["albaranes"], segunda["lineas"]) == (2, 2, 6)


def test_ejercicio_no_disponible_se_registra_y_no_rompe_los_demas(session_factory) -> None:
    falso = FactusolFalso(datos_figurantes())
    with session_factory() as s:
        out = historico.cargar(s, falso, ["2026", "2015", "2017"], en_curso="2026")
        assert [r["estado"] for r in out] == ["cargado", "no_disponible", "cargado"]
        estado = {e["ejercicio"]: e for e in historico.estado_ejercicios(s, "2026")}
    assert estado["2015"]["estado"] == "no_disponible"
    assert "reábrelo en el escritorio de FACTUSOL" in estado["2015"]["detalle"]
    assert estado["2016"]["estado"] == "sin_cargar"
    assert estado["2017"]["lineas"] == 4
    assert list(estado)[0] == "2026" and list(estado)[-1] == "2014"


def test_ejercicio_que_deja_de_estar_disponible_conserva_lo_volcado(cargado) -> None:
    datos = datos_figurantes()
    del datos["2017"]
    with cargado() as s:
        historico.cargar_ejercicio(s, FactusolFalso(datos), "2017")
        fila = s.get(HistoricoEjercicio, "2017")
        assert fila.estado == "no_disponible" and fila.cargado_at is not None
        assert s.scalar(select(func.count(HistoricoLinea.id))
                        .where(HistoricoLinea.ejercicio == "2017")) == 4


def test_fallo_que_no_es_bdnoexiste_queda_como_error_sin_datos_de_clientes(
    session_factory,
) -> None:
    class Roto:
        def load_table(self, *a: Any, **k: Any) -> list[dict]:
            raise TimeoutError("Talleres Figurante S.L. 4101")

    with session_factory() as s:
        r = historico.cargar_ejercicio(s, Roto(), "2026")
    assert r["estado"] == "error"
    assert r["detalle"] == "No se ha podido leer FACTUSOL (TimeoutError)."


def test_fecha_1900_no_tumba_nada(cargado) -> None:
    with cargado() as s:
        doc = s.scalars(select(HistoricoDocumento)
                        .where(HistoricoDocumento.codigo == 170002)).one()
        assert doc.fecha is None and doc.fecha_original.startswith("1900-01-01")
        rotulos = {c["codcli"]: c for c in historico.compradores(s, codart=MAQUINA,
                                                                 desde=2017, hasta=2017)}
    ultima = rotulos[CLI_ROTULOS]["ultima_compra"]
    assert ultima["fecha"] is None and ultima["fecha_original"].startswith("1900")
    assert ultima["ejercicio"] == "2017"


def test_linea_de_nota_se_guarda_y_no_cuenta(cargado) -> None:
    with cargado() as s:
        nota = s.scalars(select(HistoricoLinea)
                         .where(HistoricoLinea.descripcion.like("Portes%"))).one()
        assert nota.es_producto is False and nota.codart is None
        assert historico.resumen_notas(s) == {"lineas": 10, "producto": 8, "notas": 2}
        estado = {e["ejercicio"]: e for e in historico.estado_ejercicios(s, "2026")}
        # Buscar por el texto de la nota no encuentra compradores.
        assert historico.compradores(s, q="Portes") == []
    assert (estado["2026"]["lineas_producto"], estado["2026"]["lineas_nota"]) == (5, 1)


def test_articulo_descatalogado_sale_con_el_nombre_de_su_anio(cargado) -> None:
    with cargado() as s:
        compradores = historico.compradores(s, q="Plotter Antiguo")
        encontrados = historico.buscar_articulos(s, "Plotter")
    assert [c["codcli"] for c in compradores] == [CLI_TALLER]
    assert compradores[0]["articulos"] == [
        {"codart": DESCATALOGADA, "descripcion": "Plotter Antiguo X-70"}]
    assert encontrados[0]["descripcion"] == "Plotter Antiguo X-70"
    assert encontrados[0]["ultimo_ejercicio"] == "2017"


# --- la consulta -------------------------------------------------------------------------


def test_buscar_articulo_devuelve_compradores_con_ultima_compra_y_unidades(cargado) -> None:
    with cargado() as s:
        out = {c["codcli"]: c for c in historico.compradores(s, codart=MAQUINA)}
    assert set(out) == {CLI_TALLER, CLI_ROTULOS, "4103"}
    taller = out[CLI_TALLER]
    assert taller["ultima_compra"] == {
        "fecha": "2026-03-10", "fecha_original": None, "ejercicio": "2026",
        "tipo": "factura", "documento": "5-260001"}
    assert (taller["unidades"], taller["compras"]) == (2, 2)
    # Rótulos: 2 en 2017 + 1 en 2026 facturadas; el albarán de esa misma
    # venta no se suma.
    assert out[CLI_ROTULOS]["unidades"] == 3
    assert out[CLI_ROTULOS]["ultima_compra"]["documento"] == "5-260002"
    # Solo albarán (sin facturar): cuenta lo del albarán.
    assert out["4103"]["unidades"] == 2
    assert out["4103"]["ultima_compra"]["tipo"] == "albaran"


def test_filtro_por_anios_y_por_familia(cargado) -> None:
    with cargado() as s:
        solo_2017 = historico.compradores(s, codart=MAQUINA, desde=2017, hasta=2017)
        tintas = historico.compradores(s, familia="TIN")
        fams = {f["codigo"]: f for f in historico.familias(s)}
    assert {c["codcli"] for c in solo_2017} == {CLI_TALLER, CLI_ROTULOS}
    assert all(c["ultima_compra"]["ejercicio"] == "2017" for c in solo_2017)
    assert [c["codcli"] for c in tintas] == [CLI_ROTULOS]
    assert (fams["LAS"]["articulos"], fams["LAS"]["ventas"]) == (1, 6)
    assert (fams["PLO"]["articulos"], fams["PLO"]["ventas"]) == (1, 1)


def test_cliente_sin_empresa_vinculada_se_ve_marcado(cargado) -> None:
    with cargado() as s:
        out = {c["codcli"]: c for c in historico.compradores(s, codart=MAQUINA)}
    assert out[CLI_TALLER]["empresa"]["nombre"] == "Talleres Figurante"
    assert out[CLI_ROTULOS]["empresa"] is None


def test_la_clase_de_la_familia_sobrevive_a_recargar(cargado) -> None:
    with cargado() as s:
        historico.clasificar_familia(s, "LAS", "maquina", por="admin@example.com")
        historico.cargar_familias(s, FactusolFalso(datos_figurantes()), "2026")
        assert s.get(HistoricoFamilia, "LAS").clase == "maquina"
        with pytest.raises(ValueError):
            historico.clasificar_familia(s, "LAS", "cohete", por="x")


# --- la API ------------------------------------------------------------------------------


def test_api_compradores_csv_y_marca_sin_empresa(http, cargado) -> None:
    h = auth_headers(http, "admin")
    r = http.get(f"/api/erp/historico/compradores?codart={MAQUINA}", headers=h)
    assert r.status_code == 200
    assert (r.json()["total"], r.json()["sin_empresa"]) == (3, 2)
    csv = http.get(f"/api/erp/historico/compradores.csv?codart={MAQUINA}", headers=h)
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
    texto = csv.content.decode("utf-8-sig")
    assert texto.splitlines()[0].startswith("Código cliente FACTUSOL;Cliente;")
    assert "(sin empresa vinculada)" in texto


def test_api_el_taller_no_ve_el_historico(http, cargado) -> None:
    r = http.get("/api/erp/historico/ejercicios", headers=auth_headers(http, "sat"))
    assert r.status_code == 403


def test_api_estado_ejercicios_y_notas(http, cargado) -> None:
    r = http.get("/api/erp/historico/ejercicios", headers=auth_headers(http, "admin"))
    body = r.json()
    assert body["en_curso"] == "2026" and len(body["ejercicios"]) == 13
    assert body["notas"]["notas"] == 2


def test_api_lanzar_carga_encola_y_marca_cargando(http, session_factory, monkeypatch) -> None:
    encolados: list[list[str]] = []

    def falso(session: Session, ejercicios: list[str]) -> str:
        encolados.append(ejercicios)
        historico_job.marcar_cargando(session, ejercicios)
        return "encolada"

    monkeypatch.setattr(historico_job, "encolar_carga", falso)
    h = auth_headers(http, "admin")
    uno = http.post("/api/erp/historico/cargar", json={"ejercicios": ["2017"]}, headers=h)
    todos = http.post("/api/erp/historico/cargar", json={}, headers=h)
    malo = http.post("/api/erp/historico/cargar", json={"ejercicios": ["1999"]}, headers=h)
    assert (uno.status_code, todos.status_code, malo.status_code) == (202, 202, 422)
    assert encolados[0] == ["2017"] and len(encolados[1]) == 13
    estado = http.get("/api/erp/historico/ejercicios", headers=h).json()["ejercicios"]
    assert {e["estado"] for e in estado} == {"cargando"}


def test_api_carga_ocupada_da_409(http, monkeypatch) -> None:
    monkeypatch.setattr(historico_job, "encolar_carga", lambda s, e: "ocupada")
    r = http.post("/api/erp/historico/cargar", json={}, headers=auth_headers(http, "admin"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "carga_en_marcha"


def test_api_clasificar_familia(http, cargado) -> None:
    r = http.patch("/api/erp/historico/familias/LAS", json={"clase": "maquina"},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200 and r.json()["clase"] == "maquina"
    fams = http.get("/api/erp/historico/familias", headers=auth_headers(http, "admin")).json()
    assert {f["codigo"]: f["clase"] for f in fams["items"]}["LAS"] == "maquina"
    assert fams["clases"] == ["maquina", "consumible", "otro"]


# --- el trabajo --------------------------------------------------------------------------


def test_el_tic_recarga_el_ejercicio_en_curso_y_las_familias(session_factory,
                                                              monkeypatch) -> None:
    falso = FactusolFalso(datos_figurantes())
    monkeypatch.setattr(historico_job, "_cliente_y_en_curso", lambda s: (falso, "2026"))
    with session_factory() as s:
        out = historico_job.run(s)
        assert [r["ejercicio"] for r in out] == ["2026"]
        assert s.get(HistoricoFamilia, "TIN").descripcion == "Tintas"
    assert {e for _, e in falso.llamadas} == {"2026"}


def test_carga_atascada_se_ve_como_interrumpida(session_factory) -> None:
    from datetime import datetime, timedelta

    with session_factory() as s:
        s.add(HistoricoEjercicio(ejercicio="2020", estado="cargando",
                                 intento_at=datetime.utcnow() - timedelta(hours=2)))
        s.commit()
        e = {x["ejercicio"]: x for x in historico.estado_ejercicios(s, "2026")}["2020"]
    assert e["estado"] == "error" and "interrumpió" in e["detalle"]


# --- lo que encontró la revisión ---------------------------------------------------------


def test_un_fallo_al_guardar_no_para_a_los_demas_ni_borra_lo_volcado(cargado,
                                                                       monkeypatch) -> None:
    real = historico._sustituir

    def rompe_2017(session, ejercicio, *a, **k):
        if ejercicio == "2017":
            real(session, ejercicio, *a, **k)   # borra y escribe a medias…
            raise RuntimeError("Talleres Figurante S.L. 4101")   # …y falla
        return real(session, ejercicio, *a, **k)

    monkeypatch.setattr(historico, "_sustituir", rompe_2017)
    with cargado() as s:
        out = historico.cargar(s, FactusolFalso(datos_figurantes()), ["2017", "2026"],
                               en_curso="2026")
        assert [r["estado"] for r in out] == ["error", "cargado"]
        fila = s.get(HistoricoEjercicio, "2017")
        assert fila.detalle == "No se ha podido guardar en BoHub (RuntimeError)."
        # Lo de 2017 que ya había sigue entero.
        assert s.scalar(select(func.count(HistoricoLinea.id))
                        .where(HistoricoLinea.ejercicio == "2017")) == 4


def test_unidades_por_articulo_factura_o_albaran(session_factory) -> None:
    datos = datos_figurantes()
    # 4102 tiene la tinta FACTURADA y, además, un albarán sin facturar de la
    # máquina 9060 (otro artículo de la misma familia): cuentan los dos.
    datos["2026"]["F_ART"].append(_art("1713", "Láser Figurante 9060", "LAS"))
    datos["2026"]["F_ALB"].append(_alb(26003, CLI_ROTULOS, "Rótulos de Ejemplo S.C.",
                                       "2026-07-01T00:00:00"))
    datos["2026"]["F_LAL"].append(_lal(26003, 1, "1713", "Láser Figurante 9060", 1))
    with session_factory() as s:
        historico.cargar_ejercicio(s, FactusolFalso(datos), "2026")
        out = {c["codcli"]: c for c in historico.compradores(s, familia="LAS")}
    # 1712: 1 facturada (su albarán 26001 no suma); 1713: 1 por albarán.
    assert out[CLI_ROTULOS]["unidades"] == 2
    # Documentos: la factura 260002 (1712) y el albarán 26003 (1713).
    assert out[CLI_ROTULOS]["compras"] == 2


def test_los_comodines_del_usuario_no_son_comodines(cargado) -> None:
    with cargado() as s:
        assert historico.buscar_articulos(s, "L_ser") == []
        assert historico.compradores(s, q="%") == []
        assert historico.buscar_articulos(s, "Láser Figurante")[0]["codart"] == MAQUINA


def test_en_enero_el_tic_recarga_tambien_el_ejercicio_recien_cerrado() -> None:
    from datetime import date

    assert historico_job.ejercicios_del_tic("2027", date(2027, 1, 15)) == ["2027", "2026"]
    assert historico_job.ejercicios_del_tic("2027", date(2027, 2, 1)) == ["2027"]
    assert historico_job.ejercicios_del_tic("2014", date(2014, 1, 2)) == ["2014"]


class RedisFalso:
    def __init__(self) -> None:
        self.claves: dict[str, str] = {}

    def set(self, k: str, v: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and k in self.claves:
            return False
        self.claves[k] = v
        return True

    def eval(self, _script: str, _n: int, k: str, ficha: str) -> int:
        if self.claves.get(k) == ficha:
            del self.claves[k]
            return 1
        return 0

    def delete(self, k: str) -> None:
        self.claves.pop(k, None)


def test_encolar_marca_cargando_antes_y_el_cerrojo_solo_lo_suelta_su_duenio(
    session_factory, monkeypatch,
) -> None:
    import rq

    redis = RedisFalso()
    vistos: list[tuple[str, tuple]] = []

    class ColaFalsa:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def enqueue(self, fn: Any, *args: Any) -> None:
            with session_factory() as s2:
                vistos.append((s2.get(HistoricoEjercicio, "2017").estado, args))

    monkeypatch.setattr(historico_job, "_redis", lambda: redis)
    monkeypatch.setattr(rq, "Queue", ColaFalsa)
    with session_factory() as s:
        assert historico_job.encolar_carga(s, ["2017"]) == "encolada"
        assert historico_job.encolar_carga(s, ["2018"]) == "ocupada"
    (estado, (ejercicios, ficha)), = vistos
    assert (estado, ejercicios) == ("cargando", ["2017"])
    # Una carga que se pasó de plazo no suelta el cerrojo de otra.
    redis.claves[historico_job.CARGA_KEY] = "de-otra-carga"
    historico_job._soltar_carga(ficha)
    assert redis.claves[historico_job.CARGA_KEY] == "de-otra-carga"
    historico_job._soltar_carga("de-otra-carga")
    assert historico_job.CARGA_KEY not in redis.claves


def test_el_tic_con_una_carga_en_marcha_se_reintenta(monkeypatch) -> None:
    import rq

    redis = RedisFalso()
    redis.claves[historico_job.CARGA_KEY] = "carga-de-la-pantalla"
    reintentos: list[tuple] = []

    class ColaFalsa:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def enqueue_in(self, espera: Any, fn: Any, *args: Any) -> None:
            reintentos.append((espera, fn, args))

    monkeypatch.setattr(historico_job, "_redis", lambda: redis)
    monkeypatch.setattr(rq, "Queue", ColaFalsa)
    historico_job._tic()
    assert reintentos == [(historico_job.REINTENTO_TIC, historico_job._tic_reintento, (1,))]


def test_la_hora_de_la_carga_va_en_utc(cargado) -> None:
    with cargado() as s:
        e = historico.estado_ejercicios(s, "2026")[0]
    assert e["cargado_at"].endswith("Z")
