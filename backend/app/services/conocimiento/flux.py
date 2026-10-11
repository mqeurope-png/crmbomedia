"""La base de conocimiento de FLUX (support.flux3dp.com): categorías por
modelo, el enlace por idioma y los dos destinos de un cliente de FLUX.

FLUX tiene DOS sitios a los que mandar a alguien, y son datos, no código:

- Cómo se hace algo, software (Beam Studio), calibración, materiales → la KB
  de FLUX en su idioma y en la categoría de su modelo
  (`https://support.flux3dp.com/hc/{locale}/categories/{id}`).
- Máquina averiada, repuesto, garantía, que vaya alguien → NUESTRO soporte
  (`fluxlasers.es/soporte/` o `fluxlasers.eu/{idioma}/support/`, por la tabla
  de rutas).
- No está claro → el nuestro.

Las FAQ de fluxlasers.es/preguntas-frecuentes/ están desactualizadas y no se
enlazan nunca: no están en ninguna tabla.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models.conocimiento import FluxArticulo, FluxCategoria, FluxSincronizacion
from app.services.conocimiento.rutas import Enlace, idioma_de

BASE_HC = "https://support.flux3dp.com"
API_HC = f"{BASE_HC}/api/v2/help_center"

#: Nuestro idioma → locale de la KB de FLUX (no hay `es-es`: el castellano es
#: `es-419`). Un idioma que no esté aquí va al inglés.
LOCALES: dict[str, str] = {
    "es": "es-419", "en": "en-us", "de": "de-de", "fr": "fr-fr", "nl": "nl", "pt": "pt-br",
}
IDIOMA_DEFECTO = "en"

ORIGEN_FLUX_KB = "flux_kb"
ORIGEN_FLUX_SOPORTE = "flux_soporte"

#: Lo que el cliente pide: a la KB, a nuestro soporte, o no está claro.
INTENCION_KB = "kb"
INTENCION_SOPORTE = "soporte"


@dataclass(frozen=True)
class Categoria:
    categoria_id: str
    modelo: str
    palabras: tuple[str, ...] = ()
    posicion: int = 0
    activo: bool = True

    def como_dict(self) -> dict[str, Any]:
        return {
            "categoria_id": self.categoria_id, "modelo": self.modelo,
            "palabras": list(self.palabras), "posicion": self.posicion, "activo": self.activo,
            "enlace_es": enlace_categoria(self.categoria_id, "es"),
        }


#: Las categorías de la KB de FLUX comprobadas el 11/10/2026 (una por modelo
#: o tema), con las palabras con las que se reconoce el modelo en un texto o
#: en la descripción de una línea de pedido. Las palabras más largas ganan
#: («beambox ii» antes que «beambox»).
CATEGORIAS_DE_PARTIDA: tuple[Categoria, ...] = (
    Categoria("360000108616", "Beamo", ("beamo",), 0),
    Categoria("13025863120655", "beamo II", ("beamo ii", "beamo 2"), 1),
    Categoria("360000121176", "Beambox", ("beambox", "beambox pro"), 2),
    Categoria("10647778378639", "Beambox II", ("beambox ii", "beambox 2"), 3),
    Categoria("4406510807823", "HEXA", ("hexa",), 4),
    Categoria("14101381797647", "HEXA RF", ("hexa rf",), 5),
    Categoria("7104732498447", "Ador", ("ador",), 6),
    Categoria("10552359659535", "Promark", ("promark",), 7),
    Categoria("360000142376", "Beam Air", ("beam air",), 8),
    Categoria("9071046535567", "Beam Air Pro", ("beam air pro",), 9),
    Categoria("10105771335055", "Chiller", ("chiller",), 10),
    Categoria("360000126835", "Beam Studio", ("beam studio",), 11),
    Categoria("360000142695", "Materiales láser",
              ("materiales láser", "laser materials", "lasermaterialien", "matériaux laser"),
              12),
)


def locale_de(idioma: str | None) -> str:
    return LOCALES.get(idioma_de(idioma), LOCALES[IDIOMA_DEFECTO])


def idioma_de_locale(locale: str | None) -> str | None:
    buscado = (locale or "").strip().lower()
    for idioma, loc in LOCALES.items():
        if loc == buscado:
            return idioma
    return None


def enlace_categoria(categoria_id: str, idioma: str | None) -> str:
    return f"{BASE_HC}/hc/{locale_de(idioma)}/categories/{categoria_id}"


def _lista(raw: str | None) -> tuple[str, ...]:
    try:
        datos = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(datos, list):
        return ()
    return tuple(str(x).strip().lower() for x in datos if str(x).strip())


def _desde_fila(fila: FluxCategoria) -> Categoria:
    return Categoria(
        categoria_id=fila.categoria_id, modelo=fila.modelo, palabras=_lista(fila.palabras_json),
        posicion=int(fila.posicion or 0), activo=bool(fila.activo),
    )


def cargar_categorias(session: Session) -> list[Categoria]:
    """Todas (activas e inactivas), en orden; con la tabla vacía, las de partida."""
    try:
        filas = list(session.scalars(
            select(FluxCategoria).order_by(FluxCategoria.posicion, FluxCategoria.modelo)))
    except (OperationalError, ProgrammingError):
        return list(CATEGORIAS_DE_PARTIDA)
    if not filas:
        return list(CATEGORIAS_DE_PARTIDA)
    return [_desde_fila(f) for f in filas]


def sembrar_si_vacio(session: Session) -> int:
    if session.scalar(select(FluxCategoria.categoria_id).limit(1)) is not None:
        return 0
    for c in CATEGORIAS_DE_PARTIDA:
        session.add(FluxCategoria(
            categoria_id=c.categoria_id, modelo=c.modelo,
            palabras_json=json.dumps(list(c.palabras)), posicion=c.posicion, activo=True,
        ))
    session.flush()
    return len(CATEGORIAS_DE_PARTIDA)


def modelo_en_texto(texto: str | None, categorias: Iterable[Categoria]) -> Categoria | None:
    """La categoría cuyo modelo aparece en el texto; si aparecen varias, la de
    la palabra más larga («beambox ii» gana a «beambox»)."""
    plano = re.sub(r"\s+", " ", (texto or "").lower())
    if not plano:
        return None
    mejor: tuple[int, Categoria] | None = None
    for cat in categorias:
        if not cat.activo:
            continue
        for palabra in cat.palabras:
            if not palabra:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(palabra)}(?![a-z0-9])", plano):
                if mejor is None or len(palabra) > mejor[0]:
                    mejor = (len(palabra), cat)
    return mejor[1] if mejor else None


def enlace_kb(categoria: Categoria | None, idioma: str | None) -> Enlace:
    """El enlace a la KB de FLUX: la categoría del modelo en el idioma del
    cliente, o la portada de la KB si no se sabe el modelo."""
    locale = locale_de(idioma)
    if categoria is None:
        return Enlace(f"{BASE_HC}/hc/{locale}", ORIGEN_FLUX_KB, idioma=idioma_de_locale(locale),
                      etiqueta="FLUX Help Center")
    return Enlace(
        enlace_categoria(categoria.categoria_id, idioma), ORIGEN_FLUX_KB,
        idioma=idioma_de_locale(locale), etiqueta=categoria.modelo,
    )


def _palabras_en(texto: str, palabras: Iterable[str]) -> list[str]:
    plano = texto.lower()
    return [p for p in palabras if p and p.lower() in plano]


def intencion(texto: str | None, config: dict[str, Any]) -> str | None:
    """`kb` (cómo se hace, software, calibración, materiales), `soporte`
    (averiada, repuesto, garantía, que vaya alguien) o None (no está claro).
    Las palabras salen de la configuración (`conocimiento.flux_palabras_*`).
    Si hay señales de las dos, gana el soporte: una máquina parada es lo que
    no puede esperar."""
    plano = (texto or "").lower()
    if not plano.strip():
        return None
    soporte = _palabras_en(plano, config.get("flux_palabras_soporte") or ())
    if soporte:
        return INTENCION_SOPORTE
    if _palabras_en(plano, config.get("flux_palabras_kb") or ()):
        return INTENCION_KB
    return None


def articulos_de(
    session: Session, *, categoria_id: str | None, idioma: str | None, limite: int = 8,
) -> list[FluxArticulo]:
    """Los artículos vigentes (no idos) de la categoría en el idioma, los más
    recientes primero: títulos y enlaces para que el asistente elija."""
    locale = locale_de(idioma)
    consulta = select(FluxArticulo).where(
        FluxArticulo.locale == locale, FluxArticulo.ido_en.is_(None),
    )
    if categoria_id:
        try:
            consulta = consulta.where(FluxArticulo.category_id == int(categoria_id))
        except ValueError:
            return []
    consulta = consulta.order_by(FluxArticulo.actualizado_en.desc()).limit(limite)
    try:
        return list(session.scalars(consulta))
    except (OperationalError, ProgrammingError):
        return []


def estado_indice(session: Session) -> dict[str, Any]:
    """Cuántos artículos vigentes hay por idioma, cuántos idos y la última
    sincronización: para la pantalla."""
    try:
        por_locale = session.execute(
            select(FluxArticulo.locale, func.count()).where(FluxArticulo.ido_en.is_(None))
            .group_by(FluxArticulo.locale)
        ).all()
        idos = session.scalar(select(func.count()).select_from(FluxArticulo)
                              .where(FluxArticulo.ido_en.is_not(None))) or 0
        ultima = session.scalars(
            select(FluxSincronizacion).order_by(FluxSincronizacion.inicio.desc()).limit(1)
        ).first()
    except (OperationalError, ProgrammingError):
        return {"articulos_por_idioma": {}, "idos": 0, "ultima_sincronizacion": None}
    return {
        "articulos_por_idioma": {
            (idioma_de_locale(loc) or loc): int(n) for loc, n in por_locale
        },
        "idos": int(idos),
        "ultima_sincronizacion": sincronizacion_como_dict(ultima) if ultima else None,
    }


def sincronizacion_como_dict(fila: Any) -> dict[str, Any]:
    try:
        locales = json.loads(fila.locales_json or "{}")
    except (TypeError, ValueError):
        locales = {}
    return {
        "id": fila.id, "origen": fila.origen, "estado": fila.estado,
        "inicio": fila.inicio.isoformat() if fila.inicio else None,
        "fin": fila.fin.isoformat() if fila.fin else None,
        "articulos": fila.articulos, "nuevos": fila.nuevos, "actualizados": fila.actualizados,
        "idos": fila.idos, "locales": locales, "detalle": fila.detalle,
    }


# --- alta y edición de categorías --------------------------------------------------


def crear_categoria(
    session: Session, *, categoria_id: str, modelo: str, palabras: Iterable[str] | None = None,
    posicion: int | None = None,
) -> FluxCategoria:
    sembrar_si_vacio(session)
    cid = str(categoria_id or "").strip()
    if not cid.isdigit():
        raise ValueError("El id de la categoría de FLUX es el número de su URL.")
    if session.get(FluxCategoria, cid) is not None:
        raise ValueError("Esa categoría ya está dada de alta: edítala.")
    nombre = str(modelo or "").strip()
    if not nombre:
        raise ValueError("La categoría necesita el nombre del modelo.")
    lista = _normalizar_palabras(palabras if palabras is not None else [nombre])
    if posicion is None:
        posicion = (session.scalar(select(FluxCategoria.posicion).order_by(
            FluxCategoria.posicion.desc()).limit(1)) or 0) + 1
    fila = FluxCategoria(categoria_id=cid, modelo=nombre, palabras_json=json.dumps(lista),
                         posicion=int(posicion), activo=True)
    session.add(fila)
    session.flush()
    return fila


def _normalizar_palabras(palabras: Iterable[Any]) -> list[str]:
    vistas: list[str] = []
    for p in palabras:
        texto = re.sub(r"\s+", " ", str(p or "").strip().lower())
        if texto and texto not in vistas:
            vistas.append(texto)
    return vistas


def actualizar_categoria(
    session: Session, categoria_id: str, cambios: dict[str, Any],
) -> FluxCategoria:
    sembrar_si_vacio(session)
    fila = session.get(FluxCategoria, str(categoria_id))
    if fila is None:
        raise LookupError("Esa categoría no existe.")
    if "modelo" in cambios and cambios["modelo"] is not None:
        nombre = str(cambios["modelo"]).strip()
        if not nombre:
            raise ValueError("La categoría necesita el nombre del modelo.")
        fila.modelo = nombre
    if "palabras" in cambios and cambios["palabras"] is not None:
        fila.palabras_json = json.dumps(_normalizar_palabras(cambios["palabras"]))
    if "activo" in cambios and cambios["activo"] is not None:
        fila.activo = bool(cambios["activo"])
    if "posicion" in cambios and cambios["posicion"] is not None:
        fila.posicion = int(cambios["posicion"])
    session.flush()
    return fila
