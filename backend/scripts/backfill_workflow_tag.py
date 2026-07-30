"""One-off: mirror a workflow-written CSV tag into `contact_tags`.

PR-Hotfix-Ficha-360 Bug 2. La acción `add_tag` de los workflows solo
escribía el CSV legacy `contacts.tags`, así que el tag no aparecía en
la pestaña Tags (que lee la M:N `contact_tags`). El handler ya está
arreglado; este script converge los contactos que ejecutaron el
workflow ANTES del fix. Por defecto apunta al caso de validación de
Bart (tag `testeando` de josep.profitos@cartodelta.com).

NO es una migración Alembic — se ejecuta manualmente tras el deploy.

Idempotente: si el tag ya existe se reutiliza (case-insensitive) y si
el link contacto↔tag ya está, no duplica. `--dry-run` rolls back.

Usage:
    python -m scripts.backfill_workflow_tag
    python -m scripts.backfill_workflow_tag --email otro@dominio.com --tag vip
    python -m scripts.backfill_workflow_tag --dry-run
"""
from __future__ import annotations

import argparse
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models.crm import Contact
from app.repositories import crm as crm_repository

log = logging.getLogger("backfill_workflow_tag")
logging.basicConfig(level=logging.INFO, format="%(message)s")

DEFAULT_EMAIL = "josep.profitos@cartodelta.com"
DEFAULT_TAG = "testeando"


def backfill(*, email: str, tag_name: str, dry_run: bool) -> dict[str, str]:
    engine = get_engine()
    with Session(engine) as session:
        contact = session.scalar(select(Contact).where(Contact.email == email))
        if contact is None:
            log.error("Contacto %s no encontrado — nada que hacer.", email)
            return {"outcome": "contact_not_found"}

        csv_tags = {
            t.strip().lower()
            for t in (contact.tags or "").split(",")
            if t.strip()
        }
        if tag_name.strip().lower() not in csv_tags:
            log.warning(
                "El CSV del contacto no contiene %r (tags=%r) — se crea "
                "el link igualmente porque el workflow reportó ok.",
                tag_name, contact.tags,
            )

        tag, created = crm_repository.upsert_tag(session, name=tag_name)
        linked = crm_repository.assign_tag_to_contact(
            session,
            contact_id=contact.id,
            tag_id=tag.id,
            assigned_by_user_id=None,
            source="workflow",
        )
        outcome = (
            f"tag {'creado' if created else 'reutilizado'} (id={tag.id}), "
            f"link {'creado' if linked else 'ya existía'}"
        )
        if dry_run:
            session.rollback()
            log.info("[dry-run] %s — rollback.", outcome)
        else:
            session.commit()
            log.info("%s — commit.", outcome)
        return {"outcome": outcome}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--tag", default=DEFAULT_TAG)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    backfill(email=args.email, tag_name=args.tag, dry_run=args.dry_run)
