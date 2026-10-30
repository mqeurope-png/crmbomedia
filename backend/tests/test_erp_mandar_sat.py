"""«Mandar a SAT» y el albarán de un pedido sin duplicarlo (30/10/2026).

- Desde una factura: nunca un albarán nuevo; se vincula el que la factura
  apunta en sus líneas (`DOCLFA='A'`) o se queda sin él y se dice.
- Desde una proforma: sigue creándolo; desde un albarán: sigue sin crearlo.
- Vincular y desvincular un albarán desde la ficha del pedido.
- El cobro no frena la preparación, tampoco en un pedido web.
Figurantes (Acme SL)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from sqlalchemy import select

from app.erp.models import HistoricoDocumento, HistoricoEjercicio, HistoricoLinea, Order
from app.erp.models.orders import OrderSource
from tests._test_helpers import auth_headers
from tests.test_erp_pedido_desde_factusol import (  # noqa: F401 — fixtures
    FakeClient,
    _alb,
    _lfa,
    _patched,
    _post_from_factusol,
    _tables,
    http,
    session_factory,
)


def _tablas(*, enlace: bool) -> dict:
    """La factura 5-8 sale del albarán 5-8 (enlace en sus líneas) o se
    tecleó sin él."""
    t = _tables()
    if enlace:
        t["F_LFA"] = [{**ln, "DOCLFA": "A", "DTPLFA": "5", "DCOLFA": 8} for ln in t["F_LFA"]]
    return t


def _enqueue():
    return patch("app.integrations.factusol.jobs.enqueue_create_order_albaran",
                 return_value="job-1")


def _mandar(c, oid: str, **body):
    return c.post(f"/api/erp/orders/{oid}/mandar-sat", json={"confirm": True, **body},
                  headers=auth_headers(c, "pedidos"))


def _crear_desde(c, fake, doc_type: str, codigo: int = 8) -> dict:
    with _patched(fake):
        r = _post_from_factusol(c, {"doc_type": doc_type, "serie": 5, "codigo": codigo})
    assert r.status_code == 201, r.text
    return r.json()


# --- parte 3: desde una factura, ningún albarán nuevo ------------------------------------


def test_desde_factura_y_mandado_al_sat_no_crea_albaran_y_vincula_el_suyo(
    http, session_factory,  # noqa: F811
) -> None:
    fake = FakeClient(_tablas(enlace=True))
    pedido = _crear_desde(http, fake, "facturas")
    # Al crearlo ya se vincula el albarán de la factura.
    assert pedido["albaran_de_factura"]["estado"] == "vinculado"
    assert pedido["factusol_albaran_number"] == "5-000008"
    with _patched(fake), _enqueue() as enq:
        r = _mandar(http, pedido["id"])
        # Y crear uno a mano tampoco se puede.
        r2 = http.post(f"/api/erp/orders/{pedido['id']}/albaran", json={},
                       headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cola"] == "entrado"
    assert body["albaran"]["crear"] is False
    assert body["order"]["preparation_status"] == "in_queue"
    assert body["order"]["factusol_albaran_number"] == "5-000008"
    assert r2.status_code == 409
    enq.assert_not_called()
    assert fake.writes == []


def test_factura_sin_enlace_se_queda_sin_albaran_y_lo_dice(http, session_factory) -> None:  # noqa: F811
    fake = FakeClient(_tablas(enlace=False))
    pedido = _crear_desde(http, fake, "facturas")
    assert pedido["albaran_de_factura"]["estado"] == "sin_enlace"
    with _patched(fake), _enqueue() as enq:
        r = _mandar(http, pedido["id"])
        r2 = http.post(f"/api/erp/orders/{pedido['id']}/albaran", json={},
                       headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["albaran"]["estado"] == "sin_enlace"
    assert body["albaran"]["crear"] is False
    assert body["order"]["factusol_albaran_number"] is None
    assert body["order"]["mandar_sat"]["factura_sin_albaran"] is True
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "factura_source_no_albaran"
    enq.assert_not_called()
    assert fake.writes == []


def test_la_factura_volcada_manda_sobre_la_lectura_en_vivo(http, session_factory) -> None:  # noqa: F811
    # Volcada con el enlace (0157): no hace falta leer FACTUSOL en vivo.
    with session_factory() as s:
        s.add(HistoricoEjercicio(ejercicio="2026", estado="cargado", con_enlaces=True))
        doc = HistoricoDocumento(ejercicio="2026", tipo="factura", serie="5", codigo=8,
                                 codcli="55555")
        s.add(doc)
        s.flush()
        s.add(HistoricoLinea(documento_id=doc.id, ejercicio="2026", posicion=1,
                             codart="MBO", es_producto=True, origen_tipo="A",
                             origen_serie="5", origen_codigo=8))
        s.commit()
    pedido = _crear_desde(http, FakeClient(_tablas(enlace=False)), "facturas")
    assert pedido["factusol_albaran_number"] == "5-000008"


def test_desde_proforma_sigue_creando_su_albaran(http, session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        o = Order(external_source=OrderSource.FACTUSOL_PROFORMA, external_id="574",
                  order_number="PRO-000574", company_id="acme", total_amount=10,
                  currency="EUR", packing_json=(
                      '{"factusol_source": {"doc_type": "presupuestos", "serie": 1, '
                      '"codigo": 574}, "factusol_payment": {"paid": false}}'))
        s.add(o)
        s.commit()
        oid = o.id
    with _patched(FakeClient(_tables())):
        r = _mandar(http, oid)
    assert r.status_code == 200, r.text
    # La ficha encola el albarán como siempre (convierte la proforma).
    assert r.json()["albaran"]["crear"] is True
    with _patched(FakeClient(_tables())), _enqueue() as enq:
        r2 = http.post(f"/api/erp/orders/{oid}/albaran", json={},
                       headers=auth_headers(http, "pedidos"))
    assert r2.status_code == 202, r2.text
    enq.assert_called_once()


def test_desde_albaran_sigue_sin_crear_otro(http, session_factory) -> None:  # noqa: F811
    fake = FakeClient(_tables())
    pedido = _crear_desde(http, fake, "albaranes")
    assert pedido["factusol_albaran_number"] == "5-000008"
    with _patched(fake), _enqueue() as enq:
        r = _mandar(http, pedido["id"])
    assert r.status_code == 200, r.text
    assert r.json()["albaran"]["crear"] is False
    enq.assert_not_called()
    assert fake.writes == []


def test_la_linea_de_vida_dice_mandar_a_sat_en_vez_de_aprobar(http, session_factory) -> None:  # noqa: F811
    pedido = _crear_desde(http, FakeClient(_tablas(enlace=True)), "facturas")
    wf = pedido["workflow"]
    assert wf["next_action"] == "mandar_sat"
    assert next(p for p in wf["steps"] if p["key"] == "aprobado")["label"] == "Mandado al SAT"


# --- parte 4: vincular un albarán desde la ficha --------------------------------------------


def _historico_albaranes(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        s.add(HistoricoEjercicio(ejercicio="2026", estado="cargado", con_enlaces=True))
        for cod, dia in ((31, 1), (32, 15), (33, 20)):
            doc = HistoricoDocumento(ejercicio="2026", tipo="albaran", serie="5", codigo=cod,
                                     codcli="55555", fecha=date(2026, 9, dia),
                                     cliente_nombre="Acme SL")
            s.add(doc)
            s.flush()
            s.add(HistoricoLinea(documento_id=doc.id, ejercicio="2026", posicion=1,
                                 codart="MBO", es_producto=True, origen_tipo="P",
                                 origen_serie="1", origen_codigo=500 + cod))
        # Otro cliente: no se propone (sí si se busca su número).
        s.add(HistoricoDocumento(ejercicio="2026", tipo="albaran", serie="5", codigo=40,
                                 codcli="77777", fecha=date(2026, 9, 25)))
        s.commit()


def test_vincular_y_desvincular_un_albaran_desde_la_ficha(http, session_factory) -> None:  # noqa: F811
    _historico_albaranes(session_factory)
    fake = FakeClient(_tablas(enlace=False))
    pedido = _crear_desde(http, fake, "facturas")
    oid = pedido["id"]
    url = f"/api/erp/orders/{oid}"
    with _patched(fake):
        cand = http.get(f"{url}/albaranes-candidatos", headers=auth_headers(http, "pedidos"))
    assert cand.status_code == 200, cand.text
    items = cand.json()["items"]
    # Los del cliente, los más recientes primero, con su origen; el 5-8 es de
    # hoy (aún sin volcar) y sale leído en vivo, en su sitio por fecha.
    assert [i["numero"] for i in items] == ["5-000033", "5-000032", "5-000008", "5-000031"]
    assert items[0]["viene_de"] == "Proforma 1-000533"
    assert "5-000040" not in {i["numero"] for i in items}
    # El buscador encuentra el de otro cliente por número.
    with _patched(fake):
        busca = http.get(f"{url}/albaranes-candidatos", params={"q": "5-40"},
                         headers=auth_headers(http, "pedidos")).json()["items"]
    assert [i["numero"] for i in busca] == ["5-000040"]

    with _patched(fake):
        r = http.post(f"{url}/albaran/vincular", json={"confirm": True, "serie": "5",
                                                       "codigo": 32},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["factusol_albaran_number"] == "5-000032"
    assert fake.writes == []
    # Y se desvincula (por si era el que no era).
    r = http.post(f"{url}/unlink-document", json={"kind": "albaran", "confirm": True},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    with session_factory() as s:
        assert s.get(Order, oid).factusol_albaran_number is None


def test_un_albaran_vinculado_a_otro_pedido_no_se_ofrece(http, session_factory) -> None:  # noqa: F811
    _historico_albaranes(session_factory)
    with session_factory() as s:
        s.add(Order(external_source=OrderSource.MANUAL, order_number="MAN-1",
                    company_id="acme", total_amount=1, currency="EUR",
                    factusol_albaran_number="5-000033"))
        s.commit()
    fake = FakeClient(_tablas(enlace=False))
    pedido = _crear_desde(http, fake, "facturas")
    url = f"/api/erp/orders/{pedido['id']}"
    with _patched(fake):
        items = http.get(f"{url}/albaranes-candidatos",
                         headers=auth_headers(http, "pedidos")).json()["items"]
        r = http.post(f"{url}/albaran/vincular",
                      json={"confirm": True, "serie": "5", "codigo": 33},
                      headers=auth_headers(http, "pedidos"))
    assert "5-000033" not in {i["numero"] for i in items}
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "en_otro_pedido"


def test_el_albaran_de_su_factura_va_primero(http, session_factory) -> None:  # noqa: F811
    _historico_albaranes(session_factory)
    t = _tablas(enlace=False)
    # La factura 5-8 apunta al albarán 5-31 (el más antiguo del cliente).
    t["F_LFA"] = [{**ln, "DOCLFA": "A", "DTPLFA": "5", "DCOLFA": 31} for ln in t["F_LFA"]]
    fake = FakeClient(t)
    with session_factory() as s:
        # Ocupado por otro pedido al crear: no se vincula solo.
        s.add(Order(external_source=OrderSource.MANUAL, order_number="MAN-2",
                    company_id="acme", total_amount=1, currency="EUR",
                    factusol_albaran_number="5-000031"))
        s.commit()
    pedido = _crear_desde(http, fake, "facturas")
    assert pedido["albaran_de_factura"]["estado"] == "en_otro_pedido"
    with session_factory() as s:
        s.scalars(select(Order).where(Order.order_number == "MAN-2")).one() \
            .factusol_albaran_number = None
        s.commit()
    with _patched(fake):
        items = http.get(f"/api/erp/orders/{pedido['id']}/albaranes-candidatos",
                         headers=auth_headers(http, "pedidos")).json()["items"]
    assert items[0]["numero"] == "5-000031" and items[0]["de_la_factura"] is True


# --- parte 2: el cobro no frena el envío ---------------------------------------------------


def _transicion(c, oid: str, rol: str, to: str):
    return c.post(f"/api/erp/orders/{oid}/transitions",
                  json={"domain": "preparation", "to_status": to},
                  headers=auth_headers(c, rol))


def test_un_pedido_sin_cobrar_entra_en_la_cola_y_se_prepara(http, session_factory) -> None:  # noqa: F811
    pedido = _crear_desde(http, FakeClient(_tablas(enlace=True)), "facturas")
    assert pedido["payment_status"] != "paid"
    with _patched(FakeClient(_tablas(enlace=True))):
        assert _mandar(http, pedido["id"]).status_code == 200
    r = _transicion(http, pedido["id"], "sat", "preparing")
    assert r.status_code == 200, r.text


def test_un_pedido_web_sin_pagar_tambien(http, session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        o = Order(external_source=OrderSource.WOOCOMMERCE, external_id="w-1",
                  order_number="WEB-1", company_id="acme", total_amount=50,
                  currency="EUR", payment_status="pending", preparation_status="in_queue")
        s.add(o)
        s.commit()
        oid = o.id
    r = _transicion(http, oid, "sat", "preparing")
    assert r.status_code == 200, r.text


def test_sin_confirmar_no_hace_nada(http, session_factory) -> None:  # noqa: F811
    pedido = _crear_desde(http, FakeClient(_tablas(enlace=True)), "facturas")
    r = http.post(f"/api/erp/orders/{pedido['id']}/mandar-sat", json={},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 400
    with session_factory() as s:
        assert s.get(Order, pedido["id"]).preparation_status == "pending_review"


def test_el_correo_sin_permiso_de_sat_no_mueve_nada(http, session_factory) -> None:  # noqa: F811
    pedido = _crear_desde(http, FakeClient(_tablas(enlace=True)), "facturas")
    r = http.post(f"/api/erp/orders/{pedido['id']}/mandar-sat", json={
        "confirm": True, "correo": {"to": ["x@example.com"], "subject": "s",
                                    "body_text": "b", "from_alias": "a@b.c",
                                    "include_factura": True}},
        headers=auth_headers(http, "pedidos"))
    # Con factura no: el correo al taller va sin factura (y nada se mueve).
    assert r.status_code in (403, 422)
    with session_factory() as s:
        assert s.get(Order, pedido["id"]).preparation_status == "pending_review"


# --- lo que encontró la primera revisión ---------------------------------------------------


def test_una_factura_de_otro_anio_con_el_mismo_numero_no_se_usa(http, session_factory) -> None:  # noqa: F811
    # La 5-8 de 2025 (volcada) venía del albarán 5-31; la de hoy no apunta a ninguno.
    with session_factory() as s:
        s.add(HistoricoEjercicio(ejercicio="2025", estado="cargado", con_enlaces=True))
        doc = HistoricoDocumento(ejercicio="2025", tipo="factura", serie="5", codigo=8,
                                 codcli="55555")
        s.add(doc)
        s.flush()
        s.add(HistoricoLinea(documento_id=doc.id, ejercicio="2025", posicion=1, codart="MBO",
                             es_producto=True, origen_tipo="A", origen_serie="5",
                             origen_codigo=31))
        s.commit()
    pedido = _crear_desde(http, FakeClient(_tablas(enlace=False)), "facturas")
    assert pedido["albaran_de_factura"]["estado"] == "sin_enlace"
    assert pedido["factusol_albaran_number"] is None


def test_un_albaran_de_otro_ejercicio_no_se_vincula_solo(http, session_factory) -> None:  # noqa: F811
    t = _tablas(enlace=False)
    # La factura apunta al albarán 5-77, que no está en el ejercicio en curso.
    t["F_LFA"] = [{**ln, "DOCLFA": "A", "DTPLFA": "5", "DCOLFA": 77} for ln in t["F_LFA"]]
    pedido = _crear_desde(http, FakeClient(t), "facturas")
    assert pedido["albaran_de_factura"]["estado"] == "otro_ejercicio"
    assert pedido["factusol_albaran_number"] is None


def test_un_pedido_desde_albaran_desvinculado_no_crea_otro(http, session_factory) -> None:  # noqa: F811
    fake = FakeClient(_tables())
    pedido = _crear_desde(http, fake, "albaranes")
    url = f"/api/erp/orders/{pedido['id']}"
    r = http.post(f"{url}/unlink-document", json={"kind": "albaran", "confirm": True},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    with _patched(fake), _enqueue() as enq:
        m = _mandar(http, pedido["id"])
        r2 = http.post(f"{url}/albaran", json={}, headers=auth_headers(http, "pedidos"))
    assert m.json()["albaran"]["crear"] is False
    assert m.json()["order"]["mandar_sat"]["albaran_solo_vincular"] is True
    assert r2.status_code == 409
    enq.assert_not_called()
    assert fake.writes == []


def test_un_pedido_quitado_de_las_listas_no_se_manda(http, session_factory) -> None:  # noqa: F811
    from datetime import UTC, datetime

    pedido = _crear_desde(http, FakeClient(_tablas(enlace=True)), "facturas")
    with session_factory() as s:
        s.get(Order, pedido["id"]).seguimiento_excluded_at = datetime.now(UTC)
        s.commit()
    with _patched(FakeClient(_tablas(enlace=True))):
        r = _mandar(http, pedido["id"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "excluded"


# --- traídos a mano, deshacer el envío al SAT y la línea de vida (31/10/2026) --------------


def _traido(session_factory, **over) -> str:  # noqa: F811
    from datetime import UTC, datetime

    with session_factory() as s:
        o = Order(external_source=OrderSource.WOOCOMMERCE, external_id="w-17",
                  order_number="WEB-FIG-17", company_id="acme", total_amount=50,
                  currency="EUR", payment_status="pending",
                  manual_web_import_at=datetime.now(UTC), **over)
        s.add(o)
        s.commit()
        return o.id


def test_un_web_traido_a_mano_se_manda_al_sat_con_su_modal(http, session_factory) -> None:  # noqa: F811
    oid = _traido(session_factory)
    d = http.get(f"/api/erp/orders/{oid}", headers=auth_headers(http, "pedidos")).json()
    assert d["workflow"]["next_action"] == "mandar_sat"
    assert next(p for p in d["workflow"]["steps"] if p["key"] == "aprobado")["label"] == \
        "Mandado al SAT"
    with _patched(FakeClient(_tables())), _enqueue() as enq:
        r = _mandar(http, oid)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["preparation_status"] == "in_queue"
    assert r.json()["albaran"]["crear"] is False      # web: lo crea WooCommerce
    enq.assert_not_called()


def test_deshacer_el_envio_al_sat_vuelve_a_por_revisar(http, session_factory) -> None:  # noqa: F811
    oid = _traido(session_factory)
    with _patched(FakeClient(_tables())):
        _mandar(http, oid)
    r = http.post(f"/api/erp/orders/{oid}/deshacer-sat",
                  json={"confirm": True, "motivo": "Aprobado sin querer"},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    assert o["preparation_status"] == "pending_review"
    assert o["approved_at"] is None
    assert o["workflow"]["next_action"] == "mandar_sat"
    assert any("Deshecho el envío al SAT" in (h["reason"] or "") for h in o["status_history"])
    # Y se puede volver a mandar.
    with _patched(FakeClient(_tables())):
        assert _mandar(http, oid).json()["cola"] == "entrado"


def test_no_se_deshace_si_el_taller_ya_empezo(http, session_factory) -> None:  # noqa: F811
    for estado, palabra in (("preparing", "preparándolo"), ("packed", "embalado")):
        oid = _traido(session_factory, preparation_status=estado)
        r = http.post(f"/api/erp/orders/{oid}/deshacer-sat", json={"confirm": True},
                      headers=auth_headers(http, "pedidos"))
        assert r.status_code == 409
        assert palabra in r.json()["detail"]["detail"]


def test_deshacer_vale_para_un_pedido_desde_factura(http, session_factory) -> None:  # noqa: F811
    pedido = _crear_desde(http, FakeClient(_tablas(enlace=True)), "facturas")
    with _patched(FakeClient(_tablas(enlace=True))):
        _mandar(http, pedido["id"])
    r = http.post(f"/api/erp/orders/{pedido['id']}/deshacer-sat", json={"confirm": True},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["order"]["preparation_status"] == "pending_review"
    # El albarán de la factura sigue vinculado: deshacer no toca FACTUSOL.
    assert r.json()["order"]["factusol_albaran_number"] == "5-000008"


def test_tras_deshacerlo_no_vuelve_a_entrar_solo_al_pagar(http, session_factory) -> None:  # noqa: F811
    from app.erp.sat_autoenqueue import enqueue_paid_order

    oid = _traido(session_factory)
    with _patched(FakeClient(_tables())):
        _mandar(http, oid)
    http.post(f"/api/erp/orders/{oid}/deshacer-sat", json={"confirm": True},
              headers=auth_headers(http, "pedidos"))
    with session_factory() as s:
        o = s.get(Order, oid)
        o.payment_status = "paid"
        assert enqueue_paid_order(s, o) is False
        assert o.preparation_status == "pending_review"


def test_la_linea_de_vida_marca_el_paso_de_la_accion(http, session_factory) -> None:  # noqa: F811
    # Sin pagar: «Pagado» nunca es el paso actual; el actual es el de la acción.
    oid = _traido(session_factory)
    wf = http.get(f"/api/erp/orders/{oid}", headers=auth_headers(http, "pedidos")).json()[
        "workflow"]
    ahora = [p["key"] for p in wf["steps"] if p["state"] == "now"]
    assert ahora == ["aprobado"]
    assert next(p for p in wf["steps"] if p["key"] == "pagado")["state"] == "pending"
    # Desde factura, sin albarán: toca enviar la factura y ese es el paso actual.
    pedido = _crear_desde(http, FakeClient(_tablas(enlace=False)), "facturas")
    with _patched(FakeClient(_tablas(enlace=False))):
        wf = _mandar(http, pedido["id"]).json()["order"]["workflow"]
    ahora = [p["key"] for p in wf["steps"] if p["state"] == "now"]
    assert wf["next_action"] == "enviar_factura" and ahora == ["factura_enviada"]
