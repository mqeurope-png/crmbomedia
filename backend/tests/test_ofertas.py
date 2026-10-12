"""Ofertas: presupuestar a un lead sin tocar FACTUSOL.

Los trece casos de la petición y el escáner del paquete: la plantilla
conserva las líneas con sus artículos; crear una oferta para un contacto sin
empresa / sin FACTUSOL no escribe nada; editar una enviada pone la fecha a hoy
y recalcula la validez (la fecha a mano manda); el PDF sale en el idioma del
contacto con el nombre de #531-#533; con / sin IVA los textos y VIES en los
cinco idiomas; enviar marca enviada, adjunta el PDF y registra; convertir con
empresa vinculada crea el presupuesto en la serie correcta con sus líneas;
convertir sin empresa pasa por el flujo de #502 (no falla en silencio); una
línea sin artículo para la conversión; convertir deja la oferta enlazada;
enviar mueve al contacto a «Propuesta enviada»; el comercial puede crear,
editar y enviar; y ningún camino escribe en FACTUSOL salvo el botón de
convertir. Nada toca Gmail ni FACTUSOL de verdad.
"""
from __future__ import annotations

import ast
import io
import re
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp import ofertas as svc
from app.erp.models import Oferta, OfertaPlantilla
from app.main import app
from app.models.crm import (
    AuditLog,
    Company,
    Contact,
    ContactPipelineStage,
    ContactStageHistory,
    Pipeline,
    PipelineStage,
    User,
    UserEmailAliasPref,
)
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_quotes import _FakeFactusol

RAIZ = Path(__file__).resolve().parents[1] / "app"


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


class _FactusolProhibido:
    """Un cliente que no admite NI una llamada: la prueba de que crear, editar
    y enviar una oferta no tocan FACTUSOL ni para leer."""

    default_ejercicio = "2026"

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"FACTUSOL no debe tocarse ({name})")


def _patch_factusol(cliente: Any):
    return patch("app.integrations.factusol.client.FactusolClient.from_settings",
                 return_value=cliente)


def _patch_send():
    msg = MagicMock()
    msg.id = "msg-1"
    msg.thread_id = "thread-1"
    return patch("app.integrations.gmail.service.send_email", return_value=msg), msg


def _seed_alias(session: Session, alias: str, role: str = "comercial") -> None:
    user = session.query(User).filter_by(email=f"{role}@example.com").one()
    session.add(UserEmailAliasPref(user_id=user.id, alias_email=alias, is_allowed=True))
    session.commit()


def _contacto(session: Session, *, language: str | None = "de", company: Company | None = None,
              email: str = "lena@example.de") -> Contact:
    c = Contact(first_name="Lena", last_name="Müller", email=email, language=language,
                address_country="DE", company_id=company.id if company else None)
    session.add(c)
    session.commit()
    return c


def _empresa(session: Session, *, linked: bool) -> Company:
    e = Company(name="Manufaktur für Gestaltung GmbH", source="manual", country="DE",
                tax_id="DE123456789", vat="DE123456789", city="München",
                address_line="Hauptstr. 1", postal_code="80331",
                factusol_company_id="2458" if linked else None)
    session.add(e)
    session.commit()
    return e


LINEAS = [
    {"codart": "UV1", "sku": "UV-A3", "descripcion": "Impresora UV A3", "cantidad": 1,
     "precio": 1000, "iva_pct": 21},
    {"codart": None, "descripcion": "Formación de puesta en marcha incluida", "cantidad": 0,
     "precio": 0},
    {"codart": "T1", "sku": "TINTA-C", "descripcion": "Tinta cian", "cantidad": 2,
     "precio": 50, "descuento_pct": 10, "iva_pct": 21},
]


def _plantilla(session: Session) -> OfertaPlantilla:
    p = svc.crear_plantilla(session, nombre="UV básica", lineas=svc.normalizar_lineas(LINEAS),
                            usuario=None)
    session.commit()
    return p


def _oferta(session: Session, contacto: Contact, *, serie: int = 2, con_iva: bool = True,
            hoy: date | None = None, lineas: list[dict[str, Any]] | None = None) -> Oferta:
    o = svc.crear_oferta(
        session, contacto=contacto, serie=serie, usuario=None,
        lineas=svc.normalizar_lineas(lineas if lineas is not None else LINEAS),
        con_iva=con_iva, hoy=hoy,
    )
    session.commit()
    return o


def _texto_pdf(contenido: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)


def _eventos(session: Session, action: str) -> list[AuditLog]:
    return list(session.scalars(select(AuditLog).where(AuditLog.action == action)))


# ---------------------------------------------------------------------------
# 1. La plantilla conserva las líneas con sus artículos
# ---------------------------------------------------------------------------


