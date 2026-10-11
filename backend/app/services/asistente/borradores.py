"""Los borradores del asistente después de generarlos: cómo se enseñan, la
conversación (los turnos de una misma serie) y qué pasó con cada uno
(descartado, regenerado, enviado tal cual o editado). El envío lo hace el
compositor; aquí solo se anota el resultado."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ia import (
    ESTADO_DESCARTADO,
    ESTADO_ENVIADO_EDITADO,
    ESTADO_ENVIADO_TAL_CUAL,
    ESTADO_GENERADO,
    ESTADO_REGENERADO,
    BorradorAsistente,
)
from app.services.asistente.texto import a_html, normalizado, sin_firma
from app.services.html_sanitizer import html_to_text


def _json(raw: str | None, defecto: Any) -> Any:
    try:
        return json.loads(raw) if raw else defecto
    except (TypeError, ValueError):
        return defecto


def html_de(b: BorradorAsistente) -> str:
    """El HTML del borrador: el guardado (HTML sencillo, desde 0141) o, en los
    de antes, el derivado de su texto plano."""
    if b.html is not None:
        return b.html
    enlaces = _json(b.enlaces_json, [])
    return a_html(b.texto, [e.get("url", "") for e in enlaces if isinstance(e, dict)])


def como_dict(b: BorradorAsistente) -> dict[str, Any]:
    enlaces = _json(b.enlaces_json, [])
    contexto = _json(b.contexto_json, {})
    if not isinstance(contexto, dict):
        contexto = {}
    seleccion = contexto.get("seleccion")
    if not isinstance(seleccion, dict):
        seleccion = None
    return {
        "id": b.id, "thread_id": b.thread_id, "message_id": b.message_id,
        "contact_id": b.contact_id, "usuario_id": b.usuario_id, "idioma": b.idioma,
        "modelo": b.modelo, "tokens_entrada": b.tokens_entrada, "tokens_salida": b.tokens_salida,
        "tokens_cache_lectura": b.tokens_cache_lectura or 0,
        "tokens_cache_escritura": b.tokens_cache_escritura or 0,
        "coste_usd": round(float(b.coste_usd or 0), 6),
        "destinatario": b.destinatario, "remitente": b.remitente, "asunto": b.asunto,
        "destinatario_interno": bool(contexto.get("destinatario_interno")),
        "sin_hilo": b.thread_id is None,
        "texto": b.texto, "html": html_de(b),
        # La corrección partió de lo que había en el editor (el compositor lo
        # pone sin preguntar) y, si fue sobre una selección, el fragmento
        # reescrito y si pudo empalmarse en el texto (si no, `html` ES el
        # fragmento y el compositor lo sustituye en el editor).
        "sobre_editor": bool(contexto.get("sobre_editor")),
        "seleccion": seleccion,
        "fragmento": bool(seleccion) and not seleccion.get("empalmado", True),
        "aviso": b.aviso, "instruccion": b.instruccion,
        "conversacion_id": b.conversacion_id or b.id, "turno": b.turno or 0,
        "padre_id": b.padre_id,
        "pendiente": _json(b.pendiente_json, []), "enlaces": enlaces,
        "contexto": contexto, "alcance": b.alcance,
        "mensajes_hilo": b.mensajes_hilo, "mensajes_historico": b.mensajes_historico,
        "estado": b.estado, "email_message_id": b.email_message_id,
        "creado": b.created_at.isoformat() if b.created_at else None,
        "resuelto_en": b.resuelto_en.isoformat() if b.resuelto_en else None,
    }


def turnos_de(session: Session, borrador: BorradorAsistente) -> list[BorradorAsistente]:
    """Todos los turnos de la conversación del borrador, el primero antes."""
    clave = borrador.conversacion_id or borrador.id
    return list(session.scalars(
        select(BorradorAsistente).where(BorradorAsistente.conversacion_id == clave)
        .order_by(BorradorAsistente.turno, BorradorAsistente.created_at)
    )) or [borrador]


def marcar_descartado(session: Session, borrador: BorradorAsistente) -> BorradorAsistente:
    """Descartar es descartar la conversación: todos sus turnos sin resolver."""
    ahora = datetime.now(UTC)
    for turno in turnos_de(session, borrador):
        if turno.estado == ESTADO_GENERADO:
            turno.estado = ESTADO_DESCARTADO
            turno.resuelto_en = ahora
    session.flush()
    return borrador


def resultado_del_envio(borrador: BorradorAsistente, *, body_html: str | None,
                        body_text: str | None) -> str:
    """`enviado_tal_cual` si lo que se envió es el borrador (sin contar la
    firma que añade el compositor); si no, `enviado_editado`. Un borrador en
    HTML se compara pasando los dos lados por el mismo conversor a texto; un
    turno que solo reescribió una selección cuenta como tal cual si el
    fragmento está en lo enviado."""
    enviado = html_to_text(sin_firma(body_html)) if body_html else (body_text or "")
    contexto = _json(borrador.contexto_json, {})
    seleccion = contexto.get("seleccion") if isinstance(contexto, dict) else None
    if isinstance(seleccion, dict) and not seleccion.get("empalmado", True):
        fragmento = normalizado(html_to_text(str(seleccion.get("fragmento_html") or "")))
        return (
            ESTADO_ENVIADO_TAL_CUAL if fragmento and fragmento in normalizado(enviado)
            else ESTADO_ENVIADO_EDITADO
        )
    esperado = html_to_text(borrador.html) if borrador.html else borrador.texto
    return (
        ESTADO_ENVIADO_TAL_CUAL if normalizado(enviado) == normalizado(esperado)
        else ESTADO_ENVIADO_EDITADO
    )


def marcar_enviado(
    session: Session, borrador_id: str | None, *, usuario_id: str, email_message_id: str | None,
    body_html: str | None, body_text: str | None,
) -> BorradorAsistente | None:
    """Anota qué pasó con el borrador cuando la persona envió desde el
    compositor. Solo quien lo pidió puede resolverlo; un id que no existe se
    ignora: el envío nunca falla por esto. Puede enviarse un turno anterior
    (la persona volvió atrás en la conversación): entonces los turnos
    posteriores sin resolver quedan como regenerados."""
    if not borrador_id:
        return None
    borrador = session.get(BorradorAsistente, borrador_id)
    if borrador is None or borrador.usuario_id != usuario_id:
        return None
    if borrador.estado not in (ESTADO_GENERADO, ESTADO_REGENERADO):
        return borrador
    ahora = datetime.now(UTC)
    borrador.estado = resultado_del_envio(borrador, body_html=body_html, body_text=body_text)
    borrador.email_message_id = email_message_id
    borrador.resuelto_en = ahora
    for turno in turnos_de(session, borrador):
        if turno.id != borrador.id and turno.estado == ESTADO_GENERADO:
            turno.estado = ESTADO_REGENERADO
            turno.resuelto_en = ahora
    session.flush()
    return borrador
