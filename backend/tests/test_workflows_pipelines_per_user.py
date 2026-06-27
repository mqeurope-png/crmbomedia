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


def test_workflows_admin_sees_others_with_owner_email(client: TestClient):
    """PR-OAuth-Permisos-Admin Item 10. El admin ve los workflows
    privados de otros users, con owner_email para agruparlos en
    'De otros users'."""
    mgr = _create_workflow(client, role="manager", name="Privado de Manager")
    listed = client.get(
        "/api/workflows", headers=auth_headers(client, "admin")
    ).json()
    found = next(w for w in listed if w["id"] == mgr["id"])
    assert found["is_mine"] is False
    assert found["is_global"] is False
    assert found["owner_email"]  # email del manager presente


def test_pipelines_admin_sees_others_with_owner_email(client: TestClient):
    mgr = _create_pipeline(client, role="manager", name="Privado Manager")
    listed = client.get(
        "/api/pipelines", headers=auth_headers(client, "admin")
    ).json()
    found = next(p for p in listed if p["id"] == mgr["id"])
    assert found["is_mine"] is False
    assert found["is_global"] is False
    assert found["owner_email"]


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


# ---------------------------------------------------------------------------
# PR-Hotfix-Workflows-Pipelines-Permisos — Bug B + auditoría
# ---------------------------------------------------------------------------


