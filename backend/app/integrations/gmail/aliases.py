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

from app.models.crm import UserEmailAliasPref

logger = logging.getLogger(__name__)


def sync_send_as_aliases(session: Session, *, user_id: str) -> int:
    """Lee los Send-As aliases de Gmail y refleja `is_default`/`is_allowed`
    en `user_email_alias_prefs`. Devuelve cuántos aliases se procesaron.

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

    default_email: str | None = None
    now = datetime.now(UTC)
    processed = 0
    for alias in gmail_aliases:
        email = (alias.get("send_as_email") or "").strip()
        if not email:
            continue
        key = email.lower()
        is_default = bool(alias.get("is_default"))
        # `list_send_as_aliases` ya filtra a verificados, así que todo lo
        # que llega aquí es is_allowed=1.
        is_allowed = True
        display = alias.get("display_name") or None
        if is_default:
            default_email = key
        row = existing.get(key)
        if row is None:
            session.add(
                UserEmailAliasPref(
                    user_id=user_id,
                    alias_email=email,
                    is_allowed=is_allowed,
                    is_default=is_default,
                    gmail_display_name=display,
                )
            )
        else:
            row.is_allowed = is_allowed
            row.is_default = is_default
            if display:
                row.gmail_display_name = display
            row.updated_at = now
        processed += 1

    # Garantizar UN solo default: si Gmail marcó uno, ponemos is_default=0
    # en todos los demás aliases del user.
    if default_email is not None:
        for key, row in existing.items():
            if key != default_email and row.is_default:
                row.is_default = False
                row.updated_at = now

    session.flush()
    return processed


def sync_all_active_users(session: Session) -> int:
    """Cron `gmail:sync_aliases`. Recorre los users con integración
    Gmail activa y sincroniza sus aliases. Devuelve cuántos users se
    procesaron con éxito. Un fallo en un user (scope, token) NO aborta
    el resto."""
    from app.core.audit import Action, record_event  # noqa: PLC0415
    from app.models.crm import UserGoogleIntegration  # noqa: PLC0415

    integrations = list(
        session.scalars(
            select(UserGoogleIntegration).where(
                UserGoogleIntegration.status == "active",
            )
        )
    )
    ok = 0
    for integ in integrations:
        try:
            count = sync_send_as_aliases(session, user_id=integ.user_id)
            record_event(
                session,
                action=Action.GMAIL_ALIASES_SYNCED,
                target_type="user_google_integration",
                target_id=integ.id,
                metadata={"user_id": integ.user_id, "synced_count": count},
            )
            session.commit()
            ok += 1
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning(
                "gmail.sync_aliases failed user_id=%s", integ.user_id,
                exc_info=True,
            )
    logger.info(
        "gmail.sync_aliases done users_ok=%d/%d", ok, len(integrations)
    )
    return ok
