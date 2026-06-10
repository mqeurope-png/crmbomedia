"""CRM-context builder for the segment AI prompt.

Verifies that the block injected into Claude's system prompt actually
reflects the data sitting in the operator's DB: real tag ids, real
integration accounts, real countries, real pipelines + stages, and
the live `lead_score` range. The router has its own tests; this file
focuses on the builder in isolation so a future refactor doesn't drop
a category silently.
"""
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.crm import (
    Base,
    Contact,
    ContactTag,
    ExternalSystem,
    Pipeline,
    PipelineStage,
    Tag,
    User,
    UserRole,
)
from app.models.integration_settings import IntegrationAccount
from app.services.segments import ai_context


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    ai_context.reset_cache()
    with factory() as session:
        yield session
    ai_context.reset_cache()
    Base.metadata.drop_all(engine)


def _seed_owner(session: Session) -> User:
    owner = User(
        email="owner@example.com",
        full_name="Owner",
        password_hash="x",
        role=UserRole.MANAGER,
        is_active=True,
    )
    session.add(owner)
    session.flush()
    return owner


def test_context_lists_top_tags_with_ids_and_names(session: Session):
    """The model has to receive each tag's actual id so the rules it
    proposes match the schema the engine expects."""
    vip = Tag(name="VIP", name_normalized="vip", color="#ef4444")
    mbo = Tag(name="formMBO", name_normalized="formmbo")
    rare = Tag(name="rare", name_normalized="rare")
    session.add_all([vip, mbo, rare])
    session.flush()
    ana = Contact(first_name="Ana", email="a@e.com")
    boris = Contact(first_name="Boris", email="b@e.com")
    session.add_all([ana, boris])
    session.flush()
    # Make `formMBO` the most-used tag so a hypothetical truncation
    # would keep it.
    session.add_all(
        [
            ContactTag(contact_id=ana.id, tag_id=mbo.id, source="manual"),
            ContactTag(contact_id=boris.id, tag_id=mbo.id, source="manual"),
            ContactTag(contact_id=ana.id, tag_id=vip.id, source="manual"),
        ]
    )
    session.commit()

    text = ai_context.build_crm_context(session)
    assert "TAGS DISPONIBLES" in text
    assert mbo.id in text
    assert "formMBO" in text
    assert vip.id in text
    # Tags appear in usage-descending order — formMBO (2 contacts)
    # must be listed before VIP (1 contact) and `rare` (0 contacts).
    assert text.index("formMBO") < text.index("VIP") < text.index("rare")


def test_context_lists_enabled_integration_accounts_only(session: Session):
    """Pause an account → it must disappear from the block; otherwise
    the model would propose origin_account_id values the operator's
    workflow doesn't even hit."""
    session.add_all(
        [
            IntegrationAccount(
                system=ExternalSystem.AGILECRM,
                account_id="default",
                display_name="AgileCRM cuenta principal",
                enabled=True,
            ),
            IntegrationAccount(
                system=ExternalSystem.AGILECRM,
                account_id="es",
                display_name="AgileCRM España",
                enabled=True,
            ),
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="paused-bv",
                display_name="Brevo (paused)",
                enabled=False,
            ),
        ]
    )
    session.commit()

    text = ai_context.build_crm_context(session)
    assert "CUENTAS DE INTEGRACIÓN" in text
    assert "AgileCRM España" in text
    assert "account_id=\"default\"" in text
    assert "paused-bv" not in text


def test_context_lists_distinct_countries(session: Session):
    session.add_all(
        [
            Contact(first_name="Ana", email="a@e.com", address_country="ES"),
            Contact(first_name="Bru", email="b@e.com", address_country="ES"),
            Contact(first_name="Cor", email="c@e.com", address_country="MX"),
            Contact(first_name="Dan", email="d@e.com", address_country=None),
            Contact(first_name="Eli", email="e@e.com", address_country=""),
        ]
    )
    session.commit()
    text = ai_context.build_crm_context(session)
    assert "PAÍSES PRESENTES" in text
    assert "ES" in text
    assert "MX" in text


def test_context_lists_pipelines_with_stages(session: Session):
    owner = _seed_owner(session)
    pipeline = Pipeline(
        name="Ventas B2B",
        owner_user_id=owner.id,
        is_active=True,
        is_shared=True,
    )
    session.add(pipeline)
    session.flush()
    new_lead = PipelineStage(
        pipeline_id=pipeline.id, name="Nuevo lead", position=0
    )
    won = PipelineStage(
        pipeline_id=pipeline.id, name="Ganado", position=1, is_won=True
    )
    session.add_all([new_lead, won])
    session.commit()

    text = ai_context.build_crm_context(session)
    assert "Pipeline \"Ventas B2B\"" in text
    assert "Nuevo lead" in text
    assert pipeline.id in text
    assert new_lead.id in text


def test_context_includes_lead_score_range(session: Session):
    session.add_all(
        [
            Contact(first_name="Ana", email="a@e.com", lead_score=-10),
            Contact(first_name="Bru", email="b@e.com", lead_score=95),
            Contact(first_name="Cor", email="c@e.com", lead_score=42),
        ]
    )
    session.commit()
    text = ai_context.build_crm_context(session)
    assert "RANGO LEAD SCORE" in text
    assert "-10" in text and "95" in text


def test_context_handles_empty_db(session: Session):
    """Brand-new tenant with no contacts must not crash — Claude just
    sees explicit `ninguna`/`ninguno` so it doesn't hallucinate ids."""
    text = ai_context.build_crm_context(session)
    assert "ninguna" in text.lower() or "ninguno" in text.lower()


def test_context_is_cached(session: Session, monkeypatch):
    """A second call inside the TTL must NOT touch the DB. We swap the
    loader after the first call to detect a re-query — if the cache
    layer were broken the second call would raise."""
    session.add(
        IntegrationAccount(
            system=ExternalSystem.AGILECRM,
            account_id="default",
            display_name="AgileCRM",
            enabled=True,
        )
    )
    session.commit()

    first = ai_context.build_crm_context(session)
    assert "AgileCRM" in first

    def boom(_):
        raise AssertionError("loader called twice within TTL")

    monkeypatch.setattr(ai_context, "_load_integration_accounts", boom)
    second = ai_context.build_crm_context(session)
    assert second == first


def test_context_cache_reset_helper_works(session: Session):
    first = ai_context.build_crm_context(session)
    ai_context.reset_cache()
    # Add a tag AFTER first build — the new value must show up only if
    # the cache truly got cleared.
    session.add(Tag(name="late", name_normalized="late"))
    session.commit()
    second = ai_context.build_crm_context(session)
    assert "late" in second
    assert second != first