def test_plantilla_desde_presupuesto_conserva_lineas_con_articulos(http, session_factory) -> None:
    fake = _FakeFactusol(
        quotes=[{"CODPRE": 40, "TIPPRE": "2", "REFPRE": "UV", "FECPRE": "2026-09-01T00:00:00",
                 "CLIPRE": "2458", "CNOPRE": "Acme", "TOTPRE": 1210.0, "NET1PRE": 1000.0,
                 "PIVA1PRE": 21, "IIVA1PRE": 210.0}],
        lines=[{"CODLPS": 40, "TIPLPS": "2", "POSLPS": 1, "ARTLPS": "UV1",
                "DESLPS": "Impresora UV A3", "CANLPS": 1, "PRELPS": 1000.0, "DT1LPS": 0,
                "TOTLPS": 1000.0, "IVALPS": 21},
               {"CODLPS": 40, "TIPLPS": "2", "POSLPS": 2, "ARTLPS": "T1",
                "DESLPS": "Tinta cian", "CANLPS": 2, "PRELPS": 50.0, "DT1LPS": 10,
                "TOTLPS": 90.0, "IVALPS": 21}],
        articles=[{"CODART": "UV1", "EQUART": "UV-A3", "DESART": "Impresora UV A3"},
                  {"CODART": "T1", "EQUART": "TINTA-C", "DESART": "Tinta cian"}],
    )
    with _patch_factusol(fake):
        r = http.post("/api/erp/ofertas/plantillas", headers=auth_headers(http, "comercial"),
                      json={"nombre": "UV básica", "descripcion": "Equipo + tinta",
                            "serie": 2, "codpre": 40})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["origen_numero"] == "2-000040" and body["activa"] is True
    assert [(ln["codart"], ln["sku"], ln["cantidad"], ln["precio"], ln["descuento_pct"])
            for ln in body["lineas"]] == [("UV1", "UV-A3", 1.0, 1000.0, 0.0),
                                          ("T1", "TINTA-C", 2.0, 50.0, 10.0)]
    assert body["lineas"][0]["descripcion"] == "Impresora UV A3"
    # Solo lectura: ni una escritura en FACTUSOL.
    assert fake.writes == [] and fake.updates == []
    with session_factory() as s:
        p = s.get(OfertaPlantilla, body["id"])
        assert p is not None and [ln.codart for ln in p.lineas] == ["UV1", "T1"]
    # Desactivar / editar la plantilla.
    r2 = http.patch(f"/api/erp/ofertas/plantillas/{body['id']}",
                    headers=auth_headers(http, "comercial"), json={"activa": False})
    assert r2.status_code == 200 and r2.json()["activa"] is False
    r3 = http.get("/api/erp/ofertas/plantillas?activas=true", headers=auth_headers(http))
    assert r3.json()["items"] == []


# ---------------------------------------------------------------------------
# 2. Crear una oferta para un contacto sin empresa / sin FACTUSOL no escribe nada
# ---------------------------------------------------------------------------


def test_crear_oferta_para_contacto_sin_empresa_no_toca_factusol(http, session_factory) -> None:
    with session_factory() as s:
        contacto = _contacto(s)
        plantilla = _plantilla(s)
        contact_id, plantilla_id = contacto.id, plantilla.id
    with _patch_factusol(_FactusolProhibido()):
        r = http.post("/api/erp/ofertas", headers=auth_headers(http, "comercial"),
                      json={"contact_id": contact_id, "serie": 2, "plantilla_id": plantilla_id})
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["numero"].startswith("OF-") and o["numero"].endswith("-0001")
    assert o["estado"] == "borrador" and o["empresa"] is None and o["factusol"] is None
    assert o["idioma"] == "de"               # el del contacto
    assert o["emisora"] == "MQ Europe" and o["serie"] == 2
    assert o["validez_dias"] == 30
    assert (date.fromisoformat(o["valida_hasta"])
            == date.fromisoformat(o["fecha"]) + timedelta(days=30))
    assert [ln["codart"] for ln in o["lineas"]] == ["UV1", None, "T1"]
    assert o["lineas"][1]["text_only"] is True
    assert o["base"] == 1090.0 and o["iva"] == 228.9 and o["total"] == 1318.9
    # Una segunda oferta sigue el contador propio.
    r2 = http.post("/api/erp/ofertas", headers=auth_headers(http, "comercial"),
                   json={"contact_id": contact_id, "serie": 1, "plantilla_id": plantilla_id,
                         "con_iva": False, "idioma": "en"})
    assert r2.status_code == 201 and r2.json()["numero"].endswith("-0002")
    assert r2.json()["iva"] == 0.0 and r2.json()["total"] == 1090.0
    # Listado por contacto y en la ficha.
    r3 = http.get(f"/api/erp/ofertas?contact_id={contact_id}", headers=auth_headers(http))
    assert [x["numero"][-4:] for x in r3.json()["items"]] == ["0002", "0001"]
    with session_factory() as s:
        assert _eventos(s, "erp.oferta_created")


# ---------------------------------------------------------------------------
# 3. Editar una enviada: fecha a hoy y validez recalculada; la fecha a mano manda
# ---------------------------------------------------------------------------


