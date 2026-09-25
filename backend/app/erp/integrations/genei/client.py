"""Cliente síncrono de la API v2 de Genei (agregador de agencias de envío).

Genei se autentica con **email + password** (`POST /login`) y devuelve un
**token Bearer con 15 días de validez**; todas las llamadas van con
`Authorization: Bearer <token>`.

Re-autenticación automática (sin que nadie vuelva a meter la password):
- El token se guarda en una **caché compartida** del proceso (`TokenCache`,
  por credenciales) y se reutiliza entre peticiones hasta poco antes de
  caducar (y como mucho `TOKEN_MAX_AGE_SECONDS`); entonces se pide otro solo.
- Si Genei rechaza el token — `401`/`403` o su envoltorio HTTP 200
  `status:0` con un mensaje de token/sesión caducada —, se hace **login con las
  credenciales guardadas** (cifradas) y se **reintenta la llamada una vez**.
- Si el login falla porque Genei rechaza el usuario/contraseña, se eleva
  `GeneiAuthError` («revisa credenciales de Genei») y durante
  `AUTH_FAILURE_BACKOFF_SECONDS` no se vuelve a intentar con esas mismas
  credenciales (sin bucle ni martilleo a Genei); guardar credenciales o
  «Probar conexión» lo desbloquean al momento.
Password y token **nunca** se registran en el log ni salen en respuestas.

El cliente es síncrono a propósito (igual que `FactusolClient`): vive dentro de
handlers de request o jobs RQ, no en el event loop. Para tests se inyecta un
`transport` de httpx (`MockTransport`) — no sale a red.

Base: `https://apiv2.genei.es` + `/api/v2`. `from_carrier(...)` lo construye a
partir de una fila `Carrier` (credenciales cifradas con Fernet).

PR-1: login + prices + crear/leer/eliminar envío + etiqueta. El **pago** (una
acción humana) y el **webhook** de estados llegan en PR-2 — aquí no se paga ni
se escucha nada.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.core.crypto import DecryptionError, decrypt

if TYPE_CHECKING:  # pragma: no cover - solo para type checking
    from app.erp.models.carriers import Carrier

logger = logging.getLogger(__name__)

#: Sufijo de versión de la API. `Carrier.api_base_url` guarda el host
#: (`https://apiv2.genei.es`); las rutas cuelgan de `<host>/api/v2`.
API_PREFIX = "/api/v2"

#: El token de Genei dura 15 días. Si el JWT no trae `exp` legible, se usa este
#: TTL como respaldo (un poco por debajo de 15 d para renovar con holgura).
TOKEN_FALLBACK_TTL_SECONDS = 14 * 24 * 3600
#: Margen para renovar antes de que caduque de verdad (evita cortar a mitad).
TOKEN_SAFETY_MARGIN_SECONDS = 3600
#: Edad máxima de un token cacheado, diga lo que diga su `exp`: se renueva solo
#: antes, por si Genei lo invalida por su cuenta (sesión) sin avisar con 401.
TOKEN_MAX_AGE_SECONDS = 12 * 3600
#: Tras un login RECHAZADO (usuario/contraseña), no se reintenta con las mismas
#: credenciales durante este tiempo: error claro al momento, sin bucle.
AUTH_FAILURE_BACKOFF_SECONDS = 300

#: Lo que ve la persona cuando Genei rechaza las credenciales guardadas.
AUTH_REJECTED_MESSAGE = (
    "Genei ha rechazado el usuario o la contraseña guardados: revisa las "
    "credenciales de Genei en Ajustes → Envíos."
)

#: HTTP con el que Genei (o su pasarela) rechaza un token no válido/caducado.
_AUTH_HTTP_STATUSES = frozenset({401, 403, 419, 440})
#: Genei responde HTTP 200 también en error (`status:0`); estas pistas en el
#: mensaje indican que lo rechazado es el TOKEN/sesión, no los datos.
_AUTH_HINTS = re.compile(
    r"token|jwt|expir|caduc|unauthori[sz]|unauthenticat|no autori[sz]|autentica|"
    r"authenticat|sesi[oó]n|session|log ?in",
    re.IGNORECASE,
)

DEFAULT_TIMEOUT = 30.0

#: Claves donde puede venir el token según el envoltorio (login).
_TOKEN_KEYS = ("token", "access_token", "accessToken", "jwt", "bearer")
#: Claves donde puede venir el código del envío al crearlo. Al CREAR, Genei
#: devuelve el código en `data.reference` (verificado en vivo); al leer/listar,
#: en `codigo_envio`.
_SHIPMENT_CODE_KEYS = ("reference", "shipmentCode", "shipment_code", "codigo_envio", "code")
#: Claves donde puede venir la URL de pago al crear el envío.
_PAYMENT_URL_KEYS = ("paymentUrl", "payment_url", "url_pago", "urlPago")


class GeneiError(RuntimeError):
    """Error de la API de Genei con contexto (status + cuerpo recortado)."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = (body or "")[:2000]


