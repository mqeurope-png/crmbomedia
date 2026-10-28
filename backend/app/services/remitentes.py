"""Los remitentes del desplegable «De» de cada usuario (`user_email_alias_prefs`).

Una sola lógica para las dos pantallas que los tocan:

  - Mi cuenta → Gmail (alias de envío): cada uno los suyos
    (`/api/emails/aliases`; el comercial lo ve en solo lectura desde
    CRM-PERFIL y su perfil lo gestiona el administrador).
  - Usuarios → ficha de un usuario → «Remitentes»: el administrador, los de
    cualquiera (`/api/users/{id}/senders`), con registro en Auditoría.

Lo que se marca desde cualquiera de las dos queda como elección
(`user_opted_in`) y el sync de Gmail (`integrations/gmail/aliases.py`) la
respeta en cada pasada. La columna se llama así porque nació para la elección
del propio usuario; una marca del administrador cuenta igual, y quién la puso
queda en Auditoría (`user.senders_updated`).

Solo se puede enviar desde una dirección dada de alta y verificada como
«Enviar como» en la cuenta de Google del equipo: Gmail rechaza las demás. El
cliente de Gmail ya devuelve solo las verificadas; la vista del administrador
añade las que el usuario tiene marcadas o registradas como entrada y Gmail no
tiene, con `en_gmail=False`, para que se vea por qué no salen en el «De».

No confundir con `user_email_aliases` («Direcciones de entrada»): el correo
que LLEGA a esas direcciones se atribuye al usuario; no le deja escribir
desde ellas.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.gmail import service as gmail_service
from app.models.crm import User, UserEmailAlias, UserEmailAliasPref
from app.schemas.emails import AliasPreferenceItem, EmailAlias
from app.services import empresas_grupo as eg

logger = logging.getLogger(__name__)


def nombre_visible(override: str | None, gmail_display: str | None) -> str:
    """PR-DisplayName-Remitente. Override > gmail > "". El override
    se considera presente solo si NO es None y NO está vacío tras
    strip — el clear desde la UI persiste None, no string vacío."""
    if override:
        cleaned = override.strip()
        if cleaned:
            return cleaned
    return (gmail_display or "").strip()


def _direcciones_de_entrada(session: Session, user_id: str) -> list[str]:
    return list(session.scalars(
        select(UserEmailAlias.alias_email).where(
            UserEmailAlias.user_id == user_id, UserEmailAlias.active.is_(True))
        .order_by(UserEmailAlias.alias_email)
    ))


def _fila(
    *, email: str, gmail: dict[str, Any] | None, pref: UserEmailAliasPref | None,
    en_gmail: bool | None, de_entrada: bool,
) -> EmailAlias:
    gmail_name = (gmail.get("display_name") or None) if gmail else (
        pref.gmail_display_name if pref else None)
    override = pref.display_name_override if pref else None
    resuelto = nombre_visible(override, gmail_name)
    return EmailAlias(
        send_as_email=email,
        display_name=(gmail.get("display_name", "") if gmail else (gmail_name or "")),
        is_primary=bool(gmail and gmail["is_primary"]),
        is_default=bool(gmail and gmail["is_default"]),
        verification_status=gmail.get("verification_status") if gmail else None,
        user_pref_allowed=pref.is_allowed if pref else False,
        user_pref_default=pref.is_default if pref else False,
        user_pref_opted_in=pref.user_opted_in if pref else None,
        gmail_display_name=gmail_name,
        display_name_override=override,
        resolved_display_name=resuelto,
        en_gmail=en_gmail,
        de_entrada=de_entrada,
        menciona_bomedia=bool(eg.menciones(resuelto)),
    )


def listar(session: Session, user: User, *, completo: bool = False) -> list[EmailAlias]:
    """Los «enviar como» de Gmail con las preferencias de `user`.

    Cada lectura refresca el `gmail_display_name` guardado con lo que Gmail
    diga hoy (PR-DisplayName-Remitente) y commitea si cambió.

    Sin `completo` (Mi cuenta) es la lista de Gmail tal cual y los errores de
    Gmail (`GmailNotConnectedError`, `GmailScopeMissingError`) suben al
    llamador. Con `completo` (la ficha de Usuarios) se añaden, detrás, las
    direcciones que el usuario tiene en sus preferencias o registradas como
    entrada y Gmail no tiene (`en_gmail=False`); si no se puede consultar
    Gmail, salen las de la base de datos con `en_gmail=None`."""
    gmail: list[dict[str, Any]] | None
    try:
        gmail = gmail_service.list_aliases(session, user.id)
    except Exception:  # noqa: BLE001 — desconectado, sin permiso o caído
        if not completo:
            raise
        logger.warning("remitentes: Gmail no disponible para user_id=%s", user.id,
                       exc_info=True)
        gmail = None
    prefs = list(session.scalars(
        select(UserEmailAliasPref).where(UserEmailAliasPref.user_id == user.id)))
    prefs_by_email = {p.alias_email: p for p in prefs}
    entrada = {a.strip().lower() for a in _direcciones_de_entrada(session, user.id)}

    # Sync gmail_display_name en las prefs existentes — el snapshot
    # se actualiza con lo que Gmail diga hoy. Si el alias no tiene
    # pref (no marcado), no creamos una row vacía solo para el cache;
    # cuando el user lo marque la prox upsert lo sincronizará.
    dirty = False
    for item in gmail or []:
        pref = prefs_by_email.get(item["send_as_email"])
        if pref is None:
            continue
        new_gmail_name = item.get("display_name") or None
        if pref.gmail_display_name != new_gmail_name:
            pref.gmail_display_name = new_gmail_name
            dirty = True
    if dirty:
        session.commit()

    out = [
        _fila(email=item["send_as_email"], gmail=item,
              pref=prefs_by_email.get(item["send_as_email"]), en_gmail=True,
              de_entrada=item["send_as_email"].strip().lower() in entrada)
        for item in gmail or []
    ]
    if not completo:
        return out
    en_gmail: bool | None = False if gmail is not None else None
    vistas = {a.send_as_email.strip().lower() for a in out}
    for pref in sorted(prefs, key=lambda p: p.alias_email.lower()):
        clave = pref.alias_email.strip().lower()
        if clave in vistas:
            continue
        vistas.add(clave)
        out.append(_fila(email=pref.alias_email, gmail=None, pref=pref,
                         en_gmail=en_gmail, de_entrada=clave in entrada))
    for direccion in _direcciones_de_entrada(session, user.id):
        clave = direccion.strip().lower()
        if clave in vistas:
            continue
        vistas.add(clave)
        out.append(_fila(email=direccion, gmail=None, pref=None, en_gmail=en_gmail,
                         de_entrada=True))
    return out


def _estado(row: UserEmailAliasPref) -> dict[str, Any]:
    return {"marcado": row.is_allowed, "predeterminado": row.is_default,
            "nombre": row.display_name_override, "eleccion": row.user_opted_in}


def guardar(
    session: Session, user: User, preferencias: Iterable[AliasPreferenceItem],
) -> list[dict[str, Any]]:
    """Guarda de una vez las preferencias de remitente de `user` y devuelve
    lo que cambió (`[{alias, antes, despues}]`, para Auditoría). No commitea.

    Lo que se marca o desmarca es una elección deliberada y queda registrada
    como tal (`user_opted_in`), para que el sync de alias
    (`gmail/aliases.py`) la respete en cada pasada en vez de pisarla:

    - `is_allowed=true`: la fila queda visible. Si estaba apagada, o se
      acaba de elegir como predeterminada, se registra `user_opted_in=true`.
      Una fila que ya estaba visible por la regla por defecto (su alias
      propio) sigue «sin pronunciarse».
    - `is_allowed=false`: la fila NO se borra. Queda apagada y, si estaba
      encendida (o se había elegido), con `user_opted_in=false`: un rechazo
      hay que recordarlo, también el del alias propio. Una fila que ya
      estaba apagada no cambia, y no se crea fila para un alias apagado que
      no tenía ninguna. (Hasta el 10/10/2026 se borraba la fila mientras el
      sync creaba centenares apagadas: las dos mitades no se ponían de
      acuerdo. Las filas apagadas existen.)
    - Marcar `is_default=true` en una fila quita el predeterminado de las
      demás del usuario.

    PR-Aliases-UX. Si llegan remitentes marcados pero NINGUNO con
    `is_default`, el primero marcado (orden de envío) pasa a serlo.
    Garantía: con N≥1 marcados, exactamente 1 predeterminado. Sin marcados,
    no hay predeterminado. Así el desplegable del compositor es determinista.
    """
    filas = list(session.scalars(
        select(UserEmailAliasPref).where(UserEmailAliasPref.user_id == user.id)))
    existing = {p.alias_email: p for p in filas}
    antes = {p.alias_email: _estado(p) for p in filas}
    incoming_default: str | None = None
    first_allowed: str | None = None
    for item in preferencias:
        row = existing.get(item.alias_email)
        if not item.is_allowed:
            if row is not None and (row.is_allowed or row.is_default or row.user_opted_in):
                # Desmarcar algo que estaba encendido (o elegido): rechazo.
                row.is_allowed = False
                row.is_default = False
                row.user_opted_in = False
            elif row is None and item.alias_email.strip().lower() == (
                (user.email or "").strip().lower()
            ):
                # El alias propio aún sin fila (antes del primer sync) y
                # desmarcado: el sync lo encendería por la regla por defecto,
                # así que el rechazo se guarda ya.
                row = UserEmailAliasPref(
                    user_id=user.id,
                    alias_email=item.alias_email,
                    is_allowed=False,
                    is_default=False,
                    user_opted_in=False,
                )
                session.add(row)
                existing[item.alias_email] = row
            continue
        if first_allowed is None:
            first_allowed = item.alias_email
        if item.is_default:
            incoming_default = item.alias_email
        # PR-DisplayName-Remitente. None = no tocar el override
        # actual; string = strip + NULL on empty (clear) o setear el
        # valor. Nunca se persiste "" como string.
        override_set = "display_name_override" in item.model_fields_set
        override_value: str | None = None
        if override_set:
            raw = (item.display_name_override or "").strip()
            override_value = raw or None
        if row is None:
            row = UserEmailAliasPref(
                user_id=user.id,
                alias_email=item.alias_email,
                is_allowed=True,
                is_default=item.is_default,
                user_opted_in=True,
            )
            if override_set:
                row.display_name_override = override_value
            session.add(row)
            existing[item.alias_email] = row
        else:
            if (
                not row.is_allowed
                or row.user_opted_in is False
                or (item.is_default and not row.is_default)
            ):
                # Encender algo apagado, o elegirlo como predeterminado: lo quiere.
                row.user_opted_in = True
            row.is_allowed = True
            row.is_default = item.is_default
            if override_set:
                row.display_name_override = override_value
    # Normaliza: si hay marcados pero ningún predeterminado declarado, el
    # primero gana; y el predeterminado es uno en todo el usuario, no solo
    # en lo que llegó (sin distinguir mayúsculas, como la base de datos).
    if incoming_default is None:
        incoming_default = first_allowed
    if incoming_default is not None:
        ganador = incoming_default.strip().lower()
        for row in existing.values():
            row.is_default = row.alias_email.strip().lower() == ganador
    session.flush()

    cambios: list[dict[str, Any]] = []
    for alias, row in sorted(existing.items(), key=lambda kv: kv[0].lower()):
        despues = _estado(row)
        previo = antes.get(alias)
        if previo != despues:
            cambios.append({"alias": alias, "antes": previo, "despues": despues})
    return cambios


def resumen_de_cambios(cambios: list[dict[str, Any]]) -> str:
    """Una línea legible para la Auditoría: «+a, -b, predeterminado c, nombre
    de d: «X»»."""
    partes: list[str] = []
    for c in cambios:
        previo = c["antes"] or {}
        despues = c["despues"]
        alias = c["alias"]
        if despues["marcado"] and not previo.get("marcado"):
            partes.append(f"+{alias}")
        elif previo.get("marcado") and not despues["marcado"]:
            partes.append(f"-{alias}")
        if despues["predeterminado"] and not previo.get("predeterminado"):
            partes.append(f"predeterminado {alias}")
        if despues["nombre"] != previo.get("nombre"):
            partes.append(f"nombre de {alias}: «{despues['nombre'] or 'el de Gmail'}»")
    return ", ".join(partes)
