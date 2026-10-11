"""Las máquinas: el JSON del compositor de correos de Bomedia (bomedia-v4),
leído de su fila de Supabase. BoHub solo LEE; el compositor es una aplicación
aparte y aquí no se escribe nada.

    GET {SUPABASE_COMPOSER_URL}/rest/v1/composer_data?select=data,updated_at&id=eq.main
    apikey: <la clave publicable del compositor>   (SUPABASE_COMPOSER_KEY, de .env)

`data.products[]`: `id, brand, name, badge, img, desc, area, alt, feat1, feat2,
price, link, visible` y `i18n: {fr: {desc, feat1, feat2, price, link}, de, en,
nl}`. Los campos raíz son el castellano. `price` es TEXTO («9.300 €»,
«9 300 €») y se guarda tal cual; el número derivado es solo para ordenar.
`data.brands[]`: logo, color y URL por idioma de cada marca.

Primero se descarga todo y después se escribe: si Supabase no responde (o no
hay clave), la pasada queda en error y el catálogo anterior sigue intacto. Se
guarda el `updated_at` de la fila como fecha de actualización del origen. Lo
que desaparece del compositor se marca `ido_en`, no se borra.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conocimiento import (
    FUENTE_COMPOSITOR,
    SYNC_ERROR,
    SYNC_PROGRAMADA,
    TIPO_MAQUINA,
    CatalogoSincronizacion,
    MarcaCatalogo,
)
from app.services.conocimiento import catalogo
from app.services.conocimiento.rutas import idioma_de
from app.services.web_forms import sitios as registro_sitios

logger = logging.getLogger(__name__)

#: El proyecto de Supabase del compositor (no es un secreto: la URL pública).
#: La clave publicable NO va en el código: `SUPABASE_COMPOSER_KEY` en `.env`.
SUPABASE_URL_DEFECTO = "https://midvgxxndddasxlnstkg.supabase.co"
RUTA_FILA = "/rest/v1/composer_data?select=data,updated_at&id=eq.main"
TIMEOUT_SEGUNDOS = 20.0
SIN_CLAVE = ("Sin clave del compositor: falta SUPABASE_COMPOSER_KEY (la clave publicable que usa "
             "el propio compositor) en .env.production.")


class CompositorError(RuntimeError):
    """Supabase no ha respondido bien (red, 4xx/5xx, JSON ilegible, sin fila)."""


def _cliente() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_SEGUNDOS), follow_redirects=True,
        headers={"User-Agent": "BoHub/kb-catalogo", "Accept": "application/json"},
    )


def _fecha(valor: Any) -> datetime | None:
    if not valor:
        return None
    try:
        fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def descargar(http: httpx.Client, *, url_base: str, clave: str) -> tuple[dict[str, Any],
                                                                        datetime | None]:
    """`(data, updated_at)` de la fila `main`. Toda la red pasa por aquí."""
    url = f"{url_base.rstrip('/')}{RUTA_FILA}"
    cabeceras = {"apikey": clave, "Authorization": f"Bearer {clave}"}
    try:
        respuesta = http.get(url, headers=cabeceras)
    except httpx.HTTPError as exc:
        raise CompositorError(f"Supabase: {type(exc).__name__}: {exc}") from exc
    if respuesta.status_code != 200:
        raise CompositorError(f"Supabase: HTTP {respuesta.status_code}")
    try:
        filas = respuesta.json()
    except ValueError as exc:
        raise CompositorError("Supabase: respuesta ilegible") from exc
    if not isinstance(filas, list) or not filas or not isinstance(filas[0], dict):
        raise CompositorError("Supabase: la fila «main» de composer_data no está")
    fila = filas[0]
    data = fila.get("data") if isinstance(fila.get("data"), dict) else fila
    if not isinstance(data, dict):
        raise CompositorError("Supabase: la fila no trae «data»")
    return data, _fecha(fila.get("updated_at"))


def _texto(valor: Any) -> str | None:
    texto = str(valor or "").strip()
    return texto or None


def _campos(p: dict[str, Any], raiz: bool) -> dict[str, Any]:
    return {
        "desc": _texto(p.get("desc")), "feat1": _texto(p.get("feat1")),
        "feat2": _texto(p.get("feat2")), "price": _texto(p.get("price")),
        "link": _texto(p.get("link")),
        **({"area": _texto(p.get("area")), "alt": _texto(p.get("alt"))} if raiz else {}),
    }


def filas_de(data: dict[str, Any], *, actualizado_origen: datetime | None) -> list[dict[str, Any]]:
    """Una fila por producto × idioma a partir del JSON del compositor."""
    productos = data.get("products")
    if not isinstance(productos, list):
        return []
    reg = registro_sitios.registro()
    salida: list[dict[str, Any]] = []
    for p in productos:
        if not isinstance(p, dict) or not _texto(p.get("id")) or not _texto(p.get("name")):
            continue
        por_idioma: dict[str, dict[str, Any]] = {catalogo.IDIOMA_RAIZ: _campos(p, raiz=True)}
        i18n = p.get("i18n")
        if isinstance(i18n, dict):
            for lang, campos in i18n.items():
                codigo = idioma_de(str(lang))
                if codigo and isinstance(campos, dict) and codigo not in por_idioma:
                    por_idioma[codigo] = _campos(campos, raiz=False)
        area = _texto(p.get("area"))
        for idioma, campos in por_idioma.items():
            enlace = campos.get("link")
            sitio = reg.por_dominio(enlace.split("/")[2]) if enlace and "//" in enlace else None
            precio = campos.get("price")
            salida.append({
                "origen_id": str(p["id"]).strip(), "idioma": idioma, "tipo": TIPO_MAQUINA,
                "marca": str(p.get("brand") or "").strip(), "nombre": str(p["name"]).strip()[:255],
                "descripcion": campos.get("desc"),
                "caracteristicas_json": json.dumps(
                    [c for c in (campos.get("feat1"), campos.get("feat2")) if c],
                    ensure_ascii=False),
                "categoria": area, "imagen": _texto(p.get("img")),
                "precio_texto": precio[:80] if precio else None,
                "precio_num": catalogo.numero_de(precio),
                "moneda": catalogo.moneda_de(precio),
                "enlace": enlace[:500] if enlace else None,
                "sitio": sitio.clave if sitio is not None else "",
                "actualizado_origen": actualizado_origen,
                "visible": p.get("visible") is not False,
                "datos_json": json.dumps({
                    "badge": _texto(p.get("badge")), "alt": _texto(p.get("alt")),
                }, ensure_ascii=False),
            })
    return salida


def escribir_marcas(session: Session, data: dict[str, Any], *, ahora: datetime) -> int:
    """`data.brands[]` → `kb_catalogo_marcas`; el idioma por defecto editado
    desde la pantalla se conserva."""
    marcas = data.get("brands")
    if not isinstance(marcas, list):
        return 0
    existentes = {m.nombre: m for m in session.scalars(select(MarcaCatalogo))}
    vistas: set[str] = set()
    for b in marcas:
        if not isinstance(b, dict):
            continue
        nombre = _texto(b.get("name") or b.get("id") or b.get("brand"))
        if not nombre or nombre in vistas:
            continue
        vistas.add(nombre)
        urls: dict[str, str] = {}
        for clave in ("urls", "url", "links", "link", "site", "sites"):
            valor = b.get(clave)
            if isinstance(valor, dict):
                urls.update({idioma_de(str(k)) or str(k): str(v) for k, v in valor.items() if v})
            elif isinstance(valor, str) and valor.strip():
                urls.setdefault(catalogo.IDIOMA_RAIZ, valor.strip())
        fila = existentes.get(nombre)
        if fila is None:
            fila = MarcaCatalogo(nombre=nombre[:80], idioma_defecto=idioma_de(
                str(b.get("idioma_defecto") or b.get("default_lang") or b.get("lang") or ""))
                or catalogo.IDIOMA_RAIZ)
            session.add(fila)
        fila.logo = _texto(b.get("logo"))
        fila.color = _texto(b.get("color"))
        fila.urls_json = json.dumps(urls, ensure_ascii=False)
        fila.visto_en = ahora
        fila.ido_en = None
    for nombre, fila in existentes.items():
        if nombre not in vistas and fila.ido_en is None:
            fila.ido_en = ahora
    session.flush()
    return len(vistas)


def sincronizar(
    session: Session, *, origen: str = SYNC_PROGRAMADA, http: httpx.Client | None = None,
    ahora: datetime | None = None, url_base: str | None = None, clave: str | None = None,
) -> CatalogoSincronizacion:
    """Una pasada completa. Devuelve la fila del histórico, ya en la sesión (el
    que llama hace commit). Sin clave o con Supabase caído: estado `error`,
    motivo en `detalle`, catálogo anterior intacto."""
    from app.core.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    ahora = ahora or datetime.now(UTC)
    url_base = url_base or settings.supabase_composer_url or SUPABASE_URL_DEFECTO
    clave = clave or settings.supabase_composer_key or ""
    pasada = catalogo.abrir_pasada(session, fuente=FUENTE_COMPOSITOR, origen=origen, ahora=ahora)
    if not clave:
        pasada.estado = SYNC_ERROR
        pasada.detalle = SIN_CLAVE
        pasada.fin = datetime.now(UTC)
        session.flush()
        logger.warning("kb.catalogo: compositor sin sincronizar: %s", SIN_CLAVE)
        return pasada
    propio = http is None
    cliente = http or _cliente()
    try:
        data, actualizado = descargar(cliente, url_base=url_base, clave=clave)
    except CompositorError as exc:
        pasada.estado = SYNC_ERROR
        pasada.detalle = str(exc)[:1000]
        pasada.fin = datetime.now(UTC)
        session.flush()
        logger.warning("kb.catalogo: compositor sin sincronizar, catálogo anterior intacto: %s",
                       exc)
        return pasada
    finally:
        if propio:
            cliente.close()
    filas = filas_de(data, actualizado_origen=actualizado)
    resultado = catalogo.escribir_filas(session, filas, fuente=FUENTE_COMPOSITOR, ahora=ahora)
    marcas = escribir_marcas(session, data, ahora=ahora)
    por_idioma: dict[str, int] = {}
    for f in filas:
        por_idioma[f["idioma"]] = por_idioma.get(f["idioma"], 0) + 1
    resumen = {"compositor": {**resultado, "marcas": marcas, "por_idioma": por_idioma,
                              "con_precio": sum(1 for f in filas if f["precio_texto"])}}
    pasada.origen_actualizado_en = actualizado
    catalogo.cerrar_pasada(pasada, resumen, [], total=1)
    session.flush()
    logger.info(
        "kb.catalogo: compositor %s — %d filas (%d productos), %d nuevas, %d actualizadas, "
        "%d idas, %d marcas",
        pasada.estado, pasada.productos, len({f["origen_id"] for f in filas}), pasada.nuevos,
        pasada.actualizados, pasada.idos, marcas,
    )
    return pasada
