"""Punto A (lote Proformas) — enviar el presupuesto / proforma por email.

Reutiliza el flujo de la factura: plantillas por idioma, remitente por serie,
contactos de la empresa vinculada, PDF adjunto y envío por Gmail (mockeado).
Se comprueba la previsualización (idioma por cascada y por selector,
premarcado del contacto vinculado, remitente de la serie, nombre del adjunto),
el envío (adjunto PDF, evento `erp.proforma_emailed` y marca en el listado),
el reenvío (segundo evento) y que sin destinatarios / sin confirmación / sin
Gmail NO se envía ni se registra nada. Nada toca FACTUSOL.
"""
from __future__ import annotations

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
from app.main import app
from app.models.crm import AuditLog, Company, Contact, UserEmailAliasPref
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


def _pre_row(**over: Any) -> dict[str, Any]:
    row = {
        "TIPPRE": "2", "CODPRE": 75, "FECPRE": "2026-10-02T00:00:00",
        "CLIPRE": 2458, "CNIPRE": "FR16339753527", "CNOPRE": "LA MAISON DE LA PLAQUE",
        "CDOPRE": "Rue du Chemin Noir 5", "CCPPRE": "21120", "CPOPRE": "Is-sur-Tille",
        "CPAPRE": "250", "FOPPRE": "002", "REFPRE": "Placas",
        "CEMPRE": "marta@maison.example",
        "TOTPRE": 1234.56, "NET1PRE": 1020.3, "BAS1PRE": 1020.3,
        "PIVA1PRE": 21, "IIVA1PRE": 214.26,
    }
    row.update(over)
    return row


def _tables(**over: Any) -> dict[str, list[dict[str, Any]]]:
    tables = {"F_PRE": [_pre_row()], "F_LPS": [], "F_FOP": [], "F_FPA": [], "F_CLI": []}
    tables.update(over)
    return tables


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


def _seed_company(session: Session, *, language: str | None = "fr",
                  country: str = "FR") -> Company:
    """Empresa CRM vinculada al cliente 2458 con dos contactos: el de la
    cabecera (CEMPRE) y otro con email; y uno sin email."""
    company = Company(name="La Maison de la Plaque", source="manual",
                      factusol_company_id="2458", language=language, country=country)
    session.add(company)
    session.flush()
    session.add_all([
        Contact(first_name="Marta", last_name="Coll", email="marta@maison.example",
                company_id=company.id),
        Contact(first_name="Jean", last_name="Dupont", email="jean@maison.example",
                company_id=company.id),
        Contact(first_name="Sin", last_name="Email", email=None, company_id=company.id),
    ])
    session.commit()
    return company


def _seed_alias(session: Session, alias: str, role: str = "pedidos") -> None:
    from app.models.crm import User
    user = session.query(User).filter_by(email=f"{role}@example.com").one()
    session.add(UserEmailAliasPref(user_id=user.id, alias_email=alias, is_allowed=True))
    session.commit()


def _events(session: Session) -> list[AuditLog]:
    return list(session.scalars(
        select(AuditLog).where(AuditLog.action == "erp.proforma_emailed")
        .order_by(AuditLog.created_at)
    ))


# ---------------------------------------------------------------------------
# Plantillas y marcadores
# ---------------------------------------------------------------------------


def test_quote_email_defaults_y_marcadores(session_factory) -> None:
    from app.erp.quote_email import QUOTE_EMAIL_DEFAULTS, render_quote_email

    assert set(QUOTE_EMAIL_DEFAULTS) == {"es", "en", "de", "fr", "nl"}
    fields = {"numero": "2-000075", "empresa": "La Maison", "contacto": "Marta Coll",
              "total": "1.234,56 €", "fecha": "02/10/2026",
              "validez": "Devis valable 30 jours.", "firma": "MQ Europe"}
    with session_factory() as s:
        subject, body = render_quote_email(s, lang="fr", fields=fields)
        assert subject == "Devis 2-000075"
        for valor in ("Marta Coll", "2-000075", "02/10/2026", "1.234,56 €",
                      "Devis valable 30 jours.", "MQ Europe"):
            assert valor in body
        assert "{" not in body                       # ningún marcador sin sustituir
        # Sin validez, la línea desaparece sin dejar un hueco doble.
        _, sin = render_quote_email(s, lang="es", fields={**fields, "validez": ""})
        assert "\n\n\n" not in sin
        # Idioma no soportado → español.
        assert render_quote_email(s, lang="it", fields=fields)[0] == "Presupuesto 2-000075"