def test_editar_enviada_pone_fecha_hoy_y_recalcula_validez(session_factory) -> None:
    with session_factory() as s:
        contacto = _contacto(s)
        o = _oferta(s, contacto, hoy=date(2026, 10, 1))
        o.estado = "enviada"
        o.enviada_en = datetime(2026, 10, 1, tzinfo=UTC)
        s.commit()
        assert (o.fecha, o.valida_hasta) == (date(2026, 10, 1), date(2026, 10, 31))
        svc.actualizar_oferta(
            s, o, usuario=None, hoy=date(2026, 10, 20),
            lineas=svc.normalizar_lineas([{"codart": "UV1", "descripcion": "Impresora UV A3",
                                           "cantidad": 2, "precio": 900}]),
        )
        s.commit()
        assert (o.fecha, o.valida_hasta) == (date(2026, 10, 20), date(2026, 11, 19))
        assert o.estado == "enviada" and o.version == 2 and float(o.total) == 2178.0
        # La fecha a mano manda (y la validez cuenta desde ella).
        svc.actualizar_oferta(s, o, usuario=None, hoy=date(2026, 10, 21),
                              fecha=date(2026, 11, 2), validez_dias=10)
        s.commit()
        assert (o.fecha, o.valida_hasta) == (date(2026, 11, 2), date(2026, 11, 12))
        assert o.version == 3
        # Una caducada que se edita vuelve a borrador (hay que reenviarla).
        o.estado, o.valida_hasta = "enviada", date(2026, 1, 1)
        s.commit()
        assert svc.estado_efectivo(o, date(2026, 10, 21)) == "caducada"
        svc.actualizar_oferta(s, o, usuario=None, hoy=date(2026, 10, 21))
        assert o.estado == "borrador" and o.valida_hasta == date(2026, 10, 31)
        # Una convertida no se edita.
        o.estado = "convertida"
        with pytest.raises(svc.OfertaError) as exc:
            svc.actualizar_oferta(s, o, usuario=None)
        assert exc.value.code == "oferta_convertida"


def test_patch_por_api_pone_la_fecha_de_hoy(http, session_factory) -> None:
    with session_factory() as s:
        o = _oferta(s, _contacto(s), hoy=date(2026, 1, 5))
        oferta_id = o.id
    r = http.patch(f"/api/erp/ofertas/{oferta_id}", headers=auth_headers(http, "comercial"),
                   json={"referencia": "Su ref. 77"})
    assert r.status_code == 200, r.text
    hoy = datetime.now(UTC).date()
    assert r.json()["fecha"] == hoy.isoformat()
    assert r.json()["valida_hasta"] == (hoy + timedelta(days=30)).isoformat()
    assert r.json()["referencia"] == "Su ref. 77" and r.json()["version"] == 2


# ---------------------------------------------------------------------------
# 4. PDF en el idioma del contacto, con el nombre de #531-#533
# ---------------------------------------------------------------------------


def test_pdf_en_idioma_del_contacto_y_nombre_de_fichero(http, session_factory) -> None:
    with session_factory() as s:
        o = _oferta(s, _contacto(s, language="de"), hoy=date(2026, 10, 12))
        oferta_id, numero = o.id, o.numero
    with _patch_factusol(_FactusolProhibido()):
        r = http.get(f"/api/erp/ofertas/{oferta_id}/pdf", headers=auth_headers(http))
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf")
    assert (r.headers["content-disposition"]
            == f'inline; filename="Angebot LENA MULLER {numero}.pdf"')
    texto = _texto_pdf(r.content)
    assert "ANGEBOT" in texto and numero in texto
    assert "Angebot gültig bis 11-11-2026." in texto
    assert "Impresora UV A3" in texto
    # El selector manda sobre el idioma de la oferta.
    r2 = http.get(f"/api/erp/ofertas/{oferta_id}/pdf?lang=fr", headers=auth_headers(http))
    assert r2.headers["content-disposition"] == f'inline; filename="Offre LENA MULLER {numero}.pdf"'


# ---------------------------------------------------------------------------
# 5. Con / sin IVA: textos correctos y VIES en los cinco idiomas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang,titulo,con,sin,vies", [
    ("es", "OFERTA", "Importes con IVA (21 %).", "Importes sin IVA.", "VIES"),
    ("en", "OFFER", "Amounts include VAT (21 %).", "Amounts exclude VAT.", "VIES"),
    ("de", "ANGEBOT", "Beträge inkl. MwSt. (21 %).", "Beträge ohne MwSt.", "VIES"),
    ("fr", "OFFRE", "Montants TVA incluse (21 %).", "Montants hors TVA.", "VIES"),
    ("nl", "OFFERTE", "Bedragen inclusief btw (21 %).", "Bedragen exclusief btw.", "VIES"),
])
def test_pdf_con_y_sin_iva_textos_y_vies(session_factory, lang, titulo, con, sin, vies) -> None:
    from app.erp.factusol_pdf import labels_for

    lab = labels_for(lang)
    assert vies in lab["oferta_aviso"] and "{" not in lab["oferta_aviso"]
    with session_factory() as s:
        contacto = _contacto(s)
        con_iva = _oferta(s, contacto, con_iva=True, hoy=date(2026, 10, 12))
        sin_iva = _oferta(s, contacto, con_iva=False, hoy=date(2026, 10, 12))
        texto_con = _texto_pdf(svc.pdf(s, con_iva, lang=lang)).replace("\n", " ")
        texto_sin = _texto_pdf(svc.pdf(s, sin_iva, lang=lang)).replace("\n", " ")
    for texto in (texto_con, texto_sin):
        assert titulo in texto
        # El aviso entero (partido en líneas por el pie): sus primeras palabras y VIES.
        assert lab["oferta_aviso"][:30] in texto and vies in texto
    assert con in texto_con and sin not in texto_con
    assert sin in texto_sin and con not in texto_sin
    # Con IVA: desglose base / IVA / total; sin IVA: base única sin «21 %».
    assert "1.318,90" in texto_con or "1,318.90" in texto_con
    assert "1.090,00" in texto_sin or "1,090.00" in texto_sin
    # Ningún texto intracomunitario de la EMISORA (serie 2, MQ Europe: «No
    # belgian VAT due…»): el régimen se fija al convertir. El aviso de la
    # oferta sí habla de VIES y de operaciones intracomunitarias, a propósito.
    assert "No belgian VAT due" not in texto_sin and "No belgian VAT due" not in texto_con


