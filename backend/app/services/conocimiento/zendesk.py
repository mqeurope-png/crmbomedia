"""El índice de la KB de FLUX: sincronización con la API pública del Help
Center de Zendesk (`/api/v2/help_center/{locale}/articles.json`).

- Se indexan TÍTULO y ENLACE (más categoría, etiquetas, fecha, desfasado,
  idioma de origen). Nunca el cuerpo.
- Una pasada completa por locale, paginando `next_page`: son unos cientos de
  artículos por idioma, cabe entera.
- La caída de FLUX no rompe nada: primero se descarga todo el locale y solo
  después se escribe, así que el locale que falla deja una línea en el
  histórico y el índice anterior intacto. Los artículos que ya no están en
  FLUX se marcan `ido_en`, no se borran.
- Idempotente: una segunda pasada igual no crea filas.

Es solo lectura de una API pública, sin credenciales.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conocimiento import (
    SYNC_ERROR,
    SYNC_OK,
    SYNC_PARCIAL,
    SYNC_PROGRAMADA,
    FluxArticulo,
    FluxSincronizacion,
)
from app.services.conocimiento.flux import API_HC, LOCALES

logger = logging.getLogger(__name__)

POR_PAGINA = 100
#: Tope de páginas por recurso y locale: una KB de 100 páginas de 100
#: artículos no es la de FLUX; si se llega, se corta y se avisa.
MAX_PAGINAS = 100
TIMEOUT_SEGUNDOS = 20.0
#: Locales que se descargan a la vez.
EN_PARALELO = 3


class ZendeskError(RuntimeError):
    """FLUX no ha respondido bien (red, 5xx, JSON ilegible)."""


def _cliente() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_SEGUNDOS), follow_redirects=True,
        headers={"User-Agent": "BoHub/kb-flux", "Accept": "application/json"},
    )


def listar(http: httpx.Client, locale: str, recurso: str) -> list[dict[str, Any]]:
    """Todas las páginas de `articles` o `sections` del locale."""
    url: str | None = f"{API_HC}/{locale}/{recurso}.json?per_page={POR_PAGINA}"
    salida: list[dict[str, Any]] = []
    paginas = 0
    while url and paginas < MAX_PAGINAS:
        try:
            respuesta = http.get(url)
        except httpx.HTTPError as exc:
            raise ZendeskError(f"{recurso} {locale}: {type(exc).__name__}: {exc}") from exc
        if respuesta.status_code != 200:
            raise ZendeskError(f"{recurso} {locale}: HTTP {respuesta.status_code}")
        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise ZendeskError(f"{recurso} {locale}: respuesta ilegible") from exc
        elementos = datos.get(recurso) if isinstance(datos, dict) else None
        if not isinstance(elementos, list):
            raise ZendeskError(f"{recurso} {locale}: sin lista «{recurso}» en la respuesta")
        salida.extend(e for e in elementos if isinstance(e, dict))
        url = (datos.get("next_page") or None) if isinstance(datos, dict) else None
        paginas += 1
    if url:
        logger.warning("kb.flux: %s %s cortado en %d páginas", recurso, locale, MAX_PAGINAS)
    return salida


def _fecha(valor: Any) -> datetime | None:
    if not valor:
        return None
    try:
        fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def _entero(valor: Any) -> int | None:
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _aplicar(fila: FluxArticulo, art: dict[str, Any], *, categoria: int | None) -> bool:
    """Copia lo que indexamos del artículo. True si algo cambió."""
    antes = (
        fila.titulo, fila.url, fila.section_id, fila.category_id, fila.etiquetas_json,
        fila.actualizado_en, fila.outdated, fila.outdated_locales_json, fila.source_locale,
    )
    fila.titulo = str(art.get("title") or "").strip()[:500] or "(sin título)"
    fila.url = str(art.get("html_url") or "").strip()[:500]
    fila.section_id = _entero(art.get("section_id"))
    fila.category_id = categoria
    etiquetas = art.get("label_names")
    fila.etiquetas_json = json.dumps(
        [str(e) for e in etiquetas] if isinstance(etiquetas, list) else [], ensure_ascii=False)
    fila.actualizado_en = _fecha(art.get("updated_at") or art.get("edited_at"))
    fila.outdated = bool(art.get("outdated"))
    desfasados = art.get("outdated_locales")
    fila.outdated_locales_json = json.dumps(
        [str(x) for x in desfasados] if isinstance(desfasados, list) else [])
    fila.source_locale = str(art.get("source_locale") or "")[:10] or None
    despues = (
        fila.titulo, fila.url, fila.section_id, fila.category_id, fila.etiquetas_json,
        fila.actualizado_en, fila.outdated, fila.outdated_locales_json, fila.source_locale,
    )
    return antes != despues


def descargar_locale(http: httpx.Client, locale: str) -> tuple[dict[int | None, int | None],
                                                               list[dict[str, Any]]]:
    """Secciones (sección → categoría) y artículos del locale. Toda la red
    pasa por aquí: si falla, no se ha escrito nada."""
    secciones = {
        _entero(s.get("id")): _entero(s.get("category_id"))
        for s in listar(http, locale, "sections")
    }
    return secciones, listar(http, locale, "articles")


def escribir_locale(
    session: Session, *, idioma: str, locale: str, secciones: dict[int | None, int | None],
    articulos: list[dict[str, Any]], ahora: datetime,
) -> dict[str, Any]:
    """Vuelca lo descargado: altas, cambios y los que ya no están (`ido_en`)."""
    existentes = {
        fila.id: fila for fila in session.scalars(
            select(FluxArticulo).where(FluxArticulo.locale == locale))
    }
    vistos: set[str] = set()
    nuevos = actualizados = 0
    for art in articulos:
        zid = _entero(art.get("id"))
        if zid is None or art.get("draft"):
            continue
        clave = f"{zid}:{locale}"
        if clave in vistos:
            continue
        vistos.add(clave)
        categoria = secciones.get(_entero(art.get("section_id")))
        fila = existentes.get(clave)
        if fila is None:
            fila = FluxArticulo(id=clave, zendesk_id=zid, locale=locale, idioma=idioma,
                                titulo="", url="", visto_en=ahora)
            _aplicar(fila, art, categoria=categoria)
            session.add(fila)
            nuevos += 1
        else:
            if _aplicar(fila, art, categoria=categoria) or fila.ido_en is not None:
                actualizados += 1
            fila.ido_en = None
        fila.visto_en = ahora
    idos = 0
    for clave, fila in existentes.items():
        if clave not in vistos and fila.ido_en is None:
            fila.ido_en = ahora
            idos += 1
    session.flush()
    return {"articulos": len(vistos), "nuevos": nuevos, "actualizados": actualizados,
            "idos": idos, "error": None}


def sincronizar(
    session: Session, *, locales: Iterable[str] | None = None, origen: str = SYNC_PROGRAMADA,
    http: httpx.Client | None = None, ahora: datetime | None = None,
) -> FluxSincronizacion:
    """Una pasada completa (todos los locales o los dados). Devuelve la fila
    del histórico, ya en la sesión (el que llama hace commit)."""
    ahora = ahora or datetime.now(UTC)
    pedidos = set(locales) if locales is not None else None
    objetivo = {k: v for k, v in LOCALES.items() if pedidos is None or k in pedidos}
    pasada = FluxSincronizacion(origen=origen, inicio=ahora, estado=SYNC_OK)
    session.add(pasada)
    resumen: dict[str, Any] = {}
    errores: list[str] = []
    propio = http is None
    cliente = http or _cliente()
    try:
        # La red, en paralelo por locale (el cliente de httpx admite hilos);
        # la base, después y en orden, desde este hilo.
        def _bajar(locale: str) -> Any:
            try:
                return descargar_locale(cliente, locale)
            except Exception as exc:  # noqa: BLE001 — un locale caído no tira los demás
                return exc

        with ThreadPoolExecutor(max_workers=EN_PARALELO) as pool:
            descargas = dict(zip(objetivo.values(), pool.map(_bajar, objetivo.values()),
                                 strict=True))
        for idioma, locale in objetivo.items():
            bajada = descargas[locale]
            if isinstance(bajada, Exception):
                logger.warning("kb.flux: %s sin sincronizar, índice anterior intacto: %s",
                               locale, bajada)
                resumen[locale] = {"articulos": 0, "nuevos": 0, "actualizados": 0, "idos": 0,
                                   "error": str(bajada)[:300]}
                errores.append(f"{locale}: {str(bajada)[:200]}")
                continue
            secciones, articulos = bajada
            resumen[locale] = escribir_locale(
                session, idioma=idioma, locale=locale, secciones=secciones,
                articulos=articulos, ahora=ahora)
    finally:
        if propio:
            cliente.close()
    pasada.articulos = sum(int(r["articulos"]) for r in resumen.values())
    pasada.nuevos = sum(int(r["nuevos"]) for r in resumen.values())
    pasada.actualizados = sum(int(r["actualizados"]) for r in resumen.values())
    pasada.idos = sum(int(r["idos"]) for r in resumen.values())
    pasada.locales_json = json.dumps(resumen, ensure_ascii=False)
    if errores and len(errores) == len(objetivo):
        pasada.estado = SYNC_ERROR
    elif errores:
        pasada.estado = SYNC_PARCIAL
    else:
        pasada.estado = SYNC_OK
    pasada.detalle = "\n".join(errores) or None
    pasada.fin = datetime.now(UTC)
    session.flush()
    logger.info(
        "kb.flux: sincronización %s — %d artículos, %d nuevos, %d actualizados, %d idos%s",
        pasada.estado, pasada.articulos, pasada.nuevos, pasada.actualizados, pasada.idos,
        f", {len(errores)} locales con error" if errores else "",
    )
    return pasada
