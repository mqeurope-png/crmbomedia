"""Motor de reglas de auto-asignación (Sprint Reglas-Assign — PR-C).

Una `AssignmentRule` declara un árbol IR (`conditions_json`) más un
target de asignación (`primary_user_id` + `secondary_user_ids_json`).
Cuando se evalúa contra un contacto:

  1. Si la regla apunta a un user inactivo / borrado → se auto-desactiva
     en el acto (decisión §5 spec) y queda registrada para la UI.
  2. El árbol se evalúa **in-memory** vía
     `evaluate_contact_against_rules` — sin tocar la BD, decisión O(1).
     Para los campos `assigned_users`/`primary_user` se delega al
     reader in-memory: el contacto recién creado no tiene assignments,
     así que `contains_any` da False, `is_empty` da True — coherente.
  3. Si match + el modo `apply_to` lo permite → se aplica vía
     `repositories.assignments.add_assignment(source="rule:<id>")` con
     `is_primary=True` para el target primario y `is_primary=False`
     para cada secundario.
  4. `stop_on_match=True` corta la cadena. `override_existing=True`
     fuerza la asignación aunque ya hubiese (resetea el primary y
     reemplaza secundarios; el spec exige NO borrar secundarios
     manuales — por eso `override_existing` es opt-in).

Triggers (decisión §2 spec):
  - `fire-on-create` desde POST /api/contacts + reconcilers de Brevo
    y Agile fresh-create.
  - `manual-run` desde /api/assignment-rules/{id}/run.
  - NUNCA on-update (riesgo de bucle: una regla que asigna a X dispara
    `contact.updated`, que volvería a disparar la regla).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import (
    AssignmentRule,
    Contact,
    ContactAssignment,
    User,
)
from app.repositories import assignments as assignments_repo
from app.services.segments.engine import (
    SegmentRuleError,
    evaluate_contact_against_rules,
)

log = logging.getLogger(__name__)


@dataclass
class RuleApplication:
    """Una regla matchó y se aplicó (o se aplicaría en dry-run) a un
    contacto. La UI usa esto para mostrar el log de evaluación."""

    rule_id: str
    rule_name: str
    primary_user_id: str | None
    secondary_user_ids: list[str]
    stopped_chain: bool


@dataclass
class RuleEvalResult:
    """Resultado de evaluar TODAS las reglas activas contra UN contacto.
    `applied` lista las que se aplicaron en orden; `auto_disabled`
    contiene reglas que se desactivaron en esta pasada (decisión §5)."""

    contact_id: str
    applied: list[RuleApplication]
    auto_disabled: list[str]
    skipped_already_assigned: bool


def evaluate_for_contact(
    session: Session,
    contact: Contact,
    *,
    trigger: str = "create",
    dry_run: bool = False,
) -> RuleEvalResult:
    """Aplica las reglas activas a un contacto. Pensado para llamarse
    inmediatamente DESPUÉS de crear el contacto, dentro de la misma
    transacción (el caller hace commit). `trigger` se persiste en el
    audit y el source para trazabilidad.

    No commitea — el caller es el dueño de la transacción.
    """
    _ = trigger  # reservado para el source detallado en futura iteración
    result = RuleEvalResult(
        contact_id=contact.id,
        applied=[],
        auto_disabled=[],
        skipped_already_assigned=False,
    )

    # Pre-check: ¿el contacto ya tiene assignments? Determina cómo
    # responde cada regla con `apply_to=unassigned_only`.
    has_assignments = bool(
        session.scalar(
            select(ContactAssignment.id)
            .where(ContactAssignment.contact_id == contact.id)
            .limit(1)
        )
    )

    rules = list(
        session.scalars(
            select(AssignmentRule)
            .where(AssignmentRule.is_active.is_(True))
            .order_by(AssignmentRule.priority.asc(), AssignmentRule.created_at.asc())
        )
    )
    for rule in rules:
        # Auto-disable si el target primary ya no existe / está inactivo.
        if rule.primary_user_id and not _is_user_active(
            session, rule.primary_user_id
        ):
            rule.is_active = False
            result.auto_disabled.append(rule.id)
            log.warning(
                "assignment_rule.auto_disabled rule_id=%s primary=%s",
                rule.id,
                rule.primary_user_id,
            )
            continue

        if has_assignments and rule.apply_to == "unassigned_only" and not rule.override_existing:
            # La regla no aplica a este contacto; sigue con la siguiente.
            continue

        try:
            tree = json.loads(rule.conditions_json or "{}")
        except json.JSONDecodeError:
            log.warning(
                "assignment_rule.invalid_conditions_json rule_id=%s",
                rule.id,
            )
            continue
        try:
            matched = evaluate_contact_against_rules(contact, tree)
        except SegmentRuleError as exc:
            log.warning(
                "assignment_rule.eval_error rule_id=%s err=%s",
                rule.id,
                exc,
            )
            continue
        if not matched:
            continue

        secondaries = _parse_secondary_ids(rule.secondary_user_ids_json)
        if not dry_run:
            _apply_rule(
                session,
                contact_id=contact.id,
                rule=rule,
                secondaries=secondaries,
            )
            has_assignments = True  # subsiguientes reglas se ven el cambio

        result.applied.append(
            RuleApplication(
                rule_id=rule.id,
                rule_name=rule.name,
                primary_user_id=rule.primary_user_id,
                secondary_user_ids=secondaries,
                stopped_chain=rule.stop_on_match,
            )
        )
        if rule.stop_on_match:
            break

    return result


def _is_user_active(session: Session, user_id: str) -> bool:
    user = session.get(User, user_id)
    return bool(user and user.is_active)


def _parse_secondary_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def _apply_rule(
    session: Session,
    *,
    contact_id: str,
    rule: AssignmentRule,
    secondaries: list[str],
) -> None:
    """Aplica una regla. Filtra targets inactivos en silencio — la
    regla siguió match-eando, no se desactiva por un secundario malo."""
    source = f"rule:{rule.id}"
    if rule.primary_user_id and _is_user_active(session, rule.primary_user_id):
        assignments_repo.add_assignment(
            session,
            contact_id=contact_id,
            user_id=rule.primary_user_id,
            is_primary=True,
            source=source,
            rule_id=rule.id,
        )
    for secondary_id in secondaries:
        if not _is_user_active(session, secondary_id):
            continue
        assignments_repo.add_assignment(
            session,
            contact_id=contact_id,
            user_id=secondary_id,
            is_primary=False,
            source=source,
            rule_id=rule.id,
        )


def run_rule_over_universe(
    session: Session,
    *,
    rule: AssignmentRule,
    actor_user_id: str | None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Aplica UNA regla concreta sobre todos los contactos que
    matcheen. Usado por el endpoint /run del admin. Devuelve métricas
    útiles para la UI ("aplicada a N contactos").

    Para el match usa `build_filter` en SQL — más eficiente que iterar
    a-uno-a-uno y permite respetar `apply_to=unassigned_only` con un
    subquery."""
    from app.services.segments.engine import build_filter  # noqa: PLC0415

    if rule.primary_user_id and not _is_user_active(
        session, rule.primary_user_id
    ):
        rule.is_active = False
        return {
            "rule_id": rule.id,
            "matched": 0,
            "applied": 0,
            "auto_disabled": True,
            "reason": "primary_user_inactive",
        }

    try:
        tree = json.loads(rule.conditions_json or "{}")
    except json.JSONDecodeError:
        return {
            "rule_id": rule.id,
            "matched": 0,
            "applied": 0,
            "error": "invalid_conditions_json",
        }

    flt = build_filter(tree)
    stmt = select(Contact).where(flt, Contact.is_active.is_(True))
    # PR-E: apply_to ahora soporta `new_only` (contactos creados
    # después de la creación de la regla — útil para reglas que sólo
    # deben afectar a leads entrantes), `unassigned_only` (sin
    # asignaciones — comportamiento histórico), y `all_matching` /
    # `all` (cualquier match; equivalente a un "force"). Mantiene
    # `all` como alias de `all_matching` por compatibilidad con
    # reglas pre-PR-E.
    if rule.apply_to == "unassigned_only" and not rule.override_existing:
        stmt = stmt.where(
            ~Contact.id.in_(select(ContactAssignment.contact_id))
        )
    elif rule.apply_to == "new_only":
        stmt = stmt.where(Contact.created_at >= rule.created_at)
        if not rule.override_existing:
            stmt = stmt.where(
                ~Contact.id.in_(select(ContactAssignment.contact_id))
            )
    candidates = list(session.scalars(stmt))
    if dry_run:
        return {
            "rule_id": rule.id,
            "matched": len(candidates),
            "applied": 0,
            "dry_run": True,
        }

    secondaries = _parse_secondary_ids(rule.secondary_user_ids_json)
    applied = 0
    for contact in candidates:
        _apply_rule(
            session,
            contact_id=contact.id,
            rule=rule,
            secondaries=secondaries,
        )
        applied += 1

    return {
        "rule_id": rule.id,
        "matched": len(candidates),
        "applied": applied,
        "actor_user_id": actor_user_id,
    }