# ---------------------------------------------------------------------------
# 6. Enviar: enviada, PDF adjunto, registro
# ---------------------------------------------------------------------------


def test_enviar_marca_enviada_adjunta_pdf_y_registra(http, session_factory) -> None:
    with session_factory() as s:
        o = _oferta(s, _contacto(s), hoy=date(2026, 10, 12))
        _seed_alias(s, "ventas@bomedia.net")
        oferta_id, numero = o.id, o.numero
    headers = auth_headers(http, "comercial")
    with _patch_factusol(_FactusolProhibido()):
        pre = http.get(f"/api/erp/ofertas/{oferta_id}/email-preview", headers=headers)
    assert pre.status_code == 200, pre.text
    p = pre.json()
    assert p["to"] == "lena@example.de" and p["lang"] == "de"
    assert p["subject"] == f"Angebot {numero}" and numero in p["body_text"]
    assert "Angebot gültig bis 11-11-2026." in p["body_text"]
    assert p["attachment_filename"] == f"Angebot LENA MULLER {numero}.pdf"
    assert p["company_contacts"][0]["is_primary"] is True
    enviar, _ = _patch_send()
    with (_patch_factusol(_FactusolProhibido()), enviar as send,
          patch("app.workflows.dispatcher.dispatch_event") as hecho):
        # Sin confirmación no se envía.
        r0 = http.post(f"/api/erp/ofertas/{oferta_id}/email", headers=headers, json={
            "to": [p["to"]], "subject": p["subject"], "body_text": p["body_text"],
            "lang": "de", "from_alias": "ventas@bomedia.net"})
        assert r0.status_code == 400 and send.call_count == 0
        r = http.post(f"/api/erp/ofertas/{oferta_id}/email", headers=headers, json={
            "confirm": True, "to": [p["to"]], "cc": ["jefe@example.de"],
            "subject": p["subject"], "body_text": p["body_text"], "lang": "de",
            "from_alias": "ventas@bomedia.net"})
    assert r.status_code == 201, r.text
    assert r.json()["estado"] == "enviada" and r.json()["sent"] is True
    kwargs = send.call_args.kwargs
    assert kwargs["to"] == ["lena@example.de"] and kwargs["cc"] == ["jefe@example.de"]
    assert kwargs["from_alias"] == "ventas@bomedia.net"
    adj = kwargs["attachments"][0]
    assert adj["filename"] == f"Angebot LENA MULLER {numero}.pdf"
    assert adj["content_type"] == "application/pdf" and adj["data"].startswith(b"%PDF")
    assert hecho.call_args.args[1] == "oferta.enviada"
    with session_factory() as s:
        o = s.get(Oferta, oferta_id)
        assert o.estado == "enviada" and o.enviada_en is not None
        assert len(o.envios) == 1 and o.envios[0].version == 1
        assert o.envios[0].message_id == "msg-1" and o.envios[0].adjunto == adj["filename"]
        ev = _eventos(s, "erp.oferta_emailed")
        assert {e.target_type for e in ev} == {"oferta", "contact"}
    d = http.get(f"/api/erp/ofertas/{oferta_id}", headers=headers).json()
    assert d["num_envios"] == 1 and d["pendiente_de_envio"] is False
    assert d["ultimo_envio"]["para"] == ["lena@example.de"]


# ---------------------------------------------------------------------------
# 7 + 10. Convertir con empresa vinculada: presupuesto en la serie correcta, enlazado
# ---------------------------------------------------------------------------


