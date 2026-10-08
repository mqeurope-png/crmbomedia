"""Cuadre — trabajos en segundo plano (RQ) que fallaron y nadie ha mirado.

El registro de fallidos de RQ no lo vigila nadie: un
`sync_orders_backfill('boprint', '2026-07-04')` falló el 3 de agosto de 2026
(`WooError: GET /orders → 400`) y se descubrió dos meses después, por
casualidad.

**Agrupado, no uno por fallo.** La primera versión sacaba un descuadre por
trabajo: con 149.603 fallidos el panel listaba 300 líneas de lo mismo y no se
podía leer. Ahora se agrupa por **cola + función + tipo de error**, y cada
grupo dice cuántos van, desde cuándo, hasta cuándo y un ejemplo con sus
argumentos. 1.687 fallos iguales son UNA línea.

**La severidad la marca el dato, no la comprobación**: un grupo que sigue
pasando hoy y lleva cientos de fallos es alta; uno que sigue pasando es
media; uno cuyo último fallo es de hace más de una semana es baja (se arregló
o dejó de ocurrir, y lo que queda es limpiar el registro).

Solo lee: no reintenta ni borra nada de las colas. Reencolar y vaciar están en
`POST /api/erp/cuadre/colas/{reencolar,vaciar}`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.erp.cuadre.contexto import Contexto
from app.erp.cuadre.registry import (
    ENTIDAD_TRABAJO_COLA,
    FUENTE_MYSQL,
    Hallazgo,
    comprobacion,
)

TRABAJO_COLA_FALLIDO = "trabajo_cola_fallido"

#: Cuántas entradas del registro se miran por cola. Esto corre DENTRO de la
#: petición web (mysql no está entre las fuentes en segundo plano), así que
#: hay tope: leer 146.876 trabajos aquí tumbaría la pantalla. Los recuentos
#: dicen hasta dónde se ha mirado.
MAX_POR_COLA = 2_000
#: Ids que se piden a Redis de una vez.
PAGINA = 200
#: Un grupo cuyo último fallo es más viejo que esto ya no está pasando.
DIAS_PARA_DARLO_POR_PARADO = 7
#: A partir de aquí un grupo que sigue pasando es alta: no es un caso raro,
#: es una tormenta.
FALLOS_PARA_SER_ALTA = 100


@dataclass
class TrabajoFallido:
    id: str
    cola: str
    funcion: str
    argumentos: str
    fecha: datetime | None
    error: str


@dataclass
class GrupoFallidos:
    """Los fallos de una misma función con el mismo tipo de error."""

    cola: str
    funcion: str
    tipo_error: str
    cuantos: int = 0
    primero: datetime | None = None
    ultimo: datetime | None = None
    ejemplo: TrabajoFallido | None = None
    sin_datos: int = 0
    ids: list[str] = field(default_factory=list)

    @property
    def clave(self) -> str:
        return f"{self.cola}|{self.funcion}|{self.tipo_error}"

    def anota(self, t: TrabajoFallido) -> None:
        self.cuantos += 1
        if t.fecha is not None:
            if self.primero is None or t.fecha < self.primero:
                self.primero = t.fecha
            if self.ultimo is None or t.fecha > self.ultimo:
                self.ultimo = t.fecha
        # El ejemplo es el más reciente con argumentos legibles: es el que
        # sirve para repetir la operación a mano.
        if t.argumentos and (
            self.ejemplo is None
            or (t.fecha or datetime.min.replace(tzinfo=UTC))
            > (self.ejemplo.fecha or datetime.min.replace(tzinfo=UTC))
        ):
            self.ejemplo = t
        if len(self.ids) < 5:
            self.ids.append(t.id)

    def sigue_pasando(self, ahora: datetime) -> bool:
        if self.ultimo is None:
            return False
        return (ahora - self.ultimo) <= timedelta(days=DIAS_PARA_DARLO_POR_PARADO)

    def severidad(self, ahora: datetime) -> str:
        if not self.sigue_pasando(ahora):
            return "baja"
        return "alta" if self.cuantos >= FALLOS_PARA_SER_ALTA else "media"


def _recortar(texto: str, n: int) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= n else texto[: n - 1] + "…"


def _ultima_linea(exc_info: str | None) -> str:
    lineas = [ln.strip() for ln in (exc_info or "").splitlines() if ln.strip()]
    return lineas[-1] if lineas else "(sin detalle del error)"


#: Lo que cambia de un fallo a otro siendo el mismo fallo: uuids, fechas,
#: correos y números. Se borra para que `400 for contact 91af…` y `400 for
#: contact 2b7c…` caigan en el mismo grupo.
#:
#: NO se tocan los textos entre comillas por el hecho de ser largos: ahí van
#: los nombres de las claves (`'uq_activity_event_system_account_external_id'`)
#: y dos violaciones de claves DISTINTAS son problemas distintos. Los valores
#: que viajan entre comillas llevan números, y de esos ya se encarga la regla
#: de abajo.
_RUIDO = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<id>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]?[\d:.+]*"), "<fecha>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<correo>"),
    # Referencias con letras Y números (`brevo-default-2032101`, `BOPRIN-99866`,
    # `job_7f3a1`): lo que identifica un caso concreto. Un número SUELTO no se
    # toca, porque ahí están los códigos de estado y las aridades: un 400 y un
    # 404, o «takes 2» y «takes 3», son problemas distintos.
    (re.compile(r"\b(?=[\w.-]*[A-Za-z])(?=[\w.-]*\d)[\w.-]+\b"), "<ref>"),
]


def tipo_de_error(error: str) -> str:
    """El error sin lo que cambia entre una ocurrencia y otra.

    `Duplicate entry 'brevo-default-2032101:…' for key 'uq_…'` y el mismo con
    otro id son el MISMO problema; si no se normaliza, 1.687 fallos iguales
    siguen saliendo como 1.687 líneas distintas."""
    texto = " ".join((error or "").split())
    for patron, reemplazo in _RUIDO:
        texto = patron.sub(reemplazo, texto)
    return _recortar(texto, 200)


def _argumentos(job: Any) -> str:
    try:
        partes = [repr(a) for a in (job.args or ())]
        partes += [f"{k}={v!r}" for k, v in (job.kwargs or {}).items()]
    except Exception:  # noqa: BLE001 — datos ilegibles: se dice, no se cae
        return "(no se pueden leer)"
    return _recortar(", ".join(partes), 240)


def leer_fallidos(
    conn: Any = None, *, max_por_cola: int = MAX_POR_COLA
) -> tuple[list[TrabajoFallido], dict[str, int]]:
    """`(trabajos, {cola: cuántos hay en su registro})`, los más recientes
    primero y hasta `max_por_cola` por cola. Solo lee: no limpia el registro
    (`get_job_ids` de RQ lo haría) ni toca los trabajos."""
    from rq import Queue  # noqa: PLC0415
    from rq.job import Job  # noqa: PLC0415
    from rq.registry import FailedJobRegistry  # noqa: PLC0415

    from app.workers.queues import redis_connection  # noqa: PLC0415

    conn = conn or redis_connection()
    out: list[TrabajoFallido] = []
    totales: dict[str, int] = {}
    for cola in Queue.all(connection=conn):
        registro = FailedJobRegistry(queue=cola)
        totales[cola.name] = len(registro)
        leidos = 0
        while leidos < max_por_cola:
            # Puntuación = momento del fallo + TTL: al revés, los más nuevos.
            pagina = [
                i.decode() if isinstance(i, bytes) else str(i)
                for i in conn.zrevrange(registro.key, leidos, leidos + PAGINA - 1)
            ]
            if not pagina:
                break
            leidos += len(pagina)
            for job_id, job in zip(
                pagina, Job.fetch_many(pagina, connection=conn), strict=False
            ):
                if job is None:
                    out.append(TrabajoFallido(
                        job_id, cola.name, "(datos del trabajo caducados)",
                        "", None, "(sin detalle del error)"))
                    continue
                try:
                    funcion = job.func_name
                except Exception:  # noqa: BLE001
                    funcion = "(desconocida)"
                fecha = job.ended_at or job.enqueued_at
                if fecha is not None and fecha.tzinfo is None:
                    fecha = fecha.replace(tzinfo=UTC)
                out.append(TrabajoFallido(
                    job_id, cola.name, str(funcion), _argumentos(job), fecha,
                    _recortar(_ultima_linea(job.exc_info), 300)))
            if len(pagina) < PAGINA:
                break
    return out, totales


def agrupar(trabajos: list[TrabajoFallido]) -> list[GrupoFallidos]:
    """Un grupo por cola + función + tipo de error, del más gordo al más
    pequeño."""
    grupos: dict[str, GrupoFallidos] = {}
    for t in trabajos:
        corta = t.funcion.rsplit(".", 1)[-1]
        grupo = GrupoFallidos(cola=t.cola, funcion=corta,
                              tipo_error=tipo_de_error(t.error))
        grupo = grupos.setdefault(grupo.clave, grupo)
        grupo.anota(t)
        if not t.argumentos and t.fecha is None:
            grupo.sin_datos += 1
    return sorted(grupos.values(), key=lambda g: (-g.cuantos, g.cola, g.funcion))


def memoria_de_colas(conn: Any = None) -> dict[str, Any]:
    """Cuánta memoria de Redis ocupan los trabajos fallidos.

    Se mide por muestreo: `MEMORY USAGE` de unas cuantas claves de trabajo,
    por la media, por el total del registro. Medir las 149.603 una a una
    costaría más que el dato. Devuelve también la memoria total de Redis, que
    es con lo que se compara para saber si preocupa.
    """
    from rq import Queue  # noqa: PLC0415
    from rq.registry import FailedJobRegistry  # noqa: PLC0415

    from app.workers.queues import redis_connection  # noqa: PLC0415

    conn = conn or redis_connection()
    muestra_bytes = 0
    muestra_n = 0
    por_cola: dict[str, int] = {}
    for cola in Queue.all(connection=conn):
        registro = FailedJobRegistry(queue=cola)
        cuantos = len(registro)
        por_cola[cola.name] = cuantos
        if not cuantos:
            continue
        ids = [i.decode() if isinstance(i, bytes) else str(i)
               for i in conn.zrevrange(registro.key, 0, 24)]
        for job_id in ids:
            try:
                usados = conn.memory_usage(f"rq:job:{job_id}")
            except Exception:  # noqa: BLE001 — Redis viejo sin MEMORY USAGE
                usados = None
            if usados:
                muestra_bytes += int(usados)
                muestra_n += 1
    total_trabajos = sum(por_cola.values())
    media = (muestra_bytes / muestra_n) if muestra_n else 0
    try:
        usada = int(conn.info("memory").get("used_memory") or 0)
    except Exception:  # noqa: BLE001
        usada = 0
    return {
        "fallidos_por_cola": por_cola,
        "fallidos_total": total_trabajos,
        "bytes_por_trabajo_medio": round(media),
        "bytes_estimados": round(media * total_trabajos),
        "redis_usada_bytes": usada,
        "muestra": muestra_n,
    }


def _fecha(dt: datetime | None) -> str:
    return dt.strftime("%d/%m/%Y %H:%M") if dt else "fecha desconocida"


def _id_de_grupo(grupo: GrupoFallidos) -> str:
    """Estable entre pasadas, para que «revisado» se quede puesto. Cabe en
    los 64 caracteres de la columna."""
    return hashlib.sha256(grupo.clave.encode("utf-8")).hexdigest()[:48]


@comprobacion(
    id=TRABAJO_COLA_FALLIDO, orden=17,
    titulo="Trabajos en cola fallidos sin revisar",
    descripcion="Trabajos en segundo plano (sincronizaciones, importaciones, envíos…) que "
                "terminaron con error y siguen en el registro de fallidos de las colas: "
                "nadie los mira por sí solos. Agrupados por función y tipo de error.",
    severidad="media", fuente=FUENTE_MYSQL, grupo="integraciones",
)
def trabajo_cola_fallido(ctx: Contexto) -> Iterator[Hallazgo]:
    _ = ctx
    trabajos, totales = leer_fallidos()
    ahora = datetime.now(UTC)
    for grupo in agrupar(trabajos):
        ejemplo = grupo.ejemplo
        sigue = grupo.sigue_pasando(ahora)
        # Si el registro de la cola tiene más de lo que se ha mirado, el
        # recuento es un «al menos»: decirlo es parte del dato.
        hay = totales.get(grupo.cola, 0)
        recorte = hay > MAX_POR_COLA
        cuantos = f"al menos {grupo.cuantos}" if recorte else str(grupo.cuantos)
        detalle = (
            f"{cuantos} fallo(s) de `{grupo.funcion}` en la cola {grupo.cola} "
            f"con el mismo error: {grupo.tipo_error}. "
            f"Del {_fecha(grupo.primero)} al {_fecha(grupo.ultimo)}"
            + (". Sigue pasando." if sigue else ". No ha vuelto a pasar.")
        )
        if ejemplo is not None:
            detalle += f" Ejemplo: {grupo.funcion}({ejemplo.argumentos}) → {ejemplo.error}"
        yield Hallazgo(
            entidad_tipo=ENTIDAD_TRABAJO_COLA,
            entidad_id=_id_de_grupo(grupo),
            etiqueta=f"{grupo.funcion} · cola {grupo.cola} · {grupo.cuantos}",
            detalle=detalle,
            pista_de_arreglo=(
                "Mira el error del ejemplo. Si el arreglo ya está desplegado, "
                "reencola la cola desde «Colas»; si ya no importa, vacía su "
                "registro. Si sigue pasando, lo primero es la causa."
            ),
            severidad=grupo.severidad(ahora),
            # Lo que motiva el descuadre: el tipo de error y el DÍA del último
            # fallo. Con el recuento exacto en la huella, cada fallo nuevo
            # reabriría un descuadre ya revisado; con el día, como mucho una
            # vez al día mientras siga pasando.
            huella_datos={
                "error": grupo.tipo_error,
                "ultimo_dia": grupo.ultimo.date().isoformat() if grupo.ultimo else None,
            },
            datos={
                "cola": grupo.cola, "funcion": grupo.funcion,
                "tipo_error": grupo.tipo_error, "cuantos": grupo.cuantos,
                "recuento_recortado": recorte,
                "en_el_registro": hay,
                "primero": grupo.primero.isoformat() if grupo.primero else None,
                "ultimo": grupo.ultimo.isoformat() if grupo.ultimo else None,
                "sigue_pasando": sigue,
                "sin_datos": grupo.sin_datos,
                "ejemplo_argumentos": ejemplo.argumentos if ejemplo else "",
                "ejemplo_error": ejemplo.error if ejemplo else "",
                "ejemplo_ids": grupo.ids,
            },
        )
