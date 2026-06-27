"""PR-OAuth-Permisos-Admin Item 13 — sincronización de Send-As aliases.

La tabla `user_email_alias_prefs.is_default` controla qué alias procesa
el handler del backfill. Antes NO se actualizaba desde Gmail al
reconectar: si el user marcaba el ★ default en Gmail directamente (o en
la UI del CRM que sí sincroniza), la BD local seguía con `is_default=0`
y el handler skipeaba al user.

Este módulo refleja el estado real de Gmail Settings → "Send mail as"
en la tabla local:
  - Gmail `isDefault=true`         → `is_default=1` (y 0 en los demás)
  - alias verificado (accepted)    → `is_allowed=1`
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import User, UserEmailAliasPref

logger = logging.getLogger(__name__)


def sync_send_as_aliases(session: Session, *, user_id: str) -> int:
    """Refleja los Send-As aliases de Gmail en `user_email_alias_prefs`
    RESPETANDO las preferencias previas del user. Devuelve cuántos aliases
    se procesaron.

    PR-Hotfix-OAuth-Banner Bug 15. La cuenta Google es org-wide y compartida:
    Gmail devuelve los 50+ aliases de la cuenta para CADA user. Antes este
    sync marcaba TODOS `is_allowed=1` por user, desbordando de 1-4 a 50+ y
    pisando lo que cada user había elegido. Ahora:
      - Fila existente: NO se toca `is_allowed` (preferencia del user). Se
        actualiza `gmail_display_name`; `is_default` solo si el user no
        tiene ya un default propio.
      - Fila nueva: `is_allowed=0` (oculta), salvo que el alias sea el
        email propio del user (`users.email`) → `is_allowed=1`.
      - Primer sync del user (tabla vacía) + alias propio marcado default
        en Gmail → se siembra como `is_default=1`.
      - Alias que ya no existe en Gmail: la fila se conserva pero
        `is_allowed=0` (no se borra).

    No commitea — el caller maneja la transacción. Best-effort a nivel
    de caller: si Gmail no está conectado / falta scope, levanta la
    excepción correspondiente y el caller la captura."""
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
    first_sync = len(existing) == 0
    user = session.get(User, user_id)
    user_email = (user.email or "").strip().lower() if user else ""
    # ¿El user ya eligió un default propio? Si sí, NO lo sobreescribimos.
    assigned_default = any(r.is_default for r in existing.values())

    now = datetime.now(UTC)
    processed = 0
    gmail_keys: set[str] = set()
    for alias in gmail_aliases:
        email = (alias.get("send_as_email") or "").strip()
        if not email:
            continue
        key = email.lower()
        gmail_keys.add(key)
        is_default_gmail = bool(alias.get("is_default"))
        display = alias.get("display_name") or None
        is_self = key == user_email
        row = existing.get(key)
        if row is not None:
            # Fila existente → respetar `is_allowed` (preferencia del user).
            if display:
                row.gmail_display_name = display
            if not assigned_default and is_default_gmail:
                row.is_default = True
                assigned_default = True
            row.updated_at = now
        else:
            # Alias nuevo → oculto, salvo el alias propio del user.
            new_default = (
                first_sync and is_self and is_default_gmail and not assigned_default
            )
            if new_default:
                assigned_default = True
            session.add(
                UserEmailAliasPref(
                    user_id=user_id,
                    alias_email=email,
                    is_allowed=is_self,
                    is_default=new_default,
                    gmail_display_name=display,
                )
            )
        processed += 1

    # Aliases que ya no existen en Gmail: conservar la fila para histórico
    # pero marcarla no-usable (is_allowed=0). NO se borra.
    for key, row in existing.items():
        if key not in gmail_keys and row.is_allowed:
            row.is_allowed = False
            row.updated_at = now

    session.flush()
    return processed


def sync_all_active_users(session: Session) -> int:
    """PR-OAuth-Google-Unificado. Cron `gmail:sync_aliases`. Gateado por
    la integración ORG: si está activa, recorre TODOS los users del CRM
    y sincroniza sus aliases Send-As (per-user, leídos de la cuenta
    compartida). Devuelve cuántos users se procesaron con éxito. Un fallo
    en un user (scope, token) NO aborta el resto."""
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