def test_convertir_crea_presupuesto_en_serie_con_lineas_y_enlaza(http, session_factory) -> None:
    with session_factory() as s:
        empresa = _empresa(s, linked=True)
        contacto = _contacto(s, company=empresa)
        o = _oferta(s, contacto, serie=2, hoy=date(2026, 10, 12))
        o.referencia = "Su ref. 77"
        s.commit()
        oferta_id, numero = o.id, o.numero
    # El endpoint prechequea y encola (la cola se simula).
    with patch("app.integrations.factusol.jobs.enqueue_convert_oferta",
               return_value="job-1") as encolar:
        r = http.post(f"/api/erp/ofertas/{oferta_id}/convertir",
                      headers=auth_headers(http, "comercial"))
    assert r.status_code == 202 and r.json()["job_id"] == "job-1", r.text
    assert encolar.call_args.args == (oferta_id,)
    # El job escribe en FACTUSOL: cabecera F_PRE en la serie 2 + líneas F_LPS.
    fake = _FakeFactusol(
        quotes=[{"CODPRE": 574, "TIPPRE": "1", "CLIPRE": "1"}],
        articles=[{"CODART": "UV1", "EQUART": "UV-A3"}, {"CODART": "T1", "EQUART": "TINTA-C"}],
    )
    with session_factory() as s:
        o = s.get(Oferta, oferta_id)
        result = svc.convertir(s, fake, o, ejercicio="2026", actor=None)
        assert result["codpre"] == 575 and result["serie"] == 2
        assert result["factusol_numero"] == "2-000575"
        assert o.estado == "convertida" and o.factusol_codpre == 575 and o.factusol_serie == 2
        assert o.convertida_en is not None
        assert _eventos(s, "erp.oferta_converted")
    cab = fake.writes_to("F_PRE")
    assert len(cab) == 1
    assert cab[0]["TIPPRE"] == "2" and cab[0]["CODPRE"] == "575"
    assert cab[0]["CLIPRE"] == "2458" and cab[0]["CNOPRE"] == "Manufaktur für Gestaltung GmbH"
    assert cab[0]["REFPRE"] == "Su ref. 77" and cab[0]["CEMPRE"] == "lena@example.de"
    assert cab[0]["NET1PRE"] == 1090.0
    # Alemania → MQ Europe (BE): intracomunitario, IVA 0 (régimen definitivo al convertir).
    assert cab[0]["IIVA1PRE"] == 0.0 and cab[0]["TOTPRE"] == 1090.0
    lineas = fake.writes_to("F_LPS")
    assert [ln["ARTLPS"] for ln in lineas] == ["UV1", "", "T1"]
    assert all(ln["CODLPS"] == "575" and ln["TIPLPS"] == "2" for ln in lineas)
    assert lineas[2]["CANLPS"] == 2.0 and lineas[2]["PRELPS"] == 50.0
    # Consultable: la oferta queda enlazada y ya no se convierte dos veces.
    d = http.get(f"/api/erp/ofertas/{oferta_id}", headers=auth_headers(http)).json()
    assert d["estado"] == "convertida" and d["factusol"]["numero"] == "2-000575"
    r2 = http.post(f"/api/erp/ofertas/{oferta_id}/convertir",
                   headers=auth_headers(http, "comercial"))
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "oferta_convertida"
    assert numero == d["numero"]


# ---------------------------------------------------------------------------
# 8. Convertir sin empresa vinculada → flujo de #502, no falla en silencio
# ---------------------------------------------------------------------------


def test_convertir_sin_empresa_vinculada_pide_el_flujo_de_vincular(http, session_factory) -> None:
    with session_factory() as s:
        sin_empresa = _contacto(s, email="solo@example.de")
        empresa = _empresa(s, linked=False)
        con_empresa_sin_factusol = _contacto(s, email="lena@example.de", company=empresa)
        o1 = _oferta(s, sin_empresa, hoy=date(2026, 10, 12))
        o2 = _oferta(s, con_empresa_sin_factusol, hoy=date(2026, 10, 12))
        ids = (o1.id, o2.id, empresa.id, sin_empresa.id)
    headers = auth_headers(http, "comercial")
    with patch("app.integrations.factusol.jobs.enqueue_convert_oferta") as encolar:
        r1 = http.post(f"/api/erp/ofertas/{ids[0]}/convertir", headers=headers)
        r2 = http.post(f"/api/erp/ofertas/{ids[1]}/convertir", headers=headers)
    assert encolar.call_count == 0
    assert r1.status_code == 409 and r1.json()["detail"]["code"] == "company_not_linked"
    assert r1.json()["detail"]["company_id"] is None
    assert r1.json()["detail"]["contact_id"] == ids[3]
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "company_not_linked"
    assert r2.json()["detail"]["company_id"] == ids[2]
    assert "Manufaktur" in r2.json()["detail"]["detail"]
    # Y el servicio tampoco escribe nada si se le llama directamente.
    fake = _FakeFactusol()
    with session_factory() as s:
        with pytest.raises(svc.OfertaError) as exc:
            svc.convertir(s, fake, s.get(Oferta, ids[1]), ejercicio="2026", actor=None)
        assert exc.value.code == "company_not_linked"
    assert fake.writes == []


# ---------------------------------------------------------------------------
# 9. Línea sin artículo: para la conversión
# ---------------------------------------------------------------------------


def test_linea_sin_articulo_para_la_conversion(http, session_factory) -> None:
    with session_factory() as s:
        empresa = _empresa(s, linked=True)
        o = _oferta(s, _contacto(s, company=empresa), lineas=[
            {"codart": "UV1", "descripcion": "Impresora", "cantidad": 1, "precio": 1000},
            {"codart": None, "descripcion": "Mesa a medida", "cantidad": 1, "precio": 300},
            {"codart": None, "descripcion": "Nota sin importe", "cantidad": 0, "precio": 0},
        ], hoy=date(2026, 10, 12))
        oferta_id = o.id
    with patch("app.integrations.factusol.jobs.enqueue_convert_oferta") as encolar:
        r = http.post(f"/api/erp/ofertas/{oferta_id}/convertir",
                      headers=auth_headers(http, "comercial"))
    assert encolar.call_count == 0
    assert r.status_code == 422 and r.json()["detail"]["code"] == "lineas_sin_articulo"
    assert r.json()["detail"]["posiciones"] == [2]      # la nota sin importe sí vale
    fake = _FakeFactusol()
    with session_factory() as s:
        with pytest.raises(svc.OfertaError):
            svc.convertir(s, fake, s.get(Oferta, oferta_id), ejercicio="2026", actor=None)
        assert s.get(Oferta, oferta_id).estado == "borrador"
    assert fake.writes == []


