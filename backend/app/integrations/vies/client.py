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

Cómo se lee la respuesta (confirmado con la respuesta real del VPS, VAT
FR90501738249: `{"actionSucceed": false, "errorWrappers": [{"error":
"MS_MAX_CONCURRENT_REQ"}]}` — Francia limitando peticiones, NO un veredicto):

- `valid: true` (sin errores) → `valido`.
- `actionSucceed: true` + `valid: false` → `no_valido`. Es el ÚNICO caso de
  VAT no dado de alta. (Sin `actionSucceed` en la respuesta, solo cuenta
  `valid: false` con `userError: "VALID"` explícito: la respuesta clásica
  «petición correcta, número no registrado».)
- `actionSucceed: false`, cualquier `errorWrappers` o `userError` de error
  (`MS_MAX_CONCURRENT_REQ`, `GLOBAL_MAX_CONCURRENT_REQ`, `MS_UNAVAILABLE`,
  `SERVICE_UNAVAILABLE`, `TIMEOUT`, `VAT_BLOCKED`, `IP_BLOCKED`,
  `INVALID_REQUESTER_INFO`, `INVALID_INPUT`…), HTTP ≠ 200, cuerpo raro o sin
  veredicto → `desconocido` («pendiente de validar»): NUNCA «no válido», no
  bloquea, el régimen sigue por país + NIF-IVA y se reintenta más tarde.

Robustez (VIES se cae y va lento a menudo): timeout corto; ante limitación
de ritmo (`*_MAX_CONCURRENT_REQ*`) se reintenta con una pequeña espera (2
reintentos) antes de quedarse en `desconocido`; CUALQUIER fallo (red, 5xx,
JSON raro, estado miembro caído) se traduce a `desconocido` — nunca una
excepción hacia arriba. El resultado se cachea en proceso (por NIF-IVA) para
no revalidar en cada carga (los `desconocido` caducan pronto); `force=True`
salta la caché («Revalidar en VIES»).

Estados:
- `valido`: VIES dice que el número existe y está activo.
- `no_valido`: VIES responde con éxito y dice que NO está dado de alta (o el
  número ni siquiera tiene forma de NIF-IVA: no se llama).
- `desconocido`: VIES no respondió / no pudo comprobarlo / error temporal.
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

#: Valores de `userError` que NO son un error («petición correcta»).
_NO_ERROR = frozenset({"", "VALID", "NONE"})
#: Errores de limitación de ritmo de VIES: se reintenta con espera.
RATE_LIMIT_ERRORS = frozenset({
    "MS_MAX_CONCURRENT_REQ", "MS_MAX_CONCURRENT_REQ_TIME",
    "GLOBAL_MAX_CONCURRENT_REQ", "GLOBAL_MAX_CONCURRENT_REQ_TIME",
})
#: Esperas (segundos) antes de cada reintento por limitación de ritmo.
RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0)


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
    #: Códigos de error que devolvió VIES (`errorWrappers` / `userError`).
    error_codes: tuple[str, ...] = ()

    @property
    def rate_limited(self) -> bool:
        return any(code in RATE_LIMIT_ERRORS for code in self.error_codes)

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
    transport: Transport | None = None, sleeper: Callable[[float], None] | None = None,
) -> ViesResult:
    """Valida el NIF-IVA en VIES. Nunca lanza: cualquier fallo es
    `desconocido` con su motivo en `error`. Cacheado por NIF-IVA normalizado
    (país + número sin prefijo) salvo `force`, así la revalidación y la
    comprobación al cargar la ficha comparten la misma entrada. Ante
    limitación de ritmo (`*_MAX_CONCURRENT_REQ*`) reintenta con espera
    (`RETRY_DELAYS`; `sleeper` inyectable en tests)."""
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
    wait = sleeper or time.sleep
    payload = {"countryCode": country, "vatNumber": number}
    attempt = 0
    while True:
        now = datetime.now(UTC)
        try:
            status_code, body = call(url, payload)
        except Exception as exc:  # noqa: BLE001 — red, timeout, DNS…
            result = ViesResult(
                status=VIES_DESCONOCIDO, valid=None, vat=normalized, country_code=country,
                number=number, checked_at=now,
                error=f"VIES no responde: {type(exc).__name__}: {str(exc)[:120]}",
            )
            logger.warning("vies %s (enviado %s): %s", normalized, payload, result.error)
            _cache_put(result)
            return result

        result = _interpret(status_code, body, normalized, country, number, now)
        if result.rate_limited and attempt < len(RETRY_DELAYS):
            # Limitación de ritmo del estado miembro / global: no es un
            # veredicto; se espera un poco y se vuelve a preguntar.
            delay = RETRY_DELAYS[attempt]
            attempt += 1
            logger.info("vies %s: %s → reintento %d en %.1fs", normalized, result.error,
                        attempt, delay)
            wait(delay)
            continue
        break

    if result.status == VIES_VALIDO:
        logger.info("vies %s → valido (enviado %s)", normalized, payload)
    else:
        # Sin veredicto positivo se deja constancia de QUÉ se envió y QUÉ
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