def test_plantillas_de_presupuesto_se_guardan_y_previsualizan(http, session_factory) -> None:
    r = http.patch("/api/erp/settings", headers=auth_headers(http, "admin"), json={
        "factusol_quote_email_templates": {
            "es": {"subject": "Su presupuesto {numero} de {firma}", "body": ""},
        },
    })
    assert r.status_code == 200, r.text
    got = http.get("/api/erp/settings", headers=auth_headers(http, "admin")).json()
    tpl = got["factusol_quote_email_templates"]
    assert tpl["es"]["subject"] == "Su presupuesto {numero} de {firma}"
    assert "Adjuntamos el presupuesto" in tpl["es"]["body"]          # el cuerpo cae al default
    assert tpl["fr"]["subject"] == "Devis {numero}"
    # Ejemplo con datos de muestra: lo guardado manda y los marcadores se rellenan.
    pre = http.post("/api/erp/settings/quote-email/preview",
                    headers=auth_headers(http, "pedidos"), json={"lang": "es"})
    assert pre.status_code == 200, pre.text
    assert pre.json()["subject"] == "Su presupuesto 2-000075 de MQ Europe"
    assert "Marta Coll" in pre.json()["body_text"]


# ---------------------------------------------------------------------------
# Previsualización
# ---------------------------------------------------------------------------


