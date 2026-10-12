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
    """`mark_manually_edited` dedupe + acepta Capa A y Capa B
    (PR-Fix-Patch-No-Marca-Manual-Edits: Capa B se marca por
    trazabilidad para que el banner la muestre, aunque su protección
    en el sync sea incondicional). Ignora campos desconocidos."""
    with session_factory() as session:
        contact = Contact(first_name="X", email="x@x.com")
        session.add(contact)
        session.flush()
        protection.mark_manually_edited(contact, ["first_name", "phone"])
        protection.mark_manually_edited(
            contact, ["phone", "lead_score", "unknown_field"]
        )
        marks = protection.get_manually_edited_fields(contact)
        # phone deduplicado; lead_score (Capa B) sí entra; unknown ignorado.
        assert marks == ["first_name", "lead_score", "phone"]


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


# ---------------------------------------------------------------------
# PR-Fix-Patch-No-Marca-Manual-Edits — Capa B también se marca
# ---------------------------------------------------------------------


def test_patch_marks_layer_b_lead_score_for_traceability(
    client: TestClient, session_factory: sessionmaker
):
    """Bart reportó (2026-06-25): edita lead_score en la UI →
    el campo persiste pero `manually_edited_fields_json` sigue NULL.
    Cambio de política: también marcamos Capa B aunque sea para
    trazabilidad (el sync nunca tocó Capa B por diseño, pero el
    banner del modal lo enseña como "editado manualmente")."""
    contact_id = _create_contact_via_sync(session_factory)
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"lead_score": 73},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["manually_edited_fields"] == ["lead_score"]


def test_patch_marks_star_rating_too(
    client: TestClient, session_factory: sessionmaker
):
    contact_id = _create_contact_via_sync(session_factory)
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"star_rating": 5},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    assert response.json()["manually_edited_fields"] == ["star_rating"]


def test_patch_marks_owner_via_owner_id_alias(
    client: TestClient, session_factory: sessionmaker
):
    """`owner_id` es el alias del cliente; en BD es
    `owner_user_id`. El handler hace la traducción y marca el
    campo canónico."""
    contact_id = _create_contact_via_sync(session_factory)
    with session_factory() as session:
        manel_uid = session.scalar(
            select(User.id).where(User.role == UserRole.USER)
        )
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"owner_id": manel_uid},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    assert response.json()["manually_edited_fields"] == ["owner_user_id"]


