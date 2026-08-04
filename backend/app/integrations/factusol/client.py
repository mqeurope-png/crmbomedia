"""Cliente HTTP de la API DELSOL (FACTUSOL en la nube) — Fase C PR C-1.

La API DELSOL es **genérica sobre tablas** (no REST por entidad): login →
Bearer JWT temporal, y luego operaciones sobre cualquier tabla del ERP
(`F_CLI` clientes, `F_ART` artículos, `F_FAC` facturas…): CargaTabla (leer),
EscribirRegistro (insertar 1), ActualizarRegistro (modificar por filtro),
BorrarRegistros (borrar por filtro).

Auth (validada en prod por Bart, 2026-08-04):
  POST /login/Autenticar
  {codigoFabricante, codigoCliente, baseDatosCliente, password(base64)}
  → 200 {"resultado": "<JWT>", "respuesta": "OK"}   (JWT expira a los ~3 min)

El password llega cifrado con Fernet (INTEGRATION_SECRETS_KEY) en el env
`FACTUSOL_PASSWORD_ENCRYPTED`; se descifra en memoria y se envía en base64.
El JWT se cachea con margen de 30s sobre su `exp`.

⚠️ C-1-fix1: las rutas de DATOS siguen SIN confirmar — `/registros/*` devuelve
404 en producción y la doc oficial (apidoc.sdelsol.com) está bloqueada por la
política de egress de CI/dev, así que no se han cambiado por otra conjetura.
Son configurables por env (`FACTUSOL_PATH_LOAD_TABLE`, …) para corregirlas sin
redeploy de código, y `scripts/factusol_discover_paths.py` las descubre desde
el VPS. El login SÍ está confirmado.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt

logger = logging.getLogger(__name__)

#: Renovamos el token N segundos antes de su `exp` para no pillar un 401 en
#: mitad de una secuencia de escrituras (el JWT dura ~3 min).
TOKEN_SAFETY_MARGIN_SECONDS = 30
#: Fallback de vida del token si el JWT no trae `exp` parseable.
TOKEN_FALLBACK_TTL_SECONDS = 150

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0

#: Ruta de login — CONFIRMADA en producción (200 + JWT). ASP.NET la resuelve
#: sin distinguir mayúsculas (`/Login/Autenticar` también vale).
LOGIN_PATH = "/login/Autenticar"

#: Rutas de datos — ⚠️ NO CONFIRMADAS. Estos defaults dan 404 en producción
#: (C-1-fix1): eran una conjetura del Sprint 0 y la doc oficial
#: (apidoc.sdelsol.com) no es accesible ni desde CI ni desde el entorno de
#: desarrollo (bloqueada por política de egress), así que NO se han sustituido
#: por otra conjetura.
#:
#: Se sobreescriben SIN tocar código con las envs
#: `FACTUSOL_PATH_LOAD_TABLE` / `_WRITE_RECORD` / `_UPDATE_RECORD` /
#: `_DELETE_RECORDS`. Para averiguar las correctas desde el VPS (que sí llega
#: a la API) usar `python -m scripts.factusol_discover_paths`, que explota el
#: oráculo 404 (ruta inexistente) vs 401/400 (ruta válida).
PATH_CARGA_TABLA = "/registros/cargaTabla"
PATH_ESCRIBIR = "/registros/escribirRegistro"
PATH_ACTUALIZAR = "/registros/actualizarRegistro"
PATH_BORRAR = "/registros/borrarRegistros"


class FactusolError(RuntimeError):
    """Error de la API DELSOL con contexto (status + body recortado)."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = (body or "")[:2000]


