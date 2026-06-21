"""PR-Manual-Capturas — seed reproducible para el manual de usuario.

Pobla una BD SQLite con datos demo realistas pero 100% ficticios para
poder componer screenshots con `scripts/capture_manual_screenshots.py`.

Uso:

    export DATABASE_URL=sqlite:////tmp/crmbomedia-demo.db
    export INTEGRATION_SECRETS_KEY="$(python -c \
        'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    export SECRET_KEY="demo-secret-key-not-for-prod"
    python -m backend.scripts.seed_manual_demo

NO usa alembic (cubre Base.metadata.create_all). El schema generado es
equivalente y mucho más rápido para una demo efímera.

Credenciales seed:
    admin@demo.com / DemoAdmin2026!     (rol admin, sin 2FA)
    comercial@demo.com / DemoComercial2026! (rol user, sin 2FA)
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

# Permite ejecutar como módulo desde la raíz del repo.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.crm import (
    ActivityEvent,
    AssignmentRule,
    Base,
    Company,
    Contact,
    ContactAssignment,
    ContactPhone,
    ContactPipelineStage,
    ContactTag,
    CustomFieldDefinition,
    EmailDirection,
    EmailEventType,
    EmailFolder,
    EmailMessage,
    EmailMessageEvent,
    EmailThread,
    Note,
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
    WorkflowRun,
    WorkflowRunState,
    WorkflowStatus,
    WorkflowStep,
)

NOW = datetime.now(UTC)


def _ago(days: int = 0, hours: int = 0, minutes: int = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours, minutes=minutes)


def _ahead(days: int = 0, hours: int = 0, minutes: int = 0) -> datetime:
    return NOW + timedelta(days=days, hours=hours, minutes=minutes)


def reset_db(engine) -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def seed_users(session: Session) -> dict[str, User]:
    """Crea admin Bart Demo + comercial Manel Demo con TOTP off."""
    admin = User(
        email="admin@demo.com",
        full_name="Bart Demo",
        password_hash=hash_password("DemoAdmin2026!"),
        role=UserRole.ADMIN,
        is_active=True,
        totp_enabled=False,
    )
    comercial = User(
        email="comercial@demo.com",
        full_name="Manel Demo",
        password_hash=hash_password("DemoComercial2026!"),
        role=UserRole.USER,
        is_active=True,
        totp_enabled=False,
    )
    viewer = User(
        email="lectura@demo.com",
        full_name="Lectura Demo",
        password_hash=hash_password("DemoView2026!"),
        role=UserRole.VIEWER,
        is_active=True,
        totp_enabled=False,
    )
    session.add_all([admin, comercial, viewer])
    session.commit()
    return {"admin": admin, "comercial": comercial, "viewer": viewer}


def seed_tags(session: Session) -> dict[str, Tag]:
    tags = {}
    for name in [
        "FESPA",
        "VIP",
        "Newsletter",
        "Webform",
        "Captado-evento",
        "Distribuidor",
        "Reseller",
        "Lead-frío",
    ]:
        tag = Tag(name=name, name_normalized=name.lower())
        session.add(tag)
        tags[name] = tag
    session.commit()
    return tags


def seed_custom_fields(session: Session) -> None:
    for key, label, ftype in [
        ("zona_geografica", "Zona geográfica", "text"),
        ("interes_principal", "Interés principal", "text"),
        ("presupuesto_anual", "Presupuesto anual €", "number"),
        ("primer_contacto_fecha", "Primer contacto", "date"),
    ]:
        session.add(
            CustomFieldDefinition(
                key=key,
                label=label,
                field_type=ftype,
                source="manual",
            )
        )
    session.commit()


def seed_companies(session: Session) -> list[Company]:
    rows = [
        Company(name="Empresa Demo SL", tax_id="ESB12345678", website="https://demo-sl.example"),
        Company(name="Cliente Plus SA", tax_id="ESA87654321", website="https://cliente-plus.example"),
        Company(name="Industrias Demo SL", tax_id="ESB11223344", website="https://industrias-demo.example"),
        Company(name="Comercial Norte SL", tax_id="ESB22334455"),
        Company(name="Estudio Creativo SLU", tax_id="ESB33445566"),
    ]
    session.add_all(rows)
    session.commit()
    return rows


def seed_contacts(
    session: Session,
    *,
    users: dict[str, User],
    tags: dict[str, Tag],
    companies: list[Company],
) -> list[Contact]:
    """20 contactos con datos variados. Algunos owned por Manel, otros
    por Bart. Lead score + star rating mezclados. Custom fields algunos."""
    contacts: list[Contact] = []
    sample = [
        # (first_name, last_name, email, score, stars, status, owner_key, company_idx, tag_names)
        ("Demo", "Cliente Activo", "cliente1@demo.com", 78, 5, "won",
         "comercial", 0, ["FESPA", "VIP"]),
        ("Demo", "Cliente Plus", "cliente2@demo.com", 64, 4, "qualified",
         "comercial", 1, ["VIP", "Newsletter"]),
        ("Lara", "Prospect Demo", "lara@demo.com", 52, 3, "qualified",
         "comercial", 2, ["FESPA"]),
        ("Juan", "Lead Reciente", "juan@demo.com", 30, 2, "new",
         "admin", None, ["Webform", "Lead-frío"]),
        ("Ana", "Distribuidor Demo", "ana@demo.com", 80, 5, "won",
         "comercial", 3, ["Distribuidor", "VIP"]),
        ("Carlos", "Reseller Demo", "carlos@demo.com", 71, 4, "qualified",
         "comercial", 4, ["Reseller"]),
        ("Pedro", "Lead Frío Demo", "pedro@demo.com", 12, 1, "new",
         "admin", None, ["Lead-frío"]),
        ("Sofía", "Cliente Demo 1", "sofia@demo.com", 58, 3, "qualified",
         "comercial", 0, ["FESPA", "Captado-evento"]),
        ("Marta", "Cliente Demo 2", "marta@demo.com", 65, 4, "qualified",
         "comercial", 1, ["Newsletter"]),
        ("Roberto", "Inactivo Demo", "roberto@demo.com", 8, 1, "lost",
         "admin", None, ["Lead-frío"]),
        ("Elena", "VIP Demo", "elena@demo.com", 90, 5, "won",
         "comercial", 2, ["VIP", "FESPA"]),
        ("Mateo", "Lead Caliente", "mateo@demo.com", 73, 4, "qualified",
         "comercial", 3, ["Webform", "FESPA"]),
        ("Lucía", "Reciente Demo", "lucia@demo.com", 41, 3, "new",
         "comercial", None, ["Newsletter"]),
        ("Pablo", "Distribuidor 2", "pablo@demo.com", 68, 4, "qualified",
         "admin", 4, ["Distribuidor"]),
        ("Inés", "Reseller 2", "ines@demo.com", 55, 3, "qualified",
         "comercial", 0, ["Reseller", "Newsletter"]),
        ("Tomás", "Antiguo Demo", "tomas@demo.com", 25, 2, "new",
         "admin", None, ["Lead-frío"]),
        ("Clara", "Demo Industria", "clara@demo.com", 62, 3, "qualified",
         "comercial", 2, ["FESPA"]),
        ("Daniel", "Demo Captado", "daniel@demo.com", 45, 3, "qualified",
         "comercial", None, ["Captado-evento"]),
        ("Patricia", "VIP 2 Demo", "patricia@demo.com", 88, 5, "won",
         "comercial", 1, ["VIP"]),
        ("Sergio", "Lead Activo", "sergio@demo.com", 39, 2, "new",
         "comercial", 3, ["Webform"]),
    ]
    for (first, last, email, score, stars, status, owner_key,
         company_idx, tag_names) in sample:
        owner = users[owner_key]
        contact = Contact(
            first_name=first,
            last_name=last,
            email=email,
            phone=f"+34 600 {hash(email) % 1000000:06d}",
            origin="Manual",
            origin_account_id=None,
            lead_score=score,
            star_rating=stars,
            commercial_status=status,
            owner_user_id=owner.id,
            company_id=companies[company_idx].id if company_idx is not None else None,
            address_country="ES",
            address_country_name="España",
            address_city=["Barcelona", "Madrid", "Valencia", "Sevilla", "Bilbao"][hash(email) % 5],
            job_title=["Director Comercial", "Marketing Manager", "CEO",
                       "Compras", "IT Manager"][hash(email) % 5],
            custom_fields=json.dumps({
                "zona_geografica": ["Cataluña", "Centro", "Levante", "Sur", "Norte"][hash(email) % 5],
                "interes_principal": ["Productos FESPA", "Newsletter mensual",
                                       "Servicios premium", "Recambios"][hash(email) % 4],
            }),
            tags=",".join(tag_names),
        )
        session.add(contact)
        session.flush()
        # Primary assignment row.
        session.add(
            ContactAssignment(
                contact_id=contact.id,
                user_id=owner.id,
                is_primary=True,
                source="manual_creator",
            )
        )
        # Tag mappings.
        for tag_name in tag_names:
            session.add(
                ContactTag(
                    contact_id=contact.id,
                    tag_id=tags[tag_name].id,
                    source="manual",
                )
            )
        # Some phones.
        if score > 40:
            session.add(
                ContactPhone(
                    contact_id=contact.id,
                    label="Móvil",
                    number=f"+34 600 {hash(email + 'p2') % 1000000:06d}",
                    is_primary=True,
                    source="manual",
                )
            )
        contacts.append(contact)
    session.commit()
    return contacts


def seed_pipelines(session: Session, *, owner_id: str) -> tuple[Pipeline, list[PipelineStage]]:
    pipeline = Pipeline(
        name="Ventas B2B",
        description="Pipeline principal del equipo comercial",
        owner_user_id=owner_id,
    )
    session.add(pipeline)
    session.flush()
    stages_def = [
        ("Cualificación", 0, False, False, 7),
        ("Demo / Presentación", 1, False, False, 10),
        ("Propuesta enviada", 2, False, False, 14),
        ("Negociación", 3, False, False, 7),
        ("Ganado", 4, True, False, 0),
        ("Perdido", 5, False, True, 0),
    ]
    stages = []
    for name, position, is_won, is_lost, target_days in stages_def:
        stage = PipelineStage(
            pipeline_id=pipeline.id,
            name=name,
            position=position,
            is_won=is_won,
            is_lost=is_lost,
            target_days=target_days or None,
        )
        session.add(stage)
        stages.append(stage)
    session.commit()
    # Pipeline secundario.
    pipeline2 = Pipeline(name="Postventa & soporte premium",
                         description="Pipeline para upsells",
                         owner_user_id=owner_id)
    session.add(pipeline2)
    session.flush()
    for i, name in enumerate(["Detectado", "Conversando", "Cerrado"]):
        session.add(
            PipelineStage(
                pipeline_id=pipeline2.id,
                name=name,
                position=i,
                is_won=(name == "Cerrado"),
                is_lost=False,
            )
        )
    session.commit()
    return pipeline, stages


def seed_opportunities(
    session: Session,
    *,
    pipeline: Pipeline,
    stages: list[PipelineStage],
    contacts: list[Contact],
) -> None:
    """Reparte 10 oportunidades por los stages."""
    distribution = [
        (0, 0), (1, 0), (2, 0),       # Cualificación: 3
        (3, 1), (4, 1),               # Demo: 2
        (5, 2), (6, 2), (7, 2),       # Propuesta: 3
        (8, 3),                       # Negociación: 1
        (9, 4),                       # Ganado: 1
    ]
    for contact_idx, stage_idx in distribution:
        contact = contacts[contact_idx]
        stage = stages[stage_idx]
        session.add(
            ContactPipelineStage(
                contact_id=contact.id,
                pipeline_id=pipeline.id,
                stage_id=stage.id,
                entered_stage_at=_ago(days=hash(contact.id) % 20),
                added_to_pipeline_at=_ago(days=hash(contact.id) % 20),
                notes=f"Oportunidad ~{10_000 + (hash(contact.id) % 30_000)}€ — "
                      + ["Plotter UV", "Tinta FESPA", "Servicio premium",
                         "Recambios kit"][hash(contact.id) % 4],
            )
        )
    session.commit()


def seed_tasks(
    session: Session,
    *,
    users: dict[str, User],
    contacts: list[Contact],
) -> None:
    """10 tareas mezcladas: pendientes hoy, vencidas, completadas."""
    samples = [
        ("Llamar para confirmar pedido", "pending", _ahead(hours=2), "alta",
         "comercial", 0),
        ("Enviar propuesta revisada", "pending", _ahead(hours=4), "alta",
         "comercial", 1),
        ("Email seguimiento newsletter", "pending", _ahead(days=1), "media",
         "comercial", 2),
        ("Preparar demo personalizada", "pending", _ahead(days=2, hours=1), "alta",
         "comercial", 4),
        ("Revisar contrato firmado", "completed", _ago(days=1), "media",
         "comercial", 8),
        ("Cancelar reunión semana próxima", "completed", _ago(days=3), "baja",
         "comercial", 9),
        ("Llamada vencida hace 2 días", "pending", _ago(days=2), "alta",
         "comercial", 3),
        ("Pedir referencias adicionales", "pending", _ahead(days=5), "baja",
         "admin", 5),
        ("Cierre mensual del pipeline", "pending", _ahead(hours=6), "alta",
         "admin", None),
        ("Llamada VIP para upsell", "pending", _ahead(days=3), "alta",
         "comercial", 10),
    ]
    for title, status, due_at, prio, owner_key, contact_idx in samples:
        owner = users[owner_key]
        task = Task(
            title=title,
            description="Tarea generada por seed de demo.",
            status=status,
            due_at=due_at,
            priority=prio,
            assigned_user_id=owner.id,
            created_by_user_id=owner.id,
            contact_id=contacts[contact_idx].id if contact_idx is not None else None,
        )
        if status == "completed":
            task.completed_at = _ago(days=1)
        session.add(task)
    session.commit()


def seed_notes(session: Session, *, contacts: list[Contact], users: dict[str, User]) -> None:
    """3 notas en el primer contacto + 1-2 en el resto."""
    for i, contact in enumerate(contacts[:6]):
        for j, body in enumerate([
            "Cliente muy interesado en la línea premium. Pedir referencias antes del cierre.",
            "Confirmado por email: prefiere reunión la semana próxima por la tarde.",
            "Lead caliente — competencia ofrece -10% pero menor servicio.",
        ][: 3 if i == 0 else 2]):
            session.add(
                Note(
                    contact_id=contact.id,
                    body=body,
                    source="manual",
                    pinned=(j == 0),
                    created_by_user_id=users["comercial"].id,
                )
            )
    session.commit()


def seed_email_folders(session: Session, *, users: dict[str, User]) -> None:
    """Carpetas mínimas para que la sidebar de /emails muestre algo."""
    for name in ["Pedidos pendientes", "Pedidos cerrados", "Proveedores"]:
        session.add(
            EmailFolder(
                user_id=users["comercial"].id,
                name=name,
            )
        )
    session.commit()


def seed_email_threads(
    session: Session,
    *,
    users: dict[str, User],
    contacts: list[Contact],
) -> None:
    """5 threads con mensajes + eventos open/click para badges."""
    user = users["comercial"]
    for i, contact in enumerate(contacts[:5]):
        thread_id = str(uuid4())
        thread = EmailThread(
            id=thread_id,
            contact_id=contact.id,
            initiated_by_user_id=user.id,
            gmail_thread_id=f"demo-thread-{i}",
            gmail_account_user_id=user.id,
            subject=[
                "Propuesta plotter Roland — revisión",
                "Demo personalizada confirmada",
                "Re: información tintas FESPA",
                "Pedido recambios kit demo",
                "Bienvenida + onboarding clientes premium",
            ][i],
            first_message_at=_ago(days=i + 1),
            last_message_at=_ago(days=i, hours=2),
            message_count=2,
        )
        session.add(thread)
        session.flush()
        # Mensaje outbound.
        msg_out = EmailMessage(
            thread_id=thread.id,
            gmail_message_id=f"demo-out-{i}",
            gmail_account_user_id=user.id,
            direction=EmailDirection.OUTBOUND,
            from_email=user.email,
            from_name=user.full_name,
            to_emails_json=json.dumps([contact.email]),
            subject=thread.subject,
            body_html=f"<p>Hola {contact.first_name},</p><p>Adjunto propuesta solicitada.</p>",
            snippet="Adjunto propuesta solicitada.",
            sent_at=_ago(days=i + 1),
            contact_id=contact.id,
            created_by_user_id=user.id,
        )
        session.add(msg_out)
        session.flush()
        # Evento OPEN para los 3 primeros + CLICK para 2.
        if i < 3:
            session.add(
                EmailMessageEvent(
                    message_id=msg_out.id,
                    event_type=EmailEventType.OPEN,
                    occurred_at=_ago(days=i, hours=20),
                )
            )
        if i < 2:
            session.add(
                EmailMessageEvent(
                    message_id=msg_out.id,
                    event_type=EmailEventType.CLICK,
                    occurred_at=_ago(days=i, hours=18),
                )
            )
        # Mensaje inbound (reply) para los 2 primeros.
        if i < 2:
            session.add(
                EmailMessage(
                    thread_id=thread.id,
                    gmail_message_id=f"demo-in-{i}",
                    gmail_account_user_id=user.id,
                    direction=EmailDirection.INBOUND,
                    from_email=contact.email,
                    to_emails_json=json.dumps([user.email]),
                    subject="RE: " + thread.subject,
                    snippet="Recibido, mañana lo revisamos.",
                    sent_at=_ago(days=i, hours=2),
                    contact_id=contact.id,
                )
            )
    session.commit()


def seed_activity_events(
    session: Session, *, contacts: list[Contact], users: dict[str, User]
) -> None:
    """3-5 actividades por contacto para alimentar el timeline."""
    user = users["comercial"]
    for contact in contacts[:8]:
        for idx, (ev_type, days_ago, body) in enumerate([
            ("email.sent_from_crm", 1, f"Email enviado a {contact.email}"),
            ("task.completed", 2, "Llamada inicial completada"),
            ("note.added", 3, "Nota: lead muy interesado"),
            ("contact.updated", 5, "Tags actualizados"),
        ]):
            session.add(
                ActivityEvent(
                    contact_id=contact.id,
                    system="crm",
                    account_id="default",
                    external_id=f"demo-{contact.id[:6]}-{idx}",
                    event_type=ev_type,
                    occurred_at=_ago(days=days_ago),
                    body=body,
                )
            )
    session.commit()


def seed_segments(session: Session, *, users: dict[str, User]) -> None:
    samples = [
        ("VIPs activos", "Clientes ganados con score alto + VIP", {
            "operator": "AND",
            "children": [
                {"type": "rule", "field": "lead_score",
                 "comparator": "gte", "value": 70},
                {"type": "rule", "field": "tags",
                 "comparator": "contains_any", "value": ["VIP"]},
            ],
        }),
        ("Leads recientes sin contacto", "Nuevos < 7 días, sin task pendiente", {
            "operator": "AND",
            "children": [
                {"type": "rule", "field": "commercial_status",
                 "comparator": "eq", "value": "new"},
                {"type": "rule", "field": "created_at",
                 "comparator": "in_last_n_days", "value": 7},
            ],
        }),
        ("Distribuidores Cataluña", "Por zona + tag", {
            "operator": "AND",
            "children": [
                {"type": "rule", "field": "tags",
                 "comparator": "contains_any", "value": ["Distribuidor"]},
                {"type": "rule", "field": "address_country",
                 "comparator": "eq", "value": "ES"},
            ],
        }),
    ]
    for name, description, rules in samples:
        session.add(
            Segment(
                name=name,
                description=description,
                rules_json=json.dumps(rules),
                owner_user_id=users["admin"].id,
                is_shared=True,
                is_dynamic=True,
            )
        )
    session.commit()


def seed_assignment_rules(
    session: Session, *, users: dict[str, User]
) -> None:
    samples = [
        ("VIPs → Manel", {
            "operator": "AND",
            "children": [
                {"type": "rule", "field": "tags",
                 "comparator": "contains_any", "value": ["VIP"]},
            ],
        }, users["comercial"].id, True, False),
        ("Webform → Admin (round robin)", {
            "operator": "AND",
            "children": [
                {"type": "rule", "field": "tags",
                 "comparator": "contains_any", "value": ["Webform"]},
            ],
        }, users["admin"].id, True, False),
        ("Reasignar perdidos", {
            "operator": "AND",
            "children": [
                {"type": "rule", "field": "commercial_status",
                 "comparator": "eq", "value": "lost"},
            ],
        }, users["admin"].id, True, True),
    ]
    for i, (name, rules, primary_uid, active, override) in enumerate(samples):
        session.add(
            AssignmentRule(
                name=name,
                conditions_json=json.dumps(rules),
                primary_user_id=primary_uid,
                priority=100 - i * 10,
                apply_to="unassigned_only",
                stop_on_match=True,
                override_existing=override,
                is_active=active,
                created_by_user_id=users["admin"].id,
            )
        )
    session.commit()


def seed_workflows(
    session: Session, *, users: dict[str, User], contacts: list[Contact]
) -> None:
    """3 workflows en distinto estado + algunos runs históricos."""
    wf_active = Workflow(
        name="Onboarding lead nuevo",
        description="Email bienvenida + crear tarea a las 24h",
        status=WorkflowStatus.ACTIVE,
        trigger_type="contact.created",
        trigger_config_json="{}",
        total_entered=12,
        total_completed=8,
        total_won=3,
        created_by_user_id=users["admin"].id,
    )
    wf_paused = Workflow(
        name="Reactivación 30d sin actividad",
        description="Pausa por ahora — revisar copy del email",
        status=WorkflowStatus.PAUSED,
        trigger_type="contact.updated",
        trigger_config_json="{}",
        total_entered=4,
        total_completed=1,
        created_by_user_id=users["admin"].id,
    )
    wf_draft = Workflow(
        name="Cumpleaños cliente — felicitación",
        description="Draft pendiente de revisión",
        status=WorkflowStatus.DRAFT,
        trigger_type="contact.created",
        trigger_config_json="{}",
        created_by_user_id=users["admin"].id,
    )
    session.add_all([wf_active, wf_paused, wf_draft])
    session.flush()
    # Entry step para cada uno.
    for wf in (wf_active, wf_paused, wf_draft):
        session.add(
            WorkflowStep(
                workflow_id=wf.id,
                type="action_email_send",
                config_json="{}",
                is_entry=True,
                position_x=100.0,
                position_y=100.0,
                display_name="Enviar email bienvenida",
            )
        )
    session.flush()
    # Runs históricos del active.
    for i, contact in enumerate(contacts[:5]):
        entry_step = session.query(WorkflowStep).filter_by(
            workflow_id=wf_active.id, is_entry=True
        ).one()
        run = WorkflowRun(
            workflow_id=wf_active.id,
            contact_id=contact.id,
            current_step_id=entry_step.id,
            state=WorkflowRunState.COMPLETED if i < 3 else WorkflowRunState.RUNNING,
            active_dedup_key=f"{wf_active.id}:{contact.id}:{uuid4()}",
            trigger_payload_json="{}",
            started_at=_ago(days=i + 1),
            wake_at=_ago(days=i + 1),
            completed_at=_ago(days=i) if i < 3 else None,
        )
        session.add(run)
    session.commit()


def main() -> None:
    import os
    from urllib.parse import urlparse

    url = os.environ.get("DATABASE_URL", "sqlite:////tmp/crmbomedia-demo.db")
    parsed = urlparse(url)
    if parsed.scheme.startswith("sqlite"):
        db_path = parsed.path
        if db_path and Path(db_path).exists():
            Path(db_path).unlink()
    engine = create_engine(url, future=True)
    reset_db(engine)
    with Session(engine) as session:
        users = seed_users(session)
        tags = seed_tags(session)
        seed_custom_fields(session)
        companies = seed_companies(session)
        contacts = seed_contacts(
            session, users=users, tags=tags, companies=companies
        )
        pipeline, stages = seed_pipelines(session, owner_id=users["admin"].id)
        seed_opportunities(
            session, pipeline=pipeline, stages=stages, contacts=contacts
        )
        seed_tasks(session, users=users, contacts=contacts)
        seed_notes(session, contacts=contacts, users=users)
        seed_email_folders(session, users=users)
        seed_email_threads(session, users=users, contacts=contacts)
        seed_activity_events(session, contacts=contacts, users=users)
        seed_segments(session, users=users)
        seed_assignment_rules(session, users=users)
        seed_workflows(session, users=users, contacts=contacts)
        print(f"Seed completado en {url}")
        print("  Users:       admin@demo.com / DemoAdmin2026!")
        print("               comercial@demo.com / DemoComercial2026!")
        print(f"  Contacts:    {len(contacts)}")
        print(f"  Companies:   {len(companies)}")
        print(f"  Pipelines:   {pipeline.name} + 1 más")


if __name__ == "__main__":
    main()
