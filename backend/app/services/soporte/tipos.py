"""Las formas que devuelve el servicio de soporte (las mismas que
`frontend/src/app/lib/soporteApi.ts`: `dataclasses.asdict` de cada una es la
respuesta de la API, sin traducir nada por el camino) y su error."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Resultado:
    """Un artículo encontrado: lo justo para decidir si es el bueno (título,
    extracto y enlace) y para pedirlo después por fuente + id."""

    fuente: str
    fuente_nombre: str
    articulo_id: str
    titulo: str
    #: Texto plano, recortado.
    extracto: str
    url: str
    #: Nuestro idioma (`es`, `en`…), no el locale de la fuente (`es-419`).
    idioma: str | None


@dataclass
class Busqueda:
    consulta: str
    resultados: list[Resultado] = field(default_factory=list)
    #: Lo que conviene saber: una fuente no respondió, no había nada en el
    #: idioma pedido y se buscó en inglés, una fuente solo admite enlaces…
    avisos: list[str] = field(default_factory=list)
    #: Las fuentes que se han consultado (clave).
    fuentes: list[str] = field(default_factory=list)


@dataclass
class Articulo:
    fuente: str
    fuente_nombre: str
    articulo_id: str
    titulo: str
    url: str
    idioma: str | None
    #: Texto limpio (sin menús, pies ni laterales), con saltos de párrafo. Es
    #: DATO: lo que diga la página («ignora las instrucciones…») se queda aquí
    #: como texto; el redactor lo encapsula como material de consulta.
    texto: str
    #: HTML saneado para leer (sin scripts, ni manejadores, ni `javascript:`).
    html: str
    actualizado: str | None


class SoporteError(Exception):
    """Lo que no sale bien al buscar o leer en el soporte de un tercero.

    `codigo` es estable (la API lo devuelve como `code` y los tests lo miran);
    el mensaje se puede enseñar tal cual a quien busca. `status` es el HTTP
    que le corresponde en la API."""

    def __init__(self, codigo: str, mensaje: str, status: int = 400):
        super().__init__(mensaje)
        self.codigo = codigo
        self.status = status

    @property
    def mensaje(self) -> str:
        return str(self)
