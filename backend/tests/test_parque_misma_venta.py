"""«¿Misma venta?» con respuesta (1/11/2026): el aviso enseña importe y
referencia, «sí» lo saca del parque como duplicado (no como retirada), «no»
quita el aviso de ese par para siempre, y un envío de sustitución a 0 € sale
ya propuesto. Figurantes."""
from __future__ import annotations

from typing import Any

import pytest

from app.erp import historico, parque
from app.erp import parque_consultas as pc
from app.erp.models import ParqueBaja, ParqueDosCompras
from app.models.crm import Company
from tests._test_helpers import auth_headers
from tests.test_erp_historico import FactusolFalso, http, session_factory  # noqa: F401
from tests.test_erp_parque import CLI_HEXA, HEXA, _datos_hexa, _marcar_maquinas

FACTURA = "1-123021"


def _cargar(session_factory, *, referencia: str = "Pedido figurante 23",  # noqa: F811
            precio: float = 10, fecha: str | None = None) -> str:
    """Factura 1-123021 (15/12/2022, 9.000 €) y un albarán suelto 3-230001 del
    mismo modelo, sin enlace (2/1/2023, salvo `fecha`)."""
    d = _datos_hexa(enlace="ninguno")
    alb = d["2023"]["F_ALB"][0]
    alb["REFALB"] = referencia
    if fecha:
        alb["FECALB"] = fecha
    d["2023"]["F_LAL"][0].update({"PRELAL": precio, "TOTLAL": precio})
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026", "2023", "2022"], en_curso="2026")
        from app.erp import historico_clasificacion as cl

        cl.clasificar(s, "LAS", {"clase": "maquina"}, por="x")
        _marcar_maquinas(s)
        empresa = Company(name="Grabados Figurante", factusol_company_id=CLI_HEXA)
        s.add(empresa)
        s.commit()
        return empresa.id


def _hexas(s, company_id: str) -> dict[str, dict[str, Any]]:
    p = parque.parque(s, s.get(Company, company_id))
    return {m["origen"]: m for m in p["maquinas"] if m["codart"] == HEXA} | {"_p": p}


def _activas_del_cliente(s) -> int:
    return len([m for m in pc._parque_activo(s).get(CLI_HEXA, []) if m["codart"] == HEXA])


def test_el_aviso_trae_importe_y_referencia(session_factory) -> None:  # noqa: F811
    cid = _cargar(session_factory)
    with session_factory() as s:
        pm = _hexas(s, cid)["albaran"]["posible_misma_venta"]
    assert pm["numero"] == FACTURA and pm["clave"].startswith("2022|factura|1|123021|")
    assert pm["importe"] == 9000.0 and pm["referencia"] == "Pedido figurante 123021"
    assert pm["albaran"] == {"importe": 10.0, "referencia": "Pedido figurante 23"}
    assert pm["propuesta"] is None


def test_si_es_la_misma_venta_sale_como_duplicado_no_como_retirada(session_factory) -> None:  # noqa: F811,E501
    cid = _cargar(session_factory)
    with session_factory() as s:
        m = _hexas(s, cid)
        alb, factura = m["albaran"]["clave"], m["albaran"]["posible_misma_venta"]["clave"]
        assert m["_p"]["activas"] == 2 and _activas_del_cliente(s) == 2
        parque.contestar_misma_venta(s, s.get(Company, cid), clave=alb, factura=factura,
                                     es_misma=True, por="bart@example.com")
        m = _hexas(s, cid)
        baja = m["albaran"]["baja"]
        assert baja["tipo"] == parque.MISMA_VENTA and baja["duplica"]["numero"] == FACTURA
        assert baja["por"] == "bart@example.com" and baja["propuesta"] is False
        assert m["_p"]["activas"] == 1 and _activas_del_cliente(s) == 1
        # No es una máquina retirada: no cuenta en «tiene baja».
        con_baja: set[str] = set()
        pc._parque_activo(s, con_baja=con_baja)
        assert CLI_HEXA not in con_baja
        # Deshacerlo (volver a ponerla en servicio) devuelve el aviso.
        parque.reactivar(s, s.get(Company, cid), clave=alb)
        m = _hexas(s, cid)
        assert m["albaran"]["baja"] is None and m["albaran"]["posible_misma_venta"]
        # Una baja de siempre (retirada) sí cuenta en «tiene baja».
        parque.dar_baja(s, s.get(Company, cid), clave=alb, motivo="En un contenedor", por="x")
        pc._parque_activo(s, con_baja=con_baja)
        assert CLI_HEXA in con_baja


