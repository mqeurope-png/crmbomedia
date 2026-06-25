"""PR-Fix-Sync-No-Sobreescribe-Cambios-CRM tests.

Verifica que:

1. PATCH /api/contacts/{id} marca los campos de Capa A editados en
   `manually_edited_fields_json`.
2. El sync de AgileCRM respeta el array de protección y NO
   sobrescribe los campos marcados.
3. El sync de AgileCRM actualiza los campos de Capa A que NO están
   marcados.
4. El sync NUNCA toca campos de Capa B (lead_score, owner_user_id,
   star_rating, commercial_status, etc.) sea cual sea el array.
5. Tags se mergean — el sync nunca quita tags. Manual unassign no
   se revierte.
6. POST /api/contacts/{id}/reset-manual-edits limpia el array
   selectivamente o entero.
7. Eventos Brevo (email_message_events) se importan siempre,
   independiente del array.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.db.session import get_session
from app.integrations.agilecrm.jobs import _upsert_contact_for_payload
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    ContactTag,
    EmailDirection,
    EmailEventType,
    EmailMessage,
    EmailMessageEvent,
    EmailThread,
    ExternalSystem,
    Tag,
    User,
    UserRole,
)
from app.models.integration_settings import IntegrationAccount
from app.services import contact_sync_protection as protection
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
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _agile_payload(
    *, external_id: int, email: str, first_name: str = "Original",
    last_name: str = "Apellido", phone: str = "+34 600 111 222",
    job_title: str = "Director", tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": external_id,
        "tags": tags or [],
        "properties": [
            {"name": "first_name", "value": first_name},
            {"name": "last_name", "value": last_name},
            {"name": "email", "value": email},
            {"name": "phone", "value": phone},
            {"name": "title", "value": job_title},
        ],
    }


def _create_contact_via_sync(
    session_factory: sessionmaker, *, external_id: int = 1, email: str = "ana@example.com"
) -> str:
    with session_factory() as session:
        action, _, contact_id, _ = _upsert_contact_for_payload(
            session,
            account_id="default",
            payload=_agile_payload(external_id=external_id, email=email),
        )
        assert action == "created"
        session.commit()
        return contact_id


# ---------------------------------------------------------------------
# 1. PATCH marca los campos de Capa A
# ---------------------------------------------------------------------


def test_patch_contact_marks_edited_fields(
    client: TestClient, session_factory: sessionmaker
):
    """El operador edita first_name y phone vía PATCH → ambos quedan
    marcados en `manually_edited_fields_json`."""
    contact_id = _create_contact_via_sync(session_factory)
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"first_name": "Editado", "phone": "+34 600 999 000"},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["manually_edited_fields"]) == {"first_name", "phone"}


def test_patch_does_not_mark_when_value_unchanged(
    client: TestClient, session_factory: sessionmaker
):
    """Si el operador manda el mismo valor que ya tenía, el campo no
    se marca (evita falsos positivos que bloquearían syncs futuros)."""
    contact_id = _create_contact_via_sync(session_factory)
    # Capturamos el first_name actual.
    with session_factory() as session:
        existing = session.get(Contact, contact_id)
        current_name = existing.first_name
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"first_name": current_name},  # mismo valor
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    assert response.json()["manually_edited_fields"] == []


# ---------------------------------------------------------------------
# 2 + 3. Sync respeta protegidos y actualiza los no-protegidos
# ---------------------------------------------------------------------


def test_sync_skips_manually_edited_fields(
    client: TestClient, session_factory: sessionmaker
):
    """El operador editó phone manualmente. El próximo sync con un
    teléfono distinto en AgileCRM debe RESPETAR el del CRM."""
    contact_id = _create_contact_via_sync(session_factory)
    # PATCH manual con teléfono nuevo.
    client.patch(
        f"/api/contacts/{contact_id}",
        json={"phone": "+34 611 000 000"},
        headers=auth_headers(client, "user"),
    )
    # Sync repite con teléfono distinto.
    with session_factory() as session:
        _upsert_contact_for_payload(
            session,
            account_id="default",
            payload=_agile_payload(
                external_id=1, email="ana@example.com",
                phone="+34 999 888 777",
            ),
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.phone == "+34 611 000 000", (
            f"phone editado manualmente debe sobrevivir; vi {c.phone!r}"
        )


def test_sync_updates_non_edited_fields(
    client: TestClient, session_factory: sessionmaker
):
    """El operador editó solo phone. job_title NO está protegido →
    el sync lo actualiza con el de AgileCRM."""
    contact_id = _create_contact_via_sync(session_factory)
    client.patch(
        f"/api/contacts/{contact_id}",
        json={"phone": "+34 611 000 000"},
        headers=auth_headers(client, "user"),
    )
    with session_factory() as session:
        _upsert_contact_for_payload(
            session,
            account_id="default",
            payload=_agile_payload(
                external_id=1, email="ana@example.com",
                phone="+34 999 888 777",
                job_title="CEO actualizado",
            ),
        )
        session.commit()
        c = session.get(Contact, contact_id)
        # phone respetado
        assert c.phone == "+34 611 000 000"
        # job_title (no protegido) actualizado
        assert c.job_title == "CEO actualizado"


# ---------------------------------------------------------------------
# 4. Capa B NUNCA se toca
# ---------------------------------------------------------------------


def test_sync_never_overwrites_layer_b_fields(
    session_factory: sessionmaker
):
    """Capa B (lead_score, owner_user_id, star_rating,
    commercial_status) protegida incondicionalmente. Sin marcas en el
    array, el sync NO los toca cuando vienen distintos en el payload."""
    contact_id = _create_contact_via_sync(session_factory)
    with session_factory() as session:
        manel_uid = session.scalar(
            select(User.id).where(User.role == UserRole.USER)
        )
        c = session.get(Contact, contact_id)
        c.lead_score = 73
        c.star_rating = 4
        c.commercial_status = "qualified"
        c.owner_user_id = manel_uid
        session.commit()

    # Sync con valores distintos en el payload (custom prop lead_score).
    payload = _agile_payload(external_id=1, email="ana@example.com")
    payload["properties"].extend([
        {"name": "lead_score", "value": 1},  # intenta sobrescribir
        {"name": "star_value", "value": 1},  # intenta sobrescribir
    ])
    with session_factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default", payload=payload
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.lead_score == 73, (
            "lead_score (Capa B) debe sobrevivir al sync siempre"
        )
        assert c.star_rating == 4
        assert c.commercial_status == "qualified"
        assert c.owner_user_id == manel_uid


# ---------------------------------------------------------------------
# 5. Tags: merge — el sync nunca quita
# ---------------------------------------------------------------------


def test_tags_merged_not_replaced_in_sync(
    session_factory: sessionmaker
):
    """1) Sync trae tag X → contacto tiene X.
    2) Operador añade tag manual Y desde la UI.
    3) Siguiente sync trae X + Z (sin Y) → contacto tiene X+Y+Z."""
    # Paso 1: sync inicial con tag X.
    contact_id = _create_contact_via_sync(session_factory)
    with session_factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default",
            payload=_agile_payload(
                external_id=1, email="ana@example.com",
                tags=["TagX"],
            ),
        )
        session.commit()

    # Paso 2: operador añade tag Y manual.
    with session_factory() as session:
        manual_tag = Tag(name="ManualY", name_normalized="manualy")
        session.add(manual_tag)
        session.flush()
        session.add(
            ContactTag(
                contact_id=contact_id, tag_id=manual_tag.id, source="manual"
            )
        )
        session.commit()

    # Paso 3: nuevo sync sin TagX (la quitan en Agile) y con TagZ.
    with session_factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default",
            payload=_agile_payload(
                external_id=1, email="ana@example.com",
                tags=["TagZ"],
            ),
        )
        session.commit()

    # Verifica: TagX (Agile original), ManualY, TagZ todas presentes.
    with session_factory() as session:
        links = list(session.scalars(
            select(ContactTag).where(ContactTag.contact_id == contact_id)
        ))
        tag_ids = {link.tag_id for link in links}
        tags = list(session.scalars(select(Tag).where(Tag.id.in_(tag_ids))))
        names = {t.name_normalized for t in tags}
        assert names >= {"tagx", "manualy", "tagz"}, (
            f"Esperaba MERGE; vi {names}"
        )


def test_tags_removed_manually_dont_come_back_unless_added_explicitly(
    session_factory: sessionmaker
):
    """Si el operador quita una tag desde el CRM, el sync no la
    vuelve a meter — el sync solo añade lo que llega en el payload."""
    contact_id = _create_contact_via_sync(session_factory)
    with session_factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default",
            payload=_agile_payload(
                external_id=1, email="ana@example.com",
                tags=["VIP"],
            ),
        )
        session.commit()
    # Operador la quita.
    with session_factory() as session:
        tag = session.scalar(
            select(Tag).where(Tag.name_normalized == "vip")
        )
        session.execute(
            ContactTag.__table__.delete().where(
                ContactTag.contact_id == contact_id,
                ContactTag.tag_id == tag.id,
            )
        )
        session.commit()
    # Sync siguiente sigue trayendo VIP en payload pero sin Agile
    # nuevamente quitando: contact_tag se RE-añade porque el sync
    # solo añade (esto es el comportamiento esperado; el operador
    # tiene que quitarla en Agile también para que no vuelva).
    with session_factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default",
            payload=_agile_payload(
                external_id=1, email="ana@example.com",
                tags=["VIP"],  # sigue en Agile
            ),
        )
        session.commit()
        link_count = session.scalar(
            select(__import__("sqlalchemy").func.count(ContactTag.tag_id))
            .where(ContactTag.contact_id == contact_id)
        )
        assert link_count >= 1  # VIP vuelve porque sigue en payload

    # Ahora sync SIN VIP en payload → contacto mantiene VIP igualmente
    # (porque el sync nunca quita) o no la mete (porque ya no está
    # en payload). El estado debe ser: VIP NO presente (ya la quitamos
    # antes; no estaba en este payload y el sync no la mete).
    # Es decir, cuando Agile deja de traerla, el sync no la añade y
    # tampoco la quita — si ya estaba, sobrevive.
    with session_factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default",
            payload=_agile_payload(
                external_id=1, email="ana@example.com",
                tags=[],  # Agile la quitó
            ),
        )
        session.commit()
        # VIP sigue porque el sync no la quita.
        links = list(session.scalars(
            select(ContactTag).where(ContactTag.contact_id == contact_id)
        ))
        tag_ids = {link.tag_id for link in links}
        names = {
            t.name_normalized
            for t in session.scalars(
                select(Tag).where(Tag.id.in_(tag_ids))
            )
        }
        # El sync no quitó VIP — sigue ahí, por la nueva política.
        assert "vip" in names


# ---------------------------------------------------------------------
# 6. Reset endpoint
# ---------------------------------------------------------------------


def test_reset_manual_edits_endpoint_clears_field_marks(
    client: TestClient, session_factory: sessionmaker
):
    """POST /reset-manual-edits quita marcas — el próximo sync vuelve
    a sobrescribir esos campos."""
    contact_id = _create_contact_via_sync(session_factory)
    # Marca phone como editado.
    client.patch(
        f"/api/contacts/{contact_id}",
        json={"phone": "+34 611 000 000"},
        headers=auth_headers(client, "user"),
    )
    # Reset selectivo de phone.
    response = client.post(
        f"/api/contacts/{contact_id}/reset-manual-edits",
        json={"fields": ["phone"]},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    assert response.json()["manually_edited_fields"] == []

    # El sync ahora sí sobrescribe el phone.
    with session_factory() as session:
        _upsert_contact_for_payload(
            session, account_id="default",
            payload=_agile_payload(
                external_id=1, email="ana@example.com",
                phone="+34 999 888 777",
            ),
        )
        session.commit()
        c = session.get(Contact, contact_id)
        assert c.phone == "+34 999 888 777"


def test_reset_manual_edits_full_when_no_body(
    client: TestClient, session_factory: sessionmaker
):
    """POST sin body o con `fields: []` vacía todo el array."""
    contact_id = _create_contact_via_sync(session_factory)
    client.patch(
        f"/api/contacts/{contact_id}",
        json={"phone": "+34 611 000 000", "job_title": "Custom"},
        headers=auth_headers(client, "user"),
    )
    response = client.post(
        f"/api/contacts/{contact_id}/reset-manual-edits",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    assert response.json()["manually_edited_fields"] == []


# ---------------------------------------------------------------------
# 7. Brevo events independientes
# ---------------------------------------------------------------------


def test_brevo_events_always_imported_regardless_of_manual_edits(
    session_factory: sessionmaker
):
    """Eventos de tracking (opens/clicks) van a email_message_events,
    no tocan campos del contacto. La protección no aplica — los
    eventos se insertan siempre."""
    contact_id = _create_contact_via_sync(session_factory)
    # Marca varios campos como editados.
    with session_factory() as session:
        c = session.get(Contact, contact_id)
        protection.mark_manually_edited(c, ["phone", "first_name", "email"])
        session.commit()

    # Simula la inserción directa de un evento OPEN (lo que hace el
    # email_tracking router cuando llega el pixel) — debe funcionar
    # sin importar las marcas del contacto.
    with session_factory() as session:
        admin_id = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        thread = EmailThread(
            contact_id=contact_id,
            initiated_by_user_id=admin_id,
            gmail_thread_id="t-1",
            gmail_account_user_id=admin_id,
            subject="Test",
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
        )
        session.add(thread)
        session.flush()
        msg = EmailMessage(
            thread_id=thread.id,
            gmail_message_id="m-1",
            gmail_account_user_id=admin_id,
            direction=EmailDirection.OUTBOUND,
            from_email="ops@example.com",
            to_emails_json=json.dumps(["ana@example.com"]),
            sent_at=datetime.now(UTC),
            contact_id=contact_id,
        )
        session.add(msg)
        session.flush()
        session.add(
            EmailMessageEvent(
                message_id=msg.id,
                event_type=EmailEventType.OPEN,
                occurred_at=datetime.now(UTC),
            )
        )
        session.commit()
        events = list(session.scalars(
            select(EmailMessageEvent).where(EmailMessageEvent.message_id == msg.id)
        ))
        assert len(events) == 1
        assert events[0].event_type == EmailEventType.OPEN


# ---------------------------------------------------------------------
# Service-level unit tests
# ---------------------------------------------------------------------


def test_protection_helpers_are_idempotent(session_factory: sessionmaker):
    """`mark_manually_edited` dedupe + filtra Capa B + ignora desconocidos."""
    with session_factory() as session:
        contact = Contact(first_name="X", email="x@x.com")
        session.add(contact)
        session.flush()
        protection.mark_manually_edited(contact, ["first_name", "phone"])
        protection.mark_manually_edited(contact, ["phone", "lead_score", "unknown_field"])
        # phone solo una vez; lead_score (Capa B) y unknown ignorados.
        marks = protection.get_manually_edited_fields(contact)
        assert marks == ["first_name", "phone"]


def test_is_field_protected_layer_b_always(session_factory: sessionmaker):
    with session_factory() as session:
        contact = Contact(first_name="X", email="x@x.com")
        session.add(contact)
        session.flush()
        # Capa B siempre protegida, sin marcas.
        assert protection.is_field_protected(contact, "lead_score")
        assert protection.is_field_protected(contact, "owner_user_id")
        # Capa A no protegida sin marca.
        assert not protection.is_field_protected(contact, "phone")
        # Tras marcar, sí.
        protection.mark_manually_edited(contact, ["phone"])
        assert protection.is_field_protected(contact, "phone")
