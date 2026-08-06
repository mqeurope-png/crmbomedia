"""BoHub ERP Fase C · C-7 — deduplicar empresas por NIF.

Caso real: «Exatronic Lda» (`PT503420506`) existe dos veces en el CRM porque en
FACTUSOL hay dos CODCLI con el mismo NIF (2629 y 2819) y el import de C-6 las
trajo por separado.

Lo que hace peligroso el merge: las tres FK a `companies.id` son
`ON DELETE SET NULL`, así que borrar una empresa **no falla** — vacía las
referencias en silencio. Varios tests fijan justo eso.
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
from app.erp.models.orders import Order
from app.models.crm import AuditLog, Company, Contact, Task, TaskPriority
from app.services.company_dedupe import (
    COMPANY_MERGE_ACTION,
    MOVABLE_TABLES,
    _company_fk_tables,
    find_duplicates,
    merge_groups,
)


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


def _company(session: Session, name: str, **over: Any) -> Company:
    data: dict[str, Any] = {"name": name, "tax_id": "PT503420506"}
    data.update(over)
    company = Company(**data)
    session.add(company)
    session.commit()
    return company


def _contact(session: Session, company: Company, **over: Any) -> Contact:
    data: dict[str, Any] = {"first_name": "Ana", "company_id": company.id}
    data.update(over)
    contact = Contact(**data)
    session.add(contact)
    session.commit()
    return contact


def _order(session: Session, company: Company, number: str = "M-1") -> Order:
    order = Order(order_number=number, external_source="manual",
                  company_id=company.id, total_amount=100)
    session.add(order)
    session.commit()
    return order


def _user(session: Session):
    from app.models.crm import User, UserRole  # noqa: PLC0415

    user = User(email="admin@bomedia.example", full_name="Admin",
                password_hash="x", role=UserRole.ADMIN)
    session.add(user)
    session.commit()
    return user


def _task(session: Session, company: Company, user) -> Task:
    task = Task(title="Llamar", company_id=company.id,
                assigned_user_id=user.id, created_by_user_id=user.id,
                priority=TaskPriority.MEDIUM)
    session.add(task)
    session.commit()
    return task


# --- dry-run ----------------------------------------------------------------


def test_duplicates_endpoint_finds_companies_with_same_tax_id(session):
    a = _company(session, "Exatronic Lda", city="Aveiro",
                 factusol_company_id="2629")
    b = _company(session, "Exatronic Lda", factusol_company_id="2819")
    _company(session, "OTRA COSA", tax_id="B99999999")
    _contact(session, a, email="geral@exatronic.example")

    result = find_duplicates(session)

    assert result["total_groups"] == 1
    assert result["total_companies_involved"] == 2
    group = result["groups"][0]
    assert group["tax_id"] == "PT503420506"
    assert {c["id"] for c in group["companies"]} == {a.id, b.id}
    por_id = {c["id"]: c for c in group["companies"]}
    assert por_id[a.id]["contacts_count"] == 1
    assert por_id[a.id]["factusol_company_id"] == "2629"
    assert por_id[b.id]["contacts_count"] == 0
    assert por_id[b.id]["factusol_company_id"] == "2819"


def test_duplicates_endpoint_ignores_empty_tax_id(session):
    """Sin NIF no hay evidencia de que sean la misma empresa: agruparlas todas
    juntas sería un disparate, y este endpoint acaba borrando filas."""
    _company(session, "SIN NIF UNO", tax_id=None)
    _company(session, "SIN NIF DOS", tax_id=None)
    _company(session, "VACÍO UNO", tax_id="")
    _company(session, "VACÍO DOS", tax_id="")

    assert find_duplicates(session)["total_groups"] == 0


def test_duplicates_endpoint_returns_nothing_when_all_are_unique(session):
    _company(session, "UNA", tax_id="B1")
    _company(session, "OTRA", tax_id="B2")
    result = find_duplicates(session)
    assert result == {"total_groups": 0, "total_companies_involved": 0,
                      "groups": []}


def test_duplicates_counts_orders_and_tasks_too(session):
    user = _user(session)
    a = _company(session, "CON HISTORIA")
    _company(session, "VACÍA")
    _order(session, a)
    _order(session, a, number="M-2")
    _task(session, a, user)

    group = find_duplicates(session)["groups"][0]
    con_historia = next(c for c in group["companies"] if c["id"] == a.id)
    assert con_historia["orders_count"] == 2
    assert con_historia["tasks_count"] == 1


def test_duplicates_dry_run_writes_nothing(session):
    a = _company(session, "UNA")
    _company(session, "OTRA")
    find_duplicates(session)
    session.refresh(a)
    assert len(list(session.scalars(select(Company)))) == 2


# --- merge ------------------------------------------------------------------


def test_merge_moves_contacts_from_merged_to_keep(session):
    keep = _company(session, "Exatronic Lda", factusol_company_id="2629")
    other = _company(session, "Exatronic Lda", factusol_company_id="2819")
    contact = _contact(session, other, email="geral@exatronic.example")

    result = merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    assert result["merged_groups"] == 1
    assert result["contacts_moved"] == 1
    assert result["errors"] == []
    session.refresh(contact)
    assert contact.company_id == keep.id


def test_merge_moves_orders_from_merged_to_keep(session):
    keep = _company(session, "PRINCIPAL")
    other = _company(session, "ABSORBIDA")
    order = _order(session, other)

    result = merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    assert result["orders_moved"] == 1
    session.refresh(order)
    assert order.company_id == keep.id


def test_merge_moves_tasks_too(session):
    """`tasks.company_id` es la FK que el merge viejo de la ficha se dejaba: al
    borrar la empresa la ponía a NULL sin avisar."""
    user = _user(session)
    keep = _company(session, "PRINCIPAL")
    other = _company(session, "ABSORBIDA")
    task = _task(session, other, user)

    result = merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    assert result["tasks_moved"] == 1
    session.refresh(task)
    assert task.company_id == keep.id


def test_merge_completes_empty_fields_in_keep_from_merge(session):
    keep = _company(session, "Exatronic Lda", city=None, address_line=None)
    other = _company(session, "Exatronic Lda", city="Aveiro",
                     address_line="R. Eng. Ferreira 1", website="exatronic.pt")

    merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    session.refresh(keep)
    assert keep.city == "Aveiro"
    assert keep.address_line == "R. Eng. Ferreira 1"
    assert keep.website == "exatronic.pt"


def test_merge_does_not_overwrite_non_empty_fields_in_keep(session):
    """Si el operador la eligió como principal, sus datos mandan."""
    keep = _company(session, "NOMBRE BUENO", city="Aveiro", notes="lo mío")
    other = _company(session, "NOMBRE MALO", city="OTRA CIUDAD", notes="lo suyo")

    merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    session.refresh(keep)
    assert keep.name == "NOMBRE BUENO"
    assert keep.city == "Aveiro"
    assert keep.notes == "lo mío"


def test_merge_deletes_merged_company_after_moving_all_records(session):
    user = _user(session)
    keep = _company(session, "PRINCIPAL")
    other = _company(session, "ABSORBIDA")
    contact = _contact(session, other, email="x@y.example")
    order = _order(session, other)
    task = _task(session, other, user)

    merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    assert session.get(Company, other.id) is None
    # Y nada quedó huérfano: las tres FK son ON DELETE SET NULL, así que un
    # merge que se olvidase de una tabla la habría vaciado sin dar error.
    for row in (contact, order, task):
        session.refresh(row)
        assert row.company_id == keep.id


def test_merge_rejects_if_tax_ids_differ(session):
    """Fusionar dos NIF distintos sería juntar dos empresas de verdad, y eso no
    se deshace con un UPDATE."""
    keep = _company(session, "UNA", tax_id="PT503420506")
    other = _company(session, "OTRA", tax_id="B99999999")

    result = merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    assert result["merged_groups"] == 0
    assert len(result["errors"]) == 1
    assert "no se fusionan" in result["errors"][0]["error"]
    assert session.get(Company, other.id) is not None


def test_merge_accepts_tax_ids_that_only_differ_in_punctuation(session):
    keep = _company(session, "UNA", tax_id="B-61.444 402")
    other = _company(session, "OTRA", tax_id="b61444402")
    result = merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])
    assert result["merged_groups"] == 1


def test_merge_saves_snapshot_in_audit_log_for_rollback(session):
    keep = _company(session, "PRINCIPAL", factusol_company_id="2629")
    other = _company(session, "ABSORBIDA", city="Aveiro",
                     factusol_company_id="2819", source="factusol_import")
    _contact(session, other, email="x@y.example")

    merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    entry = session.scalars(
        select(AuditLog).where(AuditLog.action == COMPANY_MERGE_ACTION)
    ).one()
    assert entry.target_id == keep.id
    meta = json.loads(entry.metadata_json)
    assert meta["keep_id"] == keep.id
    assert meta["merge_ids"] == [other.id]
    snapshot = meta["merged_data_snapshot"][0]
    # Snapshot completo: es lo único que queda para rehacerla a mano.
    assert snapshot["id"] == other.id
    assert snapshot["name"] == "ABSORBIDA"
    assert snapshot["city"] == "Aveiro"
    assert snapshot["tax_id"] == "PT503420506"
    assert snapshot["factusol_company_id"] == "2819"
    assert snapshot["source"] == "factusol_import"
    assert meta["moved"]["contacts_moved"] == 1


def test_merge_keeps_the_principal_codcli_and_records_the_discarded_one(session):
    """Puede haber facturación colgando del CODCLI descartado: si no queda
    registrado, nadie sabría que se perdió el vínculo."""
    keep = _company(session, "PRINCIPAL", factusol_company_id="2629")
    other = _company(session, "ABSORBIDA", factusol_company_id="2819")

    result = merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    session.refresh(keep)
    assert keep.factusol_company_id == "2629"
    assert result["results"][0]["discarded_factusol_codclis"] == ["2819"]
    meta = json.loads(session.scalars(
        select(AuditLog).where(AuditLog.action == COMPANY_MERGE_ACTION)
    ).one().metadata_json)
    assert meta["discarded_factusol_codclis"] == ["2819"]


def test_merge_inherits_codcli_when_the_principal_has_none(session):
    keep = _company(session, "PRINCIPAL", factusol_company_id=None)
    other = _company(session, "ABSORBIDA", factusol_company_id="2819",
                     factusol_sync_source="import_orphans")

    result = merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    session.refresh(keep)
    assert keep.factusol_company_id == "2819"
    assert keep.factusol_sync_source == "import_orphans"
    assert result["results"][0]["discarded_factusol_codclis"] == []


def test_merge_aborts_operation_if_an_unhandled_fk_exists(monkeypatch, session):
    """El guard de verdad no es una IntegrityError: las FK son ON DELETE SET
    NULL, así que borrar la empresa vaciaría la tabla desconocida en silencio.
    Si aparece una FK que no sabemos mover, el merge se niega."""
    keep = _company(session, "PRINCIPAL")
    other = _company(session, "ABSORBIDA")
    monkeypatch.setattr(
        "app.services.company_dedupe._company_fk_tables",
        lambda: set(MOVABLE_TABLES) | {"invoices_que_nadie_registro"},
    )

    result = merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [other.id]},
    ])

    assert result["merged_groups"] == 0
    assert "invoices_que_nadie_registro" in result["errors"][0]["error"]
    # Y no ha borrado nada.
    assert session.get(Company, other.id) is not None


def test_movable_tables_covers_every_fk_to_companies():
    """Regresión del guard anterior: si alguien añade una FK a companies.id y
    no la registra, este test lo dice antes de que lo diga producción."""
    assert _company_fk_tables() <= set(MOVABLE_TABLES)


def test_merge_one_failure_does_not_block_the_batch(session):
    ok_keep = _company(session, "OK PRINCIPAL", tax_id="B1")
    ok_other = _company(session, "OK ABSORBIDA", tax_id="B1")
    bad_keep = _company(session, "MALA", tax_id="B2")

    result = merge_groups(session, operations=[
        {"keep_id": bad_keep.id, "merge_ids": ["no-existe"]},
        {"keep_id": ok_keep.id, "merge_ids": [ok_other.id]},
    ])

    assert result["merged_groups"] == 1
    assert len(result["errors"]) == 1
    assert session.get(Company, ok_other.id) is None


def test_merge_rejects_keeping_and_merging_the_same_company(session):
    keep = _company(session, "UNA")
    result = merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [keep.id]},
    ])
    assert result["merged_groups"] == 0
    assert session.get(Company, keep.id) is not None


def test_merge_absorbs_several_companies_at_once(session):
    keep = _company(session, "PRINCIPAL")
    b = _company(session, "SEGUNDA", city="Aveiro")
    c = _company(session, "TERCERA", website="exatronic.pt")
    _contact(session, b, email="b@x.example")
    _contact(session, c, email="c@x.example")

    result = merge_groups(session, operations=[
        {"keep_id": keep.id, "merge_ids": [b.id, c.id]},
    ])

    assert result["companies_deleted"] == 2
    assert result["contacts_moved"] == 2
    session.refresh(keep)
    assert keep.city == "Aveiro"
    assert keep.website == "exatronic.pt"
    assert len(list(session.scalars(select(Company)))) == 1
