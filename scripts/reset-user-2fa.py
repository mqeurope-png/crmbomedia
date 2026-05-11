#!/usr/bin/env python3
"""Emergency reset of a user's 2FA from the VPS shell.

Use this when a user (typically an admin) has lost both their TOTP app and
their backup codes and can no longer log in. The script clears the TOTP
secret, the totp_enabled flag and the remaining backup codes for one user
identified by email. After this runs, the user logs in with their password
only; if they are an admin, the next login issues a "limited" JWT and the
account-security page prompts them to enroll a new TOTP secret.

Usage:
    sudo docker compose -f docker-compose.prod.yml exec api \\
        python -m scripts.reset_user_2fa --email admin@yourdomain.com [--yes]

    # or, if you keep a venv on the host:
    python scripts/reset-user-2fa.py --email admin@yourdomain.com

The script touches only the four 2FA columns on the user row. Audit logs
are written so the action is traceable.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

# Ensure the backend `app` package is importable when running from the repo
# root. Inside the container the `app` package is on sys.path already.
try:
    from app.db.session import get_engine
    from app.models.crm import AuditLog, User
except ImportError:
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    from app.db.session import get_engine  # type: ignore[no-redef]
    from app.models.crm import AuditLog, User  # type: ignore[no-redef]

from sqlalchemy import select
from sqlalchemy.orm import Session


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--email",
        required=True,
        help="Email of the user whose 2FA must be reset (case-insensitive).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation (for automation).",
    )
    args = parser.parse_args()

    engine = get_engine()
    with Session(engine) as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if not user:
            print(f"User not found: {args.email}", file=sys.stderr)
            return 1

        print(
            f"User : {user.email} (role={user.role.value}, totp_enabled={user.totp_enabled})"
        )
        if not user.totp_enabled and not user.totp_secret_encrypted:
            print("2FA already disabled for this user; nothing to do.")
            return 0

        if not args.yes:
            confirm = input(
                "This will clear the TOTP secret, the backup codes and the "
                "totp_enabled flag. Type RESET to confirm: "
            )
            if confirm.strip() != "RESET":
                print("Aborted (confirmation not given).")
                return 1

        user.totp_secret_encrypted = None
        user.totp_enabled = False
        user.totp_confirmed_at = None
        user.backup_codes_hash = None
        session.add(
            AuditLog(
                actor_user_id=None,
                action="reset_2fa_cli",
                entity_type="user",
                entity_id=user.id,
                message=user.email,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()

    print(
        f"2FA reset for {args.email}. The user can now log in with password only "
        "and will be guided to re-enroll TOTP on next login."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
