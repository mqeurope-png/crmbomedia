"""Las pasadas del índice de las webs: «Reindexar ahora» y la mensual.

Una pasada (`web_indice_pasadas`) recorre las webs activas de `sitios_web` UNA
A UNA, con un trabajo RQ por web en la cola `webs:indice` (la escucha
`worker-web`, la última de su lista): worker-web es un solo proceso y atiende
los webhooks de los pedidos; un recorrido entero (unos 5 minutos) los
retrasaría, y así entran entre web y web. Cada trabajo hace su web, apunta su
resumen y encola la siguiente; el de la última cierra la pasada (`ok`,
`parcial` si alguna web falló, `error` si fallaron todas).

- `encolar_pasada`: si ya hay una en cola o en curso (y no está caducada:
  más de 2 h sin moverse), devuelve esa; si no, crea una `en_cola` con la
  lista de webs y encola la primera. Nunca recorre en la petición web
  (nginx corta a los 180 s). Sin Redis fuera de producción (desarrollo), se
  recorre en línea, como el «sync backfill» de WooCommerce; en producción la
  pasada queda en error con el motivo.
- El host que se lee sale de `conocimiento.webs_indice_hosts` (de partida
  `fluxlasers` → `fluxlasers.eu`: `fluxlasers.es` NO se indexa).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.fuentes_externas import (
    PASADA_CORRIENDO,
    PASADA_EN_COLA,
    PASADA_ERROR,
    PASADA_OK,
    PASADA_PARCIAL,
    WebIndicePasada,
)
from app.services.web_forms import sitios as registro_sitios
from app.services.web_forms.sitios import Sitio
from app.services.webs import indice, lector
from app.services.webs import reglas as reglas_mod
from app.workers.queues import queue_name

logger = logging.getLogger(__name__)

WEBS_QUEUE = queue_name("webs", "indice")
#: Tiempo máximo de UN trabajo (una web). La más larga, mbolasers (cinco
#: idiomas y sus páginas leídas del HTML), ronda los 2 minutos: holgado.
JOB_TIMEOUT_SECONDS = 15 * 60
#: Una pasada en cola o en curso que lleva más de esto sin moverse se da por
#: muerta (un worker reiniciado a media pasada): no bloquea otra.
CADUCA_SEGUNDOS = 2 * 3600

ORIGEN_PROGRAMADA = "programada"
ORIGEN_MANUAL = "manual"
ORIGENES = (ORIGEN_PROGRAMADA, ORIGEN_MANUAL)
FINALES = (PASADA_OK, PASADA_PARCIAL, PASADA_ERROR)
EN_MARCHA = (PASADA_EN_COLA, PASADA_CORRIENDO)

#: Cómo se crea el lector de red de cada web (los tests lo cambian por uno
#: con `httpx.MockTransport`).
nuevo_http: Callable[[], lector.Http] = lector.Http


def _ahora() -> datetime:
    return datetime.now(UTC)


def _con_zona(fecha: datetime | None) -> datetime | None:
    if fecha is None:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def _json(raw: str | None, defecto: Any) -> Any:
    try:
        valor = json.loads(raw or "")
    except (TypeError, ValueError):
        return defecto
    return valor if isinstance(valor, type(defecto)) else defecto


# --- qué webs y con qué host -----------------------------------------------------------------


def host_de(sitio: Sitio, config: dict[str, Any] | None = None) -> str:
    """El host del que se lee la web: el de `webs_indice_hosts` si lo hay
    (FLUX se lee de fluxlasers.eu), si no el de la web."""
    hosts = (config or {}).get("webs_indice_hosts") or {}
    host = hosts.get(sitio.clave) if isinstance(hosts, dict) else None
    return str(host or sitio.web).strip().lower()


def _config(session: Session) -> dict[str, Any]:
    from app.services.conocimiento.config import configuracion  # noqa: PLC0415

    try:
        return configuracion(session)
    except Exception:  # noqa: BLE001 — sin configuración, los valores de partida
        from app.services.conocimiento.config import defectos  # noqa: PLC0415

        return defectos()


def webs_a_indexar(session: Session) -> list[tuple[Sitio, str]]:
    """Las webs activas de `sitios_web` (solo tipo web) con el host a leer."""
    config = _config(session)
    return [(s, host_de(s, config)) for s in registro_sitios.cargar(session).webs]


# --- una web ---------------------------------------------------------------------------------


def indexar_web(session: Session, clave: str, *, http: lector.Http | None = None,
                ahora: datetime | None = None) -> dict[str, Any]:
    """Lee una web y la vuelca al índice (con commit). Devuelve su resumen;
    un fallo se queda en el resumen (`estado: error`) y no tira la pasada."""
    ahora = ahora or _ahora()
    elegida = next(((s, h) for s, h in webs_a_indexar(session) if s.clave == clave), None)
    if elegida is None:
        return {"estado": PASADA_ERROR,
                "error": f"«{clave}» ya no es una web activa de la tabla de sitios."}
    sitio, host = elegida
    reglas = reglas_mod.cargar(session).para(clave)
    # Lo que viene es red (minutos): no se deja una transacción abierta.
    session.rollback()
    propio = http is None
    cliente = http or nuevo_http()
    try:
        lectura = lector.leer_web(sitio, reglas, host=host, http=cliente)
    except Exception as exc:  # noqa: BLE001 — una web rara no tira la pasada
        logger.exception("webs: %s no se pudo leer", clave)
        return {"estado": PASADA_ERROR, "dominio": host,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    finally:
        if propio:
            cliente.cerrar()
    try:
        resumen = indice.escribir_lectura(session, lectura, ahora=ahora)
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        logger.exception("webs: %s no se pudo escribir", clave)
        return {"estado": PASADA_ERROR, "dominio": host,
                "error": f"No se pudo guardar: {type(exc).__name__}"}
    logger.info("webs: %s (%s) %s — %d páginas, %d entradas, %d productos, %d nuevos, "
                "%d actualizados, %d idos, %d peticiones", clave, host, resumen["estado"],
                resumen["paginas"], resumen["entradas"], resumen["productos"],
                resumen["nuevos"], resumen["actualizados"], resumen["idos"],
                resumen["peticiones"])
    return resumen


# --- las pasadas -----------------------------------------------------------------------------


def caducada(pasada: WebIndicePasada, ahora: datetime | None = None) -> bool:
    ahora = ahora or _ahora()
    movida = _con_zona(pasada.updated_at) or _con_zona(pasada.created_at) or ahora
    return (ahora - movida).total_seconds() > CADUCA_SEGUNDOS


def en_curso(session: Session, *, ahora: datetime | None = None) -> WebIndicePasada | None:
    """La pasada en cola o en curso, si la hay. Las caducadas se cierran con
    error de paso (no bloquean otra)."""
    for pasada in session.scalars(select(WebIndicePasada).where(
            WebIndicePasada.estado.in_(EN_MARCHA)).order_by(WebIndicePasada.created_at.desc())):
        if not caducada(pasada, ahora):
            return pasada
        pasada.estado = PASADA_ERROR
        pasada.fin = ahora or _ahora()
        pasada.error = ("La pasada se quedó a medias más de 2 horas (¿se reinició "
                        "worker-web?): se da por terminada.")
    session.flush()
    return None


def ultima(session: Session, *, origen: str | None = None,
           estados: tuple[str, ...] = FINALES) -> WebIndicePasada | None:
    consulta = select(WebIndicePasada).where(WebIndicePasada.estado.in_(estados))
    if origen:
        consulta = consulta.where(WebIndicePasada.origen == origen)
    return session.scalars(consulta.order_by(WebIndicePasada.created_at.desc()).limit(1)).first()


def pasada_como_dict(p: WebIndicePasada | None) -> dict[str, Any] | None:
    """La forma `WebsPasada` de `websApi.ts`."""
    if p is None:
        return None
    inicio, fin, creado = _con_zona(p.inicio), _con_zona(p.fin), _con_zona(p.created_at)
    return {
        "id": p.id, "origen": p.origen, "estado": p.estado,
        "inicio": inicio.isoformat() if inicio else None,
        "fin": fin.isoformat() if fin else None,
        "pendientes": _json(p.pendientes_json, []), "resumen": _json(p.resumen_json, {}),
        "error": p.error, "creado_en": creado.isoformat() if creado else None,
    }


def _encolar(pasada_id: str) -> None:
    from rq import Queue  # noqa: PLC0415

    from app.workers.queues import redis_connection  # noqa: PLC0415

    Queue(WEBS_QUEUE, connection=redis_connection(),
          default_timeout=JOB_TIMEOUT_SECONDS).enqueue(
        trabajo_web, pasada_id, job_timeout=JOB_TIMEOUT_SECONDS)


def _en_produccion() -> bool:
    from app.core.config import get_settings  # noqa: PLC0415

    return str(get_settings().environment or "").lower() == "production"


def encolar_pasada(session: Session, origen: str, usuario_id: str | None = None, *,
                   ahora: datetime | None = None) -> WebIndicePasada:
    """Encola un recorrido de todas las webs (o devuelve el que ya está en cola
    o en curso). Hace commit. No recorre en la petición (salvo en desarrollo
    sin Redis)."""
    if origen not in ORIGENES:
        raise ValueError(f"Origen de pasada no válido: {origen!r}.")
    ahora = ahora or _ahora()
    actual = en_curso(session, ahora=ahora)
    if actual is not None:
        session.commit()
        return actual
    webs = [s.clave for s, _ in webs_a_indexar(session)]
    pasada = WebIndicePasada(origen=origen, estado=PASADA_EN_COLA, lanzado_por=usuario_id,
                             pendientes_json=json.dumps(webs), resumen_json="{}")
    session.add(pasada)
    session.commit()
    if not webs:
        _cerrar(session, pasada, ahora=ahora)
        session.commit()
        return pasada
    try:
        _encolar(pasada.id)
    except Exception as exc:  # noqa: BLE001
        if _en_produccion():
            logger.warning("webs: no se pudo encolar la pasada %s: %s", pasada.id, exc)
            pasada.estado = PASADA_ERROR
            pasada.fin = _ahora()
            pasada.error = ("No se pudo encolar el recorrido (¿Redis o worker-web parados?): "
                            f"{type(exc).__name__}.")
            session.commit()
            return pasada
        logger.info("webs: sin Redis (%s); la pasada %s se recorre en línea",
                    type(exc).__name__, pasada.id)
        ejecutar_en_linea(session, pasada.id)
        session.refresh(pasada)
    return pasada


def procesar_siguiente(session: Session, pasada_id: str, *,
                       http: lector.Http | None = None) -> bool:
    """Hace la siguiente web pendiente de la pasada y apunta su resumen; si era
    la última, cierra la pasada. True si quedan webs."""
    pasada = session.get(WebIndicePasada, pasada_id)
    if pasada is None or pasada.estado in FINALES:
        return False
    pendientes = [str(p) for p in _json(pasada.pendientes_json, [])]
    if not pendientes:
        _cerrar(session, pasada)
        session.commit()
        return False
    if pasada.estado == PASADA_EN_COLA:
        pasada.estado = PASADA_CORRIENDO
        pasada.inicio = pasada.inicio or _ahora()
    pasada.updated_at = _ahora()
    session.commit()
    clave = pendientes[0]
    try:
        resumen_web = indexar_web(session, clave, http=http)
    except Exception as exc:  # noqa: BLE001 — la pasada sigue con las demás
        session.rollback()
        logger.exception("webs: %s falló", clave)
        resumen_web = {"estado": PASADA_ERROR, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    pasada = session.get(WebIndicePasada, pasada_id)
    if pasada is None:
        return False
    resumen = _json(pasada.resumen_json, {})
    resumen[clave] = resumen_web
    restantes = [p for p in _json(pasada.pendientes_json, []) if p != clave]
    pasada.resumen_json = json.dumps(resumen, ensure_ascii=False, default=str)
    pasada.pendientes_json = json.dumps(restantes)
    pasada.updated_at = _ahora()
    if not restantes:
        _cerrar(session, pasada)
    session.commit()
    return bool(restantes)


def _cerrar(session: Session, pasada: WebIndicePasada, *, ahora: datetime | None = None) -> None:
    """Estado final a partir del resumen por web. Si alguna web respondió, lo
    de las webs que ya no se indexan (desactivadas) se marca ido."""
    resumen = _json(pasada.resumen_json, {})
    estados = [str((r or {}).get("estado")) for r in resumen.values()]
    if not estados or all(e == PASADA_OK for e in estados):
        pasada.estado = PASADA_OK
    elif all(e == PASADA_ERROR for e in estados):
        pasada.estado = PASADA_ERROR
    else:
        pasada.estado = PASADA_PARCIAL
    fallos = [f"{clave}: {(r or {}).get('error')}" for clave, r in resumen.items()
              if (r or {}).get("estado") != PASADA_OK and (r or {}).get("error")]
    pasada.error = "\n".join(fallos)[:4000] or None
    pasada.pendientes_json = "[]"
    pasada.fin = ahora or _ahora()
    if resumen and pasada.estado != PASADA_ERROR:
        idos = indice.marcar_idos_fuera_de(session, set(resumen), ahora=pasada.fin)
        if idos:
            logger.info("webs: %d filas de webs que ya no se indexan, idas", idos)


def ejecutar_en_linea(session: Session, pasada_id: str, *,
                      http: lector.Http | None = None) -> None:
    """Recorre la pasada entera aquí mismo (desarrollo sin Redis, y los
    tests)."""
    while procesar_siguiente(session, pasada_id, http=http):
        pass


def trabajo_web(pasada_id: str) -> None:
    """Entrada RQ (cola `webs:indice`): una web de la pasada y, si quedan,
    encola la siguiente. Si no se puede encolar, sigue aquí mismo."""
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        quedan = procesar_siguiente(session, pasada_id)
        if not quedan:
            return
        try:
            _encolar(pasada_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("webs: no se pudo encolar la siguiente web (%s); sigue en línea", exc)
            ejecutar_en_linea(session, pasada_id)
