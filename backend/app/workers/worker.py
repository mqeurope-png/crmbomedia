"""Worker RQ de BoHub: el de RQ más un paso al arrancar.

Al arrancar, antes de coger trabajo, cierra como fallidas las ejecuciones de
sincronización (`sync_logs`) de SUS colas que quedaron en `pending`/`running`
de un arranque anterior y cuyo job ya no está vivo en RQ
(`app.workers.huerfanas.cerrar_huerfanas`). Lo que otro worker vivo tiene en
marcha, o lo que sigue encolado, no se toca.

Se usa con `rq worker --worker-class app.workers.worker.BoHubWorker …` (ver
los docker-compose). Un fallo aquí nunca impide que el worker arranque.
"""
from __future__ import annotations

import logging
from typing import Any

from rq.worker import Worker

logger = logging.getLogger(__name__)


class BoHubWorker(Worker):
    def bootstrap(self, *args: Any, **kwargs: Any) -> None:
        super().bootstrap(*args, **kwargs)
        cerrar_huerfanas_al_arrancar(self.queue_names(), self.connection, self.name)


def cerrar_huerfanas_al_arrancar(colas: list[str], conn: Any, nombre: str) -> int:
    try:
        from sqlalchemy.orm import Session  # noqa: PLC0415

        from app.db.session import get_engine  # noqa: PLC0415
        from app.workers.huerfanas import cerrar_huerfanas  # noqa: PLC0415

        with Session(get_engine()) as session:
            return cerrar_huerfanas(
                session, conn=conn, colas=set(colas),
                motivo=f"cerrada al arrancar el worker {nombre}",
            )
    except Exception:  # noqa: BLE001 — el worker arranca igual
        logger.warning("huerfanas: no se pudieron cerrar al arrancar el worker", exc_info=True)
        return 0
