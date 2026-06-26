"""PR-Workflows-Pipelines-Per-User — tests del feature per-user
para workflows + pipelines + mini-fix de carpeta predeterminada de
plantillas.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)

    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


def _create_workflow(
    client: TestClient,
    *,
    role: str,
    name: str = "Mi seguimiento",
    is_global: bool = False,
) -> dict:
    response = client.post(
        "/api/workflows",
        json={
            "name": name,
            "trigger_type": "contact.created",
            "trigger_config": {},
            "is_global": is_global,
        },
        headers=auth_headers(client, role),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_workflows_list_returns_own_plus_global(client: TestClient):
    """User A ve los suyos + globales. NO ve los de User B."""
    a = _create_workflow(client, role="manager", name="Mio A")
    b = _create_workflow(client, role="user", name="Mio B")
    g = _create_workflow(
        client, role="admin", name="Equipo", is_global=True
    )
    listed_a = client.get(
        "/api/workflows", headers=auth_headers(client, "manager")
    ).json()
    ids_a = {w["id"] for w in listed_a}
    assert a["id"] in ids_a
    assert g["id"] in ids_a
    assert b["id"] not in ids_a
    # is_mine + is_global computados.
    for w in listed_a:
        if w["id"] == a["id"]:
            assert w["is_mine"] is True
            assert w["is_global"] is False
        elif w["id"] == g["id"]:
            assert w["is_mine"] is False
            assert w["is_global"] is True


def test_workflows_create_defaults_to_owner_current_user(client: TestClient):
    """POST sin is_global → owner_user_id = current_user."""
    w = _create_workflow(client, role="user", name="Privado")
    assert w["is_global"] is False
    assert w["is_mine"] is True
    assert w["owner_user_id"] is not None


def test_workflows_create_with_is_global_requires_admin(client: TestClient):
    response = client.post(
        "/api/workflows",
        json={
            "name": "X",
            "trigger_type": "contact.created",
            "trigger_config": {},
            "is_global": True,
        },
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 403
    assert "compartir" in response.json()["detail"].lower() or \
           "admin" in response.json()["detail"].lower()


def test_workflows_patch_requires_owner_or_admin(client: TestClient):
    """Owner edita, otro user → 403, admin → permitido."""
    w = _create_workflow(client, role="manager", name="A")
    # Owner edita OK.
    own_resp = client.put(
        f"/api/workflows/{w['id']}",
        json={"name": "A renamed"},
        headers=auth_headers(client, "manager"),
    )
    assert own_resp.status_code == 200
    # Otro user → 403.
    other_resp = client.put(
        f"/api/workflows/{w['id']}",
        json={"name": "Stolen"},
        headers=auth_headers(client, "user"),
    )
    assert other_resp.status_code == 403
    # Admin → permitido.
    admin_resp = client.put(
        f"/api/workflows/{w['id']}",
        json={"name": "Admin override"},
        headers=auth_headers(client, "admin"),
    )
    assert admin_resp.status_code == 200


def test_workflows_toggle_is_global_requires_admin(client: TestClient):
    w = _create_workflow(client, role="manager", name="A")
    # Owner intenta marcar global → 403.
    own_resp = client.put(
        f"/api/workflows/{w['id']}",
        json={"is_global": True},
        headers=auth_headers(client, "manager"),
    )
    assert own_resp.status_code == 403
    # Admin lo marca global → 200.
    admin_resp = client.put(
        f"/api/workflows/{w['id']}",
        json={"is_global": True},
        headers=auth_headers(client, "admin"),
    )
    assert admin_resp.status_code == 200
    assert admin_resp.json()["is_global"] is True
    assert admin_resp.json()["owner_user_id"] is None


def test_workflows_delete_requires_owner_or_admin(client: TestClient):
    w = _create_workflow(client, role="manager", name="A")
    other = client.delete(
        f"/api/workflows/{w['id']}",
        headers=auth_headers(client, "user"),
    )
    assert other.status_code == 403
    own = client.delete(
        f"/api/workflows/{w['id']}",
        headers=auth_headers(client, "manager"),
    )
    assert own.status_code == 204


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


def _create_pipeline(
    client: TestClient,
    *,
    role: str,
    name: str = "Mio",
    is_global: bool = False,
) -> dict:
    response = client.post(
        "/api/pipelines",
        json={
            "name": name,
            "is_global": is_global,
            "stages": [{"name": "Nuevo", "position": 0}],
        },
        headers=auth_headers(client, role),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_pipelines_list_returns_own_plus_global(client: TestClient):
    a = _create_pipeline(client, role="manager", name="A mio")
    b = _create_pipeline(client, role="user", name="B mio")
    g = _create_pipeline(client, role="admin", name="Equipo", is_global=True)
    listed_a = client.get(
        "/api/pipelines", headers=auth_headers(client, "manager")
    ).json()
    ids_a = {p["id"] for p in listed_a}
    assert a["id"] in ids_a
    assert g["id"] in ids_a
    assert b["id"] not in ids_a


def test_pipelines_create_defaults_to_owner_current_user(client: TestClient):
    p = _create_pipeline(client, role="user", name="Privado")
    assert p["is_global"] is False
    assert p["is_mine"] is True
    assert p["owner_user_id"] is not None


def test_pipelines_create_with_is_global_requires_admin(client: TestClient):
    response = client.post(
        "/api/pipelines",
        json={
            "name": "Equipo",
            "is_global": True,
            "stages": [{"name": "Nuevo", "position": 0}],
        },
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 403


def test_pipelines_patch_requires_owner_or_admin(client: TestClient):
    p = _create_pipeline(client, role="manager", name="A")
    other = client.patch(
        f"/api/pipelines/{p['id']}",
        json={"name": "Stolen"},
        headers=auth_headers(client, "user"),
    )
    assert other.status_code == 403
    own = client.patch(
        f"/api/pipelines/{p['id']}",
        json={"name": "Renamed"},
        headers=auth_headers(client, "manager"),
    )
    assert own.status_code == 200


def test_pipelines_toggle_is_global_requires_admin(client: TestClient):
    p = _create_pipeline(client, role="manager", name="A")
    own_resp = client.patch(
        f"/api/pipelines/{p['id']}",
        json={"is_global": True},
        headers=auth_headers(client, "manager"),
    )
    assert own_resp.status_code == 403
    admin_resp = client.patch(
        f"/api/pipelines/{p['id']}",
        json={"is_global": True},
        headers=auth_headers(client, "admin"),
    )
    assert admin_resp.status_code == 200
    assert admin_resp.json()["is_global"] is True
    assert admin_resp.json()["owner_user_id"] is None


def test_pipelines_delete_requires_owner_or_admin(client: TestClient):
    p = _create_pipeline(client, role="manager", name="A")
    other = client.delete(
        f"/api/pipelines/{p['id']}",
        headers=auth_headers(client, "user"),
    )
    assert other.status_code == 403
    own = client.delete(
        f"/api/pipelines/{p['id']}",
        headers=auth_headers(client, "manager"),
    )
    assert own.status_code == 200


# ---------------------------------------------------------------------------
# Mini-fix — default template folder per-user
# ---------------------------------------------------------------------------


def _create_folder(client: TestClient, *, role: str, name: str) -> dict:
    response = client.post(
        "/api/email-template-folders",
        json={"name": name},
        headers=auth_headers(client, role),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_default_template_folder_pref_upsert(client: TestClient):
    f = _create_folder(client, role="manager", name="Frio")
    response = client.put(
        "/api/users/me/default-template-folder",
        json={"folder_id": f["id"]},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 204
    # Idempotente — segundo PUT a la misma carpeta es 204.
    response2 = client.put(
        "/api/users/me/default-template-folder",
        json={"folder_id": f["id"]},
        headers=auth_headers(client, "manager"),
    )
    assert response2.status_code == 204
    # GET devuelve el folder_id.
    get_resp = client.get(
        "/api/users/me/default-template-folder",
        headers=auth_headers(client, "manager"),
    )
    assert get_resp.json()["folder_id"] == f["id"]


def test_default_template_folder_pref_clear_via_null(client: TestClient):
    f = _create_folder(client, role="manager", name="X")
    client.put(
        "/api/users/me/default-template-folder",
        json={"folder_id": f["id"]},
        headers=auth_headers(client, "manager"),
    )
    response = client.put(
        "/api/users/me/default-template-folder",
        json={"folder_id": None},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 204
    get_resp = client.get(
        "/api/users/me/default-template-folder",
        headers=auth_headers(client, "manager"),
    )
    assert get_resp.json()["folder_id"] is None


def test_email_template_folders_includes_is_default_for_me(
    client: TestClient,
):
    f1 = _create_folder(client, role="manager", name="A")
    f2 = _create_folder(client, role="manager", name="B")
    client.put(
        "/api/users/me/default-template-folder",
        json={"folder_id": f2["id"]},
        headers=auth_headers(client, "manager"),
    )
    tree = client.get(
        "/api/email-template-folders",
        headers=auth_headers(client, "manager"),
    ).json()
    by_id = {node["id"]: node for node in tree}
    assert by_id[f1["id"]]["is_default_for_me"] is False
    assert by_id[f2["id"]]["is_default_for_me"] is True
