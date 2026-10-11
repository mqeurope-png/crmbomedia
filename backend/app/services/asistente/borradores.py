"""Los borradores del asistente después de generarlos: cómo se enseñan, y
qué pasó con cada uno (descartado, regenerado, enviado tal cual o editado).
El envío lo hace el compositor; aquí solo se anota el resultado."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.ia import (
    ESTADO_DESCARTADO,
    ESTADO_ENVIADO_EDITADO,
    ESTADO_ENVIADO_TAL_CUAL,
    ESTADO_GENERADO,
    BorradorAsistente,
)
from app.services.asistente.texto import a_html, normalizado, sin_firma
from app.services.html_sanitizer import html_to_text


def _json(raw: str | None, defecto: Any) -> Any:
    try:
        return json.loads(raw) if raw else defecto
    except (TypeError, ValueError):
        return defecto


def como_dict(b: BorradorAsistente) -> dict[str, Any]:
    enlaces = _json(b.enlaces_json, [])
    return {
        "id": b.id, "thread_id": b.thread_id, "message_id": b.message_id,
        "contact_id": b.contact_id, "usuario_id": b.usuario_id, "idioma": b.idioma,
        "modelo": b.modelo, "tokens_entrada": b.tokens_entrada, "tokens_salida": b.tokens_salida,
        "coste_usd": round(float(b.coste_usd or 0), 6),
        "destinatario": b.destinatario, "remitente": b.remitente, "asunto": b.asunto,
        "texto": b.texto, "html": a_html(b.texto, [e.get("url", "") for e in enlaces]),
        "pendiente": _json(b.pendiente_json, []), "enlaces": enlaces,
        "contexto": _json(b.contexto_json, {}), "alcance": b.alcance,
        "mensajes_hilo": b.mensajes_hilo, "mensajes_historico": b.mensajes_historico,
        "estado": b.estado, "email_message_id": b.email_message_id,
        "creado": b.created_at.isoformat() if b.created_at else None,
        "resuelto_en": b.resuelto_en.isoformat() if b.resuelto_en else None,
    }


def marcar_descartado(session: Session, borrador: BorradorAsistente) -> BorradorAsistente:
    if borrador.estado == ESTADO_GENERADO:
        borrador.estado = ESTADO_DESCARTADO
        borrador.resuelto_en = datetime.now(UTC)
        session.flush()
    return borrador


def resultado_del_envio(borrador: BorradorAsistente, *, body_html: str | None,
                        body_text: str | None) -> str:
    """`enviado_tal_cual` si lo que se envió es el borrador (sin contar la
    firma que añade el compositor); si no, `enviado_editado`."""
    enviado = html_to_text(sin_firma(body_html)) if body_html else (body_text or "")
    return (
        ESTADO_ENVIADO_TAL_CUAL if normalizado(enviado) == normalizado(borrador.texto)
        else ESTADO_ENVIADO_EDITADO
    )


def marcar_enviado(
    session: Session, borrador_id: str | None, *, usuario_id: str, email_message_id: str | None,
    body_html: str | None, body_text: str | None,
) -> BorradorAsistente | None:
    """Anota qué pasó con el borrador cuando la persona envió desde el
    compositor. Solo quien lo pidió puede resolverlo; un id que no existe se
    ignora: el envío nunca falla por esto."""
    if not borrador_id:
        return None
    borrador = session.get(BorradorAsistente, borrador_id)
    if borrador is None or borrador.usuario_id != usuario_id:
        return None
    if borrador.estado not in (ESTADO_GENERADO,):
        return borrador
    borrador.estado = resultado_del_envio(borrador, body_html=body_html, body_text=body_text)
    borrador.email_message_id = email_message_id
    borrador.resuelto_en = datetime.now(UTC)
    session.flush()
    return borrador
