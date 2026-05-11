"""Shared seed + auth helpers for the test suite.

Before 2FA landed, every test seeded users in a tiny loop and used a
two-line `auth_headers` helper. Now the seeded admin must have a TOTP
secret confirmed (otherwise `require_admin` issues a limited JWT and every
existing admin test breaks). To keep the diff small we centralize the
seed + login dance here and import from each test file.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.crypto import encrypt
from app.core.security import hash_password
from app.models.crm import User, UserRole

# A stable base32 secret shared by every admin seeded in the suite. Tests
# that need a fresh secret (the 2FA-specific suite) create their own users.
ADMIN_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
DEFAULT_PASSWORD = "password123"


def seed_test_users(
    session: Session,
    *,
    password: str = DEFAULT_PASSWORD,
    admin_totp_enabled: bool = True,
) -> None:
    """Seed one user per UserRole. Admin gets TOTP confirmed by default so
    `require_admin` issues a non-limited JWT and the existing assertions
    keep working unchanged."""
    for role in UserRole:
        kwargs: dict[str, object] = {
            "email": f"{role.value}@example.com",
            "full_name": f"{role.value.title()} User",
            "password_hash": hash_password(password),
            "role": role,
            "is_active": True,
        }
        if role == UserRole.ADMIN and admin_totp_enabled:
            kwargs.update(
                totp_secret_encrypted=encrypt(ADMIN_TOTP_SECRET),
                totp_enabled=True,
                totp_confirmed_at=datetime.now(UTC),
            )
        session.add(User(**kwargs))
    session.commit()


def auth_headers(
    client: TestClient,
    role: str = "admin",
    *,
    password: str = DEFAULT_PASSWORD,
) -> dict[str, str]:
    """Login and (when needed) complete the 2FA verify step.

    Returns the Authorization header with the final JWT, ready to drop into
    every API call.
    """
    response = client.post(
        "/api/auth/login",
        json={"email": f"{role}@example.com", "password": password},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    if body.get("requires_2fa"):
        code = pyotp.TOTP(ADMIN_TOTP_SECRET).now()
        verify = client.post(
            "/api/auth/2fa/verify",
            json={"temp_token": body["access_token"], "code": code},
        )
        assert verify.status_code == 200, verify.text
        body = verify.json()
    return {"Authorization": f"Bearer {body['access_token']}"}
