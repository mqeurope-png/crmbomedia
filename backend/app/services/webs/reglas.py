"""Las reglas de qué entra en el índice de las webs y qué no (tabla
`web_indice_reglas`, editable desde Configuración ERP → Base de conocimiento).

Mientras la tabla esté vacía valen las de partida (`reglas_partida`, las mismas
que siembra la migración 0148): así un test con `create_all` o una base recién
creada se comportan igual que producción. La pantalla siembra la tabla con
ellas la primera vez que la abre (`asegurar_sembradas`) para que se puedan
editar.

- `pagina_excluir_slug`: expresión regular (sin distinguir mayúsculas) sobre
  el slug de páginas y entradas.
- `pagina_excluir_id`: id de WordPress de una página o entrada (en WPML cada
  idioma tiene sus ids: se pone el de cada uno).
- `categoria_fuera`: un producto queda FUERA si alguna de sus categorías, o
  alguna madre de ellas, EMPIEZA por este nombre (sin tildes ni mayúsculas).
- `producto_excluir_id` / `producto_incluir_id`: excepciones por id de
  producto. Si un id está en las dos, gana excluir (lo prudente: no ofrecer
  un repuesto como máquina).

Cada regla vale para una web (`sitio`) o para todas (`sitio` vacío).
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.fuentes_externas import (
    REGLA_CATEGORIA_FUERA,
    REGLA_PAGINA_ID,
    REGLA_PAGINA_SLUG,
    REGLA_PRODUCTO_EXCLUIR,
    REGLA_PRODUCTO_INCLUIR,
    REGLA_TIPOS,
    WebIndiceRegla,
)
from app.services.externo.html_texto import plano
from app.services.webs.reglas_partida import CATEGORIAS_FUERA, PAGINAS_FUERA

logger = logging.getLogger(__name__)

#: Tipos cuyo patrón es un id numérico de WordPress.
TIPOS_ID = (REGLA_PAGINA_ID, REGLA_PRODUCTO_EXCLUIR, REGLA_PRODUCTO_INCLUIR)


@dataclass(frozen=True)
class ReglaSuelta:
    """Una regla ya leída (de la tabla o de las de partida)."""

    tipo: str
    patron: str
    sitio: str | None = None


def _de_partida() -> list[ReglaSuelta]:
    reglas = [ReglaSuelta(REGLA_PAGINA_SLUG, patron) for patron, _ in PAGINAS_FUERA]
    reglas += [ReglaSuelta(REGLA_CATEGORIA_FUERA, prefijo) for prefijo, _ in CATEGORIAS_FUERA]
    return reglas


def _vale(regla: ReglaSuelta, sitio: str) -> bool:
    return not regla.sitio or regla.sitio == sitio


@dataclass
class ReglasSitio:
    """Las reglas que valen para UNA web, ya compiladas."""

    sitio: str
    slugs: list[re.Pattern[str]] = field(default_factory=list)
    paginas_id: set[int] = field(default_factory=set)
    #: Prefijos de categoría, ya en `plano` (sin tildes, minúsculas).
    categorias: list[str] = field(default_factory=list)
    excluir: set[int] = field(default_factory=set)
    incluir: set[int] = field(default_factory=set)

    def pagina_fuera(self, slug: str | None, wp_id: int) -> bool:
        if wp_id in self.paginas_id:
            return True
        # WordPress guarda los slugs con tildes codificados (`%c3%a9`).
        limpio = unquote(slug or "")
        return any(p.search(limpio) for p in self.slugs)

    def categoria_fuera(self, nombre: str | None) -> bool:
        nombre_plano = plano(html.unescape(nombre or "")).strip()
        return bool(nombre_plano) and any(nombre_plano.startswith(p) for p in self.categorias)


@dataclass
class Reglas:
    """Todas las reglas activas; `para(sitio)` da las de una web."""

    reglas: list[ReglaSuelta]
    #: True si salen de la tabla; False si son las de partida.
    de_la_base: bool = False

    def para(self, sitio: str) -> ReglasSitio:
        salida = ReglasSitio(sitio=sitio)
        for regla in self.reglas:
            if not _vale(regla, sitio):
                continue
            patron = (regla.patron or "").strip()
            if not patron:
                continue
            if regla.tipo == REGLA_PAGINA_SLUG:
                try:
                    salida.slugs.append(re.compile(patron, re.IGNORECASE))
                except re.error as exc:
                    # La API no deja guardar una que no compile; si alguien la
                    # metió a mano en la base, se salta y se avisa.
                    logger.warning("webs: regla de slug %r no compila (%s); se salta",
                                   patron, exc)
            elif regla.tipo == REGLA_CATEGORIA_FUERA:
                prefijo = plano(html.unescape(patron)).strip()
                if prefijo:
                    salida.categorias.append(prefijo)
            elif regla.tipo in TIPOS_ID:
                try:
                    valor = int(patron)
                except ValueError:
                    logger.warning("webs: regla %s con id no numérico %r; se salta",
                                   regla.tipo, patron)
                    continue
                {REGLA_PAGINA_ID: salida.paginas_id, REGLA_PRODUCTO_EXCLUIR: salida.excluir,
                 REGLA_PRODUCTO_INCLUIR: salida.incluir}[regla.tipo].add(valor)
        return salida


def cargar(session: Session) -> Reglas:
    """Las reglas activas de la tabla; con la tabla vacía, las de partida."""
    filas = list(session.scalars(
        select(WebIndiceRegla).order_by(WebIndiceRegla.posicion, WebIndiceRegla.id)))
    if not filas:
        return Reglas(reglas=_de_partida(), de_la_base=False)
    return Reglas(
        reglas=[ReglaSuelta(f.tipo, f.patron, f.sitio or None) for f in filas if f.activo],
        de_la_base=True)


def asegurar_sembradas(session: Session) -> bool:
    """Con la tabla vacía, la siembra con las de partida (sin commit) para que
    la pantalla las pueda editar. True si ha sembrado. En producción la tabla
    ya la sembró la migración y esto no hace nada."""
    if session.scalar(select(func.count()).select_from(WebIndiceRegla)):
        return False
    for posicion, (patron, nota) in enumerate(PAGINAS_FUERA):
        session.add(WebIndiceRegla(tipo=REGLA_PAGINA_SLUG, patron=patron, sitio=None,
                                   activo=True, nota=nota, posicion=posicion))
    for posicion, (prefijo, nota) in enumerate(CATEGORIAS_FUERA):
        session.add(WebIndiceRegla(tipo=REGLA_CATEGORIA_FUERA, patron=prefijo, sitio=None,
                                   activo=True, nota=nota or None, posicion=posicion))
    session.flush()
    return True


# --- validación de lo que llega de la pantalla -------------------------------------------------


def validar_patron(tipo: str, patron: Any) -> str:
    """El patrón limpio para ese tipo de regla. `ValueError` legible si no vale."""
    if tipo not in REGLA_TIPOS:
        raise ValueError(f"Tipo de regla no válido: {tipo!r} ({', '.join(REGLA_TIPOS)}).")
    texto = str(patron or "").strip()
    if not texto:
        raise ValueError("La regla necesita un patrón.")
    if len(texto) > 255:
        raise ValueError("El patrón no puede pasar de 255 caracteres.")
    if tipo == REGLA_PAGINA_SLUG:
        try:
            re.compile(texto, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"La expresión regular no es válida: {exc}.") from exc
    elif tipo in TIPOS_ID:
        if not texto.isdigit():
            raise ValueError("Esta regla es un id de WordPress: solo números.")
        texto = str(int(texto))
    return texto


def regla_como_dict(fila: WebIndiceRegla) -> dict[str, Any]:
    """La forma `WebRegla` de `websApi.ts`."""
    return {
        "id": fila.id, "tipo": fila.tipo, "patron": fila.patron, "sitio": fila.sitio or None,
        "activo": bool(fila.activo), "nota": fila.nota or None, "posicion": int(fila.posicion or 0),
    }
