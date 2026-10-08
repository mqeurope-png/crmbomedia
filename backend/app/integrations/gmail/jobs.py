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
from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def con_cerrojo(user_id: str, *, quien: str) -> Iterator[bool]:
    """`True` si se puede trabajar, `False` si ya hay otra pasada en marcha.

    Lo usan los TRES caminos que escriben los mismos mensajes de la misma
    cuenta: el push, el sondeo de respaldo y la recuperación de un hueco. Con
    el cerrojo solo en el push, el choque push-contra-sondeo —que es el que
    provoca los `Lock wait timeout (1205)`— seguía vivo."""
    conn, se_puede = _tomar_cerrojo(user_id)
    if not se_puede:
        logger.info("gmail.%s ya hay una pasada en marcha user_id=%s, se omite",
                    quien, user_id)
    try:
        yield se_puede
    finally:
        if conn is not None:
            _soltar_cerrojo(conn, user_id)


def process_history_job(user_id: str, new_history_id: int) -> int:
    """RQ entry point. Returns the count of messages imported."""
    from google.auth.exceptions import RefreshError  # noqa: PLC0415

    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415
    from app.integrations.google_calendar.client import (  # noqa: PLC0415
        GoogleAuthExpiredError,
    )

    # Ya hay una pasada en marcha para esta cuenta → no se pierde nada: la que
    # corre lee el cursor de la base de datos, así que se llevará también lo
    # que acaba de llegar.
    with con_cerrojo(user_id, quien="process_history") as se_puede:
        if not se_puede:
            return 0
        with Session(get_engine()) as session:
            try:
                imported = gmail_service.process_history(
                    session, user_id=user_id, new_history_id=new_history_id
                )
                session.commit()
                return imported
            except (GoogleAuthExpiredError, RefreshError) as exc:
                if isinstance(exc, RefreshError) and not _autorizacion_muerta(exc):
                    # Un 429 o un 500 del endpoint de tokens de Google también
                    # llega como `RefreshError`, y ese SÍ se arregla
                    # reintentando. Marcar la cuenta por eso pararía todo el
                    # correo hasta que alguien la reconectara a mano.
                    session.rollback()
                    logger.warning(
                        "gmail.process_history fallo temporal al refrescar el "
                        "token user_id=%s: %s", user_id, exc,
                    )
                    raise
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


#: Lo que dice Google cuando la autorización ya no vale: hay que reconectar.
#: Cualquier otro `RefreshError` (429, 500, red) se arregla reintentando.
AUTORIZACION_MUERTA = ("invalid_grant", "invalid_client", "unauthorized_client",
                       "invalid_request")


def _autorizacion_muerta(exc: BaseException) -> bool:
    texto = str(exc).lower()
    return any(marca in texto for marca in AUTORIZACION_MUERTA)


#: El repaso de diez días no cabe en los 600 s por defecto de la cola.
RECUPERACION_TIMEOUT = 4 * 60 * 60


def enqueue_recuperar_hueco(*, user_id: str, dias: int) -> None:
    """Encola el repaso que recupera el hueco de un cursor caducado.

    **No se traga el fallo**: si no se puede encolar, la excepción sube y el
    llamador NO mueve el cursor, así que el próximo push vuelve a intentarlo.
    Tragárselo dejaba el hueco irrecuperable con solo una línea de log."""
    from app.workers.queues import queue_for  # noqa: PLC0415

    queue = queue_for("gmail", "poll_fallback")
    queue.enqueue(recuperar_hueco_job, user_id, dias,
                  job_timeout=RECUPERACION_TIMEOUT)


def recuperar_hueco_job(user_id: str, dias: int) -> int:
    """Trae los mensajes de los últimos `dias` días por `messages.list`.

    Es la sincronización completa acotada a la que se cae cuando el cursor de
    `history.list` ha caducado. Idempotente: el repaso salta lo que ya está
    guardado, así que volver a lanzarlo no duplica nada.

    **Día a día, con un `commit` por día.** Diez días en una sola transacción
    se perdían enteros si el trabajo se agotaba a media faena, y el cursor ya
    estaba movido: el hueco quedaba irrecuperable. Así, como mucho se pierde
    el día en curso, y como es idempotente se recupera relanzándolo.

    Con cerrojo: escribe los mismos mensajes que el push, y con un conjunto de
    escritura mucho más grande.
    """
    from app.integrations.gmail import backfill_universal  # noqa: PLC0415

    with con_cerrojo(user_id, quien="recuperar_hueco") as se_puede:
        if not se_puede:
            # Hay una pasada en marcha: se reencola para más tarde en vez de
            # perder el hueco.
            enqueue_recuperar_hueco(user_id=user_id, dias=dias)
            return 0
        recuperados = 0
        fallidos = 0
        hoy = date.today()
        for atras in range(max(1, dias), 0, -1):
            dia = hoy - timedelta(days=atras - 1)
            with Session(get_engine()) as session:
                try:
                    informe = backfill_universal.run_backfill_universal(
                        session, user_id=user_id, since=dia, until=dia,
                        sleep_between_pages=0.5,
                    )
                    session.commit()
                except Exception:  # noqa: BLE001 — un día malo no corta los demás
                    session.rollback()
                    fallidos += 1
                    logger.warning(
                        "gmail.recuperar_hueco falló el día %s user_id=%s",
                        dia, user_id, exc_info=True,
                    )
                    continue
                recuperados += informe.imported_linked + informe.imported_orphan
        logger.warning(
            "gmail.recuperar_hueco user_id=%s dias=%s recuperados=%s "
            "dias_fallidos=%s", user_id, dias, recuperados, fallidos,
        )
        return recuperados


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
        # El cerrojo va AQUÍ y no solo en el push: el choque de los dos a la
        # vez sobre la misma cuenta es el que provoca los 1205.
        with con_cerrojo(org.connected_by_user_id, quien="poll_fallback") as se_puede:
            if not se_puede:
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
