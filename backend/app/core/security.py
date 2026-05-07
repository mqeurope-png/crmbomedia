import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings

HASH_NAME = "sha256"
ITERATIONS = 210_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(HASH_NAME, password.encode(), salt.encode(), ITERATIONS)
    return f"pbkdf2_{HASH_NAME}${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = password_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != f"pbkdf2_{HASH_NAME}":
        return False
    digest = hashlib.pbkdf2_hmac(HASH_NAME, password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(digest.hex(), expected)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_reset_token() -> str:
    return secrets.token_urlsafe(32)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_reset_token(token: str, token_hash: str | None) -> bool:
    if not token_hash:
        return False
    return hmac.compare_digest(hash_reset_token(token), token_hash)


def create_access_token(subject: str, role: str, expires_minutes: int | None = None) -> str:
    settings = get_settings()
    expires_delta = timedelta(minutes=expires_minutes or settings.access_token_expire_minutes)
    expires_at = datetime.now(UTC) + expires_delta
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": subject, "role": role, "exp": int(expires_at.timestamp())}
    signing_input = ".".join(
        [
            _b64encode(json.dumps(header, separators=(",", ":")).encode()),
            _b64encode(json.dumps(payload, separators=(",", ":")).encode()),
        ]
    )
    signature = hmac.new(
        settings.secret_key.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        header, payload, signature = token.split(".")
    except ValueError:
        return None
    signing_input = f"{header}.{payload}"
    expected_signature = hmac.new(
        settings.secret_key.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(_b64encode(expected_signature), signature):
        return None
    try:
        data = json.loads(_b64decode(payload))
    except (json.JSONDecodeError, ValueError):
        return None
    if int(data.get("exp", 0)) < int(datetime.now(UTC).timestamp()):
        return None
    return data
