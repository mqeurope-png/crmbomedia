"""Clase y gama de las familias del histórico, y las reglas que las proponen
(30/10/2026). Familias, artículos y clientes: FIGURANTES (los de
`test_erp_historico`)."""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.erp import historico
from app.erp import historico_clasificacion as cl
from app.erp.models import HistoricoFamilia, HistoricoGama, HistoricoReglaFamilia
from tests._test_helpers import auth_headers
from tests.test_erp_historico import (  # noqa: F401 — fixtures
    CLI_ROTULOS,
    CLI_TALLER,
    FactusolFalso,
    _art,
    _fac,
    _lfa,
    cargado,
    datos_figurantes,
    http,
    session_factory,
)

POR = "oficina@example.com"


def _gamas(s: Session) -> None:
    for i, (c, n) in enumerate([("uv-led", "UV LED"), ("laser", "Láser"), ("otros", "Otros")]):
        s.add(HistoricoGama(codigo=c, nombre=n, orden=i))
    s.commit()


def _regla(s: Session, patron: str, clase: str | None, gama: str | None,
           campo: str = "codigo", orden: int = 100) -> None:
    s.add(HistoricoReglaFamilia(campo=campo, patron=patron, clase=clase, gama=gama,
                                orden=orden))
    s.commit()


def _datos_con_add_on() -> dict[str, Any]:
    """Los figurantes más una familia de add-ons (ACC) vendida en 2026."""
    datos = datos_figurantes()
    d = datos["2026"]
    d["F_FAM"].append({"CODFAM": "ACC", "DESFAM": "Add-ons Figurante"})
    d["F_ART"].append(_art("4001", "Mesa de panal de ejemplo", "ACC"))
    d["F_FAC"].append(_fac(260003, CLI_TALLER, "Talleres Figurante S.L.", "2026-09-01T00:00:00"))
    d["F_LFA"].append(_lfa(260003, 1, "4001", "Mesa de panal de ejemplo", 1, 300))
    return datos


@pytest.fixture()
def clasificado(session_factory):  # noqa: F811
    with session_factory() as s:
        _gamas(s)
        historico.cargar(s, FactusolFalso(_datos_con_add_on()), ["2026", "2017"],
                         en_curso="2026")
    return session_factory


def test_recambio_o_accesorio_y_su_gama_se_conservan_al_recargar(clasificado) -> None:
    with clasificado() as s:
        cl.clasificar(s, "PLO", {"clase": "recambio", "gama": "laser"}, por=POR)
        cl.clasificar(s, "ACC", {"clase": "accesorio", "gama": "uv-led"}, por=POR)
        # Recargar el ejercicio y las familias no toca la clasificación.
        historico.cargar(s, FactusolFalso(_datos_con_add_on()), ["2026"], en_curso="2026")
        assert (s.get(HistoricoFamilia, "PLO").clase, s.get(HistoricoFamilia, "PLO").gama) == (
            "recambio", "laser")
        assert (s.get(HistoricoFamilia, "ACC").clase, s.get(HistoricoFamilia, "ACC").gama) == (
            "accesorio", "uv-led")


def test_la_gama_se_guarda_la_lista_se_amplia_y_se_conserva(clasificado) -> None:
    with clasificado() as s:
        nueva = cl.crear_gama(s, "Corte por agua")
        assert nueva == {"codigo": "corte-por-agua", "nombre": "Corte por agua"}
        with pytest.raises(ValueError):
            cl.crear_gama(s, "corte por agua")
        cl.clasificar(s, "TIN", {"gama": "corte-por-agua"}, por=POR)
        with pytest.raises(ValueError):
            cl.clasificar(s, "TIN", {"gama": "no-existe"}, por=POR)
        historico.cargar_familias(s, FactusolFalso(_datos_con_add_on()), "2026")
        assert s.get(HistoricoFamilia, "TIN").gama == "corte-por-agua"
        assert [g["nombre"] for g in cl.gamas(s)][-1] == "Corte por agua"


