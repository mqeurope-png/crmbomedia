"""La familia VACÍA de un artículo en un ejercicio (1/11/2026): se toma la del
ejercicio más reciente que la tenga; si no la tiene en ninguno, se enseña y no
se inventa. Figurantes."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.erp import historico
from app.erp import historico_clasificacion as cl
from app.erp import historico_maquinas as hm
from app.erp.models import HistoricoArticulo
from tests.test_erp_historico import FactusolFalso, _art, _fac, _lfa, session_factory  # noqa: F401

CLI = "4901"


def _anio(ej: int, arts: list[dict], ventas: list[tuple[str, float]]) -> dict[str, Any]:
    cod = ej * 10 + 1
    return {"F_FAC": [_fac(cod, CLI, "Cliente Figurante", f"{ej}-03-01T00:00:00")],
            "F_LFA": [_lfa(cod, i, a, a, 1, p) for i, (a, p) in enumerate(ventas, 1)],
            "F_ALB": [], "F_LAL": [], "F_ART": arts,
            "F_FAM": [{"CODFAM": "700", "DESFAM": "Tinta UV figurante"},
                      {"CODFAM": "006", "DESFAM": "Duplicadoras"}]}


def _datos() -> dict[str, Any]:
    return {
        # En 2019 la tinta tiene familia; en 2024 la volcaron vacía.
        "2019": _anio(2019, [_art("53606", "TINTA UV FIGURANTE 1L", "700"),
                             _art("D1", "DUPLICADORA FIGURANTE", "006")],
                      [("53606", 40)]),
        "2024": _anio(2024, [_art("53606", "TINTA UV FIGURANTE 1L", ""),
                             _art("TJM1", "ARTICULO SIN FAMILIA FIGURANTE", ""),
                             _art("D1", "DUPLICADORA FIGURANTE", "")],
                      [("53606", 40), ("53606", 40), ("TJM1", 10), ("D1", 900)]),
    }


def _preparar(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(_datos()), list(_datos()), en_curso="2024")
        cl.clasificar(s, "700", {"clase": "consumible"}, por="bart")
        cl.clasificar(s, "006", {"clase": "maquina"}, por="bart")


def test_la_familia_vacia_se_toma_de_otro_ejercicio_y_cuenta(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        # Las tres líneas de la tinta (2019 y las dos de 2024) cuentan como consumible.
        compra = historico.compradores(s, clase="consumible")
        assert [c["codcli"] for c in compra] == [CLI] and compra[0]["unidades"] == 3
        fams = {f["codigo"]: f for f in historico.familias(s)}
        assert fams["700"]["ventas"] == 3 and fams["700"]["articulos"] == 1
        # La duplicadora, vacía en 2024, vuelve a su familia de máquinas.
        assert [i["familia"] for i in hm.articulos(s)["items"] if i["codart"] == "D1"] == ["006"]


def test_sin_familia_en_ningun_ejercicio_se_ensena_y_no_se_inventa(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        sc = hm.sin_catalogo(s)
        v = sc["familia_vacia"]
        assert (v["articulos"], v["lineas"]) == (1, 1)
        assert v["items"] == [{"codart": "TJM1", "descripcion": "ARTICULO SIN FAMILIA FIGURANTE",
                               "lineas": 1}]
        assert v["recuperadas"] == 2                     # 53606 y D1
        # Y no se mezcla con las familias sin clasificar.
        assert sc["sin_familia"]["articulos"] == 0


def test_rellenar_no_reescribe_el_catalogo(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        historico.compradores(s, clase="consumible")
        hm.sin_catalogo(s)
        assert s.scalar(select(HistoricoArticulo.familia).where(
            HistoricoArticulo.codart == "53606", HistoricoArticulo.ejercicio == "2024")) is None
