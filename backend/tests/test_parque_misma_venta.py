"""«¿Misma venta?» con respuesta (1/11/2026): el aviso enseña importe y
referencia, «sí» lo deja fuera del parque como duplicado (sin tocar las bajas),
«no» quita el aviso de ese par para siempre, y un envío de sustitución a 0 € sale
ya propuesto, sin sacar del parque una máquina de verdad. Figurantes."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select

from app.erp import historico, parque
from app.erp import parque_consultas as pc
from app.erp.models import ParqueBaja, ParqueMismaVenta
from app.models.crm import Company
from tests._test_helpers import auth_headers
from tests.test_erp_historico import FactusolFalso, _fac, _lfa, http, session_factory  # noqa: F401
from tests.test_erp_parque import CLI_HEXA, HEXA, _datos_hexa, _marcar_maquinas

FACTURA = "1-123021"
SUSTITUCION = "ENVÍO MÁQUINA SUBSTITUCIÓN"


def _datos(*, referencia: str, precio: float, fecha: str | None,
           enlace: str = "ninguno") -> dict[str, Any]:
    """Factura 1-123021 (15/12/2022, 9.000 €) y un albarán 3-230001 del mismo
    modelo (2/1/2023, salvo `fecha`), sin enlace salvo `enlace`."""
    d = _datos_hexa(enlace=enlace)
    alb = d["2023"]["F_ALB"][0]
    alb["REFALB"] = referencia
    if fecha:
        alb["FECALB"] = fecha
    d["2023"]["F_LAL"][0].update({"PRELAL": precio, "TOTLAL": precio})
    return d


def _cargar(session_factory, *, referencia: str = "Pedido figurante 23",  # noqa: F811
            precio: float = 10, fecha: str | None = None,
            retocar: Callable[[dict[str, Any]], None] | None = None) -> str:
    d = _datos(referencia=referencia, precio=precio, fecha=fecha)
    if retocar:
        retocar(d)
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026", "2023", "2022"], en_curso="2026")
        from app.erp import historico_clasificacion as cl

        cl.clasificar(s, "LAS", {"clase": "maquina"}, por="x")
        _marcar_maquinas(s)
        empresa = Company(name="Grabados Figurante", factusol_company_id=CLI_HEXA)
        s.add(empresa)
        s.commit()
        return empresa.id


def _hexas(s, company_id: str) -> dict[str, Any]:
    p = parque.parque(s, s.get(Company, company_id))
    filas = [m for m in p["maquinas"] if m["codart"] == HEXA]
    out: dict[str, Any] = {"_p": p, "facturas": [m for m in filas if m["origen"] == "factura"]}
    for m in filas:
        out.setdefault(m["origen"], m)
    return out


def _activas_del_cliente(s) -> int:
    return len([m for m in pc._parque_activo(s).get(CLI_HEXA, []) if m["codart"] == HEXA])


def _con_baja(s) -> bool:
    con: set[str] = set()
    pc._parque_activo(s, con_baja=con)
    return CLI_HEXA in con


def _aviso(s, cid: str) -> tuple[str, str]:
    alb = _hexas(s, cid)["albaran"]
    return alb["clave"], alb["posible_misma_venta"]["clave"]


def test_el_aviso_trae_importe_y_referencia(session_factory) -> None:  # noqa: F811
    cid = _cargar(session_factory)
    with session_factory() as s:
        pm = _hexas(s, cid)["albaran"]["posible_misma_venta"]
    assert pm["numero"] == FACTURA and pm["clave"].startswith("2022|factura|1|123021|")
    assert pm["importe"] == 9000.0 and pm["referencia"] == "Pedido figurante 123021"
    assert pm["albaran"] == {"importe": 10.0, "referencia": "Pedido figurante 23"}
    assert pm["propuesta"] is None


def test_si_es_la_misma_venta_no_cuenta_y_no_es_una_baja(session_factory) -> None:  # noqa: F811
    cid = _cargar(session_factory)
    with session_factory() as s:
        alb, factura = _aviso(s, cid)
        assert _hexas(s, cid)["_p"]["activas"] == 2 and _activas_del_cliente(s) == 2
        parque.contestar_misma_venta(s, s.get(Company, cid), clave=alb, factura=factura,
                                     es_misma=True, por="bart@example.com")
        m = _hexas(s, cid)
        assert m["albaran"]["misma_venta"]["numero"] == FACTURA
        assert m["albaran"]["posible_misma_venta"] is None
        baja = m["albaran"]["baja"]
        assert baja["tipo"] == parque.MISMA_VENTA and baja["duplica"]["numero"] == FACTURA
        assert baja["por"] == "bart@example.com" and baja["propuesta"] is False
        assert m["_p"]["activas"] == 1 and _activas_del_cliente(s) == 1
        assert s.get(ParqueBaja, alb) is None                  # no es una baja
        assert not _con_baja(s)                                # ni cuenta en «tiene baja»
        # Deshacerlo devuelve el aviso.
        parque.deshacer_misma_venta(s, s.get(Company, cid), clave=alb, factura=factura)
        m = _hexas(s, cid)
        assert m["albaran"]["baja"] is None and m["albaran"]["posible_misma_venta"]
        # Una baja de siempre (retirada) sí cuenta en «tiene baja».
        parque.dar_baja(s, s.get(Company, cid), clave=alb, motivo="En un contenedor", por="x")
        assert _con_baja(s)


def test_contestar_sobre_una_baja_de_antes_no_la_toca(session_factory) -> None:  # noqa: F811
    # Antes, el aviso decía «da de baja este albarán»: quedó como retirada. Se
    # puede contestar; la baja de Bart sigue intacta y vuelve si se deshace.
    cid = _cargar(session_factory)
    with session_factory() as s:
        alb, factura = _aviso(s, cid)
        parque.dar_baja(s, s.get(Company, cid), clave=alb, motivo="Repetido", por="bart")
        assert _hexas(s, cid)["albaran"]["baja"]["tipo"] is None
        parque.contestar_misma_venta(s, s.get(Company, cid), clave=alb, factura=factura,
                                     es_misma=True, por="otra@example.com")
        assert _hexas(s, cid)["albaran"]["baja"]["tipo"] == parque.MISMA_VENTA
        assert not _con_baja(s)
        b = s.get(ParqueBaja, alb)
        assert (b.motivo, b.por) == ("Repetido", "bart")
        parque.deshacer_misma_venta(s, s.get(Company, cid), clave=alb, factura=factura)
        baja = _hexas(s, cid)["albaran"]["baja"]
        assert (baja["tipo"], baja["motivo"], baja["por"]) == (None, "Repetido", "bart")


def test_no_son_dos_compras_quita_el_aviso_de_ese_par(session_factory) -> None:  # noqa: F811
    cid = _cargar(session_factory)
    with session_factory() as s:
        alb, factura = _aviso(s, cid)
        empresa = s.get(Company, cid)
        parque.contestar_misma_venta(s, empresa, clave=alb, factura=factura, es_misma=False,
                                     por="bart@example.com")
        m = _hexas(s, cid)
        assert m["albaran"]["posible_misma_venta"] is None and m["albaran"]["baja"] is None
        assert [(d["numero"], d["por"]) for d in m["albaran"]["dos_compras"]] == [
            (FACTURA, "bart@example.com")]
        assert m["_p"]["activas"] == 2
        # Se puede cambiar de idea («sí») y volver a «no»: una respuesta por par.
        parque.contestar_misma_venta(s, empresa, clave=alb, factura=factura, es_misma=True,
                                     por="x")
        assert _hexas(s, cid)["_p"]["activas"] == 1
        parque.contestar_misma_venta(s, empresa, clave=alb, factura=factura, es_misma=False,
                                     por="x")
        assert _hexas(s, cid)["_p"]["activas"] == 2
        assert len(list(s.scalars(select(ParqueMismaVenta)))) == 1
        # Deshacerlo devuelve el aviso; dos veces, no.
        parque.deshacer_misma_venta(s, empresa, clave=alb, factura=factura)
        assert _hexas(s, cid)["albaran"]["posible_misma_venta"]["clave"] == factura
        with pytest.raises(LookupError):
            parque.deshacer_misma_venta(s, empresa, clave=alb, factura=factura)
        # Una factura que no es la del aviso, no.
        with pytest.raises(LookupError):
            parque.contestar_misma_venta(s, empresa, clave=alb, factura="2022|factura|1|9|1|X",
                                         es_misma=True, por="x")


def test_si_despues_se_emparejan_la_factura_sigue_contando(session_factory) -> None:  # noqa: F811
    # Revisión: «sí» y luego el año se recarga con el enlace (la factura apunta
    # al albarán). La respuesta era del albarán: no pasa a la factura.
    cid = _cargar(session_factory)
    with session_factory() as s:
        alb, factura = _aviso(s, cid)
        parque.contestar_misma_venta(s, s.get(Company, cid), clave=alb, factura=factura,
                                     es_misma=True, por="x")
        d = _datos(referencia="Pedido figurante 23", precio=10, fecha=None, enlace="directo")
        historico.cargar(s, FactusolFalso(d), ["2023", "2022"], en_curso="2026")
        m = _hexas(s, cid)
        assert "albaran" not in m and len(m["facturas"]) == 1
        assert m["facturas"][0]["baja"] is None
        assert m["_p"]["activas"] == 1 and _activas_del_cliente(s) == 1


@pytest.mark.parametrize("fecha", [None, "2023-09-01T00:00:00"])
def test_envio_de_sustitucion_a_0_sale_propuesto(session_factory, fecha) -> None:  # noqa: F811
    # A 0 € y «SUBSTITUCIÓN» en la referencia: ya resuelto, también lejos de
    # la factura (una sustitución llega cuando falla la máquina).
    cid = _cargar(session_factory, referencia=SUSTITUCION, precio=0, fecha=fecha)
    with session_factory() as s:
        m = _hexas(s, cid)
        pm, baja = m["albaran"]["posible_misma_venta"], m["albaran"]["baja"]
        assert pm["propuesta"] == "sustitucion" and pm["albaran"]["importe"] == 0.0
        assert baja["propuesta"] is True and baja["tipo"] == parque.MISMA_VENTA
        assert baja["motivo"] == parque.MOTIVO_SUSTITUCION and baja["duplica"]["numero"] == FACTURA
        assert m["_p"]["activas"] == 1 and _activas_del_cliente(s) == 1
        assert not _con_baja(s)
        assert s.scalar(select(ParqueMismaVenta.albaran)) is None      # propuesto: no escribe
        # «Sí» lo deja escrito; «No» lo devuelve al parque.
        empresa = s.get(Company, cid)
        parque.contestar_misma_venta(s, empresa, clave=m["albaran"]["clave"],
                                     factura=pm["clave"], es_misma=True, por="x")
        assert _hexas(s, cid)["albaran"]["misma_venta"]["numero"] == FACTURA
        parque.contestar_misma_venta(s, empresa, clave=m["albaran"]["clave"],
                                     factura=pm["clave"], es_misma=False, por="x")
        m = _hexas(s, cid)
        assert m["albaran"]["baja"] is None and m["albaran"]["posible_misma_venta"] is None
        assert m["_p"]["activas"] == 2


@pytest.mark.parametrize(("referencia", "precio"), [("Pedido figurante 23", 0),
                                                   ("Sustitución figurante", 10)])
def test_sin_las_dos_cosas_no_se_propone(session_factory, referencia, precio) -> None:  # noqa: F811,E501
    # A 0 € sin «sustitución», o «sustitución» con precio: se pregunta.
    cid = _cargar(session_factory, referencia=referencia, precio=precio)
    with session_factory() as s:
        m = _hexas(s, cid)
        assert m["albaran"]["posible_misma_venta"]["propuesta"] is None
        assert m["albaran"]["baja"] is None


def test_no_se_propone_contra_una_factura_retirada(session_factory) -> None:  # noqa: F811
    # Revisión: la máquina de la factura se averió y Bart la dio de baja; la
    # sustitución es la que cuenta. No se propone: el cliente no se queda sin máquina.
    cid = _cargar(session_factory, referencia=SUSTITUCION, precio=0, fecha="2023-09-01T00:00:00")
    with session_factory() as s:
        factura = _hexas(s, cid)["factura"]["clave"]
        parque.dar_baja(s, s.get(Company, cid), clave=factura, motivo="Averiada", por="bart")
        m = _hexas(s, cid)
        assert m["albaran"]["posible_misma_venta"] is None and m["albaran"]["baja"] is None
        assert m["_p"]["activas"] == 1 and _activas_del_cliente(s) == 1


def test_un_no_a_la_propuesta_no_salta_a_otra_factura(session_factory) -> None:  # noqa: F811
    # Revisión: dos facturas anteriores del modelo; «no» a una no la propone
    # con la otra.
    def otra_factura(d: dict[str, Any]) -> None:
        d["2022"]["F_FAC"].append(_fac(122001, CLI_HEXA, "Grabados Figurante",
                                       "2022-03-01T00:00:00", "1"))
        d["2022"]["F_LFA"].append(_lfa(122001, 1, HEXA, "HEXA 60W", 1, 9000, "1"))

    cid = _cargar(session_factory, referencia=SUSTITUCION, precio=0, fecha="2023-09-01T00:00:00",
                  retocar=otra_factura)
    with session_factory() as s:
        alb, factura = _aviso(s, cid)
        assert _hexas(s, cid)["albaran"]["posible_misma_venta"]["numero"] == FACTURA
        parque.contestar_misma_venta(s, s.get(Company, cid), clave=alb, factura=factura,
                                     es_misma=False, por="x")
        m = _hexas(s, cid)
        assert m["albaran"]["posible_misma_venta"] is None and m["albaran"]["baja"] is None
        assert m["_p"]["activas"] == 3


def test_la_propuesta_no_casa_con_una_factura_posterior(session_factory) -> None:  # noqa: F811
    # Revisión: la sustitución es de una máquina de antes (no consta) y la
    # factura cercana es POSTERIOR: una compra nueva. Se pregunta, no se propone.
    cid = _cargar(session_factory, referencia=SUSTITUCION, precio=0, fecha="2022-12-01T00:00:00")
    with session_factory() as s:
        m = _hexas(s, cid)
        assert m["albaran"]["posible_misma_venta"]["propuesta"] is None
        assert m["albaran"]["baja"] is None and m["_p"]["activas"] == 2


def test_con_abonos_del_modelo_no_se_propone(session_factory) -> None:  # noqa: F811
    # Revisión: con un abono del modelo no se sabe qué venta anula.
    def abono(d: dict[str, Any]) -> None:
        d["2023"].setdefault("F_FAC", []).append(_fac(230900, CLI_HEXA, "Grabados Figurante",
                                                      "2023-10-01T00:00:00", "1"))
        d["2023"].setdefault("F_LFA", []).append(_lfa(230900, 1, HEXA, "HEXA 60W", -1, 9000, "1"))
        # Y una compra nueva después: el abono anula esta (la más reciente).
        d["2023"]["F_FAC"].append(_fac(231100, CLI_HEXA, "Grabados Figurante",
                                       "2023-11-01T00:00:00", "1"))
        d["2023"]["F_LFA"].append(_lfa(231100, 1, HEXA, "HEXA 60W", 1, 9000, "1"))

    cid = _cargar(session_factory, referencia=SUSTITUCION, precio=0, fecha="2023-09-01T00:00:00",
                  retocar=abono)
    with session_factory() as s:
        m = _hexas(s, cid)
        # El abono anula la compra nueva (la más reciente), como siempre; la
        # sustitución no se propone contra la primera factura: cuentan las dos.
        assert m["albaran"]["posible_misma_venta"] is None and m["albaran"]["baja"] is None
        assert m["_p"]["activas"] == 2


def test_api_contestar(http, session_factory) -> None:  # noqa: F811
    cid = _cargar(session_factory)
    with session_factory() as s:
        alb, factura = _aviso(s, cid)
    url = f"/api/erp/parque/empresas/{cid}/misma-venta"
    cuerpo = {"clave": alb, "factura": factura, "es_misma": True}
    assert http.post(url, json=cuerpo, headers=auth_headers(http, "user")).status_code == 403
    assert http.post(url, json={"clave": alb, "factura": factura},
                     headers=auth_headers(http, "pedidos")).status_code == 422
    r = http.post(url, json={**cuerpo, "factura": "2022|factura|1|9|1|X"},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "aviso_no_existe"
    p = http.post(url, json=cuerpo, headers=auth_headers(http, "pedidos")).json()
    alb_fila = next(x for x in p["maquinas"] if x["clave"] == alb)
    assert alb_fila["baja"]["tipo"] == "misma_venta" and p["activas"] == 1
    p = http.post(url, json={**cuerpo, "es_misma": False},
                  headers=auth_headers(http, "pedidos")).json()
    assert p["activas"] == 2
    p = http.post(f"{url}/deshacer", json={"clave": alb, "factura": factura},
                  headers=auth_headers(http, "pedidos")).json()
    assert next(x for x in p["maquinas"] if x["clave"] == alb)["posible_misma_venta"]
