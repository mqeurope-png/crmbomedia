"""Operar sobre el registro de fallidos de RQ desde la aplicación.

Los trabajos fallidos guardan sus argumentos completos, así que se pueden
volver a encolar tal cual. Es la única forma de recuperar lo que se perdió
con los 1.687 lotes de `brevo:webhook_process` que murieron por un evento
duplicado: las entregas, aperturas y clics que venían dentro.

Reglas:
  - **Vista previa primero**: con `probar=True` no toca nada, solo cuenta y
    devuelve un ejemplo. Es lo que ve quien va a pulsar el botón.
  - **Por cola y, si se quiere, por función**: nadie reencola «todo» sin
    querer.
  - **Con tope**: 146.876 trabajos de `brevo:push_contact` no se reencolan
    de golpe, ni tiene sentido hacerlo (son un 400 de datos).
  - Reencolar NO es idempotente por sí mismo: lo es el trabajo. Para los
    webhooks de Brevo lo es desde que la inserción de eventos reconoce los
    que ya están.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Tope por llamada. Reencolar de a poco deja ver el efecto antes de seguir.
TOPE_POR_LLAMADA = 500


def _nombre_funcion(job: Any) -> str:
    try:
        return str(job.func_name or "")
    except Exception:  # noqa: BLE001 — datos ilegibles: no se cae
        return ""


def reencolar(
    cola: str, *, funcion: str | None = None, limite: int = TOPE_POR_LLAMADA,
    probar: bool = True, conn: Any = None,
) -> dict[str, Any]:
    """Vuelve a encolar trabajos del registro de fallidos de `cola`.

    `funcion` filtra por el nombre completo del job (`endswith`, así vale
    tanto el nombre corto como la ruta entera). Devuelve el recuento y, en
    vista previa, un ejemplo de lo que se reencolaría.
    """
    from rq import Queue  # noqa: PLC0415
    from rq.job import Job  # noqa: PLC0415
    from rq.registry import FailedJobRegistry  # noqa: PLC0415

    from app.workers.queues import redis_connection  # noqa: PLC0415

    limite = max(1, min(int(limite or TOPE_POR_LLAMADA), TOPE_POR_LLAMADA))
    conn = conn or redis_connection()
    q = Queue(cola, connection=conn)
    registro = FailedJobRegistry(queue=q)
    total_en_registro = len(registro)
    # Los más ANTIGUOS primero: se recupera en el orden en que se perdió.
    ids = [i.decode() if isinstance(i, bytes) else str(i)
           for i in conn.zrange(registro.key, 0, -1)]

    elegidos: list[Job] = []
    sin_datos = 0
    for job in Job.fetch_many(ids, connection=conn):
        if len(elegidos) >= limite:
            break
        if job is None:
            # El trabajo caducó y solo queda su id en el registro: no hay
            # argumentos que reencolar.
            sin_datos += 1
            continue
        if funcion and not _nombre_funcion(job).endswith(funcion):
            continue
        elegidos.append(job)

    salida: dict[str, Any] = {
        "cola": cola, "funcion": funcion, "probar": probar,
        "en_registro": total_en_registro, "elegidos": len(elegidos),
        "sin_datos": sin_datos, "reencolados": 0, "fallos_al_reencolar": 0,
        "tope": limite,
    }
    if elegidos:
        primero = elegidos[0]
        salida["ejemplo"] = {
            "id": primero.id, "funcion": _nombre_funcion(primero),
            "fecha": primero.ended_at.isoformat() if primero.ended_at else None,
        }
    if probar:
        return salida

    for job in elegidos:
        try:
            registro.requeue(job.id)
            salida["reencolados"] += 1
        except Exception:  # noqa: BLE001 — uno malo no corta los demás
            salida["fallos_al_reencolar"] += 1
            logger.warning("colas: no se pudo reencolar %s de %s", job.id, cola,
                           exc_info=True)
    logger.warning(
        "colas: reencolados %d trabajos de %s (función=%s, quedaban %d en el "
        "registro, %d sin datos)", salida["reencolados"], cola, funcion,
        total_en_registro, sin_datos,
    )
    return salida


def vaciar(cola: str, *, probar: bool = True, conn: Any = None) -> dict[str, Any]:
    """Descarta el registro de fallidos de una cola. Deja constancia de
    cuántos se han descartado; con `probar=True` solo cuenta."""
    from rq import Queue  # noqa: PLC0415
    from rq.registry import FailedJobRegistry  # noqa: PLC0415

    from app.workers.queues import redis_connection  # noqa: PLC0415

    conn = conn or redis_connection()
    registro = FailedJobRegistry(queue=Queue(cola, connection=conn))
    cuantos = len(registro)
    if probar:
        return {"cola": cola, "probar": True, "en_registro": cuantos,
                "descartados": 0}
    descartados = 0
    for job_id in [i.decode() if isinstance(i, bytes) else str(i)
                   for i in conn.zrange(registro.key, 0, -1)]:
        try:
            # `delete_job` se lleva el trabajo y su traza; sin él solo se
            # quita del registro y los datos siguen ocupando Redis.
            registro.remove(job_id, delete_job=True)
            descartados += 1
        except Exception:  # noqa: BLE001
            logger.warning("colas: no se pudo descartar %s de %s", job_id, cola,
                           exc_info=True)
    logger.warning("colas: descartados %d trabajos fallidos de %s (de %d)",
                   descartados, cola, cuantos)
    return {"cola": cola, "probar": False, "en_registro": cuantos,
            "descartados": descartados}
