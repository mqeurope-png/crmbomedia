"""Sprint Reglas-Assign — PR-Da hotfix tests.

Bug 1: `origin_account_id` debe filtrar por la TUPLA (system,
account_id), no solo por account_id. Brevo y AgileCRM (y otros sistemas
futuros) pueden usar el mismo literal "default" como account_id, así
que un EXISTS plano cross-system matchea contactos de cuentas
equivocadas.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.crm import (
    Base,
    Contact,
    ExternalReference,
    ExternalSystem,
)
from app.services.segments.engine import build_filter


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(engine)


def _seed(factory: sessionmaker) -> dict[str, str]:
    """3 contactos: A en Agile/default, B en Brevo/default, C sin
    external_refs."""
    with factory() as session:
        a = Contact(first_name="A", email="a@a.com")
        b = Contact(first_name="B", email="b@b.com")
        c = Contact(first_name="C", email="c@c.com")
        session.add_all([a, b, c])
        session.flush()
        session.add(
            ExternalReference(
                system=ExternalSystem.AGILECRM,
                account_id="default",
                external_id="ag-1",
                contact_id=a.id,
            )
        )
        session.add(
            ExternalReference(
                system=ExternalSystem.BREVO,
                account_id="default",
                external_id="br-1",
                contact_id=b.id,
            )
        )
        session.commit()
        return {"a": a.id, "b": b.id, "c": c.id}


def test_compound_key_filters_only_target_system(
    session_factory: sessionmaker,
) -> None:
    """`agilecrm:default` matchea solo el contacto Agile, NO el Brevo
    (que comparte account_id literal)."""
    ids = _seed(session_factory)
    tree = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "origin_account_id",
                "comparator": "eq",
                "value": "agilecrm:default",
            }
        ],
    }
    flt = build_filter(tree)
    with session_factory() as session:
        rows = list(session.scalars(select(Contact.id).where(flt)))
        assert rows == [ids["a"]]


def test_compound_key_in_multiple_pairs(
    session_factory: sessionmaker,
) -> None:
    """`in [agilecrm:default, brevo:default]` matchea ambos pero no C."""
    ids = _seed(session_factory)
    tree = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "origin_account_id",
                "comparator": "in",
                "value": ["agilecrm:default", "brevo:default"],
            }
        ],
    }
    flt = build_filter(tree)
    with session_factory() as session:
        rows = set(session.scalars(select(Contact.id).where(flt)))
        assert rows == {ids["a"], ids["b"]}


def test_compound_key_neq_excludes_target_system_only(
    session_factory: sessionmaker,
) -> None:
    """`neq agilecrm:default` mantiene Brevo + sin-refs, excluye Agile."""
    ids = _seed(session_factory)
    tree = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "origin_account_id",
                "comparator": "neq",
                "value": "agilecrm:default",
            }
        ],
    }
    flt = build_filter(tree)
    with session_factory() as session:
        rows = set(session.scalars(select(Contact.id).where(flt)))
        assert rows == {ids["b"], ids["c"]}


def test_legacy_bare_value_still_works_cross_system(
    session_factory: sessionmaker,
) -> None:
    """Vistas guardadas pre-UI-OriginAccountMultiSelect mandaban
    "default" sin prefijo. Backward-compat: matchea cualquier sistema
    con ese account_id literal (= comportamiento legacy ambiguo)."""
    ids = _seed(session_factory)
    tree = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "origin_account_id",
                "comparator": "eq",
                "value": "default",
            }
        ],
    }
    flt = build_filter(tree)
    with session_factory() as session:
        rows = set(session.scalars(select(Contact.id).where(flt)))
        assert rows == {ids["a"], ids["b"]}
