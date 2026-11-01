"""Las garantías no son consumible (1/11/2026): el script les da clase propia
«Otro» sin tocar la familia ni lo que decidió Bart, mide cuánto se mueve el
ritmo (el de la ficha) y los dormidos y huecos, recuerda lo que hizo para no
pisar lo que se deshaga a mano, y se deshace. Figurantes."""
from __future__ import annotations

from typing import Any

import pytest

from app.erp import historico
from app.erp import historico_clasificacion as cl
from app.erp import historico_equivalencias as equivalencias
from app.erp import historico_maquinas as hm
from app.erp.models.historico import HistoricoArticuloClase, HistoricoFamilia
from app.models.crm import Company
from tests.test_erp_historico import (  # noqa: F401 — fixtures
    FactusolFalso,
    _art,
    _fac,
    _lfa,
    session_factory,
)

CLI = "4950"
LASER, FILTRO = "LAS100", "FIL200"
GARANTIA, GARANTIA_BART, CAMBIA_FAMILIA = "GAR300", "GAR400", "GAR500"
EQUIVALENTE, REUSADO = "GAR600", "X100"
FAM = [{"CODFAM": "021", "DESFAM": "FLUX"}, {"CODFAM": "251", "DESFAM": "Flux no web"},
       {"CODFAM": "900", "DESFAM": "Accesorios figurantes"}]
CATALOGO = {
    "2024": [_art(LASER, "LASER FIGURANTE 40W", "021"), _art(FILTRO, "Filtro figurante", "251"),
             _art(REUSADO, "Filtro carbón figurante", "251")],
    "2025": [_art(LASER, "LASER FIGURANTE 40W", "021"), _art(FILTRO, "Filtro figurante", "251"),
             # En 2025 su familia era consumible; en 2026 ya no.
             _art(CAMBIA_FAMILIA, "Garantía figurante 2 años", "251")],
    "2026": [_art(LASER, "LASER FIGURANTE 40W", "021"), _art(FILTRO, "Filtro figurante", "251"),
             _art(GARANTIA, "Flux Gar. Las · 1 año de garantía limitada", "251"),
             _art(GARANTIA_BART, "Garantía figurante ampliada", "251"),
             _art(CAMBIA_FAMILIA, "Garantía figurante 2 años", "900"),
             # El mismo código, reutilizado: en 2024 era un filtro.
             _art(REUSADO, "Garantía figurante 1 año", "251"),
             _art(EQUIVALENTE, "Láser figurante 40W · 1 año de garantía", "251")],
}


def _datos() -> dict[str, Any]:
    # La máquina, y con cada máquina que revende, una garantía a 0 €: cada mes.
    ventas = [(260001, "2026-01-05", LASER, 3000)]
    ventas += [(260010 + i, f"2026-{i + 1:02d}-10", GARANTIA, 0) for i in range(9)]
    # Filtros de verdad: cada ~6 meses.
    ventas += [(240001, "2024-06-01", FILTRO, 50), (240002, "2024-12-01", FILTRO, 50),
               (250001, "2025-06-01", FILTRO, 50)]
    por_anio: dict[str, Any] = {
        a: {"F_FAC": [], "F_LFA": [], "F_ALB": [], "F_LAL": [], "F_ART": cat, "F_FAM": FAM}
        for a, cat in CATALOGO.items()}
    for codigo, fecha, art, precio in ventas:
        d = por_anio[fecha[:4]]
        d["F_FAC"].append(_fac(codigo, CLI, "Cliente Figurante", f"{fecha}T00:00:00"))
        d["F_LFA"].append(_lfa(codigo, 1, art, art, 1, precio))
    return por_anio


