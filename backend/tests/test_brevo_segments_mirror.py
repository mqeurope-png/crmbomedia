"""Brevo segments mirror — sync, member resolution by email,
unique-email skipping, mirror deletion when remote segment vanishes,
manual refresh endpoint."""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_session
from app.integrations.brevo.segments import sync_brevo_segments
from app.main import app
from app.models.crm import Contact, ExternalSystem, Segment
from app.models.integration_settings import IntegrationAccount
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as session:
        seed_test_users(session)
        session.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo",
                enabled=True,
            )
        )
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class _FakeClient:
    """Replays prepared Brevo responses for segments + their members."""

    segments: list[dict[str, Any]] = []
    members_by_segment: dict[int, list[str]] = {}

    def __init__(self, session, account_id, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def list_segments(self, *, limit=100, offset=0):
        return {
            "segments": _FakeClient.segments[offset : offset + limit],
            "count": len(_FakeClient.segments),
        }

    async def get_segment_contacts(self, segment_id, *, limit=100, offset=0):
        emails = _FakeClient.members_by_segment.get(int(segment_id), [])
        slice_ = emails[offset : offset + limit]
        return {
            "contacts": [{"email": e} for e in slice_],
            "count": len(emails),
        }


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeClient.segments = []
    _FakeClient.members_by_segment = {}


def _patch_client():
    return patch(
        "app.integrations.brevo.segments.BrevoClient", _FakeClient
    )


def _seed_contacts(session: Session) -> tuple[str, str]:
    ana = Contact(first_name="Ana", email="ana@example.com")
    boris = Contact(first_name="Boris", email="boris@example.com")
    session.add_all([ana, boris])
    session.flush()
    return ana.id, boris.id


def test_creates_mirror_with_resolved_member_ids(session_factory):
    _FakeClient.segments = [{"id": 11, "name": "VIPs"}]
    _FakeClient.members_by_segment = {
        11: ["ana@example.com", "boris@example.com", "stranger@unknown.invalid"],
    }
    with session_factory() as session:
        ana_id, boris_id = _seed_contacts(session)
        session.commit()
        with _patch_client():
            stats = __import__(
                "asyncio"
            ).run(sync_brevo_segments(session, "main"))
        session.commit()
        assert stats["segments_refreshed"] == 1
        assert stats["members_matched"] == 2
        # The unknown email is silently skipped — webhooks NEVER
        # create contacts here.
        assert stats["unknown_skipped"] == 1
        mirror = session.scalar(select(Segment))
        assert mirror.external_source == "brevo:main:11"
        assert mirror.is_dynamic is False
        assert mirror.rules_json is None
        members = sorted(
            __import__("json").loads(mirror.static_contact_ids)
        )
        assert members == sorted([ana_id, boris_id])
        assert mirror.cached_count == 2
        assert mirror.external_last_refreshed_at is not None


def test_second_run_updates_existing_mirror(session_factory):
    _FakeClient.segments = [{"id": 11, "name": "VIPs"}]
    _FakeClient.members_by_segment = {11: ["ana@example.com"]}
    with session_factory() as session:
        _seed_contacts(session)
        session.commit()
        with _patch_client():
            __import__("asyncio").run(sync_brevo_segments(session, "main"))
        session.commit()
        assert session.scalar(
            select(Segment.cached_count).where(
                Segment.external_source == "brevo:main:11"
            )
        ) == 1

        # Brevo adds Boris on the next refresh.
        _FakeClient.members_by_segment = {
            11: ["ana@example.com", "boris@example.com"],
        }
        with _patch_client():
            __import__("asyncio").run(sync_brevo_segments(session, "main"))
        session.commit()
        assert session.scalar(
            select(Segment.cached_count).where(
                Segment.external_source == "brevo:main:11"
            )
        ) == 2


def test_removed_remote_segment_drops_local_mirror(session_factory):
    _FakeClient.segments = [{"id": 11, "name": "VIPs"}, {"id": 12, "name": "Cold"}]
    _FakeClient.members_by_segment = {11: [], 12: []}
    with session_factory() as session:
        _seed_contacts(session)
        session.commit()
        with _patch_client():
            __import__("asyncio").run(sync_brevo_segments(session, "main"))
        session.commit()
        assert session.scalar(
            select(Segment).where(Segment.external_source == "brevo:main:12")
        ) is not None

        # Brevo drops segment 12.
        _FakeClient.segments = [{"id": 11, "name": "VIPs"}]
        with _patch_client():
            stats = __import__("asyncio").run(
                sync_brevo_segments(session, "main")
            )
        session.commit()
        assert stats["segments_removed"] == 1
        assert session.scalar(
            select(Segment).where(Segment.external_source == "brevo:main:12")
        ) is None


def test_manual_refresh_endpoint_enqueues_for_managed_mirror(
    client: TestClient, session_factory
):
    with session_factory() as session:
        _seed_contacts(session)
        # Pre-create a mirror so the endpoint can find it.
        from app.models.crm import User, UserRole

        admin = session.scalar(select(User).where(User.role == UserRole.ADMIN))
        session.add(
            Segment(
                name="VIPs",
                owner_user_id=admin.id,
                external_source="brevo:main:11",
                is_dynamic=False,
                static_contact_ids="[]",
            )
        )
        session.commit()
        seg = session.scalar(
            select(Segment).where(Segment.external_source == "brevo:main:11")
        )
        seg_id = seg.id

    with patch("app.api.brevo.enqueue_sync_job") as fake:
        fake.return_value = ("log-1", "job-1")
        response = client.post(
            f"/api/brevo/segments/{seg_id}/refresh",
            headers=auth_headers(client, "manager"),
        )
    assert response.status_code == 200, response.text
    assert response.json() == {"sync_log_id": "log-1", "job_id": "job-1"}
    # Routed to the correct queue.
    call_kwargs = fake.call_args.kwargs
    assert call_kwargs["operation"] == "refresh_segment"
    assert call_kwargs["account_id"] == "main"
    assert call_kwargs["payload"] == {"segment_id": seg_id}


def test_manual_refresh_endpoint_rejects_non_brevo_segment(
    client: TestClient, session_factory
):
    """Hitting the refresh endpoint with a native (non-mirror) segment
    must 400 — there's nothing to refresh from Brevo."""
    with session_factory() as session:
        from app.models.crm import User, UserRole

        admin = session.scalar(select(User).where(User.role == UserRole.ADMIN))
        session.add(
            Segment(
                name="Native",
                owner_user_id=admin.id,
                is_dynamic=True,
                rules_json='{"type":"rule","field":"is_active","comparator":"eq","value":true}',
            )
        )
        session.commit()
        seg_id = session.scalar(
            select(Segment.id).where(Segment.name == "Native")
        )

    response = client.post(
        f"/api/brevo/segments/{seg_id}/refresh",
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 404


def test_refresh_all_endpoint_enqueues_account_level_sync(client: TestClient):
    with patch("app.api.brevo.enqueue_sync_job") as fake:
        fake.return_value = ("log-2", "job-2")
        response = client.post(
            "/api/brevo/segments/refresh-all?account_id=main",
            headers=auth_headers(client, "manager"),
        )
    assert response.status_code == 200
    assert fake.call_args.kwargs["operation"] == "refresh_segments"
