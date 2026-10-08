"""RQ jobs for the Gmail integration.

Two surfaces:
- `enqueue_process_history` — fired by the webhook to import inbound
  replies for a given user.
- `enqueue_renew_all_watches` — fired by the scheduler heartbeat
  to top up watches before the 7-day upstream expiry.

The job entry points use the standard `app.db.session.get_session`
context so they can run under the worker without an HTTP request.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models.crm import GmailPubsubWatch

logger = logging.getLogger(__name__)


def enqueue_process_history(*, user_id: str, new_history_id: int) -> None:
    """Push the history-processing job onto the worker queue. Best
    effort — if Redis is unreachable, we fall back to in-process
    execution so the webhook still imports the replies."""
    try:
        from app.workers.queues import queue_for  # noqa: PLC0415

        queue = queue_for("gmail", "process_history")
        queue.enqueue(process_history_job, user_id, new_history_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "gmail.enqueue_failed user_id=%s; running inline", user_id
        )
        process_history_job(user_id, new_history_id)


#: Cerrojo por cuenta mientras se procesa su historial. El push y el sondeo de
#: respaldo pueden caer a la vez sobre la misma cuenta, y entonces las dos
#: pasadas escriben los mismos mensajes: de ahí los `Lock wait timeout
#: exceeded (1205)` del registro de fallidos. Con TTL, para que un worker que
#: se muera no deje la cuenta cerrada.
CERROJO_PREFIJO = "gmail:history:"
CERROJO_TTL = 15 * 60


def _tomar_cerrojo(user_id: str) -> tuple[Any | None, bool]:
    """`(conexión, se_puede_seguir)`.

    Si el cerrojo ya lo tiene otro, `se_puede_seguir` es `False`. Si Redis no
    responde se sigue adelante sin cerrojo (fail-open): mejor arriesgar un
    choque que dejar de capturar correo."""
    try:
        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        if conn.set(f"{CERROJO_PREFIJO}{user_id}", "1", nx=True, ex=CERROJO_TTL):
            return conn, True
        return None, False
    except Exception as exc:  # noqa: BLE001
        logger.warning("gmail.history cerrojo sin Redis user_id=%s: %s", user_id, exc)
        return None, True


def _soltar_cerrojo(conn: Any, user_id: str) -> None:
    try:
        conn.delete(f"{CERROJO_PREFIJO}{user_id}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("gmail.history cerrojo no liberado user_id=%s: %s",
                       user_id, exc)


def process_history_job(user_id: str, new_history_id: int) -> int:
    """RQ entry point. Returns the count of messages imported."""
    from google.auth.exceptions import RefreshError  # noqa: PLC0415

    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415
    from app.integrations.google_calendar.client import (  # noqa: PLC0415
        GoogleAuthExpiredError,
    )

    conn, se_puede = _tomar_cerrojo(user_id)
    if not se_puede:
        # Ya hay una pasada en marcha para esta cuenta. No se pierde nada: la
        # que corre lee el cursor de la base de datos, así que se llevará
        # también lo que acaba de llegar.
        logger.info("gmail.process_history ya en marcha user_id=%s, se omite",
                    user_id)
        return 0
    try:
        with Session(get_engine()) as session:
            try:
                imported = gmail_service.process_history(
                    session, user_id=user_id, new_history_id=new_history_id
                )
                session.commit()
                return imported
            except (GoogleAuthExpiredError, RefreshError) as exc:
                # `RefreshError` crudo: google-auth refresca el token DENTRO
                # del `execute()`, así que no pasa por nuestro envoltorio y
                # se escapaba sin marcar nada. Era el motivo de que los
                # `invalid_grant` se reintentaran sin parar en vez de pedir
                # una reconexión.
                session.rollback()
                from app.integrations.google_calendar.service import (  # noqa: PLC0415
                    mark_needs_reconnect,
                )

                mark_needs_reconnect(session, user_id=user_id,
                                    error=f"invalid_grant: {exc}"[:255])
                session.commit()
                logger.warning(
                    "gmail.process_history token rechazado user_id=%s: la "
                    "cuenta queda marcada para reconectar y deja de "
                    "encolarse", user_id,
                )
                # NO se relanza: reintentarlo no arregla un token revocado.
                return 0
            except Exception:
                session.rollback()
                logger.warning(
                    "gmail.process_history_job_failed user_id=%s", user_id,
                    exc_info=True,
                )
                raise
    finally:
        if conn is not None:
            _soltar_cerrojo(conn, user_id)


def enqueue_recuperar_hueco(*, user_id: str, dias: int) -> None:
    """Encola el repaso que recupera el hueco de un cursor caducado."""
    try:
        from app.workers.queues import queue_for  # noqa: PLC0415

        queue = queue_for("gmail", "poll_fallback")
        queue.enqueue(recuperar_hueco_job, user_id, dias)
    except Exception:  # noqa: BLE001 — sin Redis queda el aviso del log
        logger.warning(
            "gmail.recuperar_hueco no se pudo encolar user_id=%s dias=%s; "
            "hazlo a mano con `python -m app.integrations.gmail_watch "
            "--since <fecha> --yes`", user_id, dias, exc_info=True,
        )


def recuperar_hueco_job(user_id: str, dias: int) -> int:
    """Trae los mensajes de los últimos `dias` días por `messages.list`.

    Es la sincronización completa acotada a la que se cae cuando el cursor de
    `history.list` ha caducado. Idempotente: el repaso salta lo que ya está
    guardado, así que volver a lanzarlo no duplica nada.
    """
    from app.integrations.gmail.backfill_universal import (  # noqa: PLC0415
        run_backfill_universal,
    )

    with Session(get_engine()) as session:
        try:
            informe = run_backfill_universal(
                session,
                user_id=user_id,
                since=date.today() - timedelta(days=max(1, dias)),
                until=date.today(),
                sleep_between_pages=0.5,
            )
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning(
                "gmail.recuperar_hueco falló user_id=%s dias=%s", user_id, dias,
                exc_info=True,
            )
            raise
        recuperados = informe.imported_linked + informe.imported_orphan
        logger.warning(
            "gmail.recuperar_hueco user_id=%s dias=%s recuperados=%s",
            user_id, dias, recuperados,
        )
        return int(recuperados or 0)


def enqueue_renew_all_watches() -> None:
    try:
        from app.workers.queues import queue_for  # noqa: PLC0415

        queue = queue_for("gmail", "renew_watches")
        queue.enqueue(renew_all_watches_job)
    except Exception:  # noqa: BLE001
        logger.warning("gmail.renew.enqueue_failed; running inline")
        renew_all_watches_job()


def renew_all_watches_job() -> int:
    """PR-OAuth-Google-Unificado. Renueva el watch de la cuenta org
    ÚNICA. Devuelve 1 si se renovó, 0 si no hay integración org activa.

    Antes iteraba 6 integraciones per-user; ahora hay 1 cuenta Gmail
    compartida → 1 watch atribuido al user que conectó."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415
    from app.integrations.google_calendar.service import (  # noqa: PLC0415
        get_org_integration,
    )

    with Session(get_engine()) as session:
        org = get_org_integration(session)
        if org is None or org.status != "active" or not org.connected_by_user_id:
            logger.info(
                "gmail.renew skip — org integration not active/connected"
            )
            return 0
        try:
            gmail_service.register_watch(
                session, user_id=org.connected_by_user_id
            )
            session.commit()
            return 1
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning("gmail.renew_failed org watch", exc_info=True)
            return 0


