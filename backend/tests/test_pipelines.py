"""CRUD pipelines + stages + contact assignments + reordering, history
writes, and the basic report endpoint."""
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as seed_session:
        seed_test_users(seed_session)

    def override_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def _create_pipeline(client: TestClient, stages: list[str] | None = None) -> dict:
    # PR-Workflows-Pipelines-Per-User. Tests existentes asumían
    # acceso transversal por role. Se mantiene esa premisa creando
    # el pipeline como GLOBAL (admin con is_global=True), así
    # viewer/manager también lo ven. Los tests del nuevo
    # comportamiento de privacidad usan POST manualmente.
    payload = {
        "name": "Pipeline Ventas",
        "description": "Pipeline de prueba",
        "is_global": True,
        "stages": [
            {"name": stage_name, "position": index}
            for index, stage_name in enumerate(
                stages or ["Nuevo", "Contactado", "Cualificado", "Ganado"]
            )
        ],
    }
    response = client.post(
        "/api/pipelines", json=payload, headers=auth_headers(client, "admin")
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_contact(client: TestClient, email: str = "ana@example.com") -> dict:
    response = client.post(
        "/api/contacts",
        json={"first_name": "Ana", "email": email, "marketing_consent": "unknown"},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_pipeline_with_inline_stages(client: TestClient):
    """A single POST sets up the pipeline + its initial stages. The
    stage payloads' `position` field is normalised so callers can
    omit it or send it scrambled — the route forces 0..N-1."""
    pipeline = _create_pipeline(client)
    assert pipeline["name"] == "Pipeline Ventas"
    assert pipeline["stages"][0]["position"] == 0
    assert pipeline["stages"][-1]["position"] == len(pipeline["stages"]) - 1
    assert pipeline["contact_count"] == 0


def test_add_stage_inserts_and_shifts_positions(client: TestClient):
    """Inserting a stage at position 1 must shift everything below by
    one — the contiguous-0..N-1 invariant survives every mutation."""
    pipeline = _create_pipeline(client, stages=["A", "B", "C"])
    # PR-Hotfix-Workflows-Pipelines-Permisos. Post-#250 editar etapas de
    # un pipeline GLOBAL (creado vía _create_pipeline) requiere admin.
    response = client.post(
        f"/api/pipelines/{pipeline['id']}/stages",
        json={"name": "Nueva", "position": 1},
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 201
    refreshed = client.get(
        f"/api/pipelines/{pipeline['id']}", headers=auth_headers(client, "viewer")
    ).json()
    names = [(s["position"], s["name"]) for s in refreshed["stages"]]
    assert names == [(0, "A"), (1, "Nueva"), (2, "B"), (3, "C")]


def test_reorder_stages_requires_full_set(client: TestClient):
    """Dropping a stage from the reorder body must 400 — the operator
    would silently lose the position otherwise."""
    pipeline = _create_pipeline(client, stages=["A", "B", "C"])
    short = client.post(
        f"/api/pipelines/{pipeline['id']}/stages/reorder",
        json={"stage_ids": [pipeline["stages"][0]["id"], pipeline["stages"][1]["id"]]},
        headers=auth_headers(client, "admin"),
    )
    assert short.status_code == 400


def test_reorder_stages_permutation_applies(client: TestClient):
    pipeline = _create_pipeline(client, stages=["A", "B", "C"])
    ids = [s["id"] for s in pipeline["stages"]]
    reordered = client.post(
        f"/api/pipelines/{pipeline['id']}/stages/reorder",
        json={"stage_ids": [ids[2], ids[0], ids[1]]},
        headers=auth_headers(client, "admin"),
    )
    assert reordered.status_code == 200
    refreshed = client.get(
        f"/api/pipelines/{pipeline['id']}", headers=auth_headers(client, "viewer")
    ).json()
    names_in_order = [s["name"] for s in refreshed["stages"]]
    assert names_in_order == ["C", "A", "B"]


def test_add_contact_to_pipeline_defaults_to_first_stage(client: TestClient):
    pipeline = _create_pipeline(client)
    contact = _create_contact(client)
    response = client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["stage_id"] == pipeline["stages"][0]["id"]
    assert body["is_archived"] is False


def test_contact_cannot_be_in_pipeline_twice(client: TestClient):
    """Adding a contact a second time must NOT produce a unique-key
    error. The repository upserts so the operator can re-add an
    archived contact without dealing with HTTP 409."""
    pipeline = _create_pipeline(client)
    contact = _create_contact(client)
    headers = auth_headers(client, "manager")
    first = client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=headers,
    )
    second = client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={
            "pipeline_id": pipeline["id"],
            "stage_id": pipeline["stages"][2]["id"],
        },
        headers=headers,
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["stage_id"] == pipeline["stages"][2]["id"]


def test_move_contact_writes_history(client: TestClient):
    """Moving between stages must persist a `contact_stage_history`
    row carrying the duration spent in the previous stage."""
    from app.models.crm import ContactStageHistory

    pipeline = _create_pipeline(client)
    contact = _create_contact(client)
    add = client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=auth_headers(client, "manager"),
    )
    assignment = add.json()
    move = client.patch(
        f"/api/contact-pipeline-stages/{assignment['id']}",
        json={
            "stage_id": pipeline["stages"][1]["id"],
            "note": "Le respondió el email",
        },
        headers=auth_headers(client, "manager"),
    )
    assert move.status_code == 200
    session_factory = app.dependency_overrides[get_session]
    gen = session_factory()
    session = next(gen)
    try:
        rows = list(session.query(ContactStageHistory).order_by(
            ContactStageHistory.moved_at
        ))
    finally:
        gen.close()
    # 1 for add, 1 for move.
    assert len(rows) == 2
    assert rows[0].from_stage_id is None
    assert rows[1].from_stage_id == pipeline["stages"][0]["id"]
    assert rows[1].to_stage_id == pipeline["stages"][1]["id"]


def test_move_to_same_stage_is_idempotent(client: TestClient):
    """Re-clicking "Move" to the current column must not produce a
    history row. Otherwise a UI double-drag would pollute the report."""
    from app.models.crm import ContactStageHistory

    pipeline = _create_pipeline(client)
    contact = _create_contact(client)
    add = client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=auth_headers(client, "manager"),
    )
    response = client.patch(
        f"/api/contact-pipeline-stages/{add.json()['id']}",
        json={"stage_id": pipeline["stages"][0]["id"]},
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200
    session_factory = app.dependency_overrides[get_session]
    gen = session_factory()
    session = next(gen)
    try:
        assert session.query(ContactStageHistory).count() == 1
    finally:
        gen.close()


def test_delete_stage_with_contacts_requires_move_target(client: TestClient):
    pipeline = _create_pipeline(client)
    contact = _create_contact(client)
    client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=auth_headers(client, "manager"),
    )
    stage_id = pipeline["stages"][0]["id"]
    blocked = client.delete(
        f"/api/pipeline-stages/{stage_id}",
        headers=auth_headers(client, "admin"),
    )
    assert blocked.status_code == 400

    ok = client.delete(
        f"/api/pipeline-stages/{stage_id}?move_to_stage_id={pipeline['stages'][1]['id']}",
        headers=auth_headers(client, "admin"),
    )
    assert ok.status_code == 200
    refreshed = client.get(
        f"/api/pipelines/{pipeline['id']}", headers=auth_headers(client, "viewer")
    ).json()
    positions = [s["position"] for s in refreshed["stages"]]
    assert positions == list(range(len(refreshed["stages"])))


