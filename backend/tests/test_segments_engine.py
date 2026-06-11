"""Rule engine: whitelist + compile + in-memory evaluator.

Tests run against an in-memory SQLite DB seeded with a few contacts,
so the SQL plan is actually exercised — not just the AST.
"""
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

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


def test_value_type_mismatch_surfaces_as_segment_rule_error(session_factory):
    """A typed value mismatch (e.g. tags expects a list of UUIDs but the
    operator typed a free-form string) used to bubble up as a plain
    `ValueError` and trigger HTTP 500 in production. The engine now
    wraps `validate_value` so the route's `except SegmentRuleError`
    catches it and returns 400 with a field-aware detail."""
    with pytest.raises(SegmentRuleError) as exc_info:
        build_filter(
            {
                "type": "rule",
                "field": "tags",
                "comparator": "contains_any",
                "value": "formmbo",
            }
        )
    assert "tags" in str(exc_info.value)


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


# ---------------------------------------------------------------------------
# in_brevo_list — JSON-anchored LIKE against external_references metadata
# ---------------------------------------------------------------------------


def _seed_brevo_refs(session, contacts, list_ids_by_contact):
    """Attach a Brevo external_references row to each contact with the
    given `list_ids` array (shape mirrors the brevo mapper output)."""
    import json as _json

    from app.models.crm import ExternalReference, ExternalSystem

    for key, list_ids in list_ids_by_contact.items():
        session.add(
            ExternalReference(
                system=ExternalSystem.BREVO,
                account_id="default",
                external_id=f"brevo-{key}",
                contact_id=contacts[key].id,
                metadata_json=_json.dumps(
                    {"list_ids": list_ids, "email_blacklisted": False}
                ),
            )
        )
    session.commit()