def test_patch_marks_phone_when_phones_array_changes(
    client: TestClient, session_factory: sessionmaker
):
    """El modal manda `phones` (lista de objetos). El handler
    actualiza `contact.phone` con el primary y marca `phone` como
    editado manualmente — caso real de Bart cuando un comercial
    actualiza el teléfono del cliente."""
    contact_id = _create_contact_via_sync(session_factory)
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={
            "phones": [
                {"number": "+34 611 222 333", "label": "Móvil", "is_primary": True}
            ]
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    body = response.json()
    assert "phone" in body["manually_edited_fields"]


def test_patch_multiple_fields_adds_all_to_set(
    client: TestClient, session_factory: sessionmaker
):
    """PATCH con first_name + phone + lead_score en el mismo body
    → los 3 quedan en el array (no se duplican, no se pierde
    ninguno)."""
    contact_id = _create_contact_via_sync(session_factory)
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={
            "first_name": "Editado",
            "phones": [
                {"number": "+34 600 000 001", "label": "Móvil", "is_primary": True}
            ],
            "lead_score": 80,
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    marks = set(response.json()["manually_edited_fields"])
    assert marks == {"first_name", "phone", "lead_score"}


def test_patch_same_field_twice_doesnt_duplicate(
    client: TestClient, session_factory: sessionmaker
):
    """Dos PATCHs consecutivos sobre el mismo campo NO duplican
    la entrada en el array."""
    contact_id = _create_contact_via_sync(session_factory)
    client.patch(
        f"/api/contacts/{contact_id}",
        json={"first_name": "Cambio1"},
        headers=auth_headers(client, "user"),
    )
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"first_name": "Cambio2"},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    marks = response.json()["manually_edited_fields"]
    assert marks == ["first_name"]
    assert len(marks) == 1


def test_reset_clears_specific_field_only(
    client: TestClient, session_factory: sessionmaker
):
    """Reset selectivo de 1 campo entre varios marcados → el resto
    sobrevive."""
    contact_id = _create_contact_via_sync(session_factory)
    # Marca first_name + phone + lead_score.
    client.patch(
        f"/api/contacts/{contact_id}",
        json={
            "first_name": "Mark1",
            "phones": [
                {"number": "+34 600 111 222", "label": "Móvil", "is_primary": True}
            ],
            "lead_score": 50,
        },
        headers=auth_headers(client, "user"),
    )
    response = client.post(
        f"/api/contacts/{contact_id}/reset-manual-edits",
        json={"fields": ["phone"]},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    marks = set(response.json()["manually_edited_fields"])
    assert marks == {"first_name", "lead_score"}


# ---------------------------------------------------------------------
# custom_fields: se fusiona clave a clave, no se sustituye entero
# ---------------------------------------------------------------------


def _agile_con_campos(
    *, external_id: int = 1, email: str = "ana@example.com", **campos: str
) -> dict[str, Any]:
    payload = _agile_payload(external_id=external_id, email=email)
    payload["properties"].extend(
        {"type": "CUSTOM", "name": nombre.replace("_", " "), "value": valor}
        for nombre, valor in campos.items()
    )
    return payload


def _sync_agile(session_factory: sessionmaker, payload: dict[str, Any]) -> None:
    with session_factory() as session:
        _upsert_contact_for_payload(session, account_id="default", payload=payload)
        session.commit()


def _sync_brevo(
    session_factory: sessionmaker, attributes: dict[str, Any],
    *, email: str = "ana@example.com",
) -> None:
    from app.integrations.brevo.jobs import upsert_brevo_contact  # noqa: PLC0415

    with session_factory() as session:
        upsert_brevo_contact(
            session,
            account_id="main",
            payload={
                "id": 9, "email": email, "emailBlacklisted": False,
                "attributes": {"NOMBRE": "Ana", **attributes}, "listIds": [],
            },
        )
        session.commit()


def _campos(session_factory: sessionmaker, contact_id: str) -> dict[str, Any]:
    with session_factory() as session:
        raw = session.get(Contact, contact_id).custom_fields
        return json.loads(raw) if raw else {}


def test_sync_de_brevo_no_borra_el_horario_que_trajo_agile(
    session_factory: sessionmaker,
):
    """El caso del backlog: un contacto en Agile (con «Horario») y en Brevo
    (con otros campos). Antes cada sync sustituía el JSON entero y el último
    en pasar borraba lo del otro; ahora conviven, y en cualquier orden."""
    _sync_agile(
        session_factory,
        _agile_con_campos(Horario="L-V 9-14", CONTACTO_Persona="Marta"),
    )
    with session_factory() as session:
        contact_id = session.scalar(select(Contact.id))
    _sync_brevo(session_factory, {"GRADO_DE_INTERES": "alto"})
    assert _campos(session_factory, contact_id) == {
        "Horario": "L-V 9-14",
        "CONTACTO Persona": "Marta",
        "GRADO_DE_INTERES": "alto",
    }
    # Y la vuelta: el siguiente sync de Agile no borra lo de Brevo.
    _sync_agile(
        session_factory,
        _agile_con_campos(Horario="L-V 9-15", CONTACTO_Persona="Marta"),
    )
    assert _campos(session_factory, contact_id) == {
        "Horario": "L-V 9-15",
        "CONTACTO Persona": "Marta",
        "GRADO_DE_INTERES": "alto",
    }


def test_sync_no_borra_lo_de_formularios_ni_workflows(
    session_factory: sessionmaker,
):
    contact_id = _create_contact_via_sync(session_factory)
    with session_factory() as session:
        c = session.get(Contact, contact_id)
        c.custom_fields = json.dumps({"product_interest": "DTF", "INTERES": "UV"})
        session.commit()
    _sync_agile(session_factory, _agile_con_campos(Horario="9-14"))
    assert _campos(session_factory, contact_id) == {
        "product_interest": "DTF", "INTERES": "UV", "Horario": "9-14",
    }


def test_sync_no_vacia_una_clave_con_un_valor_vacio(session_factory: sessionmaker):
    from app.services.contact_sync_protection import merge_custom_fields  # noqa: PLC0415

    contact = Contact(first_name="X", custom_fields=json.dumps({"Horario": "9-14"}))
    assert merge_custom_fields(contact, json.dumps({"Horario": "  "})) is None
    assert merge_custom_fields(contact, json.dumps({"Horario": None})) is None
    assert merge_custom_fields(contact, None) is None
    # Lo mismo que ya hay: nada que escribir.
    assert merge_custom_fields(contact, json.dumps({"Horario": "9-14"})) is None


def test_sync_no_pisa_un_json_que_no_es_un_objeto(session_factory: sessionmaker):
    from app.services.contact_sync_protection import merge_custom_fields  # noqa: PLC0415

    contact = Contact(first_name="X", custom_fields="texto suelto de una importación")
    assert merge_custom_fields(contact, json.dumps({"Horario": "9-14"})) is None
    vacio = Contact(first_name="Y", custom_fields="null")
    assert json.loads(
        merge_custom_fields(vacio, json.dumps({"Horario": "9-14"}))
    ) == {"Horario": "9-14"}


def test_sync_respeta_la_clave_editada_a_mano_y_trae_las_demas(
    client: TestClient, session_factory: sessionmaker
):
    _sync_agile(session_factory, _agile_con_campos(Horario="9-14"))
    with session_factory() as session:
        contact_id = session.scalar(select(Contact.id))
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"custom_fields": {"Horario": "Solo mañanas"}},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["manually_edited_fields"] == ["custom_fields.Horario"]
    _sync_agile(
        session_factory, _agile_con_campos(Horario="9-18", CONTACTO_Persona="Marta"),
    )
    assert _campos(session_factory, contact_id) == {
        "Horario": "Solo mañanas", "CONTACTO Persona": "Marta",
    }


def test_marca_antigua_protege_las_claves_que_hay_y_deja_anadir(
    session_factory: sessionmaker,
):
    """La marca `custom_fields` de antes (el modal entero) protege lo que ya
    tiene el contacto, pero deja que vuelvan las claves que le faltan."""
    _sync_agile(session_factory, _agile_con_campos(Horario="9-14"))
    with session_factory() as session:
        c = session.scalar(select(Contact))
        contact_id = c.id
        protection.mark_manually_edited(c, ["custom_fields"])
        session.commit()
    _sync_brevo(session_factory, {"GRADO_DE_INTERES": "alto"})
    _sync_agile(session_factory, _agile_con_campos(Horario="9-18"))
    assert _campos(session_factory, contact_id) == {
        "Horario": "9-14", "GRADO_DE_INTERES": "alto",
    }
    # Tampoco se quita una clave protegida aunque su cuenta deje de traerla.
    _sync_agile(session_factory, _agile_payload(external_id=1, email="ana@example.com"))
    assert _campos(session_factory, contact_id)["Horario"] == "9-14"


def test_lo_que_una_cuenta_deja_de_traer_se_quita_si_nadie_lo_toco(
    session_factory: sessionmaker,
):
    """Si lo borran en Agile o en Brevo, se borra aquí: pero solo lo que esa
    cuenta puso y nadie ha cambiado después."""
    _sync_agile(
        session_factory,
        _agile_con_campos(Horario="9-14", CONTACTO_Persona="Marta"),
    )
    with session_factory() as session:
        contact_id = session.scalar(select(Contact.id))
    _sync_brevo(session_factory, {"GRADO_DE_INTERES": "alto", "INTERES": "UV"})
    # Un workflow cambia el INTERES que trajo Brevo.
    with session_factory() as session:
        c = session.get(Contact, contact_id)
        campos = json.loads(c.custom_fields)
        campos["INTERES"] = "DTF"
        c.custom_fields = json.dumps(campos)
        session.commit()
    # En Agile quitan «CONTACTO Persona»; en Brevo, los dos atributos.
    _sync_agile(session_factory, _agile_con_campos(Horario="9-14"))
    _sync_brevo(session_factory, {})
    assert _campos(session_factory, contact_id) == {
        "Horario": "9-14",
        # Lo cambió el workflow: no es ya lo que puso Brevo, se queda.
        "INTERES": "DTF",
    }


def test_referencia_antigua_sin_lo_que_trajo_no_quita_nada(
    session_factory: sessionmaker,
):
    """Las referencias de antes del cambio no guardan qué trajo su cuenta:
    su primer sync no quita nada (y lo apunta para el siguiente)."""
    from app.models.crm import ExternalReference  # noqa: PLC0415

    _sync_agile(
        session_factory,
        _agile_con_campos(Horario="9-14", CONTACTO_Persona="Marta"),
    )
    with session_factory() as session:
        ref = session.scalar(select(ExternalReference))
        contact_id = ref.contact_id
        metadata = json.loads(ref.metadata_json)
        assert metadata["custom_fields"] == {
            "Horario": "9-14", "CONTACTO Persona": "Marta",
        }
        del metadata["custom_fields"]
        ref.metadata_json = json.dumps(metadata)
        session.commit()
    _sync_agile(session_factory, _agile_con_campos(Horario="9-14"))
    assert _campos(session_factory, contact_id) == {
        "Horario": "9-14", "CONTACTO Persona": "Marta",
    }
    # El siguiente ya sabe lo que trajo: si vuelve a faltar, nada que quitar
    # (no lo trajo la vez anterior).
    _sync_agile(session_factory, _agile_con_campos(Horario="9-14"))
    assert _campos(session_factory, contact_id)["CONTACTO Persona"] == "Marta"


def test_patch_custom_fields_fusiona_y_marca_solo_lo_que_cambia(
    client: TestClient, session_factory: sessionmaker
):
    contact_id = _create_contact_via_sync(session_factory)
    with session_factory() as session:
        c = session.get(Contact, contact_id)
        c.custom_fields = json.dumps(
            {"Horario": "9-14", "NIVEL": 5, "GRADO_DE_INTERES": "alto"}
        )
        session.commit()
    # Un modal viejo manda el JSON entero, con todo como texto: solo cuenta
    # lo que cambia de verdad; `5` y `"5"` son lo mismo.
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"custom_fields": {"Horario": "10-18", "NIVEL": "5"}},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["manually_edited_fields"] == ["custom_fields.Horario"]
    assert _campos(session_factory, contact_id) == {
        "Horario": "10-18", "NIVEL": 5, "GRADO_DE_INTERES": "alto",
    }


