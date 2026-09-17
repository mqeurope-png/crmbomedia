"""Lote 8 · B3 — fusión masiva de empresas duplicadas por NIF normalizado.

Cubre el planificador (`plan_duplicate_merges`, solo lectura) y el ejecutor
(`apply_duplicate_merges`, que reusa el núcleo de B1: reasigna TODO y archiva la
absorbida, reversible). El agrupado usa `company_discovery.nif_key`, así que
`ESB123` y `B123` caen en el mismo grupo.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401  — registra los modelos en Base.metadata
from app.db.base import Base
from app.erp.models.orders import Order
from app.models.crm import Company, Contact, Task, TaskPriority
from app.services.company_dedupe import (
    apply_duplicate_merges,
    plan_duplicate_merges,
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
    data: dict[str, Any] = {"name": name}
    data.update(over)
    company = Company(**data)
    session.add(company)
    session.commit()
    return company


def _user(session: Session):
    from app.models.crm import User, UserRole  # noqa: PLC0415

    user = User(email="admin@bomedia.example", full_name="Admin",
                password_hash="x", role=UserRole.ADMIN)
    session.add(user)
    session.commit()
    return user


# --- planificador (solo lectura) --------------------------------------------


def test_plan_groups_by_normalized_nif_and_picks_factusol_survivor(session):
    # Mismo NIF salvo el prefijo ES: nif_key los normaliza al mismo grupo.
    keep = _company(session, "Bomedia SL", tax_id="ESB63609309",
                    factusol_company_id="11")
    absorbed = _company(session, "Bomedia SL", tax_id="B63609309")
    _company(session, "Sin duplicar", tax_id="A11111111")  # grupo de 1: se ignora

    plan = plan_duplicate_merges(session)

    assert len(plan.to_merge) == 1
    assert not plan.review
    action = plan.to_merge[0]
    # Superviviente = la vinculada a FACTUSOL; la otra se absorbe.
    assert action.keep_id == keep.id
    assert action.keep_codcli == "11"
    assert action.merge_ids == [absorbed.id]


def test_plan_reviews_when_no_company_is_linked_to_factusol(session):
    _company(session, "Onlyguay SC", tax_id="B12312312")
    _company(session, "Onlyguay S C", tax_id="B12312312")

    plan = plan_duplicate_merges(session)

    assert not plan.to_merge
    assert len(plan.review) == 1
    assert "ninguna" in plan.review[0].reason.lower()


def test_plan_reviews_when_several_distinct_codclis(session):
    _company(session, "Exatronic Lda", tax_id="PT503420506",
             factusol_company_id="2629")
    _company(session, "Exatronic Lda", tax_id="PT503420506",
             factusol_company_id="2819")

    plan = plan_duplicate_merges(session)

    assert not plan.to_merge
    assert len(plan.review) == 1
    assert "2629" in plan.review[0].reason and "2819" in plan.review[0].reason


def test_plan_reviews_when_names_are_disparate(session):
    _company(session, "ACME SL", tax_id="B70707070", factusol_company_id="55")
    _company(session, "Zzz Totalmente Distinta Corp", tax_id="B70707070")

    plan = plan_duplicate_merges(session)

    assert not plan.to_merge
    assert len(plan.review) == 1
    assert "dispares" in plan.review[0].reason.lower()


def test_plan_skips_malformed_and_empty_nif(session):
    # NIF vacío o basura (sin dígitos / muy corto) no agrupa.
    _company(session, "A", tax_id="")
    _company(session, "B", tax_id=None)
    _company(session, "C", tax_id="XX")
    _company(session, "D", tax_id="XX")

    plan = plan_duplicate_merges(session)

    assert not plan.to_merge
    assert not plan.review


def test_plan_writes_nothing(session):
    keep = _company(session, "Bomedia SL", tax_id="B63609309",
                    factusol_company_id="11")
    absorbed = _company(session, "Bomedia SL", tax_id="B63609309")

    plan_duplicate_merges(session)
    session.expire_all()

    # El dry-run NO archiva ni reasigna nada.
    assert session.get(Company, keep.id).is_archived is False
    assert session.get(Company, absorbed.id).is_archived is False


# --- ejecutor ----------------------------------------------------------------


def test_apply_reassigns_everything_and_archives_absorbed(session):
    user = _user(session)
    keep = _company(session, "Bomedia SL", tax_id="ESB63609309",
                    factusol_company_id="11")
    absorbed = _company(session, "Bomedia SL", tax_id="B63609309")
    # Todo lo que cuelga de la absorbida debe reasignarse (no huérfanos).
    session.add_all([
        Contact(first_name="Ana", company_id=absorbed.id),
        Order(order_number="M-1", external_source="manual",
              company_id=absorbed.id, total_amount=100),
        Task(title="Llamar", company_id=absorbed.id, assigned_user_id=user.id,
             created_by_user_id=user.id, priority=TaskPriority.MEDIUM),
    ])
    session.commit()

    plan = plan_duplicate_merges(session)
    summary = apply_duplicate_merges(session, plan, actor=user)

    assert summary["merged_groups"] == 1
    assert summary["companies_archived"] == 1
    assert (summary["contacts_moved"], summary["orders_moved"],
            summary["tasks_moved"]) == (1, 1, 1)

    session.expire_all()
    # La absorbida se ARCHIVA (reversible), no se borra.
    absorbed_now = session.get(Company, absorbed.id)
    assert absorbed_now is not None and absorbed_now.is_archived is True
    assert session.get(Company, keep.id).is_archived is False
    # Sus registros ahora cuelgan de la superviviente.
    for model in (Contact, Order, Task):
        rows = list(session.scalars(select(model)))
        assert rows and all(r.company_id == keep.id for r in rows)


def test_apply_is_idempotent(session):
    keep = _company(session, "Bomedia SL", tax_id="B63609309",
                    factusol_company_id="11")
    _company(session, "Bomedia SL", tax_id="B63609309")

    plan1 = plan_duplicate_merges(session)
    apply_duplicate_merges(session, plan1)

    # Segunda pasada: la absorbida ya está archivada, así que el grupo se
    # reduce a la superviviente sola y no hay nada que fusionar.
    session.expire_all()
    plan2 = plan_duplicate_merges(session)
    assert not plan2.to_merge
    summary2 = apply_duplicate_merges(session, plan2)
    assert summary2["merged_groups"] == 0
    assert session.get(Company, keep.id).is_archived is False


def test_apply_only_key_filters_to_one_group(session):
    keep_a = _company(session, "Bomedia SL", tax_id="B63609309",
                      factusol_company_id="11")
    _company(session, "Bomedia SL", tax_id="B63609309")
    _company(session, "Otra SL", tax_id="B70707070", factusol_company_id="22")
    _company(session, "Otra SL", tax_id="B70707070")

    plan = plan_duplicate_merges(session, only_key="B63609309")
    assert len(plan.to_merge) == 1
    assert plan.to_merge[0].keep_id == keep_a.id
