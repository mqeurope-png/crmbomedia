"""Rule engine: whitelist + compile + in-memory evaluator.

Tests run against an in-memory SQLite DB seeded with a few contacts,
so the SQL plan is actually exercised — not just the AST.
"""
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.crm import (
    Base,
    Contact,
    ContactTag,
    Tag,
)
from app.services.segments.engine import (
    SegmentRuleError,
    build_filter,
    evaluate_contact_against_rules,
)


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


def _seed(session: Session) -> dict[str, Contact]:
    vip = Tag(name="VIP", name_normalized="vip", color="#ef4444")
    cold = Tag(name="Cold", name_normalized="cold")
    session.add_all([vip, cold])
    session.flush()

    contacts = {
        "ana": Contact(
            first_name="Ana",
            email="ana@example.com",
            phone="+34 600 100 100",
            lead_score=80,
            commercial_status="qualified",
            marketing_consent="granted",
            address_country="ES",
        ),
        "boris": Contact(
            first_name="Boris",
            email="boris@example.com",
            phone="+34 600 200 200",
            lead_score=30,
            commercial_status="new",
            marketing_consent="denied",
            address_country="FR",
        ),
        "carla": Contact(
            first_name="Carla",
            email="carla@example.com",
            lead_score=60,
            commercial_status="qualified",
            marketing_consent="granted",
        ),
    }
    for contact in contacts.values():
        session.add(contact)
    session.flush()
    session.add_all(
        [
            ContactTag(contact_id=contacts["ana"].id, tag_id=vip.id, source="manual"),
            ContactTag(contact_id=contacts["carla"].id, tag_id=vip.id, source="manual"),
            ContactTag(contact_id=contacts["boris"].id, tag_id=cold.id, source="manual"),
        ]
    )
    session.commit()
    return contacts


def _ids(contacts: list[Contact]) -> set[str]:
    return {c.id for c in contacts}


def test_unknown_field_is_rejected_before_sql(session_factory):
    """Anti-injection: the whitelist must catch unknown fields BEFORE
    the engine touches any SQL. The route maps this to 400 so the UI
    can highlight the offending node."""
    with pytest.raises(SegmentRuleError):
        build_filter(
            {
                "type": "rule",
                "field": "password_hash",
                "comparator": "contains",
                "value": "%admin%",
            }
        )


def test_unsupported_comparator_for_field_is_rejected(session_factory):
    """Even when the field exists, a comparator outside its whitelist
    must fail. `email` doesn't accept `gt` for instance."""
    with pytest.raises(SegmentRuleError):
        build_filter(
            {
                "type": "rule",
                "field": "email",
                "comparator": "gt",
                "value": "x",
            }
        )