def test_list_pipeline_contacts_groups_by_stage(client: TestClient):
    pipeline = _create_pipeline(client, stages=["Nuevo", "Contactado"])
    ana = _create_contact(client, "ana@example.com")
    boris = _create_contact(client, "boris@example.com")
    headers = auth_headers(client, "manager")
    client.post(
        f"/api/contacts/{ana['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=headers,
    )
    add_b = client.post(
        f"/api/contacts/{boris['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=headers,
    )
    client.patch(
        f"/api/contact-pipeline-stages/{add_b.json()['id']}",
        json={"stage_id": pipeline["stages"][1]["id"]},
        headers=headers,
    )

    response = client.get(
        f"/api/pipelines/{pipeline['id']}/contacts",
        headers=auth_headers(client, "viewer"),
    )
    body = response.json()
    stages = {group["stage_name"]: group for group in body["stages"]}
    assert stages["Nuevo"]["total"] == 1
    assert stages["Contactado"]["total"] == 1
    assert stages["Nuevo"]["contacts"][0]["email"] == "ana@example.com"


def test_archive_assignment_hides_it_from_list(client: TestClient):
    pipeline = _create_pipeline(client, stages=["Nuevo"])
    contact = _create_contact(client)
    add = client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=auth_headers(client, "manager"),
    )
    archive = client.delete(
        f"/api/contact-pipeline-stages/{add.json()['id']}",
        headers=auth_headers(client, "manager"),
    )
    assert archive.status_code == 200
    body = client.get(
        f"/api/pipelines/{pipeline['id']}/contacts",
        headers=auth_headers(client, "viewer"),
    ).json()
    assert body["stages"][0]["total"] == 0


