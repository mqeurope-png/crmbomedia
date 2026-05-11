"""TOTP (RFC 6238) helpers + one-time backup codes.

The secret is encrypted at rest using the same Fernet key (INTEGRATION_SECRETS_KEY)
that protects integration API keys; that ciphertext lives in
`users.totp_secret_encrypted`. Backup codes are hashed with the same pbkdf2
routine as passwords and stored as a JSON list in `users.backup_codes_hash`;
consumption removes the matching hash from the list.

The plaintext secret and the plaintext backup codes leave the server only:
  * the secret: once, when /api/auth/2fa/setup is called, so the user can
    paste it into Google Authenticator / Authy / 1Password (also embedded in
    the otpauth:// URI for the QR).
  * the backup codes: once, when /api/auth/2fa/confirm succeeds.
Neither is loggable; the API never returns them outside those two endpoints.
"""
from __future__ import annotations

import json
import secrets

import pyotp

from app.core.security import hash_password, verify_password

BACKUP_CODE_COUNT = 8
# 5 random bytes -> 10 hex chars. ~40 bits of entropy per code; enough for
# one-shot use because the codes are stored hashed and removed on use.
BACKUP_CODE_BYTES = 5
TOTP_VALID_WINDOW = 1  # accept the previous, current and next 30-second step


def generate_secret() -> str:
    """Return a freshly generated base32 TOTP secret."""
    return pyotp.random_base32()


def build_provisioning_uri(secret: str, *, account_name: str, issuer: str) -> str:
    """Build the otpauth:// URI that authenticator apps consume via QR."""
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_totp_code(secret: str | None, code: str) -> bool:
    """Verify a 6-digit code against the user's secret with ±1 step tolerance."""
    if not secret or not code:
        return False
    cleaned = code.strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) not in (6, 8):
        return False
    return pyotp.TOTP(secret).verify(cleaned, valid_window=TOTP_VALID_WINDOW)


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> list[str]:
    return [secrets.token_hex(BACKUP_CODE_BYTES) for _ in range(count)]


def hash_backup_codes(codes: list[str]) -> str:
    """Hash each code with the password pbkdf2 routine and serialize to JSON."""
    return json.dumps([hash_password(code) for code in codes])


def _normalize_backup_code(raw: str) -> str:
    return raw.strip().lower().replace(" ", "").replace("-", "")


def verify_and_consume_backup_code(
    stored_json: str | None, code: str
) -> tuple[bool, str | None]:
    """Return (consumed, new_stored_json).

    On success the new JSON has the matching hash removed; if the list ends
    up empty, returns None so the column goes back to NULL.
    On failure the input is returned unchanged.
    """
    if not stored_json or not code:
        return False, stored_json
    candidate = _normalize_backup_code(code)
    if not candidate:
        return False, stored_json
    try:
        hashes: list[str] = json.loads(stored_json)
    except (json.JSONDecodeError, TypeError):
        return False, stored_json
    for index, stored in enumerate(hashes):
        if verify_password(candidate, stored):
            remaining = hashes[:index] + hashes[index + 1 :]
            return True, (json.dumps(remaining) if remaining else None)
    return False, stored_json


def remaining_backup_codes(stored_json: str | None) -> int:
    if not stored_json:
        return 0
    try:
        return len(json.loads(stored_json))
    except (json.JSONDecodeError, TypeError):
        return 0
