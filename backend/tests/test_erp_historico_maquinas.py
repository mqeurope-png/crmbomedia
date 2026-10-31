"""«Máquina» por artículo, la propuesta por texto, las series que cuentan y
el aviso de «¿misma venta?» entre años (31/10/2026). Figurantes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.erp import historico, parque
from app.erp import historico_clasificacion as cl
from app.erp import historico_maquinas as hm
from app.erp.models import HistoricoArticuloClase
from app.models.crm import Company
from tests._test_helpers import auth_headers
from tests.test_erp_historico import (  # noqa: F401 — fixtures
    FactusolFalso,
    _alb,
    _art,
    _fac,
    _lal,
    _lfa,
    http,
    session_factory,
)

CLI = "4201"            # «Láseres Figurante S.L.»
OTRO = "4202"           # «Otro Cliente Figurante»
BEAMBOX = "5001"
LENTE = "5002"
CARTUCHO = "5003"
NUEVO = "5004"


def _datos() -> dict[str, Any]:
    cat = [_art(BEAMBOX, "FLUX BEAMBOX PRO 50W", "FLX"),
           _art(LENTE, "FLUX FOCUSING LENS", "FLX"),
           _art(CARTUCHO, "SPARE PARTS PP100 HOLDER CARTRIDGE", "FLX"),
           _art(NUEVO, "FLUX ARTICULO NUEVO FIGURANTE", "FLX")]
    return {
        "2026": {
            "F_FAC": [_fac(260101, CLI, "Láseres Figurante S.L.", "2026-03-10T00:00:00"),
                      _fac(260102, OTRO, "Otro Cliente Figurante", "2026-04-10T00:00:00"),
                      # Serie 3: la que no cuenta como venta.
                      _fac(260103, CLI, "Láseres Figurante S.L.", "2026-05-10T00:00:00", "3")],
            "F_LFA": [_lfa(260101, 1, BEAMBOX, "BEAMBOX", 1, 4000),
                      _lfa(260101, 2, LENTE, "LENTE", 2, 40),
                      _lfa(260101, 3, CARTUCHO, "CARTUCHO", 1, 20),
                      _lfa(260102, 1, BEAMBOX, "BEAMBOX", 1, 4000),
                      _lfa(260103, 1, BEAMBOX, "BEAMBOX", 1, 4000, "3")],
            "F_ALB": [], "F_LAL": [],
            "F_ART": cat,
            "F_FAM": [{"CODFAM": "FLX", "DESFAM": "Cortadoras figurante"}],
        },
    }


def _preparar(session_factory, datos=None) -> dict[str, str]:  # noqa: F811
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(datos or _datos()), ["2026"], en_curso="2026")
        cl.clasificar(s, "FLX", {"clase": "maquina"}, por="x")
        for p in hm.PISTAS_INICIALES + hm.PISTAS_NO_0159:
            hm.guardar_regla(s, p)
        for p in hm.PISTAS_MAQUINA + hm.PISTAS_MAQUINA_0160:
            hm.guardar_regla(s, p, hm.PISTA_SI)
        a = Company(name="Láseres Figurante", factusol_company_id=CLI)
        b = Company(name="Otro Cliente Figurante", factusol_company_id=OTRO)
        s.add_all([a, b])
        s.commit()
        return {"a": a.id, "b": b.id}


def _codarts(s, company_id: str) -> list[str]:
    return sorted(m["codart"] for m in parque.parque(s, s.get(Company, company_id))["maquinas"])


def test_sin_marcar_no_entra_y_marcarlo_lo_mete_en_todos_los_clientes(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        # La familia es de máquinas, pero nada está marcado: parque vacío.
        assert _codarts(s, ids["a"]) == [] and _codarts(s, ids["b"]) == []
        hm.marcar(s, BEAMBOX, True, por="x")
        assert BEAMBOX in _codarts(s, ids["a"]) and _codarts(s, ids["b"]) == [BEAMBOX]


def test_un_articulo_nuevo_de_una_familia_de_maquinas_no_entra_solo(session_factory) -> None:  # noqa: F811
    d = _datos()
    d["2026"]["F_LFA"].append(_lfa(260101, 4, NUEVO, "NUEVO", 1, 999))
    ids = _preparar(session_factory, d)
    with session_factory() as s:
        hm.marcar(s, BEAMBOX, True, por="x")
        assert NUEVO not in _codarts(s, ids["a"])
        # Está en la lista para mirarlo, sin revisar.
        fila = next(i for i in hm.articulos(s)["items"] if i["codart"] == NUEVO)
        assert fila["estado"] == hm.SIN_REVISAR


def test_la_propuesta_por_texto(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        items = {i["codart"]: i for i in hm.articulos(s)["items"]}
    assert (items[BEAMBOX]["propuesta"], items[BEAMBOX]["pista_si"]) == ("maquina", "BEAMBOX")
    assert (items[LENTE]["propuesta"], items[LENTE]["pista_no"]) == ("no_maquina", "LENS")
    assert items[CARTUCHO]["propuesta"] == "no_maquina"
    # Por trozo (1/11/2026): «ARTIS» casa dentro de «ARTISJET».
    assert hm.pista("ARTISJET A3000U", hm._compilar(["ARTIS"])) == "ARTIS"


def test_aceptar_todas_no_pisa_lo_marcado_a_mano(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        # Bart desmarca la Beambox (por lo que sea) y marca la lente.
        hm.marcar(s, BEAMBOX, False, por="bart")
        hm.marcar(s, LENTE, True, por="bart")
        hechas = hm.aceptar_todas(s, por="x")
        assert hechas == {"maquina": 0, "no_maquina": 1}     # solo el cartucho
        assert s.get(HistoricoArticuloClase, CARTUCHO).por == "x (aceptar todas)"
        clases = {p.codart: p.clase for p in s.scalars(select(HistoricoArticuloClase))}
        assert clases[BEAMBOX] == "no_maquina" and clases[LENTE] == "maquina"
        assert clases[CARTUCHO] == "no_maquina"
        r = hm.articulos(s)
        assert (r["marcadas"], r["total"]) == (1, 3)
        assert _codarts(s, ids["a"]) == [LENTE]


def test_una_serie_que_no_cuenta_sale_del_parque_y_de_compradores(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        hm.marcar(s, BEAMBOX, True, por="x")
        antes = [m for m in parque.parque(s, s.get(Company, ids["a"]))["maquinas"]]
        assert sorted(m["documento"]["serie"] for m in antes) == ["3", "5"]
        assert {c["codcli"] for c in historico.compradores(s, codart=BEAMBOX)} == {CLI, OTRO}
        hm.marcar_serie(s, "3", False, por="x")
        despues = parque.parque(s, s.get(Company, ids["a"]))["maquinas"]
        assert [m["documento"]["serie"] for m in despues] == ["5"]
        compra = next(c for c in historico.compradores(s, codart=BEAMBOX) if c["codcli"] == CLI)
        assert compra["compras"] == 1 and compra["ultima_compra"]["documento"].startswith("5-")
        # Y lo que se queda fuera, contado.
        series = hm.series(s)
        assert series["fuera"] == {"documentos": 1, "lineas": 1}
        assert next(i for i in series["items"] if i["serie"] == "3")["cuenta"] is False


def test_el_aviso_de_misma_venta_cruza_el_cambio_de_anio(session_factory) -> None:  # noqa: F811
    # Factura de diciembre (con su albarán emparejado) y otro albarán suelto
    # de enero del mismo artículo, 18 días después.
    d = {
        "2022": {
            "F_FAC": [_fac(123021, CLI, "Láseres Figurante S.L.", "2022-12-15T00:00:00", "1")],
            "F_LFA": [{**_lfa(123021, 1, BEAMBOX, "BEAMBOX", 1, 4000, "1"),
                       "DOCLFA": "A", "DTPLFA": "1", "DCOLFA": 516}],
            "F_ALB": [{**_alb(516, CLI, "Láseres Figurante S.L.", "2022-12-10T00:00:00", "1"),
                       "ESTALB": "1"}],
            "F_LAL": [_lal(516, 1, BEAMBOX, "BEAMBOX", 1, "1")],
            "F_ART": _datos()["2026"]["F_ART"],
        },
        "2023": {
            "F_ALB": [{**_alb(230001, CLI, "Láseres Figurante S.L.", "2023-01-02T00:00:00", "3"),
                       "ESTALB": "0"}],
            "F_LAL": [_lal(230001, 1, BEAMBOX, "BEAMBOX", 1, "3")],
            "F_ART": _datos()["2026"]["F_ART"],
        },
        "2026": {"F_FAM": [{"CODFAM": "FLX", "DESFAM": "Cortadoras figurante"}]},
    }
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026", "2023", "2022"], en_curso="2026")
        cl.clasificar(s, "FLX", {"clase": "maquina"}, por="x")
        hm.marcar(s, BEAMBOX, True, por="x")
        empresa = Company(name="Láseres Figurante", factusol_company_id=CLI)
        s.add(empresa)
        s.commit()
        maquinas = parque.parque(s, empresa)["maquinas"]
    suelto = next(m for m in maquinas if m["origen"] == "albaran")
    assert suelto["posible_misma_venta"]["numero"] == "1-123021"
    fac = next(m for m in maquinas if m["origen"] == "factura")
    assert [a["numero"] for a in fac["albaranes"]] == ["1-000516"]


def test_api_maquinas_y_series_las_cambia_quien_configura(http, session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    r = http.get("/api/erp/historico/maquinas", headers=auth_headers(http, "user"))
    assert r.status_code == 200 and r.json()["total"] == 3
    assert http.put(f"/api/erp/historico/maquinas/{BEAMBOX}", json={"es_maquina": True},
                    headers=auth_headers(http, "pedidos")).status_code == 403
    r = http.put(f"/api/erp/historico/maquinas/{BEAMBOX}", json={"es_maquina": True},
                 headers=auth_headers(http, "admin"))
    assert r.status_code == 200 and r.json()["marcadas"] == 1
    r = http.post("/api/erp/historico/maquinas/aceptar-propuestas",
                  headers=auth_headers(http, "admin"))
    assert r.json()["aplicadas"] == {"maquina": 0, "no_maquina": 2}
    r = http.put("/api/erp/historico/series/3", json={"cuenta": False},
                 headers=auth_headers(http, "admin"))
    assert r.status_code == 200 and r.json()["fuera"]["documentos"] == 1
    r = http.post("/api/erp/historico/reglas-articulo", json={"patron": "lens"},
                  headers=auth_headers(http, "admin"))
    assert r.status_code == 422      # ya está («LENS»)


def test_la_migracion_siembra_las_mismas_pistas() -> None:
    ruta = next(Path(__file__).resolve().parents[1].glob(
        "alembic/versions/20261031_0158_*.py"))
    spec = importlib.util.spec_from_file_location("m0158", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert modulo.PISTAS == hm.PISTAS_INICIALES


# --- lo que encontró la revisión ---------------------------------------------------------


def test_no_es_maquina_no_borra_la_clase_de_la_familia(session_factory) -> None:  # noqa: F811
    assert hm.clase_efectiva_py("no_maquina", "accesorio") == "accesorio"
    assert hm.clase_efectiva_py("no_maquina", "maquina") is None
    assert hm.clase_efectiva_py(None, "maquina") is None
    assert hm.clase_efectiva_py("maquina", "accesorio") == "maquina"


def test_el_aviso_no_apunta_a_la_factura_del_propio_albaran(session_factory) -> None:  # noqa: F811
    # Albarán de 2 uds; su factura (que lo apunta) solo factura 1: queda 1 suelta.
    d = {
        "2026": {
            "F_FAC": [_fac(260201, CLI, "Láseres Figurante S.L.", "2026-03-10T00:00:00")],
            "F_LFA": [{**_lfa(260201, 1, BEAMBOX, "BEAMBOX", 1, 4000),
                       "DOCLFA": "A", "DTPLFA": "5", "DCOLFA": 26020}],
            "F_ALB": [{**_alb(26020, CLI, "Láseres Figurante S.L.", "2026-03-01T00:00:00"),
                       "ESTALB": "0"}],
            "F_LAL": [_lal(26020, 1, BEAMBOX, "BEAMBOX", 2)],
            "F_ART": _datos()["2026"]["F_ART"],
            "F_FAM": [{"CODFAM": "FLX", "DESFAM": "Cortadoras figurante"}],
        },
    }
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026"], en_curso="2026")
        cl.clasificar(s, "FLX", {"clase": "maquina"}, por="x")
        hm.marcar(s, BEAMBOX, True, por="x")
        empresa = Company(name="Láseres Figurante", factusol_company_id=CLI)
        s.add(empresa)
        s.commit()
        suelto = next(m for m in parque.parque(s, empresa)["maquinas"] if m["origen"] == "albaran")
    assert suelto["cantidad"] == 1 and suelto["posible_misma_venta"] is None



# --- la propuesta exige evidencia positiva (31/10/2026) ------------------------------------


def _propuesta(desc: str, precio: float | None = None, umbral: float = 2000) -> str | None:
    si = hm._compilar(list(hm.PISTAS_MAQUINA + hm.PISTAS_MAQUINA_0160))
    no = hm._compilar(list(hm.PISTAS_INICIALES + hm.PISTAS_NO_0159))
    return hm.proponer(desc, si, no, precio_max=precio, umbral=umbral)["propuesta"]


def test_sin_evidencia_positiva_no_se_propone_como_maquina() -> None:
    # Los que la primera propuesta daba por máquina (lista de Bart).
    for desc in ("Raspberry Pi 3", "X Axis Stepper Motor", "Mainboard Beambox",
                 "12V10A POWER SUPPLY", "FLUX FAN", "DUCT CLAMP", "FLOW SENSOR",
                 "LASER CUTTER LUBRICANT", "CUCHILLA SPEEDSTAR", "LÁMINA SILICONADA A4",
                 "PLATO 18X38CM PANTALON", "MESA TRABAJO 6090D", "Werksgeprüfter Laser.",
                 "FLUX HX AF Sensor (3D touch)"):
        assert _propuesta(desc) != "maquina", desc
    # Sin ninguna pista: sin propuesta, no «máquina».
    assert _propuesta("Werksgeprüfter Laser.") is None
    assert _propuesta("Raspberry Pi 3") == "no_maquina"


def test_las_maquinas_por_su_nombre_y_la_duda_no_se_decide_sola() -> None:
    for desc in ("FLUX BEAMBOX PRO 50W", "IMPRESORA UV LED FIGURANTE", "FLUX HEXA 60W",
                 "EPSON PP-100III", "CELOPACK 300"):
        assert _propuesta(desc) == "maquina", desc
    # Positiva y negativa a la vez: sin propuesta, lo decide Bart (antes
    # salía «no · MANUAL», y es una máquina).
    assert _propuesta("SPEEDSTAR 2 ENCELOFANADORA MANUAL") == "dudoso"
    assert _propuesta("FLUX FOCUSING LENS") == "no_maquina"


def test_aceptar_todas_no_toca_lo_dudoso_ni_lo_que_no_tiene_propuesta(session_factory) -> None:  # noqa: F811
    d = _datos()
    d["2026"]["F_ART"] += [_art("5005", "SPEEDSTAR 2 ENCELOFANADORA MANUAL", "FLX"),
                           _art("5006", "Werksgeprüfter Laser.", "FLX")]
    d["2026"]["F_LFA"] += [_lfa(260101, 5, "5005", "X", 1, 1), _lfa(260101, 6, "5006", "X", 1, 1)]
    _preparar(session_factory, d)
    with session_factory() as s:
        hm.aceptar_todas(s, por="x")
        clases = {p.codart: p.clase for p in s.scalars(select(HistoricoArticuloClase))}
    assert clases[BEAMBOX] == "maquina" and clases[LENTE] == "no_maquina"
    assert "5005" not in clases and "5006" not in clases     # siguen sin revisar


def test_la_lista_es_de_las_familias_de_maquina_de_hoy(session_factory) -> None:  # noqa: F811
    # Un artículo que en 2017 estaba en la familia de máquinas y hoy en una de
    # accesorios no sale (salvo que esté marcado como máquina).
    d = _datos()
    d["2017"] = {"F_FAC": [], "F_LFA": [], "F_ALB": [], "F_LAL": [],
                 "F_ART": [_art("5007", "SOPORTE FIGURANTE", "FLX")]}
    d["2026"]["F_ART"].append(_art("5007", "SOPORTE FIGURANTE", "ACX"))
    d["2026"]["F_FAM"].append({"CODFAM": "ACX", "DESFAM": "Accesorios figurante"})
    d["2026"]["F_LFA"].append(_lfa(260101, 7, "5007", "SOPORTE", 1, 10))
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026", "2017"], en_curso="2026")
        cl.clasificar(s, "FLX", {"clase": "maquina"}, por="x")
        cl.clasificar(s, "ACX", {"clase": "accesorio"}, por="x")
        assert "5007" not in {i["codart"] for i in hm.articulos(s)["items"]}
        hm.marcar(s, "5007", True, por="x")
        fila = next(i for i in hm.articulos(s)["items"] if i["codart"] == "5007")
        assert (fila["familia"], fila["familia_clase"]) == ("ACX", "accesorio")


def test_la_migracion_0159_siembra_las_positivas() -> None:
    ruta = next(Path(__file__).resolve().parents[1].glob(
        "alembic/versions/20261031_0159_*.py"))
    spec = importlib.util.spec_from_file_location("m0159", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert modulo.POSITIVAS == hm.PISTAS_MAQUINA and modulo.NEGATIVAS == hm.PISTAS_NO_0159



# --- pistas por trozo y el precio como segunda señal (1/11/2026) ---------------------------


def test_las_pistas_casan_por_trozo() -> None:
    for desc in ("ARTISJET A3000U", "EPSON PP100", "ADR1-10DVD", "ADR1-14DVD TORRE DUPLICACIÓN"):
        assert _propuesta(desc) == "maquina", desc
    for desc in ("FLUX FOCUSING LENS", "FLUX HX AF Sensor (3D touch)",
                 "SPARE PARTS PP100 HOLDER CARTRIDGE"):
        assert _propuesta(desc) == "no_maquina", desc


def test_el_precio_suma_y_no_veta() -> None:
    # El texto manda: una Ador barata sigue siendo máquina.
    assert _propuesta("FLUX ADOR 10W", precio=900) == "maquina"
    # Una lente cara sigue siendo «no».
    assert _propuesta("FLUX FOCUSING LENS", precio=5000) == "no_maquina"
    # Sin pista: por encima del umbral, dudosa; por debajo, nada.
    assert _propuesta("A5000 UV", precio=12000) == "dudoso"
    assert _propuesta("A5000 UV", precio=1500) is None
    # El umbral es un dato: bajándolo, la misma pasa a dudosa.
    assert _propuesta("A5000 UV", precio=1500, umbral=1000) == "dudoso"


def _datos_precio() -> dict[str, Any]:
    d = _datos()
    d["2026"]["F_ART"].append(_art("5010", "A5000 UV FIGURANTE", "FLX"))
    d["2026"]["F_LFA"] += [_lfa(260101, 10, "5010", "garantía", 1, 0),
                           _lfa(260102, 10, "5010", "A5000", 1, 12000)]
    return d


def test_el_precio_es_el_maximo_y_el_umbral_es_un_dato(session_factory) -> None:  # noqa: F811
    _preparar(session_factory, _datos_precio())
    with session_factory() as s:
        fila = next(i for i in hm.articulos(s)["items"] if i["codart"] == "5010")
        assert fila["precio_max"] == 12000
        assert (fila["propuesta"], fila["por_precio"]) == ("dudoso", True)
        assert fila["estado"] == hm.SIN_REVISAR          # no se marca sola
        hm.guardar_umbral(s, 15000)
        fila = next(i for i in hm.articulos(s)["items"] if i["codart"] == "5010")
        assert fila["propuesta"] is None


def test_lo_revisado_no_lleva_propuesta_y_sale_en_el_filtro(session_factory) -> None:  # noqa: F811
    _preparar(session_factory, _datos_precio())
    with session_factory() as s:
        hm.marcar(s, BEAMBOX, True, por="bart")
        hm.marcar(s, "5010", False, por="bart")          # «No es máquina»
        items = {i["codart"]: i for i in hm.articulos(s)["items"]}
        # Marcada sigue marcada; la descartada no reaparece como propuesta…
        assert items[BEAMBOX]["estado"] == hm.MARCADA
        assert (items["5010"]["estado"], items["5010"]["propuesta"]) == (hm.NO_MAQUINA, None)
        # …pero el filtro de colisiones la enseña (por precio, hoy sería dudosa).
        assert items["5010"]["propondria"] == "dudoso"
        assert hm.articulos(s)["colisiones"] == 1
        # «Aceptar todas» no la toca.
        hm.aceptar_todas(s, por="x")
        assert s.get(HistoricoArticuloClase, "5010").clase == "no_maquina"
        assert s.get(HistoricoArticuloClase, BEAMBOX).clase == "maquina"


def test_dar_por_revisadas_una_familia_y_deshacerlo(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        hm.marcar(s, BEAMBOX, True, por="bart")
        assert hm.dar_por_revisadas(s, "FLX", por="bart") == 2   # lente y cartucho
        hm.marcar(s, LENTE, True, por="bart")                    # luego, a mano
        assert hm.articulos(s)["sin_revisar"] == 0
        assert hm.deshacer_revisadas(s, "FLX") == 1              # solo el cartucho
        clases = {p.codart: p.clase for p in s.scalars(select(HistoricoArticuloClase))}
        assert clases == {BEAMBOX: "maquina", LENTE: "maquina"}


def test_api_umbral_y_familia(http, session_factory) -> None:  # noqa: F811
    _preparar(session_factory, _datos_precio())
    h = auth_headers(http, "admin")
    r = http.put("/api/erp/historico/maquinas-umbral", json={"umbral": 500}, headers=h)
    assert r.status_code == 200 and r.json()["umbral"] == 500
    assert http.post("/api/erp/historico/maquinas-familias/FLX/revisar", json={},
                     headers=h).status_code == 400
    r = http.post("/api/erp/historico/maquinas-familias/FLX/revisar", json={"confirm": True},
                  headers=h)
    assert r.status_code == 200 and r.json()["revisadas"] == 4
    r = http.post("/api/erp/historico/maquinas-familias/FLX/deshacer", json={"confirm": True},
                  headers=h)
    assert r.json()["deshechas"] == 4
    assert http.put("/api/erp/historico/maquinas-umbral", json={"umbral": 1},
                    headers=auth_headers(http, "pedidos")).status_code == 403


def test_la_migracion_0160_siembra_umbral_y_positivas() -> None:
    ruta = next(Path(__file__).resolve().parents[1].glob(
        "alembic/versions/20261101_0160_*.py"))
    spec = importlib.util.spec_from_file_location("m0160", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert modulo.POSITIVAS == hm.PISTAS_MAQUINA_0160
    assert float(modulo.UMBRAL) == hm.UMBRAL_POR_DEFECTO
