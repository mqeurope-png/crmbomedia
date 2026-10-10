"""Respuesta a leads · varios intereses por lead y el catálogo en datos
(10/10/2026): la lista ordenada, el mapa con combinaciones, los huecos, la
corrección múltiple, el catálogo desde la API (alta, edición, desactivar sin
borrar) y la migración de los códigos antiguos."""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.email_templates.models import EmailTemplate
from app.main import app
from app.models.crm import Contact
from app.models.leads import LeadClassification, LeadInterest
from app.services.leads import config as config_leads
from app.services.leads import intereses, plantillas, registro
from app.services.leads.clasificador import (
    Clasificacion,
    ClasificadorPalabrasClave,
    EntradaLead,
    clasificar_lead,
)
from app.services.leads.intereses import Catalogo
from app.services.leads.proveedor_anthropic import prompt_sistema
from app.workflows import variables
from tests._test_helpers import auth_headers, seed_test_users

METAL_Y_CAMISETAS = "Ich möchte auf Metallplatten sowie auf T-Shirts drucken"


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


def _plantilla(session: Session, nombre: str) -> EmailTemplate:
    tpl = EmailTemplate(name=nombre, subject=nombre, body_html="<p>x</p>", is_global=True)
    session.add(tpl)
    session.flush()
    return tpl


def _contacto(session: Session, email: str) -> Contact:
    c = Contact(first_name="Lead", email=email)
    session.add(c)
    session.flush()
    return c


def _clasificar_y_registrar(
    session: Session, contacto: Contact, texto: str, *, referencia: str = "envio-1",
    intereses_ia: list[str] | None = None,
) -> LeadClassification:
    entrada = EntradaLead(texto=texto, fuente="web_form", referencia=referencia,
                          lead_at=datetime.now(UTC), sitio="artisjet-eu", idioma_formulario="de",
                          email=contacto.email)
    if intereses_ia is None:
        clasificacion = clasificar_lead(entrada, ClasificadorPalabrasClave())
    else:
        clasificacion = Clasificacion(idioma="de", intereses=intereses_ia, confianza=0.9,
                                      motivo="ia de prueba", idioma_fuente="ia",
                                      interes_fuente="ia", proveedor="ia_falsa")
    return registro.registrar(session, contacto, entrada, clasificacion)


# --- el catálogo (puro) -------------------------------------------------------


def test_el_catalogo_de_partida_tiene_los_trece_y_limpia_lo_que_devuelve_un_proveedor() -> None:
    cat = Catalogo.de_partida()
    assert cat.codigos == (
        "uv_pequeno", "uv_mediano", "uv_grande", "dtf", "corte_laser", "grabado_laser", "cnc",
        "packaging", "vending", "distribucion", "soporte_postventa", "tienda", "otro",
    )
    assert cat.comerciales == set(cat.codigos) - {"soporte_postventa", "otro"}
    assert cat.etiqueta("tienda") == "Tienda · consumibles y repuestos"
    assert cat.etiqueta("cohetes") == "cohetes" and cat.etiqueta(None) == "—"
    assert cat.texto(["uv_mediano", "dtf"]) == "UV LED mediano formato + DTF · impresión textil"
    # Códigos desconocidos fuera, repetidos una vez, una sola talla de UV,
    # «otro» si no queda nada.
    assert cat.limpiar(["dtf", "cohetes", "uv_mediano", "uv_grande", "DTF "]) \
        == ["dtf", "uv_mediano"]
    assert cat.limpiar([]) == ["otro"] and cat.limpiar(["cohetes"]) == ["otro"]
    assert "`dtf`: DTF · impresión textil — Impresión textil" in cat.para_prompt()
    assert "`otro`: Otro (no es una venta)" in cat.para_prompt()


def test_los_codigos_antiguos_se_migran_a_los_nuevos() -> None:
    assert intereses.migrar_codigo("uv_gran_formato") == "uv_grande"
    assert intereses.migrar_codigo("laser_cnc") == "corte_laser"
    assert intereses.migrar_codigo("consumibles") == "tienda"
    assert intereses.migrar_codigo("repuestos") == "tienda"
    assert intereses.migrar_codigo("servicio_tecnico") == "soporte_postventa"
    assert intereses.migrar_codigo("uv_pequeno_mediano") == "uv_mediano"   # anotado, Bart revisa
    for igual in ("vending", "distribucion", "otro", None, ""):
        assert intereses.migrar_codigo(igual) == igual
    assert intereses.RENOMBRADOS_MAPA["uv_pequeno_mediano"] == ("uv_pequeno", "uv_mediano")
    assert intereses.RENOMBRADOS_MAPA["consumibles"] == ("tienda",)
    # Todo lo renombrado existe en la lista de partida.
    cat = Catalogo.de_partida()
    assert all(cat.conoce(nuevo) for nuevo in intereses.RENOMBRADOS.values())