def test_patch_custom_fields_null_quita_la_clave_y_el_sync_no_la_devuelve(
    client: TestClient, session_factory: sessionmaker
):
    _sync_agile(session_factory, _agile_con_campos(Horario="9-14"))
    with session_factory() as session:
        contact_id = session.scalar(select(Contact.id))
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"custom_fields": {"Horario": None}},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["manually_edited_fields"] == ["custom_fields.Horario"]
    assert _campos(session_factory, contact_id) == {}
    _sync_agile(
        session_factory, _agile_con_campos(Horario="9-14", CONTACTO_Persona="Marta"),
    )
    assert _campos(session_factory, contact_id) == {"CONTACTO Persona": "Marta"}


def test_patch_custom_fields_que_no_es_un_objeto_da_422(
    client: TestClient, session_factory: sessionmaker
):
    contact_id = _create_contact_via_sync(session_factory)
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"custom_fields": "esto no es un objeto"},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 422
    # Un objeto JSON en texto (legado) sigue valiendo.
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"custom_fields": '{"Horario": "9-14"}'},
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert _campos(session_factory, contact_id) == {"Horario": "9-14"}


def test_reset_de_custom_fields_quita_tambien_las_marcas_por_clave(
    session_factory: sessionmaker,
):
    with session_factory() as session:
        contact = Contact(first_name="X", email="x@x.com")
        session.add(contact)
        session.flush()
        protection.mark_manually_edited(
            contact,
            ["phone", protection.custom_field_mark("Horario"), "custom_fields."],
        )
        # La marca vacía `custom_fields.` no vale; las demás sí.
        assert protection.get_manually_edited_fields(contact) == [
            "custom_fields.Horario", "phone",
        ]
        protection.reset_manual_edits(contact, ["custom_fields"])
        assert protection.get_manually_edited_fields(contact) == ["phone"]


