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
    ExternalSystem,
    UserEmailAliasPref,
)
from app.models.integration_settings import IntegrationAccount, IntegrationMode
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
    # El nº de PEDIDO acompaña al de factura en el asunto ({pedido}).
    assert body["subject"] == "Facture 5-260063 · commande ART-000123"
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
    assert tpls["fr"]["subject"] == "Facture {numero}{pedido}"
    tpls["fr"]["subject"] = "Votre facture {numero}"
    r2 = http.patch("/api/erp/settings", json={
        "factusol_invoice_email_templates": tpls,
    }, headers=headers)
    assert (r2.json()["factusol_invoice_email_templates"]["fr"]["subject"]
            == "Votre facture {numero}")


# ---------------------------------------------------------------------------
# «Enviar factura al cliente» desde la ficha: remitente por TIENDA (manda sobre
# la serie), nº de pedido en asunto/cuerpo, y ajustes por tienda.
# ---------------------------------------------------------------------------


def _seed_store(session: Session, slug: str = "boprint") -> IntegrationAccount:
    store = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug,
        display_name=slug.title(), enabled=True, mode=IntegrationMode.LIVE,
    )
    session.add(store)
    session.flush()
    return store


def _seed_store_order(session: Session, slug: str = "boprint") -> Order:
    """Como `_seed_order`, pero el pedido es de la TIENDA `slug`."""
    store = _seed_store(session, slug)
    order = _seed_order(session, language="es", email="cliente@example.es", country="ES")
    order.store_id = store.id
    session.commit()
    return order


def test_store_email_from_config_defaults_override_and_clear() -> None:
    from app.erp.invoice_email import store_email_from_config

    base = store_email_from_config(None)
    assert base["boprint"] == "pedidos@streamtec.es"
    assert base["artisjet"] == "info@artisjet-printers.eu"
    over = store_email_from_config({"boprint": "tienda@boprint.es", "Fluxlasers": ""})
    assert over["boprint"] == "tienda@boprint.es"
    assert "fluxlasers" not in over  # vacío borra el default (cae a la serie)


def test_invoice_email_sender_by_store_beats_serie(http, session_factory) -> None:
    """El remitente sale de la TIENDA del pedido (configurable); sin alias de
    tienda cae al de la serie; la previsualización declara la fuente."""
    with session_factory() as s:
        _seed_store_order(s, "boprint")
        _seed_alias(s)
    admin = auth_headers(http, "admin")
    # Tienda con alias propio → manda sobre la serie 5 (pedidos@streamtec.es).
    http.patch("/api/erp/settings", json={
        "factusol_store_email_from": {"boprint": "tienda@boprint.es"},
    }, headers=admin)
    with _patched_factusol():
        pre = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert pre["from_alias"] == "tienda@boprint.es"
    assert pre["from_alias_source"] == "tienda"
    assert pre["store"] == "boprint"
    # Sin alias de tienda (vacío) → cae al de la SERIE.
    http.patch("/api/erp/settings", json={
        "factusol_store_email_from": {"boprint": ""},
    }, headers=admin)
    with _patched_factusol():
        pre2 = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert pre2["from_alias"] == "pedidos@streamtec.es"
    assert pre2["from_alias_source"] == "serie"


def test_invoice_email_pedido_in_subject_and_body(http, session_factory) -> None:
    """El nº de pedido de BoHub va en el ASUNTO y en el CUERPO (placeholder
    {pedido}), además del nº de factura, en el idioma del pedido."""
    with session_factory() as s:
        _seed_order(s, language="es")
        _seed_alias(s)
    with _patched_factusol():
        pre = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert pre["order_number"] == "ART-000123"
    assert pre["subject"] == "Factura 5-260063 · pedido ART-000123"
    assert "5-260063" in pre["body_text"] and "ART-000123" in pre["body_text"]


def test_store_email_from_settings_roundtrip(http, session_factory) -> None:
    _ = session_factory
    headers = auth_headers(http, "admin")
    r = http.get("/api/erp/settings", headers=headers)
    stores = r.json()["factusol_store_email_from"]
    assert stores["boprint"] == "pedidos@streamtec.es"  # precargado
    r2 = http.patch("/api/erp/settings", json={
        "factusol_store_email_from": {"boprint": "tienda@boprint.es"},
    }, headers=headers)
    assert r2.json()["factusol_store_email_from"]["boprint"] == "tienda@boprint.es"
    # Las demás tiendas conservan su precarga.
    assert r2.json()["factusol_store_email_from"]["artisjet"] == "info@artisjet-printers.eu"


