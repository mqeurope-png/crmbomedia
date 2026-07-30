"""Bulk-action endpoint for the contacts list.

Mini-PR C Fase 3. Surfaces a single POST /api/contacts/bulk-action
that handles every contact-list bulk operation the UI exposes:
reassign owner, add/remove tag, change commercial status, deactivate.

Limited to 1000 contacts per call — anything larger is paginated by
the client. Every action writes an audit row with the affected
contact ids in the metadata.

Brevo list push and segment creation deliberately stay out of this
endpoint because they need a real saved view to identify the cohort;
the UI sends those flows to the existing
`/api/contact-views/{id}/push-to-brevo` and
`/api/segments` endpoints respectively.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_user
from app.db.session import get_session
from app.models.crm import (
    Contact,
    ContactTag,
    Pipeline,
    Segment,
    Tag,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
)
from app.repositories import assignments as assignments_repo
from app.repositories import crm as crm_repository
from app.repositories import pipelines as pipelines_repository
from app.repositories import segments as segments_repository
from app.repositories import tasks as tasks_repository
from app.services.ownership import (
    partition_contacts_by_ownership,
    user_processes_all_contacts,
)

router = APIRouter(prefix="/api/contacts", tags=["contacts"])
logger = logging.getLogger(__name__)

BulkAction = Literal[
    "assign_owner",
    "add_tag",
    "remove_tag",
    "change_status",
    "change_lifecycle",
    "deactivate",
    "add_to_pipeline",
    "add_to_workflow",
    "add_to_segment",
    "create_task",
]

# PR-Hotfix-Notas-Workflows Item C. Acción bulk → Action del audit. Una
# sola fila por bulk (ver `bulk_action`). Las acciones sin entrada usan
# CONTACT_UPDATED; add_to_workflow usa el literal que ya usa el enroll
# individual (no hay constante en el enum).
_AUDIT_ACTION: dict[str, Action] = {
    "add_tag": Action.CONTACT_TAGS_BULK_ACTION,
    "remove_tag": Action.CONTACT_TAGS_BULK_ACTION,
    "add_to_pipeline": Action.CONTACT_PIPELINE_STAGE_ADDED,
    "create_task": Action.TASK_CREATED,
}

# Sprint Reglas-Assign PR-D: subido de 1000 a 50000. El cap antiguo
# bloqueaba la reasignación de carteras grandes ("asignar todos los
# 1200 leads filtrados al comercial X"). El cap nuevo es un seguro de
# memoria contra requests maliciosas / accidentales — 50k UUIDs son
# ~2 MB de payload, suficiente para los volúmenes reales de la CRM.
# Internamente procesamos por chunks (CHUNK_SIZE) para no atascar
# una sola transacción gigante.
MAX_BULK_CONTACTS = 50_000
CHUNK_SIZE = 500


class BulkActionPayload(BaseModel):
    contact_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_CONTACTS)
    action: BulkAction
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/bulk-action")
def bulk_action(
    body: BulkActionPayload,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Run a single bulk action across the contact ids the caller
    sent. Returns `{action, affected_count, contact_ids}`.

    Authorisation:
    - `assign_owner` requires admin or manager.
    - `deactivate` requires admin.
    - The rest are open to any signed-in user (the `require_user`
      dep already excludes viewers).
    """
    _check_role_for(body.action, current_user)
    # PR-Bulk-Comerciales. Un comercial (`user`) solo aplica acciones
    # masivas a SUS contactos; admin/manager a todos. Los ajenos se
    # ignoran silenciosamente (el frontend ya avisó con el modal). El
    # filtro es la única regla de propiedad — vive en el helper.
    owned_ids, foreign_ids = partition_contacts_by_ownership(
        session, body.contact_ids, current_user
    )
    owner_filtered = not user_processes_all_contacts(current_user)
    # Sprint Reglas-Assign PR-D: chunking server-side. Sin esto, una
    # selección de >>1000 contactos generaba una sola transacción
    # gigante que (a) lockeaba la tabla durante segundos en MySQL y
    # (b) explotaba la memoria de PyMySQL al cargar todos los Contact
    # rows. Chunks de CHUNK_SIZE con commit por chunk: progreso real,
    # transacciones cortas, y al fallo a mitad lo procesado queda.
    affected_total = 0
    touched_ids: list[str] = []
    for chunk_idx in range(0, len(owned_ids), CHUNK_SIZE):
        ids_chunk = owned_ids[chunk_idx : chunk_idx + CHUNK_SIZE]
        contacts = list(
            session.scalars(
                select(Contact).where(Contact.id.in_(ids_chunk))
            )
        )
        if not contacts:
            continue
        affected_total += _dispatch(session, body, contacts, current_user)
        touched_ids.extend(c.id for c in contacts)
        session.commit()

    if not touched_ids:
        # Comercial que seleccionó SOLO contactos ajenos REALES: no es un
        # error, simplemente no hay nada suyo que procesar. Si en cambio
        # los ids no corresponden a ningún contacto (basura / inexistentes)
        # sí es un 400.
        real_foreign = (
            list(
                session.scalars(
                    select(Contact.id).where(Contact.id.in_(foreign_ids))
                )
            )
            if owner_filtered and foreign_ids
            else []
        )
        if real_foreign:
            return {
                "action": body.action,
                "affected_count": 0,
                "contact_ids": [],
                "skipped_foreign": len(real_foreign),
                "skipped_ids": real_foreign[:50],
            }
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ningún contacto válido en la selección.",
        )
    # Una única audit row para el bulk completo — describe el alcance
    # total, no cada chunk. `contact_ids` capado a 50 para que el JSON
    # no se infle en payloads grandes.
    audit_action: Action | str = _AUDIT_ACTION.get(
        body.action, Action.CONTACT_UPDATED
    )
    if body.action == "add_to_workflow":
        audit_action = "workflow.contact_added_manually"
    record_event(
        session,
        action=audit_action,
        target_type="contact",
        actor=current_user,
        metadata={
            "bulk_action": body.action,
            "affected_count": affected_total,
            "total_targets": len(touched_ids),
            "contact_ids": touched_ids[:50],
            "payload_keys": sorted(body.payload.keys()),
            # PR-Bulk-Comerciales. Trazabilidad de acciones de comercial.
            "via": "bulk",
            "owner_filtered": owner_filtered,
            "skipped_foreign": len(foreign_ids),
        },
        request=request,
    )
    session.commit()
    return {
        "action": body.action,
        "affected_count": affected_total,
        "contact_ids": touched_ids,
        "skipped_foreign": len(foreign_ids),
        "skipped_ids": foreign_ids[:50],
    }