# --- el mapa con combinaciones -----------------------------------------------------


def test_la_clave_del_mapa_es_del_conjunto_y_se_valida() -> None:
    assert plantillas.clave_mapa("vending", "ES") == "vending:es"
    assert plantillas.clave_mapa(["uv_mediano", "dtf"], "de") == "dtf+uv_mediano:de"
    assert plantillas.clave_mapa(["dtf", "uv_mediano", "dtf"], "de") == "dtf+uv_mediano:de"
    assert plantillas.partes_clave("dtf+uv_mediano:de") == (["dtf", "uv_mediano"], "de")
    assert plantillas.partes_clave("sin-idioma") == (["sin-idioma"], "")

    base = config_leads.validar({"mapa": {
        "vending:es": "tpl-1", "uv_mediano+dtf:de": "tpl-2", "cnc:fr": "",
        "soporte_postventa:es": "tpl-1",          # cualquier código conocido vale
    }}, None, plantillas_validas={"tpl-1", "tpl-2"})
    assert base["mapa"] == {"vending:es": "tpl-1", "dtf+uv_mediano:de": "tpl-2", "cnc:fr": "",
                            "soporte_postventa:es": "tpl-1"}
    for malo in ({"mapa": {"dtf+cohetes:de": "tpl-1"}}, {"mapa": {"dtf:xx": "tpl-1"}},
                 {"mapa": {":es": "tpl-1"}},
                 {"mapa": {"dtf+uv_mediano:de": "tpl-1", "uv_mediano+dtf:de": "tpl-2"}}):
        with pytest.raises(ValueError):
            config_leads.validar(malo, None, plantillas_validas={"tpl-1", "tpl-2"})
    # Un interés nuevo del catálogo de la base vale en el mapa.
    cat = Catalogo([*Catalogo.de_partida().todos,
                    intereses.Interes("smartjet", "SmartJet", "", True, 99)])
    assert config_leads.validar({"mapa": {"smartjet:es": "tpl-1"}}, None,
                                plantillas_validas={"tpl-1"}, catalogo=cat)["mapa"] \
        == {"smartjet:es": "tpl-1"}


def test_plantilla_para_elige_el_conjunto_exacto_luego_el_principal_y_si_no_ninguna(
    session_factory,
) -> None:
    with session_factory() as s:
        vending = _plantilla(s, "Lead · Vending (ES)")
        uv_antigua = _plantilla(s, "Lead · UV pequeño-mediano (DE)")
        mixta = _plantilla(s, "Lead · UV y textil (DE)")
        s.commit()
        cat = Catalogo.de_partida()
        mapa = {"dtf+uv_mediano:de": mixta.id, "vending:fr": ""}

        # 1. El conjunto exacto, sin importar el orden del lead.
        assert plantillas.plantilla_para(s, ["uv_mediano", "dtf"], "de", mapa, cat).id == mixta.id
        assert plantillas.plantilla_para(s, ["dtf", "uv_mediano"], "de", mapa, cat).id == mixta.id
        # 2. Sin fila para el conjunto, la del principal: por el nombre antiguo
        #    (UV pequeño-mediano vale para uv_mediano) …
        assert plantillas.plantilla_para(s, ["uv_mediano", "cnc"], "de", mapa, cat).id \
            == uv_antigua.id
        #    … o por el nombre nuevo; y un solo interés, como siempre.
        assert plantillas.plantilla_para(s, ["vending"], "es", mapa, cat).id == vending.id
        assert plantillas.plantilla_para(s, "vending", "es", mapa, cat).id == vending.id
        # 3. Ninguna: DTF principal sin plantilla (aunque UV la tenga), «sin
        #    plantilla a propósito» en el mapa, principal no comercial, nada.
        assert plantillas.plantilla_para(s, ["dtf", "uv_mediano"], "es", mapa, cat) is None
        assert plantillas.plantilla_para(s, ["vending"], "fr", mapa, cat) is None
        assert plantillas.plantilla_para(s, ["soporte_postventa", "vending"], "es", mapa, cat) \
            is None
        assert plantillas.plantilla_para(s, [], "es", mapa, cat) is None
        assert plantillas.plantilla_para(s, ["vending"], None, mapa, cat) is None

        # Los huecos: comercial × idioma sin plantilla; vending:fr es a propósito.
        huecos = {(h["interes"], h["idioma"]): h["a_proposito"]
                  for h in plantillas.huecos(s, mapa, cat)}
        assert ("vending", "es") not in huecos and ("uv_mediano", "de") not in huecos
        assert ("uv_pequeno", "de") not in huecos           # el nombre antiguo vale para los dos
        assert huecos[("vending", "fr")] is True and huecos[("dtf", "de")] is False
        assert ("soporte_postventa", "es") not in huecos   # no comercial: no es un hueco
        resuelto = plantillas.mapa_resuelto_por_nombre(s, cat)
        assert resuelto["uv_mediano:de"] == uv_antigua.id and resuelto["vending:es"] == vending.id
        assert resuelto["dtf:de"] is None and "soporte_postventa:es" not in resuelto