def _preparar(session_factory) -> None:  # noqa: F811
    datos = _datos()
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(datos), list(datos), en_curso="2026")
        if not any(x["nombre"] == "Láser" for x in cl.gamas(s)):
            cl.crear_gama(s, "Láser")
        cl.clasificar(s, "021", {"clase": "maquina", "gama": "laser"}, por="bart")
        cl.clasificar(s, "251", {"clase": "consumible", "gama": "laser"}, por="bart")
        cl.clasificar(s, "900", {"clase": "accesorio", "gama": "laser"}, por="bart")
        hm.marcar(s, LASER, True, por="bart")
        # Otro código del mismo modelo: cuenta como máquina.
        equivalencias.declarar(s, LASER, EQUIVALENTE, por="bart")
        # Bart ya decidió que esta «garantía» es un recambio: no se toca.
        cl.clasificar_articulo(s, GARANTIA_BART, "recambio", por="bart")
        s.add(Company(name="Cliente Figurante", factusol_company_id=CLI))
        s.commit()


@pytest.fixture
def script(session_factory, monkeypatch):  # noqa: F811
    from scripts import garantias_como_otro

    _preparar(session_factory)
    engine = session_factory.kw["bind"]
    monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
    # Sin cola (como en una máquina sin Redis): recalcula aquí mismo.
    monkeypatch.setattr("app.services.empresa_erp_job.encolar", lambda *a, **k: "sin_cola")
    return garantias_como_otro


def _clase(session_factory, codart: str) -> str | None:  # noqa: F811
    with session_factory() as s:
        p = s.get(HistoricoArticuloClase, codart)
        return p.clase if p else None


def test_que_es_una_garantia() -> None:
    from scripts.garantias_como_otro import palabra_de_garantia as g

    for si in ("Flux Gar. Las · 1 año de garantía limitada", "FLUX GAR LAS", "Gtía. 2 años",
               "GTIA 1 AÑO", "Garant. 2 años", "Garant 2 años", "Extended warranty",
               "Guarantee 1 year", "Garantieverlängerung", "Garanzia 24 mesi",
               "Ampliación de garantía"):
        assert g(si), si
    for no in ("Tubo (sin garantía)", "Reparación fuera de garantía", "Cabezal en garantía",
               "Kit filtros con garantía 6 meses", "Qualität garantiert",
               "Filtro de calidad garantizada", "Lens quality guaranteed",
               "Reparación fuera  de garantía", "Garaje figurante", "Filtro figurante"):
        assert g(no) is None, no


def test_probar_mide_lo_de_la_ficha_y_no_escribe(script, session_factory, capsys) -> None:  # noqa: F811,E501
    assert script.main(["--probar"]) == 0
    salida = capsys.readouterr().out
    # La de cada año: la que en 2025 era consumible, también.
    assert "que hoy cuentan como consumible o recambio: 2 (9 líneas de factura)" in salida
    assert GARANTIA in salida and CAMBIA_FAMILIA in salida
    assert "Con clase propia que puso alguien (no se tocan): 1" in salida
    assert GARANTIA_BART in salida and "[recambio]" in salida
    assert "Equivalentes de una máquina marcada" in salida and EQUIVALENTE in salida
    assert "Dudosas" in salida and "Filtro carbón figurante" in salida          # X100, aparte
    # El ritmo de la ficha, con las garantías a 0 €: se mueve.
    assert "cliente 4950: cada 1 mes → cada 6 meses" in salida
    assert "clientes con máquina de la gama: 1 → 1" in salida                   # el parque, igual
    for codigo in (GARANTIA, CAMBIA_FAMILIA, REUSADO, EQUIVALENTE):
        assert _clase(session_factory, codigo) is None                          # no escribe
    with session_factory() as s:
        assert script._registro(s) == {"puestos": [], "excluidos": []}
    # La gama, por su código o por su nombre; una que no existe, no.
    assert script.main(["--probar", "--gama", "Láser"]) == 0
    assert script.main(["--probar", "--gama", "plotter"]) == 2
    # Lo que no es una garantía, fuera; un código que no está en la lista, se avisa.
    capsys.readouterr()
    assert script.main(["--probar", "--excluir", CAMBIA_FAMILIA.lower(), "NOEXISTE"]) == 0
    salida = capsys.readouterr().out
    assert "consumible o recambio: 1" in salida and "Dejadas fuera" in salida
    assert "no están en ninguna lista (se ignoran): NOEXISTE" in salida


