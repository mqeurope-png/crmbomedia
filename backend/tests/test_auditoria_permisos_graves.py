"""Las cuatro filtraciones graves de la auditoría de permisos (30/10/2026,
`docs/auditoria-permisos-2026-10-30.md`), cerradas:

- F-1: la búsqueda genérica de entidades no enseña hilos de correo ajenos ni
  deja entrar a Solo lectura en correo y marketing.
- F-2: desde «mi cuenta» no se enciende un remitente que el admin no ha dado,
  ni un workflow privado envía desde uno.
- F-3: el ERP · Comercial no toca pedidos web por las puertas que se lo
  saltaban (masivas, Genei, línea de vida, taller, excepciones, vincular).
- F-4: el Responsable sin rol ERP no cambia el estado de los pedidos.

Datos: figurantes.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select

from app.erp.models import ErpException, ExceptionType, Order
from app.erp.models.orders import OrderSource
from app.models.crm import EmailThread, User, UserEmailAliasPref, UserRole
from app.models.workflows import Workflow
from tests._test_helpers import auth_headers
from tests.test_erp_orders_api import (  # noqa: F401 — fixtures y helpers
    _create,
    _fire,
    client,
    session_factory,
)

AHORA = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


def _uid(s, role: UserRole) -> str:
    return s.scalar(select(User.id).where(User.role == role))


def _hilo(s, autor: str, asunto: str) -> None:
    s.add(EmailThread(initiated_by_user_id=autor, gmail_account_user_id=autor,
                      gmail_thread_id=f"g-{asunto}", subject=asunto,
                      first_message_at=AHORA, last_message_at=AHORA))


# --- F-1 -------------------------------------------------------------------------------


def test_f1_la_busqueda_de_correos_solo_trae_los_propios(client, session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        _hilo(s, _uid(s, UserRole.USER), "Presupuesto figurante de Usuario")
        _hilo(s, _uid(s, UserRole.MANAGER), "Asunto privado del Responsable")
        s.commit()

    def asuntos(rol: str) -> tuple[int, set[str]]:
        r = client.post("/api/entities/email_thread/search", json={},
                        headers=auth_headers(client, rol))
        return r.status_code, {i["subject"] for i in r.json().get("items", [])} \
            if r.status_code == 200 else set()

    assert asuntos("user") == (200, {"Presupuesto figurante de Usuario"})
    assert asuntos("admin") == (200, {"Presupuesto figurante de Usuario",
                                      "Asunto privado del Responsable"})
    assert asuntos("viewer")[0] == 403
    ids = client.post("/api/entities/email_thread/search/ids", json={},
                      headers=auth_headers(client, "user")).json()
    assert ids["count"] == 1
    # Marketing tampoco para Solo lectura; contactos sí (como su pantalla).
    assert client.post("/api/entities/brevo_campaign/search", json={},
                       headers=auth_headers(client, "viewer")).status_code == 403
    assert client.post("/api/entities/contact/search", json={},
                       headers=auth_headers(client, "viewer")).status_code == 200


# --- F-2 -------------------------------------------------------------------------------


def test_f2_desde_mi_cuenta_no_se_enciende_un_remitente_no_concedido(
    client, session_factory,  # noqa: F811
) -> None:
    with session_factory() as s:
        uid = _uid(s, UserRole.USER)
        s.add(UserEmailAliasPref(user_id=uid, alias_email="ventas@figurante.example",
                                 is_allowed=True, is_default=True))
        s.commit()
    h = auth_headers(client, "user")

    def poner(*prefs: tuple[str, bool, bool]):
        return client.put("/api/emails/aliases/preferences", headers=h, json={
            "preferences": [{"alias_email": a, "is_allowed": on, "is_default": d}
                            for a, on, d in prefs]})

    r = poner(("ventas@figurante.example", True, False),
              ("direccion@figurante.example", True, True))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "remitente_no_concedido"
    # Apagar o elegir entre los que ya tiene sí.
    assert poner(("ventas@figurante.example", False, False)).status_code == 200
    with session_factory() as s:
        filas = {p.alias_email: p.is_allowed for p in s.scalars(
            select(UserEmailAliasPref).where(UserEmailAliasPref.user_id == uid))}
    assert filas == {"ventas@figurante.example": False}


def test_f2_un_workflow_privado_no_envia_desde_un_remitente_ajeno(
    session_factory,  # noqa: F811
) -> None:
    from app.workflows.steps import _remitente_fijo_permitido

    with session_factory() as s:
        uid = _uid(s, UserRole.USER)
        autor = s.get(User, uid)
        s.add(UserEmailAliasPref(user_id=uid, alias_email="ventas@figurante.example",
                                 is_allowed=True))
        privado = Workflow(name="Privado figurante", owner_user_id=uid,
                           trigger_type="manual")
        global_ = Workflow(name="Del equipo", owner_user_id=None, trigger_type="manual")
        s.add_all([privado, global_])
        s.commit()
        run_privado = SimpleNamespace(workflow_id=privado.id)
        assert _remitente_fijo_permitido(s, run_privado, "ventas@figurante.example")
        assert _remitente_fijo_permitido(s, run_privado, autor.email)
        assert not _remitente_fijo_permitido(s, run_privado, "direccion@figurante.example")
        # Los del equipo los crea el admin.
        assert _remitente_fijo_permitido(s, SimpleNamespace(workflow_id=global_.id),
                                         "direccion@figurante.example")


# --- F-3 -------------------------------------------------------------------------------


def _pedido_web(client, session_factory) -> str:  # noqa: F811
    oid = _create(client)["id"]
    with session_factory() as s:
        o = s.get(Order, oid)
        o.external_source = OrderSource.WOOCOMMERCE.value
        s.commit()
    return oid


def test_f3_el_comercial_no_toca_pedidos_web_por_las_masivas(client, session_factory) -> None:  # noqa: F811
    web = _pedido_web(client, session_factory)
    manual = _create(client, order_number="FIG-2")["id"]
    h = auth_headers(client, "comercial")
    r = client.post("/api/erp/orders/bulk-complete", headers=h,
                    json={"order_ids": [web, manual]})
    assert r.status_code == 200
    assert [f["order_id"] for f in r.json()["failed"]] == [web]
    r = client.post("/api/erp/orders/bulk-mark-externally-processed", headers=h,
                    json={"order_ids": [web, manual]})
    assert r.json()["marked"] == 1
    with session_factory() as s:
        o = s.get(Order, web)
        # Nada escrito en el pedido web.
        assert o.completed_at is None and o.externally_processed_at is None
    # Pedidos sí puede (ve web).
    r = client.post("/api/erp/orders/bulk-mark-externally-processed",
                    headers=auth_headers(client, "pedidos"), json={"order_ids": [web]})
    assert r.json()["marked"] == 1


def test_f3_el_comercial_no_ve_ni_gestiona_un_pedido_web_por_otras_puertas(
    client, session_factory,  # noqa: F811
) -> None:
    web = _pedido_web(client, session_factory)
    with session_factory() as s:
        s.add(ErpException(type=ExceptionType.STOCK_SHORTAGE, order_id=web))
        s.commit()
        exc_id = s.scalar(select(ErpException.id).where(ErpException.order_id == web))
    h = auth_headers(client, "comercial")
    assert client.get(f"/api/erp/orders/{web}/timeline", headers=h).status_code == 403
    assert client.get(f"/api/erp/orders/{web}/genei/prefill", headers=h).status_code in (
        403, 404)
    assert client.get("/api/erp/sat/find-order", params={"number": "FIG-1"},
                      headers=h).status_code == 404
    assert client.get("/api/erp/exceptions", headers=h).json()["items"] == []
    assert client.post(f"/api/erp/exceptions/{exc_id}/assign", headers=h,
                       json={}).status_code == 403
    # Pedidos lo ve todo.
    hp = auth_headers(client, "pedidos")
    assert client.get(f"/api/erp/orders/{web}/timeline", headers=hp).status_code == 200
    assert len(client.get("/api/erp/exceptions", headers=hp).json()["items"]) == 1


# --- F-4 -------------------------------------------------------------------------------


def test_f4_el_responsable_sin_rol_erp_no_cambia_el_estado(client, session_factory) -> None:  # noqa: F811
    oid = _create(client)["id"]
    for rol in ("manager", "user"):
        r = _fire(client, oid, "payment", "paid", role=rol)
        assert r.status_code == 403, rol
    detalle = client.get(f"/api/erp/orders/{oid}", headers=auth_headers(client, "manager"))
    assert not any(detalle.json()["available_transitions"].values())
    # Con un rol ERP añadido, por ese rol.
    with session_factory() as s:
        m = s.get(User, _uid(s, UserRole.MANAGER))
        m.erp_roles = '["pedidos"]'
        s.commit()
    assert _fire(client, oid, "payment", "paid", role="manager").status_code == 200


# --- lo que encontró la revisión -------------------------------------------------------


def test_f2_un_modo_de_remitente_desconocido_tambien_se_comprueba(session_factory) -> None:  # noqa: F811
    """`_resolve_workflow_from_alias` trata un modo desconocido como fijo:
    la comprobación del remitente tiene que cubrirlo igual."""
    from app.workflows import steps

    with session_factory() as s:
        uid = _uid(s, UserRole.USER)
        s.add(UserEmailAliasPref(user_id=uid, alias_email="ventas@figurante.example",
                                 is_allowed=True))
        s.commit()
        alias, _ = steps._resolve_workflow_from_alias(
            session=s, owner_id=uid, mode="fijo", fixed_alias="otro@figurante.example",
            wanted_display_name=None)
    assert alias == "otro@figurante.example"     # se comporta como fijo…
    import inspect

    # …y el paso comprueba todo lo que no sea «del propietario».
    assert 'alias_mode not in ("owner_default", "owner_specific")' in inspect.getsource(
        steps._step_send_email)


def test_f2_el_erp_no_acepta_cualquier_remitente_verificado(
    session_factory, monkeypatch,  # noqa: F811
) -> None:
    from app.erp import invoice_email
    from app.integrations.gmail import service as gmail_service

    monkeypatch.setattr(gmail_service, "list_aliases", lambda s, uid: [
        {"send_as_email": "direccion@figurante.example"},
        {"send_as_email": "tienda@figurante.example"}])
    monkeypatch.setattr(invoice_email, "erp_configured_senders",
                        lambda s: {"tienda@figurante.example"})
    with session_factory() as s:
        comercial = s.get(User, _uid(s, UserRole.COMERCIAL))
        admin = s.get(User, _uid(s, UserRole.ADMIN))
        ver = invoice_email.check_sender_alias
        assert ver(s, comercial, "direccion@figurante.example")["ok"] is False
        assert ver(s, comercial, "tienda@figurante.example")["ok"] is True
        assert ver(s, admin, "direccion@figurante.example")["ok"] is True


def test_f2_un_remitente_concedido_pendiente_de_gmail_no_da_403(
    client, session_factory,  # noqa: F811
) -> None:
    with session_factory() as s:
        uid = _uid(s, UserRole.USER)
        # El admin lo eligió; Gmail aún no lo tenía: queda apagado y elegido.
        s.add(UserEmailAliasPref(user_id=uid, alias_email="nuevo@figurante.example",
                                 is_allowed=False, user_opted_in=True))
        s.commit()
    r = client.put("/api/emails/aliases/preferences", headers=auth_headers(client, "user"),
                   json={"preferences": [{"alias_email": "nuevo@figurante.example",
                                          "is_allowed": True, "is_default": True}]})
    assert r.status_code == 200, r.text


def test_f3_los_candidatos_a_vincular_no_traen_pedidos_web(
    client, session_factory, monkeypatch,  # noqa: F811
) -> None:
    from app.erp.api import factusol as api_factusol

    web = _pedido_web(client, session_factory)
    manual = _create(client, order_number="FIG-2")["id"]
    monkeypatch.setattr(api_factusol, "_link_doc_or_404", lambda *a, **k: {
        "referencia": "", "cliente_codigo": "", "numero": "1-1"})
    monkeypatch.setattr(api_factusol, "_orders_linked_to_document", lambda *a, **k: [])

    def ids(rol: str) -> set[str]:
        r = client.get("/api/erp/factusol/documents/facturas/1/1/link-candidates",
                       params={"q": "-"}, headers=auth_headers(client, rol))
        assert r.status_code == 200, r.text
        return {c["id"] for c in r.json()["candidates"]}

    assert ids("comercial") == {manual}
    assert ids("pedidos") == {web, manual}


def test_f4_el_credito_b2b_es_solo_del_admin(client, session_factory) -> None:  # noqa: F811
    oid = _create(client)["id"]
    with session_factory() as s:
        m = s.get(User, _uid(s, UserRole.MANAGER))
        m.erp_roles = '["comercial"]'
        s.commit()
    assert _fire(client, oid, "payment", "credit_approved", role="manager").status_code == 403
    assert _fire(client, oid, "payment", "credit_approved", role="admin").status_code == 200
