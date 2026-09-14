"""Cliente VIES (Fase VIES del rediseño de flujo).

Valida un NIF-IVA intracomunitario contra el servicio oficial de la UE, la
API REST pública y gratuita de VIES:

    POST {base_url}/check-vat-number
    {"countryCode": "FR", "vatNumber": "16339753527"}
    → {"valid": true, "name": "...", "address": "...", "requestDate": ...,
       "userError": "VALID" | "INVALID_INPUT" | "MS_UNAVAILABLE" | ...}

`countryCode` es el prefijo del NIF-IVA (Grecia = `EL`) y `vatNumber` el
número SIN prefijo. El API NO perdona el prefijo dentro del número (la web
de VIES lo quita sola y avisa; el API responde «no válido»), así que aquí se
normaliza SIEMPRE antes de llamar (`vies_request_parts`): mayúsculas, sin
espacios / puntos / guiones, y sin las 2 letras del país si el número
empieza por ellas (`FR90501738249` → `FR` + `90501738249`; `ESB12345678` →
`ES` + `B12345678`; `B12345678` con país `ES` → tal cual).

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
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.integrations.factusol.vat_regime import _VAT_PREFIX_TO_ISO2

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


_NUMBER_RE = re.compile(r"^[A-Z0-9]{2,12}$")


def _country_prefixes(country_code: str) -> tuple[str, ...]:
    """Prefijos con los que puede venir escrito el número de ese país, el
    primero es el `countryCode` que espera VIES (Grecia: `EL`, no `GR`;
    Irlanda del Norte: `XI`)."""
    code = country_code.strip().upper()
    if code in ("GR", "EL"):
        return ("EL", "GR")
    if code in ("GB", "XI"):
        return ("XI", "GB")
    return (code,)


def vies_request_parts(vat: Any, *, country_code: str | None = None) -> tuple[str, str] | None:
    """`(countryCode, vatNumber)` tal como los espera el API de VIES.

    - Mayúsculas, sin espacios, puntos ni guiones.
    - Si se conoce el país (`country_code`) y el número empieza por sus 2
      letras, se quitan: `FR90501738249` → `("FR", "90501738249")`,
      `ESB12345678` → `("ES", "B12345678")`. Si NO empieza por ellas se envía
      tal cual (`B12345678` con `ES` → `("ES", "B12345678")`): el prefijo de
      país no se confunde con la primera letra del propio NIF.
    - Sin país: las 2 primeras letras tienen que ser un prefijo de la UE.
    - None si no queda un número con forma de NIF-IVA (2-12 alfanuméricos).
    """
    raw = re.sub(r"[\s.\-]", "", str(vat or "")).upper()
    if not raw:
        return None
    if country_code and country_code.strip():
        prefixes = _country_prefixes(country_code)
        country = prefixes[0]
        number = raw
        for prefix in prefixes:
            if raw.startswith(prefix):
                number = raw[len(prefix):]
                break
    else:
        country, number = raw[:2], raw[2:]
        if country not in _VAT_PREFIX_TO_ISO2:
            return None
    if not _NUMBER_RE.match(number):
        return None
    return country, number


def split_vat(vat: Any) -> tuple[str, str] | None:
    """`FR16339753527` → `("FR", "16339753527")`; None si no tiene forma de
    NIF-IVA intracomunitario con prefijo de país."""
    return vies_request_parts(vat)


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
    vat: Any, *, country_code: str | None = None, force: bool = False,
    base_url: str | None = None, timeout: float | None = None,
    transport: Transport | None = None,
) -> ViesResult:
    """Valida el NIF-IVA en VIES. Nunca lanza: cualquier fallo es
    `desconocido` con su motivo en `error`. Cacheado por NIF-IVA normalizado
    (país + número sin prefijo) salvo `force`, así la revalidación y la
    comprobación al cargar la ficha comparten la misma entrada."""
    parts = vies_request_parts(vat, country_code=country_code)
    if parts is None:
        raw = str(vat or "").strip()
        logger.info("vies %r (país %r): formato de NIF-IVA no válido", raw, country_code)
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
    payload = {"countryCode": country, "vatNumber": number}
    now = datetime.now(UTC)
    try:
        status_code, body = call(url, payload)
    except Exception as exc:  # noqa: BLE001 — red, timeout, DNS…
        result = ViesResult(
            status=VIES_DESCONOCIDO, valid=None, vat=normalized, country_code=country,
            number=number, error=f"VIES no responde: {type(exc).__name__}: {str(exc)[:120]}",
            checked_at=now,
        )
        logger.warning("vies %s (enviado %s): %s", normalized, payload, result.error)
        _cache_put(result)
        return result

    result = _interpret(status_code, body, normalized, country, number, now)
    if result.status == VIES_VALIDO:
        logger.info("vies %s → valido (enviado %s)", normalized, payload)
    else:
        # Con el veredicto negativo se deja constancia de QUÉ se envió y QUÉ
        # contestó VIES, para poder diagnosticarlo desde el log del servidor.
        logger.warning(
            "vies %s → %s (%s) · enviado %s · respuesta HTTP %s %s",
            normalized, result.status, result.error, payload, status_code, _short(body),
        )
    _cache_put(result)
    return result


def _short(body: Any, limit: int = 400) -> str:
    text = " ".join(str(body).split())
    return text if len(text) <= limit else text[:limit] + "…"


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
    # `true` como booleano o como texto: VIES es un JSON, pero mejor no fiarse.
    if valid is True or str(valid).strip().lower() == "true":
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


def _main(argv: list[str]) -> int:  # pragma: no cover — diagnóstico manual
    """Diagnóstico desde el servidor (solo lectura, no toca la BD):

        python -m app.integrations.vies.client FR90501738249
        python -m app.integrations.vies.client 90501738249 FR

    Enseña exactamente qué se envía a VIES, qué contesta (HTTP + cuerpo) y
    cómo lo interpreta BoHub."""
    import json  # noqa: PLC0415
    import sys  # noqa: PLC0415

    if not argv:
        print("uso: python -m app.integrations.vies.client <NIF-IVA> [país ISO2]")
        return 2
    vat, country = argv[0], (argv[1] if len(argv) > 1 else None)
    parts = vies_request_parts(vat, country_code=country)
    print(f"entrada: {vat!r} país={country!r}")
    if parts is None:
        print("→ formato de NIF-IVA no válido (no se llama a VIES)")
        return 1
    payload = {"countryCode": parts[0], "vatNumber": parts[1]}
    url = f"{DEFAULT_BASE_URL}/check-vat-number"
    print(f"POST {url}\n{json.dumps(payload)}")
    try:
        status_code, body = _http_transport(DEFAULT_TIMEOUT_SECONDS * 3)(url, payload)
    except Exception as exc:  # noqa: BLE001
        print(f"→ sin respuesta: {type(exc).__name__}: {exc}")
        return 1
    print(f"HTTP {status_code}\n{json.dumps(body, indent=2, ensure_ascii=False)}")
    result = _interpret(status_code, body, parts[0] + parts[1], parts[0], parts[1],
                        datetime.now(UTC))
    print(f"→ BoHub: {result.status}" + (f" ({result.error})" if result.error else ""))
    sys.stdout.flush()
    return 0 if result.status == VIES_VALIDO else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
