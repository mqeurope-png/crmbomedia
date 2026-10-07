"""Registro de comprobaciones del Cuadre.

Una comprobación es una función que recibe el `Contexto` de la pasada y
devuelve los descuadres que ve (`Hallazgo`). Para añadir una nueva basta con
escribir la función y decorarla con `@comprobacion(...)` en uno de los módulos
de comprobaciones (`checks_mysql.py` / `checks_factusol.py` /
`checks_woocommerce.py`): el motor, la
API, el job nocturno, la Configuración ERP y la pantalla la recogen solas.
Ver «Cómo añadir una comprobación» en `docs/erp/cuadre.md`.

Reglas de una comprobación:
  - SOLO LECTURA: no escribe en FACTUSOL ni en la BD, no mueve estados.
  - `huella_datos` lleva los valores que MOTIVAN el descuadre (importes,
    estados), nunca los días transcurridos: un descuadre marcado «revisado»
    no reaparece mientras su huella no cambie.
  - `entidad_id` es estable (el `Order.id`, el `serie-código` de la factura…).
  - FACTUSOL se lee solo con `ctx.tabla(...)` (una lectura por tabla y
    pasada, del ejercicio en curso): nada de consultas fila a fila.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from app.erp.cuadre.contexto import Contexto

SEVERIDADES: tuple[str, ...] = ("alta", "media", "baja")
FUENTE_MYSQL = "mysql"
FUENTE_FACTUSOL = "factusol"
#: Las que preguntan a las tiendas (HTTP): corren en el worker, como FACTUSOL.
FUENTE_WOOCOMMERCE = "woocommerce"
FUENTES: tuple[str, ...] = (FUENTE_MYSQL, FUENTE_FACTUSOL, FUENTE_WOOCOMMERCE)
#: Fuentes que «Comprobar ahora» manda al worker (nunca en la petición web).
FUENTES_EN_SEGUNDO_PLANO: tuple[str, ...] = (FUENTE_FACTUSOL, FUENTE_WOOCOMMERCE)

#: Tipos de entidad de un descuadre.
ENTIDAD_PEDIDO = "pedido"
ENTIDAD_FACTURA = "factura"
ENTIDAD_PRESUPUESTO = "presupuesto"
ENTIDAD_FILA_HOJA = "fila_hoja"
ENTIDAD_CUENTA = "cuenta_integracion"
#: Un pedido de la tienda que (aún) no está en BoHub: `tienda:id de Woo`.
ENTIDAD_PEDIDO_WOO = "pedido_woo"
#: Un trabajo de las colas de RQ (su id).
ENTIDAD_TRABAJO_COLA = "trabajo_cola"


@dataclass
class Hallazgo:
    """Un descuadre tal como lo devuelve una comprobación."""

    entidad_tipo: str
    entidad_id: str
    #: Texto corto que identifica la entidad («BOPRIN-99866», «Factura 5-260086»).
    etiqueta: str
    #: Qué no cuadra, en una frase.
    detalle: str
    #: Dónde se arregla, en una frase.
    pista_de_arreglo: str
    #: Enlace a la entidad (pedido, documento, fila).
    enlace: str | None = None
    #: Botón «a donde se arregla» (pantalla con la acción que ya existe).
    arreglo_enlace: str | None = None
    arreglo_boton: str | None = None
    #: Valores que motivan el descuadre (→ huella). Sin días transcurridos.
    huella_datos: dict[str, Any] = field(default_factory=dict)
    #: Datos extra para la pantalla / el Excel (importe pendiente, días…).
    datos: dict[str, Any] = field(default_factory=dict)
    #: Parte de la fuente a la que pertenece (la tienda): si la pasada no
    #: pudo mirar esa parte, sus descuadres no se dan por resueltos.
    ambito: str | None = None

    def huella(self) -> str:
        raw = json.dumps(self.huella_datos, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def detalle_dict(self) -> dict[str, Any]:
        return {
            "etiqueta": self.etiqueta,
            "detalle": self.detalle,
            "pista_de_arreglo": self.pista_de_arreglo,
            "enlace": self.enlace,
            "arreglo_enlace": self.arreglo_enlace,
            "arreglo_boton": self.arreglo_boton,
            "datos": self.datos,
        }


@dataclass(frozen=True)
class Comprobacion:
    id: str
    titulo: str
    #: Descripción de una línea (sale en la tarjeta y en Configuración).
    descripcion: str
    severidad: str
    fuente: str
    #: dinero | envios | documentos | integraciones (agrupa en la pantalla).
    grupo: str
    funcion: Callable[[Contexto], Iterable[Hallazgo]]
    #: Umbral en días por defecto (None = la comprobación no usa umbral).
    dias_defecto: int | None = None
    #: Qué significa el umbral, para Configuración («Avisar pasados N días»).
    dias_texto: str | None = None
    #: Posición en la pantalla y en Configuración (las nuevas, al final).
    orden: int = 0
    #: Activa si Configuración ERP no dice otra cosa.
    activa_defecto: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "titulo": self.titulo, "descripcion": self.descripcion,
            "severidad": self.severidad, "fuente": self.fuente, "grupo": self.grupo,
            "dias_defecto": self.dias_defecto, "dias_texto": self.dias_texto,
            "activa_defecto": self.activa_defecto,
            "orden": self.orden,
        }


#: id → comprobación, en orden de registro.
REGISTRO: dict[str, Comprobacion] = {}


def comprobacion(
    *, id: str, titulo: str, descripcion: str, severidad: str, fuente: str,  # noqa: A002
    grupo: str, dias_defecto: int | None = None, dias_texto: str | None = None,
    orden: int | None = None, activa_defecto: bool = True,
) -> Callable[[Callable[[Contexto], Iterable[Hallazgo]]], Callable[[Contexto], Iterable[Hallazgo]]]:
    """Decorador que registra una comprobación."""
    if severidad not in SEVERIDADES:
        raise ValueError(f"severidad desconocida: {severidad!r}")
    if fuente not in FUENTES:
        raise ValueError(f"fuente desconocida: {fuente!r}")
    if len(id) > 64:            # `cuadre_findings.check_id` es varchar(64)
        raise ValueError(f"id demasiado largo: {id!r}")

    def deco(func: Callable[[Contexto], Iterable[Hallazgo]]):
        if id in REGISTRO:
            raise ValueError(f"comprobación duplicada: {id!r}")
        REGISTRO[id] = Comprobacion(
            id=id, titulo=titulo, descripcion=descripcion, severidad=severidad,
            fuente=fuente, grupo=grupo, funcion=func, dias_defecto=dias_defecto,
            dias_texto=dias_texto, orden=orden if orden is not None else 100 + len(REGISTRO),
            activa_defecto=activa_defecto,
        )
        return func

    return deco


def registro() -> dict[str, Comprobacion]:
    """El registro completo (importa los módulos de comprobaciones)."""
    from app.erp.cuadre import (  # noqa: F401, PLC0415
        checks_colas,
        checks_factusol,
        checks_mysql,
        checks_woocommerce,
    )

    return REGISTRO


def catalogo() -> list[dict[str, Any]]:
    """Las comprobaciones registradas, para la API y Configuración."""
    return [c.as_dict() for c in sorted(registro().values(), key=lambda c: c.orden)]