def test_una_baja_de_antes_se_puede_contestar(session_factory) -> None:  # noqa: F811
    # Antes, el aviso decía «da de baja este albarán»: la baja quedó como
    # retirada. Contestar «sí» la pasa a duplicado y conserva el motivo.
    cid = _cargar(session_factory)
    with session_factory() as s:
        m = _hexas(s, cid)
        alb, factura = m["albaran"]["clave"], m["albaran"]["posible_misma_venta"]["clave"]
        parque.dar_baja(s, s.get(Company, cid), clave=alb, motivo="Repetido", por="x")
        assert _hexas(s, cid)["albaran"]["baja"]["tipo"] is None
        parque.contestar_misma_venta(s, s.get(Company, cid), clave=alb, factura=factura,
                                     es_misma=True, por="bart@example.com")
        baja = _hexas(s, cid)["albaran"]["baja"]
        assert (baja["tipo"], baja["motivo"]) == (parque.MISMA_VENTA, "Repetido")


def test_no_son_dos_compras_quita_el_aviso_de_ese_par(session_factory) -> None:  # noqa: F811
    cid = _cargar(session_factory)
    with session_factory() as s:
        m = _hexas(s, cid)
        alb, factura = m["albaran"]["clave"], m["albaran"]["posible_misma_venta"]["clave"]
        parque.contestar_misma_venta(s, s.get(Company, cid), clave=alb, factura=factura,
                                     es_misma=False, por="bart@example.com")
        m = _hexas(s, cid)
        assert m["albaran"]["posible_misma_venta"] is None and m["albaran"]["baja"] is None
        assert [(d["numero"], d["por"]) for d in m["albaran"]["dos_compras"]] == [
            (FACTURA, "bart@example.com")]
        assert m["_p"]["activas"] == 2
        # Contestar otra vez el mismo aviso ya no se puede: no está.
        with pytest.raises(LookupError):
            parque.contestar_misma_venta(s, s.get(Company, cid), clave=alb, factura=factura,
                                         es_misma=True, por="x")
        # Deshacerlo devuelve el aviso.
        parque.deshacer_dos_compras(s, s.get(Company, cid), clave=alb, factura=factura)
        assert _hexas(s, cid)["albaran"]["posible_misma_venta"]["clave"] == factura
        assert s.get(ParqueDosCompras, (alb, factura)) is None
        with pytest.raises(LookupError):
            parque.deshacer_dos_compras(s, s.get(Company, cid), clave=alb, factura=factura)


def test_no_tras_un_si_lo_vuelve_a_poner_en_servicio(session_factory) -> None:  # noqa: F811
    cid = _cargar(session_factory)
    with session_factory() as s:
        m = _hexas(s, cid)
        alb, factura = m["albaran"]["clave"], m["albaran"]["posible_misma_venta"]["clave"]
        empresa = s.get(Company, cid)
        parque.contestar_misma_venta(s, empresa, clave=alb, factura=factura, es_misma=True,
                                     por="x")
        parque.contestar_misma_venta(s, empresa, clave=alb, factura=factura, es_misma=False,
                                     por="x")
        assert s.get(ParqueBaja, alb) is None
        assert _hexas(s, cid)["_p"]["activas"] == 2
        # Una factura que no es la del aviso, no.
        with pytest.raises(LookupError):
            parque.contestar_misma_venta(s, empresa, clave=alb, factura="2022|factura|1|9|1|X",
                                         es_misma=True, por="x")


@pytest.mark.parametrize("fecha", [None, "2023-09-01T00:00:00"])
def test_envio_de_sustitucion_a_0_sale_propuesto(session_factory, fecha) -> None:  # noqa: F811
    # A 0 € y «SUBSTITUCIÓN» en la referencia: ya resuelto, también lejos de
    # la factura (una sustitución llega cuando falla la máquina).
    cid = _cargar(session_factory, referencia="ENVÍO MÁQUINA SUBSTITUCIÓN", precio=0,
                  fecha=fecha)
    with session_factory() as s:
        m = _hexas(s, cid)
        pm, baja = m["albaran"]["posible_misma_venta"], m["albaran"]["baja"]
        assert pm["propuesta"] == "sustitucion" and pm["albaran"]["importe"] == 0.0
        assert baja["propuesta"] is True and baja["tipo"] == parque.MISMA_VENTA
        assert baja["motivo"] == parque.MOTIVO_SUSTITUCION and baja["duplica"]["numero"] == FACTURA
        assert m["_p"]["activas"] == 1 and _activas_del_cliente(s) == 1
        con_baja: set[str] = set()
        pc._parque_activo(s, con_baja=con_baja)
        assert CLI_HEXA not in con_baja
        assert s.get(ParqueBaja, m["albaran"]["clave"]) is None          # propuesto: no escribe
        # «Sí» lo deja escrito; «No» lo devuelve al parque.
        empresa = s.get(Company, cid)
        parque.contestar_misma_venta(s, empresa, clave=m["albaran"]["clave"],
                                     factura=pm["clave"], es_misma=True, por="x")
        assert s.get(ParqueBaja, m["albaran"]["clave"]).motivo == parque.MOTIVO_SUSTITUCION
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


def test_api_contestar(http, session_factory) -> None:  # noqa: F811
    cid = _cargar(session_factory)
    with session_factory() as s:
        m = _hexas(s, cid)
        alb, factura = m["albaran"]["clave"], m["albaran"]["posible_misma_venta"]["clave"]
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
