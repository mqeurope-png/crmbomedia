"""Cliente VIES (Fase VIES del rediseño de flujo).

Valida un NIF-IVA intracomunitario contra el servicio oficial de la UE, la
API REST pública y gratuita de VIES:

    POST {base_url}/check-vat-number
    {"countryCode": "FR", "vatNumber": "16339753527"}
    → {"valid": true, "name": "...", "address": "...", "requestDate": ...,
       "userError": "VALID" | "INVALID_INPUT" | "MS_UNAVAILABLE" | ...}

`countryCode` es el prefijo del NIF-IVA (Grecia = `EL`) y `vatNumber` el
número SIN prefijo.

Robustez (VIES se cae y va lento a menudo): timeout corto, un solo intento,
y CUALQUIER fallo (red, 5xx, JSON raro, estado miembro caído) se traduce a
`desconocido` — nunca a «no válido», nunca una excepción hacia arriba. El
resultado se cachea en proceso (por NIF-IVA) para no revalidar en cada
carga; `force=True` salta la caché («Revalidar en VIES»).

Estados:
- `valido`: VIES dice que el número existe y está activo.
- `no_valido`: VIES responde y dice que NO (o el formato no es válido).
- `desconocido`: VIES no respondió / no pudo comprobarlo.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.integrations.factusol.vat_regime import normalize_vat

logger = logging.getLogger(__name__)

VIES_VALIDO = "valido"
VIES_NO_VALIDO = "no_valido"
VIES_DESCONOCIDO = "desconocido"
VIES_PENDIENTE = "pendiente"
VIES_STATUSES: tuple[str, ...] = (VIES_VALIDO, VIES_NO_VALIDO, VIES_DESCONOCIDO, VIES_PENDIENTE)

DEFAULT_BASE_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api"
DEFAULT_TIMEOUT_SECONDS = 4.0
#: Un resultado firme (válido / no válido) vale un día; uno «desconocido»
#: (VIES caído) se reintenta a los 10 minutos.
CACHE_TTL_SECONDS = 24 * 3600
CACHE_TTL_UNKNOWN_SECONDS = 600

#: `userError` de VIES que significan «el número no es válido» (respuesta
#: firme), frente a los que significan «no he podido comprobarlo».
_INVALID_ERRORS = frozenset({"INVALID_INPUT", "INVALID"})
_OK_ERRORS = frozenset({"VALID", "", "NONE"})


@dataclass(frozen=True)
class ViesResult:
    status: str
    valid: bool | None
    vat: str
    country_code: str
    number: str
    name: str | None = None
    address: str | None = None
    error: str | None = None
    checked_at: datetime | None = None
    from_cache: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "valid": self.valid, "vat": self.vat,
            "country_code": self.country_code, "number": self.number,
            "name": self.name, "address": self.address, "error": self.error,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "from_cache": self.from_cache,
        }


def split_vat(vat: Any) -> tuple[str, str] | None:
    """`FR16339753527` → `("FR", "16339753527")`; None si no tiene forma de
    NIF-IVA intracomunitario."""
    number = normalize_vat(vat)
    if not number:
        return None
    return number[:2], number[2:]


# --- caché en proceso -----------------------------------------------------------

_cache: dict[str, tuple[float, ViesResult]] = {}
_cache_lock = threading.Lock()


def _cache_get(vat: str) -> ViesResult | None:
    with _cache_lock:
        hit = _cache.get(vat)
        if not hit:
            return None
        expires, result = hit
        if expires < time.monotonic():
            _cache.pop(vat, None)
            return None
        return result


def _cache_put(result: ViesResult) -> None:
    ttl = CACHE_TTL_UNKNOWN_SECONDS if result.status == VIES_DESCONOCIDO else CACHE_TTL_SECONDS
    with _cache_lock:
        _cache[result.vat] = (time.monotonic() + ttl, result)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# --- llamada -----------------------------------------------------------------------

#: Firma del transporte inyectable (tests): `(url, json) -> (status_code, body)`.
Transport = Callable[[str, dict[str, Any]], tuple[int, Any]]


def _http_transport(timeout: float) -> Transport:
    def call(url: str, payload: dict[str, Any]) -> tuple[int, Any]:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload, headers={"Accept": "application/json"})
            try:
                body = r.json()
            except ValueError:
                body = None
            return r.status_code, body
    return call


def check_vat(
    vat: Any, *, force: bool = False, base_url: str | None = None,
    timeout: float | None = None, transport: Transport | None = None,
) -> ViesResult:
    """Valida el NIF-IVA en VIES. Nunca lanza: cualquier fallo es
    `desconocido` con su motivo en `error`. Cacheado por NIF-IVA salvo
    `force`."""
    parts = split_vat(vat)
    if parts is None:
        raw = str(vat or "").strip()
        return ViesResult(
            status=VIES_NO_VALIDO, valid=False, vat=raw, country_code="", number="",
            error="formato de NIF-IVA no válido", checked_at=datetime.now(UTC),
        )
    country, number = parts
    normalized = country + number
    if not force:
        cached = _cache_get(normalized)
        if cached is not None:
            return ViesResult(**{**cached.__dict__, "from_cache": True})

    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/check-vat-number"
    call = transport or _http_transport(timeout or DEFAULT_TIMEOUT_SECONDS)
    now = datetime.now(UTC)
    try:
        status_code, body = call(url, {"countryCode": country, "vatNumber": number})
    except Exception as exc:  # noqa: BLE001 — red, timeout, DNS…
        result = ViesResult(
            status=VIES_DESCONOCIDO, valid=None, vat=normalized, country_code=country,
            number=number, error=f"VIES no responde: {type(exc).__name__}: {str(exc)[:120]}",
            checked_at=now,
        )
        logger.info("vies %s: %s", normalized, result.error)
        _cache_put(result)
        return result

    result = _interpret(status_code, body, normalized, country, number, now)
    (logger.info if result.status != VIES_DESCONOCIDO else logger.warning)(
        "vies %s → %s%s", normalized, result.status,
        f" ({result.error})" if result.error else "",
    )
    _cache_put(result)
    return result


def _interpret(
    status_code: int, body: Any, vat: str, country: str, number: str, now: datetime,
) -> ViesResult:
    if status_code != 200 or not isinstance(body, dict):
        return ViesResult(
            status=VIES_DESCONOCIDO, valid=None, vat=vat, country_code=country,
            number=number, error=f"VIES HTTP {status_code}", checked_at=now,
        )
    user_error = str(body.get("userError") or "").strip().upper()
    valid = body.get("valid")
    if valid is True:
        return ViesResult(
            status=VIES_VALIDO, valid=True, vat=vat, country_code=country, number=number,
            name=_clean(body.get("name")), address=_clean(body.get("address")),
            checked_at=now,
        )
    if user_error in _OK_ERRORS or user_error in _INVALID_ERRORS:
        # VIES respondió con criterio: el número no existe / no está activo /
        # no tiene un formato válido.
        return ViesResult(
            status=VIES_NO_VALIDO, valid=False, vat=vat, country_code=country,
            number=number, error=user_error or None, checked_at=now,
        )
    # MS_UNAVAILABLE, SERVICE_UNAVAILABLE, TIMEOUT, *_MAX_CONCURRENT_REQ,
    # IP_BLOCKED, VAT_BLOCKED… — no se ha podido comprobar.
    return ViesResult(
        status=VIES_DESCONOCIDO, valid=None, vat=vat, country_code=country,
        number=number, error=user_error or "respuesta sin veredicto", checked_at=now,
    )


def _clean(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    if not text or text == "---":
        return None
    return text[:500]
