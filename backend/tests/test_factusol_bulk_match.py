"""BoHub ERP Fase C · C-5 — conciliación masiva CRM ↔ FACTUSOL.

El CRM es la fuente sucia (imports heterogéneos) y F_CLI la limpia. Se prueba
que el dry-run propone sin tocar nada y que el apply solo escribe lo aprobado,
guardando antes los valores previos.

Sin red: el cliente FACTUSOL es un doble que sirve las filas configuradas.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401  — registra los modelos en Base.metadata
from app.db.base import Base
from app.integrations.factusol.bulk_match import (
    BULK_SYNC_ACTION,
    BULK_SYNC_BY_EMAIL_ACTION,
    BULK_SYNC_BY_EMAIL_CREATE_ACTION,
    BULK_SYNC_BY_EMAIL_REASSIGN_ACTION,
    BULK_SYNC_BY_EMAIL_SOURCE,
    BULK_SYNC_SOURCE,
    apply_by_contact_email,
    apply_operations,
    dry_run,
    dry_run_by_contact_email,
)
from app.models.crm import AuditLog, Company, Contact


class _FakeFactusol:
    """Doble del cliente: sirve F_CLI entero, como hace `load_table`."""

    def __init__(self, *, customers=None):
        self.default_ejercicio = "2026"
        self._customers = list(customers or [])
        self.calls: list[str] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        self.calls.append(tabla)
        return list(self._customers) if tabla == "F_CLI" else []


def _cli(codcli: int, **over: Any) -> dict[str, Any]:
    row = {
        "CODCLI": codcli, "NIFCLI": "B61444402",
        "NOFCLI": "AUDIOVISUALES DATA SL", "NOCCLI": "AUDIOVISUALES DATA",
        "DOMCLI": "C/ Industria 12", "POBCLI": "VILADECANS",
        "CPOCLI": "08840", "PROCLI": "Barcelona", "PAICLI": "724",
        "EMACLI": "info@audiovisualesdata.example", "TELCLI": "934000000",
    }
    row.update(over)
    return row


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def session(session_factory) -> Generator[Session, None, None]:
    with session_factory() as s:
        yield s


def _company(session: Session, **over: Any) -> Company:
    data = {"name": "AUDIOVISUALES DATA", "tax_id": "B61444402"}
    data.update(over)
    company = Company(**data)
    session.add(company)
    session.commit()
    return company


# --- dry-run ----------------------------------------------------------------


def test_bulk_match_dry_run_returns_matches_by_nif(session):
    company = _company(session)
    fake = _FakeFactusol(customers=[_cli(3342), _cli(9999, NIFCLI="X0000000X",
                                                   NOFCLI="OTRA COSA",
                                                   NOCCLI="OTRA COSA")])
    result = dry_run(session, fake, ejercicio="2026")

    assert result["total_crm_companies"] == 1
    assert len(result["matches"]) == 1
    match = result["matches"][0]
    assert match["crm_company_id"] == company.id
    assert match["match_type"] == "nif"
    assert match["confidence"] == "high"
    assert [c["factusol_codcli"] for c in match["candidates"]] == ["3342"]

    diffs = {d["field"]: d for d in match["candidates"][0]["differences"]}
    # El nombre difiere (falta el «SL») y la ciudad estaba vacía en el CRM.
    assert diffs["name"]["differs"] is True
    assert diffs["name"]["factusol"] == "AUDIOVISUALES DATA SL"
    assert diffs["city"]["differs"] is True
    # El NIF coincide: no es una diferencia.
    assert diffs["tax_id"]["differs"] is False


def test_bulk_match_dry_run_reads_f_cli_once(session):
    """Con miles de empresas, una consulta por empresa serían miles de
    peticiones contra un token que caduca a los 3 minutos."""
    for i in range(5):
        _company(session, name=f"EMPRESA {i}", tax_id=f"B0000000{i}")
    fake = _FakeFactusol(customers=[_cli(1)])
    dry_run(session, fake, ejercicio="2026")
    assert fake.calls.count("F_CLI") == 1


def test_bulk_match_dry_run_multiple_candidates(session):
    """Caso real: LABORATORIOS PORTA tiene 2 F_CLI con el mismo NIF."""
    _company(session, name="LABORATORIOS PORTA", tax_id="B64113590")
    fake = _FakeFactusol(customers=[
        _cli(1, NIFCLI="B64113590", NOFCLI="LABORATORIOS PORTA S.L.",
             NOCCLI="LABORATORIOS PORTA S.L."),
        _cli(2758, NIFCLI="B64113590", NOFCLI="LABORATORIOS PORTA SL",
             NOCCLI="LAB PORTA"),
    ])
    result = dry_run(session, fake, ejercicio="2026")
    candidates = result["matches"][0]["candidates"]
    assert [c["factusol_codcli"] for c in candidates] == ["1", "2758"]


def test_bulk_match_dry_run_no_match_returned_separately(session):
    _company(session, name="EMPRESA FANTASMA", tax_id="Z9999999Z")
    fake = _FakeFactusol(customers=[_cli(3342)])
    result = dry_run(session, fake, ejercicio="2026")
    assert result["matches"] == []
    assert [n["crm_name"] for n in result["no_match"]] == ["EMPRESA FANTASMA"]


def test_bulk_match_dry_run_matches_by_name_when_no_tax_id(session):
    _company(session, name="Audiovisuales Data", tax_id=None)
    fake = _FakeFactusol(customers=[_cli(3342)])
    result = dry_run(session, fake, ejercicio="2026")
    match = result["matches"][0]
    assert match["match_type"] == "name"
    # Un match por nombre es una sugerencia, no un hecho contable.
    assert match["confidence"] == "low"


def test_bulk_match_dry_run_ignores_short_names(session):
    """Un nombre de 2 letras casaría con media base."""
    _company(session, name="TC", tax_id=None)
    fake = _FakeFactusol(customers=[_cli(3342)])
    assert dry_run(session, fake, ejercicio="2026")["matches"] == []


def test_bulk_match_dry_run_skips_already_linked_by_default(session):
    _company(session, factusol_company_id="3342")
    fake = _FakeFactusol(customers=[_cli(3342)])
    assert dry_run(session, fake, ejercicio="2026")["total_crm_companies"] == 0
    # Con filter=all sí entran, para un refresco masivo.
    assert dry_run(session, fake, ejercicio="2026",
                   unlinked_only=False)["total_crm_companies"] == 1


def test_bulk_match_dry_run_no_escribe_nada(session):
    company = _company(session)
    dry_run(session, _FakeFactusol(customers=[_cli(3342)]), ejercicio="2026")
    session.refresh(company)
    assert company.name == "AUDIOVISUALES DATA"
    assert company.factusol_company_id is None


# --- apply ------------------------------------------------------------------


def test_bulk_match_apply_updates_fields_and_links(session):
    company = _company(session)
    fake = _FakeFactusol(customers=[_cli(3342)])
    result = apply_operations(
        session, fake, ejercicio="2026", operations=[{
            "crm_company_id": company.id, "factusol_codcli": "3342",
            "fields_to_sync": ["name", "city", "postal_code"],
        }],
    )
    assert result == {"applied": 1, "errors": []}
    session.refresh(company)
    assert company.name == "AUDIOVISUALES DATA SL"
    assert company.city == "VILADECANS"
    assert company.postal_code == "08840"
    assert company.factusol_company_id == "3342"
    assert company.factusol_sync_source == BULK_SYNC_SOURCE
    assert company.factusol_synced_at is not None


def test_bulk_match_apply_only_touches_selected_fields(session):
    company = _company(session, address_line="DIRECCIÓN QUE NO SE TOCA")
    fake = _FakeFactusol(customers=[_cli(3342)])
    apply_operations(session, fake, ejercicio="2026", operations=[{
        "crm_company_id": company.id, "factusol_codcli": "3342",
        "fields_to_sync": ["city"],
    }])
    session.refresh(company)
    assert company.city == "VILADECANS"
    assert company.address_line == "DIRECCIÓN QUE NO SE TOCA"


def test_bulk_match_apply_does_not_overwrite_with_empty_factusol_value(session):
    """Un dato vacío en FACTUSOL no pisa el del CRM: el objetivo es limpiar
    datos, no borrarlos."""
    company = _company(session, city="Barcelona")
    fake = _FakeFactusol(customers=[_cli(3342, POBCLI="")])
    apply_operations(session, fake, ejercicio="2026", operations=[{
        "crm_company_id": company.id, "factusol_codcli": "3342",
        "fields_to_sync": ["city"],
    }])
    session.refresh(company)
    assert company.city == "Barcelona"


def test_bulk_match_apply_saves_backup_to_audit_log(session):
    """`companies` no tiene metadata_json, así que el backup va al AuditLog —
    fechado, atribuido y consultable. Es lo que se lee para revertir."""
    company = _company(session, city="CIUDAD VIEJA")
    fake = _FakeFactusol(customers=[_cli(3342)])
    apply_operations(session, fake, ejercicio="2026", operations=[{
        "crm_company_id": company.id, "factusol_codcli": "3342",
        "fields_to_sync": ["name", "city"],
    }], actor_id=None)

    entry = session.scalars(
        select(AuditLog).where(AuditLog.action == BULK_SYNC_ACTION)
    ).one()
    assert entry.target_id == company.id
    meta = json.loads(entry.metadata_json)
    assert meta["factusol_codcli"] == "3342"
    assert meta["applied_fields"] == ["name", "city"]
    assert meta["previous_values"] == {
        "name": "AUDIOVISUALES DATA", "city": "CIUDAD VIEJA",
    }


def test_bulk_match_apply_skips_already_linked(session):
    company = _company(session, factusol_company_id="9999")
    fake = _FakeFactusol(customers=[_cli(3342)])
    result = apply_operations(session, fake, ejercicio="2026", operations=[{
        "crm_company_id": company.id, "factusol_codcli": "3342",
        "fields_to_sync": ["name"],
    }])
    assert result["applied"] == 0
    assert "ya está vinculada" in result["errors"][0]["error"]
    session.refresh(company)
    assert company.factusol_company_id == "9999"


def test_bulk_match_apply_one_failure_does_not_block_the_batch(session):
    """En una limpieza de cientos de registros, abortar el lote entero por un
    caso raro obligaría a repetir toda la revisión."""
    ok = _company(session, name="BUENA")
    fake = _FakeFactusol(customers=[_cli(3342)])
    result = apply_operations(session, fake, ejercicio="2026", operations=[
        {"crm_company_id": "no-existe", "factusol_codcli": "3342",
         "fields_to_sync": ["name"]},
        {"crm_company_id": ok.id, "factusol_codcli": "3342",
         "fields_to_sync": ["name"]},
    ])
    assert result["applied"] == 1
    assert len(result["errors"]) == 1
    session.refresh(ok)
    assert ok.factusol_company_id == "3342"


def test_bulk_match_apply_rejects_unknown_codcli(session):
    company = _company(session)
    fake = _FakeFactusol(customers=[_cli(3342)])
    result = apply_operations(session, fake, ejercicio="2026", operations=[{
        "crm_company_id": company.id, "factusol_codcli": "404",
        "fields_to_sync": ["name"],
    }])
    assert result["applied"] == 0
    assert "no existe" in result["errors"][0]["error"]


# --- modo «contactos por email» (C-5-fix1) ----------------------------------
#
# El modo por NIF/nombre da ruido: la mayoría de las empresas del CRM vienen de
# imports sin NIF y el nombre difuso produce falsos positivos. El email o casa
# exacto o no casa.


def _contact(session: Session, *, email: str | None, company: Company | None = None,
             first_name: str = "Juan", last_name: str = "Pérez") -> Contact:
    contact = Contact(first_name=first_name, last_name=last_name, email=email,
                      company_id=company.id if company else None)
    session.add(contact)
    session.commit()
    return contact


def test_by_email_dry_run_matches_exact_case_insensitive(session):
    company = _company(session, name="Labor. Porta")
    _contact(session, email="Juan@LaboratoriosPorta.com", company=company)
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="juan@laboratoriosporta.com",
                                         NOFCLI="LABORATORIOS PORTA S.L.",
                                         NIFCLI="B64113590")])
    result = dry_run_by_contact_email(session, fake, ejercicio="2026")

    assert result["total_contacts_with_email"] == 1
    assert result["no_match_count"] == 0
    match = result["matches"][0]
    assert match["contact_email"] == "Juan@LaboratoriosPorta.com"
    assert match["company_id"] == company.id
    assert match["candidates"][0]["factusol_codcli"] == "1"
    diffs = {d["field"]: d for d in match["candidates"][0]["differences"]}
    assert diffs["name"]["differs"] is True
    assert diffs["tax_id"]["factusol"] == "B64113590"


def test_by_email_dry_run_no_hace_fuzzy(session):
    """Solo match exacto: un email parecido NO cuenta."""
    company = _company(session)
    _contact(session, email="juan@porta.com", company=company)
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="juan@porta.es")])
    result = dry_run_by_contact_email(session, fake, ejercicio="2026")
    assert result["matches"] == []
    assert result["no_match_count"] == 1


def test_by_email_dry_run_skips_contacts_without_email(session):
    company = _company(session)
    _contact(session, email=None, company=company)
    _contact(session, email="", company=company, first_name="Ana")
    fake = _FakeFactusol(customers=[_cli(1)])
    result = dry_run_by_contact_email(session, fake, ejercicio="2026")
    assert result["total_contacts_with_email"] == 0
    assert result["matches"] == []


def test_by_email_dry_run_marks_contacts_without_company(session):
    _contact(session, email="suelto@example.com", company=None)
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="suelto@example.com")])
    result = dry_run_by_contact_email(session, fake, ejercicio="2026")
    match = result["matches"][0]
    assert match["company_id"] is None
    assert result["matches_without_company"] == 1
    # Sin empresa no hay con qué comparar: se enseña lo que traería FACTUSOL.
    assert match["candidates"][0]["differing_fields"] > 0


def test_by_email_dry_run_reads_f_cli_once(session):
    company = _company(session)
    for i in range(5):
        _contact(session, email=f"c{i}@example.com", company=company,
                 first_name=f"C{i}")
    fake = _FakeFactusol(customers=[_cli(1)])
    dry_run_by_contact_email(session, fake, ejercicio="2026")
    assert fake.calls.count("F_CLI") == 1


# --- apply por email --------------------------------------------------------


def test_by_email_apply_updates_company_of_contact_and_links(session):
    company = _company(session, name="Labor. Porta", city=None)
    contact = _contact(session, email="juan@porta.com", company=company)
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="juan@porta.com",
                                         NOFCLI="LABORATORIOS PORTA S.L.",
                                         POBCLI="Barcelona")])
    result = apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "1",
        "fields_to_sync": ["name", "city"],
    }])
    assert result["applied"] == 1
    assert result["refreshed"] == 1
    assert result["errors"] == []
    assert result["results"][0]["result"] == "refreshed"
    session.refresh(company)
    assert company.name == "LABORATORIOS PORTA S.L."
    assert company.city == "Barcelona"
    assert company.factusol_company_id == "1"
    assert company.factusol_sync_source == BULK_SYNC_BY_EMAIL_SOURCE


def test_by_email_apply_creates_company_when_contact_has_none(session):
    """C-5-fix2: antes se saltaba. Ahora se crea la empresa con los datos
    limpios de F_CLI, se vincula al CODCLI y se le asigna al contacto."""
    contact = _contact(session, email="suelto@example.com", company=None)
    fake = _FakeFactusol(customers=[_cli(
        1, EMACLI="suelto@example.com", NOFCLI="EMPRESA NUEVA SL",
        NIFCLI="B99999999", DOMCLI="C/ Nueva 1", POBCLI="Girona",
        CPOCLI="17001", PROCLI="Girona",
    )])
    result = apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "1",
        "fields_to_sync": ["name"],
    }])
    assert result["applied"] == 1
    assert result["created_new_company"] == 1
    assert result["errors"] == []

    session.refresh(contact)
    assert contact.company_id is not None
    company = session.get(Company, contact.company_id)
    # La empresa nace con TODOS los datos de F_CLI, no solo los marcados: no
    # hay nada previo que preservar.
    assert company.name == "EMPRESA NUEVA SL"
    assert company.tax_id == "B99999999"
    assert company.city == "Girona"
    assert company.postal_code == "17001"
    assert company.factusol_company_id == "1"
    assert company.factusol_sync_source == BULK_SYNC_BY_EMAIL_SOURCE
    assert company.source == "factusol"


def test_by_email_apply_reuses_company_already_linked_to_that_codcli(session):
    """Si otra empresa CRM ya está vinculada a ese CODCLI, se le asigna esa al
    contacto en vez de crear otra: dos empresas apuntando al mismo cliente de
    FACTUSOL es la duplicidad que costó C-3-fix3."""
    existing = _company(session, name="YA VINCULADA", factusol_company_id="1")
    contact = _contact(session, email="suelto@example.com", company=None)
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="suelto@example.com")])
    result = apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "1",
        "fields_to_sync": ["name"],
    }])
    assert result["linked_existing_company"] == 1
    assert result["created_new_company"] == 0
    session.refresh(contact)
    assert contact.company_id == existing.id
    # Y NO se ha creado ninguna empresa de más.
    assert len(list(session.scalars(select(Company)))) == 1


def test_by_email_apply_creation_logs_its_own_audit_action(session):
    """Acción distinta: una empresa creada no se deshace restaurando valores
    previos (no los hay), se deshace borrándola."""
    contact = _contact(session, email="suelto@example.com", company=None)
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="suelto@example.com",
                                         NOFCLI="EMPRESA NUEVA SL")])
    apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "1",
        "fields_to_sync": ["name"],
    }])
    entry = session.scalars(
        select(AuditLog).where(
            AuditLog.action == BULK_SYNC_BY_EMAIL_CREATE_ACTION)
    ).one()
    meta = json.loads(entry.metadata_json)
    assert meta["created_company"] is True
    assert meta["company_name"] == "EMPRESA NUEVA SL"
    assert meta["contact_id"] == contact.id
    assert meta["previous_values"] == {}


def test_by_email_apply_never_overwrites_the_link_of_the_original_company(session):
    """El vínculo de la empresa original es intocable: C-5-fix5 mueve el
    contacto, pero jamás repunta la empresa a otro CODCLI."""
    company = _company(session, factusol_company_id="9999")
    contact = _contact(session, email="juan@porta.com", company=company)
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="juan@porta.com")])
    apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "1",
        "fields_to_sync": ["name"],
    }])
    session.refresh(company)
    assert company.factusol_company_id == "9999"


def test_by_email_apply_permite_reaplicar_al_mismo_codcli(session):
    """Vinculada al MISMO cliente no es conflicto: es un refresco de datos."""
    company = _company(session, factusol_company_id="1", name="VIEJO")
    contact = _contact(session, email="juan@porta.com", company=company)
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="juan@porta.com",
                                         NOFCLI="NOMBRE LIMPIO")])
    result = apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "1",
        "fields_to_sync": ["name"],
    }])
    assert result["applied"] == 1
    session.refresh(company)
    assert company.name == "NOMBRE LIMPIO"


def test_by_email_apply_saves_audit_log_backup(session):
    company = _company(session, name="NOMBRE VIEJO")
    contact = _contact(session, email="juan@porta.com", company=company)
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="juan@porta.com",
                                         NOFCLI="NOMBRE LIMPIO")])
    apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "1",
        "fields_to_sync": ["name"],
    }])
    entry = session.scalars(
        select(AuditLog).where(AuditLog.action == BULK_SYNC_BY_EMAIL_ACTION)
    ).one()
    meta = json.loads(entry.metadata_json)
    assert meta["previous_values"] == {"name": "NOMBRE VIEJO"}
    # El contacto que originó el match queda registrado, para poder rastrearlo.
    assert meta["contact_id"] == contact.id
    assert meta["contact_email"] == "juan@porta.com"


def test_by_email_apply_empty_factusol_value_never_overwrites_crm(session):
    company = _company(session, city="Barcelona")
    contact = _contact(session, email="juan@porta.com", company=company)
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="juan@porta.com", POBCLI="")])
    apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "1",
        "fields_to_sync": ["city"],
    }])
    session.refresh(company)
    assert company.city == "Barcelona"


def test_by_email_dry_run_processes_all_contacts_not_just_a_batch(session):
    """C-5-fix2: el tope de 200 cortaba el bucle a los 200 matches, así que de
    20 282 contactos solo se miraban ~4 000 — y `no_match_count` contaba solo lo
    iterado, con lo que el resumen ni siquiera cuadraba."""
    company = _company(session)
    # 500 con match + 200 sin match: muy por encima del viejo tope de 200.
    customers = []
    for i in range(500):
        _contact(session, email=f"match{i}@example.com", company=company,
                 first_name=f"M{i}")
        customers.append(_cli(1000 + i, EMACLI=f"match{i}@example.com"))
    for i in range(200):
        _contact(session, email=f"nomatch{i}@example.com", company=company,
                 first_name=f"N{i}")

    result = dry_run_by_contact_email(
        session, _FakeFactusol(customers=customers), ejercicio="2026",
    )
    assert result["total_contacts_with_email"] == 700
    assert len(result["matches"]) == 500
    assert result["no_match_count"] == 200
    assert result["truncated"] is False
    # Los totales cuadran: con match + sin match = total.
    assert len(result["matches"]) + result["no_match_count"] == 700


# --- reasignación de contactos mal agrupados (C-5-fix5) ---------------------
#
# Caso Vilatzara: en el primer apply de producción, 128 contactos se omitieron
# y el 90% eran el mismo patrón — decenas de contactos colgando de un único
# «Institut Vilatzara» (codcli 3960) cuando sus emails @xtec.cat son de
# escuelas distintas, cada una con su propio F_CLI. No era un vínculo en
# conflicto, era una agrupación mal hecha en el CRM.


def test_by_email_apply_reassigns_contact_to_existing_company_with_correct_codcli(
    session,
):
    vilatzara = _company(session, name="Institut Vilatzara",
                         factusol_company_id="3960")
    ardenya = _company(session, name="Escola Ardenya",
                       factusol_company_id="4101")
    contact = _contact(session, email="ardenya@xtec.cat", company=vilatzara,
                       first_name="Marta")
    fake = _FakeFactusol(customers=[_cli(4101, EMACLI="ardenya@xtec.cat",
                                         NOFCLI="ESCOLA ARDENYA")])
    result = apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "4101",
        "fields_to_sync": ["name"],
    }])

    assert result["applied"] == 1
    assert result["reassigned_to_existing_company"] == 1
    assert result["reassigned"] == 1
    assert result["skipped_already_linked_other"] == 0
    assert result["results"][0]["result"] == "reassigned_to_existing_company"
    session.refresh(contact)
    assert contact.company_id == ardenya.id
    # Y no se ha creado ninguna empresa de más: ya existía la correcta.
    assert len(list(session.scalars(select(Company)))) == 2


def test_by_email_apply_reassigns_contact_and_creates_new_company_if_none_exists_with_codcli(  # noqa: E501
    session,
):
    vilatzara = _company(session, name="Institut Vilatzara",
                         factusol_company_id="3960")
    contact = _contact(session, email="a8034567@xtec.cat", company=vilatzara,
                       first_name="Pere")
    fake = _FakeFactusol(customers=[_cli(
        4102, EMACLI="a8034567@xtec.cat", NOFCLI="ESCOLA ALEXANDRE GALÍ",
        NIFCLI="Q0801234A", DOMCLI="C/ Escola 3", POBCLI="Mataró",
        CPOCLI="08302", PROCLI="Barcelona",
    )])
    result = apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "4102",
        "fields_to_sync": ["name"],
    }])

    assert result["reassigned_to_new_company"] == 1
    assert result["reassigned_to_existing_company"] == 0
    assert result["created_new_company"] == 0
    session.refresh(contact)
    assert contact.company_id != vilatzara.id
    nueva = session.get(Company, contact.company_id)
    # La empresa nace con TODOS los datos de F_CLI, no solo los marcados.
    assert nueva.name == "ESCOLA ALEXANDRE GALÍ"
    assert nueva.tax_id == "Q0801234A"
    assert nueva.city == "Mataró"
    assert nueva.postal_code == "08302"
    assert nueva.factusol_company_id == "4102"
    assert nueva.factusol_sync_source == BULK_SYNC_BY_EMAIL_SOURCE


def test_by_email_apply_original_company_untouched_after_reassign(session):
    """Vilatzara puede tener contactos legítimos: pierde el mal asignado y nada
    más. Su vínculo, su nombre y sus otros contactos siguen igual."""
    vilatzara = _company(session, name="Institut Vilatzara", city="VILASSAR",
                         factusol_company_id="3960")
    legitimo = _contact(session, email="secretaria@vilatzara.cat",
                        company=vilatzara, first_name="Rosa")
    mal_asignado = _contact(session, email="ardenya@xtec.cat",
                            company=vilatzara, first_name="Marta")
    fake = _FakeFactusol(customers=[_cli(4101, EMACLI="ardenya@xtec.cat",
                                         NOFCLI="ESCOLA ARDENYA",
                                         POBCLI="SANTA CRISTINA")])
    apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": mal_asignado.id, "factusol_codcli": "4101",
        "fields_to_sync": ["name", "city"],
    }])

    session.refresh(vilatzara)
    assert vilatzara.factusol_company_id == "3960"
    assert vilatzara.name == "Institut Vilatzara"
    # Ni siquiera se le han traído los datos de la escuela: no es su cliente.
    assert vilatzara.city == "VILASSAR"
    session.refresh(legitimo)
    assert legitimo.company_id == vilatzara.id
    session.refresh(mal_asignado)
    assert mal_asignado.company_id != vilatzara.id


def test_by_email_apply_audit_log_reassign_includes_old_and_new_company_ids(session):
    """Se audita el CONTACTO, no una empresa: lo que cambia es su company_id, y
    revertirlo es devolverlo a `old_company_id`."""
    vilatzara = _company(session, name="Institut Vilatzara",
                         factusol_company_id="3960")
    ardenya = _company(session, name="Escola Ardenya",
                       factusol_company_id="4101")
    contact = _contact(session, email="ardenya@xtec.cat", company=vilatzara,
                       first_name="Marta")
    fake = _FakeFactusol(customers=[_cli(4101, EMACLI="ardenya@xtec.cat")])
    apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "4101",
        "fields_to_sync": ["name"],
    }])

    entry = session.scalars(
        select(AuditLog).where(
            AuditLog.action == BULK_SYNC_BY_EMAIL_REASSIGN_ACTION)
    ).one()
    assert entry.target_type == "contact"
    assert entry.target_id == contact.id
    meta = json.loads(entry.metadata_json)
    assert meta["contact_id"] == contact.id
    assert meta["contact_email"] == "ardenya@xtec.cat"
    assert meta["old_company_id"] == vilatzara.id
    assert meta["old_company_factusol_id"] == "3960"
    assert meta["new_company_id"] == ardenya.id
    assert meta["new_company_factusol_id"] == "4101"
    assert meta["reassign_type"] == "existing"


def test_by_email_apply_audit_log_reassign_marks_a_created_company(session):
    vilatzara = _company(session, name="Institut Vilatzara",
                         factusol_company_id="3960")
    contact = _contact(session, email="perams@xtec.cat", company=vilatzara)
    fake = _FakeFactusol(customers=[_cli(4103, EMACLI="perams@xtec.cat",
                                         NOFCLI="ESCOLA JOSEP M. PERAMÀS")])
    apply_by_contact_email(session, fake, ejercicio="2026", operations=[{
        "contact_id": contact.id, "factusol_codcli": "4103",
        "fields_to_sync": ["name"],
    }])

    entry = session.scalars(
        select(AuditLog).where(
            AuditLog.action == BULK_SYNC_BY_EMAIL_REASSIGN_ACTION)
    ).one()
    meta = json.loads(entry.metadata_json)
    assert meta["reassign_type"] == "new_created"
    session.refresh(contact)
    assert meta["new_company_id"] == contact.company_id


def test_by_email_apply_reassign_does_not_duplicate_companies_in_a_batch(session):
    """Varios contactos de la MISMA escuela: el primero crea la empresa, los
    demás la encuentran. Dos empresas apuntando al mismo cliente de FACTUSOL es
    la duplicidad que costó C-3-fix3."""
    vilatzara = _company(session, name="Institut Vilatzara",
                         factusol_company_id="3960")
    contactos = [
        _contact(session, email="ardenya@xtec.cat", company=vilatzara,
                 first_name="Marta"),
        _contact(session, email="ardenya.direccio@xtec.cat", company=vilatzara,
                 first_name="Jordi"),
    ]
    fake = _FakeFactusol(customers=[
        _cli(4101, EMACLI="ardenya@xtec.cat", NOFCLI="ESCOLA ARDENYA"),
        _cli(4101, EMACLI="ardenya.direccio@xtec.cat", NOFCLI="ESCOLA ARDENYA"),
    ])
    result = apply_by_contact_email(session, fake, ejercicio="2026", operations=[
        {"contact_id": c.id, "factusol_codcli": "4101",
         "fields_to_sync": ["name"]} for c in contactos
    ])

    assert result["reassigned_to_new_company"] == 1
    assert result["reassigned_to_existing_company"] == 1
    for contact in contactos:
        session.refresh(contact)
    assert contactos[0].company_id == contactos[1].company_id
    # Vilatzara + la escuela creada. Ni una más.
    assert len(list(session.scalars(select(Company)))) == 2


def test_by_email_dry_run_flags_truncation_instead_of_silently_cutting(session):
    """Si algún día se llega al tope de seguridad, se avisa: cortar en silencio
    haría que el operador diera por revisados contactos que nadie miró."""
    company = _company(session)
    customers = []
    for i in range(5):
        _contact(session, email=f"m{i}@example.com", company=company,
                 first_name=f"M{i}")
        customers.append(_cli(2000 + i, EMACLI=f"m{i}@example.com"))

    result = dry_run_by_contact_email(
        session, _FakeFactusol(customers=customers), ejercicio="2026",
        batch_size=2,
    )
    assert len(result["matches"]) == 2
    assert result["truncated"] is True