def _jwt_exp(token: str) -> float | None:
    """Extrae el `exp` (epoch seconds) del payload del JWT, sin verificar la
    firma (solo lo usamos para saber cuándo renovar)."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = data.get("exp")
        return float(exp) if exp is not None else None
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None


class FactusolClient:
    """Cliente síncrono (las escrituras viven en jobs RQ, no hace falta async).

    Para tests se inyecta un `transport` de httpx (MockTransport) — no sale a
    red. En prod se construye con `from_settings()`.
    """

    def __init__(
        self, *,
        base_url: str,
        codigo_fabricante: str,
        codigo_cliente: str,
        base_datos_cliente: str,
        password: str,
        default_ejercicio: str = "2026",
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        path_load_table: str = "",
        path_write_record: str = "",
        path_update_record: str = "",
        path_delete_records: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.codigo_fabricante = codigo_fabricante
        self.codigo_cliente = codigo_cliente
        self.base_datos_cliente = base_datos_cliente
        self._password = password
        self.default_ejercicio = default_ejercicio
        self._timeout = timeout
        self._transport = transport
        # Rutas de datos configurables (ver constantes arriba): vacío → default.
        self.path_load_table = path_load_table or PATH_CARGA_TABLA
        self.path_write_record = path_write_record or PATH_ESCRIBIR
        self.path_update_record = path_update_record or PATH_ACTUALIZAR
        self.path_delete_records = path_delete_records or PATH_BORRAR
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> FactusolClient:
        s = settings or get_settings()
        password = decrypt(s.factusol_password_encrypted) if s.factusol_password_encrypted else ""
        return cls(
            base_url=s.factusol_base_url,
            codigo_fabricante=s.factusol_codigo_fabricante,
            codigo_cliente=s.factusol_codigo_cliente,
            base_datos_cliente=s.factusol_base_datos_cliente,
            password=password,
            default_ejercicio=s.factusol_default_ejercicio,
            path_load_table=s.factusol_path_load_table,
            path_write_record=s.factusol_path_write_record,
            path_update_record=s.factusol_path_update_record,
            path_delete_records=s.factusol_path_delete_records,
        )

    # --- auth ----------------------------------------------------------------

    def authenticate(self) -> str:
        """Login → JWT. Cachea token + expiración. Devuelve el token."""
        payload = {
            "codigoFabricante": self.codigo_fabricante,
            "codigoCliente": self.codigo_cliente,
            "baseDatosCliente": self.base_datos_cliente,
            # FACTUSOL espera el password en base64.
            "password": base64.b64encode(self._password.encode()).decode(),
        }
        resp = self._raw_request("POST", LOGIN_PATH, json=payload, authed=False)
        if resp.status_code >= 400:
            raise FactusolError(
                f"Login FACTUSOL → {resp.status_code}",
                status=resp.status_code, body=resp.text,
            )
        data = resp.json()
        token = data.get("resultado")
        if data.get("respuesta") != "OK" or not token:
            raise FactusolError(
                f"Login FACTUSOL sin token (respuesta={data.get('respuesta')!r})",
                status=resp.status_code, body=resp.text,
            )
        self._token = token
        exp = _jwt_exp(token)
        self._token_expires_at = (
            exp if exp is not None else time.time() + TOKEN_FALLBACK_TTL_SECONDS
        )
        return token

    def token_valid_seconds(self) -> int:
        """Segundos que le quedan al token cacheado (0 si no hay o caducó)."""
        if self._token is None:
            return 0
        return max(0, int(self._token_expires_at - time.time()))

    def token_claims(self) -> dict[str, Any]:
        """Claims del JWT cacheado (sin verificar firma) — para el smoke-test."""
        if not self._token:
            return {}
        try:
            payload_b64 = self._token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload_b64))
            return data if isinstance(data, dict) else {}
        except (IndexError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _ensure_token(self) -> str:
        if (
            self._token is None
            or time.time() >= self._token_expires_at - TOKEN_SAFETY_MARGIN_SECONDS
        ):
            self.authenticate()
        assert self._token is not None
        return self._token

    # --- operaciones genéricas sobre tablas ---------------------------------

    def load_table(
        self, tabla: str, *, filtro: str = "", campos: list[str] | None = None,
        numero_registros: int | None = None, ejercicio: str | None = None,
    ) -> list[dict[str, Any]]:
        """CargaTabla — lee registros de una tabla. `filtro` es la cláusula
        SQL-like de la API; `campos` limita columnas; `numero_registros`
        acota el volumen (útil para el smoke-test)."""
        body: dict[str, Any] = {"tabla": tabla, "ejercicio": ejercicio or self.default_ejercicio}
        if filtro:
            body["filtro"] = filtro
        if campos:
            body["campos"] = campos
        if numero_registros is not None:
            body["numeroRegistros"] = numero_registros
        data = self._request("POST", self.path_load_table, json=body)
        rows = data.get("registros") or data.get("datos") or data.get("data") or []
        return rows if isinstance(rows, list) else []

    def write_record(
        self, tabla: str, data: dict[str, Any], *, ejercicio: str | None = None,
    ) -> dict[str, Any]:
        """EscribirRegistro — inserta UN registro (la API es de registro a
        registro; no hay bulk documentado)."""
        return self._request("POST", self.path_write_record, json={
            "tabla": tabla, "registro": data,
            "ejercicio": ejercicio or self.default_ejercicio,
        })

    def update_record(
        self, tabla: str, key: str, data: dict[str, Any], *, ejercicio: str | None = None,
    ) -> dict[str, Any]:
        """ActualizarRegistro — modifica los registros que cumplan `key`
        (filtro SQL-like, p.ej. "CODCLI='22870'")."""
        return self._request("POST", self.path_update_record, json={
            "tabla": tabla, "filtro": key, "registro": data,
            "ejercicio": ejercicio or self.default_ejercicio,
        })

    def delete_records(
        self, tabla: str, filtro: str, *, ejercicio: str | None = None,
    ) -> dict[str, Any]:
        """BorrarRegistros — borra por filtro."""
        return self._request("POST", self.path_delete_records, json={
            "tabla": tabla, "filtro": filtro,
            "ejercicio": ejercicio or self.default_ejercicio,
        })

    # --- transporte ----------------------------------------------------------

    def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Petición autenticada con renovación de token + retry 5xx. Un 401
        fuerza UNA re-autenticación inmediata y un reintento (token caducado
        en vuelo)."""
        self._ensure_token()
        reauthed = False
        attempt = 0
        while True:
            attempt += 1
            resp = self._raw_request(method, path, json=json, authed=True)
            if resp.status_code == 401 and not reauthed:
                reauthed = True
                self.authenticate()
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt <= MAX_RETRIES:
                self._sleep_backoff(attempt)
                continue
            if resp.status_code >= 400:
                raise FactusolError(
                    f"{method} {path} → {resp.status_code}: {resp.text[:500]}",
                    status=resp.status_code, body=resp.text,
                )
            return resp.json()

    def _raw_request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, authed: bool = True,
    ) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if authed and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=self._timeout, transport=self._transport) as c:
            return c.request(method, url, json=json, headers=headers)

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        logger.info("factusol retry en %.1fs (intento %d)", wait, attempt)
        time.sleep(wait)
