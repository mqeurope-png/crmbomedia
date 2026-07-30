"""Bulk-action endpoint smoke tests."""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    ContactPipelineStage,
    ContactTag,
    Pipeline,
    PipelineStage,
    Segment,
    Tag,
    Task,
    User,
    UserRole,
)
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
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
        # PR-Bulk-Comerciales. Las acciones masivas del comercial (`user`)
        # solo aplican a SUS contactos, así que los seeds se asignan al
        # comercial para que los tests de dispatch con rol "user" sigan
        # actuando sobre los 3.
        uid = seed.scalar(select(User.id).where(User.role == UserRole.USER))
        seed.add_all(
            [
                Contact(first_name="A", email="a@example.com", owner_user_id=uid),
                Contact(first_name="B", email="b@example.com", owner_user_id=uid),
                Contact(first_name="C", email="c@example.com", owner_user_id=uid),
            ]
        )
        seed.add(Tag(name="VIP", name_normalized="vip"))
        seed.commit()
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


def _all_contact_ids(session_factory: sessionmaker) -> list[str]:
    with session_factory() as s:
        return [c.id for c in s.scalars(select(Contact))]


def test_assign_owner_accepts_role_user(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """PR-Ca hotfix — decisión §1 spec Reglas-Assign. Cualquier
    comercial puede auto-asignarse o asignar a otro vía bulk; antes el
    endpoint pedía manager+ y rompía el flujo de cartera personal."""
    contact_ids = _all_contact_ids(session_factory)
    with session_factory() as s:
        manager_id = s.scalar(select(User.id).where(User.role == UserRole.MANAGER))
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "assign_owner",
            "payload": {"owner_user_id": manager_id},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected_count"] == len(contact_ids)


def test_assign_owner_manager_succeeds(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    with session_factory() as s:
        manager_id = s.scalar(select(User.id).where(User.role == UserRole.MANAGER))
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "assign_owner",
            "payload": {"owner_user_id": manager_id},
        },
        headers=auth_headers(client, "manager"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected_count"] == 3
    with session_factory() as s:
        owners = {c.owner_user_id for c in s.scalars(select(Contact))}
    assert owners == {manager_id}


def test_change_status_user_ok(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "change_status",
            "payload": {"new_status": "qualified"},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    assert resp.json()["affected_count"] == 3


def test_add_tag_creates_assignments_only_once(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    with session_factory() as s:
        tag_id = s.scalar(select(Tag.id))
    resp1 = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "add_tag",
            "payload": {"tag_id": tag_id},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp1.status_code == 200
    assert resp1.json()["affected_count"] == 3
    # Re-run — none should be added a second time.
    resp2 = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "add_tag",
            "payload": {"tag_id": tag_id},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp2.json()["affected_count"] == 0
    with session_factory() as s:
        assignments = list(s.scalars(select(ContactTag)))
    assert len(assignments) == 3


def test_deactivate_only_admin(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    blocked = client.post(
        "/api/contacts/bulk-action",
        json={"contact_ids": contact_ids, "action": "deactivate"},
        headers=auth_headers(client, "manager"),
    )
    assert blocked.status_code == 403
    ok = client.post(
        "/api/contacts/bulk-action",
        json={"contact_ids": contact_ids, "action": "deactivate"},
        headers=auth_headers(client, "admin"),
    )
    assert ok.status_code == 200
    assert ok.json()["affected_count"] == 3


def test_bulk_rejects_oversized_selection(client: TestClient) -> None:
    """Sprint Reglas-Assign PR-D — el cap subió de 1000 a 50000. Sigue
    siendo un seguro de memoria contra requests maliciosas; el límite
    real lo aplica el chunking server-side de la operación."""
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": [f"x-{i}" for i in range(50_001)],
            "action": "change_status",
            "payload": {"new_status": "qualified"},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 422


def test_search_ids_returns_uuids_only(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """`POST /api/contacts/search/ids` returns just the UUIDs of the
    matching contacts — used by the "Seleccionar todos del filtro"
    banner so the client can expand the selection without pulling
    every Contact body."""
    response = client.post(
        "/api/contacts/search/ids",
        json={},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["ids"], list)
    assert body["count"] == 3
    assert body["truncated"] is False
    assert body["max_ids"] == 10_000


def test_search_ids_respects_assigned_to_me(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """The same `assigned_to_me` flag the search endpoint honours
    must trim the id list — otherwise the bulk banner could end up
    asking the user to act on contacts they don't own."""
    with session_factory() as s:
        user_id = s.scalar(select(User.id).where(User.role == UserRole.USER))
        # Solo el primer contacto es del user; el resto se desasigna
        # (el fixture los crea todos suyos para los tests de bulk).
        contacts = list(s.scalars(select(Contact)))
        for i, c in enumerate(contacts):
            c.owner_user_id = user_id if i == 0 else None
        s.commit()
    response = client.post(
        "/api/contacts/search/ids",
        json={"assigned_to_me": True},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_bulk_assign_owner_handles_more_than_1000(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Sprint Reglas-Assign PR-D — el bulk antes capaba a 1000. Ahora
    50000 con chunks server-side de 500. Genera 1500 contactos y
    verifica que se asignan todos en una sola request."""
    with session_factory() as s:
        owner_id = s.scalar(select(User.id).where(User.role == UserRole.USER))
        rows = [
            Contact(
                first_name=f"Bulk{i:04d}",
                email=f"bulk_{i:04d}@example.com",
                tags="",
                commercial_status="new",
            )
            for i in range(1500)
        ]
        s.add_all(rows)
        s.commit()
        ids = [c.id for c in rows]

    response = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": ids,
            "action": "assign_owner",
            "payload": {"owner_user_id": owner_id},
        },
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["affected_count"] == 1500
    assert len(body["contact_ids"]) == 1500

    from app.models.crm import ContactAssignment  # noqa: PLC0415

    with session_factory() as s:
        assigned = s.scalar(
            select(func.count(ContactAssignment.id)).where(
                ContactAssignment.user_id == owner_id
            )
        )
        assert assigned == 1500


# ---------------------------------------------------------------------------
# PR-Hotfix-Notas-Workflows Item C — nuevas acciones bulk
# ---------------------------------------------------------------------------


def _admin_id(session_factory: sessionmaker) -> str:
    with session_factory() as s:
        return s.scalar(select(User.id).where(User.role == UserRole.ADMIN))


def test_bulk_add_tag_create_on_the_fly(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "add_tag",
            "payload": {"tag_name": "Nueva Al Vuelo"},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected_count"] == len(contact_ids)
    with session_factory() as s:
        tag = s.scalar(select(Tag).where(Tag.name_normalized == "nueva al vuelo"))
        assert tag is not None
        assert tag.color  # color determinista asignado
        links = s.scalar(
            select(func.count(ContactTag.tag_id)).where(ContactTag.tag_id == tag.id)
        )
        assert links == len(contact_ids)


def test_bulk_add_to_pipeline_creates_stage_rows(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    with session_factory() as s:
        pipeline = Pipeline(name="Ventas")
        s.add(pipeline)
        s.flush()
        stage = PipelineStage(pipeline_id=pipeline.id, name="Nuevo", position=0)
        s.add(stage)
        s.commit()
        pipeline_id = pipeline.id
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "add_to_pipeline",
            "payload": {"pipeline_id": pipeline_id},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected_count"] == len(contact_ids)
    with session_factory() as s:
        n = s.scalar(
            select(func.count(ContactPipelineStage.id)).where(
                ContactPipelineStage.pipeline_id == pipeline_id
            )
        )
        assert n == len(contact_ids)


def test_bulk_add_to_pipeline_bad_pipeline_400(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "add_to_pipeline",
            "payload": {"pipeline_id": "does-not-exist"},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 400


def test_bulk_create_task_one_per_contact(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "create_task",
            "payload": {"title": "Llamar", "priority": "high"},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected_count"] == len(contact_ids)
    with session_factory() as s:
        tasks = list(s.scalars(select(Task).where(Task.title == "Llamar")))
        assert len(tasks) == len(contact_ids)
        assert {t.contact_id for t in tasks} == set(contact_ids)


def test_bulk_create_task_requires_title(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "create_task",
            "payload": {},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 400


def test_bulk_change_lifecycle_sets_commercial_status(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "change_lifecycle",
            "payload": {"lifecycle_status": "customer"},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    with session_factory() as s:
        statuses = {
            c.commercial_status
            for c in s.scalars(select(Contact).where(Contact.id.in_(contact_ids)))
        }
        assert statuses == {"customer"}


def test_bulk_add_to_segment_static(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    admin_id = _admin_id(session_factory)
    with session_factory() as s:
        seg = Segment(
            name="Estático", owner_user_id=admin_id, is_dynamic=False,
        )
        s.add(seg)
        s.commit()
        seg_id = seg.id
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "add_to_segment",
            "payload": {"segment_id": seg_id},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    from app.repositories import segments as segments_repository

    with session_factory() as s:
        seg = s.get(Segment, seg_id)
        assert set(segments_repository.decode_static_ids(seg)) == set(contact_ids)


def test_bulk_add_to_segment_dynamic_rejected(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    admin_id = _admin_id(session_factory)
    with session_factory() as s:
        seg = Segment(
            name="Dinámico", owner_user_id=admin_id, is_dynamic=True,
        )
        s.add(seg)
        s.commit()
        seg_id = seg.id
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "add_to_segment",
            "payload": {"segment_id": seg_id},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 400


def test_bulk_add_to_workflow_enrolls_contacts(
    client: TestClient, session_factory: sessionmaker
) -> None:
    contact_ids = _all_contact_ids(session_factory)
    admin_id = _admin_id(session_factory)
    with session_factory() as s:
        wf = Workflow(
            name="Enroll",
            trigger_type="contact.created",
            status=WorkflowStatus.ACTIVE,
            created_by_user_id=admin_id,
            trigger_config_json="{}",
        )
        s.add(wf)
        s.flush()
        trig = WorkflowStep(
            workflow_id=wf.id, type="trigger", config_json="{}",
            position_x=0, position_y=0, is_entry=True,
        )
        exit_step = WorkflowStep(
            workflow_id=wf.id, type="exit_won", config_json="{}",
            position_x=0, position_y=150, is_entry=False,
        )
        s.add_all([trig, exit_step])
        s.flush()
        s.add(
            WorkflowEdge(
                workflow_id=wf.id, from_step_id=trig.id,
                to_step_id=exit_step.id, branch_label="default",
            )
        )
        s.commit()
        wf_id = wf.id
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": contact_ids,
            "action": "add_to_workflow",
            "payload": {"workflow_id": wf_id},
        },
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected_count"] == len(contact_ids)
    with session_factory() as s:
        runs = s.scalar(
            select(func.count(WorkflowRun.id)).where(WorkflowRun.workflow_id == wf_id)
        )
        assert runs == len(contact_ids)


def test_bulk_export_csv_open_to_commercial_own(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """PR-Bulk-Comerciales. Export abierto a comerciales: exportan sus
    propios contactos (el fixture los asigna al `user`). Viewer sigue
    prohibido; admin exporta todo."""
    contact_ids = _all_contact_ids(session_factory)
    # Comercial: OK sobre los suyos.
    mine = client.post(
        "/api/contacts/bulk-export-csv",
        json={"contact_ids": contact_ids},
        headers=auth_headers(client, "user"),
    )
    assert mine.status_code == 200, mine.text
    assert mine.headers["content-type"].startswith("text/csv")
    # Viewer: prohibido (require_user excluye viewers).
    viewer = client.post(
        "/api/contacts/bulk-export-csv",
        json={"contact_ids": contact_ids},
        headers=auth_headers(client, "viewer"),
    )
    assert viewer.status_code == 403
    # Admin: OK, devuelve CSV.
    ok = client.post(
        "/api/contacts/bulk-export-csv",
        json={"contact_ids": contact_ids},
        headers=auth_headers(client, "admin"),
    )
    assert ok.status_code == 200
    assert "first_name" in ok.text


def test_bulk_nonexistent_ids_400(
    client: TestClient, session_factory: sessionmaker
) -> None:
    resp = client.post(
        "/api/contacts/bulk-action",
        json={
            "contact_ids": ["nope-1", "nope-2"],
            "action": "create_task",
            "payload": {"title": "x"},
        },
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 400
