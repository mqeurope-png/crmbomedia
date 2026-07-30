"""Anti-spam de los formularios web: honeypot, reCAPTCHA v3, rate limit.

Defensa en profundidad — cada capa es independiente y fail-open ante
errores de infraestructura (Redis caído, Google no responde) para no
tirar la captura de leads legítimos; el resto de capas cubren.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

HONEYPOT_FIELD = "website"  # invisible en el widget; si viene lleno → bot.
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW_SECONDS = 3600
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def honeypot_triggered(payload: dict) -> bool:
    """True si el campo trampa viene relleno (bot)."""
    return bool(str(payload.get(HONEYPOT_FIELD) or "").strip())


def verify_recaptcha(token: str | None, ip: str | None) -> float | None:
    """Verifica el token reCAPTCHA v3 con Google. Devuelve el score
    (0.0-1.0), o None si reCAPTCHA no está configurado o la verificación
    no se pudo completar (error de red → fail-open, no bloquea).

    Un token ausente cuando reCAPTCHA SÍ está configurado devuelve 0.0
    (se tratará como score bajo → spam)."""
    settings = get_settings()
    if not settings.recaptcha_configured:
        return None
    if not token:
        return 0.0
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                RECAPTCHA_VERIFY_URL,
                data={
                    "secret": settings.recaptcha_secret,
                    "response": token,
                    "remoteip": ip or "",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("web_forms.recaptcha verify failed", exc_info=True)
        return None
    if not data.get("success"):
        return 0.0
    try:
        return float(data.get("score", 0.0))
    except (TypeError, ValueError):
        return 0.0


def recaptcha_min_score() -> float:
    return float(get_settings().recaptcha_min_score)


def check_and_increment_rate_limit(ip: str | None, form_id: str) -> bool:
    """Rate limit por IP y form: máx RATE_LIMIT_MAX/hora. Devuelve True si
    se permite el submit, False si se excedió. Sin IP o Redis caído →
    permitido (fail-open)."""
    if not ip:
        return True
    try:
        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        key = f"form_submit_rate:{ip}:{form_id}"
        count = conn.incr(key)
        if count == 1:
            conn.expire(key, RATE_LIMIT_WINDOW_SECONDS)
        return int(count) <= RATE_LIMIT_MAX
    except Exception:  # noqa: BLE001 — infra caída no debe tumbar la captura
        logger.warning("web_forms.rate_limit check failed", exc_info=True)
        return True
