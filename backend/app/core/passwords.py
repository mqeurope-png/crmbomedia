"""Password policy used across user creation, password change and admin reset.

ÚNICA fuente de verdad de la regla. Los schemas la aplican tal cual, los
mensajes de error del backend salen de aquí y el frontend
(`PasswordRequirements.tsx`) refleja los mismos tres puntos.

Regla (y nada más):
  - mínimo MIN_LENGTH caracteres
  - al menos una letra mayúscula
  - al menos un número
"""
from __future__ import annotations

import re
from collections.abc import Callable

MIN_LENGTH = 8
#: Tope técnico (bcrypt no mira más allá de 72 bytes); no es una regla de la
#: política que se enseñe al usuario.
MAX_LENGTH = 128

_UPPERCASE_RE = re.compile(r"[A-Z]")
_DIGIT_RE = re.compile(r"\d")

#: Los tres requisitos, en el orden en que se enseñan en pantalla:
#: `(clave, texto, comprobación)`. El texto es el mismo que ve el usuario en la
#: lista de requisitos y en el error del backend.
POLICY_RULES: tuple[tuple[str, str, Callable[[str], bool]], ...] = (
    ("length", f"Mínimo {MIN_LENGTH} caracteres",
     lambda p: len(p) >= MIN_LENGTH),
    ("upper", "Al menos una letra mayúscula",
     lambda p: bool(_UPPERCASE_RE.search(p))),
    ("digit", "Al menos un número",
     lambda p: bool(_DIGIT_RE.search(p))),
)


class PasswordPolicyError(ValueError):
    """Raised when a candidate password violates the active policy.

    Inherits from ValueError so pydantic field validators surface the
    message verbatim and FastAPI returns 422 with the explanation.
    """


def validate_password_policy(password: str) -> None:
    """Raise PasswordPolicyError if `password` violates the policy."""
    if not isinstance(password, str):
        raise PasswordPolicyError("La contraseña debe ser texto.")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(
            f"La contraseña no puede superar {MAX_LENGTH} caracteres."
        )
    for _key, texto, check in POLICY_RULES:
        if not check(password):
            raise PasswordPolicyError(f"La contraseña no cumple: {texto.lower()}.")


def policy_summary() -> dict[str, object]:
    """Machine-readable description used by tests and the frontend."""
    return {
        "min_length": MIN_LENGTH,
        "max_length": MAX_LENGTH,
        "rules": [texto for _key, texto, _check in POLICY_RULES],
    }
