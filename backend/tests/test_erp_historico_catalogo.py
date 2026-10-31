"""Los agujeros del catálogo, separados (1/11/2026): lo vendido sin catálogo y
lo que tiene catálogo pero no una familia que case. Los códigos de familia NO
se normalizan: «014» y «14» son familias distintas. Figurantes."""
from __future__ import annotations

from typing import Any

from app.erp import historico
from app.erp import historico_clasificacion as cl
from app.erp import historico_maquinas as hm
from app.erp.models import HistoricoFamilia
from tests.test_erp_historico import FactusolFalso, _art, _fac, _lfa, session_factory  # noqa: F401


class SinFFam(FactusolFalso):
    """Un FACTUSOL al que se le cae `F_FAM` del ejercicio en curso."""

    def load_table(self, tabla: str, filtro: str = "1=1", ejercicio: str | None = None,
                   **kw: Any) -> list[dict]:
        if tabla == "F_FAM" and str(ejercicio) == "2026":
            raise RuntimeError("caída")
        return super().load_table(tabla, filtro, ejercicio, **kw)


def _datos() -> dict[str, Any]:
    return {
        "2017": {"F_FAC": [_fac(170001, "4601", "Figurante", "2017-03-10T00:00:00")],
                 "F_LFA": [_lfa(170001, 1, "60995", "BEAMBOX", 1, 2500)],
                 "F_ALB": [], "F_LAL": [],
                 "F_ART": [_art("60995", "FLUX BEAMBOX 40W", "19")],
                 "F_FAM": [{"CODFAM": "19", "DESFAM": "Láseres Figurante 2017"}]},
        "2026": {"F_FAC": [_fac(260001, "4601", "Figurante", "2026-03-10T00:00:00")],
                 "F_LFA": [_lfa(260001, 1, "ROLL1", "RODILLO", 2, 50),
                           _lfa(260001, 2, "CNC1", "CNC", 1, 900),
                           _lfa(260001, 3, "0712", "TECLEADO CON CERO", 1, 10),
                           _lfa(260001, 4, "NADA9", "SIN CATALOGO", 1, 10)],
                 "F_ALB": [], "F_LAL": [],
                 "F_ART": [_art("ROLL1", "SPARE PARTS ROLLCOATER", "019"),
                           _art("CNC1", "CNC FIGURANTE", "14"),
                           _art("712", "OTRO ARTICULO", "019")],
                 "F_FAM": [{"CODFAM": "019", "DESFAM": "SPARE PARTS ROLLCOATER"},
                           {"CODFAM": "14", "DESFAM": None}]},
    }


def test_familias_con_ceros_distintas_y_la_19_entra_aunque_falle_ffam(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        historico.cargar(s, SinFFam(_datos()), ["2017", "2026"], en_curso="2026")
        fams = {f.codigo: f.descripcion for f in s.query(HistoricoFamilia)}
        # La 19 solo la usa un artículo de 2017: entra, con el nombre de SU año.
        assert fams["19"] == "Láseres Figurante 2017"
        assert "019" in fams and "14" in fams        # tal cual, sin quitar ni poner ceros


def test_los_dos_numeros_separados_y_los_ceros_solo_se_ensenan(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(_datos()), ["2017", "2026"], en_curso="2026")
        cl.clasificar(s, "019", {"clase": "recambio"}, por="bart")
        sc = hm.sin_catalogo(s)
        # Sin catálogo: el tecleado con cero y el que no existe.
        assert (sc["articulos"], sc["lineas"]) == (2, 2)
        assert [(x["codart"], x["parecidos"]) for x in sc["con_ceros"]] == [("0712", ["712"])]
        # Con catálogo y sin familia que case: la 19 (sin clase) y la 14 (sin clase).
        fam = {g["familia"]: (g["motivo"], g["articulos"]) for g in sc["sin_familia"]["familias"]}
        assert fam == {"19": ("sin_clase", 1), "14": ("sin_clase", 1)}
        # La 019 está clasificada y NO arrastra a la 19 ni a la 14.
        assert sc["sin_familia"]["articulos"] == 2
        # Nada se reescribe: los códigos guardados siguen igual.
        assert {f.codigo for f in s.query(HistoricoFamilia)} == {"19", "019", "14"}
        cl.clasificar(s, "14", {"clase": "maquina"}, por="bart")
        assert hm.sin_catalogo(s)["sin_familia"]["articulos"] == 1


def test_la_serie_con_nombre_fiscal_bomedia_no_lo_ensena(session_factory) -> None:  # noqa: F811
    import json

    from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID, ErpSettings

    with session_factory() as s:
        historico.cargar(s, FactusolFalso(_datos()), ["2026"], en_curso="2026")
        cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID) or ErpSettings(
            id=ERP_SETTINGS_SINGLETON_ID)
        cfg.factusol_series_json = json.dumps({"companies": {"5": {"nombre": "Bomedia S.L."}}})
        s.add(cfg)
        s.commit()
        nombre = next(i for i in hm.series(s)["items"] if i["serie"] == "5")["nombre"]
        assert nombre is None or "bomedia" not in nombre.lower()
        cfg.factusol_series_json = json.dumps({"companies": {"5": {"nombre": "Bomedia S.L."}},
                                               "names": {"5": "Streamtec"}})
        s.commit()
        assert next(i for i in hm.series(s)["items"] if i["serie"] == "5")["nombre"] == "Streamtec"
