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


def test_apply_completes_group_with_domain_collision(session):
    """El caso que reventaba en la fusión masiva (CREDAN, LÚDIC 3, URV, Mario
    Castillo): la absorbida tiene dominio y la superviviente no. Antes iba a
    `errors` por el índice único; ahora se fusiona y re-correr la completa."""
    keep = _company(session, "CREDAN", tax_id="B11111111",
                    factusol_company_id="11")
    absorbed = _company(session, "CREDAN SL", tax_id="B11111111",
                        domain="credan.com")
    session.add(Order(order_number="M-1", external_source="manual",
                      company_id=absorbed.id, total_amount=100))
    session.commit()

    plan = plan_duplicate_merges(session)
    summary = apply_duplicate_merges(session, plan)

    assert summary["merged_groups"] == 1
    assert summary["errors"] == []
    assert summary["orders_moved"] == 1
    session.expire_all()
    keep_now = session.get(Company, keep.id)
    absorbed_now = session.get(Company, absorbed.id)
    assert keep_now.domain == "credan.com"       # heredado sin colisión
    assert absorbed_now.is_archived is True
    assert absorbed_now.domain is None           # liberado

    # Re-correr no vuelve a tocar nada (idempotente).
    plan2 = plan_duplicate_merges(session)
    assert not plan2.to_merge
    assert apply_duplicate_merges(session, plan2)["merged_groups"] == 0


def test_apply_completes_group_with_garbage_domain(session):
    keep = _company(session, "Ludic 3 SL", tax_id="Q9350003A",
                    factusol_company_id="22")
    _company(session, "Ludic 3 SL", tax_id="Q9350003A", domain="instagram.com")

    summary = apply_duplicate_merges(session, plan_duplicate_merges(session))

    assert summary["merged_groups"] == 1
    assert summary["errors"] == []
    session.expire_all()
    assert session.get(Company, keep.id).domain is None  # basura no heredada


# --- modo --by-name: el guard debe alinearse con el criterio del plan --------


def test_apply_by_name_merges_absorbed_without_nif(session):
    """El bug: en `--by-name` los absorbidos NO tienen NIF (es la condición del
    modo), pero el guard exigía que compartieran el NIF de la superviviente, así
    que abortaba TODOS los grupos («ya no comparte NIF normalizado»). Ahora un
    absorbido sin NIF es compatible y el grupo se fusiona."""
    from app.services.company_dedupe import plan_name_only_merges

    keep = _company(session, "EURL Y'A PAS PHOTO", tax_id="FR91523447399",
                    factusol_company_id="3854")
    sin_nif = [_company(session, "EURL Y'A PAS PHOTO") for _ in range(3)]

    plan = plan_name_only_merges(session)
    assert len(plan.to_merge) == 1, "el dry-run ya los veía"

    summary = apply_duplicate_merges(session, plan)

    assert summary["errors"] == []
    assert summary["merged_groups"] == 1
    assert summary["companies_archived"] == 3
    session.expire_all()
    assert session.get(Company, keep.id).is_archived is False
    for other in sin_nif:
        assert session.get(Company, other.id).is_archived is True


def test_apply_by_name_still_aborts_two_distinct_nifs(session):
    """Sigue sin fusionar a ciegas: dos NIF no vacíos y DISTINTOS se abortan
    aunque el plan (manipulado) los traiga juntos."""
    from app.services.company_dedupe import (
        MODE_NAME,
        MassMergePlan,
        MergeGroupAction,
    )

    keep = _company(session, "Talleres Gomez", tax_id="B64113590")
    otro = _company(session, "Talleres Gomez", tax_id="B12345674")
    plan = MassMergePlan(mode=MODE_NAME, to_merge=[MergeGroupAction(
        nif_key="nombre:TALLERESGOMEZ", keep_id=keep.id, keep_name=keep.name,
        keep_codcli=None, merge_ids=[otro.id], merge_names=[otro.name])])

    summary = apply_duplicate_merges(session, plan)

    assert summary["merged_groups"] == 0
    assert len(summary["errors"]) == 1
    assert "no comparte NIF" in summary["errors"][0]["error"]
    session.expire_all()
    assert session.get(Company, otro.id).is_archived is False


def test_apply_by_nif_guard_unchanged(session):
    """El modo por-NIF conserva su guard estricto: si el plan quedó obsoleto y
    los NIF ya no casan, se aborta el grupo."""
    from app.services.company_dedupe import MassMergePlan, MergeGroupAction

    keep = _company(session, "Bomedia", tax_id="B64113590",
                    factusol_company_id="11")
    otro = _company(session, "Bomedia SL", tax_id="B64113590")
    plan = MassMergePlan(to_merge=[MergeGroupAction(
        nif_key="B64113590", keep_id=keep.id, keep_name=keep.name,
        keep_codcli="11", merge_ids=[otro.id], merge_names=[otro.name])])
    # El plan se queda obsoleto: alguien corrige el NIF de la absorbida.
    otro.tax_id = "B99999999"
    session.commit()

    summary = apply_duplicate_merges(session, plan)

    assert summary["merged_groups"] == 0
    assert "no comparte NIF" in summary["errors"][0]["error"]


def test_apply_by_name_is_idempotent(session):
    """Re-ejecutar tras aplicar no vuelve a fusionar nada."""
    from app.services.company_dedupe import plan_name_only_merges

    _company(session, "EURL Y'A PAS PHOTO", tax_id="FR91523447399")
    _company(session, "EURL Y'A PAS PHOTO")

    plan = plan_name_only_merges(session)
    apply_duplicate_merges(session, plan)
    session.expire_all()

    # Las absorbidas quedaron archivadas → salen del universo del plan.
    again = plan_name_only_merges(session)
    assert again.to_merge == [] and again.review == []
