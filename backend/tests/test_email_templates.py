"""Sprint Email v2.2 — backend smoke tests."""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.email_templates.models import EmailTemplate
from app.email_templates.services import extract_text_from_html
from app.main import app
from app.models.crm import Base
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


# ───────────────────────────────────────────────────────────────────
# Service helpers
# ───────────────────────────────────────────────────────────────────


def test_extract_text_strips_tags_and_entities() -> None:
    out = extract_text_from_html(
        "<p>Hola <strong>mundo</strong>&nbsp;&amp; saludos</p>"
    )
    assert out == "Hola mundo & saludos"


def test_extract_text_handles_none_and_empty() -> None:
    assert extract_text_from_html(None) is None
    assert extract_text_from_html("") is None
    assert extract_text_from_html("<p></p>") is None


# ───────────────────────────────────────────────────────────────────
# Templates CRUD
# ───────────────────────────────────────────────────────────────────


def test_create_template_owned_by_caller(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    response = client.post(
        "/api/email-templates",
        json={
            "name": "Bienvenida nuevo cliente",
            "subject": "¡Bienvenido!",
            "body_html": "<p>Hola {nombre}</p>",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Bienvenida nuevo cliente"
    assert body["body_text"] == "Hola {nombre}"
    assert body["is_global"] is False
    assert body["owner_user_id"] is not None
    with session_factory() as session:
        row = session.get(EmailTemplate, body["id"])
        assert row is not None
        assert row.usage_count == 0


def test_non_admin_cannot_create_global(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    response = client.post(
        "/api/email-templates",
        json={"name": "X", "body_html": "<p>x</p>", "is_global": True},
        headers=headers,
    )
    assert response.status_code == 403


def test_admin_can_create_global(client: TestClient) -> None:
    headers = auth_headers(client, role="admin")
    response = client.post(
        "/api/email-templates",
        json={"name": "Global", "body_html": "<p>x</p>", "is_global": True},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["is_global"] is True


def test_list_filters_by_folder_and_q(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    # Create folder + 2 templates.
    folder = client.post(
        "/api/email-template-folders",
        json={"name": "Comerciales"},
        headers=headers,
    ).json()
    folder_id = folder["id"]
    client.post(
        "/api/email-templates",
        json={
            "name": "Apertura cliente",
            "body_html": "<p>a</p>",
            "folder_id": folder_id,
        },
        headers=headers,
    )
    client.post(
        "/api/email-templates",
        json={"name": "Recordatorio", "body_html": "<p>b</p>"},
        headers=headers,
    )
    # Filter by folder.
    in_folder = client.get(
        f"/api/email-templates?folder_id={folder_id}", headers=headers
    )
    assert in_folder.status_code == 200
    rows = in_folder.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "Apertura cliente"

    # Filter by q.
    by_q = client.get("/api/email-templates?q=Record", headers=headers)
    assert by_q.status_code == 200
    assert len(by_q.json()) == 1


def test_owner_can_edit_own_template(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    created = client.post(
        "/api/email-templates",
        json={"name": "X", "body_html": "<p>x</p>"},
        headers=headers,
    ).json()
    updated = client.put(
        f"/api/email-templates/{created['id']}",
        json={"name": "X edited", "body_html": "<p>updated</p>"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "X edited"
    assert updated.json()["body_text"] == "updated"


def test_non_owner_cannot_edit(client: TestClient) -> None:
    user_headers = auth_headers(client, role="user")
    created = client.post(
        "/api/email-templates",
        json={"name": "X", "body_html": "<p>x</p>"},
        headers=user_headers,
    ).json()
    manager_headers = auth_headers(client, role="manager")
    update = client.put(
        f"/api/email-templates/{created['id']}",
        json={"name": "Stolen", "body_html": "<p>theft</p>"},
        headers=manager_headers,
    )
    assert update.status_code == 403


def test_use_increments_usage_count(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    created = client.post(
        "/api/email-templates",
        json={"name": "X", "body_html": "<p>x</p>"},
        headers=headers,
    ).json()
    r1 = client.post(
        f"/api/email-templates/{created['id']}/use", headers=headers
    )
    r2 = client.post(
        f"/api/email-templates/{created['id']}/use", headers=headers
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["usage_count"] == 2
    with session_factory() as session:
        row = session.get(EmailTemplate, created["id"])
        assert row is not None
        assert row.last_used_at is not None


# ───────────────────────────────────────────────────────────────────
# Folders CRUD + tree
# ───────────────────────────────────────────────────────────────────


def test_create_folder_and_tree(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    parent = client.post(
        "/api/email-template-folders",
        json={"name": "Comerciales"},
        headers=headers,
    ).json()
    child = client.post(
        "/api/email-template-folders",
        json={"name": "Bart", "parent_folder_id": parent["id"]},
        headers=headers,
    ).json()
    assert child["parent_folder_id"] == parent["id"]
    tree = client.get("/api/email-template-folders", headers=headers).json()
    assert len(tree) == 1
    assert tree[0]["id"] == parent["id"]
    assert tree[0]["children"][0]["id"] == child["id"]


def test_folder_depth_enforced(client: TestClient) -> None:
    headers = auth_headers(client, role="admin")
    f1 = client.post(
        "/api/email-template-folders", json={"name": "L1"}, headers=headers
    ).json()
    f2 = client.post(
        "/api/email-template-folders",
        json={"name": "L2", "parent_folder_id": f1["id"]},
        headers=headers,
    ).json()
    f3 = client.post(
        "/api/email-template-folders",
        json={"name": "L3", "parent_folder_id": f2["id"]},
        headers=headers,
    )
    # 3 levels OK
    assert f3.status_code == 201
    # 4 levels rejected
    f4 = client.post(
        "/api/email-template-folders",
        json={"name": "L4", "parent_folder_id": f3.json()["id"]},
        headers=headers,
    )
    assert f4.status_code == 400


def test_folder_cycle_rejected(client: TestClient) -> None:
    headers = auth_headers(client, role="admin")
    parent = client.post(
        "/api/email-template-folders", json={"name": "P"}, headers=headers
    ).json()
    child = client.post(
        "/api/email-template-folders",
        json={"name": "C", "parent_folder_id": parent["id"]},
        headers=headers,
    ).json()
    # Try to make parent a child of child → cycle.
    update = client.put(
        f"/api/email-template-folders/{parent['id']}",
        json={"name": "P", "parent_folder_id": child["id"]},
        headers=headers,
    )
    assert update.status_code == 400


def test_delete_folder_nulls_template_folder_id(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    folder = client.post(
        "/api/email-template-folders",
        json={"name": "ToDelete"},
        headers=headers,
    ).json()
    tpl = client.post(
        "/api/email-templates",
        json={
            "name": "Inside folder",
            "body_html": "<p>x</p>",
            "folder_id": folder["id"],
        },
        headers=headers,
    ).json()
    delete = client.delete(
        f"/api/email-template-folders/{folder['id']}", headers=headers
    )
    assert delete.status_code == 200
    with session_factory() as session:
        row = session.get(EmailTemplate, tpl["id"])
        assert row is not None
        assert row.folder_id is None


# ───────────────────────────────────────────────────────────────────
# Picker
# ───────────────────────────────────────────────────────────────────


def test_picker_returns_combined_view(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    client.post(
        "/api/email-templates",
        json={"name": "T1", "body_html": "<p>1</p>"},
        headers=headers,
    )
    response = client.get("/api/emails/templates-picker", headers=headers)
    assert response.status_code == 200
    body = response.json()
    for key in ("crm", "brevo", "folders", "recent"):
        assert key in body
    assert len(body["crm"]) >= 1


def test_recent_orders_by_last_used(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, role="user")
    a = client.post(
        "/api/email-templates",
        json={"name": "A", "body_html": "<p>a</p>"},
        headers=headers,
    ).json()
    b = client.post(
        "/api/email-templates",
        json={"name": "B", "body_html": "<p>b</p>"},
        headers=headers,
    ).json()
    # Mark B used first, then A used. Recent should rank A first.
    client.post(f"/api/email-templates/{b['id']}/use", headers=headers)
    client.post(f"/api/email-templates/{a['id']}/use", headers=headers)
    response = client.get("/api/emails/templates-picker", headers=headers)
    assert response.status_code == 200
    recent_ids = [r["id"] for r in response.json()["recent"]]
    assert recent_ids[0] == a["id"]


def test_extract_text_strips_style_and_script_block_contents() -> None:
    """Bart's verified bug: TinyMCE-authored sends shipped CSS reset
    boilerplate inside `<style>`, and our snippet preview rendered
    raw CSS source instead of the operator's words."""
    html = (
        "<p></p>"
        "<style>body,table,td,p,a{margin:0;padding:0}img{border:0}</style>"
        "<script>alert('x')</script>"
        "<!--[if mso]><b>outlook</b><![endif]-->"
        "<p>Hola Eduard, confirmo nuestra cita para mañana a las 10h.</p>"
    )
    assert (
        extract_text_from_html(html)
        == "Hola Eduard, confirmo nuestra cita para mañana a las 10h."
    )
