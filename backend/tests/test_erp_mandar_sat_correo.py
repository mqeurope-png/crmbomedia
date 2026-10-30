"""«Mandar a SAT» con la casilla del correo al taller (30/10/2026): el pedido
entra en la Cola SAT y el correo sale, todo en una acción. Sin marcarla no
sale nada; si el correo falla, el pedido sigue en la cola y se reintenta.
Figurantes."""
from __future__ import annotations

from unittest.mock import patch

from app.erp.models import Order
from tests._test_helpers import auth_headers
from tests.test_erp_order_email import (  # noqa: F401 — fixtures
    ALIAS,
    SAT,
    _patch_send,
    _patched_factusol,
    _seed_alias,
    _seed_order,
    _set_sat_email,
    http,
    session_factory,
)


def _correo(**over) -> dict:
    return {"to": [SAT], "subject": "Pedido PRO-000574 — Duplicoder SL",
            "body_text": "Va para preparar.", "lang": "es", "from_alias": ALIAS, **over}


def _preparar(session_factory, http, **seed) -> str:  # noqa: F811
    with session_factory() as s:
        order = _seed_order(s, invoice=None, **seed)
        _seed_alias(s)
        oid = order.id
    _set_sat_email(http, SAT)
    return oid


def _mandar(c, oid: str, correo: dict | None):
    return c.post(f"/api/erp/orders/{oid}/mandar-sat",
                  json={"confirm": True, "correo": correo},
                  headers=auth_headers(c, "pedidos"))


def test_con_la_casilla_entra_en_la_cola_y_avisa_al_taller(http, session_factory) -> None:  # noqa: F811
    oid = _preparar(session_factory, http)
    send, _msg = _patch_send()
    with _patched_factusol(), send as sent:
        r = _mandar(http, oid, _correo())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cola"] == "entrado"
    assert body["correo"]["estado"] == "enviado"
    assert body["order"]["preparation_status"] == "in_queue"
    sent.assert_called_once()
    assert sent.call_args.kwargs["to"] == [SAT]
    # El mismo correo de siempre: con el albarán y sin factura.
    (adjunto,) = sent.call_args.kwargs["attachments"]
    assert "albar" in adjunto["filename"].lower()


def test_sin_la_casilla_no_sale_ningun_correo(http, session_factory) -> None:  # noqa: F811
    oid = _preparar(session_factory, http)
    send, _msg = _patch_send()
    with _patched_factusol(), send as sent:
        r = _mandar(http, oid, None)
    assert r.status_code == 200, r.text
    assert r.json()["correo"] is None
    assert r.json()["order"]["preparation_status"] == "in_queue"
    sent.assert_not_called()


