"""El mapa interés × idioma → plantilla de `email_templates`.

Las 30 plantillas están en la carpeta «plantillas autoresponse», con nombres
`Lead · <contenido> (<IDIOMA>)`: cinco intereses comerciales en seis idiomas,
así que no faltan idiomas. El mapa se resuelve por nombre y se puede
sobrescribir desde la pantalla (Configuración ERP → Respuesta a leads), que
es como Bart engancha contenidos nuevos (SmartJet, textil/DTF) sin un PR.

`consumibles`, `servicio_tecnico` y `repuestos` no son leads comerciales: no
tienen plantilla de venta. Lo mismo `otro` y cualquier interés sin plantilla:
borrador vacío y aviso de «sin plantilla para este interés/idioma».
"""
from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.leads.clasificador import (
    INTERES_DISTRIBUCION,
    INTERES_LASER,
    INTERES_UV_GRANDE,
    INTERES_UV_PEQUENO,
    INTERES_VENDING,
    INTERESES_COMERCIALES,
)

CARPETA = "plantillas autoresponse"
#: Idiomas con plantilla.
IDIOMAS_CON_PLANTILLA: tuple[str, ...] = ("es", "en", "fr", "de", "nl", "pt")
NOMBRES: dict[str, str] = {
    INTERES_UV_PEQUENO: "UV pequeño-mediano",
    INTERES_UV_GRANDE: "UV gran formato",
    INTERES_LASER: "Láser y CNC",
    INTERES_VENDING: "Vending",
    INTERES_DISTRIBUCION: "Distribución",
}


def nombre_plantilla(interes: str, idioma: str) -> str | None:
    """`Lead · Vending (ES)`; `None` si el interés no tiene plantilla de venta."""
    contenido = NOMBRES.get(interes)
    if not contenido:
        return None
    return f"Lead · {contenido} ({(idioma or '').upper()})"


def clave_mapa(interes: str, idioma: str) -> str:
    return f"{interes}:{(idioma or '').lower()}"


def _plano(texto: str) -> str:
    """Sin acentos, sin espacios dobles, en minúsculas: «Lead · Láser y CNC
    (FR)» y «lead · laser y cnc (fr)» son la misma plantilla."""
    sin = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )
    return " ".join(sin.lower().split())


def buscar_por_nombre(session: Session, nombre: str) -> Any | None:
    """La plantilla con ese nombre (tolerando acentos y mayúsculas). Si hay
    varias, la más reciente."""
    from app.email_templates.models import EmailTemplate  # noqa: PLC0415

    candidatas = list(session.scalars(
        select(EmailTemplate)
        .where(EmailTemplate.name.like("%Lead%"))
        .order_by(EmailTemplate.created_at.desc())
    ))
    buscado = _plano(nombre)
    for tpl in candidatas:
        if _plano(tpl.name) == buscado:
            return tpl
    return None


def mapa_resuelto_por_nombre(session: Session) -> dict[str, str | None]:
    """Para la pantalla: `interes:idioma` → id de la plantilla que se resuelve
    por nombre (o `None` si no hay), cargando las candidatas UNA sola vez."""
    from app.email_templates.models import EmailTemplate  # noqa: PLC0415

    candidatas = list(session.scalars(
        select(EmailTemplate)
        .where(EmailTemplate.name.like("%Lead%"))
        .order_by(EmailTemplate.created_at.desc())
    ))
    por_plano: dict[str, str] = {}
    for tpl in candidatas:
        por_plano.setdefault(_plano(tpl.name), tpl.id)      # la más reciente manda
    salida: dict[str, str | None] = {}
    for interes in sorted(INTERESES_COMERCIALES):
        for idioma in IDIOMAS_CON_PLANTILLA:
            nombre = nombre_plantilla(interes, idioma)
            salida[clave_mapa(interes, idioma)] = por_plano.get(_plano(nombre)) if nombre else None
    return salida


def plantilla_para(
    session: Session, interes: str | None, idioma: str | None,
    mapa: dict[str, Any] | None = None,
) -> Any | None:
    """La plantilla de ese interés en ese idioma, o `None`.

    `mapa` (de la configuración) manda: `{"vending:es": "<template_id>"}`; un
    valor vacío significa «sin plantilla a propósito». Sin entrada en el mapa
    se busca por nombre."""
    from app.email_templates.models import EmailTemplate  # noqa: PLC0415

    interes = (interes or "").strip()
    idioma = (idioma or "").strip().lower()
    if not interes or interes not in INTERESES_COMERCIALES or not idioma:
        return None
    clave = clave_mapa(interes, idioma)
    if mapa and clave in mapa:
        template_id = str(mapa.get(clave) or "").strip()
        if not template_id:
            return None
        return session.get(EmailTemplate, template_id)
    nombre = nombre_plantilla(interes, idioma)
    return buscar_por_nombre(session, nombre) if nombre else None