def _check_role_for(action: BulkAction, user: User) -> None:
    # PR-Ca hotfix: assign_owner se bajó de manager+ a require_user
    # para alinearse con la decisión §1 del spec Reglas-Assign — un
    # comercial puede auto-asignarse o asignar a otro (ya valía vía
    # /api/contacts/{id}/assignments; el bulk seguía con la restricción
    # legacy por error). `deactivate` se queda en admin-only, no se
    # toca.
    _ = user  # no role check for assign_owner anymore
    if action == "deactivate" and user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo admin puede desactivar contactos en bulk.",
        )


def _dispatch(
    session: Session,
    body: BulkActionPayload,
    contacts: list[Contact],
    current_user: User,
) -> int:
    """Apply the action; return the number of rows actually touched."""
    if body.action == "assign_owner":
        owner_id = body.payload.get("owner_user_id")
        if not owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Falta `owner_user_id` en payload.",
            )
        owner = session.get(User, owner_id)
        if owner is None or not owner.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El owner indicado no existe o está inactivo.",
            )
        # Sprint Reglas-Assign PR-B: el bulk legacy "assign_owner" ahora
        # mantiene el invariante multi-comercial — pasa por add_assignment
        # con is_primary=True, que demota la primary previa si la había y
        # recalcula el caché owner_user_id. La acción semántica sigue
        # siendo "fijar al responsable", no "borrar secundarios".
        n = 0
        for c in contacts:
            if c.owner_user_id == owner_id:
                # Ya era primary — nada que tocar.
                continue
            assignments_repo.add_assignment(
                session,
                contact_id=c.id,
                user_id=owner_id,
                is_primary=True,
                source="manual",
            )
            n += 1
        return n
    if body.action == "add_tag":
        tag = _resolve_tag_for_add(session, body.payload, current_user)
        n = 0
        existing = {
            (a.contact_id, a.tag_id)
            for a in session.scalars(
                select(ContactTag).where(
                    ContactTag.contact_id.in_([c.id for c in contacts]),
                    ContactTag.tag_id == tag.id,
                )
            )
        }
        for c in contacts:
            if (c.id, tag.id) in existing:
                continue
            session.add(ContactTag(contact_id=c.id, tag_id=tag.id))
            n += 1
        return n
    if body.action == "remove_tag":
        tag = _require_tag(session, body.payload.get("tag_id"))
        assignments = list(
            session.scalars(
                select(ContactTag).where(
                    ContactTag.contact_id.in_([c.id for c in contacts]),
                    ContactTag.tag_id == tag.id,
                )
            )
        )
        for a in assignments:
            session.delete(a)
        return len(assignments)
    if body.action in ("change_status", "change_lifecycle"):
        # PR-Hotfix-Notas-Workflows Item C. "estado del ciclo"
        # (lifecycle_status) === `commercial_status` en este modelo. Se
        # acepta tanto `new_status` (legacy) como `lifecycle_status`.
        new_status = body.payload.get("new_status") or body.payload.get(
            "lifecycle_status"
        )
        if not new_status:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Falta `new_status`/`lifecycle_status` en payload.",
            )
        n = 0
        for c in contacts:
            if c.commercial_status != new_status:
                c.commercial_status = new_status
                n += 1
        return n
    if body.action == "deactivate":
        n = 0
        for c in contacts:
            if c.is_active:
                c.is_active = False
                n += 1
        return n
    if body.action == "add_to_pipeline":
        return _dispatch_add_to_pipeline(session, body, contacts, current_user)
    if body.action == "add_to_workflow":
        return _dispatch_add_to_workflow(session, body, contacts, current_user)
    if body.action == "add_to_segment":
        return _dispatch_add_to_segment(session, body, contacts)
    if body.action == "create_task":
        return _dispatch_create_task(session, body, contacts, current_user)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Acción bulk desconocida: {body.action!r}",
    )