def test_simple_equality_filter(session_factory):
    factory = session_factory
    with factory() as session:
        seeded = _seed(session)
        condition = build_filter(
            {
                "type": "rule",
                "field": "commercial_status",
                "comparator": "eq",
                "value": "qualified",
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["ana"].id, seeded["carla"].id}


def test_and_tree_combines_predicates(session_factory):
    factory = session_factory
    with factory() as session:
        seeded = _seed(session)
        condition = build_filter(
            {
                "operator": "AND",
                "children": [
                    {
                        "type": "rule",
                        "field": "lead_score",
                        "comparator": "gte",
                        "value": 50,
                    },
                    {
                        "type": "rule",
                        "field": "marketing_consent",
                        "comparator": "eq",
                        "value": "granted",
                    },
                ],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["ana"].id, seeded["carla"].id}


def test_or_tree_unifies_predicates(session_factory):
    factory = session_factory
    with factory() as session:
        seeded = _seed(session)
        condition = build_filter(
            {
                "operator": "OR",
                "children": [
                    {
                        "type": "rule",
                        "field": "address_country",
                        "comparator": "eq",
                        "value": "FR",
                    },
                    {
                        "type": "rule",
                        "field": "lead_score",
                        "comparator": "gte",
                        "value": 80,
                    },
                ],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["ana"].id, seeded["boris"].id}


def test_not_tree_inverts_predicate(session_factory):
    factory = session_factory
    with factory() as session:
        seeded = _seed(session)
        condition = build_filter(
            {
                "operator": "NOT",
                "children": [
                    {
                        "type": "rule",
                        "field": "marketing_consent",
                        "comparator": "eq",
                        "value": "granted",
                    }
                ],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["boris"].id}


def test_tag_contains_any_matches_by_tag_id(session_factory):
    factory = session_factory
    with factory() as session:
        seeded = _seed(session)
        vip_id = session.scalar(
            select(Tag.id).where(Tag.name_normalized == "vip")
        )
        condition = build_filter(
            {
                "type": "rule",
                "field": "tags",
                "comparator": "contains_any",
                "value": [vip_id],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["ana"].id, seeded["carla"].id}


def test_tag_contains_none_excludes_tagged_contacts(session_factory):
    factory = session_factory
    with factory() as session:
        seeded = _seed(session)
        vip_id = session.scalar(
            select(Tag.id).where(Tag.name_normalized == "vip")
        )
        condition = build_filter(
            {
                "type": "rule",
                "field": "tags",
                "comparator": "contains_none",
                "value": [vip_id],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["boris"].id}


def test_lead_score_between(session_factory):
    factory = session_factory
    with factory() as session:
        seeded = _seed(session)
        condition = build_filter(
            {
                "type": "rule",
                "field": "lead_score",
                "comparator": "between",
                "value": [40, 70],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["carla"].id}


def test_in_last_n_days_filter(session_factory):
    factory = session_factory
    with factory() as session:
        seeded = _seed(session)
        # Backdate Boris to 100 days ago so the 30-day filter excludes him.
        from datetime import UTC, datetime, timedelta

        boris = session.get(Contact, seeded["boris"].id)
        boris.created_at = datetime.now(UTC) - timedelta(days=100)
        session.commit()

        condition = build_filter(
            {
                "type": "rule",
                "field": "created_at",
                "comparator": "in_last_n_days",
                "value": 30,
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert seeded["boris"].id not in _ids(matched)
        assert seeded["ana"].id in _ids(matched)


def test_in_memory_evaluator_matches_sql_filter(session_factory):
    """The route-level engine and the future Sprint E hook MUST agree.
    Same tree, same verdict on the same contact whether we ran SQL or
    in-memory."""
    factory = session_factory
    with factory() as session:
        seeded = _seed(session)
        tree = {
            "operator": "AND",
            "children": [
                {
                    "type": "rule",
                    "field": "commercial_status",
                    "comparator": "eq",
                    "value": "qualified",
                },
                {
                    "type": "rule",
                    "field": "lead_score",
                    "comparator": "gte",
                    "value": 70,
                },
            ],
        }
        condition = build_filter(tree)
        matched_sql = list(session.scalars(select(Contact).where(condition)))
        matched_mem = [
            c
            for c in (seeded["ana"], seeded["boris"], seeded["carla"])
            if evaluate_contact_against_rules(c, tree)
        ]
        assert _ids(matched_sql) == _ids(matched_mem)


def test_max_depth_is_enforced(session_factory):
    """A maliciously deep tree must not blow the stack. The engine
    caps depth at 10 with a clean error."""
    node: dict = {"type": "rule", "field": "email", "comparator": "is_not_null"}
    for _ in range(12):
        node = {"operator": "AND", "children": [node]}
    with pytest.raises(SegmentRuleError):
        build_filter(node)


def test_empty_tree_matches_everything(session_factory):
    """A blank-canvas segment shows the full contact universe rather
    than crashing the preview."""
    factory = session_factory
    with factory() as session:
        _seed(session)
        condition = build_filter({})
        matched = list(session.scalars(select(Contact).where(condition)))
        assert len(matched) == 3
