"""ERP-F1 Parte 2 — enviar la factura por email en el idioma del cliente.

Conecta el PDF multiidioma, la cascada de idioma y el envío por Gmail. El
envío real se mockea; se verifica la CONFIRMACIÓN obligatoria, el idioma
resuelto (PDF + cuerpo), la respuesta al hilo cuando existe, el adjunto con
nombre legible, el registro en el timeline y que un fallo NO marca enviada.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderSource
from app.main import app
from app.models.crm import (
    Company,
    Contact,
    EmailMessage,
    EmailThread,
    UserEmailAliasPref,
)
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_documents import FakeClient


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


def _fac_row(**over: Any) -> dict[str, Any]:
    row = {
        "TIPFAC": "5", "CODFAC": 260063, "FECFAC": "2026-08-26T00:00:00",
        "CLIFAC": 2458, "CNIFAC": "B12345678", "CNOFAC": "DUPLICODER, S.L.",
        "CDOFAC": "C/ Muñoz Seca, 3", "CCPFAC": "08036", "CPOFAC": "Barcelona",
        "CPAFAC": "España", "FOPFAC": "002", "REFFAC": "BOP-099917",
        "TOTFAC": 225.47, "NET1FAC": 186.34, "BAS1FAC": 186.34,
        "PIVA1FAC": 21, "IIVA1FAC": 39.13,
    }
    row.update(over)
    return row


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {"F_FAC": [_fac_row()], "F_LFA": [], "F_ALB": [], "F_FOP": []}


def _seed_order(session: Session, *, language: str | None = "fr",
                email: str = "client@example.fr", country: str = "FR") -> Order:
    """Pedido ligado a la factura 5-260063 (por CODFAC) con contacto y
    empresa cliente, para que la resolución de idioma y destinatario tenga
    de dónde tirar."""
    contact = Contact(first_name="Jean", last_name="Dupont", email=email)
    session.add(contact)
    company = Company(name="Client SARL", source="manual",
                      factusol_company_id="2458", language=language,
                      country=country)
    session.add(company)
    session.flush()
    order = Order(
        external_source=OrderSource.WOOCOMMERCE, order_number="ART-000123",
        contact_id=contact.id, company_id=company.id,
        total_amount=225.47, currency="EUR",
        factusol_invoice_number="260063", language=language,
    )
    session.add(order)
    session.commit()
    return order


def _seed_alias(session: Session, role: str = "pedidos",
                alias: str = "ventas@bomedia.net") -> None:
    from app.models.crm import User
    user = session.query(User).filter_by(email=f"{role}@example.com").one()
    session.add(UserEmailAliasPref(
        user_id=user.id, alias_email=alias, is_allowed=True,
    ))
    session.commit()


def _patch_send():
    msg = MagicMock()
    msg.id = "msg-1"
    msg.thread_id = "thread-1"
    msg.snippet = "…"
    msg.from_email = "ventas@bomedia.net"
    return patch(
        "app.integrations.gmail.service.send_email", return_value=msg,
    ), msg


def _patched_factusol(tables=None):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=FakeClient(tables if tables is not None else _tables()),
    )


# ---------------------------------------------------------------------------
# Unidad: plantillas + render
# ---------------------------------------------------------------------------


def test_invoice_email_templates_defaults_and_render(session_factory) -> None:
    from app.erp.invoice_email import (
        INVOICE_EMAIL_DEFAULTS,
        render_invoice_email,
    )

    assert set(INVOICE_EMAIL_DEFAULTS) == {"es", "en", "de", "fr", "nl"}
    with session_factory() as s:
        subject, body = render_invoice_email(
            s, lang="fr", cliente="Client SARL", numero="5-260063",
            referencia="BOP-099917",
        )
        assert subject == "Facture 5-260063"
        assert "Client SARL" in body
        assert "5-260063" in body
        assert "votre réf. BOP-099917" in body
        # Idioma no soportado → cae a español.
        subject_es, _ = render_invoice_email(
            s, lang="it", cliente="X", numero="5-1", referencia="",
        )
        assert subject_es == "Factura 5-1"


# ---------------------------------------------------------------------------
# Preview + envío (endpoints)
# ---------------------------------------------------------------------------


def test_send_invoice_requires_confirmation(http, session_factory) -> None:
    with session_factory() as s:
        _seed_order(s)
        _seed_alias(s)
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        r = http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={
                "confirm": False, "to": ["client@example.fr"],
                "subject": "Facture 5-260063", "body_text": "Bonjour",
                "lang": "fr", "from_alias": "ventas@bomedia.net",
            },
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "confirmation_required"
    mock_send.assert_not_called()          # NADA se envía sin confirmar


def test_send_invoice_uses_resolved_language_for_pdf_and_body(http, session_factory) -> None:
    with session_factory() as s:
        _seed_order(s, language="fr")
        _seed_alias(s)
    # Preview: idioma resuelto = fr (del pedido), cuerpo y asunto en francés.
    with _patched_factusol():
        pre = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        )
    assert pre.status_code == 200, pre.text
    body = pre.json()
    assert body["lang"] == "fr" and body["lang_source"] == "pedido"
    assert body["subject"] == "Facture 5-260063"
    assert body["to"] == "client@example.fr"
    assert body["attachment_filename"].endswith(".pdf")

    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        r = http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={
                "confirm": True, "to": ["client@example.fr"],
                "subject": "Facture 5-260063", "body_text": "Bonjour",
                "lang": "fr", "from_alias": "ventas@bomedia.net",
            },
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 201, r.text
    kwargs = mock_send.call_args.kwargs
    # El PDF adjunto es application/pdf con nombre legible.
    att = kwargs["attachments"][0]
    assert att["content_type"] == "application/pdf"
    assert "Facture_5-260063" in att["filename"]
    assert att["data"].startswith(b"%PDF")
    # El cuerpo enviado es el francés.
    assert kwargs["subject"] == "Facture 5-260063"
    assert kwargs["contact_id"] is not None


def test_send_invoice_replies_to_thread_when_exists(http, session_factory) -> None:
    from datetime import UTC, datetime

    from app.models.crm import User

    with session_factory() as s:
        order = _seed_order(s)
        uid = s.query(User).filter_by(email="pedidos@example.com").one().id
        now = datetime.now(UTC)
        # Un hilo del contacto cuyo asunto referencia el pedido (123).
        thread = EmailThread(contact_id=order.contact_id,
                             initiated_by_user_id=uid,
                             gmail_thread_id="gt-123",
                             gmail_account_user_id=uid,
                             first_message_at=now, last_message_at=now,
                             subject="Pedido ART-000123 — consulta")
        s.add(thread)
        s.flush()
        s.add(EmailMessage(thread_id=thread.id,
                           gmail_message_id="gm-1",
                           gmail_account_user_id=uid,
                           direction="inbound",
                           from_email="client@example.fr",
                           to_emails_json="[]",
                           sent_at=now,
                           subject="Pedido ART-000123 — consulta"))
        _seed_alias(s)
        s.commit()
    with _patched_factusol():
        pre = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert pre["replies_to_thread"] is True
    assert pre["reply_to_message_id"] is not None
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "s", "body_text": "b", "lang": "fr",
                  "from_alias": "ventas@bomedia.net",
                  "reply_to_message_id": pre["reply_to_message_id"]},
            headers=auth_headers(http, "pedidos"),
        )
    assert mock_send.call_args.kwargs["in_reply_to_message_id"] == (
        pre["reply_to_message_id"]
    )


def test_send_invoice_creates_new_when_no_thread(http, session_factory) -> None:
    with session_factory() as s:
        _seed_order(s)   # sin hilos
        _seed_alias(s)
    with _patched_factusol():
        pre = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    # Sin hilo del contacto que referencie el pedido → correo nuevo.
    assert pre["replies_to_thread"] is False
    assert pre["reply_to_message_id"] is None


def test_send_invoice_attaches_pdf_with_readable_name(http, session_factory) -> None:
    with session_factory() as s:
        _seed_order(s, language="es")
        _seed_alias(s)
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "Factura 5-260063", "body_text": "Hola",
                  "lang": "es", "from_alias": "ventas@bomedia.net"},
            headers=auth_headers(http, "pedidos"),
        )
    name = mock_send.call_args.kwargs["attachments"][0]["filename"]
    assert name == "Factura_5-260063_DUPLICODER_S_L.pdf"


def test_send_failure_does_not_mark_as_sent(http, session_factory) -> None:
    from app.integrations.gmail.service import GmailScopeMissingError

    with session_factory() as s:
        order = _seed_order(s)
        oid = order.id
        _seed_alias(s)
    fail = patch("app.integrations.gmail.service.send_email",
                 side_effect=GmailScopeMissingError("sin scope"))
    with _patched_factusol(), fail:
        r = http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "s", "body_text": "b", "lang": "fr",
                  "from_alias": "ventas@bomedia.net"},
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "gmail_unavailable"
    # No hay evento de envío en el timeline (no se marcó como enviada).
    tl = http.get(f"/api/erp/orders/{oid}/timeline",
                  headers=auth_headers(http, "pedidos")).json()
    assert not any(
        e.get("detail", {}).get("factura") for e in tl.get("items", [])
    )


def test_send_logged_in_order_timeline(http, session_factory) -> None:
    with session_factory() as s:
        order = _seed_order(s, language="fr")
        oid = order.id
        _seed_alias(s)
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch:
        http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "Facture 5-260063", "body_text": "Bonjour",
                  "lang": "fr", "from_alias": "ventas@bomedia.net"},
            headers=auth_headers(http, "pedidos"),
        )
    tl = http.get(f"/api/erp/orders/{oid}/timeline",
                  headers=auth_headers(http, "pedidos")).json()
    evento = next(
        e for e in tl["items"]
        if e.get("detail", {}).get("factura") == "5-260063"
    )
    assert evento["detail"]["lang"] == "fr"
    assert "client@example.fr" in evento["detail"]["to"]


def test_send_invoice_rejects_alias_not_in_prefs(http, session_factory) -> None:
    with session_factory() as s:
        _seed_order(s)
        _seed_alias(s, alias="ventas@bomedia.net")
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        r = http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "s", "body_text": "b", "lang": "fr",
                  "from_alias": "ajeno@otro.com"},   # no está en prefs
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "alias_not_allowed"
    mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# Remitente por SERIE = empresa emisora (que la factura salga del alias de la
# empresa que emite, no del alias por defecto del usuario).
# ---------------------------------------------------------------------------


def test_invoice_email_sender_by_serie(http, session_factory) -> None:
    """El remitente del preview sale del alias de la EMPRESA EMISORA según la
    serie de la factura: serie 5 (Streamtec) → pedidos@streamtec.es; serie 2
    (MQ Europe / artisJet) → info@artisjet-printers.eu."""
    _ = session_factory
    tables = {
        "F_FAC": [_fac_row(),
                  _fac_row(TIPFAC="2", CODFAC=120001, REFFAC="ART-000200")],
        "F_LFA": [], "F_ALB": [], "F_FOP": [],
    }
    with _patched_factusol(tables):
        s5 = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
        s2 = http.get(
            "/api/erp/factusol/documents/facturas/2/120001/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert s5["from_alias"] == "pedidos@streamtec.es"
    assert s5["from_alias_source"] == "serie"
    assert s2["from_alias"] == "info@artisjet-printers.eu"
    assert s2["from_alias_source"] == "serie"


def test_sender_falls_back_when_serie_unconfigured(http, session_factory) -> None:
    """Si la serie no tiene remitente configurado (aquí se BORRA el default de
    la serie 5 poniéndolo vacío en /erp/settings), el preview cae al alias por
    defecto del usuario que envía — el comportamiento anterior."""
    with session_factory() as s:
        _seed_order(s)
        _seed_alias(s, alias="ventas@bomedia.net")
    # Un valor vacío en la config borra el default precargado de esa serie.
    http.patch("/api/erp/settings",
               json={"factusol_series_email_from": {"5": ""}},
               headers=auth_headers(http, "admin"))
    with _patched_factusol():
        pre = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert pre["from_alias"] == "ventas@bomedia.net"
    assert pre["from_alias_source"] == "usuario"


def test_sender_must_be_valid_sendas_alias(http, session_factory) -> None:
    """El alias de la serie DEBE ser un «enviar como» válido del usuario que
    envía: si no lo es, el envío se rechaza (nunca se envía desde una dirección
    no autorizada). En cuanto se da de alta como send-as del usuario, envía."""
    with session_factory() as s:
        _seed_order(s)
        _seed_alias(s, alias="ventas@bomedia.net")  # NO es pedidos@streamtec.es
    # El preview propone el alias de la serie 5 (empresa emisora).
    with _patched_factusol():
        pre = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert pre["from_alias"] == "pedidos@streamtec.es"
    assert pre["from_alias_source"] == "serie"
    # Enviar con ese alias, que NO es send-as del usuario → 403, no envía.
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        r = http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "s", "body_text": "b", "lang": "fr",
                  "from_alias": pre["from_alias"]},
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "alias_not_allowed"
    mock_send.assert_not_called()
    # Dado de alta el alias de la serie como send-as del usuario → sí envía.
    with session_factory() as s:
        _seed_alias(s, alias="pedidos@streamtec.es")
    ok_patch, _ = _patch_send()
    with _patched_factusol(), ok_patch as mock_ok:
        r2 = http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "s", "body_text": "b", "lang": "fr",
                  "from_alias": "pedidos@streamtec.es"},
            headers=auth_headers(http, "pedidos"),
        )
    assert r2.status_code == 201, r2.text
    mock_ok.assert_called_once()
    assert mock_ok.call_args.kwargs["from_alias"] == "pedidos@streamtec.es"


def test_preview_exposes_sender(http, session_factory) -> None:
    """El preview EXPONE el remitente que se usará (from_alias) y su
    procedencia (from_alias_source), para que la UI y el script de lote lo
    muestren antes de enviar."""
    _ = session_factory
    with _patched_factusol():
        pre = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert "from_alias" in pre
    assert "from_alias_source" in pre
    assert pre["from_alias"] == "pedidos@streamtec.es"
    assert pre["from_alias_source"] == "serie"


def test_invoice_email_settings_roundtrip(http, session_factory) -> None:
    _ = session_factory
    headers = auth_headers(http, "admin")
    r = http.get("/api/erp/settings", headers=headers)
    tpls = r.json()["factusol_invoice_email_templates"]
    assert tpls["fr"]["subject"] == "Facture {numero}"
    tpls["fr"]["subject"] = "Votre facture {numero}"
    r2 = http.patch("/api/erp/settings", json={
        "factusol_invoice_email_templates": tpls,
    }, headers=headers)
    assert (r2.json()["factusol_invoice_email_templates"]["fr"]["subject"]
            == "Votre facture {numero}")