def enqueue_poll_history_fallback() -> None:
    try:
        from app.workers.queues import queue_for  # noqa: PLC0415

        queue = queue_for("gmail", "poll_fallback")
        queue.enqueue(poll_history_fallback_job)
    except Exception:  # noqa: BLE001
        logger.warning("gmail.poll_fallback.enqueue_failed; running inline")
        poll_history_fallback_job()


def poll_history_fallback_job() -> int:
    """CRM-GMAIL Parte D — safety-net. Si el webhook push falla o el watch
    caduca sin renovarse, este cron (cada 15 min) hace `history.list` desde
    el cursor guardado y recupera lo que se haya perdido. Si recupera >0
    mensajes emite un warning: el push no está funcionando y hay que mirarlo.
    Devuelve el nº de mensajes recuperados."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415
    from app.integrations.google_calendar.service import (  # noqa: PLC0415
        get_org_integration,
    )

    with Session(get_engine()) as session:
        org = get_org_integration(session)
        if org is None or org.status != "active" or not org.connected_by_user_id:
            return 0
        try:
            recovered = gmail_service.process_history(
                session, user_id=org.connected_by_user_id, new_history_id=None
            )
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning("gmail.poll_fallback_failed", exc_info=True)
            return 0
        if recovered > 0:
            logger.warning(
                "gmail.poll_fallback recovered=%s — el push en tiempo real "
                "parece no estar funcionando; revisa Watch/Pub-Sub",
                recovered,
            )
        return recovered


def watches_expiring_soon(session: Session, *, days: int = 1) -> list[GmailPubsubWatch]:
    """Return watches whose expiry is within `days` days. Used by
    the cron heartbeat to renew lazily instead of unconditionally."""
    horizon = datetime.now(UTC) + timedelta(days=days)
    return list(
        session.scalars(
            select(GmailPubsubWatch).where(
                GmailPubsubWatch.watch_expires_at <= horizon
            )
        )
    )
