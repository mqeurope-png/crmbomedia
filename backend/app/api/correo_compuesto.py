"""Correos compuestos: el HTML de una estructura de bloques.

`POST /api/emails/compuesto/render` recibe la estructura (de la IA, de la
vista previa con los textos retocados o de un borrador guardado), la deja
limpia y devuelve el correo entero (documento HTML autocontenido), su texto
plano y la estructura normalizada. Con `preview` el HTML lleva los atributos
que la vista previa usa para editar; sin él es exactamente lo que se envía o
se copia. No envía nada.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.db.session import get_session
from app.models.crm import User
from app.services.correo_compuesto import bloques as bq
from app.services.correo_compuesto import datos as datos_mod
from app.services.correo_compuesto import generador
from app.services.html_sanitizer import sanitize_email_html

router = APIRouter(prefix="/api/emails/compuesto", tags=["correo-compuesto"])

#: Una estructura normal son unos pocos KB; esto es un tope contra un pegado
#: monstruoso.
MAX_ESTRUCTURA = 200_000


class RenderIn(BaseModel):
    estructura: dict[str, Any]
    asunto: str | None = Field(default=None, max_length=500)
    firma_html: str | None = Field(default=None, max_length=200_000)
    preview: bool = False


def render_estructura(session: Session, estructura: dict[str, Any], *, asunto: str | None = None,
                      firma_html: str | None = None, preview: bool = False) -> dict[str, Any]:
    """La estructura limpia, su HTML y su texto plano, con lo que se quitó."""
    avisos: list[str] = []
    limpia = bq.normalizar(estructura, avisos)
    # La firma es HTML de la persona: pasa por el saneado de siempre (lo que
    # se ve, se copia y se envía es lo mismo).
    correo = generador.generar(limpia, datos_mod.resolver(session, limpia), asunto=asunto,
                               preview=preview, firma_html=sanitize_email_html(firma_html))
    return {"estructura": limpia, "html": correo.html, "texto": correo.texto,
            "avisos": avisos + correo.avisos, "precios": correo.precios}


@router.post("/render")
def render(
    payload: RenderIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    _ = current_user
    if len(json.dumps(payload.estructura, ensure_ascii=False)) > MAX_ESTRUCTURA:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            "La estructura del correo es demasiado grande.")
    return render_estructura(session, payload.estructura, asunto=payload.asunto,
                             firma_html=payload.firma_html, preview=payload.preview)
