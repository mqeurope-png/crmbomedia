"""CRM-GMAIL — fachada + CLI de Gmail Watch (Push Notifications vía Pub/Sub).

La lógica vive en `app.integrations.gmail.service` (register/unregister) y en
`app.integrations.gmail.jobs` (renovación/poller). Este módulo la expone con
los nombres del spec y como CLI para el paso post-deploy:

    docker exec crmbo-api python -m app.integrations.gmail_watch register_watch

Comandos: `register_watch`, `renew_watch_if_expiring`, `unregister_watch`.
Todos operan sobre la cuenta Google ORG única (`connected_by_user_id`).
"""
from __future__ import annotations

import sys

from sqlalchemy.orm import Session

from app.db.session import get_engine


def _org_user_id(session: Session) -> str:
    from app.integrations.google_calendar.service import (  # noqa: PLC0415
        get_org_integration,
    )

    org = get_org_integration(session)
    if org is None or org.status != "active" or not org.connected_by_user_id:
        raise SystemExit(
            "No hay integración Google org activa/conectada. Conecta la cuenta "
            "primero desde /account."
        )
    return org.connected_by_user_id


def register_watch() -> None:
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    with Session(get_engine()) as session:
        watch = gmail_service.register_watch(
            session, user_id=_org_user_id(session)
        )
        session.commit()
        print(
            f"OK register_watch history_id={watch.history_id} "
            f"expira={watch.watch_expires_at.isoformat()}"
        )


def renew_watch_if_expiring() -> None:
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415
    from app.integrations.gmail.jobs import (  # noqa: PLC0415
        watches_expiring_soon,
    )

    with Session(get_engine()) as session:
        if not watches_expiring_soon(session, days=1):
            print("watch no caduca en <24h; nada que renovar")
            return
        watch = gmail_service.register_watch(
            session, user_id=_org_user_id(session)
        )
        session.commit()
        print(f"OK renovado history_id={watch.history_id}")


def unregister_watch() -> None:
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    with Session(get_engine()) as session:
        removed = gmail_service.unregister_watch(
            session, user_id=_org_user_id(session)
        )
        session.commit()
        print("OK unregister_watch" if removed else "no había watch")


_COMMANDS = {
    "register_watch": register_watch,
    "renew_watch_if_expiring": renew_watch_if_expiring,
    "unregister_watch": unregister_watch,
}


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in _COMMANDS:
        print(f"uso: python -m app.integrations.gmail_watch {{{'|'.join(_COMMANDS)}}}")
        raise SystemExit(2)
    _COMMANDS[args[0]]()


if __name__ == "__main__":
    main()
