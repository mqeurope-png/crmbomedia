"""Cómo se comparan los códigos sin reescribirlos (1/11/2026): el fantasma del
cero en las familias, la caja, y el alias de código de artículo de las
líneas. Figurantes."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.erp import historico, parque
from app.erp import historico_alias as al
from app.erp import historico_clasificacion as cl
from app.erp import historico_maquinas as hm
from app.erp.models import HistoricoArticulo, HistoricoFamilia
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

CLI = "4801"


def _datos() -> dict[str, Any]:
    return {"2026": {
        "F_FAC": [_fac(260801, CLI, "Cliente Figurante", "2026-02-01T00:00:00")],
        "F_LFA": [_lfa(260801, 1, "R1", "RODILLO", 1, 50),          # familia «19» (fantasma)
                  _lfa(260801, 2, "W1", "WRAPPER", 1, 50),          # familia «14» (distinta)
                  _lfa(260801, 3, "B1", "BOBINA", 1, 50),           # familia «bob» (caja)
                  _lfa(260801, 4, "053332", "MAQUINA", 1, 3000),    # alias → 53332
                  _lfa(260801, 5, "1", "UNO", 1, 10)],              # dudoso: dos candidatos
        "F_ALB": [], "F_LAL": [],
        "F_ART": [_art("R1", "RODILLO FIGURANTE", "19"),
                  _art("R2", "OTRO RODILLO", "019"),
                  _art("W1", "WRAPPER FIGURANTE", "14"),
                  _art("C1", "CELOFAN FIGURANTE", "014"),
                  _art("B1", "BOBINA FIGURANTE", "bob"),
                  _art("53332", "MAQUINA FIGURANTE", "006"),
                  _art("00001", "DISCO FIGURANTE", "500"),
                  _art("0001", "TRANSPORTE", "501")],
        "F_FAM": [{"CODFAM": "019", "DESFAM": "SPARE PARTS FIGURANTE"},
                  {"CODFAM": "014", "DESFAM": "CELOFAN FIGURANTE"},
                  {"CODFAM": "14", "DESFAM": "Recambios Wrapper Figurante"},
                  {"CODFAM": "BOB", "DESFAM": "Bobinas"},
                  {"CODFAM": "006", "DESFAM": "Duplicadoras"},
                  {"CODFAM": "500", "DESFAM": "Discos"},
                  {"CODFAM": "501", "DESFAM": "Portes"}],
    }}


def _preparar(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(_datos()), ["2026"], en_curso="2026")
        cl.clasificar(s, "019", {"clase": "recambio"}, por="bart")
        cl.clasificar(s, "014", {"clase": "consumible"}, por="bart")
        cl.clasificar(s, "14", {"clase": "recambio"}, por="bart")
        cl.clasificar(s, "BOB", {"clase": "consumible"}, por="bart")
        cl.clasificar(s, "006", {"clase": "maquina"}, por="bart")
        from app.erp.models import HistoricoAliasArticulo
        s.add(HistoricoAliasArticulo(codigo="053332", codart="53332", por="(migración 0164)"))
        s.add(Company(name="Cliente Figurante", factusol_company_id=CLI))
        s.commit()


def test_el_fantasma_del_cero_se_funde_y_las_gemelas_distintas_no(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        a = al.familias(s)
        assert a.fundidas == {"19": "019"}
        assert a.resuelve("19") == "019"
        assert a.resuelve("14") == "14" and a.resuelve("014") == "014"   # distintas
        assert a.resuelve("bob") == "BOB"                                # caja
        # El listado no enseña el fantasma y le suma sus artículos a la larga.
        fams = {f["codigo"]: f for f in historico.familias(s)}
        assert "19" not in fams and fams["019"]["articulos"] == 2
        assert fams["019"]["ventas"] == 1 and fams["BOB"]["ventas"] == 1
        # Las ventas del fantasma cuentan con la clase de la larga.
        assert {c["codcli"] for c in historico.compradores(s, clase="recambio")} == {CLI}
        assert {c["codcli"] for c in historico.compradores(s, familia="BOB")} == {CLI}
        # Nada se reescribe: lo guardado sigue igual.
        assert s.scalar(select(HistoricoArticulo.familia).where(
            HistoricoArticulo.codart == "R1")) == "19"
        assert s.get(HistoricoFamilia, "19") is not None


def test_una_corta_sin_nombre_pero_con_clase_no_se_funde_se_avisa(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        cl.clasificar(s, "19", {"clase": "consumible"}, por="bart")
        a = al.familias(s)
        assert a.fundidas == {}
        assert a.avisos == [{"corta": "19", "largas": ["019"], "motivo": "clase_puesta"}]
        assert a.resuelve("19") == "19"


def test_el_alias_de_codigo_mete_la_linea_en_su_articulo(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        r = hm.articulos(s, con_sin_catalogo=True)
        item = next(i for i in r["items"] if i["codart"] == "53332")
        assert item["familia"] == "006" and item["lineas"] == 1
        sc = r["sin_catalogo"]
        # 053332 ya no está «sin catálogo»; el «1», dudoso, sí, con sus dos candidatos.
        assert [x["codart"] for x in sc["items"]] == ["1"]
        assert sc["con_ceros"][0]["parecidos"] == ["00001", "0001"]
        hm.marcar(s, "53332", True, por="bart")
        empresa = s.scalar(select(Company).where(Company.factusol_company_id == CLI))
        assert [m["codart"] for m in parque.parque(s, empresa)["maquinas"]] == ["053332"]


def test_api_alias_lo_decide_bart(http, session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    h = auth_headers(http, "admin")
    url = "/api/erp/historico/alias-articulo"
    assert http.post(url, json={"codigo": "1", "codart": "0001"},
                     headers=auth_headers(http, "pedidos")).status_code == 403
    for cuerpo, codigo in (({"codigo": "1", "codart": "NOEXISTE"}, "sin_catalogo"),
                           ({"codigo": "R1", "codart": "R2"}, "codigo_en_catalogo"),
                           ({"codigo": "053332", "codart": "53332"}, "ya_tiene_alias")):
        r = http.post(url, json=cuerpo, headers=h)
        assert r.status_code == 422 and r.json()["detail"]["code"] == codigo, cuerpo
    r = http.post(url, json={"codigo": "1", "codart": "0001"}, headers=h)
    assert r.status_code == 200
    assert {a["codigo"] for a in r.json()["alias_articulo"]} == {"053332", "1"}
    assert r.json()["sin_catalogo"]["articulos"] == 0
    r = http.delete(f"{url}/1", headers=h)
    assert r.status_code == 200 and r.json()["sin_catalogo"]["articulos"] == 1


def test_la_migracion_0164_siembra_los_nueve() -> None:
    import importlib.util
    from pathlib import Path

    ruta = next(Path(__file__).resolve().parents[1].glob("alembic/versions/20261101_0164_*.py"))
    spec = importlib.util.spec_from_file_location("m0164", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert dict(modulo.ALIAS) == {
        "053332": "53332", "8": "00008", "7": "00007", "80": "00080", "100": "00100",
        "020": "00020", "010": "00010", "030": "00030", "040": "00040"}


# --- lo que encontró la revisión -------------------------------------------------------


def _con_albaran_alias(session_factory) -> None:  # noqa: F811
    """Albarán tecleado con el alias (053332) y su factura con el código del
    catálogo (53332), enlazada al albarán."""
    from tests.test_erp_historico import _alb, _lal
    d = _datos()
    d["2026"]["F_ALB"] = [_alb(260701, CLI, "Cliente Figurante", "2026-01-20T00:00:00")]
    d["2026"]["F_LAL"] = [_lal(260701, 1, "053332", "MAQUINA", 1)]
    lfa = _lfa(260802, 1, "53332", "MAQUINA", 1, 3000)
    lfa.update({"DOCLFA": "A", "DTPLFA": "5", "DCOLFA": 260701})
    d["2026"]["F_FAC"].append(_fac(260802, CLI, "Cliente Figurante", "2026-01-25T00:00:00"))
    d["2026"]["F_LFA"] = [x for x in d["2026"]["F_LFA"] if x["ARTLFA"] != "053332"] + [lfa]
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026"], en_curso="2026")
        cl.clasificar(s, "006", {"clase": "maquina"}, por="bart")
        from app.erp.models import HistoricoAliasArticulo
        s.add(HistoricoAliasArticulo(codigo="053332", codart="53332", por="x"))
        s.add(Company(name="Cliente Figurante", factusol_company_id=CLI))
        s.commit()
        hm.marcar(s, "53332", True, por="bart")


def test_el_albaran_con_alias_y_su_factura_son_una_venta(session_factory) -> None:  # noqa: F811
    _con_albaran_alias(session_factory)
    with session_factory() as s:
        for kw in ({"familia": "006"}, {"codart": "53332"}, {"codart": "053332"}):
            c = next(c for c in historico.compradores(s, **kw) if c["codcli"] == CLI)
            assert (c["unidades"], c["compras"]) == (1, 1), kw
        empresa = s.scalar(select(Company).where(Company.factusol_company_id == CLI))
        maquinas = parque.parque(s, empresa)["maquinas"]
        assert [(m["codart"], len(m["albaranes"])) for m in maquinas] == [("53332", 1)]
        assert maquinas[0]["mismo_modelo"] == []


def test_la_baja_del_albaran_con_alias_se_hereda(session_factory) -> None:  # noqa: F811
    _con_albaran_alias(session_factory)
    with session_factory() as s:
        from app.erp.models import ParqueBaja
        empresa = s.scalar(select(Company).where(Company.factusol_company_id == CLI))
        m = parque.parque(s, empresa)["maquinas"][0]
        clave_alb = m["albaranes"][0]["clave"]
        s.add(ParqueBaja(clave=clave_alb, company_id=empresa.id, motivo="vendida", por="bart"))
        s.commit()
        assert parque.parque(s, empresa)["activas"] == 0


def test_un_alias_no_vale_si_el_codigo_esta_en_el_catalogo(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        from app.erp.models import HistoricoAliasArticulo
        s.add(HistoricoAliasArticulo(codigo="R1", codart="53332", por="x"))
        s.commit()
        assert al.articulos(s).resuelve("R1") == "R1"