def test_compradores_filtra_por_clase_y_por_gama(clasificado) -> None:
    with clasificado() as s:
        cl.clasificar(s, "LAS", {"clase": "maquina", "gama": "uv-led"}, por=POR)
        cl.clasificar(s, "ACC", {"clase": "accesorio", "gama": "uv-led"}, por=POR)
        cl.clasificar(s, "TIN", {"clase": "consumible", "gama": "uv-led"}, por=POR)
        # «Máquina» se marca por artículo (31/10/2026): la familia ya no basta.
        assert historico.compradores(s, clase="maquina", gama="uv-led") == []
        from app.erp import historico_maquinas as hm

        hm.marcar(s, "1712", True, por=POR)
        maquinas = historico.compradores(s, clase="maquina", gama="uv-led")
        toda_la_gama = historico.compradores(s, gama="uv-led")
        accesorios = historico.compradores(s, clase="accesorio")
    # Máquina + UV LED: solo los artículos de la familia de máquinas, nunca el add-on.
    assert {a["codart"] for c in maquinas for a in c["articulos"]} == {"1712"}
    assert {a["codart"] for c in toda_la_gama for a in c["articulos"]} == {"1712", "2207", "4001"}
    assert [c["codcli"] for c in accesorios] == [CLI_TALLER]


def test_la_propuesta_clasifica_las_libres_y_marca_las_que_chocan(clasificado) -> None:
    with clasificado() as s:
        _regla(s, "LAS", "maquina", "laser")
        _regla(s, "ACC", "accesorio", "uv-led")
        _regla(s, "tintas", "consumible", "uv-led", campo="nombre")
        # Bart ya marcó los add-ons como máquina: no se pisa, se marca.
        cl.clasificar(s, "ACC", {"clase": "maquina"}, por=POR)
        p = cl.propuestas(s)
        assert p["LAS"] == {"clase": "maquina", "gama": "laser", "estado": "propuesta"}
        assert p["TIN"]["estado"] == "propuesta"     # por el nombre «Tintas»
        assert p["ACC"]["estado"] == "revisar"
        assert p["PLO"]["estado"] == "sin_propuesta"
        assert s.get(HistoricoFamilia, "ACC").clase == "maquina"


def test_aceptar_todas_no_toca_las_de_revisar_sin_confirmacion(clasificado) -> None:
    with clasificado() as s:
        _regla(s, "LAS", "maquina", "laser")
        _regla(s, "ACC", "accesorio", "uv-led")
        _regla(s, "TIN", "consumible", "uv-led")
        cl.clasificar(s, "ACC", {"clase": "maquina"}, por=POR)
        cl.clasificar(s, "TIN", {"clase": "consumible"}, por=POR)   # sin gama: no choca
        assert cl.aceptar_todas(s, por=POR) == {"aplicadas": 2, "revisar": 1}
        assert (s.get(HistoricoFamilia, "LAS").clase, s.get(HistoricoFamilia, "LAS").gama) == (
            "maquina", "laser")
        assert s.get(HistoricoFamilia, "TIN").gama == "uv-led"
        acc = s.get(HistoricoFamilia, "ACC")
        assert (acc.clase, acc.gama) == ("maquina", None)
        # Con su confirmación, sí.
        cl.aceptar(s, "ACC", por=POR)
        assert (acc.clase, acc.gama) == ("accesorio", "uv-led")


def test_mantener_la_mia_deja_de_marcarla_hasta_que_cambie_la_propuesta(clasificado) -> None:
    with clasificado() as s:
        _regla(s, "ACC", "accesorio", "uv-led")
        cl.clasificar(s, "ACC", {"clase": "maquina", "gama": "uv-led"}, por=POR)
        cl.mantener(s, "ACC", por=POR)
        assert cl.propuestas(s)["ACC"]["estado"] == "descartada"
        assert cl.aceptar_todas(s, por=POR)["revisar"] == 0
        assert s.get(HistoricoFamilia, "ACC").clase == "maquina"
        # Una regla distinta vuelve a marcarla.
        s.query(HistoricoReglaFamilia).delete()
        _regla(s, "ACC", "recambio", "uv-led")
        assert cl.propuestas(s)["ACC"]["estado"] == "revisar"


