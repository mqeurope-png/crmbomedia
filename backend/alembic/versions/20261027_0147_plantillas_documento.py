"""Plantillas de documento: las plantillas de oferta pasan a servir para todo.

Las plantillas del #559 (`ofertas_plantillas`) eran un conjunto de líneas con
sus artículos de FACTUSOL; que acabe siendo una oferta, una proforma o un
pedido se decide al usarla. Esta migración:

1. RENOMBRA las tablas (`ofertas_plantillas` → `plantillas_documento`,
   `ofertas_plantillas_lineas` → `plantillas_documento_lineas`) en vez de
   copiarlas: la plantilla que existe, sus líneas (con sus artículos) y el
   enlace de las ofertas (`ofertas.plantilla_id`, cuya clave ajena sigue a la
   tabla renombrada) quedan tal cual, con los mismos ids.
2. Añade a la plantilla `idioma` (las existentes, `es`), `carpeta_id` y el
   origen general: `origen_tipo` (presupuesto / albaran / pedido / manual),
   `origen_codigo` (antes `origen_codpre`), `origen_order_id` y
   `origen_numero` (el que se enseña, p. ej. `2-000040`).
3. Crea las carpetas (`plantillas_documento_carpetas`, jerárquicas como las de
   las plantillas de correo), su acceso (`plantillas_documento_carpetas_
   acceso`, la «compartición»), la carpeta por defecto de cada usuario
   (`plantillas_documento_preferencias`) y las etiquetas
   (`plantillas_documento_etiquetas`).

Bajar deshace los cinco pasos y vuelve a dejar `origen_codpre` (solo en las
que venían de un presupuesto). Las carpetas, accesos y etiquetas se pierden.

Revision ID: 20261027_0147
Revises: 20261012_0146
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0147"
down_revision: str | None = "20261012_0146"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plantillas_documento_carpetas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("nombre", sa.String(length=200), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["plantillas_documento_carpetas.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plantillas_documento_carpetas_parent_id",
                    "plantillas_documento_carpetas", ["parent_id"])
    op.create_index("ix_plantillas_documento_carpetas_owner_user_id",
                    "plantillas_documento_carpetas", ["owner_user_id"])

    # 1. Renombrar: datos, ids y claves ajenas se conservan.
    op.rename_table("ofertas_plantillas", "plantillas_documento")
    op.rename_table("ofertas_plantillas_lineas", "plantillas_documento_lineas")
    # El índice nuevo antes de quitar el viejo: en MySQL la clave ajena de las
    # líneas necesita un índice sobre `plantilla_id` en todo momento.
    op.create_index("ix_plantillas_documento_lineas_plantilla_id",
                    "plantillas_documento_lineas", ["plantilla_id"])
    op.drop_index("ix_ofertas_plantillas_lineas_plantilla_id",
                  table_name="plantillas_documento_lineas")

    # 2. Idioma, carpeta y origen general.
    with op.batch_alter_table("plantillas_documento") as batch:
        batch.add_column(sa.Column("idioma", sa.String(length=5), nullable=False,
                                   server_default="es"))
        batch.add_column(sa.Column("carpeta_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("origen_tipo", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("origen_codigo", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("origen_order_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("origen_numero", sa.String(length=40), nullable=True))
        # El índice antes que la clave ajena (MySQL crearía otro por su cuenta).
        batch.create_index("ix_plantillas_documento_carpeta_id", ["carpeta_id"])
        batch.create_foreign_key(
            "fk_plantillas_documento_carpeta_id", "plantillas_documento_carpetas",
            ["carpeta_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_plantillas_documento_origen_order_id", "orders",
            ["origen_order_id"], ["id"], ondelete="SET NULL",
        )

    bind = op.get_bind()
    filas = bind.execute(sa.text(
        "SELECT id, origen_serie, origen_codpre FROM plantillas_documento"
    )).fetchall()
    for pid, serie, codpre in filas:
        if codpre is not None:
            numero = f"{serie}-{int(codpre):06d}" if serie else str(codpre)
            bind.execute(sa.text(
                "UPDATE plantillas_documento SET origen_tipo = 'presupuesto', "
                "origen_codigo = :codigo, origen_numero = :numero WHERE id = :id"
            ), {"codigo": int(codpre), "numero": numero, "id": pid})
        else:
            bind.execute(sa.text(
                "UPDATE plantillas_documento SET origen_tipo = 'manual' WHERE id = :id"
            ), {"id": pid})

    with op.batch_alter_table("plantillas_documento") as batch:
        batch.drop_column("origen_codpre")

    # 3. Acceso, preferencias y etiquetas.
    op.create_table(
        "plantillas_documento_carpetas_acceso",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("carpeta_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["carpeta_id"], ["plantillas_documento_carpetas.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("carpeta_id", "user_id", name="uq_plantillas_carpeta_acceso"),
    )
    op.create_index("ix_plantillas_documento_carpetas_acceso_carpeta_id",
                    "plantillas_documento_carpetas_acceso", ["carpeta_id"])
    op.create_index("ix_plantillas_documento_carpetas_acceso_user_id",
                    "plantillas_documento_carpetas_acceso", ["user_id"])

    op.create_table(
        "plantillas_documento_preferencias",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("carpeta_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["carpeta_id"], ["plantillas_documento_carpetas.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_plantillas_documento_preferencias_user"),
    )
    op.create_index("ix_plantillas_documento_preferencias_user_id",
                    "plantillas_documento_preferencias", ["user_id"])

    op.create_table(
        "plantillas_documento_etiquetas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plantilla_id", sa.String(length=36), nullable=False),
        sa.Column("etiqueta", sa.String(length=60), nullable=False),
        sa.Column("etiqueta_norm", sa.String(length=60), nullable=False),
        sa.ForeignKeyConstraint(
            ["plantilla_id"], ["plantillas_documento.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plantilla_id", "etiqueta_norm", name="uq_plantillas_etiqueta"),
    )
    op.create_index("ix_plantillas_documento_etiquetas_plantilla_id",
                    "plantillas_documento_etiquetas", ["plantilla_id"])
    op.create_index("ix_plantillas_documento_etiquetas_etiqueta_norm",
                    "plantillas_documento_etiquetas", ["etiqueta_norm"])


def downgrade() -> None:
    op.drop_index("ix_plantillas_documento_etiquetas_etiqueta_norm",
                  table_name="plantillas_documento_etiquetas")
    op.drop_index("ix_plantillas_documento_etiquetas_plantilla_id",
                  table_name="plantillas_documento_etiquetas")
    op.drop_table("plantillas_documento_etiquetas")
    op.drop_index("ix_plantillas_documento_preferencias_user_id",
                  table_name="plantillas_documento_preferencias")
    op.drop_table("plantillas_documento_preferencias")
    op.drop_index("ix_plantillas_documento_carpetas_acceso_user_id",
                  table_name="plantillas_documento_carpetas_acceso")
    op.drop_index("ix_plantillas_documento_carpetas_acceso_carpeta_id",
                  table_name="plantillas_documento_carpetas_acceso")
    op.drop_table("plantillas_documento_carpetas_acceso")

    with op.batch_alter_table("plantillas_documento") as batch:
        batch.add_column(sa.Column("origen_codpre", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE plantillas_documento SET origen_codpre = origen_codigo "
        "WHERE origen_tipo = 'presupuesto'"
    )
    with op.batch_alter_table("plantillas_documento") as batch:
        # Las claves ajenas antes que el índice: MySQL no deja quitar un
        # índice que usa una clave ajena.
        batch.drop_constraint("fk_plantillas_documento_origen_order_id", type_="foreignkey")
        batch.drop_constraint("fk_plantillas_documento_carpeta_id", type_="foreignkey")
        batch.drop_index("ix_plantillas_documento_carpeta_id")
        batch.drop_column("origen_numero")
        batch.drop_column("origen_order_id")
        batch.drop_column("origen_codigo")
        batch.drop_column("origen_tipo")
        batch.drop_column("carpeta_id")
        batch.drop_column("idioma")

    op.create_index("ix_ofertas_plantillas_lineas_plantilla_id",
                    "plantillas_documento_lineas", ["plantilla_id"])
    op.drop_index("ix_plantillas_documento_lineas_plantilla_id",
                  table_name="plantillas_documento_lineas")
    op.rename_table("plantillas_documento_lineas", "ofertas_plantillas_lineas")
    op.rename_table("plantillas_documento", "ofertas_plantillas")

    op.drop_index("ix_plantillas_documento_carpetas_owner_user_id",
                  table_name="plantillas_documento_carpetas")
    op.drop_index("ix_plantillas_documento_carpetas_parent_id",
                  table_name="plantillas_documento_carpetas")
    op.drop_table("plantillas_documento_carpetas")
