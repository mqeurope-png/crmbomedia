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


def test_preview_saluda_al_contacto_elegido_o_a_la_empresa(http, session_factory) -> None:
    """`contact_id`: el modal recarga el cuerpo cuando el operador cambia el
    primer destinatario. Con el id de otro contacto, {contacto} es ese; con
    «none» (direcciones libres) se saluda a la empresa; y la respuesta dice
    a quién saludó (`contacto_id`) para que el modal no recargue en bucle."""
    with session_factory() as s:
        company = _seed_company(s, language="es")
        jean = s.query(Contact).filter_by(company_id=company.id, first_name="Jean").one().id
        marta = s.query(Contact).filter_by(company_id=company.id, first_name="Marta").one().id
    base = "/api/erp/factusol/quotes/75/email-preview?serie=2"
    with _patched_factusol():
        premarcado = http.get(base, headers=auth_headers(http, "pedidos")).json()
        otro = http.get(f"{base}&contact_id={jean}", headers=auth_headers(http, "pedidos")).json()
        libre = http.get(f"{base}&contact_id=none", headers=auth_headers(http, "pedidos")).json()
        desconocido = http.get(f"{base}&contact_id=no-existe",
                               headers=auth_headers(http, "pedidos")).json()
    assert premarcado["contacto_id"] == marta
    assert "Marta Coll" in premarcado["body_text"]
    assert otro["contacto_id"] == jean
    assert "Jean Dupont" in otro["body_text"] and "Marta Coll" not in otro["body_text"]
    assert libre["contacto_id"] is None
    assert libre["markers"]["contacto"] == "La Maison de la Plaque"
    assert "Marta Coll" not in libre["body_text"]
    # Un id que no es de la lista no cuela: vuelve al premarcado.
    assert desconocido["contacto_id"] == marta


def test_preview_total_fuera_del_euro_lleva_simbolo_y_codigo(http) -> None:
    """`{total}` sale como lo imprime el PDF adjunto: en euros «1.234,56 €»;
    en otra divisa, el símbolo y el código ISO («kr» a secas no distingue
    coronas), y sin repetir el código cuando el símbolo ya lo es (CHF)."""
    base = "/api/erp/factusol/quotes/75/email-preview?serie=2&lang=en"
    with _patched_factusol():
        eur = http.get(base, headers=auth_headers(http, "pedidos")).json()
        usd = http.get(f"{base}&currency=USD", headers=auth_headers(http, "pedidos")).json()
        sek = http.get(f"{base}&currency=SEK", headers=auth_headers(http, "pedidos")).json()
        chf = http.get(f"{base}&currency=CHF", headers=auth_headers(http, "pedidos")).json()
    assert eur["markers"]["total"] == "1,234.56 €"
    assert usd["markers"]["total"] == "1,234.56 $ (USD)"
    assert sek["markers"]["total"] == "1 234,56 kr (SEK)"      # estilo nórdico
    assert chf["markers"]["total"] == "1,234.56 CHF"
    assert usd["currency"] == "USD" and "1,234.56 $ (USD)" in usd["body_text"]


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


def test_send_con_pedido_vinculado_deja_la_traza_tambien_en_el_pedido(
    http, session_factory,
) -> None:
    """Si la proforma ya se convirtió en pedido, la previsualización lo dice y
    el envío deja un segundo evento sobre el pedido (su timeline), además del
    de la proforma. La marca de la lista solo cuenta el de la proforma."""
    from app.erp.models import Order, OrderSource
    from app.erp.orders_from_factusol import external_id_for

    with session_factory() as s:
        company = _seed_company(s)
        _seed_alias(s, "info@artisjet-printers.eu")
        s.add(Order(id="o-75", order_number="PRO-000075", company_id=company.id,
                    external_source=OrderSource.FACTUSOL_PROFORMA,
                    external_id=external_id_for("presupuestos", 2, 75),
                    total_amount=1234.56, currency="EUR"))
        s.commit()
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch:
        pre = http.get("/api/erp/factusol/quotes/75/email-preview?serie=2",
                       headers=auth_headers(http, "pedidos")).json()
        r = http.post("/api/erp/factusol/quotes/75/email?serie=2", json=_payload(),
                      headers=auth_headers(http, "pedidos"))
    assert (pre["order_id"], pre["order_number"]) == ("o-75", "PRO-000075")
    assert r.status_code == 201, r.text
    with session_factory() as s:
        eventos = _events(s)
        assert [(e.target_type, e.target_id) for e in eventos] == [
            ("factusol_quote", "2-000075"), ("order", "o-75"),
        ]
        assert eventos[1].message == eventos[0].message
    with _patched_factusol():
        lista = http.get("/api/erp/factusol/quotes?days_back=0",
                         headers=auth_headers(http, "pedidos")).json()
    assert next(q for q in lista["items"] if q["numero"] == "2-000075")["emailed_at"]


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


