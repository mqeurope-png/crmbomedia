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
    BULK_SYNC_SOURCE,
    apply_operations,
    dry_run,
)
from app.models.crm import AuditLog, Company


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
