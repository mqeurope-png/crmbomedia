"""GDPR / RGPD subject-rights processing.

This module owns the per-request-type business logic invoked by
`POST /api/gdpr/requests/{id}/process`. Each public processor function:

- mutates the `GdprRequest` row to reflect the outcome
  (status, completed_at, evidence_path)
- emits one `gdpr.*` audit event per side-effect via `record_event`
- returns a dict the API marshals into `GdprProcessResult.payload`

The actual `session.commit()` is the caller's responsibility, matching
the rest of the codebase (`record_event` never commits).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.config import Settings
from app.models.crm import (
    AuditLog,
    ConsentStatus,
    Contact,
    ExternalReference,
    GdprRequest,
    GdprRequestStatus,
    GdprRequestType,
    Note,
    SyncLog,
    Task,
    User,
)


class GdprProcessingError(Exception):
    """Raised when a process call cannot complete (e.g. subject not found
    for erasure)."""


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_") or "subject"


def _timestamp_token() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _resolve_export_root(settings: Settings) -> Path:
    root = Path(settings.gdpr_export_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _find_contact(session: Session, subject_email: str) -> Contact | None:
    return session.scalar(
        select(Contact).where(Contact.email == subject_email.lower())
    )


def _serialize_contact(contact: Contact) -> dict[str, Any]:
    return {
        "id": contact.id,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "email": contact.email,
        "phone": contact.phone,
        "origin": contact.origin,
        "tags": contact.tags,
        "commercial_status": contact.commercial_status,
        "owner_user_id": contact.owner_user_id,
        "marketing_consent": contact.marketing_consent.value,
        "is_email_valid": contact.is_email_valid,
        "is_active": contact.is_active,
        "company_id": contact.company_id,
        "created_at": contact.created_at.isoformat(),
        "updated_at": contact.updated_at.isoformat(),
    }


def _serialize_note(note: Note) -> dict[str, Any]:
    return {
        "id": note.id,
        "body": note.body,
        "author_user_id": note.author_user_id,
        "contact_id": note.contact_id,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
    }


def _serialize_task(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "assignee_user_id": task.assignee_user_id,
        "contact_id": task.contact_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def _serialize_external_ref(ref: ExternalReference) -> dict[str, Any]:
    return {
        "id": ref.id,
        "system": ref.system.value,
        "external_id": ref.external_id,
        "account_label": ref.account_label,
        "contact_id": ref.contact_id,
        "created_at": ref.created_at.isoformat(),
        "updated_at": ref.updated_at.isoformat(),
    }


def _collect_subject_data(
    session: Session, contact: Contact | None, subject_email: str
) -> dict[str, Any]:
    """Aggregate every row that mentions the subject. Audit rows are
    filtered by `actor_email` only — the export does not include events
    the subject is merely a target of (those are operational metadata)."""
    audit_rows = list(
        session.scalars(
            select(AuditLog).where(AuditLog.actor_email == subject_email.lower())
        )
    )
    data: dict[str, Any] = {
        "subject_email": subject_email,
        "generated_at": datetime.now(UTC).isoformat(),
        "contact": _serialize_contact(contact) if contact else None,
        "notes": [],
        "tasks": [],
        "external_references": [],
        "audit_logs": [
            {
                "id": row.id,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "ip_address": row.ip_address,
                "user_agent": row.user_agent,
                "created_at": row.created_at.isoformat(),
            }
            for row in audit_rows
        ],
    }
    if contact:
        data["notes"] = [_serialize_note(n) for n in contact.notes]
        data["tasks"] = [_serialize_task(t) for t in contact.tasks]
        data["external_references"] = [
            _serialize_external_ref(ref) for ref in contact.external_refs
        ]
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _flatten_for_csv(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Portability CSV is a long, denormalised list — every row carries a
    `section` discriminator so spreadsheets can be filtered. Operators have
    asked for "one CSV per request" instead of "one CSV per table"."""
    rows: list[dict[str, Any]] = []
    contact = payload.get("contact")
    if contact:
        rows.append({"section": "contact", **contact})
    for note in payload.get("notes", []):
        rows.append({"section": "note", **note})
    for task in payload.get("tasks", []):
        rows.append({"section": "task", **task})
    for ref in payload.get("external_references", []):
        rows.append({"section": "external_reference", **ref})
    for audit in payload.get("audit_logs", []):
        rows.append({"section": "audit_log", **audit})
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("section\n", encoding="utf-8")
        return
    columns: list[str] = ["section"]
    seen = {"section"}
    for row in rows:
        for key in row:
            if key not in seen:
                columns.append(key)
                seen.add(key)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})
    path.write_text(buffer.getvalue(), encoding="utf-8")


def _anonymized_email_marker(subject_email: str) -> str:
    digest = hashlib.sha256(subject_email.lower().encode("utf-8")).hexdigest()[:12]
    return f"[ERASED-{digest}]"


