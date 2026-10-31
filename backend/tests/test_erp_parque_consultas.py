"""Dormidos y huecos (1/11/2026). Figurantes: ni clientes ni centros reales."""
from __future__ import annotations

from datetime import date
from typing import Any

from app.erp import historico, parque
from app.erp import historico_clasificacion as cl
from app.erp import historico_maquinas as hm
from app.erp import parque_consultas as pc
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

HOY = date(2026, 11, 1)
DORMIDO, AL_DIA, HUECO_UV, RECIENTE, DE_BAJA, SOLO_RECAMBIO = (
    "4701", "4702", "4703", "4704", "4705", "4706")
LASER, FILTRO, PIEZA, UV, TINTA = "L1", "F1", "P1", "U1", "T1"
CAT = [_art(LASER, "FLUX BEAMBOX FIGURANTE 40W", "021"),
       _art(FILTRO, "Beam Air Pro pre-filtro", "251"),
       _art(PIEZA, "SPARE PARTS FIGURANTE", "211"),
       _art(UV, "IMPRESORA UV FIGURANTE A3", "UVM"),
       _art(TINTA, "TINTA UV FIGURANTE", "UV")]
FAM = [{"CODFAM": c, "DESFAM": d} for c, d in (
    ("021", "FLUX"), ("251", "Flux no web"), ("211", "Recambios láser"),
    ("UVM", "Impresoras UV"), ("UV", "Consumible UV"))]


def _anio(ej: int, ventas: list[tuple[int, str, str, str, float]]) -> dict[str, Any]:
    """ventas: (codigo, cliente, fecha, articulo, precio)."""
    return {"F_FAC": [_fac(c, cli, "Cliente Figurante", f"{fecha}T00:00:00")
                      for c, cli, fecha, _a, _p in ventas],
            "F_LFA": [_lfa(c, 1, a, a, 1, p) for c, _cli, _f, a, p in ventas],
            "F_ALB": [], "F_LAL": [], "F_ART": CAT, "F_FAM": FAM}


def _datos() -> dict[str, Any]:
    return {
        "2021": _anio(2021, [(210001, DORMIDO, "2021-02-01", LASER, 4000),
                             (210002, DORMIDO, "2021-06-01", FILTRO, 300),
                             (210003, DE_BAJA, "2021-03-01", LASER, 4000)]),
        "2022": _anio(2022, [(220001, DORMIDO, "2022-01-15", FILTRO, 250),
                             (220002, DORMIDO, "2022-09-15", FILTRO, 200),
                             (220003, HUECO_UV, "2022-05-01", UV, 15000),
                             (220004, SOLO_RECAMBIO, "2022-05-01", LASER, 4000)]),
        "2023": _anio(2023, [(230001, AL_DIA, "2023-03-01", LASER, 4000),
                             (230002, SOLO_RECAMBIO, "2023-08-01", PIEZA, 80)]),
        "2026": _anio(2026, [(260001, AL_DIA, "2026-06-01", FILTRO, 150),
                             (260002, RECIENTE, "2026-10-01", UV, 15000)]),
    }


def _preparar(session_factory, datos: dict[str, Any] | None = None) -> None:  # noqa: F811
    datos = datos or _datos()
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(datos), list(datos), en_curso="2026")
        for g in ("Láser", "UV LED"):
            if not any(x["nombre"] == g for x in cl.gamas(s)):
                cl.crear_gama(s, g)
        for codigo, clase, gama in (("021", "maquina", "laser"), ("UVM", "maquina", "uv-led"),
                                    ("251", "consumible", "laser"),
                                    ("211", "recambio", "laser"),
                                    ("UV", "consumible", "uv-led")):
            cl.clasificar(s, codigo, {"clase": clase, "gama": gama}, por="bart")
        hm.marcar(s, LASER, True, por="bart")
        hm.marcar(s, UV, True, por="bart")
        for cli in (DORMIDO, AL_DIA, HUECO_UV, RECIENTE, DE_BAJA, SOLO_RECAMBIO):
            s.add(Company(name=f"Cliente Figurante {cli}", factusol_company_id=cli))
        s.commit()