# --- registrar y corregir varios intereses ----------------------------------------


def test_un_lead_con_dos_intereses_se_guarda_ordenado_y_el_contacto_lleva_el_principal(
    session_factory,
) -> None:
    with session_factory() as s:
        contacto = _contacto(s, "torracollons@elbarquito.net")
        fila = _clasificar_y_registrar(s, contacto, METAL_Y_CAMISETAS)
        s.commit()
        assert fila.interest == "uv_pequeno"
        assert fila.intereses == ["uv_pequeno", "dtf"]
        assert json.loads(fila.interests_json) == ["uv_pequeno", "dtf"]
        assert fila.intereses_efectivos == ["uv_pequeno", "dtf"]
        assert contacto.lead_interest == "uv_pequeno"
        # Un lead de uno, como siempre.
        otro = _contacto(s, "ana@ejemplo.es")
        fila1 = _clasificar_y_registrar(s, otro, "Quiero una máquina de vending", referencia="e2")
        assert (fila1.interest, fila1.intereses) == ("vending", ["vending"])
        # Una fila de antes de la migración (sin JSON): la lista es la columna.
        fila1.interests_json = None
        assert fila1.intereses == ["vending"] and fila1.interes_efectivo == "vending"


def test_las_variables_del_workflow_llevan_todos_los_intereses(session_factory) -> None:
    with session_factory() as s:
        contacto = _contacto(s, "torracollons@elbarquito.net")
        _clasificar_y_registrar(s, contacto, METAL_Y_CAMISETAS, intereses_ia=["uv_mediano", "dtf"])
        s.commit()
        lead = variables.build_context(session=s, contact=contacto)["lead"]
        assert lead["interes"] == "uv_mediano"
        assert lead["interes_texto"] == "UV LED mediano formato + DTF · impresión textil"
        assert lead["intereses"] == "uv_mediano, dtf"
        assert lead["interes_principal_texto"] == "UV LED mediano formato"
        titulo = variables.render("Revisar lead: {{ lead.interes_texto }}",
                                  variables.build_context(session=s, contact=contacto))
        assert titulo == "Revisar lead: UV LED mediano formato + DTF · impresión textil"