# ---------------------------------------------------------------------------
# 11. Enviar mueve al contacto a «Propuesta enviada»
# ---------------------------------------------------------------------------


def test_enviar_mueve_al_contacto_a_propuesta_enviada(http, session_factory) -> None:
    with session_factory() as s:
        contacto = _contacto(s)
        pipeline = Pipeline(name="Ventas B2B")
        s.add(pipeline)
        s.flush()
        etapas = [PipelineStage(pipeline_id=pipeline.id, name=n, position=i)
                  for i, n in enumerate(["Nuevo lead", "Contactado", "Propuesta enviada",
                                         "Cerrado perdido"])]
        s.add_all(etapas)
        s.flush()
        s.add(ContactPipelineStage(contact_id=contacto.id, pipeline_id=pipeline.id,
                                   stage_id=etapas[0].id))
        o = _oferta(s, contacto, hoy=date(2026, 10, 12))
        _seed_alias(s, "ventas@bomedia.net")
        s.commit()
        oferta_id, contact_id, propuesta_id = o.id, contacto.id, etapas[2].id
    enviar, _ = _patch_send()
    with (_patch_factusol(_FactusolProhibido()), enviar,
          patch("app.workflows.dispatcher.dispatch_event") as hecho):
        r = http.post(f"/api/erp/ofertas/{oferta_id}/email",
                      headers=auth_headers(http, "comercial"), json={
                          "confirm": True, "to": ["lena@example.de"], "subject": "Oferta",
                          "body_text": "Adjunto la oferta.", "lang": "de",
                          "from_alias": "ventas@bomedia.net"})
    assert r.status_code == 201, r.text
    assert r.json()["etapas_movidas"] == ["Ventas B2B"]
    with session_factory() as s:
        asignacion = s.scalar(select(ContactPipelineStage).where(
            ContactPipelineStage.contact_id == contact_id))
        assert asignacion.stage_id == propuesta_id
        historia = s.scalar(select(ContactStageHistory).where(
            ContactStageHistory.contact_pipeline_stage_id == asignacion.id))
        assert historia is not None and historia.to_stage_id == propuesta_id
        assert "enviada" in (historia.note or "")
    # El hecho que consumirá la Fase 3, con su payload.
    args, _ = hecho.call_args
    assert args[1] == "oferta.enviada" and args[2] == contact_id
    assert args[3]["etapas_movidas"] == ["Ventas B2B"]


def test_caducidad_marca_caducada_y_emite_el_hecho(session_factory) -> None:
    with session_factory() as s:
        contacto = _contacto(s)
        vencida = _oferta(s, contacto, hoy=date(2026, 8, 1))
        vencida.estado, vencida.enviada_en = "enviada", datetime(2026, 8, 1, tzinfo=UTC)
        vigente = _oferta(s, contacto, hoy=date(2026, 10, 1))
        vigente.estado = "enviada"
        borrador = _oferta(s, contacto, hoy=date(2026, 8, 1))   # vencida pero nunca enviada
        s.commit()
        ids = (vencida.id, vigente.id, borrador.id)
        with patch("app.workflows.dispatcher.dispatch_event") as hecho:
            caducadas = svc.caducar_pendientes(s, hoy=date(2026, 10, 12))
        assert [o.id for o in caducadas] == [ids[0]]
        assert s.get(Oferta, ids[0]).estado == "caducada"
        assert s.get(Oferta, ids[0]).caducada_en is not None
        assert s.get(Oferta, ids[1]).estado == "enviada"
        assert s.get(Oferta, ids[2]).estado == "borrador"
        assert hecho.call_args.args[1] == "oferta.caducada"
        assert _eventos(s, "erp.oferta_expired")
        # La segunda pasada no repite.
        with patch("app.workflows.dispatcher.dispatch_event") as hecho2:
            assert svc.caducar_pendientes(s, hoy=date(2026, 10, 12)) == []
        assert hecho2.call_count == 0
    # Los hechos existen en el catálogo de disparadores de los workflows, y el
    # estimador cuenta por serie lo mismo que el matcher dispara.
    from app.workflows.trigger_definitions import (
        TRIGGER_DEFS,
        config_matches,
        estimate_runs_30d,
    )

    assert TRIGGER_DEFS["oferta.enviada"].available and TRIGGER_DEFS["oferta.caducada"].available
    assert config_matches("oferta.enviada", {"serie": "2"}, {"serie": 2})
    assert not config_matches("oferta.enviada", {"serie": "1"}, {"serie": 2})
    with session_factory() as s:
        hace_un_mes = datetime.now(UTC) - timedelta(days=30)
        assert estimate_runs_30d(s, "oferta.caducada", {}, hace_un_mes) == 1
        assert estimate_runs_30d(s, "oferta.caducada", {"serie": "2"}, hace_un_mes) == 1
        assert estimate_runs_30d(s, "oferta.caducada", {"serie": "1"}, hace_un_mes) == 0