def test_preview_idioma_por_cascada_contactos_premarcados_y_remitente_por_serie(
    http, session_factory,
) -> None:
    with session_factory() as s:
        _seed_company(s, language="fr")
        _seed_alias(s, "info@artisjet-printers.eu")
    with _patched_factusol():
        r = http.get("/api/erp/factusol/quotes/75/email-preview?serie=2",
                     headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["numero"] == "2-000075"
    # Idioma: el explícito de la empresa (fr) → asunto y cuerpo en francés.
    assert (body["lang"], body["lang_source"]) == ("fr", "cliente")
    assert body["subject"] == "Devis 2-000075"
    assert "Marta Coll" in body["body_text"]             # {contacto} = el premarcado
    assert "1.234,56 €" in body["body_text"]              # {total}
    assert body["markers"]["empresa"] == "La Maison de la Plaque"
    # Contactos de la empresa vinculada: el de la cabecera premarcado; sin email, no.
    contactos = {c["name"]: c for c in body["company_contacts"]}
    assert contactos["Marta Coll"]["is_primary"] is True
    assert contactos["Jean Dupont"]["is_primary"] is False
    assert contactos["Sin Email"]["has_email"] is False
    # Remitente: el de la serie 2 (MQ Europe), utilizable (preferencia del usuario).
    assert (body["from_alias"], body["from_alias_source"]) == ("info@artisjet-printers.eu", "serie")
    assert body["from_alias_ok"] is True
    assert body["attachment_filename"] == "Presupuesto-2-000075.pdf"
    assert body["company_name"] == "La Maison de la Plaque"


def test_preview_idioma_por_selector_y_variante_proforma(http, session_factory) -> None:
    with session_factory() as s:
        _seed_company(s, language="fr")
    with _patched_factusol():
        r = http.get("/api/erp/factusol/quotes/75/email-preview?serie=2&lang=de&variant=proforma",
                     headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["lang"], body["lang_source"]) == ("de", "selector")
    assert body["subject"] == "Angebot 2-000075"
    assert body["attachment_filename"] == "Proforma-2-000075.pdf"


def test_preview_sin_empresa_vinculada_cae_al_pais_del_documento(http, session_factory) -> None:
    """Sin empresa CRM: ningún contacto, el «Para» es el email de la cabecera
    y el idioma sale del país del documento (CPAPRE 250 = Francia)."""
    with _patched_factusol():
        r = http.get("/api/erp/factusol/quotes/75/email-preview?serie=2",
                     headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company_contacts"] == [] and body["company_id"] is None
    assert body["to"] == "marta@maison.example"
    assert (body["lang"], body["lang_source"]) == ("fr", "pais_documento")


def test_preview_404_si_no_existe(http) -> None:
    with _patched_factusol():
        r = http.get("/api/erp/factusol/quotes/999/email-preview?serie=2",
                     headers=auth_headers(http, "pedidos"))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Envío
# ---------------------------------------------------------------------------


def _payload(**over: Any) -> dict[str, Any]:
    base = {
        "confirm": True, "to": ["marta@maison.example"], "cc": [],
        "subject": "Devis 2-000075", "body_text": "Bonjour", "lang": "fr",
        "from_alias": "info@artisjet-printers.eu", "variant": None, "currency": "EUR",
    }
    base.update(over)
    return base


def test_send_adjunta_el_pdf_registra_el_evento_y_marca_el_listado(http, session_factory) -> None:
    with session_factory() as s:
        _seed_company(s)
        _seed_alias(s, "info@artisjet-printers.eu")
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        r = http.post("/api/erp/factusol/quotes/75/email?serie=2", json=_payload(),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    assert r.json()["sent"] is True
    assert r.json()["attachment_filename"] == "Presupuesto-2-000075.pdf"
    kwargs = mock_send.call_args.kwargs
    assert kwargs["from_alias"] == "info@artisjet-printers.eu"
    assert kwargs["to"] == ["marta@maison.example"]
    adjunto = kwargs["attachments"][0]
    assert adjunto["filename"] == "Presupuesto-2-000075.pdf"
    assert adjunto["content_type"] == "application/pdf"
    assert adjunto["data"][:4] == b"%PDF"
    assert kwargs["contact_id"] is not None                  # ligado al contacto Marta
    # Traza sobre la propia proforma (serie + nº), con usuario y destinatarios.
    with session_factory() as s:
        eventos = _events(s)
        assert len(eventos) == 1
        assert (eventos[0].target_type, eventos[0].target_id) == ("factusol_quote", "2-000075")
        assert eventos[0].actor_user_id is not None
        assert '"marta@maison.example"' in (eventos[0].metadata_json or "")
        assert '"lang": "fr"' in (eventos[0].metadata_json or "")
    # El listado la enseña como enviada, con los destinatarios.
    with _patched_factusol():
        lista = http.get("/api/erp/factusol/quotes?days_back=0",
                         headers=auth_headers(http, "pedidos")).json()
    item = next(q for q in lista["items"] if q["numero"] == "2-000075")
    assert item["emailed_at"] and item["emailed_to"] == ["marta@maison.example"]


def test_reenviar_deja_un_segundo_evento_y_actualiza_la_marca(http, session_factory) -> None:
    with session_factory() as s:
        _seed_company(s)
        _seed_alias(s, "info@artisjet-printers.eu")
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch:
        http.post("/api/erp/factusol/quotes/75/email?serie=2", json=_payload(),
                  headers=auth_headers(http, "pedidos"))
        r = http.post("/api/erp/factusol/quotes/75/email?serie=2",
                      json=_payload(to=["jean@maison.example"], cc=["marta@maison.example"]),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    with session_factory() as s:
        assert len(_events(s)) == 2
    with _patched_factusol():
        lista = http.get("/api/erp/factusol/quotes?days_back=0",
                         headers=auth_headers(http, "pedidos")).json()
    item = next(q for q in lista["items"] if q["numero"] == "2-000075")
    assert item["emailed_to"] == ["jean@maison.example", "marta@maison.example"]


def test_send_sin_destinatarios_no_envia_ni_registra(http, session_factory) -> None:
    with session_factory() as s:
        _seed_company(s)
        _seed_alias(s, "info@artisjet-printers.eu")
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        r = http.post("/api/erp/factusol/quotes/75/email?serie=2", json=_payload(to=["  "]),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "no_recipient"
    mock_send.assert_not_called()
    with session_factory() as s:
        assert _events(s) == []


def test_send_exige_confirmacion(http, session_factory) -> None:
    with session_factory() as s:
        _seed_alias(s, "info@artisjet-printers.eu")
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        r = http.post("/api/erp/factusol/quotes/75/email?serie=2", json=_payload(confirm=False),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "confirmation_required"
    mock_send.assert_not_called()


def test_send_remitente_no_utilizable_da_403_claro(http, session_factory) -> None:
    """Remitente que no es preferencia del usuario ni send-as comprobable (Gmail
    no conectado en tests): 403 con el motivo, sin enviar ni registrar."""
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        r = http.post("/api/erp/factusol/quotes/75/email?serie=2",
                      json=_payload(from_alias="otro@ejemplo.com"),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "alias_not_allowed"
    mock_send.assert_not_called()
    with session_factory() as s:
        assert _events(s) == []


def test_send_gmail_no_conectado_da_400_y_no_marca(http, session_factory) -> None:
    from app.integrations.gmail.service import GmailNotConnectedError

    with session_factory() as s:
        _seed_company(s)
        _seed_alias(s, "info@artisjet-printers.eu")
    with _patched_factusol(), patch(
        "app.integrations.gmail.service.send_email",
        side_effect=GmailNotConnectedError("Gmail no conectado"),
    ):
        r = http.post("/api/erp/factusol/quotes/75/email?serie=2", json=_payload(),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "gmail_unavailable"
    with session_factory() as s:
        assert _events(s) == []
    with _patched_factusol():
        lista = http.get("/api/erp/factusol/quotes?days_back=0",
                         headers=auth_headers(http, "pedidos")).json()
    assert next(q for q in lista["items"] if q["numero"] == "2-000075")["emailed_at"] is None


def test_send_no_escribe_en_factusol(http, session_factory) -> None:
    with session_factory() as s:
        _seed_company(s)
        _seed_alias(s, "info@artisjet-printers.eu")
    fake = FakeClient(_tables())
    send_patch, _ = _patch_send()
    with patch("app.integrations.factusol.client.FactusolClient.from_settings",
               return_value=fake), send_patch:
        r = http.post("/api/erp/factusol/quotes/75/email?serie=2", json=_payload(),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    # El doble solo tiene `load_table`: cualquier escritura habría fallado.
    assert all(call[0] in {"F_PRE", "F_LPS", "F_FOP", "F_FPA", "F_CLI"} for call in fake.calls)
