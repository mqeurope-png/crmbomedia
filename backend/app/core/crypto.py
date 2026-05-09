"""Symmetric encryption helpers for integration API keys.

Wraps cryptography.fernet so the rest of the codebase never touches the raw
key. Plaintext is stored only in memory; ciphertext is what reaches the
database. Losing INTEGRATION_SECRETS_KEY makes existing ciphertext
unrecoverable — see docs/security.md for the rotation procedure.
"""
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class DecryptionError(RuntimeError):
    """Raised when a stored ciphertext cannot be decrypted with the current key."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(get_settings().integration_secrets_key.encode())


def encrypt(plaintext: str) -> str:
    if not isinstance(plaintext, str) or plaintext == "":
        raise ValueError("plaintext must be a non-empty string")
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError(
            "Stored ciphertext could not be decrypted with the current "
            "INTEGRATION_SECRETS_KEY. Either the key was rotated without "
            "re-encrypting stored values, or the ciphertext is corrupted."
        ) from exc