def test_in_brevo_list_matches_single_item_array(session_factory):
    with session_factory() as session:
        seeded = _seed(session)
        _seed_brevo_refs(
            session, seeded, {"ana": [4], "boris": [], "carla": [7]}
        )
        condition = build_filter(
            {
                "type": "rule",
                "field": "in_brevo_list",
                "comparator": "in",
                "value": [4],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["ana"].id}


def test_in_brevo_list_handles_multi_element_array_without_false_positives(
    session_factory,
):
    """Searching list_id=1 must not match list 12 (`12 contains 1`
    LIKE false positive)."""
    with session_factory() as session:
        seeded = _seed(session)
        _seed_brevo_refs(
            session,
            seeded,
            {"ana": [1, 12], "boris": [12], "carla": [1]},
        )
        condition = build_filter(
            {
                "type": "rule",
                "field": "in_brevo_list",
                "comparator": "in",
                "value": [1],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["ana"].id, seeded["carla"].id}


def test_in_brevo_list_not_in_inverts(session_factory):
    with session_factory() as session:
        seeded = _seed(session)
        _seed_brevo_refs(session, seeded, {"ana": [4], "boris": [7]})
        condition = build_filter(
            {
                "type": "rule",
                "field": "in_brevo_list",
                "comparator": "not_in",
                "value": [4],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["boris"].id, seeded["carla"].id}


# ---------------------------------------------------------------------------
# in_segment — resolver, OR-across-ids, cycle detection
# ---------------------------------------------------------------------------


def test_in_segment_resolver_compiles_referenced_rules(session_factory):
    """`in_segment = [seg-id]` resolves the referenced segment's tree
    and OR's it into the parent."""
    with session_factory() as session:
        seeded = _seed(session)
        resolver_calls: list[str] = []

        def resolver(sid, _visited):
            resolver_calls.append(sid)
            if sid == "seg-vip":
                return {
                    "type": "rule",
                    "field": "lead_score",
                    "comparator": "gte",
                    "value": 70,
                }
            return None

        condition = build_filter(
            {
                "type": "rule",
                "field": "in_segment",
                "comparator": "in",
                "value": ["seg-vip"],
            },
            segment_resolver=resolver,
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["ana"].id}
        assert resolver_calls == ["seg-vip"]


def test_in_segment_cycle_is_detected(session_factory):
    """A segment that references itself raises rather than looping."""

    def resolver(sid, _visited):
        return {
            "type": "rule",
            "field": "in_segment",
            "comparator": "in",
            "value": [sid],
        }

    with session_factory() as session:
        _seed(session)
        with pytest.raises(SegmentRuleError) as exc_info:
            build_filter(
                {
                    "type": "rule",
                    "field": "in_segment",
                    "comparator": "in",
                    "value": ["loop"],
                },
                segment_resolver=resolver,
            )
    assert "cycle" in str(exc_info.value).lower()


def test_in_segment_without_resolver_raises(session_factory):
    with session_factory() as session:
        _seed(session)
        with pytest.raises(SegmentRuleError):
            build_filter(
                {
                    "type": "rule",
                    "field": "in_segment",
                    "comparator": "in",
                    "value": ["x"],
                }
            )


def test_in_segment_unknown_id_matches_nothing(session_factory):
    """`in_segment = [unknown]` should add a dead clause that filters
    nothing in (otherwise empty OR'd would behave as 'match everything'
    via the engine's AND-empty rule)."""
    with session_factory() as session:
        seeded = _seed(session)
        condition = build_filter(
            {
                "type": "rule",
                "field": "in_segment",
                "comparator": "in",
                "value": ["does-not-exist"],
            },
            segment_resolver=lambda sid, visited: None,
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert matched == []
        _ = seeded


# ---------------------------------------------------------------------------
# External-date fields wire up like any other date column
# ---------------------------------------------------------------------------


def test_created_at_external_date_filter(session_factory):
    from datetime import UTC, datetime

    with session_factory() as session:
        seeded = _seed(session)
        seeded["ana"].created_at_external = datetime(2025, 3, 1, tzinfo=UTC)
        seeded["carla"].created_at_external = datetime(2025, 9, 1, tzinfo=UTC)
        session.commit()
        condition = build_filter(
            {
                "type": "rule",
                "field": "created_at_external",
                "comparator": "before",
                "value": "2025-06-01T00:00:00+00:00",
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["ana"].id}


def test_ends_with_string_operator(session_factory):
    """Fase 4 — extended string operators include `ends_with`."""
    with session_factory() as session:
        seeded = _seed(session)
        condition = build_filter(
            {
                "type": "rule",
                "field": "email",
                "comparator": "ends_with",
                "value": "@example.com",
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {
            seeded["ana"].id,
            seeded["boris"].id,
            seeded["carla"].id,
        }


def test_not_in_enum_operator(session_factory):
    """Fase 4 — `not_in` on enum fields (commercial_status,
    marketing_consent) lets the operator carve out cohorts."""
    with session_factory() as session:
        seeded = _seed(session)
        condition = build_filter(
            {
                "type": "rule",
                "field": "commercial_status",
                "comparator": "not_in",
                "value": ["qualified"],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["boris"].id}


def test_older_than_n_days_operator(session_factory):
    with session_factory() as session:
        seeded = _seed(session)
        seeded["ana"].created_at_external = datetime.now(UTC) - timedelta(days=90)
        seeded["boris"].created_at_external = datetime.now(UTC) - timedelta(days=5)
        seeded["carla"].created_at_external = datetime.now(UTC) - timedelta(days=120)
        session.commit()
        condition = build_filter(
            {
                "type": "rule",
                "field": "created_at_external",
                "comparator": "older_than_n_days",
                "value": 60,
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["ana"].id, seeded["carla"].id}


def test_first_name_field_with_string_operators(session_factory):
    """Fase 4 — first_name + last_name now expose the full string
    operator set (the legacy `name` concat field stays for
    backward compat)."""
    with session_factory() as session:
        seeded = _seed(session)
        condition = build_filter(
            {
                "type": "rule",
                "field": "first_name",
                "comparator": "starts_with",
                "value": "C",
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["carla"].id}


def test_address_country_contains_operator(session_factory):
    """The country field now accepts substring matches in addition
    to exact ISO code matches."""
    with session_factory() as session:
        seeded = _seed(session)
        condition = build_filter(
            {
                "type": "rule",
                "field": "address_country",
                "comparator": "contains",
                "value": "F",
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        assert _ids(matched) == {seeded["boris"].id}


def test_tag_contains_none_with_two_tags(session_factory):
    """`contains_none` with two tag_ids excludes any contact that
    carries either of them — not just contacts carrying both."""
    with session_factory() as session:
        seeded = _seed(session)
        vip_id = session.scalar(select(Tag.id).where(Tag.name_normalized == "vip"))
        cold_id = session.scalar(select(Tag.id).where(Tag.name_normalized == "cold"))
        condition = build_filter(
            {
                "type": "rule",
                "field": "tags",
                "comparator": "contains_none",
                "value": [vip_id, cold_id],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        # Ana + Carla carry VIP, Boris carries Cold → none survive
        # the two-tag exclusion.
        assert _ids(matched) == set()


def test_tag_contains_none_combined_with_and(session_factory):
    """The exclusion intersects cleanly with another rule under AND."""
    with session_factory() as session:
        seeded = _seed(session)
        vip_id = session.scalar(select(Tag.id).where(Tag.name_normalized == "vip"))
        condition = build_filter(
            {
                "operator": "AND",
                "children": [
                    {
                        "type": "rule",
                        "field": "tags",
                        "comparator": "contains_none",
                        "value": [vip_id],
                    },
                    {
                        "type": "rule",
                        "field": "marketing_consent",
                        "comparator": "eq",
                        "value": "denied",
                    },
                ],
            }
        )
        matched = list(session.scalars(select(Contact).where(condition)))
        # Boris has denied marketing AND no VIP tag (he's Cold).
        assert _ids(matched) == {seeded["boris"].id}