def test_pipeline_report_computes_basic_metrics(client: TestClient):
    pipeline = _create_pipeline(client, stages=["Nuevo", "Cualificado", "Ganado"])
    contact = _create_contact(client)
    headers = auth_headers(client, "manager")
    add = client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=headers,
    )
    # Move forward once.
    client.patch(
        f"/api/contact-pipeline-stages/{add.json()['id']}",
        json={"stage_id": pipeline["stages"][1]["id"]},
        headers=headers,
    )

    response = client.get(
        f"/api/pipelines/{pipeline['id']}/report",
        headers=auth_headers(client, "viewer"),
    )
    body = response.json()
    assert body["total_contacts"] == 1
    metrics_by_position = {m["position"]: m for m in body["metrics"]}
    assert metrics_by_position[0]["contact_count"] == 0
    assert metrics_by_position[1]["contact_count"] == 1


def test_soft_delete_pipeline_hides_from_default_list(client: TestClient):
    pipeline = _create_pipeline(client)
    # PR-Workflows-Pipelines-Per-User. El pipeline es global (creado
    # por admin con is_global=True). Solo admin/owner puede borrarlo.
    deleted = client.delete(
        f"/api/pipelines/{pipeline['id']}",
        headers=auth_headers(client, "admin"),
    )
    assert deleted.status_code == 200
    rows = client.get(
        "/api/pipelines", headers=auth_headers(client, "viewer")
    ).json()
    assert all(row["id"] != pipeline["id"] for row in rows)
    rows_with_inactive = client.get(
        "/api/pipelines?include_inactive=true",
        headers=auth_headers(client, "viewer"),
    ).json()
    assert any(row["id"] == pipeline["id"] for row in rows_with_inactive)


def test_duplicate_pipeline_clones_stages_only_by_default(client: TestClient):
    """The default duplicate skips contact rows so the operator can
    iterate on the template without polluting the original's
    timeline."""
    from app.models.crm import ContactPipelineStage

    pipeline = _create_pipeline(client, stages=["A", "B"])
    contact = _create_contact(client)
    client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=auth_headers(client, "manager"),
    )

    duplicate = client.post(
        f"/api/pipelines/{pipeline['id']}/duplicate",
        json={"name": "Pipeline copia"},
        headers=auth_headers(client, "manager"),
    )
    assert duplicate.status_code == 201
    body = duplicate.json()
    assert body["name"] == "Pipeline copia"
    assert len(body["stages"]) == 2

    session_factory = app.dependency_overrides[get_session]
    gen = session_factory()
    session = next(gen)
    try:
        clone_assignments = (
            session.query(ContactPipelineStage)
            .filter(ContactPipelineStage.pipeline_id == body["id"])
            .all()
        )
    finally:
        gen.close()
    assert clone_assignments == []


