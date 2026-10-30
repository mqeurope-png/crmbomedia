"""La ficha de un documento (factura, albarán, proforma) de cualquier año, y
las proformas volcadas sin contar como venta (30/10/2026). Figurantes."""
from __future__ import annotations

from typing import Any

from app.erp import historico
from app.erp.historico_documento import DocumentoNoEncontrado, ficha
from app.erp.models import Order
from app.erp.models.orders import OrderSource
from tests._test_helpers import auth_headers
from tests.test_erp_historico import (  # noqa: F401 — fixtures
    CLI_ROTULOS,
    CLI_TALLER,
    MAQUINA,
    FactusolFalso,
    _art,
    _fac,
    _lfa,
    datos_figurantes,
    http,
    session_factory,
)


def _datos() -> dict[str, Any]:
    datos = datos_figurantes()
    datos["2019"] = {
        "F_FAC": [{**_fac(190001, CLI_TALLER, "Talleres Figurante S.L.",
                          "2019-05-06T00:00:00", "1"), "TOTFAC": 1210.0, "ESTFAC": "2.0"}],
        "F_LFA": [
            _lfa(190001, 1, MAQUINA, "LASER 1390 ANTIGUO", 1, 1000, "1"),
            _lfa(190001, 2, "", "Portes incluidos.", 0, 0, "1"),
        ],
        "F_ALB": [{"TIPALB": "1", "CODALB": 1901, "FECALB": "2019-05-01T00:00:00",
                   "CLIALB": CLI_TALLER, "CNOALB": "Talleres Figurante S.L.", "ESTALB": "1"}],
        "F_LAL": [{"TIPLAL": "1", "CODLAL": 1901, "POSLAL": 1, "ARTLAL": MAQUINA,
                   "DESLAL": "LASER 1390 ANTIGUO", "CANLAL": 1, "PRELAL": 1000,
                   "TOTLAL": 1000},
                  {"TIPLAL": "1", "CODLAL": 1901, "POSLAL": 2, "ARTLAL": "",
                   "DESLAL": "Entregar por la mañana.", "CANLAL": 0}],
        "F_PRE": [{"TIPPRE": "1", "CODPRE": 77, "FECPRE": "2019-04-01T00:00:00",
                   "CLIPRE": CLI_ROTULOS, "CNOPRE": "Rótulos de Ejemplo S.C.",
                   "TOTPRE": 2420.0, "ESTPRE": "1"}],
        "F_LPS": [{"TIPLPS": "1", "CODLPS": 77, "POSLPS": 1, "ARTLPS": MAQUINA,
                   "DESLPS": "LASER 1390 ANTIGUO", "CANLPS": 2, "PRELPS": 1000,
                   "TOTLPS": 2000},
                  {"TIPLPS": "1", "CODLPS": 77, "POSLPS": 2, "ARTLPS": "",
                   "DESLPS": "Oferta válida 30 días."}],
        "F_ART": [_art(MAQUINA, "Láser Figurante 1390 (catálogo 2019)", "LAS")],
    }
    return datos


def _cargar(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(_datos()), ["2026", "2019"], en_curso="2026")


def _ficha(s, tipo: str, ejercicio: str, serie: str, codigo: int, **kw) -> dict:
    return ficha(s, tipo=tipo, ejercicio=ejercicio, serie=serie, codigo=codigo, user=None,
                 en_curso="2026", **kw)


def test_una_factura_de_2019_abre_con_sus_lineas_y_el_nombre_de_su_anio(
    session_factory,  # noqa: F811
) -> None:
    _cargar(session_factory)
    with session_factory() as s:
        f = _ficha(s, "factura", "2019", "1", 190001)
    assert (f["fuente"], f["numero"], f["fecha"], f["total"]) == (
        "volcado", "1-190001", "2019-05-06", 1210.0)
    assert f["estado_label"] == "Cobrada"
    producto = [ln for ln in f["lineas"] if not ln["es_nota"]]
    assert producto[0]["articulo"] == "Láser Figurante 1390 (catálogo 2019)"
    assert (producto[0]["cantidad"], producto[0]["precio"], producto[0]["total"]) == (
        1, 1000, 1000)
    assert [ln["descripcion"] for ln in f["lineas"] if ln["es_nota"]] == ["Portes incluidos."]
    assert f["cliente"]["empresa"]["nombre"] == "Talleres Figurante"
    assert f["pedido"] is None


def test_una_proforma_y_un_albaran_tambien_y_sus_notas_no_son_venta(
    session_factory,  # noqa: F811
) -> None:
    _cargar(session_factory)
    with session_factory() as s:
        alb = _ficha(s, "albaran", "2019", "1", 1901)
        pro = _ficha(s, "proforma", "2019", "1", 77)
        compradores = historico.compradores(s, codart=MAQUINA, desde=2019, hasta=2019)
        notas = historico.resumen_notas(s)
    assert alb["estado_label"] == "Facturado"
    assert [ln["es_nota"] for ln in alb["lineas"]] == [False, True]
    assert (pro["tipo_label"], pro["total"], pro["estado_label"]) == ("Proforma", 2420.0,
                                                                     "Aceptado")
    assert [ln["es_nota"] for ln in pro["lineas"]] == [False, True]
    # La proforma no es una venta: Rótulos no aparece como comprador en 2019.
    assert {c["codcli"] for c in compradores} == {CLI_TALLER}
    # Ni sus líneas cuentan en el resumen de notas (solo facturas y albaranes).
    assert notas["lineas"] == 6 + 2 + 2


def test_un_documento_de_hoy_sin_volcar_se_lee_en_vivo_y_uno_antiguo_no_abre_vacio(
    session_factory,  # noqa: F811
) -> None:
    _cargar(session_factory)
    vivo = {"numero": "5-260099", "fecha": "2026-10-30", "cliente_codigo": CLI_TALLER,
            "cliente_nombre": "Talleres Figurante S.L.", "total": 121.0,
            "estado": "0", "estado_label": "Pendiente de cobro",
            "lines": [{"position": 1, "codart": MAQUINA, "description": "Láser",
                       "quantity": 1, "unit_price": 100, "line_total": 100}]}
    with session_factory() as s:
        f = _ficha(s, "factura", "2026", "5", 260099, lector_vivo=lambda *a: vivo)
        assert (f["fuente"], f["estado_label"]) == ("vivo", "Pendiente de cobro")
        try:
            _ficha(s, "factura", "2018", "1", 180001, lector_vivo=lambda *a: vivo)
            raise AssertionError("debería no encontrarse")
        except DocumentoNoEncontrado:
            pass


def test_api_ficha_con_su_pedido_de_bohub(http, session_factory) -> None:  # noqa: F811
    _cargar(session_factory)
    with session_factory() as s:
        s.add(Order(order_number="FIG-1", external_source=OrderSource.MANUAL.value,
                    factusol_invoice_serie=5, factusol_invoice_number="260001"))
        s.commit()
    r = http.get("/api/erp/historico/documentos/factura/2026/5/260001",
                 headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    assert r.json()["pedido"]["order_number"] == "FIG-1"
    assert http.get("/api/erp/historico/documentos/factura/2017/1/999",
                    headers=auth_headers(http, "admin")).status_code == 404
    assert http.get("/api/erp/historico/documentos/factura/2026/5/260001",
                    headers=auth_headers(http, "sat")).status_code == 403
