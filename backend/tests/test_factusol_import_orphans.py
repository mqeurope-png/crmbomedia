"""BoHub ERP Fase C · C-6 — importar al CRM las F_CLI huérfanas.

Después de C-5 quedan miles de clientes de FACTUSOL que no tiene ninguna
empresa del CRM. Se prueba que el dry-run los encuentra sin tocar nada y que el
apply crea empresa (+ contacto si hay email) sin duplicar ni pisar.

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
from app.integrations.factusol.import_orphans import (
    IMPORT_ORPHANS_ACTION,
    IMPORT_ORPHANS_SOURCE,
    IMPORT_ORPHANS_SYNC_SOURCE,
    IMPORT_ORPHANS_TAG,
    apply_import_orphans,
    dry_run_orphans,
)
from app.models.crm import AuditLog, Company, Contact, ContactTag, Tag


class _FakeFactusol:
    def __init__(self, *, customers=None):
        self.default_ejercicio = "2026"
        self._customers = list(customers or [])
        self.calls: list[str] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        self.calls.append(tabla)
        return list(self._customers) if tabla == "F_CLI" else []


def _cli(codcli: int, **over: Any) -> dict[str, Any]:
    row = {
        "CODCLI": codcli, "NIFCLI": "B12345678", "NOFCLI": "ACME S.L.",
        "NOCCLI": "ACME", "DOMCLI": "C. Mayor 1", "POBCLI": "Barcelona",
        "CPOCLI": "08001", "PROCLI": "Barcelona", "PAICLI": "724",
        "EMACLI": "info@acme.example", "TELCLI": "934567890",
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
    data = {"name": "YA EXISTE"}
    data.update(over)
    company = Company(**data)
    session.add(company)
    session.commit()
    return company


# --- dry-run ----------------------------------------------------------------


def test_import_orphans_dry_run_returns_only_unlinked_factusol_customers(session):
    _company(session, name="YA VINCULADA", factusol_company_id="1")
    fake = _FakeFactusol(customers=[
        _cli(1, NOFCLI="YA VINCULADA SL"),
        _cli(2, NOFCLI="HUÉRFANA UNO"),
        _cli(3, NOFCLI="HUÉRFANA DOS"),
    ])
    result = dry_run_orphans(session, fake, ejercicio="2026")

    assert result["total_factusol_clientes"] == 3
    assert result["linked_already"] == 1
    assert result["orphans_to_import"] == 2
    assert [o["codcli"] for o in result["orphans"]] == ["2", "3"]
    assert result["orphans"][0]["nofcli"] == "HUÉRFANA UNO"
    assert result["orphans"][0]["nifcli"] == "B12345678"
    assert result["orphans"][0]["pobcli"] == "Barcelona"


def test_import_orphans_dry_run_filter_only_with_email(session):
    fake = _FakeFactusol(customers=[
        _cli(1, EMACLI="con@email.example"),
        _cli(2, EMACLI=""),
        _cli(3, EMACLI=None),
    ])
    todos = dry_run_orphans(session, fake, ejercicio="2026")
    assert todos["orphans_to_import"] == 3
    assert todos["with_email"] == 1
    assert todos["without_email"] == 2

    solo_email = dry_run_orphans(session, fake, ejercicio="2026",
                                 only_with_email=True)
    assert solo_email["orphans_to_import"] == 1
    assert solo_email["without_email"] == 0
    assert [o["codcli"] for o in solo_email["orphans"]] == ["1"]


def test_import_orphans_dry_run_marks_which_rows_will_get_a_contact(session):
    fake = _FakeFactusol(customers=[
        _cli(1, EMACLI="con@email.example"), _cli(2, EMACLI=""),
    ])
    orphans = dry_run_orphans(session, fake, ejercicio="2026")["orphans"]
    assert orphans[0]["will_create_contact"] is True
    assert orphans[1]["will_create_contact"] is False


def test_import_orphans_dry_run_reads_f_cli_once_and_writes_nothing(session):
    fake = _FakeFactusol(customers=[_cli(i) for i in range(1, 6)])
    dry_run_orphans(session, fake, ejercicio="2026")
    assert fake.calls.count("F_CLI") == 1
    assert list(session.scalars(select(Company))) == []
    assert list(session.scalars(select(Contact))) == []


# --- apply ------------------------------------------------------------------


def test_import_orphans_apply_creates_company_with_tag(session):
    """El tag literal vive en el CONTACTO (el CRM no tiene tags de empresa).
    Para filtrar el lote entero está `companies.source`, que cubre también las
    empresas sin contacto."""
    fake = _FakeFactusol(customers=[_cli(1234)])
    result = apply_import_orphans(session, fake, ejercicio="2026",
                                  codclis=["1234"])

    assert result["imported_company_and_contact"] == 1
    assert result["errors"] == []
    company = session.scalars(select(Company)).one()
    assert company.name == "ACME S.L."
    assert company.tax_id == "B12345678"
    assert company.address_line == "C. Mayor 1"
    assert company.city == "Barcelona"
    assert company.postal_code == "08001"
    assert company.state == "Barcelona"
    assert company.country == "España"
    assert company.factusol_company_id == "1234"
    assert company.source == IMPORT_ORPHANS_SOURCE
    assert company.factusol_sync_source == IMPORT_ORPHANS_SYNC_SOURCE

    contact = session.scalars(select(Contact)).one()
    tag = session.scalars(select(Tag)).one()
    assert tag.name == IMPORT_ORPHANS_TAG
    link = session.scalars(select(ContactTag)).one()
    assert (link.contact_id, link.tag_id) == (contact.id, tag.id)


def test_import_orphans_sync_source_fits_the_column(session):
    """`companies.factusol_sync_source` es String(16): el «bulk_import_orphans»
    del spec (19) no cabe, y en MySQL estricto sería un error de escritura."""
    assert len(IMPORT_ORPHANS_SYNC_SOURCE) <= 16


def test_import_orphans_apply_creates_contact_when_emacli_present(session):
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="hola@acme.example",
                                         NOFCLI="ACME S.L.",
                                         TELCLI="934567890")])
    result = apply_import_orphans(session, fake, ejercicio="2026", codclis=["1"])

    assert result["results"][0]["result"] == "imported_company_and_contact"
    contact = session.scalars(select(Contact)).one()
    company = session.scalars(select(Company)).one()
    assert contact.email == "hola@acme.example"
    # El nombre de la empresa va en `first_name`: F_CLI guarda razones
    # sociales, no personas. Sin apellido, el operador lo edita después.
    assert contact.first_name == "ACME S.L."
    assert contact.last_name is None
    assert contact.phone == "934567890"
    assert contact.company_id == company.id
    assert result["results"][0]["contact_id"] == contact.id


def test_import_orphans_apply_skips_contact_when_no_emacli(session):
    """Un contacto con solo el nombre de la empresa no aporta y ensucia."""
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="")])
    result = apply_import_orphans(session, fake, ejercicio="2026", codclis=["1"])

    assert result["imported_company_only"] == 1
    assert result["imported_company_and_contact"] == 0
    assert result["results"][0]["contact_skipped"] == "no_email"
    assert result["results"][0]["contact_id"] is None
    assert len(list(session.scalars(select(Company)))) == 1
    assert list(session.scalars(select(Contact))) == []


def test_import_orphans_apply_honours_create_contacts_flag(session):
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="hola@acme.example")])
    result = apply_import_orphans(session, fake, ejercicio="2026",
                                  codclis=["1"], create_contacts_if_email=False)
    assert result["imported_company_only"] == 1
    assert result["results"][0]["contact_skipped"] == "disabled"
    assert list(session.scalars(select(Contact))) == []


def test_import_orphans_apply_creates_tag_if_not_exists(session):
    assert list(session.scalars(select(Tag))) == []
    fake = _FakeFactusol(customers=[_cli(1)])
    apply_import_orphans(session, fake, ejercicio="2026", codclis=["1"])
    tag = session.scalars(select(Tag)).one()
    assert tag.name == IMPORT_ORPHANS_TAG
    assert tag.name_normalized == IMPORT_ORPHANS_TAG


def test_import_orphans_apply_reuses_tag_if_exists(session):
    """Dos lotes seguidos no pueden dejar dos etiquetas iguales."""
    fake = _FakeFactusol(customers=[
        _cli(1, EMACLI="uno@acme.example"), _cli(2, EMACLI="dos@acme.example"),
    ])
    apply_import_orphans(session, fake, ejercicio="2026", codclis=["1"])
    apply_import_orphans(session, fake, ejercicio="2026", codclis=["2"])

    assert len(list(session.scalars(select(Tag)))) == 1
    assert len(list(session.scalars(select(ContactTag)))) == 2


def test_import_orphans_apply_race_condition_skips_gracefully(session):
    """Entre el dry-run y el apply alguien vinculó ese CODCLI. No es un error:
    pisar un vínculo que alguien puso a propósito sería peor."""
    ya = _company(session, name="LLEGÓ ANTES", factusol_company_id="1")
    fake = _FakeFactusol(customers=[_cli(1)])
    result = apply_import_orphans(session, fake, ejercicio="2026", codclis=["1"])

    assert result["skipped_race"] == 1
    assert result["imported"] == 0
    assert result["errors"] == []
    assert result["results"][0]["company_id"] == ya.id
    # Y no se ha creado ninguna empresa de más.
    assert len(list(session.scalars(select(Company)))) == 1


def test_import_orphans_apply_skips_contact_when_email_already_taken(session):
    """`contacts.email` es UNIQUE: intentar crearlo reventaría el INSERT y se
    llevaría por delante la empresa, que sí queremos. Tampoco se le roba el
    contacto a su empresa actual."""
    otra = _company(session, name="OTRA EMPRESA")
    session.add(Contact(first_name="Ya está", email="info@acme.example",
                        company_id=otra.id))
    session.commit()

    fake = _FakeFactusol(customers=[_cli(1, EMACLI="info@acme.example")])
    result = apply_import_orphans(session, fake, ejercicio="2026", codclis=["1"])

    assert result["imported_company_only"] == 1
    assert result["errors"] == []
    assert result["results"][0]["contact_skipped"] == "email_taken"
    # La empresa nueva sí se crea…
    nueva = session.scalars(
        select(Company).where(Company.factusol_company_id == "1")
    ).one()
    assert nueva.source == IMPORT_ORPHANS_SOURCE
    # …y el contacto de la otra empresa se queda donde estaba.
    contact = session.scalars(select(Contact)).one()
    assert contact.company_id == otra.id


def test_import_orphans_audit_log_contains_created_ids(session):
    fake = _FakeFactusol(customers=[_cli(1234)])
    apply_import_orphans(session, fake, ejercicio="2026", codclis=["1234"])

    entry = session.scalars(
        select(AuditLog).where(AuditLog.action == IMPORT_ORPHANS_ACTION)
    ).one()
    company = session.scalars(select(Company)).one()
    contact = session.scalars(select(Contact)).one()
    assert entry.target_type == "company"
    assert entry.target_id == company.id
    meta = json.loads(entry.metadata_json)
    assert meta["codcli"] == "1234"
    assert meta["created_company_id"] == company.id
    assert meta["created_contact_id"] == contact.id
    assert meta["tag"] == IMPORT_ORPHANS_TAG


def test_import_orphans_audit_log_marks_null_contact(session):
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="")])
    apply_import_orphans(session, fake, ejercicio="2026", codclis=["1"])
    entry = session.scalars(
        select(AuditLog).where(AuditLog.action == IMPORT_ORPHANS_ACTION)
    ).one()
    meta = json.loads(entry.metadata_json)
    assert meta["created_contact_id"] is None
    assert meta["tag"] is None


def test_import_orphans_apply_one_failure_does_not_block_the_batch(session):
    fake = _FakeFactusol(customers=[_cli(1, EMACLI="uno@acme.example")])
    result = apply_import_orphans(session, fake, ejercicio="2026",
                                  codclis=["404", "1"])
    assert result["imported"] == 1
    assert len(result["errors"]) == 1
    assert "no existe" in result["errors"][0]["error"]
    assert session.scalars(
        select(Company).where(Company.factusol_company_id == "1")
    ).one() is not None


def test_import_orphans_apply_falls_back_to_noccli_and_placeholder(session):
    fake = _FakeFactusol(customers=[
        _cli(1, NOFCLI="", NOCCLI="SOLO COMERCIAL", EMACLI=""),
        _cli(2, NOFCLI="", NOCCLI="", EMACLI=""),
    ])
    apply_import_orphans(session, fake, ejercicio="2026", codclis=["1", "2"])
    nombres = {
        c.factusol_company_id: c.name
        for c in session.scalars(select(Company))
    }
    assert nombres["1"] == "SOLO COMERCIAL"
    # Sin ningún nombre en F_CLI, mejor un marcador que una empresa vacía.
    assert nombres["2"] == "Cliente 2"


def test_import_orphans_apply_maps_country_from_paicli(session):
    fake = _FakeFactusol(customers=[
        _cli(1, PAICLI="620", EMACLI=""), _cli(2, PAICLI="250", EMACLI=""),
        _cli(3, PAICLI="999", EMACLI=""), _cli(4, PAICLI="", EMACLI=""),
    ])
    apply_import_orphans(session, fake, ejercicio="2026",
                         codclis=["1", "2", "3", "4"])
    paises = {
        c.factusol_company_id: c.country for c in session.scalars(select(Company))
    }
    assert paises["1"] == "Portugal"
    assert paises["2"] == "Francia"
    # Lo desconocido y lo vacío caen a España: la inmensa mayoría lo es.
    assert paises["3"] == "España"
    assert paises["4"] == "España"


def test_import_orphans_apply_truncates_long_contact_name(session):
    """`contacts.first_name` es String(120); `NOFCLI` puede venir más largo."""
    largo = "A" * 300
    fake = _FakeFactusol(customers=[_cli(1, NOFCLI=largo,
                                         EMACLI="largo@acme.example")])
    apply_import_orphans(session, fake, ejercicio="2026", codclis=["1"])
    contact = session.scalars(select(Contact)).one()
    assert len(contact.first_name) == 120