# ---------------------------------------------------------------------------
# Bugs #426 — destinatario / tienda de OTRO pedido homónimo, alias send-as
# oculto. Caso real: factura 5-260090 (fluxlasers, ref FLE-005784, Escola La
# Muntanyeta) proponía «Para» info@neonled.es (otro cliente) y «remitente de la
# tienda boprint». El pedido guarda el CODFAC desnudo y el mismo número existe
# en varias series: la búsqueda solo por número devolvía el primer homónimo.
# ---------------------------------------------------------------------------


def _fac_muntanyeta(**over: Any) -> dict[str, Any]:
    row = _fac_row(
        TIPFAC="5", CODFAC=260090, CLIFAC=3001, CNIFAC="G08000000",
        CNOFAC="ESCOLA LA MUNTANYETA", CPAFAC="España", REFFAC="FLE-005784",
    )
    row.update(over)
    return row


def _fac_neonled(**over: Any) -> dict[str, Any]:
    row = _fac_row(
        TIPFAC="2", CODFAC=260090, CLIFAC=2999, CNIFAC="B08999999",
        CNOFAC="NEON LED, S.L.", CPAFAC="España", REFFAC="BOP-099001",
    )
    row.update(over)
    return row


def _tables_homonimas() -> dict[str, list[dict[str, Any]]]:
    return {"F_FAC": [_fac_neonled(), _fac_muntanyeta()],
            "F_LFA": [], "F_ALB": [], "F_FOP": []}


def _seed_homonyms(
    session: Session, *, escola_serie: int | None = None, neon_serie: int | None = 2,
    flux_prefix: str | None = "FLE", link_escola_company: bool = True,
) -> tuple[Order, Order]:
    """Dos pedidos con el MISMO nº de factura desnudo (260090): el de boprint
    (NEON LED, otro cliente) creado ANTES — el que devolvía la búsqueda por
    número — y el de fluxlasers (Escola La Muntanyeta)."""
    boprint = _seed_store(session, "boprint")
    flux = _seed_store(session, "fluxlasers")
    if flux_prefix:
        flux.metadata_json = f'{{"factusol_ref_prefix": "{flux_prefix}"}}'
    neon_contact = Contact(first_name="Neon", last_name="Led", email="info@neonled.es")
    esc_contact = Contact(first_name="Escola", last_name="Muntanyeta",
                          email="escola@muntanyeta.cat")
    session.add_all([neon_contact, esc_contact])
    neon_co = Company(name="NEON LED, S.L.", source="manual",
                      factusol_company_id="2999", country="ES", language="es")
    esc_co = Company(name="Escola La Muntanyeta", source="manual",
                     factusol_company_id="3001" if link_escola_company else None,
                     country="ES", language="es")
    session.add_all([neon_co, esc_co])
    session.flush()
    neon = Order(
        external_source=OrderSource.WOOCOMMERCE, order_number="BOPRIN-99001",
        store_id=boprint.id, contact_id=neon_contact.id, company_id=neon_co.id,
        total_amount=100, currency="EUR", factusol_invoice_number="260090",
        factusol_invoice_serie=neon_serie, language="es",
    )
    session.add(neon)
    session.flush()
    escola = Order(
        external_source=OrderSource.WOOCOMMERCE, order_number="FLUXLA-5784",
        store_id=flux.id, contact_id=esc_contact.id, company_id=esc_co.id,
        total_amount=300, currency="EUR", factusol_invoice_number="260090",
        factusol_invoice_serie=escola_serie, language="es",
    )
    session.add(escola)
    session.commit()
    return neon, escola


def _gmail_aliases(*emails: str):
    """Parchea la lista EN VIVO de send-as de Gmail (lo que devuelve la API)."""
    return patch(
        "app.integrations.gmail.service.list_aliases",
        return_value=[
            {"send_as_email": e, "display_name": e.split("@")[0].title(),
             "is_primary": False, "is_default": False,
             "verification_status": "accepted"}
            for e in emails
        ],
    )


