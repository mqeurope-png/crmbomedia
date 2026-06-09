from collections.abc import Generator
from datetime import UTC, datetime

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


def create_contact(client: TestClient, email: str = "ana@example.com") -> dict:
    response = client.post(
        "/api/contacts",
        json={
            "first_name": "Ana",
            "last_name": "García",
            "email": email,
            "origin": "agilecrm",
            "marketing_consent": "granted",
        },
        headers=auth_headers(client, "manager"),
    )
    assert response.status_code == 201
    return response.json()


def test_health_returns_app_metadata(client: TestClient):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_and_current_user(client: TestClient):
    headers = auth_headers(client, "admin")

    response = client.get("/api/auth/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["email"] == "admin@example.com"
    assert response.json()["role"] == "admin"


def test_reject_invalid_login(client: TestClient):
    response = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )

    assert response.status_code == 401


def test_protect_crm_endpoints(client: TestClient):
    response = client.get("/api/contacts")

    assert response.status_code == 401


def test_admin_can_create_and_list_users(client: TestClient):
    headers = auth_headers(client, "admin")

    created = client.post(
        "/api/users",
        json={
            "email": "new-user@example.com",
            "full_name": "New User",
            "password": "NewUserPass123!",
            "role": "viewer",
        },
        headers=headers,
    )
    listed = client.get("/api/users", headers=headers)

    updated = client.patch(
        f"/api/users/{created.json()['id']}",
        json={"role": "user", "full_name": "Updated User"},
        headers=headers,
    )

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["role"] == "user"
    assert any(user["email"] == "new-user@example.com" for user in listed.json())


def test_non_admin_cannot_manage_users(client: TestClient):
    response = client.get("/api/users", headers=auth_headers(client, "manager"))

    assert response.status_code == 403


def test_create_company_requires_manager(client: TestClient):
    viewer_response = client.post(
        "/api/companies", json={"name": "MQ Europe"}, headers=auth_headers(client, "viewer")
    )
    manager_response = client.post(
        "/api/companies", json={"name": "MQ Europe"}, headers=auth_headers(client, "manager")
    )

    assert viewer_response.status_code == 403
    assert manager_response.status_code == 201


def test_list_companies_with_search_and_pagination(client: TestClient):
    headers = auth_headers(client, "manager")
    client.post("/api/companies", json={"name": "Alpha"}, headers=headers)
    client.post("/api/companies", json={"name": "Beta"}, headers=headers)

    response = client.get(
        "/api/companies",
        params={"q": "alp", "skip": 0, "limit": 10},
        headers=auth_headers(client, "viewer"),
    )

    assert response.status_code == 200
    assert [company["name"] for company in response.json()] == ["Alpha"]


def test_update_and_deactivate_company(client: TestClient):
    headers = auth_headers(client, "manager")
    company = client.post("/api/companies", json={"name": "Old"}, headers=headers).json()

    updated = client.patch(
        f"/api/companies/{company['id']}", json={"name": "New"}, headers=headers
    )
    deactivated = client.patch(f"/api/companies/{company['id']}/deactivate", headers=headers)

    assert updated.status_code == 200
    assert updated.json()["name"] == "New"
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False