def test_api_familias_propuestas_y_filtro(http, clasificado) -> None:  # noqa: F811
    h = auth_headers(http, "admin")
    r = http.post("/api/erp/historico/reglas", headers=h,
                  json={"campo": "codigo", "patron": "LAS", "clase": "maquina", "gama": "laser"})
    assert r.status_code == 201
    fams = http.get("/api/erp/historico/familias", headers=h).json()
    assert fams["clases"] == ["maquina", "consumible", "recambio", "accesorio", "otro"]
    assert [g["codigo"] for g in fams["gamas"]] == ["uv-led", "laser", "otros"]
    las = next(f for f in fams["items"] if f["codigo"] == "LAS")
    assert las["propuesta"]["estado"] == "propuesta"
    assert http.post("/api/erp/historico/familias/aceptar-propuestas",
                     headers=h).json()["aplicadas"] == 1
    # PATCH parcial: solo la gama; la clase sigue.
    r = http.patch("/api/erp/historico/familias/LAS", headers=h, json={"gama": "uv-led"})
    assert r.json() == {"codigo": "LAS", "clase": "maquina", "gama": "uv-led"}
    assert http.patch("/api/erp/historico/familias/LAS", headers=h,
                      json={"gama": "nada"}).status_code == 422
    assert http.put("/api/erp/historico/maquinas/1712", json={"es_maquina": True},
                    headers=h).status_code == 200
    compradores = http.get("/api/erp/historico/compradores?clase=maquina&gama=uv-led",
                           headers=h).json()
    assert {c["codcli"] for c in compradores["items"]} >= {CLI_TALLER, CLI_ROTULOS}
    assert http.post("/api/erp/historico/gamas", headers=h,
                     json={"nombre": "Láser"}).status_code == 422
    # Clasificar es de Configuración: pedidos no puede.
    assert http.patch("/api/erp/historico/familias/LAS", headers=auth_headers(http, "pedidos"),
                      json={"clase": "otro"}).status_code == 403


# --- lo que encontró la revisión ---------------------------------------------------------


def test_mantener_la_mia_rellena_lo_que_no_choca_y_editar_otra_cosa_no_reabre(
    clasificado,
) -> None:
    with clasificado() as s:
        _regla(s, "ACC", "recambio", "uv-led")
        cl.clasificar(s, "ACC", {"clase": "maquina"}, por=POR)
        cl.mantener(s, "ACC", por=POR)
        acc = s.get(HistoricoFamilia, "ACC")
        # La clase es la suya; la gama, que no chocaba, se toma.
        assert (acc.clase, acc.gama) == ("maquina", "uv-led")
        assert cl.propuestas(s)["ACC"]["estado"] == "descartada"
        # Cambiar la gama (que no chocaba) no la vuelve a marcar…
        cl.clasificar(s, "ACC", {"gama": "laser"}, por=POR)
        assert cl.propuestas(s)["ACC"]["estado"] == "descartada"
        # …cambiar la clase, sí.
        cl.clasificar(s, "ACC", {"clase": "otro"}, por=POR)
        assert cl.propuestas(s)["ACC"]["estado"] == "revisar"


def test_familias_solo_en_catalogos_antiguos_se_listan_para_clasificar(
    session_factory,  # noqa: F811
) -> None:
    datos = datos_figurantes()
    # PLO solo está en el F_ART de 2017; el F_FAM de 2026 no la tiene.
    datos["2026"]["F_FAM"] = [f for f in datos["2026"]["F_FAM"] if f["CODFAM"] != "PLO"]
    with session_factory() as s:
        _gamas(s)
        historico.cargar(s, FactusolFalso(datos), ["2026", "2017"], en_curso="2026")
        plo = s.get(HistoricoFamilia, "PLO")
        assert plo is not None and plo.descripcion is None
        cl.clasificar(s, "PLO", {"clase": "maquina", "gama": "laser"}, por=POR)
        from app.erp import historico_maquinas as hm

        hm.marcar(s, "0917", True, por=POR)   # «máquina», por artículo
        assert [c["codcli"] for c in historico.compradores(s, clase="maquina", gama="laser")] == [
            CLI_TALLER]


def test_regla_con_orden_cero_y_nombre_sin_acentos(clasificado) -> None:
    with clasificado() as s:
        r = cl.guardar_regla(s, {"campo": "nombre", "patron": "laseres", "clase": "maquina",
                                 "gama": "laser", "orden": 0})
        assert r["orden"] == 0
        # «laseres» casa con «Láseres».
        assert cl.propuestas(s)["LAS"]["clase"] == "maquina"