def _dispatch_add_to_pipeline(
    session: Session,
    body: BulkActionPayload,
    contacts: list[Contact],
    current_user: User,
) -> int:
    pipeline_id = body.payload.get("pipeline_id")
    if not pipeline_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta `pipeline_id` en payload.",
        )
    pipeline = session.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El pipeline indicado no existe.",
        )
    stage_id = body.payload.get("stage_id")
    n = 0
    for c in contacts:
        try:
            pipelines_repository.add_contact_to_pipeline(
                session,
                contact=c,
                pipeline=pipeline,
                stage_id=stage_id,
                note=None,
                moved_by_user_id=current_user.id,
            )
            n += 1
        except ValueError as exc:
            # Stage inválido / pipeline sin stages: aplica a TODA la
            # selección (mismo pipeline/stage), así que 400 de golpe.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
    return n


def _dispatch_add_to_workflow(
    session: Session,
    body: BulkActionPayload,
    contacts: list[Contact],
    current_user: User,
) -> int:
    from app.models.workflows import Workflow  # noqa: PLC0415
    from app.services.ownership import can_user_see_resource  # noqa: PLC0415
    from app.workflows.engine import (  # noqa: PLC0415
        ManualStartError,
        advance_run,
        start_manual_run,
    )

    workflow_id = body.payload.get("workflow_id")
    if not workflow_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta `workflow_id` en payload.",
        )
    workflow = session.get(Workflow, workflow_id)
    if workflow is None or not can_user_see_resource(current_user, workflow):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El workflow indicado no existe o no es visible.",
        )
    n = 0
    for c in contacts:
        try:
            run = start_manual_run(
                session, workflow, c, actor_user_id=current_user.id
            )
        except ManualStartError as exc:
            # Workflow degenerado (sin sucesor del trigger): falla igual
            # para todos → 422 de golpe.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.message,
            ) from exc
        advance_run(session, run.id)
        n += 1
    return n


