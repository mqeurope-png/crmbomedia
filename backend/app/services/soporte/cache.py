"""La caché del soporte (`soporte_cache`).

Un artículo de soporte no cambia de un día para otro y se consulta mucho (la
oficina busca lo mismo cada vez que una máquina da el mismo fallo):
guardarlo evita salir a la red en cada clic y que una caída de la fuente deje
sin el artículo que ya se leyó ayer.

- Clave: sha256 de `tipo|fuente|idioma|consulta-o-id`.
- Caducidad: búsquedas, un día (un artículo nuevo tiene que aparecer pronto);
  artículos, una semana (cambian poco y el enlace siempre lleva al vigente).
- Limpieza perezosa: al escribir se borran los caducados (con el índice de
  `expira_en` es barato). No hay tarea programada que mantener.
- Lo que falla NO se guarda: el siguiente intento vuelve a preguntar.

Escribir en la caché es lo de menos y va en su PROPIA transacción, corta y
con commit al momento (en MySQL): quien la usa es el asistente, a mitad de
redactar un borrador, y su transacción dura lo que la llamada al modelo
(20-40 s). Si la escritura fuera en ella, sus bloqueos (la purga de
caducados toma un rango del índice) dejarían esperando a cualquier otro
borrador que guardara en la caché a la vez, y esa espera se comería el plazo
del soporte. Si falla (dos peticiones iguales a la vez que insertan la misma
clave), se sigue sin ella. En SQLite (los tests: una sola conexión) va en un
SAVEPOINT de la sesión de quien llama.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.fuentes_externas import SoporteCache

logger = logging.getLogger(__name__)

#: Una búsqueda vale un día: lo bastante para no repetir la petición en la
#: misma jornada y lo bastante poco para que un artículo nuevo salga mañana.
DURACION_BUSQUEDA = timedelta(days=1)
#: Un artículo vale una semana: cambian poco y el borrador lleva el enlace,
#: que siempre abre la versión vigente.
DURACION_ARTICULO = timedelta(days=7)

BUSQUEDA = "busqueda"
ARTICULO = "articulo"


def _ahora() -> datetime:
    return datetime.now(UTC)


def _utc(valor: datetime) -> datetime:
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)


def clave(tipo: str, fuente: str, idioma: str | None, valor: str) -> str:
    return hashlib.sha256(f"{tipo}|{fuente}|{idioma or ''}|{valor}".encode()).hexdigest()


def leer(session: Session, clave_cache: str) -> Any | None:
    """Lo guardado si no ha caducado; si no, `None` (y se vuelve a pedir)."""
    fila = session.get(SoporteCache, clave_cache)
    if fila is None or _utc(fila.expira_en) <= _ahora():
        return None
    try:
        return json.loads(fila.datos_json)
    except ValueError:
        return None


def _escribir(session: Session, clave_cache: str, *, fuente: str, tipo: str, datos: Any,
              duracion: timedelta) -> None:
    ahora = _ahora()
    # Los caducados, fuera (salvo esta misma clave, que se reescribe).
    session.execute(
        delete(SoporteCache)
        .where(SoporteCache.expira_en < ahora, SoporteCache.clave != clave_cache)
        .execution_options(synchronize_session=False))
    fila = session.get(SoporteCache, clave_cache)
    if fila is None:
        fila = SoporteCache(clave=clave_cache)
        session.add(fila)
    fila.fuente = fuente
    fila.tipo = tipo
    fila.datos_json = json.dumps(datos, ensure_ascii=False)
    fila.creado_en = ahora
    fila.expira_en = ahora + duracion
    session.flush()


def guardar(session: Session, clave_cache: str, *, fuente: str, tipo: str, datos: Any,
            duracion: timedelta) -> None:
    """Guarda lo leído. En MySQL, en una transacción aparte con commit al
    momento: nunca deja bloqueos en la transacción de quien llama."""
    bind = session.get_bind()
    try:
        if bind.dialect.name == "sqlite":
            with session.begin_nested():
                _escribir(session, clave_cache, fuente=fuente, tipo=tipo, datos=datos,
                          duracion=duracion)
            return
        with Session(bind=bind) as aparte:
            _escribir(aparte, clave_cache, fuente=fuente, tipo=tipo, datos=datos,
                      duracion=duracion)
            aparte.commit()
    except SQLAlchemyError as exc:
        logger.warning("soporte.cache: no se ha guardado %s de %s (%s)", tipo, fuente,
                       type(exc).__name__)
