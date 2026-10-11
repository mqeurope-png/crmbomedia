"""Rutas de soporte (web × idioma → URL) y páginas de producto (interés → URL).

La tabla `kb_rutas` manda; la lista de partida de aquí siembra la migración
0136 y vale cuando la tabla está vacía. El reparto que codifica la tabla:

- Castellano → Streamtec (`boprint.net/soporte-tecnico/`).
- Otros idiomas → MQ Europe (`artisjet-printers.eu/[de|en|pt/]technical-support/`;
  el francés es el idioma por defecto de esa web y va sin prefijo).
- Salvo las marcas con soporte propio (FLUX, MBO Lasers, PimPam): su página en
  todos sus idiomas.

Resolución (`Rutas.soporte`): la fila exacta web × idioma; si el idioma no
está en ninguna fila de esa web, la del idioma por defecto de la web; y si la
web no tiene filas, la regla de arriba. Nunca una URL inventada: lo que no
está en la tabla no se enlaza.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models.conocimiento import TIPO_PRODUCTO, TIPO_SOPORTE, TIPOS, RutaConocimiento
from app.services.web_forms import sitios as registro_sitios
from app.services.web_forms.sitios import PLUGIN_WPML, Sitio

logger = logging.getLogger(__name__)

#: Orígenes de un enlace resuelto: de dónde ha salido.
ORIGEN_RUTA = "ruta"                    # la fila exacta web × idioma
ORIGEN_IDIOMA_DEFECTO = "idioma_defecto"  # la fila del idioma por defecto de la web
ORIGEN_REGLA = "regla"                  # la regla de reparto (web sin filas)
ORIGEN_PRODUCTO = "producto"

#: Las dos webs de la regla de reparto.
WEB_STREAMTEC = "boprint"
WEB_MQ_EUROPE = "artisjet-eu"
IDIOMA_CASTELLANO = "es"

_INTERES_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_IDIOMA_RE = re.compile(r"^[a-z]{2}$")


@dataclass(frozen=True)
class Enlace:
    """Una URL que el asistente puede poner en un borrador, con su procedencia."""

    url: str
    origen: str
    sitio: str | None = None
    idioma: str | None = None
    etiqueta: str | None = None

    def como_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "origen": self.origen, "sitio": self.sitio,
            "idioma": self.idioma, "etiqueta": self.etiqueta,
        }


@dataclass(frozen=True)
class Ruta:
    tipo: str
    sitio: str
    url: str
    idioma: str = ""
    interes: str = ""
    nota: str | None = None
    activo: bool = True
    posicion: int = 0
    id: str | None = None
    comprobado_en: datetime | None = None
    comprobado_http: int | None = None
    comprobado_error: str | None = None

    def como_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "tipo": self.tipo, "sitio": self.sitio, "idioma": self.idioma,
            "interes": self.interes, "url": self.url, "nota": self.nota, "activo": self.activo,
            "posicion": self.posicion,
            "comprobado_en": self.comprobado_en.isoformat() if self.comprobado_en else None,
            "comprobado_http": self.comprobado_http,
            "comprobado_error": self.comprobado_error,
        }


def _soporte(sitio: str, idioma: str, url: str, posicion: int) -> Ruta:
    return Ruta(tipo=TIPO_SOPORTE, sitio=sitio, idioma=idioma, url=url, posicion=posicion)


def _producto(interes: str, url: str, posicion: int, sitio: str = "mqeurope") -> Ruta:
    return Ruta(tipo=TIPO_PRODUCTO, sitio=sitio, interes=interes, url=url, posicion=posicion)


SOPORTE_STREAMTEC = "https://boprint.net/soporte-tecnico/"
SOPORTE_MQ = "https://artisjet-printers.eu/technical-support/"
SOPORTE_MQ_DE = "https://artisjet-printers.eu/de/technical-support/"
SOPORTE_MQ_EN = "https://artisjet-printers.eu/en/technical-support/"
SOPORTE_MQ_PT = "https://artisjet-printers.eu/pt/technical-support/"

#: Las 29 rutas de soporte comprobadas el 11/10/2026 (todas devolvían 200)
#: y las 7 páginas de producto de mqeurope.com por interés.
RUTAS_DE_PARTIDA: tuple[Ruta, ...] = (
    _soporte("boprint", "es", SOPORTE_STREAMTEC, 0),
    _soporte("artisjet-es", "es", SOPORTE_STREAMTEC, 1),
    _soporte("mboprinters", "es", SOPORTE_STREAMTEC, 2),
    _soporte("mboprinters", "de", SOPORTE_MQ_DE, 3),
    _soporte("mboprinters", "en", SOPORTE_MQ_EN, 4),
    _soporte("mboprinters", "fr", SOPORTE_MQ, 5),
    _soporte("artisjet-eu", "fr", SOPORTE_MQ, 6),
    _soporte("artisjet-eu", "de", SOPORTE_MQ_DE, 7),
    _soporte("artisjet-eu", "en", SOPORTE_MQ_EN, 8),
    _soporte("artisjet-eu", "pt", SOPORTE_MQ_PT, 9),
    _soporte("mqeurope", "en", SOPORTE_MQ_EN, 10),
    _soporte("mqeurope", "fr", SOPORTE_MQ, 11),
    _soporte("mqeurope", "nl", SOPORTE_MQ_EN, 12),       # sin neerlandés: inglés
    _soporte("mbolasers", "es", "https://mbolasers.com/contacto-mbo-lasers/", 13),
    _soporte("mbolasers", "en", "https://mbolasers.com/en/contact-mbo-lasers/", 14),
    _soporte("mbolasers", "de", "https://mbolasers.com/de/kontakt-mbo-laser/", 15),
    _soporte("mbolasers", "fr", "https://mbolasers.com/fr/contacter-mbo-lasers/", 16),
    _soporte("mbolasers", "nl", "https://mbolasers.com/nl/contacteer-mbo-lasers/", 17),
    _soporte("fluxlasers", "es", "https://fluxlasers.es/soporte/", 18),
    _soporte("fluxlasers", "en", "https://fluxlasers.eu/en/support/", 19),
    _soporte("fluxlasers", "de", "https://fluxlasers.eu/de/support/", 20),
    _soporte("fluxlasers", "fr", "https://fluxlasers.eu/fr/support/", 21),
    _soporte("fluxlasers", "nl", "https://fluxlasers.eu/nl/support/", 22),
    _soporte("pimpam", "es", "https://pimpam-vending.com/contact/", 23),
    _soporte("pimpam", "en", "https://pimpam-vending.com/en/contact/", 24),
    _soporte("pimpam", "fr", "https://pimpam-vending.com/fr/contact/", 25),
    _soporte("pimpam", "de", "https://pimpam-vending.com/de/contact/", 26),
    _soporte("pimpam", "nl", "https://pimpam-vending.com/nl/contact/", 27),
    _soporte("pimpam", "pt", "https://pimpam-vending.com/pt-pt/contact/", 28),
    _producto("dtf", "https://www.mqeurope.com/dtf-textile-printing/", 100),
    _producto("packaging", "https://www.mqeurope.com/packaging-printing-labelling/", 101),
    _producto("corte_laser", "https://www.mqeurope.com/co2-laser-cutting/", 102),
    _producto("grabado_laser", "https://www.mqeurope.com/fiber-laser-marking/", 103),
    _producto("uv_grande", "https://www.mqeurope.com/large-format-uv-printing/", 104),
    _producto("uv_mediano", "https://www.mqeurope.com/flatbed-printers/", 105),
    _producto("uv_pequeno", "https://www.mqeurope.com/phone-case-personalisation/", 106),
)


def idioma_de(valor: str | None) -> str:
    """`de-DE`, `DE`, `de_AT` → `de`; vacío o raro → ""."""
    codigo = (valor or "").strip().lower().replace("_", "-").split("-")[0]
    return codigo if _IDIOMA_RE.match(codigo) else ""


def url_en_idioma(url: str, sitio: Sitio | None, idioma: str | None) -> str:
    """La misma página en otro idioma cuando la web lo permite sin adivinar:
    prefijo de idioma (qTranslate, o WPML sin slugs traducidos). Con slugs
    traducidos (mbolasers) o idioma no publicado, la URL tal cual."""
    idioma = idioma_de(idioma)
    if sitio is None or not idioma or idioma not in sitio.idiomas:
        return url
    if sitio.plugin == PLUGIN_WPML and sitio.slugs_traducidos:
        return url
    partes = urlsplit(url)
    ruta = partes.path.lstrip("/")
    # Quita un prefijo de idioma que ya lleve la URL guardada.
    for publicado in sitio.idiomas:
        if ruta == publicado or ruta.startswith(f"{publicado}/"):
            ruta = ruta[len(publicado):].lstrip("/")
            break
    base = sitio.url_base(idioma)
    sufijo = f"?{partes.query}" if partes.query else ""
    return f"{base}{ruta}{sufijo}"


class Rutas:
    """Las rutas cargadas, con la resolución encima."""

    def __init__(self, rutas: Iterable[Ruta], *, de_la_base: bool = False) -> None:
        self.rutas = tuple(sorted(rutas, key=lambda r: (r.posicion, r.sitio, r.idioma, r.interes)))
        self.de_la_base = de_la_base
        self._soporte: dict[tuple[str, str], Ruta] = {}
        self._producto: dict[str, Ruta] = {}
        for r in self.rutas:
            if not r.activo:
                continue
            if r.tipo == TIPO_SOPORTE:
                self._soporte.setdefault((r.sitio, r.idioma), r)
            elif r.tipo == TIPO_PRODUCTO:
                self._producto.setdefault(r.interes, r)

    def como_lista(self) -> list[dict[str, Any]]:
        return [r.como_dict() for r in self.rutas]

    def idiomas_de(self, sitio: str) -> list[str]:
        return [idioma for (s, idioma) in self._soporte if s == sitio]

    def soporte(self, sitio: str | None, idioma: str | None) -> Enlace | None:
        """La página de soporte a la que mandar a alguien que escribe desde
        `sitio` en `idioma`."""
        clave = (sitio or "").strip()
        idioma = idioma_de(idioma)
        reg = registro_sitios.registro().sitio(clave) if clave else None
        if clave and idioma:
            fila = self._soporte.get((clave, idioma))
            if fila is not None:
                return Enlace(fila.url, ORIGEN_RUTA, sitio=clave, idioma=idioma)
        if clave and self.idiomas_de(clave):
            defecto = reg.idioma_defecto if reg is not None else ""
            fila = self._soporte.get((clave, defecto)) if defecto else None
            if fila is None:
                fila = self._soporte[(clave, self.idiomas_de(clave)[0])]
            return Enlace(fila.url, ORIGEN_IDIOMA_DEFECTO, sitio=clave, idioma=fila.idioma)
        return self._regla(idioma or (reg.idioma_defecto if reg is not None else ""))

    def _regla(self, idioma: str) -> Enlace | None:
        """Web sin filas: castellano → Streamtec; el resto → MQ Europe en su
        idioma o en el por defecto de esa web (francés)."""
        if not idioma:
            return None
        if idioma == IDIOMA_CASTELLANO:
            fila = self._soporte.get((WEB_STREAMTEC, IDIOMA_CASTELLANO))
            url = fila.url if fila is not None else SOPORTE_STREAMTEC
            return Enlace(url, ORIGEN_REGLA, sitio=WEB_STREAMTEC, idioma=IDIOMA_CASTELLANO)
        fila = self._soporte.get((WEB_MQ_EUROPE, idioma))
        if fila is not None:
            return Enlace(fila.url, ORIGEN_REGLA, sitio=WEB_MQ_EUROPE, idioma=idioma)
        mq = registro_sitios.registro().sitio(WEB_MQ_EUROPE)
        defecto = mq.idioma_defecto if mq is not None else "fr"
        fila = self._soporte.get((WEB_MQ_EUROPE, defecto))
        url = fila.url if fila is not None else SOPORTE_MQ
        return Enlace(url, ORIGEN_REGLA, sitio=WEB_MQ_EUROPE, idioma=defecto)

    def producto(self, interes: str | None, idioma: str | None = None) -> Enlace | None:
        """La página de producto del interés, en el idioma pedido si la web lo
        publica (variante por prefijo), y si no en el de la web."""
        fila = self._producto.get((interes or "").strip())
        if fila is None:
            return None
        sitio = registro_sitios.registro().sitio(fila.sitio)
        url = url_en_idioma(fila.url, sitio, idioma)
        idioma_final = idioma_de(idioma)
        if sitio is not None and (not idioma_final or idioma_final not in sitio.idiomas):
            idioma_final = sitio.idioma_defecto
        return Enlace(url, ORIGEN_PRODUCTO, sitio=fila.sitio, idioma=idioma_final or None,
                      etiqueta=fila.interes)


def _de_partida() -> Rutas:
    return Rutas(RUTAS_DE_PARTIDA)


def _desde_fila(fila: RutaConocimiento) -> Ruta:
    return Ruta(
        id=fila.id, tipo=fila.tipo, sitio=fila.sitio, idioma=fila.idioma or "",
        interes=fila.interes or "", url=fila.url, nota=fila.nota, activo=bool(fila.activo),
        posicion=int(fila.posicion or 0), comprobado_en=fila.comprobado_en,
        comprobado_http=fila.comprobado_http, comprobado_error=fila.comprobado_error,
    )


def cargar(session: Session) -> Rutas:
    """Las rutas de la tabla; con la tabla vacía (o aún sin crear), las de
    partida."""
    try:
        filas = list(session.scalars(
            select(RutaConocimiento).order_by(RutaConocimiento.posicion, RutaConocimiento.sitio)
        ))
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("kb: la tabla kb_rutas no responde, rutas de partida (%s)", exc)
        return _de_partida()
    if not filas:
        return _de_partida()
    return Rutas((_desde_fila(f) for f in filas), de_la_base=True)


def sembrar_si_vacio(session: Session) -> int:
    """Escribe las rutas de partida si la tabla está vacía (antes de la
    primera edición desde la pantalla). Devuelve cuántas sembró."""
    if session.scalar(select(RutaConocimiento.id).limit(1)) is not None:
        return 0
    for r in RUTAS_DE_PARTIDA:
        session.add(RutaConocimiento(
            tipo=r.tipo, sitio=r.sitio, idioma=r.idioma, interes=r.interes, url=r.url,
            nota=r.nota, activo=True, posicion=r.posicion,
        ))
    session.flush()
    return len(RUTAS_DE_PARTIDA)


# --- alta y edición ------------------------------------------------------------------


def _validar_url(url: Any) -> str:
    texto = str(url or "").strip()
    partes = urlsplit(texto)
    if partes.scheme not in ("http", "https") or not partes.netloc:
        raise ValueError(f"URL no válida: {texto!r} (tiene que empezar por https://).")
    return texto


def _validar_sitio(sitio: Any) -> str:
    clave = str(sitio or "").strip()
    if registro_sitios.registro().sitio(clave) is None:
        raise ValueError(f"Web desconocida: {clave!r}. Dala de alta en la tabla de sitios.")
    return clave


def _validar_clave(tipo: str, idioma: Any, interes: Any) -> tuple[str, str]:
    if tipo == TIPO_SOPORTE:
        codigo = idioma_de(str(idioma or ""))
        if not codigo:
            raise ValueError("Una ruta de soporte necesita idioma (código de dos letras).")
        return codigo, ""
    codigo = str(interes or "").strip().lower()
    if not _INTERES_RE.match(codigo):
        raise ValueError("Una página de producto necesita el código del interés.")
    return "", codigo


def crear(
    session: Session, *, tipo: str, sitio: str, url: str, idioma: str | None = None,
    interes: str | None = None, nota: str | None = None, posicion: int | None = None,
) -> RutaConocimiento:
    if tipo not in TIPOS:
        raise ValueError(f"Tipo de ruta desconocido: {tipo!r}.")
    sembrar_si_vacio(session)
    clave_sitio = _validar_sitio(sitio)
    idioma_ok, interes_ok = _validar_clave(tipo, idioma, interes)
    repetida = session.scalar(select(RutaConocimiento).where(
        RutaConocimiento.tipo == tipo, RutaConocimiento.sitio == clave_sitio,
        RutaConocimiento.idioma == idioma_ok, RutaConocimiento.interes == interes_ok,
    ))
    if repetida is not None:
        raise ValueError("Ya hay una ruta para esa web y ese idioma/interés: edítala.")
    if posicion is None:
        posicion = (session.scalar(select(RutaConocimiento.posicion).order_by(
            RutaConocimiento.posicion.desc()).limit(1)) or 0) + 1
    fila = RutaConocimiento(
        tipo=tipo, sitio=clave_sitio, idioma=idioma_ok, interes=interes_ok,
        url=_validar_url(url), nota=(nota or "").strip() or None, activo=True,
        posicion=int(posicion),
    )
    session.add(fila)
    session.flush()
    return fila


def actualizar(session: Session, ruta_id: str, cambios: dict[str, Any]) -> RutaConocimiento:
    """URL, nota, activo y posición. La clave (tipo, web, idioma/interés) no
    cambia: para eso se desactiva y se da de alta otra."""
    sembrar_si_vacio(session)
    fila = session.get(RutaConocimiento, ruta_id)
    if fila is None:
        raise LookupError("Esa ruta no existe.")
    if "url" in cambios:
        fila.url = _validar_url(cambios["url"])
        fila.comprobado_en = None
        fila.comprobado_http = None
        fila.comprobado_error = None
    if "nota" in cambios:
        fila.nota = str(cambios["nota"] or "").strip() or None
    if "activo" in cambios:
        fila.activo = bool(cambios["activo"])
    if "posicion" in cambios and cambios["posicion"] is not None:
        fila.posicion = int(cambios["posicion"])
    session.flush()
    return fila