def _dispatch_add_to_segment(
    session: Session, body: BulkActionPayload, contacts: list[Contact]
) -> int:
    segment_id = body.payload.get("segment_id")
    if not segment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta `segment_id` en payload.",
        )
    segment = session.get(Segment, segment_id)
    if segment is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El segmento indicado no existe.",
        )
    if segment.is_dynamic:
        # Un segmento dinámico define su pertenencia por reglas — no se
        # pueden añadir contactos a mano.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede añadir manualmente a un segmento dinámico.",
        )
    current = segments_repository.decode_static_ids(segment)
    current_set = set(current)
    added = [c.id for c in contacts if c.id not in current_set]
    if added:
        segments_repository.update_segment(
            session,
            segment=segment,
            static_contact_ids=current + added,
        )
    return len(added)


def _dispatch_create_task(
    session: Session,
    body: BulkActionPayload,
    contacts: list[Contact],
    current_user: User,
) -> int:
    title = (body.payload.get("title") or "").strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta `title` en payload.",
        )
    description = body.payload.get("description") or None
    due_at = _parse_due_at(body.payload.get("due_at"))
    priority = _coerce_task_priority(body.payload.get("priority"))
    assigned_user_id = body.payload.get("assigned_user_id") or current_user.id
    assignee = session.get(User, assigned_user_id)
    if assignee is None or not assignee.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El usuario asignado no existe o está inactivo.",
        )
    # PR-Hotfix-Notas-Workflows Item C. Una tarea POR contacto (réplica),
    # cada una vinculada a su contacto.
    n = 0
    for c in contacts:
        tasks_repository.create_task(
            session,
            title=title,
            description=description,
            due_at=due_at,
            status=TaskStatus.PENDING,
            priority=priority,
            assigned_user_id=assigned_user_id,
            contact_id=c.id,
            company_id=None,
            pipeline_stage_id=None,
            created_by_user_id=current_user.id,
            reminder_minutes_before=None,
        )
        n += 1
    return n


def _parse_due_at(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`due_at` no es una fecha ISO válida.",
        ) from None


def _coerce_task_priority(raw: Any) -> TaskPriority:
    if not raw:
        return TaskPriority.MEDIUM
    try:
        return TaskPriority(str(raw).lower())
    except ValueError:
        return TaskPriority.MEDIUM


def _resolve_tag_for_add(
    session: Session, payload: dict[str, Any], current_user: User
) -> Tag:
    """add_tag admite `tag_id` (existente) o `tag_name` (crear al vuelo,
    reusando la lógica del TagPicker: color determinista si no se pasa)."""
    tag_id = payload.get("tag_id")
    if tag_id:
        return _require_tag(session, tag_id)
    tag_name = (payload.get("tag_name") or "").strip()
    if not tag_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta `tag_id` o `tag_name` en payload.",
        )
    color = payload.get("color") or _default_tag_color(tag_name)
    tag, _created = crm_repository.upsert_tag(
        session,
        name=tag_name,
        color=color,
        created_by_user_id=current_user.id,
    )
    return tag


