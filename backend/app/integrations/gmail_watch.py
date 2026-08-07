"""CRM-GMAIL — fachada + CLI de Gmail Watch (Push Notifications vía Pub/Sub).

La lógica vive en `app.integrations.gmail.service` (register/unregister) y en
`app.integrations.gmail.jobs` (renovación/poller). Este módulo la expone con
los nombres del spec y como CLI para el paso post-deploy:

    docker exec crmbo-api python -m app.integrations.gmail_watch register_watch

Comandos: `register_watch`, `renew_watch_if_expiring`, `unregister_watch`.
Todos operan sobre la cuenta Google ORG única (`connected_by_user_id`).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

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


def backfill_universal(argv: list[str]) -> None:
    """CRM-GMAIL-BACKFILL — reprocesa el histórico con captura universal."""
    parser = argparse.ArgumentParser(
        prog="python -m app.integrations.gmail_watch backfill_universal",
        description="Reprocesa el histórico Gmail capturando TODO mail a un "
        "alias activo del CRM (no solo de contactos conocidos).",
    )
    parser.add_argument(
        "--since", type=date.fromisoformat, default=None,
        help="Fecha mínima YYYY-MM-DD (default: hoy - 6 meses).",
    )
    parser.add_argument(
        "--until", type=date.fromisoformat, default=None,
        help="Fecha tope YYYY-MM-DD, inclusiva (default: hoy).",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="No escribe nada; solo cuenta.")
    parser.add_argument("--dry-run-limit", type=int, default=500,
                        help="Máx. mensajes a examinar en dry-run (default 500).")
    parser.add_argument("--labels", default="INBOX,SPAM",
                        help="Labels de Gmail, coma-separadas (default INBOX,SPAM).")
    parser.add_argument("--yes", action="store_true",
                        help="Salta la confirmación interactiva.")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="Mensajes por página de Gmail (máx 100).")
    args = parser.parse_args(argv)

    since = args.since or (date.today() - timedelta(days=183))
    until = args.until or date.today()
    labels = [lbl.strip().upper() for lbl in args.labels.split(",") if lbl.strip()]

    from app.integrations.gmail.backfill_universal import (  # noqa: PLC0415
        run_backfill_universal,
    )
    from app.services.email_aliases import active_alias_map  # noqa: PLC0415

    with Session(get_engine()) as session:
        user_id = _org_user_id(session)
        alias_map = active_alias_map(session)
        if not alias_map:
            print(
                "Sin alias activos en user_email_aliases. Añádelos en "
                "/admin/users antes de reprocesar. Aborta."
            )
            raise SystemExit(1)

        print(f"Alias activos ({len(alias_map)}): {', '.join(sorted(alias_map.values()))}")
        print()
        print(
            "⚠️  Verifica que los alias en /admin/users están COMPLETOS antes de "
            "continuar."
        )
        print(
            "   Los mails dirigidos a un alias que NO esté configurado se "
            "descartarán (se listan al final del report)."
        )
        if not args.yes:
            try:
                resp = input(
                    f"¿Continuar backfill {since} → {until} "
                    f"labels={labels}{' [DRY-RUN]' if args.dry_run else ''}? (Y/n) "
                ).strip().lower()
            except EOFError:
                print("Sin TTY y sin --yes → aborto.")
                raise SystemExit(1) from None
            if resp not in ("", "y", "yes", "s", "si", "sí"):
                print("Cancelado.")
                return

        report = run_backfill_universal(
            session,
            user_id=user_id,
            since=since,
            until=until,
            dry_run=args.dry_run,
            dry_run_limit=args.dry_run_limit,
            labels=labels,
            batch_size=args.batch_size,
            alias_map=alias_map,
            sleep_between_pages=0.0 if args.dry_run else 0.5,
            progress=print,
        )
        print()
        print(report.render())


_COMMANDS = {
    "register_watch": register_watch,
    "renew_watch_if_expiring": renew_watch_if_expiring,
    "unregister_watch": unregister_watch,
}


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "backfill_universal":
        backfill_universal(args[1:])
        return
    if not args or args[0] not in _COMMANDS:
        names = "|".join([*_COMMANDS, "backfill_universal"])
        print(f"uso: python -m app.integrations.gmail_watch {{{names}}}")
        raise SystemExit(2)
    _COMMANDS[args[0]]()


if __name__ == "__main__":
    main()
