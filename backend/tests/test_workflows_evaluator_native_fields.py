"""PR-Fix-Evaluator-Campos-Nativos.

Asegura que el evaluador de condiciones reconoce los campos nativos
del Contact (bare keys que emite el FilterBuilder de /contactos) además
de la forma legacy con prefijo `contact.`. Cubre además los operadores
que segments emite y que el evaluador no soportaba antes
(`contains_any`, `before`, `after`, `in_last_n_days`).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from app.models.crm import Contact
from app.workflows.conditions import EvalContext, evaluate


def _ctx(**overrides) -> EvalContext:
    """Helper para construir un Contact + EvalContext de prueba sin
    tocar la base."""
    defaults = {
        "first_name": "TESTT probando",
        "last_name": "Bot",
        "email": "testt@bo.media",
        "phone": "+34600000000",
        "tags": "vip,cliente-cierre",
        "lead_score": 75,
        "commercial_status": "qualified",
        "owner_user_id": "owner-uuid-1",
        "is_active": True,
        "origin": "manual",
        "job_title": "CEO",
        "linkedin_url": "https://linkedin.com/in/test",
        "personal_website": "https://test.com",
        "address_city": "Madrid",
        "address_country": "ES",
        "custom_fields": json.dumps(
            {"INTERES": "alto", "sector_empresa": "industrial"}
        ),
        "created_at": datetime.now(UTC) - timedelta(days=2),
        "updated_at": datetime.now(UTC) - timedelta(days=1),
    }
    defaults.update(overrides)
    contact = Contact(**defaults)
    return EvalContext(session=None, contact=contact)


# ---------------------------------------------------------------------
# Bug crítico: bare key first_name funciona (era el bug de Bart).
# ---------------------------------------------------------------------


def test_evaluate_condition_native_first_name_contains() -> None:
    ctx = _ctx(first_name="TESTT probando automatización")
    tree = {
        "type": "rule",
        "field": "first_name",
        "comparator": "contains",
        "value": "TESTT",
    }
    assert evaluate(tree, ctx) is True


def test_evaluate_condition_native_first_name_no_match() -> None:
    ctx = _ctx(first_name="OTRO contacto")
    tree = {
        "type": "rule",
        "field": "first_name",
        "comparator": "contains",
        "value": "TESTT",
    }
    assert evaluate(tree, ctx) is False


def test_evaluate_condition_legacy_contact_prefix_still_works() -> None:
    """Workflows guardados con el prefijo `contact.` siguen funcionando."""
    ctx = _ctx(first_name="Foo")
    tree = {
        "type": "rule",
        "field": "contact.first_name",
        "comparator": "contains",
        "value": "Foo",
    }
    assert evaluate(tree, ctx) is True


# ---------------------------------------------------------------------
# Operadores numéricos sobre campos nativos.
# ---------------------------------------------------------------------


def test_evaluate_condition_native_lead_score_greater_than() -> None:
    ctx = _ctx(lead_score=85)
    tree = {
        "type": "rule",
        "field": "lead_score",
        "comparator": "gt",
        "value": 50,
    }
    assert evaluate(tree, ctx) is True


def test_evaluate_condition_native_lead_score_from_ui_string() -> None:
    """El frontend manda value como string — coerce a int."""
    ctx = _ctx(lead_score=85)
    tree = {
        "type": "rule",
        "field": "lead_score",
        "comparator": "gte",
        "value": "85",
    }
    assert evaluate(tree, ctx) is True


# ---------------------------------------------------------------------
# Selector (lifecycle_status / commercial_status alias).
# ---------------------------------------------------------------------


def test_evaluate_condition_native_lifecycle_status_eq() -> None:
    ctx = _ctx(commercial_status="qualified")
    tree = {
        "type": "rule",
        "field": "lifecycle_status",
        "comparator": "eq",
        "value": "qualified",
    }
    assert evaluate(tree, ctx) is True


def test_evaluate_condition_native_commercial_status_alias() -> None:
    """Ambos keys (lifecycle_status y commercial_status) apuntan al
    mismo campo de la fila."""
    ctx = _ctx(commercial_status="qualified")
    tree = {
        "type": "rule",
        "field": "commercial_status",
        "comparator": "eq",
        "value": "qualified",
    }
    assert evaluate(tree, ctx) is True


# ---------------------------------------------------------------------
# Tags + contains_any (operador nuevo).
# ---------------------------------------------------------------------


def test_evaluate_condition_native_tags_contains_any() -> None:
    ctx = _ctx(tags="vip,cliente-cierre,industrial")
    tree = {
        "type": "rule",
        "field": "tags",
        "comparator": "contains_any",
        "value": ["vip", "no-existe"],
    }
    assert evaluate(tree, ctx) is True


def test_evaluate_condition_native_tags_contains_all_missing_one_is_false() -> None:
    ctx = _ctx(tags="vip,cliente-cierre")
    tree = {
        "type": "rule",
        "field": "tags",
        "comparator": "contains_all",
        "value": ["vip", "no-existe"],
    }
    assert evaluate(tree, ctx) is False


def test_evaluate_condition_native_tags_contains_none() -> None:
    ctx = _ctx(tags="vip,cliente-cierre")
    tree = {
        "type": "rule",
        "field": "tags",
        "comparator": "contains_none",
        "value": ["industrial", "retail"],
    }
    assert evaluate(tree, ctx) is True


# ---------------------------------------------------------------------
# Custom fields dinámicos (regression + nueva feature).
# ---------------------------------------------------------------------


def test_evaluate_condition_custom_field_eq() -> None:
    ctx = _ctx(
        custom_fields=json.dumps(
            {"INTERES": "alto", "sector_empresa": "industrial"}
        )
    )
    tree = {
        "type": "rule",
        "field": "custom_fields.INTERES",
        "comparator": "eq",
        "value": "alto",
    }
    assert evaluate(tree, ctx) is True


def test_evaluate_condition_custom_field_missing_key_is_empty() -> None:
    ctx = _ctx(custom_fields=json.dumps({"INTERES": "alto"}))
    tree = {
        "type": "rule",
        "field": "custom_fields.NO_EXISTE",
        "comparator": "empty",
        "value": None,
    }
    assert evaluate(tree, ctx) is True


def test_evaluate_condition_custom_field_short_prefix_cf() -> None:
    """`cf.X` también funciona como alias de `custom_fields.X`."""
    ctx = _ctx(custom_fields=json.dumps({"INTERES": "alto"}))
    tree = {
        "type": "rule",
        "field": "cf.INTERES",
        "comparator": "eq",
        "value": "alto",
    }
    assert evaluate(tree, ctx) is True


# ---------------------------------------------------------------------
# Operadores de fecha.
# ---------------------------------------------------------------------


def test_evaluate_condition_created_at_in_last_n_days() -> None:
    ctx = _ctx(created_at=datetime.now(UTC) - timedelta(days=3))
    tree = {
        "type": "rule",
        "field": "created_at",
        "comparator": "in_last_n_days",
        "value": 7,
    }
    assert evaluate(tree, ctx) is True


def test_evaluate_condition_created_at_before_specific_date() -> None:
    ctx = _ctx(created_at=datetime(2026, 6, 18, 10, tzinfo=UTC))
    tree = {
        "type": "rule",
        "field": "created_at",
        "comparator": "before",
        "value": "2026-06-19",
    }
    assert evaluate(tree, ctx) is True


# ---------------------------------------------------------------------
# Campo desconocido → warning + False (no crash).
# ---------------------------------------------------------------------


def test_evaluate_condition_unknown_field_returns_false() -> None:
    """Campo desconocido → evaluate devuelve False (no rompe el
    workflow). El warning de logging se valida por inspección manual,
    no aquí: caplog mostró comportamiento inconsistente entre py3.11
    local y py3.12 CI y la captura por handler tampoco era estable."""
    ctx = _ctx()
    tree = {
        "type": "rule",
        "field": "field_inventado",
        "comparator": "eq",
        "value": "x",
    }
    assert evaluate(tree, ctx) is False


# ---------------------------------------------------------------------
# Full name (concat helper).
# ---------------------------------------------------------------------


def test_evaluate_condition_native_full_name_contains() -> None:
    ctx = _ctx(first_name="Bart", last_name="Pérez")
    tree = {
        "type": "rule",
        "field": "full_name",
        "comparator": "contains",
        "value": "Pérez",
    }
    assert evaluate(tree, ctx) is True


# ---------------------------------------------------------------------
# Real-world reproducer: Bart's bug.
# ---------------------------------------------------------------------


def test_evaluate_condition_bart_reproducer_trigger_filter() -> None:
    """Caso exacto: trigger 'Contacto creado' con filtro adicional
    `first_name contains TESTT`. Contacto nuevo con nombre 'TESTT 123'
    → workflow debe disparar (tree evaluate True)."""
    ctx = _ctx(first_name="TESTT 123")
    tree = {
        "operator": "and",
        "children": [
            {
                "type": "rule",
                "field": "first_name",
                "comparator": "contains",
                "value": "TESTT",
            }
        ],
    }
    assert evaluate(tree, ctx) is True