def test_pipeline_stages_owner_can_edit(client: TestClient):
    """Owner del pipeline puede CRUD de etapas. Antes era admin/manager
    only y bloqueaba a comerciales con su propio pipeline."""
    pipeline = _create_pipeline(client, role="user", name="Mio user")
    headers = auth_headers(client, "user")

    # Crear etapa.
    create = client.post(
        f"/api/pipelines/{pipeline['id']}/stages",
        json={"name": "Lead", "position": 1},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    stage_id = create.json()["id"]

    # Editar etapa.
    patch = client.patch(
        f"/api/pipeline-stages/{stage_id}",
        json={"name": "Cualificado"},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text

    # Reorder.
    fresh = client.get(
        f"/api/pipelines/{pipeline['id']}", headers=headers
    ).json()
    ids = [s["id"] for s in fresh["stages"]]
    reorder = client.post(
        f"/api/pipelines/{pipeline['id']}/stages/reorder",
        json={"stage_ids": list(reversed(ids))},
        headers=headers,
    )
    assert reorder.status_code == 200, reorder.text

    # Borrar (con move_to_stage_id por si tuviera contactos).
    delete = client.delete(
        f"/api/pipeline-stages/{stage_id}",
        headers=headers,
    )
    assert delete.status_code == 200, delete.text


def test_pipeline_stages_other_user_cannot_edit(client: TestClient):
    """Un comercial NO puede tocar las etapas del pipeline de otro
    comercial."""
    pipeline = _create_pipeline(client, role="user", name="Mio user")
    other_headers = auth_headers(client, "manager")
    create = client.post(
        f"/api/pipelines/{pipeline['id']}/stages",
        json={"name": "Lead"},
        headers=other_headers,
    )
    assert create.status_code in (403, 404), create.text


def test_pipeline_stages_admin_can_edit_any(client: TestClient):
    """Admin puede CRUD de etapas en cualquier pipeline (propio, global,
    de otro user)."""
    pipeline = _create_pipeline(client, role="user", name="De otro user")
    admin_headers = auth_headers(client, "admin")
    create = client.post(
        f"/api/pipelines/{pipeline['id']}/stages",
        json={"name": "Lead"},
        headers=admin_headers,
    )
    assert create.status_code == 201, create.text


def test_pipeline_stages_global_only_admin_can_edit(client: TestClient):
    """En un pipeline global (del equipo), solo admin puede tocar
    etapas — un comercial recibe 403 aunque lo VEA."""
    global_p = _create_pipeline(
        client, role="admin", name="Equipo", is_global=True
    )
    # Comercial lo VE (es global).
    listed = client.get(
        "/api/pipelines", headers=auth_headers(client, "user")
    ).json()
    assert any(p["id"] == global_p["id"] for p in listed)
    # Pero no puede tocar las etapas.
    forbidden = client.post(
        f"/api/pipelines/{global_p['id']}/stages",
        json={"name": "Lead"},
        headers=auth_headers(client, "user"),
    )
    assert forbidden.status_code == 403
    # Admin sí.
    ok = client.post(
        f"/api/pipelines/{global_p['id']}/stages",
        json={"name": "Lead"},
        headers=auth_headers(client, "admin"),
    )
    assert ok.status_code == 201


def test_workflow_owner_can_activate_pause_archive(client: TestClient):
    """Owner de un workflow privado puede pausar/archivar — antes admin
    only y bloqueaba al comercial que creó su propio flujo."""
    w = _create_workflow(client, role="user", name="Mio user")
    headers = auth_headers(client, "user")
    # Pause OK (mantiene draft → paused).
    pause = client.post(
        f"/api/workflows/{w['id']}/pause", headers=headers
    )
    assert pause.status_code == 200, pause.text
    # Archive OK.
    archive = client.post(
        f"/api/workflows/{w['id']}/archive", headers=headers
    )
    assert archive.status_code == 200, archive.text


def test_workflow_other_user_cannot_pause(client: TestClient):
    w = _create_workflow(client, role="user", name="Mio user")
    forbidden = client.post(
        f"/api/workflows/{w['id']}/pause",
        headers=auth_headers(client, "manager"),
    )
    # Otro user no puede ver/editar → 404 o 403.
    assert forbidden.status_code in (403, 404)


def test_workflow_use_template_assigns_owner_user_id(client: TestClient):
    """Crear desde plantilla debe asignar owner_user_id=current_user
    (no NULL/global), incluso para no-admin."""
    # Listar plantillas disponibles.
    templates = client.get(
        "/api/workflows/_templates",
        headers=auth_headers(client, "user"),
    ).json()
    if not templates:
        pytest.skip("Sin plantillas built-in disponibles para el test.")
    tid = templates[0]["id"]
    created = client.post(
        f"/api/workflows/_templates/{tid}/use",
        headers=auth_headers(client, "user"),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    # Comercial → owner_user_id debe ser != None (privado, no global).
    assert body.get("owner_user_id") is not None
    assert body.get("is_mine") is True
    assert body.get("is_global") is False


def test_workflow_duplicate_assigns_owner_user_id(client: TestClient):
    """Duplicar un workflow global como comercial → la copia debe
    quedar privada del que duplica, no global."""
    src = _create_workflow(
        client, role="admin", name="Equipo", is_global=True
    )
    copy = client.post(
        f"/api/workflows/{src['id']}/duplicate",
        headers=auth_headers(client, "user"),
    )
    assert copy.status_code == 201, copy.text
    body = copy.json()
    assert body.get("owner_user_id") is not None
    assert body.get("is_mine") is True


def test_pipeline_duplicate_by_non_admin(client: TestClient):
    """Comercial puede duplicar un pipeline del equipo — la copia se
    crea como suya (privada). Antes era manager-only."""
    src = _create_pipeline(
        client, role="admin", name="Equipo", is_global=True
    )
    copy = client.post(
        f"/api/pipelines/{src['id']}/duplicate",
        json={"include_contacts": False},
        headers=auth_headers(client, "user"),
    )
    assert copy.status_code == 201, copy.text
    body = copy.json()
    assert body.get("owner_user_id") is not None
    assert body.get("is_mine") is True


# ---------------------------------------------------------------------------
# PR-Hotfix-Pipelines-Use-Template — comercial usa plantilla de pipeline
# ---------------------------------------------------------------------------


def _first_template_id(client: TestClient, role: str = "user") -> str:
    response = client.get(
        "/api/pipeline-templates", headers=auth_headers(client, role)
    )
    assert response.status_code == 200, response.text
    templates = response.json()
    assert len(templates) > 0, "Sin plantillas hardcoded — test inválido."
    return templates[0]["id"]


def test_pipeline_use_template_allows_commercial_creates_private(
    client: TestClient,
):
    """PR-Hotfix-Pipelines-Use-Template. Comercial usa plantilla → el
    pipeline se crea privado (owner_user_id=current_user). Antes el
    endpoint era manager-only y devolvía 403."""
    tid = _first_template_id(client, role="user")
    response = client.post(
        "/api/pipelines/from-template",
        json={"template_id": tid},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["owner_user_id"] is not None
    assert body["is_mine"] is True
    assert body["is_global"] is False


def test_pipeline_use_template_admin_with_is_global_creates_global(
    client: TestClient,
):
    """Admin puede mandar is_global=True desde el wizard y el pipeline
    queda global del equipo (owner_user_id=NULL)."""
    tid = _first_template_id(client, role="admin")
    response = client.post(
        "/api/pipelines/from-template",
        json={"template_id": tid, "is_global": True},
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["owner_user_id"] is None
    assert body["is_global"] is True


def test_pipeline_use_template_non_admin_with_is_global_forbidden(
    client: TestClient,
):
    """Comercial que manda is_global=True → 403 (consistente con el
    POST /api/pipelines normal)."""
    tid = _first_template_id(client, role="user")
    response = client.post(
        "/api/pipelines/from-template",
        json={"template_id": tid, "is_global": True},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 403


def test_pipeline_duplicate_allows_commercial_creates_private(
    client: TestClient,
):
    """Comercial duplica un pipeline del equipo (regresión guard de #252,
    re-verificado aquí porque comparte la misma promesa que el use
    template fix)."""
    src = _create_pipeline(
        client, role="admin", name="Equipo", is_global=True
    )
    copy = client.post(
        f"/api/pipelines/{src['id']}/duplicate",
        json={"include_contacts": False},
        headers=auth_headers(client, "user"),
    )
    assert copy.status_code == 201, copy.text
    body = copy.json()
    assert body["owner_user_id"] is not None
    assert body["is_mine"] is True