def test_si_el_correo_falla_el_pedido_sigue_en_la_cola_y_se_reintenta(
    http, session_factory,  # noqa: F811
) -> None:
    from app.integrations.gmail.service import GmailNotConnectedError

    oid = _preparar(session_factory, http)
    with _patched_factusol(), patch("app.integrations.gmail.service.send_email",
                                    side_effect=GmailNotConnectedError("sin Gmail")):
        r = _mandar(http, oid, _correo())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["correo"]["estado"] == "fallido"
    assert "Gmail" in body["correo"]["detalle"]
    assert body["order"]["preparation_status"] == "in_queue"
    assert body["order"]["mandar_sat"]["correo"]["estado"] == "fallido"
    # Reintentar: el mismo correo, sin volver a escribirlo.
    send, _msg = _patch_send()
    with _patched_factusol(), send as sent:
        r = http.post(f"/api/erp/orders/{oid}/sat-correo/enviar", json={"confirm": True},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["correo"]["estado"] == "enviado"
    assert sent.call_args.kwargs["to"] == [SAT]
    # Y ya no hay nada pendiente.
    r = http.post(f"/api/erp/orders/{oid}/sat-correo/enviar", json={"confirm": True},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409


def test_si_hay_que_crear_el_albaran_el_correo_espera_a_que_exista(
    http, session_factory,  # noqa: F811
) -> None:
    oid = _preparar(session_factory, http, albaran=None)
    send, _msg = _patch_send()
    with _patched_factusol(), send as sent:
        r = _mandar(http, oid, _correo())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["albaran"]["crear"] is True
    assert body["correo"]["estado"] == "pendiente_albaran"
    sent.assert_not_called()
    # Cuando el albarán existe, la ficha lo manda (el mismo correo, con él).
    with session_factory() as s:
        s.get(Order, oid).factusol_albaran_number = "5-500004"
        s.commit()
    send, _msg = _patch_send()
    with _patched_factusol(), send as sent:
        r = http.post(f"/api/erp/orders/{oid}/sat-correo/enviar", json={"confirm": True},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["correo"]["estado"] == "enviado"
    sent.assert_called_once()


def test_el_taller_que_no_puede_mandar_al_sat_no_mueve_nada(http, session_factory) -> None:  # noqa: F811
    oid = _preparar(session_factory, http)
    r = http.post(f"/api/erp/orders/{oid}/mandar-sat",
                  json={"confirm": True, "correo": _correo()},
                  headers=auth_headers(http, "user"))
    assert r.status_code == 403
    with session_factory() as s:
        assert s.get(Order, oid).preparation_status == "pending_review"


def test_desde_una_factura_sin_albaran_el_correo_espera_a_que_se_vincule(
    http, session_factory,  # noqa: F811
) -> None:
    import json

    with session_factory() as s:
        order = _seed_order(s, albaran=None, with_source=False)
        order.external_source = "factusol_factura"
        order.packing_json = json.dumps({"factusol_source": {
            "doc_type": "facturas", "serie": 5, "codigo": 260063}})
        s.commit()
        _seed_alias(s)
        oid = order.id
    _set_sat_email(http, SAT)
    send, _msg = _patch_send()
    # La factura (F_LFA del figurante) no apunta a ningún albarán.
    with _patched_factusol(), send as sent:
        r = _mandar(http, oid, _correo())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["albaran"]["estado"] == "sin_enlace"
    assert body["correo"]["estado"] == "pendiente_albaran"
    assert body["order"]["preparation_status"] == "in_queue"
    sent.assert_not_called()


# --- lo que encontró la primera revisión ---------------------------------------------------


def test_un_error_inesperado_del_correo_tampoco_saca_el_pedido_de_la_cola(
    http, session_factory,  # noqa: F811
) -> None:
    oid = _preparar(session_factory, http)
    with _patched_factusol(), patch("app.integrations.gmail.service.send_email",
                                    side_effect=RuntimeError("timeout")):
        r = _mandar(http, oid, _correo())
    assert r.status_code == 200, r.text
    assert r.json()["correo"]["estado"] == "fallido"
    assert "Gmail" in r.json()["correo"]["detalle"]
    assert r.json()["order"]["preparation_status"] == "in_queue"


def test_enviar_ahora_sin_albaran_y_sin_otro_adjunto_no_rompe_el_pendiente(
    http, session_factory,  # noqa: F811
) -> None:
    oid = _preparar(session_factory, http, albaran=None)
    with _patched_factusol():
        assert _mandar(http, oid, _correo()).json()["correo"]["estado"] == "pendiente_albaran"
        r = http.post(f"/api/erp/orders/{oid}/sat-correo/enviar", json={"confirm": True},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "falta_albaran"
    with session_factory() as s:
        assert '"pendiente_albaran"' in s.get(Order, oid).packing_json


def test_el_correo_pendiente_sale_solo_al_crearse_el_albaran(http, session_factory) -> None:  # noqa: F811
    from app.erp.sat_correo import enviar_si_pendiente
    from tests.test_erp_order_email import FakeClient, _tables

    oid = _preparar(session_factory, http, albaran=None)
    with _patched_factusol():
        _mandar(http, oid, _correo())
    send, _msg = _patch_send()
    with session_factory() as s, send as sent:
        o = s.get(Order, oid)
        o.factusol_albaran_number = "5-500004"     # lo que deja el job del albarán
        s.commit()
        enviar_si_pendiente(s, o, client=FakeClient(_tables()), ejercicio="2026")
        assert '"enviado"' in s.get(Order, oid).packing_json
    sent.assert_called_once()
    assert sent.call_args.kwargs["to"] == [SAT]


# --- lo que encontró la segunda revisión ---------------------------------------------------


def test_un_segundo_clic_no_manda_el_correo_otra_vez(http, session_factory) -> None:  # noqa: F811
    oid = _preparar(session_factory, http)
    send, _msg = _patch_send()
    with _patched_factusol(), send as sent:
        assert _mandar(http, oid, _correo()).json()["correo"]["estado"] == "enviado"
        r = _mandar(http, oid, _correo())
    assert r.status_code == 200, r.text
    assert r.json()["cola"] == "ya_estaba"
    sent.assert_called_once()


def test_dos_caminos_no_mandan_el_mismo_correo_pendiente(http, session_factory) -> None:  # noqa: F811
    from app.erp import sat_correo
    from tests.test_erp_order_email import FakeClient, _tables

    oid = _preparar(session_factory, http, albaran=None)
    with _patched_factusol():
        _mandar(http, oid, _correo())
    send, _msg = _patch_send()
    with session_factory() as s, send as sent:
        o = s.get(Order, oid)
        o.factusol_albaran_number = "5-500004"
        s.commit()
        # Otro camino ya lo reclamó (se está mandando): este no lo manda.
        assert sat_correo.reclamar(s, o, {"pendiente_albaran"}) is not None
        # Y no se puede reclamar dos veces.
        assert sat_correo.reclamar(s, s.get(Order, oid), {"pendiente_albaran"}) is None
        sat_correo.enviar_si_pendiente(s, s.get(Order, oid), client=FakeClient(_tables()),
                                       ejercicio="2026")
    sent.assert_not_called()


def test_un_remitente_no_permitido_se_rechaza_al_mandar_y_no_mueve_nada(
    http, session_factory,  # noqa: F811
) -> None:
    oid = _preparar(session_factory, http)
    with _patched_factusol():
        r = _mandar(http, oid, _correo(from_alias="ajeno@otra-empresa.example"))
    assert r.status_code in (400, 403, 422), r.text
    with session_factory() as s:
        assert s.get(Order, oid).preparation_status == "pending_review"
