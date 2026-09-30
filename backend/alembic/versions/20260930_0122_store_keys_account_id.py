"""Ajustes ERP — una sola clave de tienda: el `account_id` de la cuenta Woo.

artisJet se guardaba como `artisjet` en las reglas de contrapartida (#495), la
PayPal por tienda y los remitentes, pero su cuenta Woo es `artisjet-europe`
(que es lo que ya usaba la serie por tienda). Las reglas y el remitente de
artisJet no casaban nunca. Se renombran las claves de tienda heredadas en
`erp_settings.factusol_series_json`:

- `contrapartida_rules[].tienda`,
- `paypal_contrapartidas_by_store`, `store_email_from`, `ref_prefix_by_store`
  y `by_source` (claves del mapeo).

Solo se renombra una clave heredada que NO es ya una cuenta Woo real, y solo
hacia una cuenta Woo que existe (o, sin cuentas Woo dadas de alta, hacia la
clave conocida). Si la clave nueva ya existe, se FUSIONA sin pisarla (manda la
nueva). Idempotente: una segunda pasada no cambia nada.

Revision ID: 20260930_0122
Revises: 20260930_0121
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20260930_0122"
down_revision = "20260930_0121"
branch_labels = None
depends_on = None

#: Claves de tienda heredadas → `account_id` real.
LEGACY_STORE_KEYS: dict[str, str] = {
    "artisjet": "artisjet-europe",
    "artisjetspain": "artisjet-europe",
    "flux": "fluxlasers",
    "flux-lasers": "fluxlasers",
}
#: Mapeos por tienda dentro del blob (clave = tienda).
STORE_MAPS = (
    "paypal_contrapartidas_by_store",
    "store_email_from",
    "ref_prefix_by_store",
    "by_source",
)


def _renames(woo_accounts: set[str]) -> dict[str, str]:
    """Qué claves heredadas se renombran, según las cuentas Woo reales."""
    out: dict[str, str] = {}
    for old, new in LEGACY_STORE_KEYS.items():
        if old in woo_accounts:
            continue            # esa clave ES una cuenta real: se queda
        if woo_accounts and new not in woo_accounts:
            continue            # no se inventan claves de tiendas que no existen
        out[old] = new
    return out


def migrate_series(series: dict[str, Any], renames: dict[str, str]) -> dict[str, Any]:
    """Blob con las claves de tienda renombradas (copia). Fusiona sin pisar."""
    out = dict(series)
    for name in STORE_MAPS:
        mapping = out.get(name)
        if not isinstance(mapping, dict):
            continue
        fixed: dict[str, Any] = {}
        for key, value in mapping.items():
            if str(key).strip().lower() in renames:
                continue
            fixed[key] = value
        for key, value in mapping.items():
            new = renames.get(str(key).strip().lower())
            if new is not None and new not in fixed:
                fixed[new] = value      # la nueva ya existía → se respeta
        out[name] = fixed
    rules = out.get("contrapartida_rules")
    if isinstance(rules, list):
        fixed_rules: list[Any] = []
        seen: set[str] = set()
        for rule in rules:
            if isinstance(rule, dict):
                tienda = str(rule.get("tienda") or "").strip().lower()
                if tienda in renames:
                    rule = {**rule, "tienda": renames[tienda]}
                firma = json.dumps(rule, sort_keys=True, ensure_ascii=False)
                if firma in seen:
                    continue    # misma regla repetida tras renombrar
                seen.add(firma)
            fixed_rules.append(rule)
        out["contrapartida_rules"] = fixed_rules
    return out


def upgrade() -> None:
    bind = op.get_bind()
    woo_accounts = {
        str(row[0] or "").strip().lower()
        for row in bind.execute(sa.text(
            "SELECT account_id FROM integration_accounts WHERE system = 'woocommerce'"
        ))
    }
    renames = _renames(woo_accounts)
    if not renames:
        return
    rows = bind.execute(sa.text(
        "SELECT id, factusol_series_json FROM erp_settings "
        "WHERE factusol_series_json IS NOT NULL"
    )).fetchall()
    for row_id, raw in rows:
        try:
            series = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            continue
        if not isinstance(series, dict):
            continue
        fixed = migrate_series(series, renames)
        if fixed != series:
            bind.execute(
                sa.text("UPDATE erp_settings SET factusol_series_json = :v WHERE id = :id"),
                {"v": json.dumps(fixed, ensure_ascii=False), "id": row_id},
            )


def downgrade() -> None:
    # No se deshace: la clave vieja (`artisjet`) no era de ninguna cuenta Woo.
    pass
