"""Los datos de un correo compuesto, leídos de la fila viva del catálogo.

Lo único que va a la base de datos: los productos que nombra la estructura
(en el idioma del correo; los del catálogo de `kb_catalogo` y las fichas de la
web de su página del índice, `webs.fichas`), las marcas (logo, color, web por idioma), los
avisos de IVA, VIES y «no es una factura» con la declaración de IVA de cada
fuente, los vídeos (de la tabla de vídeos, y si no de su URL) y las imágenes
del hero y de las muestras (del índice de imágenes de las webs). Con eso
`generador.generar` monta el correo sin tocar nada más.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services.conocimiento import catalogo
from app.services.conocimiento import imagenes as imagenes_kb
from app.services.correo_compuesto import bloques as bq
from app.services.correo_compuesto import videos as videos_mod
from app.services.correo_compuesto.generador import Datos, clave_marca


def _urls(raw: str | None) -> dict[str, str]:
    try:
        datos = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        datos = {}
    return {str(k): str(v) for k, v in datos.items() if v} if isinstance(datos, dict) else {}


def _diseno(raw: str | None) -> dict[str, Any]:
    """Los datos de diseño de la marca que guarda la sincronización (0143)."""
    try:
        datos = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        datos = {}
    return datos if isinstance(datos, dict) else {}


def marcas(session: Session) -> dict[str, dict[str, Any]]:
    """Las marcas del catálogo por su clave: nombre, logo, color y webs."""
    return {
        clave_marca(m.nombre): {"nombre": m.nombre, "logo": m.logo, "color": m.color,
                                "urls": _urls(m.urls_json), **_diseno(m.datos_json)}
        for m in catalogo.marcas(session) if m.ido_en is None
    }


def resolver(session: Session, estructura: dict[str, Any], *, ahora: datetime | None = None,
             config: dict[str, Any] | None = None) -> Datos:
    """Los datos que necesita el generador para `estructura` (ya normalizada)."""
    from app.services.conocimiento.config import configuracion  # noqa: PLC0415

    cfg = config or configuracion(session)
    from app.services.webs import fichas as fichas_web  # noqa: PLC0415

    idioma = estructura.get("idioma") or bq.IDIOMA_RAIZ
    claves = bq.productos_de(estructura)
    productos = catalogo.productos_por_clave(
        session, [c for c in claves if c["fuente"] != bq.FUENTE_WEB], idioma=idioma,
        ahora=ahora, config=cfg)
    productos.update(fichas_web.fichas_por_clave(session, claves, idioma=idioma))
    return Datos(
        productos=productos, marcas=marcas(session), avisos=catalogo.avisos_en(idioma),
        iva_por_fuente=dict(cfg.get("iva_por_fuente") or {}),
        videos=videos_mod.resolver(session, bq.videos_de(estructura), idioma=idioma),
        imagenes=imagenes_kb.por_ids(session, bq.imagenes_de(estructura)),
    )