def test_listado_filtra_por_estado_efectivo_antes_del_limite(session_factory) -> None:
    with session_factory() as s:
        contacto = _contacto(s)
        vieja = _oferta(s, contacto, hoy=date(2026, 1, 10))        # enviada y vencida → caducada
        vieja.estado = "enviada"
        for i in range(3):                                            # tres recientes, borrador
            _oferta(s, contacto, hoy=date(2026, 10, 1 + i))
        s.commit()
        hoy = date(2026, 10, 12)
        caducadas = svc.listar_ofertas(s, estado="caducada", limit=1, hoy=hoy)
        assert [o.id for o in caducadas] == [vieja.id]
        assert svc.listar_ofertas(s, estado="enviada", limit=10, hoy=hoy) == []
        assert len(svc.listar_ofertas(s, estado="borrador", limit=2, hoy=hoy)) == 2
        assert len(svc.listar_ofertas(s, limit=10, hoy=hoy)) == 4


# ---------------------------------------------------------------------------
# 12. El comercial crea, edita y envía sin permisos especiales
# ---------------------------------------------------------------------------


def test_comercial_crea_edita_y_envia_sin_permiso_nuevo(http, session_factory) -> None:
    with session_factory() as s:
        contacto = _contacto(s)
        plantilla = _plantilla(s)
        _seed_alias(s, "ventas@bomedia.net")
        contact_id, plantilla_id = contacto.id, plantilla.id
    headers = auth_headers(http, "comercial")
    r = http.post("/api/erp/ofertas", headers=headers,
                  json={"contact_id": contact_id, "serie": 5, "plantilla_id": plantilla_id,
                        "lineas": [{"codart": "UV1", "descripcion": "Impresora UV A3",
                                    "cantidad": 1, "precio": 950}]})
    assert r.status_code == 201, r.text
    oferta_id = r.json()["id"]
    assert r.json()["total"] == 1149.5 and r.json()["emisora"] == "Streamtec"
    r = http.patch(f"/api/erp/ofertas/{oferta_id}", headers=headers,
                   json={"con_iva": False, "validez_dias": 15})
    assert r.status_code == 200 and r.json()["total"] == 950.0 and r.json()["validez_dias"] == 15
    enviar, _ = _patch_send()
    with (_patch_factusol(_FactusolProhibido()), enviar,
          patch("app.workflows.dispatcher.dispatch_event")):
        pre = http.get(f"/api/erp/ofertas/{oferta_id}/email-preview?lang=es", headers=headers)
        assert pre.status_code == 200 and pre.json()["subject"].startswith("Oferta ")
        r = http.post(f"/api/erp/ofertas/{oferta_id}/email", headers=headers, json={
            "confirm": True, "to": ["lena@example.de"], "subject": pre.json()["subject"],
            "body_text": pre.json()["body_text"], "lang": "es",
            "from_alias": "ventas@bomedia.net"})
    assert r.status_code == 201, r.text
    # Un usuario sin capacidad de proformas (SAT) no crea.
    r = http.post("/api/erp/ofertas", headers=auth_headers(http, "sat"),
                  json={"contact_id": contact_id, "serie": 1, "plantilla_id": plantilla_id})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 13. Ningún camino escribe en FACTUSOL salvo el botón de convertir
# ---------------------------------------------------------------------------

#: Nombres que escriben en FACTUSOL (cliente y capa de proformas).
_ESCRITURAS = {
    "write_record", "update_record", "delete_records", "create_quote", "update_quote",
    "duplicate_quote", "convert_quote_to_order", "enqueue_create_quote", "enqueue_update_quote",
    "enqueue_duplicate_quote", "enqueue_convert_oferta", "enqueue_create_document",
    "emit_invoice", "convert_document", "create_albaran_for_order",
}


def _funciones_que_escriben(fichero: Path) -> set[str]:
    arbol = ast.parse(fichero.read_text(encoding="utf-8"))
    salida: set[str] = set()
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nombres = {n.id for n in ast.walk(nodo) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(nodo) if isinstance(n, ast.Attribute)} | {
            alias.name.rsplit(".", 1)[-1] for n in ast.walk(nodo)
            if isinstance(n, ast.ImportFrom) for alias in n.names}
        if nombres & _ESCRITURAS:
            salida.add(nodo.name)
    return salida


def test_ningun_camino_de_ofertas_escribe_en_factusol_salvo_convertir() -> None:
    """El escáner del paquete: en el servicio solo `convertir` nombra una
    escritura; en la API solo el endpoint del botón, que encola; el job diario
    ninguna; y en los jobs de FACTUSOL solo `convert_oferta_job` toca ofertas."""
    assert _funciones_que_escriben(RAIZ / "erp" / "ofertas.py") == {"convertir"}
    assert _funciones_que_escriben(RAIZ / "erp" / "api" / "ofertas.py") == {
        "convertir_oferta_endpoint"}
    assert _funciones_que_escriben(RAIZ / "erp" / "ofertas_job.py") == set()
    assert _funciones_que_escriben(RAIZ / "erp" / "quote_customer.py") == set()
    # Y el único job de ofertas en la cola de escrituras es el de convertir.
    jobs = (RAIZ / "integrations" / "factusol" / "jobs.py").read_text(encoding="utf-8")
    arbol = ast.parse(jobs)
    con_ofertas = {n.name for n in ast.walk(arbol)
                   if isinstance(n, ast.FunctionDef) and "oferta" in ast.unparse(n).lower()}
    assert con_ofertas == {"convert_oferta_job", "enqueue_convert_oferta"}
    # Ni envío de correo fuera de `enviar`, ni Gmail en el job diario.
    envio = re.compile(r"send_email|send_message|smtplib")
    for nombre in ("ofertas_job.py", "quote_customer.py"):
        assert not envio.search((RAIZ / "erp" / nombre).read_text(encoding="utf-8"))
    servicio = ast.parse((RAIZ / "erp" / "ofertas.py").read_text(encoding="utf-8"))
    envian = {n.name for n in ast.walk(servicio) if isinstance(n, ast.FunctionDef)
              and "send_email" in ast.unparse(n)}
    assert envian == {"enviar"}


