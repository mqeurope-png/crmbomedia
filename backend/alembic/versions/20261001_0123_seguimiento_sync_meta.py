"""Seguimiento (app) — estado propio del espejo: `seguimiento_sync_meta`.

Tabla clave → valor del espejo BoHub ↔ hoja. La primera clave es
`formato_bd`: el nº de columnas con el que están escritas las filas que el
espejo guarda (`seguimiento_sync_snapshot`, `seguimiento_manual`,
`seguimiento_legacy`). Con la columna «Courier» la hoja pasa de 19 a 20
columnas; el espejo pone al día esas filas en su propia pasada (bajo el mismo
cerrojo que la escritura de la hoja, y en la misma transacción que deja la
marca), no aquí: así nunca conviven una pasada vieja y datos ya convertidos.

Vuelta atrás (`downgrade`): si el espejo ya convirtió lo guardado (marca =
20), se devuelve al formato de 19 columnas —se quita «Courier»— ANTES de
borrar la tabla, así que al volver a subir se convierte una sola vez. La HOJA
no la toca una migración. Para volver de verdad a la versión anterior, en este
orden (docs/guia-erp-usuario.md): 1) parar la sincronización (interruptor
automático apagado, `worker-sync` parado, nadie pulsando «Actualizar hoja de
Drive»); 2) este `downgrade`, con la imagen nueva en un contenedor suelto —sin
la tabla, cualquier pasada de la versión nueva se para antes de tocar la
hoja—; 3) borrar a mano la columna O («Courier») de «Seguimiento (app)» (o
restaurar una versión de la hoja anterior a la migración); 4) arrancar la
versión anterior. En otro orden, la versión nueva volvería a insertar la
columna, o la anterior leería corrido todo lo de detrás de «Envío».

Sin DEFAULT en la columna TEXT (MySQL lo rechaza, error 1101): el ORM siempre
da valor.

Revision ID: 20261001_0123
Revises: 20260930_0122
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20261001_0123"
down_revision = "20260930_0122"
branch_labels = None
depends_on = None

#: En las filas de 20 columnas: «Courier» (detrás de «Envío») y la «id» (la
#: última). Fijos aquí: la migración no depende del código de la app.
_COURIER = 14
_ID_20 = 19

_meta = sa.table(
    "seguimiento_sync_meta", sa.column("clave", sa.String), sa.column("valor", sa.Text),
)
_snapshot = sa.table(
    "seguimiento_sync_snapshot",
    sa.column("row_id", sa.String), sa.column("kind", sa.String),
    sa.column("values_json", sa.Text),
)
_manual = sa.table(
    "seguimiento_manual",
    sa.column("id", sa.String), sa.column("row_id", sa.String),
    sa.column("values_json", sa.Text),
)
_legacy = sa.table(
    "seguimiento_legacy", sa.column("id", sa.String), sa.column("raw_json", sa.Text),
)


def upgrade() -> None:
    op.create_table(
        "seguimiento_sync_meta",
        sa.Column("clave", sa.String(length=40), nullable=False),
        sa.Column("valor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("clave"),
    )


def _lista(raw: str | None) -> list[Any]:
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def sin_courier(fila: list[Any], row_id: str | None = None) -> list[Any] | None:
    """Una fila guardada con 20 columnas → la de 19 (se quita «Courier»). Con
    `row_id` (la foto y las filas manuales llevan su id en la última columna),
    solo si la id está en la posición de 20. None si no hay nada que cambiar."""
    if row_id is not None and not (
        len(fila) > _ID_20 and str(fila[_ID_20] or "").strip() == row_id
    ):
        return None
    if len(fila) <= _COURIER:
        return None
    return [*fila[:_COURIER], *fila[_COURIER + 1:]]


def _guardar(bind: Any, tabla: Any, pk: Any, valor_pk: str, columna: str,
             fila: list[Any]) -> None:
    bind.execute(tabla.update().where(pk == valor_pk).values(
        {columna: json.dumps(fila, ensure_ascii=False, default=str)},
    ))


def downgrade() -> None:
    bind = op.get_bind()
    marca = bind.execute(
        sa.select(_meta.c.valor).where(_meta.c.clave == "formato_bd")
    ).scalar()
    if str(marca or "").strip() == "20":
        for row_id, kind, raw in bind.execute(sa.select(
            _snapshot.c.row_id, _snapshot.c.kind, _snapshot.c.values_json,
        )).fetchall():
            if kind == "legacy":
                continue                    # del histórico solo se guarda la presencia
            fila = sin_courier(_lista(raw), str(row_id))
            if fila is not None:
                _guardar(bind, _snapshot, _snapshot.c.row_id, row_id, "values_json", fila)
        for pk, row_id, raw in bind.execute(sa.select(
            _manual.c.id, _manual.c.row_id, _manual.c.values_json,
        )).fetchall():
            fila = sin_courier(_lista(raw), str(row_id))
            if fila is not None:
                _guardar(bind, _manual, _manual.c.id, pk, "values_json", fila)
        for pk, raw in bind.execute(sa.select(_legacy.c.id, _legacy.c.raw_json)).fetchall():
            fila = sin_courier(_lista(raw))
            if fila is not None:
                _guardar(bind, _legacy, _legacy.c.id, pk, "raw_json", fila)
    op.drop_table("seguimiento_sync_meta")
