"""Cuadre — trabajos en segundo plano (RQ) que fallaron y nadie ha mirado.

El registro de fallidos de RQ no lo vigila nadie: un
`sync_orders_backfill('boprint', '2026-07-04')` falló el 3 de agosto de 2026
(`WooError: GET /orders → 400`) y se descubrió dos meses después, por
casualidad. Esta comprobación los lista (función, argumentos, fecha y error)
con la mecánica de «revisado» de siempre: se descartan los antiguos y avisa
de los nuevos. Solo lee: no reintenta ni borra nada de las colas.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.erp.cuadre.contexto import Contexto
from app.erp.cuadre.registry import (
    ENTIDAD_TRABAJO_COLA,
    FUENTE_MYSQL,
    Hallazgo,
    comprobacion,
)

TRABAJO_COLA_FALLIDO = "trabajo_cola_fallido"
#: Tope por pasada (el registro de fallidos guarda hasta un año).
MAX_FALLIDOS = 300


@dataclass
class TrabajoFallido:
    id: str
    cola: str
    funcion: str
    argumentos: str
    fecha: datetime | None
    error: str


def _recortar(texto: str, n: int) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= n else texto[: n - 1] + "…"


def _ultima_linea(exc_info: str | None) -> str:
    lineas = [ln.strip() for ln in (exc_info or "").splitlines() if ln.strip()]
    return lineas[-1] if lineas else "(sin detalle del error)"


def _argumentos(job: Any) -> str:
    try:
        partes = [repr(a) for a in (job.args or ())]
        partes += [f"{k}={v!r}" for k, v in (job.kwargs or {}).items()]
    except Exception:  # noqa: BLE001 — datos ilegibles: se dice, no se cae
        return "(no se pueden leer)"
    return _recortar(", ".join(partes), 240)


def leer_fallidos(conn: Any = None, *, limite: int = MAX_FALLIDOS) -> list[TrabajoFallido]:
    """Los trabajos del registro de fallidos de TODAS las colas de RQ."""
    from rq import Queue  # noqa: PLC0415
    from rq.job import Job  # noqa: PLC0415
    from rq.registry import FailedJobRegistry  # noqa: PLC0415

    from app.workers.queues import redis_connection  # noqa: PLC0415

    conn = conn or redis_connection()
    out: list[TrabajoFallido] = []
    for cola in sorted(Queue.all(connection=conn), key=lambda q: q.name):
        ids = FailedJobRegistry(queue=cola).get_job_ids()
        for job_id, job in zip(ids, Job.fetch_many(ids, connection=conn), strict=False):
            if job is None:
                out.append(TrabajoFallido(job_id, cola.name, "(datos del trabajo caducados)",
                                          "", None, "(sin detalle del error)"))
            else:
                try:
                    funcion = job.func_name
                except Exception:  # noqa: BLE001
                    funcion = "(desconocida)"
                fecha = job.ended_at or job.enqueued_at
                if fecha is not None and fecha.tzinfo is None:
                    fecha = fecha.replace(tzinfo=UTC)
                out.append(TrabajoFallido(job_id, cola.name, str(funcion), _argumentos(job),
                                          fecha, _recortar(_ultima_linea(job.exc_info), 300)))
            if len(out) >= limite:
                return out
    return out


def _fecha(dt: datetime | None) -> str:
    return dt.strftime("%d/%m/%Y %H:%M") if dt else "fecha desconocida"


@comprobacion(
    id=TRABAJO_COLA_FALLIDO, orden=17,
    titulo="Trabajo en cola fallido sin revisar",
    descripcion="Trabajo en segundo plano (sincronizaciones, importaciones, envíos…) que "
                "terminó con error y sigue en el registro de fallidos de las colas: nadie lo "
                "mira por sí solo.",
    severidad="media", fuente=FUENTE_MYSQL, grupo="integraciones",
)
def trabajo_cola_fallido(ctx: Contexto) -> Iterator[Hallazgo]:
    _ = ctx
    for t in leer_fallidos():
        funcion = t.funcion.rsplit(".", 1)[-1]
        yield Hallazgo(
            entidad_tipo=ENTIDAD_TRABAJO_COLA, entidad_id=t.id[:64],
            etiqueta=f"{funcion} · cola {t.cola}",
            detalle=(f"Falló el {_fecha(t.fecha)}: {t.error}"
                     + (f" — {funcion}({t.argumentos})" if t.argumentos else f" — {t.funcion}")),
            pista_de_arreglo="Mira el error. Si hace falta, repite la operación desde su "
                             "pantalla (p. ej. Integraciones o Seguimiento); si ya no importa, "
                             "márcalo como revisado.",
            huella_datos={"fecha": t.fecha.isoformat() if t.fecha else None, "error": t.error},
            datos={"cola": t.cola, "funcion": t.funcion, "argumentos": t.argumentos,
                   "fecha": t.fecha.isoformat() if t.fecha else None, "error": t.error},
        )