def test_list_contact_pipelines_returns_summaries(client: TestClient):
    """`GET /api/contacts/{id}/pipelines` is the single fetch the
    contact-detail page uses to render its Pipelines section."""
    pipeline_a = _create_pipeline(client, stages=["Nuevo", "Cualificado"])
    pipeline_b_response = client.post(
        "/api/pipelines",
        json={"name": "Otra", "stages": [{"name": "Inicio", "position": 0}]},
        headers=auth_headers(client, "manager"),
    )
    pipeline_b = pipeline_b_response.json()
    contact = _create_contact(client)
    headers = auth_headers(client, "manager")
    client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline_a["id"]},
        headers=headers,
    )
    client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline_b["id"]},
        headers=headers,
    )

    response = client.get(
        f"/api/contacts/{contact['id']}/pipelines",
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 200
    summaries = response.json()
    names = {s["pipeline_name"] for s in summaries}
    assert names == {"Pipeline Ventas", "Otra"}
    for summary in summaries:
        assert "stage_name" in summary
        assert summary["days_in_stage"] >= 0


def test_list_contact_pipelines_hides_archived_by_default(client: TestClient):
    pipeline = _create_pipeline(client, stages=["Nuevo"])
    contact = _create_contact(client)
    add = client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=auth_headers(client, "manager"),
    )
    client.delete(
        f"/api/contact-pipeline-stages/{add.json()['id']}",
        headers=auth_headers(client, "manager"),
    )
    default = client.get(
        f"/api/contacts/{contact['id']}/pipelines",
        headers=auth_headers(client, "viewer"),
    )
    assert default.json() == []
    with_archived = client.get(
        f"/api/contacts/{contact['id']}/pipelines?include_archived=true",
        headers=auth_headers(client, "viewer"),
    )
    assert len(with_archived.json()) == 1


def test_stalled_contacts_endpoint_returns_overdue_rows(client: TestClient):
    """A contact whose days_in_stage > stage.target_days appears in
    the stalled list with the overdue delta. Contacts within SLA
    don't show up."""
    from app.models.crm import ContactPipelineStage

    pipeline = _create_pipeline(client, stages=["Stuck", "Done"])
    # Set target_days=1 on the first stage.
    stage_id = pipeline["stages"][0]["id"]
    patch = client.patch(
        f"/api/pipeline-stages/{stage_id}",
        json={"target_days": 1},
        headers=auth_headers(client, "admin"),
    )
    assert patch.status_code == 200

    contact = _create_contact(client)
    add = client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=auth_headers(client, "manager"),
    )
    assignment_id = add.json()["id"]

    # Manually backdate entered_stage_at so the contact is overdue.
    session_factory = app.dependency_overrides[get_session]
    gen = session_factory()
    session = next(gen)
    try:
        from datetime import UTC, datetime, timedelta

        row = session.get(ContactPipelineStage, assignment_id)
        assert row is not None
        row.entered_stage_at = datetime.now(UTC) - timedelta(days=5)
        session.commit()
    finally:
        gen.close()

    response = client.get(
        f"/api/pipelines/{pipeline['id']}/stalled-contacts",
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["email"] == contact["email"]
    assert body[0]["overdue_days"] >= 3
    assert body[0]["stage_name"] == "Stuck"


def test_stalled_contacts_empty_when_no_target(client: TestClient):
    """Stages without `target_days` never produce stalled rows even
    when contacts have been there forever."""
    pipeline = _create_pipeline(client)
    contact = _create_contact(client)
    client.post(
        f"/api/contacts/{contact['id']}/pipelines",
        json={"pipeline_id": pipeline["id"]},
        headers=auth_headers(client, "manager"),
    )
    response = client.get(
        f"/api/pipelines/{pipeline['id']}/stalled-contacts",
        headers=auth_headers(client, "viewer"),
    )
    assert response.json() == []