def test_create_contact_with_company_and_persist_consent(client: TestClient):
    headers = auth_headers(client, "manager")
    company = client.post("/api/companies", json={"name": "Empresa"}, headers=headers).json()

    response = client.post(
        "/api/contacts",
        json={
            "first_name": "Ana",
            "email": "ANA@example.com",
            "company_id": company["id"],
            "marketing_consent": "unsubscribed",
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["email"] == "ana@example.com"
    assert response.json()["marketing_consent"] == "unsubscribed"
    assert response.json()["company_id"] == company["id"]


def test_reject_contact_for_missing_company(client: TestClient):
    response = client.post(
        "/api/contacts",
        json={"first_name": "Ana", "email": "ana@example.com", "company_id": "missing"},
        headers=auth_headers(client, "manager"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Company not found"


def test_viewer_cannot_create_contact(client: TestClient):
    response = client.post(
        "/api/contacts",
        json={"first_name": "Luis", "email": "luis@example.com"},
        headers=auth_headers(client, "viewer"),
    )

    assert response.status_code == 403


def test_reject_duplicate_contact_email(client: TestClient):
    create_contact(client, "luis@example.com")

    duplicate = client.post(
        "/api/contacts",
        json={"first_name": "Luis", "email": "LUIS@example.com"},
        headers=auth_headers(client, "manager"),
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "A contact with this email already exists"


def test_reject_invalid_contact_email(client: TestClient):
    response = client.post(
        "/api/contacts",
        json={"first_name": "Luis", "email": "not-email"},
        headers=auth_headers(client, "manager"),
    )

    assert response.status_code == 422


def test_search_and_paginate_contacts(client: TestClient):
    create_contact(client, "ana@example.com")
    create_contact(client, "marta@example.com")

    response = client.get(
        "/api/contacts",
        params={"q": "mart", "skip": 0, "limit": 5},
        headers=auth_headers(client, "viewer"),
    )

    assert response.status_code == 200
    body = response.json()
    # The list endpoint returns a wrapper with `total` so the UI can
    # paginate without a second `/count` round-trip. Items, total, limit
    # and offset must all be present.
    assert body["total"] == 1
    assert body["limit"] == 5
    assert body["offset"] == 0
    assert len(body["items"]) == 1
    assert body["items"][0]["email"] == "marta@example.com"


def test_contacts_list_filter_by_tag(client: TestClient):
    """`tag=` matches an exact CSV token, never a substring. "VIP" must
    not pull rows tagged "VIPS"."""
    headers = auth_headers(client, "manager")
    for first_name, email, tags in (
        ("Ana", "ana@example.com", "vip,priority"),
        ("Boris", "boris@example.com", "newsletter"),
        ("Carla", "carla@example.com", "vips"),
        ("Diego", "diego@example.com", "vip"),
    ):
        client.post(
            "/api/contacts",
            json={
                "first_name": first_name,
                "email": email,
                "tags": tags,
                "marketing_consent": "unknown",
            },
            headers=headers,
        )

    response = client.get(
        "/api/contacts",
        params={"tag": "vip"},
        headers=auth_headers(client, "viewer"),
    )

    assert response.status_code == 200
    body = response.json()
    emails = sorted(item["email"] for item in body["items"])
    assert emails == ["ana@example.com", "diego@example.com"]
    assert body["total"] == 2


def test_contacts_list_filter_by_commercial_status(client: TestClient):
    headers = auth_headers(client, "manager")
    for name, email, status_value in (
        ("Ana", "ana@example.com", "qualified"),
        ("Boris", "boris@example.com", "new"),
    ):
        client.post(
            "/api/contacts",
            json={
                "first_name": name,
                "email": email,
                "commercial_status": status_value,
                "marketing_consent": "unknown",
            },
            headers=headers,
        )

    response = client.get(
        "/api/contacts",
        params={"commercial_status": "qualified"},
        headers=auth_headers(client, "viewer"),
    )

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "ana@example.com"


def test_contacts_list_filter_by_marketing_consent(client: TestClient):
    headers = auth_headers(client, "manager")
    for name, email, consent in (
        ("Ana", "ana@example.com", "granted"),
        ("Boris", "boris@example.com", "denied"),
        ("Carla", "carla@example.com", "unknown"),
    ):
        client.post(
            "/api/contacts",
            json={
                "first_name": name,
                "email": email,
                "marketing_consent": consent,
            },
            headers=headers,
        )

    response = client.get(
        "/api/contacts",
        params={"marketing_consent": "granted"},
        headers=auth_headers(client, "viewer"),
    )
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "ana@example.com"


def test_contacts_list_sort_by_email_asc(client: TestClient):
    headers = auth_headers(client, "manager")
    for name, email in (
        ("Carla", "carla@example.com"),
        ("Ana", "ana@example.com"),
        ("Boris", "boris@example.com"),
    ):
        client.post(
            "/api/contacts",
            json={
                "first_name": name,
                "email": email,
                "marketing_consent": "unknown",
            },
            headers=headers,
        )

    response = client.get(
        "/api/contacts",
        params={"sort_by": "email", "sort_dir": "asc"},
        headers=auth_headers(client, "viewer"),
    )
    body = response.json()
    assert [item["email"] for item in body["items"]] == [
        "ana@example.com",
        "boris@example.com",
        "carla@example.com",
    ]


def test_contacts_list_unknown_sort_by_falls_back_to_created_at(client: TestClient):
    """A malicious `sort_by=secret_column` must not leak into the SQL.
    The repository whitelist drops back to `created_at desc`."""
    headers = auth_headers(client, "manager")
    create_contact(client, "ana@example.com")
    create_contact(client, "boris@example.com")

    response = client.get(
        "/api/contacts",
        params={"sort_by": "id; drop table contacts"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_contacts_list_filter_by_origin_system(client: TestClient):
    """`origin_system` filters via external_references. A contact with no
    reference for the requested system must not appear in the page."""
    from app.models.crm import Contact, ExternalReference, ExternalSystem

    ana = create_contact(client, "ana@example.com")
    boris = create_contact(client, "boris@example.com")

    # Wire ana to AgileCRM directly through the SQLAlchemy session; the
    # API doesn't expose a "link to integration account" endpoint yet.
    session_factory = app.dependency_overrides[get_session]
    session_gen = session_factory()
    session = next(session_gen)
    try:
        session.add(
            ExternalReference(
                system=ExternalSystem.AGILECRM,
                external_id="ana-1",
                account_id="acct-1",
                contact_id=session.get(Contact, ana["id"]).id,
            )
        )
        session.commit()
    finally:
        session_gen.close()

    response = client.get(
        "/api/contacts",
        params={"origin_system": "agilecrm"},
        headers=auth_headers(client, "viewer"),
    )
    body = response.json()
    emails = [item["email"] for item in body["items"]]
    assert emails == ["ana@example.com"]
    assert body["total"] == 1
    assert boris["email"] not in emails


def test_contacts_list_filter_by_origin_account_id(client: TestClient):
    """`origin_account_id` narrows the origin filter to one integration
    account. Two AgileCRM accounts must not bleed into each other."""
    from app.models.crm import Contact, ExternalReference, ExternalSystem

    ana = create_contact(client, "ana@example.com")
    boris = create_contact(client, "boris@example.com")

    session_factory = app.dependency_overrides[get_session]
    session_gen = session_factory()
    session = next(session_gen)
    try:
        session.add_all(
            [
                ExternalReference(
                    system=ExternalSystem.AGILECRM,
                    external_id="ana-1",
                    account_id="acct-A",
                    contact_id=session.get(Contact, ana["id"]).id,
                ),
                ExternalReference(
                    system=ExternalSystem.AGILECRM,
                    external_id="boris-1",
                    account_id="acct-B",
                    contact_id=session.get(Contact, boris["id"]).id,
                ),
            ]
        )
        session.commit()
    finally:
        session_gen.close()

    response = client.get(
        "/api/contacts",
        params={"origin_system": "agilecrm", "origin_account_id": "acct-A"},
        headers=auth_headers(client, "viewer"),
    )
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "ana@example.com"


def test_get_contact_detail(client: TestClient):
    contact = create_contact(client)

    response = client.get(f"/api/contacts/{contact['id']}", headers=auth_headers(client, "viewer"))

    assert response.status_code == 200
    assert response.json()["id"] == contact["id"]
    assert response.json()["notes"] == []
    assert response.json()["tasks"] == []


def test_contact_detail_exposes_extended_agilecrm_fields(client: TestClient):
    """`GET /api/contacts/{id}` must surface the enriched columns the
    AgileCRM mapper writes — address parts, lead score, custom fields,
    plus per-reference timestamps / origin_detail / decoded metadata."""
    import json as _json

    from app.models.crm import Contact, ExternalReference, ExternalSystem

    seeded = create_contact(client, "ana@example.com")

    session_factory = app.dependency_overrides[get_session]
    session_gen = session_factory()
    session = next(session_gen)
    try:
        contact = session.get(Contact, seeded["id"])
        assert contact is not None
        contact.address_country = "ES"
        contact.address_country_name = "España"
        contact.address_state = "Madrid"
        contact.address_city = "Madrid"
        contact.lead_score = 42
        contact.custom_fields = _json.dumps({"plan": "gold", "vendor_id": "v-1"})
        session.add(
            ExternalReference(
                system=ExternalSystem.AGILECRM,
                external_id="ext-1",
                account_id="acct-1",
                contact_id=contact.id,
                account_label="ana@example.com",
                external_created_at=datetime(2025, 6, 1, 12, 0, tzinfo=UTC),
                external_updated_at=datetime(2025, 7, 2, 9, 30, tzinfo=UTC),
                origin_detail="form",
                metadata_json=_json.dumps(
                    {"owner": {"id": "99", "email": "agent@example.com"}}
                ),
            )
        )
        session.commit()
    finally:
        session_gen.close()

    response = client.get(
        f"/api/contacts/{seeded['id']}", headers=auth_headers(client, "viewer")
    )

    assert response.status_code == 200
    body = response.json()
    assert body["address_country"] == "ES"
    assert body["address_city"] == "Madrid"
    assert body["lead_score"] == 42
    assert body["custom_fields"] == {"plan": "gold", "vendor_id": "v-1"}

    refs = body["external_refs"]
    assert len(refs) == 1
    ref = refs[0]
    assert ref["system"] == "agilecrm"
    assert ref["account_id"] == "acct-1"
    assert ref["external_id"] == "ext-1"
    assert ref["external_created_at"].startswith("2025-06-01T12:00:00")
    assert ref["external_updated_at"].startswith("2025-07-02T09:30:00")
    assert ref["origin_detail"] == "form"
    assert ref["metadata"] == {"owner": {"id": "99", "email": "agent@example.com"}}


def test_contact_detail_handles_missing_extended_fields(client: TestClient):
    """A contact created manually (no AgileCRM enrichment) must still
    serialise cleanly — every enriched field is null, never a string,
    and `custom_fields` is null rather than '{}'. Prevents the UI from
    rendering "—" for the empty dict case."""
    contact = create_contact(client, "luis@example.com")

    response = client.get(
        f"/api/contacts/{contact['id']}", headers=auth_headers(client, "viewer")
    )

    body = response.json()
    assert body["address_country"] is None
    assert body["lead_score"] is None
    assert body["custom_fields"] is None
    assert body["external_refs"] == []
    assert body["activity_events"] == []


def test_contact_detail_embeds_latest_activity_events(client: TestClient):
    """The detail endpoint embeds the latest 50 events sorted by
    `occurred_at desc`. We seed 3 with deliberately out-of-order dates
    so the test catches a regression in the ORDER BY."""
    from app.models.crm import ActivityEvent

    seeded = create_contact(client, "ana@example.com")
    session_factory = app.dependency_overrides[get_session]
    session_gen = session_factory()
    session = next(session_gen)
    try:
        for idx, occurred in enumerate(
            [
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 3, 1, tzinfo=UTC),
                datetime(2025, 2, 1, tzinfo=UTC),
            ]
        ):
            session.add(
                ActivityEvent(
                    contact_id=seeded["id"],
                    system="agilecrm",
                    account_id="es",
                    external_id=f"ev-{idx}",
                    event_type="EMAIL_SENT",
                    subject=f"Email {idx}",
                    occurred_at=occurred,
                )
            )
        session.commit()
    finally:
        session_gen.close()

    response = client.get(
        f"/api/contacts/{seeded['id']}", headers=auth_headers(client, "viewer")
    )
    body = response.json()
    events = body["activity_events"]
    assert [e["subject"] for e in events] == ["Email 1", "Email 2", "Email 0"]


def test_activity_events_endpoint_paginates(client: TestClient):
    """`GET /api/contacts/{id}/activity-events` returns the wrapped
    page so the UI can render pagination controls without a separate
    /count call."""
    from app.models.crm import ActivityEvent

    seeded = create_contact(client, "ana@example.com")
    session_factory = app.dependency_overrides[get_session]
    session_gen = session_factory()
    session = next(session_gen)
    try:
        for idx in range(5):
            session.add(
                ActivityEvent(
                    contact_id=seeded["id"],
                    system="agilecrm",
                    account_id="es",
                    external_id=f"ev-{idx}",
                    event_type="EMAIL_SENT",
                    subject=f"Email {idx}",
                    occurred_at=datetime(2025, 1, idx + 1, tzinfo=UTC),
                )
            )
        session.commit()
    finally:
        session_gen.close()

    response = client.get(
        f"/api/contacts/{seeded['id']}/activity-events?skip=2&limit=2",
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 2
    assert len(body["items"]) == 2


def test_activity_events_endpoint_returns_404_for_missing_contact(client: TestClient):
    response = client.get(
        "/api/contacts/does-not-exist/activity-events",
        headers=auth_headers(client, "viewer"),
    )
    assert response.status_code == 404


def test_missing_contact_returns_404(client: TestClient):
    response = client.get("/api/contacts/missing", headers=auth_headers(client, "viewer"))

    assert response.status_code == 404
    assert response.json()["detail"] == "Contact not found"


def test_update_and_deactivate_contact(client: TestClient):
    contact = create_contact(client)
    headers = auth_headers(client, "manager")

    updated = client.patch(
        f"/api/contacts/{contact['id']}",
        json={"first_name": "Ana María", "commercial_status": "qualified"},
        headers=headers,
    )
    deactivated = client.patch(f"/api/contacts/{contact['id']}/deactivate", headers=headers)

    assert updated.status_code == 200
    assert updated.json()["first_name"] == "Ana María"
    assert updated.json()["commercial_status"] == "qualified"
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False


def test_user_can_create_note_and_task_for_contact(client: TestClient):
    contact = create_contact(client)
    headers = auth_headers(client, "user")

    note = client.post(
        f"/api/contacts/{contact['id']}/notes", json={"body": "Llamada"}, headers=headers
    )
    task = client.post(
        f"/api/contacts/{contact['id']}/tasks", json={"title": "Enviar propuesta"}, headers=headers
    )

    assert note.status_code == 201
    assert task.status_code == 201
    assert task.json()["status"] == "open"


def test_viewer_cannot_create_note_or_task(client: TestClient):
    contact = create_contact(client)
    headers = auth_headers(client, "viewer")

    note = client.post(
        f"/api/contacts/{contact['id']}/notes", json={"body": "No"}, headers=headers
    )
    task = client.post(
        f"/api/contacts/{contact['id']}/tasks", json={"title": "No"}, headers=headers
    )

    assert note.status_code == 403
    assert task.status_code == 403


def test_reject_note_and_task_for_missing_contact(client: TestClient):
    headers = auth_headers(client, "user")

    note = client.post("/api/contacts/missing/notes", json={"body": "Nota"}, headers=headers)
    task = client.post("/api/contacts/missing/tasks", json={"title": "Enviar"}, headers=headers)

    assert note.status_code == 404
    assert task.status_code == 404


def test_audit_log_records_login_and_crm_actions(client: TestClient):
    headers = auth_headers(client, "admin")
    create_contact(client, "audit@example.com")

    response = client.get("/api/audit-logs", headers=headers)

    assert response.status_code == 200
    actions = {entry["action"] for entry in response.json()}
    assert "auth.login_success" in actions
    assert "contact.created" in actions


def test_change_current_user_password(client: TestClient):
    headers = auth_headers(client, "user")

    changed = client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "ChangedPass123!"},
        headers=headers,
    )
    login_response = client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "ChangedPass123!"}
    )

    assert changed.status_code == 200
    assert login_response.status_code == 200


def test_password_reset_request_and_confirm(client: TestClient):
    requested = client.post(
        "/api/auth/password-reset/request", json={"email": "viewer@example.com"}
    )
    token = requested.json()["reset_token"]

    confirmed = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": token, "new_password": "ResetPass123!Z"},
    )
    login_response = client.post(
        "/api/auth/login", json={"email": "viewer@example.com", "password": "ResetPass123!Z"}
    )

    assert requested.status_code == 200
    assert token
    assert confirmed.status_code == 200
    assert login_response.status_code == 200


def test_admin_can_deactivate_and_reactivate_user(client: TestClient):
    headers = auth_headers(client, "admin")
    user_id = client.get("/api/users", headers=headers).json()[0]["id"]

    deactivated = client.patch(f"/api/users/{user_id}/deactivate", headers=headers)
    reactivated = client.patch(f"/api/users/{user_id}/reactivate", headers=headers)

    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True


def test_admin_can_update_user_password(client: TestClient):
    headers = auth_headers(client, "admin")
    created = client.post(
        "/api/users",
        json={
            "email": "password-target@example.com",
            "full_name": "Password Target",
            "password": "TargetPass123!",
            "role": "viewer",
        },
        headers=headers,
    )

    changed = client.patch(
        f"/api/users/{created.json()['id']}/password",
        json={"new_password": "AdminSetPass123!"},
        headers=headers,
    )
    login_response = client.post(
        "/api/auth/login",
        json={"email": "password-target@example.com", "password": "AdminSetPass123!"},
    )

    assert changed.status_code == 200
    assert login_response.status_code == 200


def test_audit_export_csv_and_json_requires_admin(client: TestClient):
    create_contact(client, "export@example.com")
    admin_headers = auth_headers(client, "admin")
    manager_headers = auth_headers(client, "manager")

    forbidden = client.get("/api/audit-logs/export", headers=manager_headers)
    csv_response = client.get("/api/audit-logs/export?format=csv", headers=admin_headers)
    json_response = client.get("/api/audit-logs/export?format=json", headers=admin_headers)

    assert forbidden.status_code == 403
    assert csv_response.status_code == 200
    assert "contact.created" in csv_response.text
    assert json_response.status_code == 200
    assert any(row["action"] == "contact.created" for row in json_response.json())


def test_openapi_endpoints_live_under_api_prefix(client: TestClient):
    """The reverse proxy routes only `/api/*` to the backend. Swagger,
    ReDoc and the OpenAPI schema must therefore be mounted under that
    prefix; the FastAPI defaults at `/docs` / `/redoc` / `/openapi.json`
    would be swallowed by Next.js."""
    docs = client.get("/api/docs")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()

    redoc = client.get("/api/redoc")
    assert redoc.status_code == 200
    assert "redoc" in redoc.text.lower()

    schema = client.get("/api/openapi.json")
    assert schema.status_code == 200
    body = schema.json()
    assert body.get("openapi", "").startswith("3.")
    # Sanity-check that real routes are present under the documented prefix.
    paths = body.get("paths", {})
    assert "/api/auth/login" in paths

    # The legacy unprefixed paths must NOT respond — Next.js would
    # otherwise believe they exist and produce confusing routing.
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_contacts_count_reflects_active_filter(client: TestClient):
    """`/contacts/count` must return the real DB total — not the
    paginated page size the list endpoint defaults to — and must apply
    the same `is_active=true` default as `/contacts`."""
    headers = auth_headers(client, "manager")

    # Seed 3 active contacts so the count is unambiguous.
    for i in range(3):
        client.post(
            "/api/contacts",
            json={
                "first_name": f"Ana{i}",
                "email": f"ana{i}@example.com",
                "marketing_consent": "granted",
            },
            headers=headers,
        )

    response = client.get("/api/contacts/count", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"total": 3}


def test_contacts_count_skips_inactive_unless_requested(client: TestClient):
    headers = auth_headers(client, "manager")
    body = client.post(
        "/api/contacts",
        json={
            "first_name": "Soft",
            "email": "soft@example.com",
            "marketing_consent": "granted",
        },
        headers=headers,
    ).json()
    # Soft-delete it.
    client.patch(f"/api/contacts/{body['id']}/deactivate", headers=headers)

    # Default: only active rows.
    default = client.get("/api/contacts/count", headers=headers)
    assert default.json() == {"total": 0}

    # `include_inactive=true` brings it back.
    full = client.get(
        "/api/contacts/count?include_inactive=true", headers=headers
    )
    assert full.json() == {"total": 1}


def test_contacts_count_applies_query_filter(client: TestClient):
    headers = auth_headers(client, "manager")
    for idx, name in enumerate(("Ana", "Boris", "Ana")):
        client.post(
            "/api/contacts",
            json={
                "first_name": name,
                "email": f"{name.lower()}{idx}@example.com",
                "marketing_consent": "granted",
            },
            headers=headers,
        )

    filtered = client.get("/api/contacts/count?q=Ana", headers=headers)
    assert filtered.json() == {"total": 2}


def test_companies_count_basic(client: TestClient):
    headers = auth_headers(client, "manager")
    for name in ("Acme", "Globex"):
        client.post(
            "/api/companies",
            json={"name": name},
            headers=headers,
        )

    response = client.get("/api/companies/count", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"total": 2}


def test_count_endpoints_require_viewer_or_above(client: TestClient):
    """Anonymous requests bounce with 401. Authenticated `viewer` role
    succeeds — the count is non-sensitive aggregate data."""
    anon = client.get("/api/contacts/count")
    assert anon.status_code == 401

    viewer = client.get(
        "/api/contacts/count", headers=auth_headers(client, "viewer")
    )
    assert viewer.status_code == 200
