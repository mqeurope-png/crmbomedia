"""Smoke tests for Sprint Composer Fase 1.

Exercises the surface that actually ships in Fase 1:
- Catalog read filters out hidden items.
- Viewer role hits 403 across the module.
- Template create / list / update / delete round-trip + revisions
  FIFO trim.
- Drafts upsert + read are scoped per user.
- Asset upload deduplicates by sha256.
- Admin-only settings endpoint encrypts the OpenAI key.
- AI proxy stubs return 503 until the key is configured.
"""
from __future__ import annotations

import io
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.composer.models import (
    ComposerBrand,
    ComposerProduct,
    ComposerTemplateRevision,
    ComposerUserHiddenItem,
)
from app.composer.services import MAX_REVISIONS_PER_TEMPLATE
from app.db.session import get_session
from app.main import app
from app.models.crm import Base, User, UserRole
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def session_factory(tmp_path: Path) -> Generator[sessionmaker, None, None]:
    """In-memory SQLite + Composer asset root pointed at tmp_path so
    uploads don't escape the test sandbox."""
    from app.composer import router as composer_router  # noqa: PLC0415

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        _seed_minimal_catalog(seed)

    original_root = composer_router.ASSET_ROOT
    composer_router.ASSET_ROOT = tmp_path / "uploads"
    try:
        yield factory
    finally:
        composer_router.ASSET_ROOT = original_root
        Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_minimal_catalog(session: Session) -> None:
    """Just enough rows for the catalog endpoint to return
    something interesting and for hide-filter assertions to bite."""
    now = datetime.now(UTC)
    session.add_all(
        [
            ComposerBrand(
                id="mbo",
                type="brand",
                label="MBO",
                color="#000",
                visible=True,
                sort_order=1,
                i18n_json="{}",
                created_at=now,
                updated_at=now,
            ),
            ComposerBrand(
                id="hidden-brand",
                type="brand",
                label="Hidden",
                color="#fff",
                visible=True,
                sort_order=2,
                i18n_json="{}",
                created_at=now,
                updated_at=now,
            ),
            ComposerProduct(
                id="p1",
                brand_id="mbo",
                name="Producto 1",
                img="https://example/p1.png",
                visible=True,
                sort_order=1,
                tags="[]",
                i18n_json="{}",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    session.commit()


def _user_id(session_factory: sessionmaker, role: UserRole) -> str:
    with session_factory() as session:
        return session.scalar(select(User.id).where(User.role == role))


# ---------------------------------------------------------------------------
# Catalog + role gating
# ---------------------------------------------------------------------------


def test_catalog_returns_seeded_rows(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    response = client.get("/api/composer/catalog", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {b["id"] for b in body["brands"]} == {"mbo", "hidden-brand"}
    assert [p["id"] for p in body["products"]] == ["p1"]


def test_catalog_hides_user_specific_items(
    client: TestClient, session_factory: sessionmaker
) -> None:
    user_id = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        session.add(
            ComposerUserHiddenItem(
                user_id=user_id, collection="brands", item_id="hidden-brand"
            )
        )
        session.commit()
    headers = auth_headers(client, role="user")
    response = client.get("/api/composer/catalog", headers=headers)
    assert response.status_code == 200, response.text
    ids = {b["id"] for b in response.json()["brands"]}
    assert "hidden-brand" not in ids
    assert "mbo" in ids


def test_viewer_blocked_from_composer(client: TestClient) -> None:
    headers = auth_headers(client, role="viewer")
    response = client.get("/api/composer/catalog", headers=headers)
    assert response.status_code == 403


def test_unauthenticated_requests_rejected(client: TestClient) -> None:
    response = client.get("/api/composer/catalog")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def test_template_round_trip_creates_revision(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    payload = {
        "name": "Mi plantilla",
        "description": "demo",
        "blocks": ["b1", "b2"],
        "is_global": False,
    }
    create = client.post("/api/composer/templates", json=payload, headers=headers)
    assert create.status_code == 201, create.text
    tpl_id = create.json()["id"]

    listed = client.get("/api/composer/templates", headers=headers)
    assert listed.status_code == 200
    assert any(t["id"] == tpl_id for t in listed.json())

    update = client.put(
        f"/api/composer/templates/{tpl_id}",
        json={**payload, "name": "Renombrada", "blocks": ["b1"]},
        headers=headers,
    )
    assert update.status_code == 200
    assert update.json()["name"] == "Renombrada"

    revisions = client.get(
        f"/api/composer/templates/{tpl_id}/revisions", headers=headers
    )
    assert revisions.status_code == 200
    rows = revisions.json()
    # create + update => 2 revisions
    assert len(rows) == 2


def test_template_revisions_fifo_trimmed(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    create = client.post(
        "/api/composer/templates",
        json={"name": "Plantilla FIFO", "blocks": ["b"], "is_global": False},
        headers=headers,
    )
    tpl_id = create.json()["id"]

    # 1 revision exists already; bump up to MAX + 5.
    for i in range(MAX_REVISIONS_PER_TEMPLATE + 5):
        update = client.put(
            f"/api/composer/templates/{tpl_id}",
            json={
                "name": f"v{i}",
                "blocks": [f"b{i}"],
                "is_global": False,
            },
            headers=headers,
        )
        assert update.status_code == 200

    with session_factory() as session:
        total = session.scalar(
            select(ComposerTemplateRevision.id)
            .where(ComposerTemplateRevision.template_id == tpl_id)
            .order_by(ComposerTemplateRevision.created_at.desc())
        )
        assert total is not None
        rows = list(
            session.scalars(
                select(ComposerTemplateRevision).where(
                    ComposerTemplateRevision.template_id == tpl_id
                )
            )
        )
    assert len(rows) == MAX_REVISIONS_PER_TEMPLATE


def test_non_owner_user_cannot_edit_template(
    client: TestClient, session_factory: sessionmaker
) -> None:
    user_headers = auth_headers(client, role="user")
    create = client.post(
        "/api/composer/templates",
        json={"name": "owned", "blocks": [], "is_global": False},
        headers=user_headers,
    )
    tpl_id = create.json()["id"]

    other_headers = auth_headers(client, role="manager")
    update = client.put(
        f"/api/composer/templates/{tpl_id}",
        json={"name": "robada", "blocks": [], "is_global": False},
        headers=other_headers,
    )
    assert update.status_code == 403


def test_admin_can_edit_any_template(client: TestClient) -> None:
    user_headers = auth_headers(client, role="user")
    create = client.post(
        "/api/composer/templates",
        json={"name": "owned", "blocks": [], "is_global": False},
        headers=user_headers,
    )
    tpl_id = create.json()["id"]

    admin_headers = auth_headers(client, role="admin")
    update = client.put(
        f"/api/composer/templates/{tpl_id}",
        json={"name": "intervenida", "blocks": [], "is_global": True},
        headers=admin_headers,
    )
    assert update.status_code == 200
    assert update.json()["is_global"] is True


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


def test_draft_upsert_is_per_user(client: TestClient) -> None:
    user_headers = auth_headers(client, role="user")
    manager_headers = auth_headers(client, role="manager")

    user_put = client.put(
        "/api/composer/drafts",
        json={"state": {"canvas": "user"}},
        headers=user_headers,
    )
    assert user_put.status_code == 200
    manager_put = client.put(
        "/api/composer/drafts",
        json={"state": {"canvas": "manager"}},
        headers=manager_headers,
    )
    assert manager_put.status_code == 200

    user_get = client.get("/api/composer/drafts", headers=user_headers)
    assert user_get.status_code == 200
    assert user_get.json()["state"] == {"canvas": "user"}

    manager_get = client.get("/api/composer/drafts", headers=manager_headers)
    assert manager_get.json()["state"] == {"canvas": "manager"}


def test_draft_returns_empty_state_when_missing(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    response = client.get("/api/composer/drafts", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"state": {}, "updated_at": None}


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


def _png_bytes() -> bytes:
    """Opaque payload — the router doesn't sniff PNG contents, it
    trusts the multipart `Content-Type` for the MIME gate."""
    return b"\x89PNG\r\n\x1a\nfake-but-with-the-right-magic-prefix"


def test_asset_upload_dedupes_by_sha256(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    body = _png_bytes()
    first = client.post(
        "/api/composer/assets",
        headers=headers,
        files={"file": ("a.png", io.BytesIO(body), "image/png")},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/composer/assets",
        headers=headers,
        files={"file": ("a-copy.png", io.BytesIO(body), "image/png")},
    )
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_asset_upload_rejects_unsupported_mime(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    response = client.post(
        "/api/composer/assets",
        headers=headers,
        files={"file": ("evil.exe", io.BytesIO(b"MZ\x90\x00"), "application/x-msdownload")},
    )
    assert response.status_code == 400


def test_asset_list_returns_only_own(client: TestClient) -> None:
    user_headers = auth_headers(client, role="user")
    client.post(
        "/api/composer/assets",
        headers=user_headers,
        files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    manager_headers = auth_headers(client, role="manager")
    response = client.get("/api/composer/assets", headers=manager_headers)
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Settings + AI stubs
# ---------------------------------------------------------------------------


def test_settings_admin_only(client: TestClient) -> None:
    user_headers = auth_headers(client, role="user")
    response = client.get("/api/composer/settings", headers=user_headers)
    assert response.status_code == 403


def test_settings_round_trip_never_leaks_plaintext(
    client: TestClient, session_factory: sessionmaker
) -> None:
    admin_headers = auth_headers(client, role="admin")
    put = client.put(
        "/api/composer/settings",
        json={"openai_api_key": "sk-supersecret", "agent_system_prompt": "hola"},
        headers=admin_headers,
    )
    assert put.status_code == 200
    body = put.json()
    assert body["openai_configured"] is True
    assert "sk-supersecret" not in put.text

    # Stored value is encrypted, not plaintext.
    from app.composer.models import ComposerSettings  # noqa: PLC0415

    with session_factory() as session:
        row = session.get(ComposerSettings, 1)
        assert row is not None
        assert row.openai_api_key_encrypted not in {None, "sk-supersecret"}


def test_ai_stubs_return_503_without_key(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    for path in ("/api/composer/ai/agent/run", "/api/composer/ai/rewrite", "/api/composer/ai/translate"):
        response = client.post(path, json={"foo": "bar"}, headers=headers)
        assert response.status_code == 503, f"{path} returned {response.status_code}"


def test_ai_stubs_return_stub_once_key_configured(client: TestClient) -> None:
    admin_headers = auth_headers(client, role="admin")
    client.put(
        "/api/composer/settings",
        json={"openai_api_key": "sk-active"},
        headers=admin_headers,
    )
    user_headers = auth_headers(client, role="user")
    response = client.post(
        "/api/composer/ai/rewrite", json={"text": "hola"}, headers=user_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "stub"