def test_settings_quote_template_test_send(http, session_factory) -> None:
    """«Enviarme una prueba» de la plantilla del presupuesto: Gmail con los
    datos de muestra, sin PDF, «[Prueba]» delante del asunto, desde el
    remitente de la serie por defecto; se audita y no toca ninguna proforma."""
    admin = auth_headers(http, "admin")
    http.patch("/api/erp/settings", json={"factusol_series_default": "2"}, headers=admin)
    with session_factory() as s:
        _seed_alias(s, "info@artisjet-printers.eu", role="admin")
    send_patch, _ = _patch_send()
    with send_patch as mock_send:
        r = http.post("/api/erp/settings/quote-email/test-send",
                      json={"lang": "fr", "subject": "Devis {numero} — {empresa}"},
                      headers=admin)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sent"] is True and body["to"] == "admin@example.com"
    assert (body["from_alias"], body["from_alias_source"]) == ("info@artisjet-printers.eu", "serie")
    assert body["subject"] == "[Prueba] Devis 2-000075 — Laboratorios Duaner S.L."
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to"] == ["admin@example.com"]
    assert kwargs["subject"] == body["subject"]
    assert "Marta Coll" in kwargs["body_text"]
    assert kwargs.get("attachments") is None                 # sin PDF: prueba del texto
    assert kwargs["contact_id"] is None
    with session_factory() as s:
        rows = s.query(AuditLog).filter_by(action="erp.settings_template_test_sent").all()
        assert len(rows) == 1 and "email de presupuesto" in (rows[0].message or "")
        assert _events(s) == []                              # ninguna proforma marcada


def test_settings_quote_template_test_send_rechaza_remitente_no_utilizable(
    http, session_factory,
) -> None:
    """Remitente de la serie que no es un «enviar como» de Gmail → 403 con el
    motivo y sin enviar; solo ADMIN puede mandar la prueba."""
    _ = session_factory
    admin = auth_headers(http, "admin")
    http.patch("/api/erp/settings", json={"factusol_series_default": "2"}, headers=admin)
    send_patch, _ = _patch_send()
    with patch("app.integrations.gmail.service.list_aliases", return_value=[{
        "send_as_email": "admin@example.com", "display_name": "Admin",
        "is_primary": True, "is_default": True, "verification_status": "accepted",
    }]), send_patch as mock_send:
        r = http.post("/api/erp/settings/quote-email/test-send", json={"lang": "es"},
                      headers=admin)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "alias_not_allowed"
    assert r.json()["detail"]["from_alias"] == "info@artisjet-printers.eu"
    mock_send.assert_not_called()
    send_patch2, _ = _patch_send()
    with send_patch2 as mock_send2:
        r2 = http.post("/api/erp/settings/quote-email/test-send", json={"lang": "es"},
                       headers=auth_headers(http, "pedidos"))
    assert r2.status_code == 403
    mock_send2.assert_not_called()


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


# ---------------------------------------------------------------------------
# Remates · punto 3 — buscar cualquier contacto del CRM
# ---------------------------------------------------------------------------


def _seed_otra_empresa(session: Session) -> Contact:
    """Contacto de OTRA empresa (no la del cliente de la proforma) + uno
    inactivo y uno sin email, que el buscador no debe ofrecer."""
    otra = Company(name="Riera Contijoch SL", source="manual")
    session.add(otra)
    session.flush()
    eduard = Contact(first_name="Eduard", last_name="Riera", email="eduard@riera.example",
                     company_id=otra.id)
    session.add_all([
        eduard,
        Contact(first_name="Eduard", last_name="Baja", email="baja@riera.example",
                company_id=otra.id, is_active=False),
        Contact(first_name="Eduard", last_name="SinEmail", email=None, company_id=otra.id),
    ])
    session.commit()
    return eduard