def _default_tag_color(name: str) -> str:
    """Paleta determinista por hash del nombre normalizado — mismo
    criterio que `routes._default_tag_color`, así un tag creado al vuelo
    desde el bulk cae en el mismo color que si se creara desde la ficha."""
    from app.schemas.crm import TAG_COLOR_PALETTE  # noqa: PLC0415

    key = (name or "").strip().lower()
    digest = sum(ord(ch) for ch in key)
    return TAG_COLOR_PALETTE[digest % len(TAG_COLOR_PALETTE)]


def _require_tag(session: Session, tag_id: str | None) -> Tag:
    if not tag_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta `tag_id` en payload.",
        )
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El tag indicado no existe.",
        )
    return tag


# QoL sprint — export CSV de contactos seleccionados desde la lista.
# Permiso: manager+ (admin lo tiene por herencia). El export legacy
# /api/audit-logs/export sigue siendo admin-only por la sensibilidad
# del audit log; aquí el dato es comercial y los managers ya lo ven
# en pantalla, así que el rol mínimo se baja.

_CSV_COLUMNS = (
    "id",
    "first_name",
    "last_name",
    "email",
    "phone",
    "job_title",
    "company_id",
    "commercial_status",
    "tags",
    "owner_user_id",
    "address_country",
    "address_city",
    "created_at",
    "updated_at",
)


class BulkExportPayload(BaseModel):
    contact_ids: list[str] = Field(
        min_length=1, max_length=MAX_BULK_CONTACTS
    )


class OwnershipPreviewPayload(BaseModel):
    contact_ids: list[str] = Field(
        min_length=1, max_length=MAX_BULK_CONTACTS
    )


@router.post("/bulk/ownership-preview")
def bulk_ownership_preview(
    body: OwnershipPreviewPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    """PR-Bulk-Comerciales. El frontend lo llama antes de ejecutar una
    acción masiva para decidir si mostrar el modal de "contactos ajenos".

    - admin/manager: `foreign` siempre 0 (procesan todo) — el frontend
      no muestra el modal.
    - comercial: `owned_by_me` y `foreign` sobre la selección."""
    owned_ids, foreign_ids = partition_contacts_by_ownership(
        session, body.contact_ids, current_user
    )
    return {
        "total": len(body.contact_ids),
        "owned_by_me": len(owned_ids),
        "foreign": len(foreign_ids),
    }


@router.post("/bulk-export-csv")
def bulk_export_csv(
    body: BulkExportPayload,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    """Devuelve un text/csv con las columnas básicas del contacto. La
    UI lo dispara desde la bulk-bar (acción 'Exportar CSV').

    PR-Bulk-Comerciales. Export abierto a comerciales: un comercial
    exporta SOLO sus contactos (filtro de propiedad); admin/manager
    exportan toda la selección. `require_user` ya excluye viewers."""
    owned_ids, _foreign = partition_contacts_by_ownership(
        session, body.contact_ids, current_user
    )
    contacts = list(
        session.scalars(
            select(Contact).where(Contact.id.in_(owned_ids))
        )
    )
    if not contacts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ningún contacto válido en la selección.",
        )
    lines = [",".join(_CSV_COLUMNS)]
    for c in contacts:
        row = []
        for col in _CSV_COLUMNS:
            val = getattr(c, col, None)
            text = "" if val is None else str(val)
            # Strip embedded commas y newlines — patrón usado también
            # en /audit-logs/export para evitar parser caprichosos.
            text = text.replace(",", " ").replace("\n", " ").replace("\r", " ")
            row.append(text)
        lines.append(",".join(row))
    record_event(
        session,
        action=Action.CONTACT_UPDATED,
        target_type="contact",
        actor=current_user,
        metadata={
            "bulk_action": "export_csv",
            "affected_count": len(contacts),
            "contact_ids": [c.id for c in contacts][:50],
            "total_targets": len(contacts),
        },
        request=request,
    )
    session.commit()
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=contacts.csv",
        },
    )