def _process_access(
    session: Session,
    *,
    gdpr_request: GdprRequest,
    actor: User,
    settings: Settings,
    request: Request | None,
) -> dict[str, Any]:
    contact = _find_contact(session, gdpr_request.subject_email)
    if contact:
        gdpr_request.subject_contact_id = contact.id
    payload = _collect_subject_data(session, contact, gdpr_request.subject_email)

    root = _resolve_export_root(settings)
    filename = f"access_{_slug(gdpr_request.subject_email)}_{_timestamp_token()}.json"
    path = root / filename
    _write_json(path, payload)
    relative = str(path)

    record_event(
        session,
        action=Action.GDPR_EXPORT_GENERATED,
        target_type="gdpr_request",
        target_id=gdpr_request.id,
        actor=actor,
        metadata={
            "request_type": gdpr_request.request_type.value,
            "subject_email": gdpr_request.subject_email,
            "evidence_path": relative,
            "formats": ["json"],
        },
        request=request,
    )

    gdpr_request.evidence_path = relative
    return {
        "evidence_path": relative,
        "formats": ["json"],
        "counts": {
            "notes": len(payload["notes"]),
            "tasks": len(payload["tasks"]),
            "external_references": len(payload["external_references"]),
            "audit_logs": len(payload["audit_logs"]),
            "contact_found": payload["contact"] is not None,
        },
    }


def _process_portability(
    session: Session,
    *,
    gdpr_request: GdprRequest,
    actor: User,
    settings: Settings,
    request: Request | None,
) -> dict[str, Any]:
    contact = _find_contact(session, gdpr_request.subject_email)
    if contact:
        gdpr_request.subject_contact_id = contact.id
    payload = _collect_subject_data(session, contact, gdpr_request.subject_email)

    root = _resolve_export_root(settings)
    stem = f"portability_{_slug(gdpr_request.subject_email)}_{_timestamp_token()}"
    json_path = root / f"{stem}.json"
    csv_path = root / f"{stem}.csv"
    _write_json(json_path, payload)
    _write_csv(csv_path, _flatten_for_csv(payload))
    json_rel = str(json_path)
    csv_rel = str(csv_path)

    record_event(
        session,
        action=Action.GDPR_EXPORT_GENERATED,
        target_type="gdpr_request",
        target_id=gdpr_request.id,
        actor=actor,
        metadata={
            "request_type": gdpr_request.request_type.value,
            "subject_email": gdpr_request.subject_email,
            "evidence_path": json_rel,
            "evidence_path_csv": csv_rel,
            "formats": ["json", "csv"],
        },
        request=request,
    )

    gdpr_request.evidence_path = json_rel
    return {
        "evidence_path": json_rel,
        "evidence_path_csv": csv_rel,
        "formats": ["json", "csv"],
        "counts": {
            "notes": len(payload["notes"]),
            "tasks": len(payload["tasks"]),
            "external_references": len(payload["external_references"]),
            "audit_logs": len(payload["audit_logs"]),
            "contact_found": payload["contact"] is not None,
        },
    }


# Endpoint paths an operator can use to perform a rectification update.
# Documented inline so the response is self-describing and the UI can list
# them directly without a second round-trip.
RECTIFICATION_ENDPOINTS = [
    {
        "method": "PATCH",
        "path": "/api/contacts/{contact_id}",
        "description": "Editar campos del contacto (nombre, email, teléfono, tags, etc.)",
    },
    {
        "method": "PATCH",
        "path": "/api/companies/{company_id}",
        "description": "Editar datos de la empresa vinculada si el cambio le afecta.",
    },
    {
        "method": "POST",
        "path": "/api/contacts/{contact_id}/notes",
        "description": (
            "Añadir una nota interna documentando la rectificación realizada "
            "(qué se cambió, cuándo, base jurídica)."
        ),
    },
]


def _process_rectification(
    session: Session,
    *,
    gdpr_request: GdprRequest,
    actor: User,
    request: Request | None,
) -> dict[str, Any]:
    contact = _find_contact(session, gdpr_request.subject_email)
    if contact:
        gdpr_request.subject_contact_id = contact.id

    record_event(
        session,
        action=Action.GDPR_RECTIFICATION_GUIDANCE,
        target_type="gdpr_request",
        target_id=gdpr_request.id,
        actor=actor,
        metadata={
            "subject_email": gdpr_request.subject_email,
            "contact_id": contact.id if contact else None,
        },
        request=request,
    )
    return {
        "contact_id": contact.id if contact else None,
        "contact_found": contact is not None,
        "endpoints": RECTIFICATION_ENDPOINTS,
        "guidance": (
            "La rectificación se aplica con los endpoints PATCH habituales. "
            "Tras aplicar el cambio, documente la base jurídica en una nota "
            "interna y vuelva a abrir esta solicitud para marcarla como "
            "completada."
        ),
    }


