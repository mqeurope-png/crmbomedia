"""drop legacy UNIQUE(system) on integration_accounts

Revision ID: 20260516_0008
Revises: 20260515_0007
Create Date: 2026-05-16 00:00:00

Bug fix for the multi-account refactor (`20260515_0007`).

That migration added the composite UNIQUE on `(system, account_id)` but
never dropped the legacy UNIQUE on `system` that came from the original
`20260507_0002` migration. On SQLite the bug is hidden because
`batch_alter_table` recreates the table without the column-level UNIQUE
constraint; on MySQL `batch_alter_table` is a no-op (regular ALTERs)
and the legacy constraint survives. Result: in production, creating a
second account for the same system fails with `IntegrityError 1062
"Duplicate entry 'agilecrm' for key 'integration_accounts.system'"`.

This migration is intentionally **introspective**: it inspects the
current schema rather than assuming a specific constraint name, drops
anything matching `UNIQUE(system)` and ensures the composite remains in
place. Wrapped in `op.batch_alter_table` so SQLite handles the drop via
table recreation while MySQL emits a plain ALTER. Idempotent — safe to
re-run.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260516_0008"
down_revision: str | None = "20260515_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "integration_accounts"
COMPOSITE_NAME = "uq_integration_accounts_system_account_id"


def _legacy_system_unique_constraints(inspector: sa.engine.Inspector) -> list[str]:
    """Names of every UNIQUE constraint whose column set is exactly
    `["system"]`. MySQL exposes the legacy `sa.UniqueConstraint("system")`
    here; SQLite exposes auto-named entries that we *cannot* drop via the
    public API, so the migration relies on batch-mode table recreation
    to clear those."""
    names: list[str] = []
    for uc in inspector.get_unique_constraints(TABLE):
        if uc.get("column_names") == ["system"] and uc.get("name"):
            names.append(uc["name"])
    return names


def _legacy_system_unique_indexes(inspector: sa.engine.Inspector) -> list[str]:
    """Names of every UNIQUE *index* whose column set is exactly
    `["system"]`. On MySQL a UNIQUE constraint also appears here under
    the same name; on SQLite the auto-name `sqlite_autoindex_*` shows up
    but can't be dropped — only batch-mode recreation can drop it."""
    names: list[str] = []
    for idx in inspector.get_indexes(TABLE):
        if (
            idx.get("column_names") == ["system"]
            and idx.get("unique")
            and idx.get("name")
            and not idx["name"].startswith("sqlite_autoindex_")
        ):
            names.append(idx["name"])
    return names


def _has_composite(inspector: sa.engine.Inspector) -> bool:
    for uc in inspector.get_unique_constraints(TABLE):
        if uc.get("column_names") == ["system", "account_id"]:
            return True
    for idx in inspector.get_indexes(TABLE):
        if idx.get("column_names") == ["system", "account_id"] and idx.get("unique"):
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    legacy_constraints = _legacy_system_unique_constraints(inspector)
    legacy_indexes = _legacy_system_unique_indexes(inspector)

    needs_work = bool(legacy_constraints or legacy_indexes)

    if needs_work:
        # batch_alter_table makes this work on SQLite (recreates the
        # table without the legacy unique) and on MySQL (emits ALTER
        # TABLE ... DROP INDEX / DROP CONSTRAINT). On SQLite the
        # drop_* calls inside the batch are best-effort because the
        # table recreate already discards the auto-named entries; we
        # swallow the exception so the migration doesn't wedge.
        with op.batch_alter_table(TABLE) as batch_op:
            for name in legacy_constraints:
                try:
                    batch_op.drop_constraint(name, type_="unique")
                except Exception:  # noqa: BLE001
                    pass
            for name in legacy_indexes:
                if name in legacy_constraints:
                    continue
                try:
                    batch_op.drop_index(name)
                except Exception:  # noqa: BLE001
                    pass

    # Ensure the composite constraint is in place.
    inspector = sa.inspect(bind)
    if not _has_composite(inspector):
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.create_unique_constraint(
                COMPOSITE_NAME,
                ["system", "account_id"],
            )


def downgrade() -> None:
    """Restore the legacy `UNIQUE(system)` and drop the composite.

    DESTRUCTIVE: if the table currently holds more than one row per
    system (which is the whole point of the refactor), this downgrade
    will refuse to apply. That's intentional — there is no safe way to
    revert without losing data.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_composite(inspector):
        with op.batch_alter_table(TABLE) as batch_op:
            try:
                batch_op.drop_constraint(COMPOSITE_NAME, type_="unique")
            except Exception:  # noqa: BLE001
                try:
                    batch_op.drop_index(COMPOSITE_NAME)
                except Exception:  # noqa: BLE001
                    pass
    if not _legacy_system_unique_constraints(inspector):
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.create_unique_constraint(
                "uq_integration_accounts_system",
                ["system"],
            )
