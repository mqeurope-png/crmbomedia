"""Códigos de artículo equivalentes: el mismo modelo con el código interno y
el de la web (1/11/2026). Figurantes: ni clientes ni centros reales."""
from __future__ import annotations

from typing import Any

from app.erp import historico, parque
from app.erp import historico_clasificacion as cl
from app.erp import historico_equivalencias as eq
from app.erp import historico_maquinas as hm
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

UNO, DOS = "4501", "4502"          # «Cliente Figurante 1/2»
INTERNO, WEB = "60995", "34"       # Beambox 40W: FACTUSOL y web
HEXA_I, HEXA_W = "61403", "4174"


def _datos() -> dict[str, Any]:
    # Cliente 1: albarán con el código interno, facturado con el de la web
    # (la línea de la factura apunta al albarán). Cliente 2: compra por la web.
    lfa = _lfa(260301, 1, WEB, "BEAMBOX WEB", 1, 2500)
    lfa.update({"DOCLFA": "A", "DTPLFA": "5", "DCOLFA": 260031})
    return {"2026": {
        "F_FAC": [_fac(260301, UNO, "Cliente Figurante 1", "2026-03-12T00:00:00"),
                  _fac(260302, DOS, "Cliente Figurante 2", "2026-04-10T00:00:00"),
                  _fac(260303, DOS, "Cliente Figurante 2", "2026-06-10T00:00:00")],
        "F_LFA": [lfa,
                  _lfa(260302, 1, WEB, "BEAMBOX WEB", 1, 2500),
                  # La Hexa con los dos códigos, en dos facturas sin enlace.
                  _lfa(260303, 1, HEXA_I, "FLUX HEXA 60W", 1, 9800),
                  _lfa(260303, 2, HEXA_W, "Flux Hexa (60W)", 1, 9800)],
        "F_ALB": [_alb(260031, UNO, "Cliente Figurante 1", "2026-03-10T00:00:00")],
        "F_LAL": [_lal(260031, 1, INTERNO, "FLUX BEAMBOX 40W", 1)],
        "F_ART": [_art(INTERNO, "FLUX BEAMBOX 40W 60995", "021"),
                  _art(WEB, "Beambox - Cortadora láser de sobremesa (40W)", "071"),
                  _art(HEXA_I, "FLUX HEXA 60W", "021"),
                  _art(HEXA_W, "Flux Hexa - Cortadora láser CO2 (60W)", "071")],
        "F_FAM": [{"CODFAM": "021", "DESFAM": "FLUX"}, {"CODFAM": "071", "DESFAM": "Fluxlasers"}],
    }}


def _preparar(session_factory, *, pares=((INTERNO, WEB),)) -> dict[str, str]:  # noqa: F811
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(_datos()), ["2026"], en_curso="2026")
        for f in ("021", "071"):
            cl.clasificar(s, f, {"clase": "maquina"}, por="bart")
        for c in (INTERNO, WEB, HEXA_I, HEXA_W):
            hm.marcar(s, c, True, por="bart")
        for a, b in pares:
            eq.declarar(s, a, b, por="bart")
        ids = {}
        for k, c in (("uno", UNO), ("dos", DOS)):
            co = Company(name=f"Cliente Figurante {k}", factusol_company_id=c)
            s.add(co)
            s.flush()
            ids[k] = co.id
        s.commit()
        return ids


def _maquinas(s, company_id: str) -> list[dict[str, Any]]:
    return parque.parque(s, s.get(Company, company_id))["maquinas"]


def test_albaran_con_un_codigo_y_factura_con_el_equivalente_son_una_maquina(session_factory) -> None:  # noqa: F811,E501
    ids = _preparar(session_factory)
    with session_factory() as s:
        uno = _maquinas(s, ids["uno"])
        assert [(m["codart"], m["origen"]) for m in uno] == [(WEB, "factura")]
        assert [a["numero"] for a in uno[0]["albaranes"]] == ["5-260031"]


def test_sin_declararlos_salen_dos(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory, pares=())
    with session_factory() as s:
        uno = _maquinas(s, ids["uno"])
        assert sorted(m["codart"] for m in uno) == sorted([WEB, INTERNO])