def test_buscar_contactos_del_crm_desde_el_erp(http, session_factory) -> None:
    """El perfil de pedidos (solo-ERP, sin acceso a /api/contacts) busca por
    nombre, email o empresa; solo contactos activos con email."""
    with session_factory() as s:
        _seed_company(s)
        eduard_id = _seed_otra_empresa(s).id
    r = http.get("/api/erp/contacts/search?q=eduard", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [eduard_id]
    assert items[0] == {"id": eduard_id, "name": "Eduard Riera",
                        "email": "eduard@riera.example", "company_name": "Riera Contijoch SL"}
    # Por empresa y por trozo de email; varias palabras deben casar todas.
    for q in ("contijoch", "riera.example", "eduard riera"):
        got = http.get(f"/api/erp/contacts/search?q={q}",
                       headers=auth_headers(http, "pedidos")).json()["items"]
        assert eduard_id in [i["id"] for i in got], q
    assert http.get("/api/erp/contacts/search?q=eduard%20marta",
                    headers=auth_headers(http, "pedidos")).json()["items"] == []
    # Comodines de SQL literales, no patrones.
    assert http.get("/api/erp/contacts/search?q=%25%25",
                    headers=auth_headers(http, "pedidos")).json()["items"] == []
    assert http.get("/api/erp/contacts/search?q=e",
                    headers=auth_headers(http, "pedidos")).status_code == 422
    # Sin ninguna palabra útil (solo espacios, o letras sueltas) no se lista
    # el CRM entero.
    for q in ("%20%20%20", "a%20b"):
        r = http.get(f"/api/erp/contacts/search?q={q}", headers=auth_headers(http, "pedidos"))
        assert r.status_code == 422, q
        assert r.json()["detail"]["code"] == "query_too_short"


def test_preview_saluda_al_contacto_del_crm_de_otra_empresa(http, session_factory) -> None:
    """Cliente sin empresa vinculada (como la 1-004358): el operador añade a
    un contacto con el buscador y el saludo usa su nombre."""
    with session_factory() as s:
        eduard_id = _seed_otra_empresa(s).id
    with _patched_factusol():
        r = http.get(f"/api/erp/factusol/quotes/75/email-preview?serie=2&lang=es"
                     f"&contact_id={eduard_id}", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company_id"] is None and body["company_contacts"] == []
    assert body["contacto_id"] == eduard_id
    assert "Eduard Riera" in body["body_text"]


def test_send_a_un_contacto_del_crm_lo_enlaza_a_su_ficha(http, session_factory) -> None:
    with session_factory() as s:
        eduard_id = _seed_otra_empresa(s).id
        _seed_alias(s, "info@artisjet-printers.eu")
    send_patch, _ = _patch_send()
    with _patched_factusol(), send_patch as mock_send:
        r = http.post("/api/erp/factusol/quotes/75/email?serie=2",
                      json=_payload(to=["eduard@riera.example"], lang="es"),
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    assert mock_send.call_args.kwargs["contact_id"] == eduard_id


def test_preview_contacto_del_crm_sin_nombre_saluda_a_la_empresa(http, session_factory) -> None:
    """Un contacto añadido con el buscador que no tiene nombre: se saluda a la
    empresa (no al contacto principal, que quizá ni recibe el correo) y la
    respuesta dice que ya se saludó a ese contacto (sin recargas en bucle)."""
    with session_factory() as s:
        _seed_company(s, language="es")
        sin_nombre = Contact(first_name="", last_name=None, email="info@otra.example")
        s.add(sin_nombre)
        s.commit()
        sin_nombre_id = sin_nombre.id
    with _patched_factusol():
        body = http.get(f"/api/erp/factusol/quotes/75/email-preview?serie=2"
                        f"&contact_id={sin_nombre_id}",
                        headers=auth_headers(http, "pedidos")).json()
    assert body["contacto_id"] == sin_nombre_id
    assert body["markers"]["contacto"] == "La Maison de la Plaque"
    assert "Marta Coll" not in body["body_text"]
