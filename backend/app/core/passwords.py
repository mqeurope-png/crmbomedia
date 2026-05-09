"""Password policy used across signup, password change and reset flows.

Centralizing the rules here keeps the schemas thin, lets the API surface
consistent error messages, and provides a single source of truth that the
tests and the frontend hint copy can reference.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

MIN_LENGTH = 12
MAX_LENGTH = 128

_UPPERCASE_RE = re.compile(r"[A-Z]")
_LOWERCASE_RE = re.compile(r"[a-z]")
_DIGIT_RE = re.compile(r"\d")
_COMMON_PASSWORDS_FILE = Path(__file__).resolve().parent / "common_passwords.txt"


class PasswordPolicyError(ValueError):
    """Raised when a candidate password violates the active policy.

    Inherits from ValueError so pydantic field validators surface the
    message verbatim and FastAPI returns 422 with the explanation.
    """


@lru_cache(maxsize=1)
def _common_passwords() -> frozenset[str]:
    if not _COMMON_PASSWORDS_FILE.exists():
        return frozenset()
    entries: set[str] = set()
    for raw in _COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line.lower())
    return frozenset(entries)


def is_common_password(password: str) -> bool:
    return password.lower() in _common_passwords()


def validate_password_policy(password: str) -> None:
    """Raise PasswordPolicyError if `password` violates the policy.

    Rules (must all pass):
      - between MIN_LENGTH and MAX_LENGTH characters
      - at least one uppercase letter
      - at least one lowercase letter
      - at least one digit
      - not present in the common-passwords blocklist
    """
    if not isinstance(password, str):
        raise PasswordPolicyError("La contraseña debe ser texto.")
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(
            f"La contraseña debe tener al menos {MIN_LENGTH} caracteres."
        )
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(
            f"La contraseña no puede superar {MAX_LENGTH} caracteres."
        )
    if not _UPPERCASE_RE.search(password):
        raise PasswordPolicyError("Debe contener al menos una letra mayúscula.")
    if not _LOWERCASE_RE.search(password):
        raise PasswordPolicyError("Debe contener al menos una letra minúscula.")
    if not _DIGIT_RE.search(password):
        raise PasswordPolicyError("Debe contener al menos un número.")
    if is_common_password(password):
        raise PasswordPolicyError(
            "Esta contraseña aparece en listas públicas; elige una distinta."
        )


def policy_summary() -> dict[str, int | str]:
    """Machine-readable description used by tests and the frontend."""
    return {
        "min_length": MIN_LENGTH,
        "max_length": MAX_LENGTH,
        "requires": "uppercase, lowercase, digit; rejects common passwords",
    }