def test_la_correccion_a_mano_deja_dos_intereses_en_el_orden_elegido(http, session_factory) -> None:
    with session_factory() as s:
        contacto = _contacto(s, "torracollons@elbarquito.net")
        fila = _clasificar_y_registrar(s, contacto, METAL_Y_CAMISETAS)
        s.commit()
        fila_id, contacto_id = fila.id, contacto.id
    cab = auth_headers(http, "admin")
    r = http.get("/api/erp/leads/clasificaciones?dias=15", headers=cab)
    item = r.json()["items"][0]
    assert item["intereses"] == ["uv_pequeno", "dtf"]
    assert item["intereses_texto"] == "UV LED pequeño formato + DTF · impresión textil"
    assert item["efectivo"]["intereses"] == ["uv_pequeno", "dtf"]
    assert item["efectivo"]["intereses_etiquetas"] == ["UV LED pequeño formato",
                                                       "DTF · impresión textil"]
    assert item["interes"] == "uv_pequeno" and item["interes_texto"] == "UV LED pequeño formato"
    opciones = {o["id"]: o for o in r.json()["opciones"]["intereses"]}
    assert opciones["dtf"]["comercial"] is True and opciones["dtf"]["activo"] is True
    assert opciones["otro"]["comercial"] is False

    # Bart dice: es UV mediano y DTF, en ese orden.
    r = http.post(f"/api/erp/leads/clasificaciones/{fila_id}/corregir", headers=cab,
                  json={"intereses": ["uv_mediano", "dtf"], "nota": "Es la 5000U"})
    assert r.status_code == 200, r.text
    corregido = r.json()
    assert corregido["efectivo"]["intereses"] == ["uv_mediano", "dtf"]
    assert corregido["efectivo"]["interes"] == "uv_mediano"
    assert corregido["correccion"]["intereses"] == ["uv_mediano", "dtf"]
    assert corregido["correccion"]["interes"] == "uv_mediano"
    assert corregido["intereses"] == ["uv_pequeno", "dtf"]        # la original se conserva
    with session_factory() as s:
        assert s.get(Contact, contacto_id).lead_interest == "uv_mediano"
        assert json.loads(s.get(LeadClassification, fila_id).corrected_interests_json) \
            == ["uv_mediano", "dtf"]
    # Al revés: DTF principal. Y `interes` suelto sigue valiendo (uno solo).
    r = http.post(f"/api/erp/leads/clasificaciones/{fila_id}/corregir", headers=cab,
                  json={"intereses": ["dtf", "uv_mediano"]})
    assert r.json()["efectivo"]["intereses"] == ["dtf", "uv_mediano"]
    r = http.post(f"/api/erp/leads/clasificaciones/{fila_id}/corregir", headers=cab,
                  json={"interes": "vending"})
    assert r.json()["efectivo"]["intereses"] == ["vending"]
    # Lo que no vale: un código desconocido, la lista vacía, nada que corregir.
    assert http.post(f"/api/erp/leads/clasificaciones/{fila_id}/corregir", headers=cab,
                     json={"intereses": ["dtf", "cohetes"]}).status_code == 400
    assert http.post(f"/api/erp/leads/clasificaciones/{fila_id}/corregir", headers=cab,
                     json={"intereses": []}).status_code == 400
    assert http.post(f"/api/erp/leads/clasificaciones/{fila_id}/corregir", headers=cab,
                     json={}).status_code == 400


# --- el catálogo desde la API ---------------------------------------------------------


def test_un_interes_nuevo_desde_la_pantalla_entra_en_el_clasificador_sin_desplegar(
    http, session_factory,
) -> None:
    cab = auth_headers(http, "admin")
    r = http.get("/api/erp/leads/intereses", headers=cab)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items][:3] == ["uv_pequeno", "uv_mediano", "uv_grande"]
    assert items[0]["en_uso"] == {"clasificaciones": 0, "en_mapa": 0}

    r = http.post("/api/erp/leads/intereses", headers=cab, json={
        "codigo": "SmartJet", "etiqueta": "SmartJet", "comercial": True,
        "descripcion": "Impresión directa sobre objetos cilíndricos con la SmartJet.",
    })
    assert r.status_code == 201, r.text
    assert r.json()["id"] == "smartjet" and r.json()["orden"] == 13
    with session_factory() as s:
        # Al escribir se sembró el catálogo (13 + 1) y el nuevo está al final.
        assert s.scalar(select(LeadInterest.code).where(LeadInterest.code == "smartjet"))
        cat = intereses.cargar(s)
        assert cat.codigos[-1] == "smartjet" and cat.comercial("smartjet")
        # Las instrucciones del modelo lo llevan, con su descripción.
        assert "`smartjet`: SmartJet — Impresión directa sobre objetos cilíndricos" \
            in prompt_sistema(cat)
        # Y lo que la IA devuelva con ese código se conserva (no cae a «otro»).

        class _IA:
            nombre = "ia_falsa"

            def clasificar(self, entrada: EntradaLead) -> Clasificacion:
                return Clasificacion(idioma="es", intereses=["smartjet"], confianza=0.9,
                                     idioma_fuente="ia", interes_fuente="ia", proveedor=self.nombre)

        out = clasificar_lead(EntradaLead(texto="Quiero una SmartJet", fuente="web_form",
                                          referencia="e1"), _IA(), cat)
        assert out.intereses == ["smartjet"]

    # Editar la descripción y la etiqueta; el código no cambia.
    r = http.patch("/api/erp/leads/intereses/smartjet", headers=cab,
                   json={"descripcion": "Objetos cilíndricos: botellas, termos, latas.",
                         "etiqueta": "SmartJet · cilíndricos"})
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "SmartJet · cilíndricos"
    with session_factory() as s:
        assert "`smartjet`: SmartJet · cilíndricos — Objetos cilíndricos" \
            in prompt_sistema(intereses.cargar(s))
    # Lo que no vale.
    assert http.post("/api/erp/leads/intereses", headers=cab,
                     json={"codigo": "smartjet", "etiqueta": "Otra vez"}).status_code == 400
    assert http.post("/api/erp/leads/intereses", headers=cab,
                     json={"codigo": "1 mal", "etiqueta": "x"}).status_code == 400
    assert http.patch("/api/erp/leads/intereses/no-existe", headers=cab,
                      json={"etiqueta": "x"}).status_code == 404
    assert http.patch("/api/erp/leads/intereses/smartjet", headers=cab, json={}).status_code == 400
    # Sin la capacidad de configuración, nada.
    assert http.get("/api/erp/leads/intereses",
                    headers=auth_headers(http, "user")).status_code == 403
    assert http.post("/api/erp/leads/intereses", headers=auth_headers(http, "user"),
                     json={"codigo": "x", "etiqueta": "x"}).status_code == 403
    # Sin uso, se puede borrar.
    assert http.delete("/api/erp/leads/intereses/smartjet", headers=cab).status_code == 204
    with session_factory() as s:
        assert not intereses.cargar(s).conoce("smartjet")


