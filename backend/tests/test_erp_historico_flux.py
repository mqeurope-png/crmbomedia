"""Familias editables, el catálogo Flux dentro del parque, la misma máquina con
dos códigos y los remates del 1/11/2026. Figurantes: ni clientes ni centros
reales."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from app.erp import historico, parque
from app.erp import historico_clasificacion as cl
from app.erp import historico_maquinas as hm
from app.models.crm import Company
from tests._test_helpers import auth_headers
from tests.test_erp_historico import (  # noqa: F401 — fixtures
    FactusolFalso,
    _art,
    _fac,
    _lfa,
    http,
    session_factory,
)

UNO, DOS, TRES = "4301", "4302", "4303"       # «Cliente Figurante 1/2/3»
WEB_40, WEB_55, FILTRO_MARCA = "34", "4173", "61499"   # familia 071 (la web)
INTERNO_40 = "60995"                                   # familia 021 (FACTUSOL)
PREFILTRO = "61580"                                    # familia 251
DIETAS = "61601"                                       # familia 118
PRIMER = "UV100"                                       # familia UV


def _datos() -> dict[str, Any]:
    cat = [
        _art(WEB_40, "Beambox - Cortadora láser de sobremesa (40W)", "071"),
        _art(WEB_55, "Beambox II (55W)", "071"),
        _art(FILTRO_MARCA, "Filtro de marca figurante 200W", "071"),
        _art(INTERNO_40, "FLUX BEAMBOX 40W 60995", "021"),
        _art(PREFILTRO, "Beam Air Pro pre-filtro", "251"),
        _art(DIETAS, "DIETAS", "118"),
        _art(PRIMER, "PRIMER UV UNIVERSAL 1L", "UV"),
        _art("ADR1-3DVD", "DUPLICADORA ADR FIGURANTE 3 DVD", "021"),
        _art("RET1", "MAQUINA DEVUELTA FIGURANTE", "021"),
    ]
    return {"2026": {
        "F_FAC": [_fac(260201, UNO, "Cliente Figurante 1", "2026-02-10T00:00:00"),
                  _fac(260202, DOS, "Cliente Figurante 2", "2026-03-10T00:00:00"),
                  _fac(260203, TRES, "Cliente Figurante 3", "2026-04-10T00:00:00"),
                  _fac(260204, UNO, "Cliente Figurante 1", "2026-05-10T00:00:00")],
        "F_LFA": [_lfa(260201, 1, WEB_55, "BEAMBOX II", 1, 3500),
                  _lfa(260201, 2, PREFILTRO, "PREFILTRO", 3, 30),
                  _lfa(260202, 1, WEB_55, "BEAMBOX II", 1, 3500),
                  _lfa(260202, 2, PRIMER, "PRIMER", 2, 25),
                  _lfa(260203, 1, WEB_40, "BEAMBOX", 1, 2500),
                  _lfa(260203, 2, FILTRO_MARCA, "FILTRO", 1, 90),
                  _lfa(260203, 3, DIETAS, "DIETAS", 1, 50),
                  # El mismo cliente, la misma máquina con el código interno.
                  _lfa(260204, 1, INTERNO_40, "FLUX BEAMBOX 40W", 1, 2500),
                  # Escrito en minúsculas en la línea (el catálogo, en mayúsculas).
                  _lfa(260204, 2, "ADR1-3dvd", "duplicadora", 1, 900),
                  # Solo devoluciones: todas las líneas en negativo.
                  _lfa(260204, 3, "RET1", "DEVOLUCION", 1, -2500),
                  # Vendido con un código que no está en ningún catálogo.
                  _lfa(260204, 4, "BORRADO9", "ARTICULO BORRADO FIGURANTE", 2, 10)],
        "F_ALB": [], "F_LAL": [], "F_ART": cat,
        "F_FAM": [{"CODFAM": "071", "DESFAM": "Fluxlasers"},
                  {"CODFAM": "021", "DESFAM": "FLUX"},
                  {"CODFAM": "251", "DESFAM": "Articulos Flux No en la web"},
                  {"CODFAM": "118", "DESFAM": "GENERAL"},
                  {"CODFAM": "UV", "DESFAM": "UV"}],
    }}


def _preparar(session_factory) -> dict[str, str]:  # noqa: F811
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(_datos()), ["2026"], en_curso="2026")
        if not any(g["codigo"] == "laser" for g in cl.gamas(s)):
            assert cl.crear_gama(s, "Láser")["codigo"] == "laser"
        cl.clasificar(s, "021", {"clase": "maquina", "gama": "laser"}, por="bart")
        cl.clasificar(s, "UV", {"clase": "otro"}, por="bart")
        for p in hm.PISTAS_INICIALES + hm.PISTAS_NO_0159 + hm.PISTAS_NO_0162:
            hm.guardar_regla(s, p)
        for p in dict.fromkeys(hm.PISTAS_MAQUINA + hm.PISTAS_MAQUINA_0160
                               + hm.PISTAS_MAQUINA_0161):
            hm.guardar_regla(s, p, hm.PISTA_SI)
        ids = {}
        for k, c in (("uno", UNO), ("dos", DOS), ("tres", TRES)):
            co = Company(name=f"Cliente Figurante {k}", factusol_company_id=c)
            s.add(co)
            s.flush()
            ids[k] = co.id
        s.commit()
        return ids


def _item(s, codart: str) -> dict[str, Any] | None:
    return next((i for i in hm.articulos(s)["items"] if i["codart"] == codart), None)


# --- parte 1: la clase y la gama se cambian desde la tabla ------------------------------


def test_clase_y_gama_desde_la_tabla_sobreviven_y_no_tocan_las_demas(http, session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    antes = {f["codigo"]: (f["clase"], f["gama"]) for f in http.get(
        "/api/erp/historico/familias", headers=auth_headers(http, "admin")).json()["items"]}
    r = http.patch("/api/erp/historico/familias/071", json={"clase": "maquina", "gama": "laser"},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200
    despues = {f["codigo"]: (f["clase"], f["gama"]) for f in http.get(
        "/api/erp/historico/familias", headers=auth_headers(http, "admin")).json()["items"]}
    assert despues["071"] == ("maquina", "laser")
    assert {k: v for k, v in despues.items() if k != "071"} == {
        k: v for k, v in antes.items() if k != "071"}
    # Sin «Configuración ERP», no.
    assert http.patch("/api/erp/historico/familias/071", json={"clase": "otro"},
                      headers=auth_headers(http, "pedidos")).status_code == 403


# --- parte 2: 071, 251 ----------------------------------------------------------------


def test_071_maquina_trae_sus_articulos_y_marcar_uno_lo_mete_en_el_parque(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        assert _item(s, WEB_55) is None              # sin clase: no aporta artículos
        cl.clasificar(s, "071", {"clase": "maquina", "gama": "laser"}, por="bart")
        vistos = {i["codart"] for i in hm.articulos(s)["items"] if i["familia"] == "071"}
        assert vistos == {WEB_40, WEB_55, FILTRO_MARCA}
        hm.marcar(s, WEB_55, True, por="bart")
        for k in ("uno", "dos"):
            maquinas = parque.parque(s, s.get(Company, ids[k]))["maquinas"]
            assert [(m["codart"], m["gama"]) for m in maquinas] == [(WEB_55, "laser")]
        assert parque.parque(s, s.get(Company, ids["tres"]))["maquinas"] == []


def test_251_como_consumible_de_laser_cuenta_sus_filtros(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        assert historico.compradores(s, clase="consumible", gama="laser") == []
        cl.clasificar(s, "251", {"clase": "consumible", "gama": "laser"}, por="bart")
        compran = historico.compradores(s, clase="consumible", gama="laser")
        assert {c["codcli"] for c in compran} == {UNO}


# --- parte 4: la misma máquina con dos códigos ----------------------------------------


def test_la_misma_maquina_por_los_dos_codigos_sale_avisada(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        cl.clasificar(s, "071", {"clase": "maquina", "gama": "laser"}, por="bart")
        hm.marcar(s, WEB_40, True, por="bart")
        hm.marcar(s, INTERNO_40, True, por="bart")
        hm.marcar(s, WEB_55, True, por="bart")
        tres = parque.parque(s, s.get(Company, ids["tres"]))["maquinas"]
        assert sorted(m["codart"] for m in tres) == [WEB_40]
        uno = parque.parque(s, s.get(Company, ids["uno"]))["maquinas"]
        # El cliente 1 tiene la 55W y la 40W interna: modelos distintos, sin aviso.
        assert all(m["mismo_modelo"] == [] for m in uno)
        # El 3 compró la 40W por la web; una factura con el código interno, también.
        s.execute(historico.HistoricoDocumento.__table__.update().where(
            historico.HistoricoDocumento.codigo == 260204).values(codcli=TRES))
        s.commit()
        tres = parque.parque(s, s.get(Company, ids["tres"]))["maquinas"]
        por = {m["codart"]: m for m in tres}
        assert set(por) == {WEB_40, INTERNO_40}                  # no se juntan solas
        assert [o["codart"] for o in por[WEB_40]["mismo_modelo"]] == [INTERNO_40]
        assert [o["codart"] for o in por[INTERNO_40]["mismo_modelo"]] == [WEB_40]


def test_el_mismo_modelo_por_el_nombre() -> None:
    assert parque._mismo_modelo("FLUX HEXA 60W", "Flux Hexa - Cortadora láser CO2 (60W)")
    assert parque._mismo_modelo("FLUX BEAMO 30W", "Beamo - Cortadora láser portátil (30W)")
    assert not parque._mismo_modelo("Beambox II (55W)", "FLUX BEAMBOX 40W 60995")
    assert not parque._mismo_modelo("IMPRESORA FIGURANTE", "IMPRESORA FIGURANTE")  # sin potencia


# --- parte 5: remates -----------------------------------------------------------------


def test_un_codigo_en_minusculas_en_la_linea_tiene_su_familia(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        i = _item(s, "ADR1-3dvd") or _item(s, "ADR1-3DVD")
        assert i is not None and i["familia"] == "021" and i["gama"] == "laser"


def test_el_precio_maximo_negativo_cuenta_por_su_valor_absoluto(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        i = _item(s, "RET1")
        assert i["precio_max"] == 2500
        assert i["propuesta"] == hm.PROPUESTA_DUDOSA and i["por_precio"] is True


def test_lo_vendido_sin_catalogo_se_cuenta(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        sc = hm.articulos(s)["sin_catalogo"]
        assert (sc["articulos"], sc["lineas"]) == (1, 1)
        assert sc["items"][0]["codart"] == "BORRADO9"


def _propuesta(desc: str) -> str | None:
    def pistas(lista):
        return [(p, p in hm.PALABRA_ENTERA_0161) for p in lista]
    si = hm._compilar(pistas(hm.PISTAS_MAQUINA + hm.PISTAS_MAQUINA_0160 + hm.PISTAS_MAQUINA_0161))
    no = hm._compilar(pistas(hm.PISTAS_INICIALES + hm.PISTAS_NO_0159 + hm.PISTAS_NO_0162))
    return hm.proponer(desc, si, no, precio_max=600, umbral=2000)["propuesta"]


def test_modulos_y_rotatorios_no_se_proponen_como_maquina() -> None:
    for desc in ("NO USAR ROTATORIO BEAMBOX/PRO", "FLUX Ador 2 W Infrared Laser Module",
                 "Ador Camera Module Set", "MÓDULO DE IMPRESIÓN FLUX ADOR"):
        assert _propuesta(desc) not in (hm.PROPUESTA_SI,), desc
    assert _propuesta("FLUX ADOR 10W") == hm.PROPUESTA_SI


def test_la_migracion_0162_siembra_las_mismas_negativas() -> None:
    ruta = next(Path(__file__).resolve().parents[1].glob("alembic/versions/20261101_0162_*.py"))
    spec = importlib.util.spec_from_file_location("m0162", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert modulo.NEGATIVAS == hm.PISTAS_NO_0162


def test_las_series_toman_el_nombre_de_la_empresa_emisora(session_factory) -> None:  # noqa: F811
    import json

    from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID, ErpSettings

    _preparar(session_factory)
    with session_factory() as s:
        cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID) or ErpSettings(
            id=ERP_SETTINGS_SINGLETON_ID)
        cfg.factusol_series_json = json.dumps(
            {"companies": {"5": {"nombre": "Empresa Figurante S.L."}}})
        s.add(cfg)
        s.commit()
        serie5 = next(i for i in hm.series(s)["items"] if i["serie"] == "5")
        assert serie5["nombre"] == "Empresa Figurante S.L."