def test_apply_recuerda_y_no_pisa_lo_deshecho_a_mano(script, session_factory, capsys) -> None:  # noqa: F811,E501
    assert script.main(["--apply"]) == 2                                       # sin --yes, no
    # Lo excluido se recuerda aunque hoy no se tocara por otro motivo (el
    # equivalente de la máquina): si mañana Bart quita la equivalencia, sigue fuera.
    assert script.main(["--apply", "--yes", "--excluir", CAMBIA_FAMILIA, EQUIVALENTE]) == 0
    with session_factory() as s:
        p = s.get(HistoricoArticuloClase, GARANTIA)
        assert (p.clase, p.por) == ("otro", script.POR)
        assert s.get(HistoricoArticuloClase, GARANTIA_BART).clase == "recambio"
        assert s.get(HistoricoFamilia, "251").clase == "consumible"            # la familia, igual
        assert script._registro(s) == {"puestos": [GARANTIA],
                                       "excluidos": [CAMBIA_FAMILIA, EQUIVALENTE]}
    assert _clase(session_factory, CAMBIA_FAMILIA) is None
    assert _clase(session_factory, REUSADO) is None                             # dudosa
    assert _clase(session_factory, EQUIVALENTE) is None                         # máquina
    # Bart la quita a mano (para él sí es consumible). Otra pasada no la vuelve a
    # poner, y la excluida sigue fuera sin repetir --excluir.
    with session_factory() as s:
        cl.clasificar_articulo(s, GARANTIA, None, por="bart")
    capsys.readouterr()
    assert script.main(["--apply", "--yes"]) == 0
    salida = capsys.readouterr().out
    assert "consumible o recambio: 0" in salida
    assert "alguien las quitó después (no se vuelven a poner): 1" in salida
    assert "Dejadas fuera (--excluir, de ahora o de antes): 1" in salida
    assert _clase(session_factory, GARANTIA) is None
    assert _clase(session_factory, CAMBIA_FAMILIA) is None


def test_revertir_quita_lo_suyo_y_respeta_lo_cambiado(script, session_factory) -> None:  # noqa: F811,E501
    assert script.main(["--apply", "--yes"]) == 0
    assert _clase(session_factory, GARANTIA) == _clase(session_factory, CAMBIA_FAMILIA) == "otro"
    # Una segunda pasada no duplica ni vuelve a firmar.
    assert script.main(["--apply", "--yes"]) == 0
    # Si alguien la cambia a mano después, deshacer no la pisa.
    with session_factory() as s:
        cl.clasificar_articulo(s, GARANTIA, "accesorio", por="bart")
    assert script.main(["--revertir"]) == 2
    assert script.main(["--revertir", "--yes"]) == 0
    assert _clase(session_factory, GARANTIA) == "accesorio"
    assert _clase(session_factory, CAMBIA_FAMILIA) is None
    with session_factory() as s:
        # Lo que deshizo el script, ya no consta como suyo; lo que cambió Bart, sí.
        assert script._registro(s)["puestos"] == [GARANTIA]
    assert script.main(["--revertir", "--yes"]) == 0                            # idempotente


def test_con_cola_programa_el_recalculo(script, session_factory, monkeypatch, capsys) -> None:  # noqa: F811,E501
    monkeypatch.setattr("app.services.empresa_erp_job.encolar", lambda *a, **k: "programado")

    def no(*_a, **_k):
        raise AssertionError("con cola no se recalcula aquí")

    monkeypatch.setattr("app.services.empresa_erp.recalcular", no)
    assert script.main(["--apply", "--yes"]) == 0
    assert "Recálculo del resumen por empresa programado" in capsys.readouterr().out