def _process_erasure(
    session: Session,
    *,
    gdpr_request: GdprRequest,
    actor: User,
    request: Request | None,
) -> dict[str, Any]:
    contact = _find_contact(session, gdpr_request.subject_email)
    counts = {
        "contact_deleted": False,
        "notes_deleted": 0,
        "tasks_deleted": 0,
        "external_references_deleted": 0,
        "audit_logs_anonymized": 0,
    }
    if contact:
        counts["notes_deleted"] = len(contact.notes)
        counts["tasks_deleted"] = len(contact.tasks)
        counts["external_references_deleted"] = len(contact.external_refs)
        contact_id = contact.id
        gdpr_request.subject_contact_id = contact_id
        # SyncLog has a FK to contacts.id but no relationship; clear it
        # explicitly so the cascade-style delete below doesn't violate the
        # DB-level constraint under MySQL (SQLite ignores it by default).
        session.execute(
            update(SyncLog)
            .where(SyncLog.contact_id == contact_id)
            .values(contact_id=None)
        )
        # Notes, tasks and external_references cascade through the
        # `all, delete-orphan` relationship on Contact.
        session.delete(contact)
        session.flush()
        counts["contact_deleted"] = True
        record_event(
            session,
            action=Action.GDPR_CONTACT_ERASED,
            target_type="contact",
            target_id=contact_id,
            actor=actor,
            metadata={
                "subject_email": gdpr_request.subject_email,
                **counts,
            },
            request=request,
        )

    # Anonymise actor_email on existing audit rows so operators can still
    # reason about historic activity without keeping personal data. The
    # original row id is preserved; only the email column is rewritten.
    marker = _anonymized_email_marker(gdpr_request.subject_email)
    affected_rows = list(
        session.scalars(
            select(AuditLog).where(
                AuditLog.actor_email == gdpr_request.subject_email.lower()
            )
        )
    )
    for row in affected_rows:
        row.actor_email = marker
    counts["audit_logs_anonymized"] = len(affected_rows)

    if affected_rows:
        record_event(
            session,
            action=Action.GDPR_AUDIT_ANONYMIZED,
            target_type="gdpr_request",
            target_id=gdpr_request.id,
            actor=actor,
            metadata={
                "subject_email": gdpr_request.subject_email,
                "marker": marker,
                "rows_anonymized": len(affected_rows),
            },
            request=request,
        )

    return {"marker": marker, **counts}


def _process_objection(
    session: Session,
    *,
    gdpr_request: GdprRequest,
    actor: User,
    request: Request | None,
) -> dict[str, Any]:
    contact = _find_contact(session, gdpr_request.subject_email)
    if not contact:
        # No contact = nothing to flip. We still complete the request so
        # the operator can document the decision in `notes`.
        return {"contact_found": False, "marketing_consent": None, "is_active": None}

    gdpr_request.subject_contact_id = contact.id
    contact.marketing_consent = ConsentStatus.DENIED
    contact.is_active = False
    record_event(
        session,
        action=Action.GDPR_OBJECTION_APPLIED,
        target_type="contact",
        target_id=contact.id,
        actor=actor,
        metadata={
            "subject_email": gdpr_request.subject_email,
            "marketing_consent": ConsentStatus.DENIED.value,
            "is_active": False,
        },
        request=request,
    )
    return {
        "contact_found": True,
        "contact_id": contact.id,
        "marketing_consent": ConsentStatus.DENIED.value,
        "is_active": False,
    }


def process_request(
    session: Session,
    *,
    gdpr_request: GdprRequest,
    actor: User,
    settings: Settings,
    request: Request | None,
) -> dict[str, Any]:
    """Dispatch on `gdpr_request.request_type` and return the per-type
    payload. Sets `status=COMPLETED` and `completed_at` on success."""
    if gdpr_request.status == GdprRequestStatus.COMPLETED:
        raise GdprProcessingError("Request is already completed")

    request_type = gdpr_request.request_type
    if request_type == GdprRequestType.ACCESS:
        payload = _process_access(
            session,
            gdpr_request=gdpr_request,
            actor=actor,
            settings=settings,
            request=request,
        )
    elif request_type == GdprRequestType.PORTABILITY:
        payload = _process_portability(
            session,
            gdpr_request=gdpr_request,
            actor=actor,
            settings=settings,
            request=request,
        )
    elif request_type == GdprRequestType.RECTIFICATION:
        payload = _process_rectification(
            session,
            gdpr_request=gdpr_request,
            actor=actor,
            request=request,
        )
    elif request_type == GdprRequestType.ERASURE:
        payload = _process_erasure(
            session,
            gdpr_request=gdpr_request,
            actor=actor,
            request=request,
        )
    elif request_type == GdprRequestType.OBJECTION:
        payload = _process_objection(
            session,
            gdpr_request=gdpr_request,
            actor=actor,
            request=request,
        )
    else:  # pragma: no cover - StrEnum keeps this exhaustive
        raise GdprProcessingError(f"Unknown request type: {request_type}")

    gdpr_request.status = GdprRequestStatus.COMPLETED
    gdpr_request.completed_at = datetime.now(UTC)

    record_event(
        session,
        action=Action.GDPR_REQUEST_PROCESSED,
        target_type="gdpr_request",
        target_id=gdpr_request.id,
        actor=actor,
        metadata={
            "request_type": request_type.value,
            "subject_email": gdpr_request.subject_email,
            "status": gdpr_request.status.value,
        },
        request=request,
    )
    return payload