# ---------------------------------------------------------------------------
# El asistente ve la oferta; la configuración
# ---------------------------------------------------------------------------


def test_asistente_recibe_la_oferta_en_su_contexto(session_factory) -> None:
    from app.services.asistente import contexto as ctx_mod
    from app.services.asistente import redactor

    with session_factory() as s:
        contacto = _contacto(s)
        o = _oferta(s, contacto, hoy=date(2026, 10, 12))
        usuario = s.query(User).filter_by(email="comercial@example.com").one()
        ctx = ctx_mod.construir(s, thread=None, usuario=usuario, destinatario=contacto.email,
                                instruccion="Mándale la oferta", oferta_id=o.id)
        assert ctx.oferta is not None and ctx.oferta["numero"] == o.numero
        assert ctx.contacto is not None and ctx.contacto["id"] == contacto.id
        bloque = redactor.bloque_contexto(ctx)
        assert "OFERTA ADJUNTA" in bloque and o.numero in bloque
        assert "Impresora UV A3" in bloque and "1.318,90" in bloque
        assert "válida hasta 11-11-2026" in bloque
        resumen = ctx.resumen()
        assert resumen["oferta"]["numero"] == o.numero and resumen["oferta"]["lineas"] == 3
        # Sin oferta, nada cambia.
        sin = ctx_mod.construir(s, thread=None, usuario=usuario, destinatario=contacto.email,
                                instruccion="Hola")
        assert sin.oferta is None and "OFERTA ADJUNTA" not in redactor.bloque_contexto(sin)


def test_buscador_de_contactos_sin_email_para_elegir_el_de_la_oferta(http, session_factory) -> None:
    """Un lead que llegó por teléfono (sin email) puede recibir una oferta: el
    buscador del editor lo encuentra con `con_email=false`; el de los envíos
    por correo sigue sin listarlo."""
    with session_factory() as s:
        s.add(Contact(first_name="Pau", last_name="Sin Correo", email=None))
        s.add(Contact(first_name="Pau", last_name="Con Correo", email="pau@example.com"))
        s.commit()
    headers = auth_headers(http, "comercial")
    con = http.get("/api/erp/contacts/search?q=pau", headers=headers).json()["items"]
    assert [c["name"] for c in con] == ["Pau Con Correo"]
    todos = http.get("/api/erp/contacts/search?q=pau&con_email=false", headers=headers)
    assert todos.status_code == 200, todos.text
    assert sorted(c["name"] for c in todos.json()["items"]) == ["Pau Con Correo", "Pau Sin Correo"]
    assert next(c for c in todos.json()["items"] if c["name"] == "Pau Sin Correo")["email"] is None


def test_fusionar_empresas_mueve_las_ofertas_a_la_principal(session_factory) -> None:
    """`ofertas.company_id` es una FK a `companies.id` (ON DELETE SET NULL): el
    fusionador de empresas la reapunta como contactos, pedidos y tareas."""
    from app.services.company_dedupe import merge_companies_into

    with session_factory() as s:
        principal = _empresa(s, linked=True)
        absorbida = Company(name="Manufaktur GmbH (dup)", source="manual", country="DE",
                            tax_id="DE123456789")
        s.add(absorbida)
        s.commit()
        contacto = _contacto(s, company=absorbida)
        o = _oferta(s, contacto, hoy=date(2026, 10, 12))
        assert o.company_id == absorbida.id
        resultado = merge_companies_into(s, principal, [absorbida])
        s.commit()
        assert resultado["ofertas_moved"] == 1
        assert s.get(Oferta, o.id).company_id == principal.id


def test_configuracion_de_ofertas_prefijo_y_validez(http, session_factory) -> None:
    headers = auth_headers(http)
    r = http.get("/api/erp/settings", headers=headers)
    assert r.json()["ofertas"] == {"prefijo": "OF", "validez_dias": 30}
    r = http.patch("/api/erp/settings", headers=headers,
                   json={"ofertas": {"prefijo": "of", "validez_dias": 45}})
    assert r.status_code == 200, r.text
    assert r.json()["ofertas"] == {"prefijo": "OF", "validez_dias": 45}
    r = http.patch("/api/erp/settings", headers=headers, json={"ofertas": {"validez_dias": 0}})
    assert r.status_code == 400
    with session_factory() as s:
        o = _oferta(s, _contacto(s), hoy=date(2026, 10, 12))
        assert o.validez_dias == 45 and o.numero == "OF-2026-0001"
    meta = http.get("/api/erp/ofertas/meta", headers=headers).json()
    assert meta["validez_dias"] == 45 and [x["serie"] for x in meta["series"]] == [1, 2, 4, 5]