def test_desactivar_un_interes_en_uso_no_lo_borra_y_lo_clasificado_sigue_legible(
    http, session_factory,
) -> None:
    with session_factory() as s:
        contacto = _contacto(s, "ana@ejemplo.es")
        fila = _clasificar_y_registrar(s, contacto, "Quiero una máquina de vending")
        s.commit()
        fila_id = fila.id
    cab = auth_headers(http, "admin")
    # En uso: 409, y sigue en el catálogo.
    r = http.delete("/api/erp/leads/intereses/vending", headers=cab)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "interes_en_uso"
    assert "1 clasificaciones" in r.json()["detail"]["detail"]
    en_uso = {i["id"]: i["en_uso"] for i in
              http.get("/api/erp/leads/intereses", headers=cab).json()["items"]}
    assert en_uso["vending"]["clasificaciones"] == 1
    # «Otro» no se borra ni se desactiva nunca.
    assert http.delete("/api/erp/leads/intereses/otro", headers=cab).status_code == 409
    assert http.patch("/api/erp/leads/intereses/otro", headers=cab,
                      json={"activo": False}).status_code == 400

    r = http.patch("/api/erp/leads/intereses/vending", headers=cab, json={"activo": False})
    assert r.status_code == 200 and r.json()["activo"] is False
    with session_factory() as s:
        cat = intereses.cargar(s)
        assert cat.conoce("vending") and not cat.activo("vending")
        assert "vending" not in cat.codigos
        assert "vending" not in [o["id"] for o in cat.opciones()]
        assert "`vending`" not in prompt_sistema(cat)
        assert cat.etiqueta("vending") == "Vending"          # lo clasificado sigue legible
        # Un proveedor que lo devuelva ya no lo coloca: no está en la lista.
        out = clasificar_lead(EntradaLead(texto="Quiero una máquina de vending",
                                          fuente="web_form", referencia="e9"),
                              ClasificadorPalabrasClave(), cat)
        assert out.intereses == ["otro"]
    # La lista corregible lo enseña con su etiqueta y lo ofrece como inactivo.
    body = http.get("/api/erp/leads/clasificaciones?dias=15", headers=cab).json()
    item = next(i for i in body["items"] if i["id"] == fila_id)
    assert item["interes_texto"] == "Vending" and item["efectivo"]["intereses_texto"] == "Vending"
    vending = next(o for o in body["opciones"]["intereses"] if o["id"] == "vending")
    assert vending["activo"] is False
    # Y en el mapa de plantillas también se cuenta como uso.
    http.patch("/api/erp/settings", headers=cab, json={
        "lead_response": {"mapa": {"dtf:es": ""}},
    })
    r = http.delete("/api/erp/leads/intereses/dtf", headers=cab)
    assert r.status_code == 409 and "1 filas del mapa" in r.json()["detail"]["detail"]
    # Vuelve a activarse.
    assert http.patch("/api/erp/leads/intereses/vending", headers=cab,
                      json={"activo": True}).json()["activo"] is True
