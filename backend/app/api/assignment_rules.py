"""CRUD + run endpoints para `assignment_rules` (Sprint Reglas-Assign).

  GET     /api/assignment-rules
  POST    /api/assignment-rules                    (require_manager)
  GET     /api/assignment-rules/{id}
  PUT     /api/assignment-rules/{id}               (require_manager)
  DELETE  /api/assignment-rules/{id}               (require_manager)
  POST    /api/assignment-rules/{id}/dry-run       (require_manager)
  POST    /api/assignment-rules/{id}/run           (require_manager)

El admin/manager configura reglas; el comercial las ve en read-only
para auditar por qué un contacto cayó en su cartera. Las reglas
validan su `conditions_json` contra el motor de filtros antes de
persistir — si compila mal, 400 con detalle.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_manager, require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import AssignmentRule, User
from app.schemas.assignment_rules import (
    AssignmentRuleDryRun,
    AssignmentRuleRead,
    AssignmentRuleWrite,
)
from app.services.assignment_rules import run_rule_over_universe
from app.services.segments.engine import SegmentRuleError, build_filter

router = APIRouter(prefix="/api/assignment-rules", tags=["assignment-rules"])


def _serialise(rule: AssignmentRule) -> AssignmentRuleRead:
    try:
        conditions = json.loads(rule.conditions_json) if rule.conditions_json else {}
    except json.JSONDecodeError:
        conditions = {}
    try:
        secondaries = (
            json.loads(rule.secondary_user_ids_json)
            if rule.secondary_user_ids_json
            else []
        )
    except json.JSONDecodeError:
        secondaries = []
    return AssignmentRuleRead(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        is_active=rule.is_active,
        priority=rule.priority,
        conditions=conditions,
        primary_user_id=rule.primary_user_id,
        secondary_user_ids=secondaries,
        apply_to=rule.apply_to,
        override_existing=rule.override_existing,
        stop_on_match=rule.stop_on_match,
        created_by_user_id=rule.created_by_user_id,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _validate_conditions(conditions: dict[str, Any]) -> None:
    """Compila el árbol contra el motor: si no compila, 400 con el
    mensaje del motor. Evita guardar reglas que reventarían en eval."""
    try:
        build_filter(conditions or {})
    except SegmentRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Condiciones inválidas: {exc}",
        ) from exc


def _validate_targets(session: Session, payload: AssignmentRuleWrite) -> None:
    """Confirma que el primary + cada secundario existen y están
    activos. Política decidida: si un target referenciado se desactiva
    DESPUÉS, el motor auto-desactiva la regla; pero crear una regla con
    targets ya muertos no aporta valor y confunde al admin."""
    targets: list[str] = []
    if payload.primary_user_id:
        targets.append(payload.primary_user_id)
    targets.extend(payload.secondary_user_ids)
    if not targets:
        return
    rows = list(
        session.scalars(
            select(User).where(User.id.in_(targets), User.is_active.is_(True))
        )
    )
    active_ids = {u.id for u in rows}
    missing = [uid for uid in targets if uid not in active_ids]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Usuarios target no encontrados o inactivos: {missing}",
        )


@router.get("", response_model=list[AssignmentRuleRead])
def list_rules(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[AssignmentRuleRead]:
    _ = current_user
    rows = list(
        session.scalars(
            select(AssignmentRule).order_by(
                AssignmentRule.priority.asc(),
                AssignmentRule.created_at.asc(),
            )
        )
    )
    return [_serialise(r) for r in rows]


@router.get("/{rule_id}", response_model=AssignmentRuleRead)
def get_rule(
    rule_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> AssignmentRuleRead:
    _ = current_user
    rule = session.get(AssignmentRule, rule_id)
    if rule is None:
        raise not_found("AssignmentRule")
    return _serialise(rule)


@router.post("", response_model=AssignmentRuleRead, status_code=201)
def create_rule(
    payload: AssignmentRuleWrite,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> AssignmentRuleRead:
    _validate_conditions(payload.conditions)
    _validate_targets(session, payload)
    now = datetime.now(UTC)
    rule = AssignmentRule(
        id=str(uuid4()),
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
        priority=payload.priority,
        conditions_json=json.dumps(payload.conditions),
        primary_user_id=payload.primary_user_id,
        secondary_user_ids_json=json.dumps(payload.secondary_user_ids)
        if payload.secondary_user_ids
        else None,
        apply_to=payload.apply_to,
        override_existing=payload.override_existing,
        stop_on_match=payload.stop_on_match,
        created_by_user_id=current_user.id,
    )
    rule.created_at = now
    rule.updated_at = now
    session.add(rule)
    record_event(
        session,
        action=Action.ASSIGNMENT_RULE_CREATED,
        target_type="assignment_rule",
        target_id=rule.id,
        actor=current_user,
        metadata={
            "name": payload.name,
            "priority": payload.priority,
            "primary_user_id": payload.primary_user_id,
            "secondary_count": len(payload.secondary_user_ids),
        },
        request=request,
    )
    session.commit()
    session.refresh(rule)
    return _serialise(rule)


@router.put("/{rule_id}", response_model=AssignmentRuleRead)
def update_rule(
    rule_id: str,
    payload: AssignmentRuleWrite,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> AssignmentRuleRead:
    rule = session.get(AssignmentRule, rule_id)
    if rule is None:
        raise not_found("AssignmentRule")
    _validate_conditions(payload.conditions)
    _validate_targets(session, payload)
    rule.name = payload.name
    rule.description = payload.description
    rule.is_active = payload.is_active
    rule.priority = payload.priority
    rule.conditions_json = json.dumps(payload.conditions)
    rule.primary_user_id = payload.primary_user_id
    rule.secondary_user_ids_json = (
        json.dumps(payload.secondary_user_ids)
        if payload.secondary_user_ids
        else None
    )
    rule.apply_to = payload.apply_to
    rule.override_existing = payload.override_existing
    rule.stop_on_match = payload.stop_on_match
    record_event(
        session,
        action=Action.ASSIGNMENT_RULE_UPDATED,
        target_type="assignment_rule",
        target_id=rule.id,
        actor=current_user,
        metadata={"name": payload.name},
        request=request,
    )
    session.commit()
    session.refresh(rule)
    return _serialise(rule)


@router.delete(
    "/{rule_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_rule(
    rule_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Response:
    rule = session.get(AssignmentRule, rule_id)
    if rule is None:
        raise not_found("AssignmentRule")
    session.delete(rule)
    record_event(
        session,
        action=Action.ASSIGNMENT_RULE_DELETED,
        target_type="assignment_rule",
        target_id=rule_id,
        actor=current_user,
        metadata={"name": rule.name},
        request=request,
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{rule_id}/dry-run", response_model=AssignmentRuleDryRun
)
def dry_run_rule(
    rule_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> AssignmentRuleDryRun:
    _ = current_user
    rule = session.get(AssignmentRule, rule_id)
    if rule is None:
        raise not_found("AssignmentRule")
    summary = run_rule_over_universe(
        session,
        rule=rule,
        actor_user_id=current_user.id,
        dry_run=True,
    )
    # auto-disable side-effect del run debería persistirse incluso en
    # dry-run para que la UI vea el warning.
    session.commit()
    return AssignmentRuleDryRun(**_normalise_summary(summary))


@router.post("/{rule_id}/run", response_model=AssignmentRuleDryRun)
def run_rule(
    rule_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> AssignmentRuleDryRun:
    rule = session.get(AssignmentRule, rule_id)
    if rule is None:
        raise not_found("AssignmentRule")
    summary = run_rule_over_universe(
        session,
        rule=rule,
        actor_user_id=current_user.id,
        dry_run=False,
    )
    record_event(
        session,
        action=Action.ASSIGNMENT_RULE_RUN,
        target_type="assignment_rule",
        target_id=rule_id,
        actor=current_user,
        metadata={k: v for k, v in summary.items() if k != "actor_user_id"},
        request=request,
    )
    if summary.get("auto_disabled"):
        record_event(
            session,
            action=Action.ASSIGNMENT_RULE_AUTO_DISABLED,
            target_type="assignment_rule",
            target_id=rule_id,
            actor=current_user,
            metadata={"reason": summary.get("reason")},
            request=request,
        )
    session.commit()
    return AssignmentRuleDryRun(**_normalise_summary(summary))


def _normalise_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """`AssignmentRuleDryRun` solo conoce un subset de claves; las
    `actor_user_id`/etc se filtran fuera del payload de la UI."""
    return {
        "rule_id": summary["rule_id"],
        "matched": summary.get("matched", 0),
        "applied": summary.get("applied", 0),
        "dry_run": summary.get("dry_run", False),
        "auto_disabled": summary.get("auto_disabled", False),
        "reason": summary.get("reason"),
        "error": summary.get("error"),
    }