def _as_bool(value: Any) -> bool | None:
    """`true`/`false` como booleano o como texto; None si no viene."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower() if value is not None else ""
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _error_codes(body: dict[str, Any]) -> tuple[str, ...]:
    """Códigos de error de la respuesta: `errorWrappers: [{error: …}]` y un
    `userError` que no sea «VALID»."""
    codes: list[str] = []
    wrappers = body.get("errorWrappers") or []
    if isinstance(wrappers, dict):
        wrappers = [wrappers]
    if isinstance(wrappers, list):
        for wrapper in wrappers:
            raw = wrapper.get("error") if isinstance(wrapper, dict) else wrapper
            code = str(raw or "").strip().upper()
            if code and code not in codes:
                codes.append(code)
    user_error = str(body.get("userError") or "").strip().upper()
    if user_error not in _NO_ERROR and user_error not in codes:
        codes.append(user_error)
    return tuple(codes)


def interpret_response(
    status_code: int, body: Any, vat: str, country: str, number: str, now: datetime,
) -> ViesResult:
    """Lee la respuesta de VIES según la regla de la cabecera del módulo:
    solo `valid: true` es válido y solo `actionSucceed: true` + `valid: false`
    es no válido; cualquier error / respuesta sin veredicto es `desconocido`."""
    if status_code != 200 or not isinstance(body, dict):
        return ViesResult(
            status=VIES_DESCONOCIDO, valid=None, vat=vat, country_code=country,
            number=number, error=f"VIES HTTP {status_code}", checked_at=now,
        )
    codes = _error_codes(body)
    action = _as_bool(body.get("actionSucceed"))
    valid = _as_bool(body.get("valid"))
    if codes or action is False:
        # MS_MAX_CONCURRENT_REQ, GLOBAL_MAX_CONCURRENT_REQ, MS_UNAVAILABLE,
        # SERVICE_UNAVAILABLE, TIMEOUT, VAT_BLOCKED, IP_BLOCKED,
        # INVALID_REQUESTER_INFO… — VIES no ha podido comprobarlo: NO es un
        # veredicto sobre el número.
        return ViesResult(
            status=VIES_DESCONOCIDO, valid=None, vat=vat, country_code=country,
            number=number, error=", ".join(codes) or "actionSucceed=false",
            checked_at=now, error_codes=codes,
        )
    if valid is True:
        return ViesResult(
            status=VIES_VALIDO, valid=True, vat=vat, country_code=country, number=number,
            name=_clean(body.get("name")), address=_clean(body.get("address")),
            checked_at=now,
        )
    user_error = str(body.get("userError") or "").strip().upper()
    if valid is False and (action is True or (action is None and user_error == "VALID")):
        # Petición correcta y el número NO está dado de alta: el único
        # veredicto de «no válido».
        return ViesResult(
            status=VIES_NO_VALIDO, valid=False, vat=vat, country_code=country,
            number=number, error="VIES: NIF-IVA no dado de alta", checked_at=now,
        )
    return ViesResult(
        status=VIES_DESCONOCIDO, valid=None, vat=vat, country_code=country,
        number=number, error="respuesta sin veredicto", checked_at=now,
    )


_interpret = interpret_response


def _clean(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    if not text or text == "---":
        return None
    return text[:500]


def _main(argv: list[str], out: Callable[[str], None] = print) -> int:
    """Diagnóstico desde el servidor (solo lectura, no toca la BD):

        python -m app.integrations.vies.client FR90501738249
        python -m app.integrations.vies.client 90501738249 FR
        python -m app.integrations.vies.client FR90501738249 --interpret '{"actionSucceed":false,…}'

    Enseña exactamente qué se envía a VIES, qué contesta (HTTP + cuerpo,
    con los reintentos por limitación de ritmo) y cómo lo interpreta BoHub.
    Con `--interpret <json>` no llama a VIES: interpreta ese cuerpo."""
    import json  # noqa: PLC0415

    args = list(argv)
    body_given: Any = None
    if "--interpret" in args:
        i = args.index("--interpret")
        if i + 1 >= len(args):
            out("uso: … --interpret '<json de la respuesta>'")
            return 2
        body_given = json.loads(args[i + 1])
        del args[i:i + 2]
    if not args:
        out("uso: python -m app.integrations.vies.client <NIF-IVA> [país ISO2] "
            "[--interpret '<json>']")
        return 2
    vat, country = args[0], (args[1] if len(args) > 1 else None)
    parts = vies_request_parts(vat, country_code=country)
    out(f"entrada: {vat!r} país={country!r}")
    if parts is None:
        out("→ formato de NIF-IVA no válido (no se llama a VIES)")
        return 1
    payload = {"countryCode": parts[0], "vatNumber": parts[1]}
    url = f"{DEFAULT_BASE_URL}/check-vat-number"
    out(f"POST {url}\n{json.dumps(payload)}")
    if body_given is not None:
        exchanges = [(200, body_given)]
        out("(sin llamar a VIES: se interpreta el cuerpo dado)")
    else:
        exchanges = []
        call = _http_transport(DEFAULT_TIMEOUT_SECONDS * 3)

        def recording(url_: str, payload_: dict[str, Any]) -> tuple[int, Any]:
            status_code_, body_ = call(url_, payload_)
            exchanges.append((status_code_, body_))
            return status_code_, body_

        result = check_vat(vat, country_code=country, force=True, transport=recording)
        if not exchanges:
            out(f"→ sin respuesta: {result.error}")
            return 1
    for n, (status_code, body) in enumerate(exchanges, 1):
        label = f" (intento {n})" if len(exchanges) > 1 else ""
        out(f"HTTP {status_code}{label}\n{json.dumps(body, indent=2, ensure_ascii=False)}")
    status_code, body = exchanges[-1]
    result = interpret_response(status_code, body, parts[0] + parts[1], parts[0], parts[1],
                                datetime.now(UTC))
    out(f"→ BoHub: {result.status}" + (f" ({result.error})" if result.error else ""))
    if result.status == VIES_DESCONOCIDO:
        out("   = pendiente de validar: no bloquea, el régimen sigue por país + NIF-IVA "
            "y se reintenta más tarde")
    return 0 if result.status == VIES_VALIDO else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