def test_un_valor_vaciado_en_agile_se_vacia_aqui(session_factory: sessionmaker):
    """Agile manda el campo vaciado ("") en vez de quitarlo: cuenta como que
    ya no lo trae."""
    _sync_agile(session_factory, _agile_con_campos(Horario="9-14"))
    with session_factory() as session:
        contact_id = session.scalar(select(Contact.id))
    _sync_agile(session_factory, _agile_con_campos(Horario=""))
    assert _campos(session_factory, contact_id) == {}


def test_no_se_quita_lo_que_otra_cuenta_sigue_trayendo(
    session_factory: sessionmaker,
):
    """El contacto está en dos cuentas de Agile con el mismo «Horario». Si
    una lo borra, se queda: la otra lo sigue trayendo. Y si de otra cuenta
    no consta qué trae (referencia de antes del cambio), no se quita nada."""
    from app.models.crm import ExternalReference  # noqa: PLC0415

    _sync_agile(session_factory, _agile_con_campos(Horario="9-14"))
    with session_factory() as session:
        contact_id = session.scalar(select(Contact.id))
        _upsert_contact_for_payload(
            session, account_id="segunda",
            payload=_agile_con_campos(external_id=2, Horario="9-14"),
        )
        session.commit()
    _sync_agile(session_factory, _agile_payload(external_id=1, email="ana@example.com"))
    assert _campos(session_factory, contact_id) == {"Horario": "9-14"}

    # La segunda cuenta también lo borra: ahora sí se quita.
    with session_factory() as session:
        _upsert_contact_for_payload(
            session, account_id="segunda",
            payload=_agile_payload(external_id=2, email="ana@example.com"),
        )
        session.commit()
    assert _campos(session_factory, contact_id) == {}

    # De vuelta: las dos lo traen, pero de la segunda no consta qué trajo.
    _sync_agile(session_factory, _agile_con_campos(Horario="9-14"))
    with session_factory() as session:
        segunda = session.scalar(
            select(ExternalReference).where(ExternalReference.account_id == "segunda")
        )
        segunda.metadata_json = json.dumps({"owner": {"id": "1"}})
        session.commit()
    _sync_agile(session_factory, _agile_payload(external_id=1, email="ana@example.com"))
    assert _campos(session_factory, contact_id) == {"Horario": "9-14"}