def test_preview_recipient_and_store_are_of_this_invoice_not_homonym(
    http, session_factory,
) -> None:
    """5-260090 es de Escola (fluxlasers): «Para», tienda, idioma y nº de
    pedido son los suyos aunque un pedido de boprint (NEON LED) tenga el mismo
    nº de factura desnudo en otra serie. Y 2-260090 sigue siendo de NEON."""
    with session_factory() as s:
        _seed_homonyms(s)
        _seed_alias(s)
    with _patched_factusol(_tables_homonimas()):
        esc = http.get(
            "/api/erp/factusol/documents/facturas/5/260090/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
        neon = http.get(
            "/api/erp/factusol/documents/facturas/2/260090/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert esc["to"] == "escola@muntanyeta.cat"
    assert esc["order_number"] == "FLUXLA-5784"
    assert esc["store"] == "fluxlasers"
    assert esc["from_alias_source"] == "tienda"
    assert esc["customer_mismatch"] is False
    assert "FLUXLA-5784" in esc["subject"]
    assert neon["to"] == "info@neonled.es"
    assert neon["store"] == "boprint"
    assert neon["order_number"] == "BOPRIN-99001"


def test_find_order_for_invoice_never_guesses_between_homonyms(session_factory) -> None:
    """Sin serie guardada, sin referencia que coincida y sin cliente enlazado,
    un pedido homónimo NO se elige (antes se devolvía el primero)."""
    from app.erp.factusol_pdf import find_order_for_invoice

    with session_factory() as s:
        # Ninguno de los dos guarda serie, la tienda flux no tiene prefijo FLE
        # (su ref derivada es FLU-005784 ≠ FLE-005784) y Escola no está
        # enlazada a FACTUSOL: no hay prueba de cuál es → None.
        neon, escola = _seed_homonyms(s, neon_serie=None, flux_prefix=None,
                                      link_escola_company=False)
        assert find_order_for_invoice(
            s, serie=5, codigo=260090, referencia="FLE-005784", cliente_codigo="3001",
        ) is None
        # Con la serie guardada en el de Escola, se elige sin dudar…
        escola.factusol_invoice_serie = 5
        s.flush()
        found = find_order_for_invoice(
            s, serie=5, codigo=260090, referencia="FLE-005784", cliente_codigo="3001",
        )
        assert found is not None and found.id == escola.id
        # …y para la serie 2 ese pedido (serie 5) queda descartado; NEON, sin
        # serie ni pruebas, tampoco se adivina.
        assert find_order_for_invoice(
            s, serie=2, codigo=260090, referencia="", cliente_codigo="",
        ) is None
        # Con su referencia (BOP-099001) sí es NEON.
        found2 = find_order_for_invoice(
            s, serie=2, codigo=260090, referencia="BOP-099001", cliente_codigo="",
        )
        assert found2 is not None and found2.id == neon.id


def test_preview_by_customer_link_when_serie_unknown(http, session_factory) -> None:
    """Sin serie guardada ni prefijo de tienda, la empresa del pedido enlazada
    al CLIFAC de la factura basta para elegir el pedido de Escola (y solo él)."""
    with session_factory() as s:
        _seed_homonyms(s, neon_serie=None, flux_prefix=None)
        _seed_alias(s)
    with _patched_factusol(_tables_homonimas()):
        esc = http.get(
            "/api/erp/factusol/documents/facturas/5/260090/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert esc["to"] == "escola@muntanyeta.cat"
    assert esc["store"] == "fluxlasers"


def test_preview_with_order_id_uses_that_order_and_rejects_foreign(
    http, session_factory,
) -> None:
    """Desde la ficha se pasa `order_id`: el destinatario, la tienda y el nº
    de pedido son los de ESE pedido; y si la factura no es suya → 409."""
    with session_factory() as s:
        neon, escola = _seed_homonyms(s, flux_prefix=None)  # sin prefijo: por nº
        neon_id, escola_id = neon.id, escola.id
        _seed_alias(s)
    with _patched_factusol(_tables_homonimas()):
        ok = http.get(
            f"/api/erp/factusol/documents/facturas/5/260090/email-preview?order_id={escola_id}",
            headers=auth_headers(http, "pedidos"),
        )
        bad = http.get(
            f"/api/erp/factusol/documents/facturas/5/260090/email-preview?order_id={neon_id}",
            headers=auth_headers(http, "pedidos"),
        )
    assert ok.status_code == 200, ok.text
    assert ok.json()["to"] == "escola@muntanyeta.cat"
    assert ok.json()["order_id"] == escola_id
    assert ok.json()["store"] == "fluxlasers"
    assert bad.status_code == 409
    assert bad.json()["detail"]["code"] == "order_invoice_mismatch"


def test_send_with_order_id_records_timeline_on_that_order_only(
    http, session_factory,
) -> None:
    with session_factory() as s:
        neon, escola = _seed_homonyms(s)
        neon_id, escola_id = neon.id, escola.id
        _seed_alias(s)
    send_patch, _ = _patch_send()
    with _patched_factusol(_tables_homonimas()), send_patch as mock_send:
        r = http.post(
            "/api/erp/factusol/documents/facturas/5/260090/email",
            json={"confirm": True, "to": ["escola@muntanyeta.cat"],
                  "subject": "Factura 5-260090", "body_text": "Hola",
                  "lang": "es", "from_alias": "ventas@bomedia.net",
                  "order_id": escola_id},
            headers=auth_headers(http, "pedidos"),
        )
        # La factura de NEON (2-260090) desde la ficha de Escola → 409, no envía.
        bad = http.post(
            "/api/erp/factusol/documents/facturas/2/260090/email",
            json={"confirm": True, "to": ["escola@muntanyeta.cat"],
                  "subject": "s", "body_text": "b", "lang": "es",
                  "from_alias": "ventas@bomedia.net", "order_id": escola_id},
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 201, r.text
    assert bad.status_code == 409
    assert mock_send.call_count == 1
    tl_esc = http.get(f"/api/erp/orders/{escola_id}/timeline",
                      headers=auth_headers(http, "pedidos")).json()
    tl_neon = http.get(f"/api/erp/orders/{neon_id}/timeline",
                       headers=auth_headers(http, "pedidos")).json()
    assert any(e.get("detail", {}).get("factura") == "5-260090" for e in tl_esc["items"])
    assert not any(e.get("detail", {}).get("factura") for e in tl_neon["items"])


def test_preview_flags_customer_mismatch(http, session_factory) -> None:
    """Si el CLIFAC de la factura no es la empresa del pedido, el preview lo
    dice (`customer_mismatch`) en vez de callar: el operador revisa el «Para»."""
    with session_factory() as s:
        _, escola = _seed_homonyms(s)
        escola_id = escola.id
    tables = {"F_FAC": [_fac_muntanyeta(CLIFAC=2999, CNOFAC="NEON LED, S.L.")],
              "F_LFA": [], "F_ALB": [], "F_FOP": []}
    with _patched_factusol(tables):
        pre = http.get(
            f"/api/erp/factusol/documents/facturas/5/260090/email-preview?order_id={escola_id}",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert pre["customer_mismatch"] is True
    assert pre["invoice_customer"] == "NEON LED, S.L."
    assert pre["to"] == "escola@muntanyeta.cat"  # sigue siendo el del pedido


# --- alias send-as: remitente configurado en Ajustes ERP verificado en Gmail ---


def test_send_accepts_erp_sender_verified_in_gmail_even_if_hidden(
    http, session_factory,
) -> None:
    """pedidos@streamtec.es no es el email del usuario → el sync lo deja
    oculto (is_allowed=0). Como es un remitente configurado en Ajustes ERP y
    Gmail lo tiene como send-as verificado, el ERP lo acepta (y refleja el
    alias en el espejo local sin hacerlo visible en el compositor)."""
    with session_factory() as s:
        _seed_order(s)
        _seed_alias(s, alias="ventas@bomedia.net")  # el propio; NO streamtec
    with _patched_factusol(), _gmail_aliases("ventas@bomedia.net", "pedidos@streamtec.es"):
        pre = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert pre["from_alias"] == "pedidos@streamtec.es"
    assert pre["from_alias_ok"] is True
    send_patch, _ = _patch_send()
    with (_patched_factusol(), _gmail_aliases("pedidos@streamtec.es"),
          send_patch as mock_send):
        r = http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "s", "body_text": "b", "lang": "fr",
                  "from_alias": "pedidos@streamtec.es"},
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 201, r.text
    assert mock_send.call_args.kwargs["from_alias"] == "pedidos@streamtec.es"
    with session_factory() as s:
        from app.models.crm import User
        uid = s.query(User).filter_by(email="pedidos@example.com").one().id
        row = s.query(UserEmailAliasPref).filter_by(
            user_id=uid, alias_email="pedidos@streamtec.es").one()
        assert row.is_allowed is False  # espejo, no visible en el compositor


def test_send_rejects_erp_sender_not_verified_in_gmail(http, session_factory) -> None:
    with session_factory() as s:
        _seed_order(s)
        _seed_alias(s, alias="ventas@bomedia.net")
    with _patched_factusol(), _gmail_aliases("ventas@bomedia.net"):
        pre = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/email-preview",
            headers=auth_headers(http, "pedidos"),
        ).json()
    assert pre["from_alias_ok"] is False
    assert pre["from_alias_problem"] == "not_in_gmail"
    send_patch, _ = _patch_send()
    with _patched_factusol(), _gmail_aliases("ventas@bomedia.net"), send_patch as mock_send:
        r = http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "s", "body_text": "b", "lang": "fr",
                  "from_alias": "pedidos@streamtec.es"},
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "alias_not_allowed"
    assert r.json()["detail"]["reason"] == "not_in_gmail"
    mock_send.assert_not_called()


def test_send_rejects_unconfigured_alias_even_if_in_gmail(http, session_factory) -> None:
    """Un alias que NO está en Ajustes ERP ni en las preferencias del usuario
    no se acepta aunque Gmail lo tenga: nadie suplanta un alias ajeno."""
    with session_factory() as s:
        _seed_order(s)
        _seed_alias(s, alias="ventas@bomedia.net")
    send_patch, _ = _patch_send()
    with (_patched_factusol(), _gmail_aliases("bart@bomedia.net") as gmail,
          send_patch as mock_send):
        r = http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "s", "body_text": "b", "lang": "fr",
                  "from_alias": "bart@bomedia.net"},
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "alias_not_allowed"
    gmail.assert_not_called()
    mock_send.assert_not_called()


def test_hidden_mirror_row_allows_erp_sender_when_gmail_unavailable(
    http, session_factory,
) -> None:
    """Si Gmail no responde, vale el espejo local del sync aunque el alias
    esté oculto (is_allowed=0): lo puso ahí el propio sync desde Gmail."""
    from app.integrations.gmail.service import GmailNotConnectedError
    from app.models.crm import User

    with session_factory() as s:
        _seed_order(s)
        uid = s.query(User).filter_by(email="pedidos@example.com").one().id
        s.add(UserEmailAliasPref(user_id=uid, alias_email="pedidos@streamtec.es",
                                 is_allowed=False))
        s.commit()
    down = patch("app.integrations.gmail.service.list_aliases",
                 side_effect=GmailNotConnectedError("no"))
    send_patch, _ = _patch_send()
    with _patched_factusol(), down, send_patch as mock_send:
        r = http.post(
            "/api/erp/factusol/documents/facturas/5/260063/email",
            json={"confirm": True, "to": ["client@example.fr"],
                  "subject": "s", "body_text": "b", "lang": "fr",
                  "from_alias": "pedidos@streamtec.es"},
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 201, r.text
    mock_send.assert_called_once()


# --- diagnóstico solo lectura ------------------------------------------------


def test_diagnose_invoice_link_reports_old_vs_new(session_factory) -> None:
    from app.erp.invoice_email_diag import diagnose_invoice_link, format_report

    with session_factory() as s:
        _seed_homonyms(s)
        client = FakeClient(_tables_homonimas())
        diag = diagnose_invoice_link(s, client, serie=5, codigo=260090, ejercicio="2026")
        assert diag["factura"]["cliente_codigo"] == "3001"
        assert diag["empresa_crm"]["nombre"] == "Escola La Muntanyeta"
        assert {c["order_number"] for c in diag["candidatos"]} == {"BOPRIN-99001", "FLUXLA-5784"}
        verdicts = {c["order_number"]: c["veredicto"] for c in diag["candidatos"]}
        assert verdicts["BOPRIN-99001"].startswith("DESCARTADO")
        assert diag["antiguo"] == "BOPRIN-99001"   # lo que devolvía la búsqueda por nº
        assert diag["nuevo"] == "FLUXLA-5784"
        assert diag["destinatario"] == "escola@muntanyeta.cat"
        report = format_report(diag)
        assert "FLE-005784" in report and "escola@muntanyeta.cat" in report
        # Nada escrito en FACTUSOL: solo lecturas.
        assert all(t in ("F_FAC", "F_LFA") for t, _ in client.calls)


def test_same_serie_homonyms_resolved_by_reffac_then_clifac(session_factory) -> None:
    """Caso real (#427 remate): boprint y fluxlasers comparten la serie 5. La
    factura 5-260090 (REFFAC FLE-005784, CLIFAC 4391 «Escola La Muntanyeta»)
    debe resolver a FLUXLA-5784 y no al homónimo BOPRIN-99927 (ref BOP-,
    cliente 3892) aunque ambos guarden nº 260090 y serie 5. Sin REFFAC ni
    CLIFAC que desempaten → ambigüedad real → None."""
    from app.erp.factusol_pdf import find_order_for_invoice

    with session_factory() as s:
        boprint = _seed_store(s, "boprint")
        flux = _seed_store(s, "fluxlasers")
        flux.metadata_json = '{"factusol_ref_prefix": "FLE"}'
        neon_co = Company(name="NEON LED, S.L.", source="manual",
                          factusol_company_id="3892", country="ES")
        esc_co = Company(name="Escola La Muntanyeta", source="manual",
                         factusol_company_id="4391", country="ES")
        s.add_all([neon_co, esc_co])
        s.flush()
        neon = Order(external_source=OrderSource.WOOCOMMERCE, order_number="BOPRIN-99927",
                     store_id=boprint.id, company_id=neon_co.id, total_amount=1,
                     currency="EUR", factusol_invoice_number="260090",
                     factusol_invoice_serie=5)
        escola = Order(external_source=OrderSource.WOOCOMMERCE, order_number="FLUXLA-5784",
                       store_id=flux.id, company_id=esc_co.id, total_amount=1,
                       currency="EUR", factusol_invoice_number="260090",
                       factusol_invoice_serie=5)
        s.add_all([neon, escola])
        s.commit()
        # Por REFFAC.
        found = find_order_for_invoice(
            s, serie=5, codigo=260090, referencia="FLE-005784", cliente_codigo="4391",
        )
        assert found is not None and found.order_number == "FLUXLA-5784"
        # Solo por CLIFAC (sin REFFAC en la factura).
        found = find_order_for_invoice(
            s, serie=5, codigo=260090, referencia="", cliente_codigo="3892",
        )
        assert found is not None and found.order_number == "BOPRIN-99927"
        # REFFAC de otro y CLIFAC de Escola: la referencia no casa con nadie →
        # decide el cliente.
        found = find_order_for_invoice(
            s, serie=5, codigo=260090, referencia="XXX-000001", cliente_codigo="4391",
        )
        assert found is not None and found.order_number == "FLUXLA-5784"
        # Sin nada que desempate → no se adivina.
        assert find_order_for_invoice(
            s, serie=5, codigo=260090, referencia="", cliente_codigo="",
        ) is None


def test_scan_invoice_links_flags_crossed_and_ambiguous(session_factory) -> None:
    """Bloque 1c · escaneo solo lectura: NEON (serie 5, nº 260090, cliente
    3892) apunta a la factura 5-260090 de Escola (CLIFAC 4391) → «cruzado»;
    Escola → «ok»; un pedido sin serie con el nº en dos series y sin
    referencia/cliente que desempaten → «ambiguo». Nada se escribe."""
    from app.erp.invoice_link_scan import format_report, scan_invoice_links, to_csv

    with session_factory() as s:
        boprint = _seed_store(s, "boprint")
        flux = _seed_store(s, "fluxlasers")
        flux.metadata_json = '{"factusol_ref_prefix": "FLE"}'
        neon_co = Company(name="NEON LED, S.L.", source="manual",
                          factusol_company_id="3892", country="ES")
        esc_co = Company(name="Escola La Muntanyeta", source="manual",
                         factusol_company_id="4391", country="ES")
        s.add_all([neon_co, esc_co])
        s.flush()
        s.add_all([
            Order(external_source=OrderSource.WOOCOMMERCE, order_number="BOPRIN-99927",
                  store_id=boprint.id, company_id=neon_co.id, total_amount=1,
                  currency="EUR", factusol_invoice_number="260090", factusol_invoice_serie=5),
            Order(external_source=OrderSource.WOOCOMMERCE, order_number="FLUXLA-5784",
                  store_id=flux.id, company_id=esc_co.id, total_amount=1,
                  currency="EUR", factusol_invoice_number="260090", factusol_invoice_serie=5),
            Order(external_source=OrderSource.MANUAL, order_number="MANUAL-000777",
                  total_amount=1, currency="EUR", factusol_invoice_number="260050"),
        ])
        s.commit()
        client = FakeClient({"F_FAC": [
            _fac_row(TIPFAC="5", CODFAC=260090, CLIFAC=4391, CNOFAC="ESCOLA LA MUNTANYETA",
                     REFFAC="FLE-005784"),
            _fac_row(TIPFAC="2", CODFAC=260050, CLIFAC=10, CNOFAC="A", REFFAC=""),
            _fac_row(TIPFAC="5", CODFAC=260050, CLIFAC=11, CNOFAC="B", REFFAC=""),
        ]})
        scan = scan_invoice_links(s, client, ejercicio="2026")
        by_number = {r["order_number"]: r for r in scan["filas"]}
        assert by_number["BOPRIN-99927"]["categoria"] == "cruzado"
        assert by_number["BOPRIN-99927"]["cnofac"] == "ESCOLA LA MUNTANYETA"
        assert by_number["FLUXLA-5784"]["categoria"] == "ok"
        assert by_number["MANUAL-000777"]["categoria"] == "ambiguo"
        assert by_number["MANUAL-000777"]["series_disponibles"] == "2,5"
        assert scan["totales"] == {"cruzado": 1, "ok": 1, "ambiguo": 1}
        report = format_report(scan)
        assert "[CRUZADO ] BOPRIN-99927" in report and "FLUXLA-5784" not in report
        assert "BOPRIN-99927" in to_csv(scan)
        # Solo lecturas de F_FAC.
        assert {t for t, _ in client.calls} == {"F_FAC"}


# ---------------------------------------------------------------------------
# Lote 2 · PR-2 — Ajustes ERP: «Ver ejemplo» y «Enviarme una prueba» de las
# plantillas (datos de muestra; la prueba sale por Gmail sin PDF).
# ---------------------------------------------------------------------------


def test_settings_template_preview_renders_sample(http, session_factory) -> None:
    """La previsualización rellena la plantilla del idioma con los datos de
    muestra (cliente, nº de factura, nº de pedido y referencia) con la misma
    sustitución que el envío real. Con `subject`/`body` usa lo que se está
    escribiendo; vacíos → la guardada/por defecto. Vale para quien solo VE."""
    _ = session_factory
    headers = auth_headers(http, "pedidos")
    r = http.post("/api/erp/settings/invoice-email/preview", json={"lang": "fr"},
                  headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lang"] == "fr"
    assert body["subject"] == "Facture 5-000118 · commande BP-2479"
    assert "Rotulación Levante S.L." in body["body_text"]
    assert "votre réf. BOP-002479" in body["body_text"]
    assert body["body_html"].startswith("<p>")
    assert "<br/>" in body["body_html"]  # mismo HTML que el envío real
    assert body["sample"]["numero"] == "5-000118"
    # Sin serie por defecto ni tiendas: el remitente de ejemplo es el alias
    # del propio usuario.
    assert body["from_alias_example"] == "pedidos@example.com"
    assert body["from_alias_source"] == "usuario"
    # Texto aún sin guardar → se usa tal cual.
    r2 = http.post("/api/erp/settings/invoice-email/preview", json={
        "lang": "es", "subject": "Su factura {numero}{pedido}",
        "body": "Hola {cliente}:\n\nva{referencia}.",
    }, headers=headers)
    assert r2.json()["subject"] == "Su factura 5-000118 · pedido BP-2479"
    # {referencia} lleva su propio separador (« (su ref. X)»), como en el envío.
    assert r2.json()["body_text"] == "Hola Rotulación Levante S.L.:\n\nva (su ref. BOP-002479)."
    # Solo espacios = vacío → la plantilla por defecto del idioma.
    r3 = http.post("/api/erp/settings/invoice-email/preview", json={
        "lang": "es", "subject": "   ", "body": "",
    }, headers=headers)
    assert r3.json()["subject"] == "Factura 5-000118 · pedido BP-2479"
    # Idioma no soportado → español.
    r4 = http.post("/api/erp/settings/invoice-email/preview", json={"lang": "it"},
                   headers=headers)
    assert r4.json()["lang"] == "es"


def test_settings_template_preview_sender_follows_default_serie(
    http, session_factory,
) -> None:
    """El remitente de ejemplo sigue el orden del envío real sin pedido: el
    de la SERIE por defecto; si no tiene, el de la primera tienda con
    remitente; si no, el del usuario."""
    admin = auth_headers(http, "admin")
    http.patch("/api/erp/settings", json={"factusol_series_default": "5"}, headers=admin)
    r = http.post("/api/erp/settings/invoice-email/preview", json={"lang": "es"},
                  headers=admin)
    assert r.json()["from_alias_example"] == "pedidos@streamtec.es"
    assert r.json()["from_alias_source"] == "serie"
    assert r.json()["from_alias_scope"] == "5"
    # Serie por defecto sin remitente → cae a la primera tienda con alias.
    with session_factory() as s:
        _seed_store(s, "artisjet")
        s.commit()
    http.patch("/api/erp/settings", json={"factusol_series_email_from": {"5": ""}},
               headers=admin)
    r2 = http.post("/api/erp/settings/invoice-email/preview", json={"lang": "es"},
                   headers=admin)
    assert r2.json()["from_alias_example"] == "info@artisjet-printers.eu"
    assert r2.json()["from_alias_source"] == "tienda"
    assert r2.json()["from_alias_scope"] == "artisjet"


def test_settings_template_test_send_to_me(http, session_factory) -> None:
    """«Enviarme una prueba»: envía por Gmail la plantilla rellena con los
    datos de muestra al propio usuario, desde el remitente configurado (serie
    por defecto), sin PDF, con «[Prueba]» delante del asunto, y lo audita."""
    from app.models.crm import AuditLog

    admin = auth_headers(http, "admin")
    http.patch("/api/erp/settings", json={"factusol_series_default": "5"}, headers=admin)
    with session_factory() as s:
        _seed_alias(s, role="admin", alias="pedidos@streamtec.es")
    send_patch, _ = _patch_send()
    with send_patch as mock_send:
        r = http.post("/api/erp/settings/invoice-email/test-send", json={
            "lang": "es", "subject": "Su factura {numero}{pedido}",
        }, headers=admin)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sent"] is True
    assert body["to"] == "admin@example.com"
    assert body["from_alias"] == "pedidos@streamtec.es"
    assert body["from_alias_source"] == "serie"
    assert body["subject"] == "[Prueba] Su factura 5-000118 · pedido BP-2479"
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to"] == ["admin@example.com"]
    assert kwargs["from_alias"] == "pedidos@streamtec.es"
    assert kwargs["subject"] == "[Prueba] Su factura 5-000118 · pedido BP-2479"
    assert "Rotulación Levante S.L." in kwargs["body_text"]
    assert kwargs["body_html"].startswith("<p>")
    assert kwargs.get("attachments") is None  # sin PDF: es una prueba del texto
    assert kwargs["contact_id"] is None
    with session_factory() as s:
        rows = s.query(AuditLog).filter_by(action="erp.settings_template_test_sent").all()
        assert len(rows) == 1
        assert "admin@example.com" in (rows[0].message or "")
    # `to` explícito → a esa dirección.
    send_patch2, _ = _patch_send()
    with send_patch2 as mock_send2:
        r2 = http.post("/api/erp/settings/invoice-email/test-send", json={
            "lang": "en", "to": "otro@example.com",
        }, headers=admin)
    assert r2.status_code == 201, r2.text
    assert mock_send2.call_args.kwargs["to"] == ["otro@example.com"]
    assert r2.json()["subject"] == "[Prueba] Invoice 5-000118 · order BP-2479"


def test_settings_template_test_send_rejects_unusable_sender(
    http, session_factory,
) -> None:
    """Si el remitente configurado no es un «enviar como» utilizable por el
    usuario, 403 con el motivo y NO se envía nada."""
    _ = session_factory
    admin = auth_headers(http, "admin")
    http.patch("/api/erp/settings", json={"factusol_series_default": "5"}, headers=admin)
    send_patch, _ = _patch_send()
    with _gmail_aliases("admin@example.com"), send_patch as mock_send:
        r = http.post("/api/erp/settings/invoice-email/test-send", json={"lang": "es"},
                      headers=admin)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "alias_not_allowed"
    assert r.json()["detail"]["reason"] == "not_in_gmail"
    assert r.json()["detail"]["from_alias"] == "pedidos@streamtec.es"
    assert "pedidos@streamtec.es" in r.json()["detail"]["detail"]
    mock_send.assert_not_called()
    # Destinatario inválido → 400 antes de tocar Gmail.
    send_patch2, _ = _patch_send()
    with send_patch2 as mock_send2:
        r2 = http.post("/api/erp/settings/invoice-email/test-send", json={
            "lang": "es", "to": "esto no es un email",
        }, headers=admin)
    assert r2.status_code == 400
    mock_send2.assert_not_called()


def test_settings_template_test_send_requires_admin(http, session_factory) -> None:
    """La prueba sale desde un alias de la organización: solo ADMIN (igual
    que guardar las plantillas). Ver el ejemplo sí puede cualquiera del ERP."""
    _ = session_factory
    send_patch, _ = _patch_send()
    with send_patch as mock_send:
        r = http.post("/api/erp/settings/invoice-email/test-send", json={"lang": "es"},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 403
    mock_send.assert_not_called()
