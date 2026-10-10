"""Sincronización de los «enviar como» de Gmail con `user_email_alias_prefs`.

La cuenta Google es una sola para toda la empresa: Gmail devuelve los 50 y
pico «enviar como» a cada usuario. Para que Norma no vea los remitentes de
Bart, el sync impone en cada pasada un valor por defecto:

  - alias **propio** (coincide con `users.email`) → visible;
  - alias **ajeno** → oculto, salvo que fuera ya su predeterminado usable.

Pero **la elección del usuario manda** (`user_opted_in`, 10/10/2026): lo que
marcó o desmarcó en sus ajustes sobrevive a la pasada, sea el alias propio o
ajeno. Hasta entonces el sync solo podía mirar `is_default`, que es uno por
definición: las secundarias que Bart elegía a mano se apagaban solas en cada
pasada y su alias propio no se podía apagar.

Otros campos:
  - `gmail_display_name` se refresca siempre desde Gmail.
  - `is_default`: si el usuario no tiene predeterminado, se siembra el de Gmail
    SOLO si ese alias le queda visible (sembrarlo sobre uno oculto creaba el
    «predeterminado imposible»: `is_default=1` con `is_allowed=0`). Al final
    de cada pasada el predeterminado es uno y está visible.
  - `display_name_override` NUNCA se toca (preferencia del usuario).
  - Alias que ya no existe en Gmail: la fila se conserva apagada (no se borra).

Tres cosas distintas que aquí no se mezclan: que el usuario esté activo (puede
entrar en BoHub), que una dirección sea nuestra (`user_email_aliases`: su correo
se captura y se enlaza al contacto) y que el usuario pueda enviar como ella
(esta tabla). Desactivar al usuario no toca ninguna de las otras dos.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import User, UserEmailAliasPref

logger = logging.getLogger(__name__)


def visibilidad_tras_sync(row: UserEmailAliasPref, *, es_propio: bool) -> bool:
    """Si el usuario se pronunció (quiere o rechaza), manda eso. Si no, la
    regla por defecto: propio visible; ajeno oculto salvo que fuera ya su
    predeterminado usable (visible)."""
    if row.user_opted_in is not None:
        return bool(row.user_opted_in)
    if es_propio:
        return True
    return bool(row.is_default and row.is_allowed)


def normalizar_predeterminado(
    rows: Iterable[UserEmailAliasPref], *, user_email: str,
    predeterminado_de_gmail: str | None = None,
) -> None:
    """Invariante: el predeterminado es uno y está visible.

    Un predeterminado apagado deja de serlo. Si quedan remitentes visibles y
    ninguno es el predeterminado, pasa a serlo, por este orden: uno de los que
    el usuario eligió; el que Gmail tiene como predeterminado de la cuenta, si
    le queda visible; su alias propio; el primero por orden alfabético. Con
    más de uno, se queda el primero por ese mismo orden. Un predeterminado
    que el usuario ya tenía no se toca."""
    filas = list(rows)
    for row in filas:
        if row.is_default and not row.is_allowed:
            row.is_default = False
    visibles = [row for row in filas if row.is_allowed]
    if not visibles:
        return
    email = (user_email or "").strip().lower()
    de_gmail = (predeterminado_de_gmail or "").strip().lower()

    def _orden(row: UserEmailAliasPref) -> tuple[bool, bool, bool, str]:
        alias = row.alias_email.strip().lower()
        return (
            row.user_opted_in is not True,
            not de_gmail or alias != de_gmail,
            alias != email,
            alias,
        )

    predeterminados = sorted((r for r in visibles if r.is_default), key=_orden)
    if len(predeterminados) == 1:
        return
    if predeterminados:
        for sobrante in predeterminados[1:]:
            sobrante.is_default = False
        return
    min(visibles, key=_orden).is_default = True


def sync_send_as_aliases(session: Session, *, user_id: str) -> int:
    """Refleja los Send-As aliases de Gmail en `user_email_alias_prefs`
    respetando lo que el usuario eligió. Devuelve cuántos aliases se
    procesaron.

    No commitea — el caller maneja la transacción. Best-effort a nivel de
    caller: si Gmail no está conectado / falta scope, levanta la excepción
    correspondiente y el caller la captura."""
    from app.integrations.gmail.service import _client_for  # noqa: PLC0415

    client = _client_for(session, user_id)
    gmail_aliases = client.list_send_as_aliases()

    existing = {
        row.alias_email.strip().lower(): row
        for row in session.scalars(
            select(UserEmailAliasPref).where(
                UserEmailAliasPref.user_id == user_id
            )
        )
    }
    user = session.get(User, user_id)
    user_email = (user.email or "").strip().lower() if user else ""

    now = datetime.now(UTC)
    processed = 0
    gmail_keys: set[str] = set()
    nuevas: list[UserEmailAliasPref] = []
    predeterminado_de_gmail: str | None = None
    for alias in gmail_aliases:
        email = (alias.get("send_as_email") or "").strip()
        if not email:
            continue
        key = email.lower()
        gmail_keys.add(key)
        if alias.get("is_default"):
            predeterminado_de_gmail = key
        display = alias.get("display_name") or None
        es_propio = key == user_email
        row = existing.get(key)
        if row is not None:
            if display:
                row.gmail_display_name = display
            row.is_allowed = visibilidad_tras_sync(row, es_propio=es_propio)
            row.updated_at = now
        else:
            # Alias nuevo: nadie se ha pronunciado → oculto, salvo el propio.
            row = UserEmailAliasPref(
                user_id=user_id,
                alias_email=email,
                is_allowed=es_propio,
                is_default=False,
                gmail_display_name=display,
            )
            session.add(row)
            nuevas.append(row)
        processed += 1

    # Aliases que ya no existen en Gmail: conservar la fila para histórico
    # pero marcarla no-usable (y, por tanto, no predeterminada). NO se borra.
    for key, row in existing.items():
        if key not in gmail_keys and (row.is_allowed or row.is_default):
            row.is_allowed = False
            row.is_default = False
            row.updated_at = now

    # Predeterminado: uno y visible. El de Gmail solo cuenta si al usuario le
    # queda visible, y por detrás de lo que él eligió.
    normalizar_predeterminado(
        [*existing.values(), *nuevas], user_email=user_email,
        predeterminado_de_gmail=predeterminado_de_gmail,
    )

    session.flush()
    return processed


def sync_all_active_users(session: Session) -> int:
    """PR-OAuth-Google-Unificado. Cron `gmail:sync_aliases`. Gateado por
    la integración ORG: si está activa, recorre los users ACTIVOS del CRM
    y sincroniza sus aliases Send-As (per-user, leídos de la cuenta
    compartida). Las preferencias de un usuario dado de baja se quedan como
    estaban: no se borran ni se tocan. Devuelve cuántos users se procesaron
    con éxito. Un fallo en un user (scope, token) NO aborta el resto."""
    from app.core.audit import Action, record_event  # noqa: PLC0415
    from app.integrations.google_calendar.service import (  # noqa: PLC0415
        get_org_integration,
    )
    from app.models.crm import User  # noqa: PLC0415

    org = get_org_integration(session)
    if org is None or org.status != "active":
        logger.info("gmail.sync_aliases skip — org integration not active")
        return 0

    user_ids = list(
        session.scalars(select(User.id).where(User.is_active.is_(True)))
    )
    ok = 0
    for uid in user_ids:
        try:
            count = sync_send_as_aliases(session, user_id=uid)
            record_event(
                session,
                action=Action.GMAIL_ALIASES_SYNCED,
                target_type="user",
                target_id=uid,
                metadata={"user_id": uid, "synced_count": count},
            )
            session.commit()
            ok += 1
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning(
                "gmail.sync_aliases failed user_id=%s", uid, exc_info=True,
            )
    logger.info("gmail.sync_aliases done users_ok=%d/%d", ok, len(user_ids))
    return ok
