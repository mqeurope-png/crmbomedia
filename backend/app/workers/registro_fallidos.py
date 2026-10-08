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
  - **Con tope, y por páginas**: el registro de `brevo:push_contact` tiene
    146.876 entradas. Nada aquí puede leerlas todas de golpe, porque esto
    corre dentro de una petición HTTP: se recorre por páginas y se para en
    el tope o al agotar la ventana de búsqueda.
  - **Los recuentos dicen hasta dónde se ha mirado** (`revisados`,
    `ventana_agotada`, `quedan`). Un tope silencioso se lee como «ya está
    todo» cuando no lo está.
  - Reencolar NO es idempotente por sí mismo: lo es el trabajo. Para los
    webhooks de Brevo lo es desde que la inserción de eventos reconoce los
    que ya están.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Tope de reencolados por llamada. De a poco deja ver el efecto antes de
#: seguir.
TOPE_POR_LLAMADA = 500

#: Tope de descartados por llamada. Borrar es una ida y vuelta a Redis por
#: trabajo; con 146.876 de golpe la petición se agotaría a media faena y el
#: recuento se perdería. La respuesta dice cuántos `quedan`.
TOPE_VACIAR = 20_000

#: Ids que se piden a Redis de una vez.
PAGINA = 500

#: Cuántas entradas del registro se miran como mucho buscando las que
#: encajan con `funcion`. Si se agota, la respuesta lo dice.
VENTANA_DE_BUSQUEDA = 20_000


def _nombre_funcion(job: Any) -> str:
    try:
        return str(job.func_name or "")
    except Exception:  # noqa: BLE001 — datos ilegibles: no se cae
        return ""


def _encaja(job: Any, funcion: str | None) -> bool:
    """`funcion` vale como nombre corto o como ruta entera, pero por tramos
    completos. Con `endswith` a secas, «job» habría casado con todos los
    `*_job` del sistema."""
    if not funcion:
        return True
    nombre = _nombre_funcion(job)
    return nombre == funcion or nombre.rsplit(".", 1)[-1] == funcion


def _ids(conn: Any, key: str, desde: int, hasta: int) -> list[str]:
    """Una página del registro, de los más ANTIGUOS a los más nuevos: se
    recupera en el orden en que se perdió."""
    return [i.decode() if isinstance(i, bytes) else str(i)
            for i in conn.zrange(key, desde, hasta)]


def reencolar(
    cola: str, *, funcion: str | None = None, limite: int = TOPE_POR_LLAMADA,
    probar: bool = True, conn: Any = None,
) -> dict[str, Any]:
    """Vuelve a encolar trabajos del registro de fallidos de `cola`.

    Devuelve el recuento, cuántas entradas se han mirado y, en vista previa,
    un ejemplo de lo que se reencolaría.
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

    elegidos: list[Any] = []
    sin_datos = 0
    revisados = 0
    while len(elegidos) < limite and revisados < VENTANA_DE_BUSQUEDA:
        pagina = _ids(conn, registro.key, revisados, revisados + PAGINA - 1)
        if not pagina:
            break
        revisados += len(pagina)
        for job in Job.fetch_many(pagina, connection=conn):
            if job is None:
                # El trabajo caducó y solo queda su id en el registro: no hay
                # argumentos que reencolar.
                sin_datos += 1
                continue
            if not _encaja(job, funcion):
                continue
            elegidos.append(job)
            if len(elegidos) >= limite:
                break
        if len(pagina) < PAGINA:
            break

    salida: dict[str, Any] = {
        "cola": cola, "funcion": funcion, "probar": probar,
        "en_registro": total_en_registro, "elegidos": len(elegidos),
        "sin_datos": sin_datos, "reencolados": 0, "fallos_al_reencolar": 0,
        "tope": limite, "revisados": revisados,
        "ventana_agotada": revisados >= VENTANA_DE_BUSQUEDA
        and len(elegidos) < limite,
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
        "registro, %d sin datos, %d revisados)", salida["reencolados"], cola,
        funcion, total_en_registro, sin_datos, revisados,
    )
    return salida


def vaciar(cola: str, *, probar: bool = True, conn: Any = None) -> dict[str, Any]:
    """Descarta el registro de fallidos de una cola, con sus trabajos.

    Deja constancia de cuántos se han descartado de verdad; con `probar=True`
    solo cuenta. Como mucho `TOPE_VACIAR` por llamada: `quedan` dice si hay
    que volver a pulsar.
    """
    from rq import Queue  # noqa: PLC0415
    from rq.job import Job  # noqa: PLC0415
    from rq.registry import FailedJobRegistry  # noqa: PLC0415

    from app.workers.queues import redis_connection  # noqa: PLC0415

    conn = conn or redis_connection()
    registro = FailedJobRegistry(queue=Queue(cola, connection=conn))
    cuantos = len(registro)
    if probar:
        return {"cola": cola, "probar": True, "en_registro": cuantos,
                "descartados": 0, "sin_datos": 0, "fallos": 0,
                "quedan": cuantos, "tope": TOPE_VACIAR}

    descartados = sin_datos = fallos = 0
    while descartados + sin_datos + fallos < TOPE_VACIAR:
        # Siempre la primera página: lo que se borra sale del registro, así
        # que el principio se va renovando.
        pagina = _ids(conn, registro.key, 0, PAGINA - 1)
        if not pagina:
            break
        avance = 0
        for job_id, job in zip(
            pagina, Job.fetch_many(pagina, connection=conn), strict=False
        ):
            try:
                if job is None:
                    # Caducó el trabajo y solo queda su id: se quita del
                    # registro, pero no había datos que liberar.
                    conn.zrem(registro.key, job_id)
                    sin_datos += 1
                else:
                    # `delete` se lleva el trabajo, su traza y su sitio en el
                    # registro; sin él los datos siguen ocupando Redis.
                    job.delete()
                    descartados += 1
                avance += 1
            except Exception:  # noqa: BLE001 — uno malo no corta los demás
                fallos += 1
                logger.warning("colas: no se pudo descartar %s de %s", job_id,
                               cola, exc_info=True)
        if avance == 0:
            # Nada salió del registro: seguir leería la misma página para
            # siempre.
            break

    quedan = len(registro)
    logger.warning(
        "colas: descartados %d trabajos fallidos de %s (%d sin datos, %d "
        "fallos, de %d; quedan %d)", descartados, cola, sin_datos, fallos,
        cuantos, quedan,
    )
    return {"cola": cola, "probar": False, "en_registro": cuantos,
            "descartados": descartados, "sin_datos": sin_datos,
            "fallos": fallos, "quedan": quedan, "tope": TOPE_VACIAR}
