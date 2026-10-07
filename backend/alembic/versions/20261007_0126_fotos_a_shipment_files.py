"""ERP · fotos del embalaje: de `packing_json.documents` a `shipment_files`.

Las fotos se guardaban en `erp_uploads_dir`, dentro del contenedor `api` (sin
volumen), y cada despliegue las borraba; la referencia quedaba en
`packing_json.documents` con `url: null`. Ahora son `shipment_files` (`kind =
foto`) en el almacén de expedición (bind mount). Esta migración pasa las
referencias que queden:

- si el archivo existe aún (en `erp_uploads_dir` o en
  `<almacén de expedición>/_rescate/`, donde se puede copiar antes de
  desplegar), se mueve al almacén y se registra en `shipment_files`;
- si se perdió, se quita la referencia (sin dejarla rota) y queda constancia en
  `packing_json.fotos_perdidas` (nombre y fecha) para que la ficha avise.

Solo datos (sin cambios de esquema). No se deshace: bajar no hace nada.

Revision ID: 20261007_0126
Revises: 20261007_0125
"""

from __future__ import annotations

from alembic import op

revision = "20261007_0126"
down_revision = "20261007_0125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.core.config import get_settings
    from app.erp.fotos import migrar_documentos

    settings = get_settings()
    migrar_documentos(
        op.get_bind(), uploads_dir=settings.erp_uploads_dir,
        shipping_dir=settings.local_shipping_storage_dir,
    )


def downgrade() -> None:
    """Migración de datos: no se deshace (los ficheros ya están en su sitio)."""
