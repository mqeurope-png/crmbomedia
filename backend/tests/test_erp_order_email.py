"""ERP · enviar el PEDIDO por email (al SAT / taller y a quien haga falta).

Reutiliza el Gmail ya integrado y los PDF del motor E4. El envío real se
mockea; se verifica que el ALBARÁN va siempre adjunto, que el PDF del pedido
y el de la factura son opcionales, el destinatario SAT configurable en
Ajustes ERP (+ otros destinatarios, CC/CCO), la confirmación obligatoria, el
registro en el timeline y los avisos cuando falta el albarán o Gmail no está.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderSource
from app.main import app
from app.models.crm import AuditLog, Company, Contact, User, UserEmailAliasPref
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_documents import FakeClient

ALIAS = "ventas@bomedia.net"
SAT = "taller@bomedia.net"


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- datos de FACTUSOL (albarán 5-500004, presupuesto 1-574, factura 5-260063)


def _alb_row(**over: Any) -> dict[str, Any]:
    row = {
        "TIPALB": "5", "CODALB": 500004, "FECALB": "2026-08-21T00:00:00",
        "CLIALB": 2458, "CNIALB": "B12345678", "CNOALB": "DUPLICODER, S.L.",
        "CDOALB": "C/ Muñoz Seca, 3", "CCPALB": "08036", "CPOALB": "Barcelona",
        "CPAALB": "724", "REFALB": "MANUAL-000010", "TOTALB": 96.8,
        "NET1ALB": 80.0, "BAS1ALB": 80.0, "PIVA1ALB": 21, "IIVA1ALB": 16.8,
        "FOPALB": "002",
    }
    row.update(over)
    return row


def _pre_row(**over: Any) -> dict[str, Any]:
    row = {
        "TIPPRE": "1", "CODPRE": 574, "FECPRE": "2026-08-01T00:00:00",
        "CLIPRE": 2458, "CNIPRE": "B12345678", "CNOPRE": "DUPLICODER, S.L.",
        "CDOPRE": "C/ Muñoz Seca, 3", "CCPPRE": "08036", "CPOPRE": "Barcelona",
        "CPAPRE": "724", "REFPRE": "Obra X", "TOTPRE": 96.8,
        "NET1PRE": 80.0, "BAS1PRE": 80.0, "PIVA1PRE": 21, "IIVA1PRE": 16.8,
        "FOPPRE": "002",
    }
    row.update(over)
    return row


def _fac_row(**over: Any) -> dict[str, Any]:
    row = {
        "TIPFAC": "5", "CODFAC": 260063, "FECFAC": "2026-08-26T00:00:00",
        "CLIFAC": 2458, "CNIFAC": "B12345678", "CNOFAC": "DUPLICODER, S.L.",
        "CDOFAC": "C/ Muñoz Seca, 3", "CCPFAC": "08036", "CPOFAC": "Barcelona",
        "CPAFAC": "724", "REFFAC": "MANUAL-000010", "TOTFAC": 96.8,
        "NET1FAC": 80.0, "BAS1FAC": 80.0, "PIVA1FAC": 21, "IIVA1FAC": 16.8,
        "FOPFAC": "002",
    }
    row.update(over)
    return row


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_ALB": [_alb_row()],
        "F_LAL": [{"TIPLAL": "5", "CODLAL": 500004, "POSLAL": 1,
                   "ARTLAL": "99cy", "DESLAL": "Tinta cyan", "CANLAL": 2.0,
                   "PRELAL": 40.0, "TOTLAL": 80.0}],
        "F_PRE": [_pre_row()],
        "F_LPS": [{"TIPLPS": "1", "CODLPS": 574, "POSLPS": 1, "ARTLPS": "99cy",
                   "DESLPS": "Tinta cyan", "CANLPS": 2.0, "PRELPS": 40.0,
                   "TOTLPS": 80.0}],
        "F_FAC": [_fac_row()],
        "F_LFA": [{"TIPLFA": "5", "CODLFA": 260063, "POSLFA": 1,
                   "ARTLFA": "99cy", "DESLFA": "Tinta cyan", "CANLFA": 2.0,
                   "PRELFA": 40.0, "TOTLFA": 80.0}],
        "F_FOP": [{"CODFOP": "002", "DESFOP": "Transferencia"}],
    }


def _patched_factusol(tables=None):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=FakeClient(tables if tables is not None else _tables()),
    )


def _patch_send():
    msg = MagicMock()
    msg.id = "msg-1"
    msg.thread_id = "thread-1"
    return patch("app.integrations.gmail.service.send_email", return_value=msg), msg


def _seed_order(
    session: Session, *, albaran: str | None = "5-500004",
    invoice: str | None = "260063", invoice_serie: int | None = 5,
    with_source: bool = True,
) -> Order:
    """Pedido creado desde el presupuesto 1-574 (Fase 1), con albarán y
    factura en FACTUSOL — los tres PDF disponibles."""
    contact = Contact(first_name="Ana", last_name="Pi", email="ana@example.com")
    company = Company(name="Duplicoder SL", source="manual",
                      factusol_company_id="2458", country="ES")
    session.add_all([contact, company])
    session.flush()
    packing = {"factusol_source": {
        "doc_type": "presupuestos", "serie": 1, "codigo": 574,
        "numero": "1-000574", "referencia": "Obra X", "forma_pago": "002",
        "forma_pago_nombre": "Transferencia", "cliente_codigo": "2458",
        "total": 96.8,
    }} if with_source else {}
    order = Order(
        external_source=OrderSource.FACTUSOL_PROFORMA, external_id="574",
        order_number="PRO-000574", contact_id=contact.id, company_id=company.id,
        total_amount=96.8, currency="EUR", language="es",
        factusol_albaran_number=albaran,
        factusol_invoice_number=invoice,
        factusol_invoice_serie=invoice_serie,
        packing_json=json.dumps(packing) if packing else None,
    )
    session.add(order)
    session.commit()
    return order


def _seed_alias(session: Session, role: str = "pedidos", alias: str = ALIAS) -> None:
    user = session.query(User).filter_by(email=f"{role}@example.com").one()
    session.add(UserEmailAliasPref(
        user_id=user.id, alias_email=alias, is_allowed=True,
    ))
    session.commit()


def _set_sat_email(http: TestClient, value: str) -> None:
    r = http.patch("/api/erp/settings", json={"sat_email": value},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text


def _body(**over: Any) -> dict[str, Any]:
    body = {
        "confirm": True, "to": [SAT], "subject": "Pedido PRO-000574 — Duplicoder SL",
        "body_text": "Adjuntamos el pedido.", "lang": "es", "from_alias": ALIAS,
    }
    body.update(over)
    return body


# --- destinatario SAT configurable -----------------------------------------


def test_email_destinatario_sat_por_defecto_configurable(http, session_factory) -> None:
    """El «Para» viene precargado con el email del SAT de Ajustes ERP; se
    puede cambiar y se pueden añadir otros destinatarios (y CC/CCO)."""
    with session_factory() as s:
        order = _seed_order(s)
        _seed_alias(s)
        oid = order.id

    # Sin configurar: no hay destinatario precargado, y el preview lo dice.
    with _patched_factusol():
        r = http.get(f"/api/erp/orders/{oid}/email-preview",
                     headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["to"] == [] and r.json()["sat_configured"] is False

    _set_sat_email(http, SAT)
    r = http.get("/api/erp/settings", headers=auth_headers(http, "admin"))
    assert r.json()["sat_email"] == SAT

    with _patched_factusol():
        r = http.get(f"/api/erp/orders/{oid}/email-preview",
                     headers=auth_headers(http, "pedidos"))
    preview = r.json()
    assert preview["to"] == [SAT] and preview["sat_configured"] is True
    assert preview["subject"] == "Pedido PRO-000574 — Duplicoder SL"
    assert "Obra X" in preview["body_text"]          # referencia del documento
    assert preview["from_alias"] == ALIAS
    assert preview["defaults"] == {"albaran": True, "pedido": False, "factura": False}
    # El selector trae los contactos de la empresa (incl. el del pedido).
    assert any(c["is_order_contact"] for c in preview["company_contacts"])

    # Enviar a OTROS destinatarios además del SAT, con CC y CCO.
    send, msg = _patch_send()
    with _patched_factusol(), send as sent:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(
            to=[SAT, "otro@taller.example"], cc=["jefe@bomedia.net"],
            bcc=["copia@bomedia.net"],
        ), headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    assert r.json()["to"] == [SAT, "otro@taller.example"]
    kwargs = sent.call_args.kwargs
    assert kwargs["to"] == [SAT, "otro@taller.example"]
    assert kwargs["cc"] == ["jefe@bomedia.net"]
    assert kwargs["bcc"] == ["copia@bomedia.net"]
    assert msg.id == "msg-1"

    # El ajuste se puede borrar («» = sin destinatario precargado).
    _set_sat_email(http, "")
    r = http.get("/api/erp/settings", headers=auth_headers(http, "admin"))
    assert r.json()["sat_email"] == ""


# --- adjuntos ---------------------------------------------------------------


def test_enviar_pedido_email_adjunta_albaran_siempre(http, session_factory) -> None:
    """El albarán va adjunto por defecto: sin marcar nada más, el correo sale
    con el PDF del albarán y solo con ese."""
    with session_factory() as s:
        order = _seed_order(s)
        _seed_alias(s)
        oid = order.id
    _set_sat_email(http, SAT)

    send, _msg = _patch_send()
    with _patched_factusol(), send as sent:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sent"] is True and body["attachment_kinds"] == ["albaran"]
    attachments = sent.call_args.kwargs["attachments"]
    assert len(attachments) == 1
    (adj,) = attachments
    assert adj["content_type"] == "application/pdf"
    assert adj["data"][:5] == b"%PDF-"                # PDF de verdad
    assert "albaran" in adj["filename"].lower() or "5-500004" in adj["filename"]
    assert body["attachments"] == [adj["filename"]]


def test_email_adjuntos_opcionales_pedido_y_factura(http, session_factory) -> None:
    """Los PDF del pedido y de la factura se adjuntan solo si se marcan; el
    albarán se puede desmarcar para enviar sin él."""
    with session_factory() as s:
        order = _seed_order(s)
        _seed_alias(s)
        oid = order.id
    _set_sat_email(http, SAT)

    # Los tres.
    send, _ = _patch_send()
    with _patched_factusol(), send as sent:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(
            include_pedido=True, include_factura=True,
        ), headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    assert r.json()["attachment_kinds"] == ["albaran", "pedido", "factura"]
    assert len(sent.call_args.kwargs["attachments"]) == 3
    assert all(a["data"][:5] == b"%PDF-"
               for a in sent.call_args.kwargs["attachments"])

    # Solo el pedido (albarán desmarcado a propósito).
    send2, _ = _patch_send()
    with _patched_factusol(), send2 as sent2:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(
            include_albaran=False, include_pedido=True,
        ), headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    assert r.json()["attachment_kinds"] == ["pedido"]
    assert len(sent2.call_args.kwargs["attachments"]) == 1

    # Sin ningún adjunto marcado no se manda un correo vacío al taller.
    send3, _ = _patch_send()
    with _patched_factusol(), send3 as sent3:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(
            include_albaran=False,
        ), headers=auth_headers(http, "pedidos"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "no_attachments"
    sent3.assert_not_called()


def test_email_sin_albaran_avisa(http, session_factory) -> None:
    """Pedido sin albarán en FACTUSOL: el preview lo avisa y ofrece crearlo;
    marcar el albarán da un error claro (no se manda un correo sin él) y se
    puede enviar sin él marcando otro documento."""
    with session_factory() as s:
        order = _seed_order(s, albaran=None, invoice=None, invoice_serie=None)
        _seed_alias(s)
        oid = order.id
    _set_sat_email(http, SAT)

    with _patched_factusol():
        r = http.get(f"/api/erp/orders/{oid}/email-preview",
                     headers=auth_headers(http, "pedidos"))
    preview = r.json()
    alb = preview["attachments"]["albaran"]
    assert alb["available"] is False and alb["code"] == "albaran_missing"
    assert "Crear albarán en FACTUSOL" in alb["reason"]
    assert preview["defaults"]["albaran"] is False
    # La factura tampoco está emitida; el pedido sí (viene del presupuesto).
    assert preview["attachments"]["factura"]["available"] is False
    assert preview["attachments"]["pedido"]["available"] is True

    send, _ = _patch_send()
    with _patched_factusol(), send as sent:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "albaran_missing"
    assert "Crear albarán en FACTUSOL" in r.json()["detail"]["detail"]
    sent.assert_not_called()

    # Enviar SIN albarán, con el PDF del pedido: permitido.
    send2, _ = _patch_send()
    with _patched_factusol(), send2 as sent2:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(
            include_albaran=False, include_pedido=True,
        ), headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    assert r.json()["attachment_kinds"] == ["pedido"]
    sent2.assert_called_once()

    # Y la factura marcada sin estar emitida también avisa.
    with _patched_factusol(), _patch_send()[0]:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(
            include_albaran=False, include_factura=True,
        ), headers=auth_headers(http, "pedidos"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "factura_missing"


# --- timeline + guards ------------------------------------------------------


def test_email_registra_en_timeline(http, session_factory) -> None:
    """El envío queda en el timeline del pedido: a quién, cuándo y qué se
    adjuntó."""
    with session_factory() as s:
        order = _seed_order(s)
        _seed_alias(s)
        oid = order.id
    _set_sat_email(http, SAT)

    send, _ = _patch_send()
    with _patched_factusol(), send:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(
            to=[SAT], cc=["jefe@bomedia.net"], include_factura=True,
        ), headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text

    with session_factory() as s:
        log = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.order_emailed",
        )).one()
        assert log.target_type == "order" and log.target_id == oid
        assert SAT in (log.message or "")
        meta = json.loads(log.metadata_json)
        assert meta["to"] == [SAT] and meta["cc"] == ["jefe@bomedia.net"]
        assert meta["attachment_kinds"] == ["albaran", "factura"]
        assert len(meta["attachments"]) == 2
        assert meta["message_id"] == "msg-1"
        assert meta["from_alias"] == ALIAS

    # Y sale en el timeline de la ficha.
    r = http.get(f"/api/erp/orders/{oid}/timeline",
                 headers=auth_headers(http, "pedidos"))
    if r.status_code == 200:
        assert any("order_emailed" in json.dumps(item)
                   for item in r.json().get("items", []))


def test_email_guards_confirmacion_destinatario_y_alias(http, session_factory) -> None:
    """Sin confirmación, sin destinatario o con un alias que no es del
    usuario NO se envía nada."""
    with session_factory() as s:
        order = _seed_order(s)
        _seed_alias(s)
        oid = order.id
    _set_sat_email(http, SAT)

    send, _ = _patch_send()
    with _patched_factusol(), send as sent:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(confirm=False),
                      headers=auth_headers(http, "pedidos"))
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "confirmation_required"

        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(to=[" "]),
                      headers=auth_headers(http, "pedidos"))
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "no_recipient"

        r = http.post(f"/api/erp/orders/{oid}/email",
                      json=_body(from_alias="ajeno@example.com"),
                      headers=auth_headers(http, "pedidos"))
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "alias_not_allowed"
    sent.assert_not_called()

    # Solo lectura no puede enviar.
    r = http.post(f"/api/erp/orders/{oid}/email", json=_body(),
                  headers=auth_headers(http, "sat"))
    assert r.status_code == 403


# --- Lote B6: «enviado al taller» = aprobado ---------------------------------


def test_email_al_sat_aprueba_si_estaba_pendiente(http, session_factory) -> None:
    """Enviar el pedido por email al SAT lo marca aprobado (pending_review →
    in_queue, approved_at/by) igual que la Cola PEDIDOS, y el historial del
    taller lo cuenta dos veces: el email y la aprobación."""
    from app.erp.models import OrderStatusHistory  # noqa: PLC0415

    with session_factory() as s:
        order = _seed_order(s)
        _seed_alias(s)
        oid = order.id
        assert order.preparation_status == "pending_review"
    _set_sat_email(http, SAT)

    send, _ = _patch_send()
    with _patched_factusol(), send:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    assert r.json()["approved"] is True
    assert r.json()["preparation_status"] == "in_queue"

    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.preparation_status == "in_queue"
        assert o.approved_at is not None and o.approved_by_user_id is not None
        h = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == oid,
        )).one()
        assert (h.from_status, h.to_status) == ("pending_review", "in_queue")
        assert h.reason == "enviado al SAT por email"
        # El envío sigue registrado (una sola vez).
        s.scalars(select(AuditLog).where(AuditLog.action == "erp.order_emailed")).one()

    r = http.get("/api/erp/sat/history", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200
    kinds = sorted(i["kind"] for i in r.json()["items"] if i["order_id"] == oid)
    assert kinds == ["aprobado", "email_sat"]
    # Y ya está en «Por embalar».
    queue = http.get("/api/erp/sat/queue", headers=auth_headers(http, "pedidos")).json()
    assert [i["order_number"] for i in queue["preparing"]] == ["PRO-000574"]


def test_email_al_sat_no_aprueba_con_bloqueos_ni_si_ya_estaba_en_cola(
    http, session_factory,
) -> None:
    """Con excepciones abiertas el correo sale igual pero NO se aprueba; y si
    el pedido ya no está pendiente de revisión no se toca nada."""
    from app.erp.models import ErpException, ExceptionType, OrderStatusHistory  # noqa: PLC0415

    with session_factory() as s:
        order = _seed_order(s)
        _seed_alias(s)
        oid = order.id
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=oid))
        s.commit()
    _set_sat_email(http, SAT)

    send, _ = _patch_send()
    with _patched_factusol(), send as sent:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    sent.assert_called_once()
    assert r.json()["approved"] is False
    assert r.json()["preparation_status"] == "pending_review"
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.preparation_status == "pending_review" and o.approved_at is None
        assert s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == oid,
        )).all() == []
        # Ya en cola (p.ej. reabierto desde el taller): el envío no lo mueve.
        o.preparation_status = "preparing"
        s.commit()

    send, _ = _patch_send()
    with _patched_factusol(), send:
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    assert r.json()["approved"] is False
    assert r.json()["preparation_status"] == "preparing"
    with session_factory() as s:
        assert s.get(Order, oid).preparation_status == "preparing"
        assert s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == oid,
        )).all() == []
        assert len(s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.order_emailed",
        )).all()) == 2


def test_email_gmail_no_conectado_avisa(http, session_factory) -> None:
    """Gmail caído / sin conectar: aviso claro y NADA registrado."""
    from app.integrations.gmail.service import GmailNotConnectedError

    with session_factory() as s:
        order = _seed_order(s)
        _seed_alias(s)
        oid = order.id
    _set_sat_email(http, SAT)

    with _patched_factusol(), patch(
        "app.integrations.gmail.service.send_email",
        side_effect=GmailNotConnectedError("Gmail no está conectado."),
    ):
        r = http.post(f"/api/erp/orders/{oid}/email", json=_body(),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "gmail_not_ready"
    with session_factory() as s:
        assert s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.order_emailed",
        )).all() == []


def test_order_email_render_y_idioma() -> None:
    """La plantilla por defecto lleva nº y cliente, y respeta el idioma del
    pedido."""
    from app.erp.order_email import render_order_email

    subject, body = render_order_email(
        lang="es", numero="PRO-000574", cliente="Duplicoder SL",
        referencia="Obra X",
    )
    assert subject == "Pedido PRO-000574 — Duplicoder SL"
    assert "PRO-000574" in body and "Duplicoder SL" in body and "Obra X" in body

    subject_en, body_en = render_order_email(
        lang="en", numero="PRO-000574", cliente="Duplicoder SL", referencia="",
    )
    assert subject_en == "Order PRO-000574 — Duplicoder SL"
    assert "ref." not in body_en                      # sin referencia, sin sufijo
    # Idioma no soportado → español.
    assert render_order_email(
        lang="zz", numero="X", cliente="Y", referencia="",
    )[0].startswith("Pedido ")
