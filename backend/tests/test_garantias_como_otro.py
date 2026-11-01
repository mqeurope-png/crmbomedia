"""Las garantías no son consumible (1/11/2026): el script les da clase propia
«Otro» sin tocar la familia ni lo que decidió Bart, mide cuánto se mueve el
ritmo y los dormidos y huecos, y se deshace. Figurantes."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.erp import historico
from app.erp import historico_clasificacion as cl
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
LASER, FILTRO, GARANTIA, GARANTIA_BART = "LAS100", "FIL200", "GAR300", "GAR400"
CAT = [_art(LASER, "LASER FIGURANTE 40W", "021"),
       _art(FILTRO, "Filtro figurante", "251"),
       _art(GARANTIA, "Flux Gar. Las · 1 año de garantía limitada", "251"),
       _art(GARANTIA_BART, "Garantía figurante ampliada", "251")]
FAM = [{"CODFAM": "021", "DESFAM": "FLUX"}, {"CODFAM": "251", "DESFAM": "Flux no web"}]


def _datos() -> dict[str, Any]:
    ventas = [(260001, "2026-01-05", LASER)]
    # Una garantía con cada máquina que revende: cada mes.
    ventas += [(260010 + i, f"2026-{i + 1:02d}-10", GARANTIA) for i in range(9)]
    # Filtros de verdad: cada ~6 meses.
    ventas += [(250001, "2024-06-01", FILTRO), (250002, "2024-12-01", FILTRO),
               (250003, "2025-06-01", FILTRO)]
    por_anio: dict[str, Any] = {}
    for codigo, fecha, art in ventas:
        d = por_anio.setdefault(fecha[:4], {"F_FAC": [], "F_LFA": [], "F_ALB": [], "F_LAL": [],
                                            "F_ART": CAT, "F_FAM": FAM})
        d["F_FAC"].append(_fac(codigo, CLI, "Cliente Figurante", f"{fecha}T00:00:00"))
        d["F_LFA"].append(_lfa(codigo, 1, art, art, 1, 100))
    return por_anio


def _preparar(session_factory) -> None:  # noqa: F811
    datos = _datos()
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(datos), list(datos), en_curso="2026")
        if not any(x["nombre"] == "Láser" for x in cl.gamas(s)):
            cl.crear_gama(s, "Láser")
        cl.clasificar(s, "021", {"clase": "maquina", "gama": "laser"}, por="bart")
        cl.clasificar(s, "251", {"clase": "consumible", "gama": "laser"}, por="bart")
        hm.marcar(s, LASER, True, por="bart")
        # Bart ya decidió que esta «garantía» es un recambio: no se toca.
        cl.clasificar_articulo(s, GARANTIA_BART, "recambio", por="bart")
        s.add(Company(name="Cliente Figurante", factusol_company_id=CLI))
        s.commit()


def test_probar_mide_y_no_escribe_apply_y_revertir(session_factory, monkeypatch, capsys) -> None:  # noqa: F811,E501
    from scripts import garantias_como_otro as script

    _preparar(session_factory)
    engine = session_factory.kw["bind"]
    monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
    assert script.main(["--probar"]) == 0
    salida = capsys.readouterr().out
    assert "que hoy cuentan como consumible o recambio: 1 (9 líneas vendidas)" in salida
    assert GARANTIA in salida and "ya puesta" in salida and GARANTIA_BART in salida
    assert "cliente 4950: cada 1 mes → cada 6 meses" in salida                  # el ritmo se mueve
    # Lo que --probar lista y no es una garantía, se deja fuera.
    assert script.main(["--probar", "--excluir", GARANTIA.lower()]) == 0
    salida = capsys.readouterr().out
    assert "consumible o recambio: 0" in salida and "Dejados fuera con --excluir: 1" in salida
    with session_factory() as s:
        assert s.get(HistoricoArticuloClase, GARANTIA) is None          # no escribe
    assert script.main(["--apply"]) == 2                                 # sin --yes, no
    assert script.main(["--apply", "--yes"]) == 0
    with session_factory() as s:
        p = s.get(HistoricoArticuloClase, GARANTIA)
        assert (p.clase, p.por) == ("otro", script.POR)
        assert s.get(HistoricoArticuloClase, GARANTIA_BART).clase == "recambio"
        assert s.get(HistoricoFamilia, "251").clase == "consumible"     # la familia, igual
    # Si alguien la cambia a mano después, deshacer no la pisa.
    with session_factory() as s:
        cl.clasificar_articulo(s, GARANTIA, "accesorio", por="bart")
    assert script.main(["--revertir", "--yes"]) == 0
    with session_factory() as s:
        assert s.get(HistoricoArticuloClase, GARANTIA).clase == "accesorio"
        cl.clasificar_articulo(s, GARANTIA, None, por="bart")
    # Y lo del script sí se deshace.
    assert script.main(["--apply", "--yes"]) == 0
    assert script.main(["--revertir", "--yes"]) == 0
    with session_factory() as s:
        assert s.get(HistoricoArticuloClase, GARANTIA) is None
        assert s.scalar(select(HistoricoArticuloClase.codart).where(
            HistoricoArticuloClase.por == script.POR)) is None