def test_una_marca_vale_para_el_grupo_salvo_decision_propia(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        # Bart dice que la de la web no es máquina: manda su decisión.
        hm.marcar(s, WEB, False, por="bart")
        assert WEB not in [m["codart"] for m in _maquinas(s, ids["dos"])]
        # Sin decisión propia sobre la de la web, va con su grupo.
        from app.erp.models import HistoricoArticuloClase
        s.delete(s.get(HistoricoArticuloClase, WEB))
        s.commit()
        assert WEB in [m["codart"] for m in _maquinas(s, ids["dos"])]


def test_buscar_un_codigo_encuentra_las_ventas_del_equivalente(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        assert {c["codcli"] for c in historico.compradores(s, codart=INTERNO)} == {UNO, DOS}
        assert {c["codcli"] for c in historico.compradores(s, codart=WEB)} == {UNO, DOS}
        assert {c["codcli"] for c in historico.compradores(s, codart=HEXA_I)} == {DOS}


def test_un_par_no_declarado_sigue_avisando_y_uno_declarado_no(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        dos = {m["codart"]: m for m in _maquinas(s, ids["dos"])}
        assert [o["codart"] for o in dos[HEXA_I]["mismo_modelo"]] == [HEXA_W]   # no declarado
        assert dos[WEB]["mismo_modelo"] == []
        eq.declarar(s, HEXA_I, HEXA_W, por="bart")
        dos = {m["codart"]: m for m in _maquinas(s, ids["dos"])}
        assert all(m["mismo_modelo"] == [] for m in dos.values())


def test_api_equivalencias(http, session_factory) -> None:  # noqa: F811
    _preparar(session_factory, pares=())
    h = auth_headers(http, "admin")
    assert http.post("/api/erp/historico/equivalencias", json={"canonico": INTERNO, "codart": WEB},
                     headers=auth_headers(http, "pedidos")).status_code == 403
    r = http.post("/api/erp/historico/equivalencias", json={"canonico": INTERNO, "codart": WEB},
                  headers=h)
    assert r.status_code == 200
    assert [(i["codart"], i["canonico"]) for i in r.json()["items"]] == [(WEB, INTERNO)]
    assert r.json()["items"][0]["descripcion"].startswith("Beambox")
    for cuerpo, codigo in (({"canonico": INTERNO, "codart": WEB}, "ya_declarado"),
                           ({"canonico": INTERNO, "codart": "NOEXISTE"}, "sin_catalogo"),
                           ({"canonico": WEB, "codart": "34"}, "mismo_codigo")):
        r = http.post("/api/erp/historico/equivalencias", json=cuerpo, headers=h)
        assert r.status_code == 422 and r.json()["detail"]["code"] == codigo
    assert http.delete(f"/api/erp/historico/equivalencias/{WEB}", headers=h).json()["items"] == []
    assert http.get("/api/erp/historico/equivalencias",
                    headers=auth_headers(http, "user")).status_code == 200


def test_la_migracion_siembra_los_cinco_pares() -> None:
    import importlib.util
    from pathlib import Path

    ruta = next(Path(__file__).resolve().parents[1].glob("alembic/versions/20261101_0163_*.py"))
    spec = importlib.util.spec_from_file_location("m0163", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert modulo.PARES == eq.PARES_FLUX


# --- lo que encontró la revisión -------------------------------------------------------


def test_quien_compro_que_no_cuenta_dos_veces_la_misma_venta(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        uno = next(c for c in historico.compradores(s, codart=INTERNO) if c["codcli"] == UNO)
        assert (uno["unidades"], uno["compras"]) == (1, 1)


def test_la_baja_del_duplicado_no_se_hereda_al_declarar_el_par(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory, pares=())
    with session_factory() as s:
        empresa = s.get(Company, ids["uno"])
        alb = next(m for m in _maquinas(s, ids["uno"]) if m["codart"] == INTERNO)
        parque.dar_baja(s, empresa, clave=alb["clave"], motivo="duplicado", por="bart")
        eq.declarar(s, INTERNO, WEB, por="bart")
        r = parque.parque(s, empresa)
        assert r["activas"] == 1 and r["maquinas"][0]["codart"] == WEB


def test_quitar_el_canonico_deshace_el_grupo(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        eq.quitar(s, INTERNO)
        assert eq.listar(s) == []
