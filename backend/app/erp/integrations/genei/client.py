"""Cliente síncrono de la API v2 de Genei (agregador de agencias de envío).

Genei se autentica con **email + password** (`POST /login`) y devuelve un
**token Bearer con 15 días de validez**; todas las llamadas van con
`Authorization: Bearer <token>`. El token se cachea en memoria y se renueva al
caducar o ante un `401` (re-login). Password y token **nunca** se registran en
el log.

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
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from app.core.crypto import decrypt

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
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # --- construcción -------------------------------------------------------

    @classmethod
    def from_carrier(
        cls, carrier: Carrier, *, transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> GeneiClient:
        """Construye el cliente desde una fila `Carrier` (Genei). Descifra las
        credenciales (`api_credentials_encrypted`, JSON Fernet `{username,
        password}`)."""
        creds = cls._decode_credentials(carrier.api_credentials_encrypted)
        return cls(
            base_url=carrier.api_base_url or "",
            username=creds.get("username") or creds.get("email") or "",
            password=creds.get("password") or "",
            default_address_id=carrier.default_address_id,
            timeout=timeout,
            transport=transport,
        )

    @staticmethod
    def _decode_credentials(ciphertext: str | None) -> dict[str, str]:
        """`api_credentials_encrypted` (Fernet de un JSON `{username, password}`)
        → dict. Sin credenciales, error de configuración claro."""
        if not ciphertext:
            raise GeneiConfigError("Genei sin credenciales guardadas (configúralas en Ajustes).")
        raw = decrypt(ciphertext)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GeneiConfigError("Las credenciales de Genei no son un JSON válido.") from exc
        if not isinstance(data, dict):
            raise GeneiConfigError("Las credenciales de Genei no tienen el formato esperado.")
        return {str(k): str(v) for k, v in data.items() if v is not None}

    @staticmethod
    def encode_credentials(username: str, password: str) -> str:
        """`{username, password}` → ciphertext Fernet listo para guardar en el
        carrier. (Lo usa el endpoint de Ajustes; nunca se registra en claro.)"""
        from app.core.crypto import encrypt  # noqa: PLC0415 - evita ciclo al importar

        if not username or not password:
            raise ValueError("Genei necesita email y password.")
        return encrypt(json.dumps({"username": username, "password": password}))

    # --- auth ---------------------------------------------------------------

    def login(self) -> str:
        """`POST /login` → token Bearer (15 d). Cachea token + expiración."""
        resp = self._raw_request(
            "POST", "/login",
            json={"username": self._username, "password": self._password},
            authed=False,
        )
        if resp.status_code >= 400:
            raise GeneiError(
                f"Login Genei → {resp.status_code}",
                status=resp.status_code, body=resp.text,
            )
        token = self._extract_token(resp)
        if not token:
            raise GeneiError(
                "Login Genei sin token en la respuesta.",
                status=resp.status_code, body=resp.text,
            )
        self._token = token
        exp = _jwt_exp(token)
        self._token_expires_at = (
            exp if exp is not None else time.time() + TOKEN_FALLBACK_TTL_SECONDS
        )
        return token

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
        if self._token is None:
            return 0
        return max(0, int(self._token_expires_at - time.time()))

    def _ensure_token(self) -> str:
        if (
            self._token is None
            or time.time() >= self._token_expires_at - TOKEN_SAFETY_MARGIN_SECONDS
        ):
            self.login()
        assert self._token is not None
        return self._token

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
        self._ensure_token()
        resp = self._raw_request("GET", f"/shipments/{shipment_code}/label", authed=True)
        if resp.status_code == 401:
            self.login()
            resp = self._raw_request("GET", f"/shipments/{shipment_code}/label", authed=True)
        if resp.status_code >= 400:
            raise GeneiError(
                f"GET /shipments/{shipment_code}/label → {resp.status_code}",
                status=resp.status_code, body=resp.text,
            )
        return _parse_label(resp, shipment_code)

    # --- transporte ---------------------------------------------------------

    def _request(
        self, method: str, path: str, *,
        json: dict[str, Any] | None = None, params: dict[str, Any] | None = None,
    ) -> Any:
        """Petición autenticada con re-login ante un 401 (una vez) y parseo
        JSON. Los errores de Genei se elevan como `GeneiError` con el cuerpo."""
        self._ensure_token()
        reauthed = False
        # Log mínimo (sin token ni cuerpo con datos personales) para no depurar
        # a ciegas: método + ruta + claves de query.
        logger.info("genei %s %s%s", method, path,
                    f" params={sorted(params)}" if params else "")
        while True:
            resp = self._raw_request(method, path, json=json, params=params, authed=True)
            if resp.status_code == 401 and not reauthed:
                reauthed = True
                self.login()
                continue
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
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            return client.request(method, url, json=json, params=params, headers=headers)


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
