"""Fotos del embalaje — traslado de UNA pasada de `packing_json.documents`
(antes se guardaban dentro del contenedor) a `shipment_files` (`kind = foto`,
en el almacén de expedición, que sí persiste).

No es una migración de Alembic: las migraciones solo cambian el esquema y
no tocan el disco. Esto mueve archivos, así que va aparte:

- Al arrancar el `api` (el contenedor que tiene montado el almacén) se
  lanza una vez en segundo plano. SETNX en Redis: con varios procesos, solo
  uno lo hace.
- Nunca falla: sin la carpeta del almacén o sin nada que mover, no hace
  nada. Es idempotente (lo movido ya no está en `documents`).
- A mano: `python -m app.erp.fotos_job` (vista previa) y `--apply`.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ARM_KEY = "erp:fotos:traslado:armed"
ARM_TTL_SECONDS = 3600


def trasladar(session: Session, *, probar: bool = False, uploads_dir: str | None = None,
              shipping_dir: str | None = None, storage: Any = None) -> dict[str, Any]:
    """Mueve (o, con `probar`, solo cuenta) las fotos antiguas. Sin la carpeta
    del almacén local no hace nada: así no escribe donde no persiste (ni falla
    en un entorno sin ella, como CI)."""
    from app.core.config import get_settings  # noqa: PLC0415
    from app.erp.fotos import migrar_documentos  # noqa: PLC0415

    settings = get_settings()
    uploads_dir = uploads_dir or settings.erp_uploads_dir
    shipping_dir = shipping_dir or settings.local_shipping_storage_dir
    if storage is None and not Path(shipping_dir).is_dir():
        return {"omitido": f"no existe la carpeta del almacén ({shipping_dir})",
                "pedidos": 0, "movidas": 0, "perdidas": 0}
    r = migrar_documentos(session.connection(), uploads_dir=uploads_dir,
                          shipping_dir=shipping_dir, storage=storage, probar=probar)
    return {"probar": probar, **r}


def run_once() -> dict[str, Any]:
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        r = trasladar(session)
        session.commit()
    if r.get("omitido"):
        logger.info("fotos traslado: %s", r["omitido"])
    return r


def _run_safe() -> None:
    try:
        run_once()
    except Exception:  # noqa: BLE001 — nunca tumba el api
        logger.warning("fotos traslado falló", exc_info=True)


def arm() -> None:
    """Al arrancar el API: lanza UNA vez el traslado en segundo plano."""
    try:
        from app.workers.queues import redis_connection  # noqa: PLC0415

        if not redis_connection().set(ARM_KEY, "1", nx=True, ex=ARM_TTL_SECONDS):
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("fotos traslado: sin Redis, no se lanza (%s)", exc)
        return
    threading.Thread(target=_run_safe, name="fotos-traslado", daemon=True).start()


if __name__ == "__main__":  # pragma: no cover
    from app.db.session import get_engine

    aplicar = "--apply" in sys.argv[1:]
    with Session(get_engine()) as _s:
        resultado = trasladar(_s, probar=not aplicar)
        if aplicar:
            _s.commit()
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
