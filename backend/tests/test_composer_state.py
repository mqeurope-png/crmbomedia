"""Smoke tests for the embed-facing `/state` + `/backups` endpoints.

These cover the monolithic surface the embedded Bomedia Composer
(under `frontend/public/composer/`) talks to via its CRM-adapted
`app-supabase.jsx`. They do NOT re-test the granular surface in
`router.py` — those have their own coverage in `test_composer.py`.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.composer.models import ComposerBrand, ComposerProduct, ComposerTemplate
from app.db.session import get_session
from app.main import app
from app.models.crm import Base, User, UserRole
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
    with factory() as seed:
        seed_test_users(seed)
        _seed_catalog(seed)
    yield factory
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


def _seed_catalog(session: Session) -> None:
    """Two brands + one product so the embed's resolver has something
    to fold against its built-in defaults."""
    now = datetime.now(UTC)
    session.add_all(
        [
            ComposerBrand(
                id="artisjet",
                type="brand",
                label="artisJet",
                color="#2563eb",
                visible=True,
                sort_order=1,
                logo="https://example/logos/artisjet.png",
                logo_text="artisJet",
                # url + urlLabel are stashed inside i18n_json so the
                # embed picks them up (matches the seed-script convention).
                i18n_json=(
                    '{"url":{"es":"https://boprint.net"},'
                    '"urlLabel":{"es":"boprint.net →"}}'
                ),
                created_at=now,
                updated_at=now,
            ),
            ComposerBrand(
                id="mbo",
                type="brand",
                label="MBO UV-LED",
                color="#7c3aed",
                visible=True,
                sort_order=2,
                logo="https://example/logos/mbo.png",
                logo_text="MBO",
                created_at=now,
                updated_at=now,
            ),
            ComposerProduct(
                id="young",
                brand_id="artisjet",
                name="artisJet Young",
                img="https://example/products/young.png",
                description="Compacta",
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


# ───────────────────────────────────────────────────────────────────
# GET /state
# ───────────────────────────────────────────────────────────────────


def test_get_state_returns_embed_shape(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    response = client.get("/api/composer/state", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    # Top-level keys the embed reads.
    for key in (
        "brands",
        "products",
        "prewrittenTexts",
        "composedBlocks",
        "standaloneBlocks",
        "templates",
        "uploadedImages",
        "users",
        "openaiKey",
        "activityLog",
    ):
        assert key in body, f"missing {key}"

    # Catalog rows came out in camelCase.
    artisjet = next(b for b in body["brands"] if b["id"] == "artisjet")
    assert artisjet["label"] == "artisJet"
    assert artisjet["logoText"] == "artisJet"
    # url + urlLabel were re-exposed at the top level so the embed
    # reads b.url[lang] / b.urlLabel[lang].
    assert artisjet["url"] == {"es": "https://boprint.net"}
    assert artisjet["urlLabel"] == {"es": "boprint.net →"}

    young = next(p for p in body["products"] if p["id"] == "young")
    assert young["brand"] == "artisjet"
    assert young["desc"] == "Compacta"


def test_get_state_synthetic_user_carries_role(client: TestClient) -> None:
    headers = auth_headers(client, role="manager")
    body = client.get("/api/composer/state", headers=headers).json()
    assert len(body["users"]) == 1
    assert body["users"][0]["role"] == "manager"


def test_get_state_openai_key_admin_only(client: TestClient) -> None:
    # First an admin writes the key.
    admin_headers = auth_headers(client, role="admin")
    put = client.put(
        "/api/composer/state",
        json={"openaiKey": "sk-test-1234"},
        headers=admin_headers,
    )
    assert put.status_code == 200

    # Admin reads it back.
    admin_body = client.get("/api/composer/state", headers=admin_headers).json()
    assert admin_body["openaiKey"] == "sk-test-1234"

    # Non-admin never sees it.
    user_body = client.get(
        "/api/composer/state", headers=auth_headers(client, role="user")
    ).json()
    assert user_body["openaiKey"] == ""


def test_get_state_viewer_403(client: TestClient) -> None:
    response = client.get(
        "/api/composer/state", headers=auth_headers(client, role="viewer")
    )
    assert response.status_code == 403


# ───────────────────────────────────────────────────────────────────
# PUT /state
# ───────────────────────────────────────────────────────────────────


def test_put_state_creates_template_owned_by_caller(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    payload = {
        "templates": [
            {
                "id": "tpl-test-1",
                "name": "Mi plantilla",
                "desc": "demo",
                "blocks": [],
                "compositorBlocks": [{"type": "text", "text": "hola"}],
                "isGlobal": True,  # ignored — only admin can set is_global
            }
        ]
    }
    response = client.put("/api/composer/state", json=payload, headers=headers)
    assert response.status_code == 200

    user_id = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        tpl = session.get(ComposerTemplate, "tpl-test-1")
        assert tpl is not None
        assert tpl.owner_user_id == user_id
        assert tpl.is_global is False  # user can't force is_global
        assert tpl.compositor_blocks_json is not None


def test_put_state_catalog_admin_or_manager_only(
    client: TestClient, session_factory: sessionmaker
) -> None:
    # Non-privileged users can't update catalog brands.
    user_headers = auth_headers(client, role="user")
    user_put = client.put(
        "/api/composer/state",
        json={
            "brands": [
                {
                    "id": "newbrand",
                    "label": "New",
                    "color": "#000",
                }
            ]
        },
        headers=user_headers,
    )
    # 200 because the request is silently ignored — only the catalog
    # write is gated, the call itself isn't refused (matches the
    # embed's optimistic write pattern).
    assert user_put.status_code == 200
    with session_factory() as session:
        assert session.get(ComposerBrand, "newbrand") is None

    # Manager succeeds.
    manager_headers = auth_headers(client, role="manager")
    manager_put = client.put(
        "/api/composer/state",
        json={
            "brands": [
                {
                    "id": "newbrand",
                    "label": "New",
                    "color": "#000",
                }
            ]
        },
        headers=manager_headers,
    )
    assert manager_put.status_code == 200
    with session_factory() as session:
        brand = session.get(ComposerBrand, "newbrand")
        assert brand is not None
        assert brand.label == "New"


def test_put_state_logs_activity(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    client.put("/api/composer/state", json={"templates": []}, headers=headers)
    from app.composer.models import ComposerActivityLog  # noqa: PLC0415

    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ComposerActivityLog).where(
                    ComposerActivityLog.action == "state.put"
                )
            )
        )
        assert len(rows) == 1


# ───────────────────────────────────────────────────────────────────
# Backups stubs
# ───────────────────────────────────────────────────────────────────


def test_backups_stubs_respond(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    assert client.get("/api/composer/backups", headers=headers).json() == []
    assert (
        client.post(
            "/api/composer/backups",
            json={"data": {}, "reason": "smoke"},
            headers=headers,
        ).status_code
        == 201
    )
    assert (
        client.delete("/api/composer/backups?keep=20", headers=headers).status_code
        == 200
    )
    # GET /{id} 404 until real persistence ships.
    response = client.get("/api/composer/backups/anything", headers=headers)
    assert response.status_code == 404


def _user_id(session_factory: sessionmaker, role: UserRole) -> str:
    with session_factory() as session:
        return session.scalar(select(User.id).where(User.role == role))
