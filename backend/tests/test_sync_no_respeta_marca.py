"""PR-Fix-Sync-No-Respeta-Marca tests.

Bart reportó (2026-06-25) que tras PR #230 + #231 el sync de
AgileCRM seguía sobreescribiendo lead_score del contacto Marny aun
estando marcado en `manually_edited_fields_json`. La causa
probable fue deployment-only (worker-sync no recreado tras el
deploy), pero igualmente añadimos batería de regresión que cubre
las 3 ramas del upsert (existing-ref, email-consolidation,
brand-new) para Agile y Brevo. Si alguna versión futura del
código regresa el bug, estos tests caen inmediatamente.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.db.session import get_session
from app.integrations.agilecrm.jobs import _upsert_contact_for_payload
from app.integrations.brevo.jobs import upsert_brevo_contact
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    ExternalReference,
    ExternalSystem,
    User,
    UserRole,
)
from app.models.integration_settings import IntegrationAccount
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sf() as seed:
        seed_test_users(seed)
        seed.add(
            IntegrationAccount(
                system=ExternalSystem.AGILECRM,
                account_id="default",
                display_name="AgileCRM default",
                enabled=True,
                credential_status="configured",
                api_key_encrypted=crypto.encrypt("ops@example.com:secret"),
            )
        )
        seed.add(
            IntegrationAccount(
                system=ExternalSystem.BREVO,
                account_id="main",
                display_name="Brevo main",
                enabled=True,
            )
        )
        seed.commit()
    yield sf
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _agile_payload(
    *,
    external_id: int,
    email: str,
    lead_score: int | None = None,
    star_value: int | None = None,
    phone: str = "+34 600 111 222",
    first_name: str = "Marny",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": external_id,
        "tags": [],
        "properties": [
            {"name": "first_name", "value": first_name},
            {"name": "email", "value": email},
            {"name": "phone", "value": phone},
        ],
    }
    if lead_score is not None:
        payload["properties"].append(
            {"name": "lead_score", "value": lead_score}
        )
    if star_value is not None:
        payload["star_value"] = star_value
    return payload


def _brevo_payload(
    *,
    external_id: int,
    email: str,
    lead_score: int | None = None,
    phone: str = "+34 600 111 222",
    first_name: str = "Marny",
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "FIRSTNAME": first_name,
        "SMS": phone,
    }
    if lead_score is not None:
        attrs["LEAD_SCORE"] = lead_score
    return {
        "id": external_id,
        "email": email,
        "emailBlacklisted": False,
        "attributes": attrs,
        "listIds": [],
    }


# ---------------------------------------------------------------------
# CASO MARNY EXACTO — Agile existing-ref
# ---------------------------------------------------------------------


def test_agilecrm_sync_never_overwrites_marked_lead_score(
    client: TestClient, factory: sessionmaker
):
    """Reproduce el caso exacto de Marny (Bart 2026-06-25):
    1) Sync inicial crea contacto con lead_score=5 (valor Agile).
    2) Operador edita lead_score=73 vía PATCH desde el modal.
       `manually_edited_fields_json` queda `["lead_score"]`.
    3) Sync periódico vuelve a llegar con el mismo lead_score=5 de
       Agile (el operador no lo cambió en Agile, solo en CRM).
    4) Resultado esperado: lead_score sigue 73 + marks intactos."""
    # 1.
    payload = _agile_payload(external_id=999, email="marny@example.com", lead_score=5)
    with factory() as session:
        action, _, contact_id, _ = _upsert_contact_for_payload(
            session, account_id="default", payload=payload
        )
        assert action == "created"
        session.commit()

    # 2.
    resp = client.patch(
        f"/api/contacts/{contact_id}",
        json={"lead_score": 73},
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    assert resp.json()["lead_score"] == 73
    assert resp.json()["manually_edited_fields"] == ["lead_score"]

    # 3 + 4.
    with factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default", payload=payload
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.lead_score == 73, (
            f"BUG REGRESO: lead_score sobreescrito {c.lead_score} (esperaba 73)"
        )
        # Marks intactos (sync no las modifica).
        assert c.manually_edited_fields_json
        assert "lead_score" in json.loads(c.manually_edited_fields_json)


def test_agilecrm_sync_never_overwrites_marked_star_rating(
    client: TestClient, factory: sessionmaker
):
    """Mismo patrón pero con star_rating (también Capa B)."""
    payload = _agile_payload(
        external_id=999, email="m2@example.com", star_value=1
    )
    with factory() as session:
        _, _, contact_id, _ = _upsert_contact_for_payload(
            session, account_id="default", payload=payload
        )
        session.commit()

    client.patch(
        f"/api/contacts/{contact_id}",
        json={"star_rating": 5},
        headers=auth_headers(client, "user"),
    )

    with factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default", payload=payload
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.star_rating == 5


def test_agilecrm_sync_never_overwrites_marked_owner(
    client: TestClient, factory: sessionmaker
):
    """owner_user_id es Capa B. Tampoco se pisa."""
    payload = _agile_payload(external_id=999, email="m3@example.com")
    with factory() as session:
        _, _, contact_id, _ = _upsert_contact_for_payload(
            session, account_id="default", payload=payload
        )
        session.commit()
        manel_uid = session.scalar(
            select(User.id).where(User.role == UserRole.USER)
        )

    client.patch(
        f"/api/contacts/{contact_id}",
        json={"owner_id": manel_uid},
        headers=auth_headers(client, "user"),
    )

    with factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default", payload=payload
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.owner_user_id == manel_uid


def test_agilecrm_sync_respects_marked_phone_capa_a(
    client: TestClient, factory: sessionmaker
):
    """Phone es Capa A — protegido solo si está marcado. Bart edita
    teléfono manualmente → marks=[`phone`] → sync no lo pisa."""
    payload_initial = _agile_payload(
        external_id=999, email="m4@example.com", phone="+34 600 111 222"
    )
    with factory() as session:
        _, _, contact_id, _ = _upsert_contact_for_payload(
            session, account_id="default", payload=payload_initial
        )
        session.commit()

    client.patch(
        f"/api/contacts/{contact_id}",
        json={
            "phones": [
                {
                    "number": "+34 611 999 999",
                    "label": "Móvil",
                    "is_primary": True,
                }
            ]
        },
        headers=auth_headers(client, "user"),
    )

    # Sync periódico: Agile sigue trayendo el teléfono viejo, no debe pisar.
    with factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default", payload=payload_initial
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.phone == "+34 611 999 999"


def test_agilecrm_sync_updates_unprotected_fields_normally(
    factory: sessionmaker,
):
    """Regresión negativa: si el operador NO edita nada, el sync
    actualiza first_name (Capa A no marcada) sin problemas."""
    payload1 = _agile_payload(
        external_id=999,
        email="m5@example.com",
        first_name="Original",
    )
    with factory() as session:
        _, _, contact_id, _ = _upsert_contact_for_payload(
            session, account_id="default", payload=payload1
        )
        session.commit()

    payload2 = _agile_payload(
        external_id=999,
        email="m5@example.com",
        first_name="Cambiado en Agile",
    )
    with factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default", payload=payload2
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.first_name == "Cambiado en Agile"


# ---------------------------------------------------------------------
# Misma batería para Brevo
# ---------------------------------------------------------------------


def test_brevo_sync_never_overwrites_marked_lead_score(
    client: TestClient, factory: sessionmaker
):
    payload = _brevo_payload(external_id=1, email="b1@example.com", lead_score=5)
    with factory() as session:
        action, contact_id = upsert_brevo_contact(
            session,
            account_id="main",
            payload=payload,
            list_names={},
        )
        assert action == "created"
        session.commit()

    client.patch(
        f"/api/contacts/{contact_id}",
        json={"lead_score": 73},
        headers=auth_headers(client, "user"),
    )

    with factory() as session:
        upsert_brevo_contact(
            session,
            account_id="main",
            payload=payload,
            list_names={},
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.lead_score == 73


def test_brevo_sync_never_overwrites_marked_star_rating(
    client: TestClient, factory: sessionmaker
):
    payload = _brevo_payload(external_id=2, email="b2@example.com")
    with factory() as session:
        _, contact_id = upsert_brevo_contact(
            session,
            account_id="main",
            payload=payload,
            list_names={},
        )
        session.commit()

    client.patch(
        f"/api/contacts/{contact_id}",
        json={"star_rating": 5},
        headers=auth_headers(client, "user"),
    )

    with factory() as session:
        upsert_brevo_contact(
            session,
            account_id="main",
            payload=payload,
            list_names={},
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.star_rating == 5


def test_brevo_sync_never_overwrites_marked_owner(
    client: TestClient, factory: sessionmaker
):
    payload = _brevo_payload(external_id=3, email="b3@example.com")
    with factory() as session:
        _, contact_id = upsert_brevo_contact(
            session,
            account_id="main",
            payload=payload,
            list_names={},
        )
        session.commit()
        manel_uid = session.scalar(
            select(User.id).where(User.role == UserRole.USER)
        )

    client.patch(
        f"/api/contacts/{contact_id}",
        json={"owner_id": manel_uid},
        headers=auth_headers(client, "user"),
    )

    with factory() as session:
        upsert_brevo_contact(
            session,
            account_id="main",
            payload=payload,
            list_names={},
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.owner_user_id == manel_uid


def test_brevo_sync_respects_marked_phone(
    client: TestClient, factory: sessionmaker
):
    payload_initial = _brevo_payload(
        external_id=4, email="b4@example.com", phone="+34 600 111 222"
    )
    with factory() as session:
        _, contact_id = upsert_brevo_contact(
            session,
            account_id="main",
            payload=payload_initial,
            list_names={},
        )
        session.commit()

    client.patch(
        f"/api/contacts/{contact_id}",
        json={
            "phones": [
                {
                    "number": "+34 611 999 999",
                    "label": "Móvil",
                    "is_primary": True,
                }
            ]
        },
        headers=auth_headers(client, "user"),
    )

    with factory() as session:
        upsert_brevo_contact(
            session,
            account_id="main",
            payload=payload_initial,
            list_names={},
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.phone == "+34 611 999 999"


def test_brevo_sync_updates_unprotected_fields_normally(
    factory: sessionmaker,
):
    payload1 = _brevo_payload(
        external_id=5,
        email="b5@example.com",
        first_name="Original",
    )
    with factory() as session:
        _, contact_id = upsert_brevo_contact(
            session,
            account_id="main",
            payload=payload1,
            list_names={},
        )
        session.commit()

    payload2 = _brevo_payload(
        external_id=5,
        email="b5@example.com",
        first_name="Cambiado en Brevo",
    )
    with factory() as session:
        upsert_brevo_contact(
            session,
            account_id="main",
            payload=payload2,
            list_names={},
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.first_name == "Cambiado en Brevo"


# ---------------------------------------------------------------------
# Email consolidation — la rama menos cubierta
# ---------------------------------------------------------------------


def test_agilecrm_sync_consolidation_path_respects_marks(
    client: TestClient, factory: sessionmaker
):
    """Caso "el mismo email aparece en otra cuenta Agile y se
    consolida bajo el contacto existente". Esa rama llama también a
    `_apply_update` con allow_email_overwrite=False — debe respetar
    Capa B + la marca de Capa A."""
    # Primera cuenta + sync inicial.
    with factory() as session:
        session.add(
            IntegrationAccount(
                system=ExternalSystem.AGILECRM,
                account_id="secondary",
                display_name="Agile secondary",
                enabled=True,
                credential_status="configured",
                api_key_encrypted=crypto.encrypt("ops@example.com:secret-uk"),
            )
        )
        session.commit()

    p1 = _agile_payload(external_id=10, email="multi@example.com", lead_score=5)
    with factory() as session:
        _, _, contact_id, _ = _upsert_contact_for_payload(
            session, account_id="default", payload=p1
        )
        session.commit()

    # Operador marca lead_score y phone.
    client.patch(
        f"/api/contacts/{contact_id}",
        json={
            "lead_score": 88,
            "phones": [
                {"number": "+34 600 000 001", "label": "Móvil", "is_primary": True}
            ],
        },
        headers=auth_headers(client, "user"),
    )

    # Mismo email aparece en la cuenta `secondary` con lead_score=1.
    p2 = _agile_payload(
        external_id=99, email="multi@example.com", lead_score=1
    )
    with factory() as session:
        _upsert_contact_for_payload(
            session, account_id="secondary", payload=p2
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.lead_score == 88, (
            "Consolidation path NO debe pisar lead_score (Capa B)"
        )
        # Phone también respetado.
        assert c.phone == "+34 600 000 001"
        # Y debe quedar una segunda ExternalReference (mismo contact).
        refs = list(
            session.scalars(
                select(ExternalReference).where(
                    ExternalReference.contact_id == contact_id
                )
            )
        )
        assert len(refs) == 2