def _por(filas: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(f["codcli"], f["gama"]): f for f in filas}


def test_dormidos_y_huecos(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        r = pc.informe(s, hoy=HOY)
        dormidos, huecos = _por(r["dormidos"]), _por(r["huecos"])
        # Compraba filtros y lleva desde 09/2022 sin comprar: dormido.
        d = dormidos[(DORMIDO, "laser")]
        assert d["ultima_compra"] == "2022-09-15" and d["meses_sin_comprar"] == 49
        assert d["compraba_12_meses"] == 450.0           # los dos de 2022 (12 meses antes)
        # Compra filtros Beam Air (251, consumible de láser): ni dormido ni hueco.
        assert (AL_DIA, "laser") not in dormidos and (AL_DIA, "laser") not in huecos
        # Una UV LED y nunca una tinta: hueco.
        assert huecos[(HUECO_UV, "uv-led")]["maquinas"] == 1
        # La UV de hace un mes aún no es hueco.
        assert (RECIENTE, "uv-led") not in huecos
        # Solo recambios, nunca un consumible: hueco (y se dice).
        assert huecos[(SOLO_RECAMBIO, "laser")]["solo_recambios"] is True
        assert set(dormidos) == {(DORMIDO, "laser")}
        assert set(huecos) == {(HUECO_UV, "uv-led"), (SOLO_RECAMBIO, "laser"), (DE_BAJA, "laser")}


def test_una_maquina_de_baja_no_cuenta(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        empresa = next(c for c in s.query(Company) if c.factusol_company_id == DE_BAJA)
        m = parque.parque(s, empresa)["maquinas"][0]
        parque.dar_baja(s, empresa, clave=m["clave"], motivo="desguazada", por="bart")
        r = pc.informe(s, hoy=HOY)
        assert all(f["codcli"] != DE_BAJA for f in (*r["dormidos"], *r["huecos"]))


def test_el_resumen_por_gama_permite_ver_si_una_familia_esta_mal(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        g = {x["gama"]: x for x in pc.informe(s, hoy=HOY)["resumen"]["por_gama"]}
        assert g["laser"]["clientes"] == 4 and g["laser"]["con_consumible"] == 2
        # Si la 251 no fuera consumible, todos los de láser serían huecos.
        cl.clasificar(s, "251", {"clase": "otro"}, por="bart")
        g = {x["gama"]: x for x in pc.informe(s, hoy=HOY)["resumen"]["por_gama"]}
        assert g["laser"]["con_consumible"] == 0 and g["laser"]["huecos"] == 4


def test_el_umbral_se_cambia_sin_tocar_codigo_y_el_csv(http, session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    h = auth_headers(http, "admin")
    r = http.get("/api/erp/historico/parque/dormidos-huecos", headers=auth_headers(http, "user"))
    assert r.status_code == 200 and r.json()["ajustes"]["meses_dormido"] == 12
    assert http.put("/api/erp/historico/parque/dormidos-ajustes", json={"meses_dormido": 60},
                    headers=auth_headers(http, "pedidos")).status_code == 403
    r = http.put("/api/erp/historico/parque/dormidos-ajustes", json={"meses_dormido": 60},
                 headers=h)
    assert r.status_code == 200 and r.json()["dormidos"] == []
    assert http.put("/api/erp/historico/parque/dormidos-ajustes", json={"meses_dormido": 0},
                    headers=h).status_code == 422
    r = http.get("/api/erp/historico/parque/huecos.csv", headers=h)
    assert r.status_code == 200 and "Compra recambios pero no consumibles" in r.text
    assert http.get("/api/erp/historico/parque/otra.csv", headers=h).status_code == 422


# --- lo que encontró la revisión -------------------------------------------------------


def test_una_devolucion_de_filtro_no_es_una_compra(session_factory) -> None:  # noqa: F811
    d = _datos()
    lfa = _lfa(260009, 1, FILTRO, FILTRO, -1, 300)
    d["2026"]["F_FAC"].append(_fac(260009, DORMIDO, "Cliente Figurante", "2026-06-01T00:00:00"))
    d["2026"]["F_LFA"].append(lfa)
    _preparar(session_factory, d)
    with session_factory() as s:
        dormidos = _por(pc.informe(s, hoy=HOY)["dormidos"])
        assert dormidos[(DORMIDO, "laser")]["ultima_compra"] == "2022-09-15"


def test_la_ficha_encuentra_al_cliente_con_ceros_delante() -> None:
    assert {"412", "0412", "00412"} <= set(parque.codigos_cliente("412"))
    assert {"412", "0412", "00412"} <= set(parque.codigos_cliente("00412"))


# --- partir las listas (1/11/2026) -----------------------------------------------------


def test_filtrar_por_antiguedad_y_gasto_y_el_csv_sale_filtrado(http, session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        r = pc.informe(s, hoy=HOY)
        huecos = _por(r["huecos"])
        # Gasto total facturado del cliente; los huecos, del que más ha comprado.
        assert huecos[(HUECO_UV, "uv-led")]["gasto_total"] == 15000.0
        assert [h["codcli"] for h in r["huecos"]][0] == HUECO_UV
        # Máquina de hace más de 24 meses y cliente de más de 5.000 €.
        f = pc.filtrar(r, meses_min=24, gasto_min=5000)
        assert {(h["codcli"], h["gama"]) for h in f["huecos"]} == {(HUECO_UV, "uv-led")}
        assert pc.filtrar(r, gama="laser", meses_max=12)["huecos"] == []
        # El resumen por gama no cambia con el filtro, ni de forma.
        assert f["resumen"] == r["resumen"]
        assert set(r["resumen"]["por_gama"][0]) == {"gama", "gama_nombre", "clientes",
                                                    "con_consumible", "dormidos", "huecos"}
    h = auth_headers(http, "admin")
    todo = http.get("/api/erp/historico/parque/huecos.csv", headers=h).text
    corto = http.get("/api/erp/historico/parque/huecos.csv?gama=uv-led&gasto_min=5000",
                     headers=h).text
    assert len(todo.strip().splitlines()) > len(corto.strip().splitlines()) == 2
    r = http.get("/api/erp/historico/parque/dormidos-huecos?gama=laser", headers=h).json()
    assert all(x["gama"] == "laser" for x in (*r["dormidos"], *r["huecos"]))


def test_filtrar_por_sector_de_la_ficha(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        empresa = next(c for c in s.query(Company) if c.factusol_company_id == HUECO_UV)
        empresa.sector = "Rotulación figurante"
        s.commit()
        r = pc.informe(s, hoy=HOY)
        assert {"sector": "Rotulación figurante", "clientes": 1} in r["resumen"]["sectores"]
        assert [h["codcli"] for h in pc.filtrar(r, sector="Rotulación figurante")["huecos"]] \
            == [HUECO_UV]
        assert HUECO_UV not in {h["codcli"] for h in pc.filtrar(r, sector="-")["huecos"]}


def test_las_maquinas_sin_gama_se_ensenan(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        cl.clasificar(s, "UVM", {"gama": None}, por="bart")
        r = pc.informe(s, hoy=HOY)
        sin = r["resumen"]["sin_gama"]
        assert r["resumen"]["maquinas_sin_gama"] == len(sin) == 2
        assert {m["familia"] for m in sin} == {"UVM"} and {m["codart"] for m in sin} == {UV}


def test_el_sector_cuenta_clientes_no_filas(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        r = pc.informe(s, hoy=HOY)
        filas = {f["codcli"] for f in (*r["dormidos"], *r["huecos"])}
        assert sum(x["clientes"] for x in r["resumen"]["sectores"]) == len(filas)