class GeneiConfigError(GeneiError):
    """Falta configuración para hablar con Genei (credenciales / URL base)."""


class GeneiAuthError(GeneiError):
    """Genei rechaza el usuario/contraseña guardados (el login, no el token).
    El mensaje es para la persona («revisa credenciales de Genei»)."""


def _now_iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def credentials_fingerprint(base_url: str, username: str, password: str) -> str:
    """Clave de la caché de tokens: hash de (URL, usuario, password). Cambiar
    las credenciales = otra clave (token y bloqueo nuevos). Nunca se guarda ni
    se registra la password en claro."""
    raw = "\x1f".join((base_url.rstrip("/"), username, password))
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class _TokenEntry:
    token: str | None = None
    expires_at: float = 0.0
    obtained_at: float = 0.0
    last_login_at: float | None = None
    failure: str | None = None
    failed_at: float | None = None


class TokenCache:
    """Token de Genei compartido entre peticiones (memoria del proceso), por
    credenciales. Guarda también el último login rechazado para no reintentar
    en bucle. Seguro entre hilos (los endpoints síncronos corren en un pool)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, _TokenEntry] = {}
        self._login_locks: dict[str, threading.Lock] = {}

    def _entry(self, key: str) -> _TokenEntry:
        return self._entries.setdefault(key, _TokenEntry())

    def login_lock(self, key: str) -> threading.Lock:
        """Un login a la vez por credenciales (dos peticiones con el token
        caducado no piden dos tokens: la segunda reutiliza el de la primera)."""
        with self._lock:
            return self._login_locks.setdefault(key, threading.Lock())

    def valid_token(self, key: str, now: float | None = None) -> str | None:
        """Token vigente (con margen y edad máxima), o None si toca renovar."""
        now = time.time() if now is None else now
        with self._lock:
            e = self._entries.get(key)
            if e is None or not e.token:
                return None
            renew_at = min(e.expires_at - TOKEN_SAFETY_MARGIN_SECONDS,
                           e.obtained_at + TOKEN_MAX_AGE_SECONDS)
            return e.token if now < renew_at else None

    def expires_at(self, key: str) -> float:
        with self._lock:
            e = self._entries.get(key)
            return e.expires_at if e and e.token else 0.0

    def store(self, key: str, token: str, expires_at: float) -> None:
        now = time.time()
        with self._lock:
            e = self._entry(key)
            e.token, e.expires_at, e.obtained_at = token, expires_at, now
            e.last_login_at = now
            e.failure = e.failed_at = None

    def drop(self, key: str, token: str | None) -> None:
        """Olvida `token` si sigue siendo el cacheado (no pisa uno más nuevo
        que otra petición ya haya pedido)."""
        with self._lock:
            e = self._entries.get(key)
            if e is not None and token and e.token == token:
                e.token = None

    def record_failure(self, key: str, message: str) -> None:
        with self._lock:
            e = self._entry(key)
            e.token = None
            e.failure, e.failed_at = message, time.time()

    def recent_failure(self, key: str, now: float | None = None) -> str | None:
        """Mensaje del último login rechazado si aún está en el bloqueo."""
        now = time.time() if now is None else now
        with self._lock:
            e = self._entries.get(key)
            if e is None or not e.failure or e.failed_at is None:
                return None
            if now - e.failed_at >= AUTH_FAILURE_BACKOFF_SECONDS:
                return None
            return e.failure

    def clear_failure(self, key: str) -> None:
        with self._lock:
            e = self._entries.get(key)
            if e is not None:
                e.failure = e.failed_at = None

    def status(self, key: str) -> dict[str, Any]:
        """Estado de la conexión para Ajustes. NUNCA incluye el token."""
        with self._lock:
            e = self._entries.get(key) or _TokenEntry()
            if e.failure:
                state = "error"
            elif e.token:
                state = "ok"
            else:
                state = "unknown"
            return {
                "state": state,
                "token_valid_until": _now_iso(e.expires_at) if e.token else None,
                "last_login_at": _now_iso(e.last_login_at),
                "last_error": e.failure,
                "last_error_at": _now_iso(e.failed_at),
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._login_locks.clear()


#: Caché del proceso que usan los clientes construidos desde el carrier
#: (`from_carrier`): el token sobrevive entre peticiones.
SHARED_TOKEN_CACHE = TokenCache()


@dataclass(frozen=True)
class GeneiLabel:
    """Etiqueta de un envío: bytes ya decodificados + su tipo (`pdf`/`zpl`)."""

    content: bytes
    kind: str  # "pdf" | "zpl" | "unknown"
    filename: str

    @property
    def mime_type(self) -> str:
        return {"pdf": "application/pdf", "zpl": "application/octet-stream"}.get(
            self.kind, "application/octet-stream"
        )


def _jwt_exp(token: str) -> float | None:
    """`exp` (epoch) del payload del JWT, sin verificar la firma. Solo se usa
    para saber cuándo renovar; si no es un JWT legible, devuelve None."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = data.get("exp")
        return float(exp) if exp is not None else None
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _first_str(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Primer valor de texto no vacío entre `keys` (mirando también en `data`
    anidado, que es donde Genei mete a veces el detalle)."""
    for scope in (data, data.get("data") if isinstance(data.get("data"), dict) else None):
        if not isinstance(scope, dict):
            continue
        for key in keys:
            val = scope.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, (int, float)):
                return str(val)
    return None


def _package_query(packages: list[dict[str, Any]]) -> dict[str, Any]:
    """Serializa `packages[]` para la query de `/agencies/prices`: cada bulto
    como `packages[i][height|width|length|weight|isBox]`. `isBox` es OBLIGATORIO
    (bool; default False = bulto normal, no una caja registrada de Genei). Sin
    él, Genei devuelve «Invalid bultos array format» y cero agencias."""
    out: dict[str, Any] = {}
    for i, pkg in enumerate(packages):
        is_box = bool(pkg.get("isBox", pkg.get("is_box", False)))
        item = {
            "height": pkg.get("height", 0),
            "width": pkg.get("width", 0),
            "length": pkg.get("length", 0),
            "weight": pkg.get("weight", 0),
            "isBox": "true" if is_box else "false",
        }
        for key, val in item.items():
            out[f"packages[{i}][{key}]"] = val
    return out


def _envelope_error(data: Any) -> str | None:
    """Genei responde HTTP 200 tanto en éxito (`status:1`) como en ERROR
    (`status:0`, `message:"Error validacion"`, `errors:[...]`). Devuelve el
    mensaje de error si lo es; None si es una respuesta buena."""
    if not isinstance(data, dict):
        return None
    errors = data.get("errors")
    if data.get("status") == 0 or (isinstance(errors, list) and errors):
        msgs = [str(m) for m in (errors or []) if m] or [str(data.get("message") or "")]
        detail = "; ".join(m for m in msgs if m)
        return detail or "error de validación"
    return None


class GeneiClient:
    """Cliente HTTP de Genei v2. `adapter_class` del carrier apunta aquí."""

    def __init__(
        self, *,
        base_url: str,
        username: str,
        password: str,
        default_address_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        token_cache: TokenCache | None = None,
    ):
        if not base_url:
            raise GeneiConfigError("Genei sin URL base configurada (Carrier.api_base_url).")
        if not username or not password:
            raise GeneiConfigError("Genei sin credenciales (email/password) configuradas.")
        self.base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self.default_address_id = default_address_id
        self._timeout = timeout
        self._transport = transport
        # Sin caché explícita, una propia (el token vive lo que el cliente);
        # `from_carrier` usa la compartida del proceso.
        self._cache = token_cache if token_cache is not None else TokenCache()
        self._key = credentials_fingerprint(self.base_url, username, password)
        #: Token con el que va la petición en curso (el de la caché).
        self._token: str | None = None

    # --- construcción -------------------------------------------------------

    @classmethod
    def from_carrier(
        cls, carrier: Carrier, *, transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT, token_cache: TokenCache | None = None,
    ) -> GeneiClient:
        """Construye el cliente desde una fila `Carrier` (Genei). Descifra las
        credenciales (`api_credentials_encrypted`, JSON Fernet `{username,
        password}`) y usa la caché de tokens COMPARTIDA del proceso: el token
        se reutiliza entre peticiones y se renueva solo."""
        creds = cls._decode_credentials(carrier.api_credentials_encrypted)
        return cls(
            base_url=carrier.api_base_url or "",
            username=creds.get("username") or creds.get("email") or "",
            password=creds.get("password") or "",
            default_address_id=carrier.default_address_id,
            timeout=timeout,
            transport=transport,
            token_cache=token_cache if token_cache is not None else SHARED_TOKEN_CACHE,
        )

    @classmethod
    def cache_key_of(cls, carrier: Carrier) -> str | None:
        """Clave de caché de las credenciales guardadas del carrier (o None)."""
        try:
            creds = cls._decode_credentials(carrier.api_credentials_encrypted)
        except GeneiConfigError:
            return None
        username = creds.get("username") or creds.get("email") or ""
        password = creds.get("password") or ""
        if not (carrier.api_base_url and username and password):
            return None
        return credentials_fingerprint(carrier.api_base_url, username, password)

    @classmethod
    def auth_status_of(
        cls, carrier: Carrier | None, *, token_cache: TokenCache | None = None,
    ) -> dict[str, Any]:
        """Estado de la conexión (token vigente / último error) para Ajustes.
        Nunca devuelve el token ni la password."""
        cache = token_cache if token_cache is not None else SHARED_TOKEN_CACHE
        key = cls.cache_key_of(carrier) if carrier else None
        if key is None:
            return TokenCache().status("")
        return cache.status(key)

    @staticmethod
    def _decode_credentials(ciphertext: str | None) -> dict[str, str]:
        """`api_credentials_encrypted` (Fernet de un JSON `{username, password}`)
        → dict. Sin credenciales, error de configuración claro."""
        if not ciphertext:
            raise GeneiConfigError("Genei sin credenciales guardadas (configúralas en Ajustes).")
        try:
            raw = decrypt(ciphertext)
        except DecryptionError as exc:
            raise GeneiConfigError(
                "No se pueden leer las credenciales de Genei guardadas (cambió la clave "
                "de cifrado del servidor): vuelve a guardarlas en Ajustes → Envíos."
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GeneiConfigError("Las credenciales de Genei no son un JSON válido.") from exc
        if not isinstance(data, dict):
            raise GeneiConfigError("Las credenciales de Genei no tienen el formato esperado.")
        return {str(k): str(v) for k, v in data.items() if v is not None}

    @staticmethod
    def encode_credentials(
        username: str, password: str, *, webhook_secret: str | None = None,
    ) -> str:
        """`{username, password[, webhook_secret]}` → ciphertext Fernet listo
        para guardar en el carrier. El `webhook_secret` (PR-2) valida el webhook
        de estados; va cifrado con el resto, nunca en `config_json` en claro."""
        from app.core.crypto import encrypt  # noqa: PLC0415 - evita ciclo al importar

        if not username or not password:
            raise ValueError("Genei necesita email y password.")
        blob: dict[str, str] = {"username": username, "password": password}
        if webhook_secret:
            blob["webhook_secret"] = webhook_secret
        return encrypt(json.dumps(blob))

    @classmethod
    def webhook_secret_of(cls, ciphertext: str | None) -> str | None:
        """Secreto del webhook guardado en las credenciales cifradas, o None."""
        if not ciphertext:
            return None
        try:
            return cls._decode_credentials(ciphertext).get("webhook_secret") or None
        except GeneiConfigError:
            return None

    # --- auth ---------------------------------------------------------------

    def login(self, *, force: bool = False) -> str:
        """`POST /login` con las credenciales guardadas → token Bearer (15 d).
        Lo deja en la caché (compartida si viene de `from_carrier`).

        Si Genei rechaza el usuario/contraseña → `GeneiAuthError` y, durante
        `AUTH_FAILURE_BACKOFF_SECONDS`, las siguientes llamadas fallan al
        momento con el mismo aviso sin volver a Genei. `force=True` («Probar
        conexión») se salta ese bloqueo. Un fallo de red o 5xx no bloquea (es
        pasajero)."""
        if not force:
            failure = self._cache.recent_failure(self._key)
            if failure:
                raise GeneiAuthError(failure, status=None)
        resp = self._raw_request(
            "POST", "/login",
            json={"username": self._username, "password": self._password},
            authed=False,
        )
        rejected = self._login_rejection(resp)
        if rejected is not None:
            message = AUTH_REJECTED_MESSAGE + (f" (Genei: {rejected})" if rejected else "")
            self._cache.record_failure(self._key, message)
            # Solo el motivo de Genei (sin password ni cuerpo completo).
            logger.warning("genei: login rechazado (HTTP %s)%s", resp.status_code,
                           f": {rejected}" if rejected else "")
            raise GeneiAuthError(message, status=resp.status_code)
        if resp.status_code >= 400:
            logger.warning("genei: login falló (HTTP %s)", resp.status_code)
            raise GeneiError(
                f"POST /login → {resp.status_code}",
                status=resp.status_code, body=resp.text,
            )
        token = self._extract_token(resp)
        if not token:
            raise GeneiError(
                "Login Genei sin token en la respuesta.",
                status=resp.status_code, body=resp.text,
            )
        exp = _jwt_exp(token)
        expires_at = exp if exp is not None else time.time() + TOKEN_FALLBACK_TTL_SECONDS
        self._cache.store(self._key, token, expires_at)
        self._token = token
        logger.info("genei: login OK, token válido hasta %s", _now_iso(expires_at))
        return token

    @staticmethod
    def _login_rejection(resp: httpx.Response) -> str | None:
        """¿Genei ha RECHAZADO las credenciales en el login? Devuelve su motivo
        (texto corto, puede ser «»), o None si no es un rechazo (éxito, o fallo
        pasajero 5xx). Genei responde a veces HTTP 200 con `status:0`."""
        if resp.status_code >= 500 or resp.status_code in (404, 405, 408, 409, 429):
            return None
        try:
            data = resp.json()
        except ValueError:
            data = None
        if 400 <= resp.status_code < 500:
            return _short_reason(data)
        err = _envelope_error(data)
        return err[:200] if err is not None else None

    @staticmethod
    def _extract_token(resp: httpx.Response) -> str | None:
        try:
            data = resp.json()
        except ValueError:
            return None
        if isinstance(data, str):
            return data.strip() or None
        if isinstance(data, dict):
            return _first_str(data, _TOKEN_KEYS)
        return None

    def token_valid_seconds(self) -> int:
        """Segundos que le quedan al token cacheado (0 si no hay o caducó)."""
        return max(0, int(self._cache.expires_at(self._key) - time.time()))

    def _ensure_token(self) -> str:
        """Token vigente de la caché; si no hay o toca renovarlo, login (uno a la
        vez por credenciales)."""
        token = self._cache.valid_token(self._key)
        if token is None:
            with self._cache.login_lock(self._key):
                token = self._cache.valid_token(self._key)
                if token is None:
                    token = self.login()
        self._token = token
        return token

    def _renew_after_rejection(self, rejected: str | None) -> None:
        """El token `rejected` ya no vale: se descarta y se obtiene otro con las
        credenciales guardadas (o el que otra petición acabe de pedir)."""
        with self._cache.login_lock(self._key):
            self._cache.drop(self._key, rejected)
            token = self._cache.valid_token(self._key)
            if token is None or token == rejected:
                token = self.login()
        self._token = token

    # --- operaciones --------------------------------------------------------

    def agency_prices(
        self, *,
        is_warehouse: bool,
        iso_country_origin: str,
        iso_country_destination: str,
        postal_code_origin: str | None = None,
        postal_code_destination: str | None = None,
        town_origin: str | None = None,
        town_destination: str | None = None,
        packages: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """`GET /agencies/prices` — agencias/tarifas factibles para esos datos.

        Devuelve la lista de agencias (cada una con su `agencyId`, precio y
        nombre). El comparador de la Cola SAT elige de aquí."""
        params: dict[str, Any] = {
            "isWarehouse": str(bool(is_warehouse)).lower(),
            "isoCountryOrigin": iso_country_origin,
            "isoCountryDestination": iso_country_destination,
        }
        if postal_code_origin:
            params["postalCodeOrigin"] = postal_code_origin
        if postal_code_destination:
            params["postalCodeDestination"] = postal_code_destination
        if town_origin:
            params["townOrigin"] = town_origin
        if town_destination:
            params["townDestination"] = town_destination
        # `packages[]` va como ARRAY en la query (notación bracket), con cada
        # bulto {height,width,length,weight,isBox}. FALTABA `isBox`: sin él,
        # Genei responde HTTP 200 con «Invalid bultos array format» y CERO
        # agencias (el bug de producción). Verificado en vivo contra la API real.
        params.update(_package_query(packages or []))
        data = self._request("GET", "/agencies/prices", params=params)
        return _as_list(data)

    def create_shipment(self, payload: dict[str, Any]) -> dict[str, Any]:
        """`POST /shipments` — crea el envío con `agencyId` factible.

        El `payload` lo compone el servicio (origin/destination/packagesArray,
        `externalShippingCode`=nº pedido, `notificationUrl`=webhook…). La
        respuesta trae `shipmentCode` y `paymentUrl`; el envío nace en estado 7
        (pendiente de pago). Aquí NO se paga."""
        data = self._request("POST", "/shipments", json=payload)
        return _as_dict(data)

    def get_shipment(self, shipment_code: str) -> dict[str, Any]:
        """`GET /shipments/{code}` — datos completos (estado, tracking…)."""
        data = self._request("GET", f"/shipments/{shipment_code}")
        return _as_dict(data)

    def get_tracking(self, shipment_code: str) -> dict[str, Any]:
        """`GET /shipments/{code}/tracking` — historial DETALLADO: los eventos
        del propio transportista (`data.estadosAgencia[]`: fecha, código y
        descripción tal cual los da CTT/UPS/GLS…), los estados internos de
        Genei (`data.estadosInternos[]`) y la URL de seguimiento de la agencia
        (`message`). Se devuelve el envoltorio entero (la URL va fuera de
        `data`); lo interpreta `tracking.summarize_tracking`."""
        data = self._request("GET", f"/shipments/{shipment_code}/tracking")
        return data if isinstance(data, dict) else {}

    def get_tracking_url(self, shipment_code: str) -> str | None:
        """`GET /shipments/{code}/tracking/url` — la web de seguimiento de la
        agencia para ese envío (`data.webSeguimiento`), o None."""
        data = self._request("GET", f"/shipments/{shipment_code}/tracking/url")
        inner = data.get("data") if isinstance(data, dict) else None
        url = inner.get("webSeguimiento") if isinstance(inner, dict) else None
        return url.strip() if isinstance(url, str) and url.strip().startswith(
            ("http://", "https://")) else None

    #: Pasarela de pago de Genei: 4 = SALDO/CRÉDITO (la que usa BoHub). El
    #: endpoint `pay/transactions` es RESTful (ejecuta el pago) SOLO con saldo;
    #: tarjeta/PSD2 serían una URL de redirección.
    PAYMENT_GATEWAY_BALANCE = 4

    def payment_token(self, gateway: int = PAYMENT_GATEWAY_BALANCE) -> str:
        """`GET /payments/token?pg=<gateway>` — JWT fresco para el pago. El token
        caduca (~2 h), así que se pide uno nuevo justo antes de pagar en vez de
        reutilizar el de la creación."""
        data = self._request("GET", "/payments/token", params={"pg": gateway})
        token = data.get("data") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise GeneiError("Genei no devolvió un token de pago.", status=200)
        return token.strip()

    def pay_transaction(self, transaction_id: str) -> dict[str, Any]:
        """`GET /payments/pay/transactions/{id}?payment_token=…` — EJECUTA el
        pago de la transacción contra el SALDO de la cuenta (pg=4). MUEVE DINERO
        REAL: solo se llama desde el botón «Pagar y tramitar» (acción humana).

        El token se pide fresco cada vez (evita el caducado de la creación). Un
        rechazo (sin saldo, transacción ya pagada…) llega como envoltorio
        `status:0` y se eleva como `GeneiError` con el mensaje de Genei."""
        token = self.payment_token(self.PAYMENT_GATEWAY_BALANCE)
        # Reintento SOLO ante un 401 HTTP (como siempre): un `status:0` que hable
        # de «token» aquí se refiere al token de PAGO, no al de sesión.
        data = self._request(
            "GET", f"/payments/pay/transactions/{transaction_id}",
            params={"payment_token": token}, auth_statuses=frozenset({401}),
            auth_envelope=False,
        )
        return _as_dict(data)

    def get_address(self, address_id: str) -> dict[str, Any]:
        """`GET /addresses/{id}` — dirección registrada en Genei (el remitente
        por defecto de la cuenta). Se usa para componer el bloque `origin` del
        envío a partir del `originAddressId` configurado, sin duplicar el dato."""
        data = self._request("GET", f"/addresses/{address_id}")
        return _as_dict(data)

    def delete_shipment(self, shipment_code: str) -> dict[str, Any]:
        """`DELETE /shipments/{code}` — elimina (prueba) o cancela mientras no
        haya pasado a tránsito."""
        data = self._request("DELETE", f"/shipments/{shipment_code}")
        return _as_dict(data)

    def get_label(self, shipment_code: str) -> GeneiLabel:
        """`GET /shipments/{code}/label` — etiqueta (PDF/ZPL, base64 o binaria).

        Devuelve los bytes ya decodificados. Genei puede mandar la etiqueta como
        binario (PDF directo) o dentro de un JSON con la cadena en base64."""
        resp = self._send("GET", f"/shipments/{shipment_code}/label")
        if resp.status_code >= 400:
            raise GeneiError(
                f"GET /shipments/{shipment_code}/label → {resp.status_code}",
                status=resp.status_code, body=resp.text,
            )
        return _parse_label(resp, shipment_code)

    # --- transporte ---------------------------------------------------------

    def _send(
        self, method: str, path: str, *,
        json: dict[str, Any] | None = None, params: dict[str, Any] | None = None,
        auth_statuses: frozenset[int] = _AUTH_HTTP_STATUSES, auth_envelope: bool = True,
    ) -> httpx.Response:
        """Petición autenticada con **re-autenticación automática**: si Genei
        rechaza el token (HTTP en `auth_statuses` o, con `auth_envelope`, un
        `status:0` que habla de token/sesión), se hace login con las credenciales
        guardadas y se reintenta UNA vez. Devuelve la respuesta final."""
        self._ensure_token()
        resp = self._raw_request(method, path, json=json, params=params, authed=True)
        reason = _token_rejection(resp, auth_statuses, auth_envelope)
        if reason is None:
            return resp
        rejected = self._token
        logger.warning("genei %s %s: token rechazado (%s) → login y reintento",
                       method, path, reason)
        self._renew_after_rejection(rejected)
        resp = self._raw_request(method, path, json=json, params=params, authed=True)
        if _token_rejection(resp, auth_statuses, auth_envelope) is not None:
            # Token recién obtenido y aún rechazado: no se insiste (sin bucle).
            self._cache.drop(self._key, self._token)
            logger.warning("genei %s %s: token nuevo también rechazado", method, path)
        return resp

    def _request(
        self, method: str, path: str, *,
        json: dict[str, Any] | None = None, params: dict[str, Any] | None = None,
        auth_statuses: frozenset[int] = _AUTH_HTTP_STATUSES, auth_envelope: bool = True,
    ) -> Any:
        """Petición autenticada (con re-autenticación, ver `_send`) y parseo
        JSON. Los errores de Genei se elevan como `GeneiError` con el cuerpo."""
        # Log mínimo (sin token ni cuerpo con datos personales) para no depurar
        # a ciegas: método + ruta + claves de query.
        logger.info("genei %s %s%s", method, path,
                    f" params={sorted(params)}" if params else "")
        resp = self._send(method, path, json=json, params=params,
                          auth_statuses=auth_statuses, auth_envelope=auth_envelope)
        if resp.status_code >= 400:
            logger.warning("genei %s %s → HTTP %s: %s", method, path,
                           resp.status_code, resp.text[:300])
            raise GeneiError(
                f"{method} {path} → {resp.status_code}: {resp.text[:500]}",
                status=resp.status_code, body=resp.text,
            )
        if not resp.content:
            return {}
        try:
            data = resp.json()
        except ValueError as exc:
            raise GeneiError(
                f"{method} {path} → respuesta no-JSON de Genei",
                status=resp.status_code, body=resp.text,
            ) from exc
        # Genei devuelve HTTP 200 tanto en éxito (`status:1`) como en ERROR
        # (`status:0`, `message:"Error validacion"`, `errors:[...]`). Sin
        # esto, un error se colaba como lista vacía (el «0 agencias siempre»).
        err = _envelope_error(data)
        if err is not None:
            logger.warning("genei %s %s → status:0 %s", method, path, err[:300])
            raise GeneiError(f"{method} {path} → Genei rechazó: {err}",
                             status=resp.status_code, body=resp.text)
        return data

    def _raw_request(
        self, method: str, path: str, *,
        json: dict[str, Any] | None = None, params: dict[str, Any] | None = None,
        authed: bool = True,
    ) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        if authed and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self.base_url}{API_PREFIX}{path}"
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                return client.request(method, url, json=json, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning("genei %s %s: timeout", method, path)
            raise GeneiError("Genei no responde ahora mismo (tiempo de espera agotado): "
                             "prueba en un momento.") from exc
        except httpx.HTTPError as exc:
            # Sin la URL completa (lleva query) ni cabeceras (llevan el token).
            logger.warning("genei %s %s: error de conexión (%s)", method, path,
                           type(exc).__name__)
            raise GeneiError("No se pudo conectar con Genei: prueba en un momento.") from exc


def _short_reason(data: Any) -> str:
    """Motivo corto que da Genei en un cuerpo de error (`message`, `error`,
    `errors`…), o «» si no hay uno legible."""
    if isinstance(data, dict):
        for key in ("message", "error", "msg", "detail", "mensaje"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:200]
        errs = data.get("errors")
        if isinstance(errs, list) and errs:
            return "; ".join(str(e) for e in errs[:3])[:200]
    if isinstance(data, str):
        return data.strip()[:200]
    return ""


def _token_rejection(
    resp: httpx.Response, statuses: frozenset[int], envelope: bool,
) -> str | None:
    """¿Genei rechaza el TOKEN (caducado/no válido)? Motivo corto, o None.

    - HTTP en `statuses` (401/403…): siempre es rechazo de token.
    - Con `envelope`: HTTP 200 `status:0` (el error «normal» de Genei) o un 4xx
      cuyo mensaje habla de token/sesión/autenticación. Un error de datos
      («agencia no factible», «Invalid bultos…») NO cuenta."""
    if resp.status_code in statuses:
        return f"HTTP {resp.status_code}"
    if not envelope or not (resp.status_code == 200 or 400 <= resp.status_code < 500):
        return None
    if "json" not in resp.headers.get("content-type", "").lower():
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    text = _envelope_error(data) if resp.status_code == 200 else _short_reason(data)
    if text and _AUTH_HINTS.search(text):
        return text[:120]
    return None


# --- helpers de parseo de respuesta -----------------------------------------


def _as_list(data: Any) -> list[dict[str, Any]]:
    """Normaliza la respuesta a `list[dict]` tanto si Genei devuelve la lista
    pelada como si la envuelve en `{data: [...]}` / `{result: [...]}`."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "result", "results", "agencies", "prices", "items"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    return []


def _as_dict(data: Any) -> dict[str, Any]:
    """Normaliza a `dict`; si viene envuelto en `{data: {...}}`, lo desenvuelve
    pero conserva `paymentUrl`/`shipmentCode` del nivel superior si están."""
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            merged = {**inner}
            for k in (*_SHIPMENT_CODE_KEYS, *_PAYMENT_URL_KEYS):
                if k in data and k not in merged:
                    merged[k] = data[k]
            return merged
        return data
    return {}


def shipment_code_of(created: dict[str, Any]) -> str | None:
    """Código del envío en la respuesta de creación (tolerante al naming)."""
    return _first_str(created, _SHIPMENT_CODE_KEYS)


def payment_url_of(created: dict[str, Any]) -> str | None:
    """URL de pago en la respuesta de creación (se usa en PR-2, acción humana)."""
    return _first_str(created, _PAYMENT_URL_KEYS)


def _parse_label(resp: httpx.Response, shipment_code: str) -> GeneiLabel:
    """Convierte la respuesta de `/label` en `GeneiLabel` (bytes + tipo)."""
    ctype = resp.headers.get("content-type", "").lower()
    filename = f"Etiqueta_{shipment_code}"
    # 1) JSON con la etiqueta en base64 (campo `etiqueta`/`label`/`content`).
    if "application/json" in ctype:
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            b64 = _first_str(data, ("etiqueta", "label", "content", "pdf", "base64"))
            fmt = (_first_str(data, ("format", "tipo", "type")) or "").lower()
            if b64:
                content = _decode_b64(b64)
                kind = "zpl" if "zpl" in fmt else "pdf"
                return GeneiLabel(content=content, kind=kind, filename=f"{filename}.{_ext(kind)}")
    # 2) Binario directo (PDF) o texto (ZPL).
    raw = resp.content
    if raw[:4] == b"%PDF":
        return GeneiLabel(content=raw, kind="pdf", filename=f"{filename}.pdf")
    text = raw[:64].lstrip()
    if text[:2] in (b"^X", b"^F") or b"^XA" in raw[:16]:
        return GeneiLabel(content=raw, kind="zpl", filename=f"{filename}.zpl")
    # 3) Último recurso: puede ser base64 en texto plano.
    try:
        decoded = _decode_b64(raw.decode("ascii", "strict"))
        if decoded[:4] == b"%PDF":
            return GeneiLabel(content=decoded, kind="pdf", filename=f"{filename}.pdf")
    except (ValueError, UnicodeDecodeError):
        pass
    return GeneiLabel(content=raw, kind="unknown", filename=filename)


def _decode_b64(value: str) -> bytes:
    """Decodifica base64 tolerando espacios/saltos y padding faltante."""
    cleaned = "".join(value.split())
    cleaned += "=" * (-len(cleaned) % 4)
    try:
        return base64.b64decode(cleaned)
    except (binascii.Error, ValueError) as exc:
        raise GeneiError("La etiqueta de Genei no es base64 válido.") from exc


def _ext(kind: str) -> str:
    return {"pdf": "pdf", "zpl": "zpl"}.get(kind, "bin")
