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

C-1-fix1 (2026-08-04): rutas y formatos VERIFICADOS contra la API real. Los
endpoints de datos cuelgan de `/admin/` (los `/registros/*` del Sprint 0 daban
404). Particularidades del contrato real, todas manejadas aquí:

- `CargaTabla` solo acepta `{ejercicio, tabla, filtro}`; `filtro` es un
  fragmento SQL WHERE y vacío devuelve `null` → se manda `1=1`.
- La respuesta es una lista ANIDADA de `{columna, dato}` por fila
  (`_rows_to_dicts` la normaliza a `list[dict]`).
- Los registros de escritura van como array de `{columna, dato}`
  (`_to_api_record` convierte los dicts planos de los mappers).
- `BorrarRegistros` es **GET** con path params.
- El token caducado llega como **HTTP 200 + `respuesta: "Unauthorized"`**, no
  como 401 — `_request` lo trata igual (re-auth + reintento).

Las rutas siguen siendo sobreescribibles por env (`FACTUSOL_PATH_LOAD_TABLE`, …)
y `scripts/factusol_discover_paths.py` las redescubre si DELSOL las cambia.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any
from urllib.parse import quote

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

#: Rutas de datos — CONFIRMADAS contra la API real (2026-08-04, C-1-fix1).
#: Todas cuelgan de `/admin/` (las `/registros/*` del Sprint 0 daban 404).
#: Siguen siendo sobreescribibles por env (`FACTUSOL_PATH_LOAD_TABLE`, …) por
#: si DELSOL las cambia — corregirlas no exige redeploy de código.
PATH_CARGA_TABLA = "/admin/CargaTabla"              # POST {ejercicio,tabla,filtro}
PATH_ESCRIBIR = "/admin/EscribirRegistro"           # POST {ejercicio,tabla,registro[]}
PATH_ACTUALIZAR = "/admin/ActualizarRegistro"       # POST, igual + PK en registro[]
#: GET con path params: /admin/BorrarRegistros/{ejercicio}/{tabla}/{filtro}
PATH_BORRAR = "/admin/BorrarRegistros"

#: `filtro` es un fragmento SQL WHERE. Vacío devuelve `resultado: null`, así
#: que "sin filtro" se expresa con `1=1`.
FILTRO_TODOS = "1=1"

#: Valores de `respuesta` que la API devuelve con HTTP 200 pero significan error.
RESPUESTA_OK = "OK"
RESPUESTA_UNAUTHORIZED = "Unauthorized"   # token caducado (¡no llega como 401!)
RESPUESTA_BD_NO_EXISTE = "BDNoExiste"     # ejercicio sin base de datos
#: `KO` pelado, sin más contexto: `{"resultado":"","respuesta":"KO"}`. DELSOL no
#: dice por qué. Visto en producción (C-6-fix1) en una `CargaTabla` de F_CLI
#: entera que había funcionado 66 segundos antes — o sea, **transitorio**:
#: sobrecarga, timeout interno o rate-limit sin cabecera que lo diga.
#: Se reintenta antes de darlo por perdido.
RESPUESTA_KO = "KO"

#: Espera entre reintentos de `KO`. Cuenta aparte del backoff de los 5xx: un
#: `KO` no es un error de transporte y no debe gastarles el presupuesto.
KO_BACKOFF_SECONDS = (0.5, 2.0, 5.0)


class FactusolError(RuntimeError):
    """Error de la API DELSOL con contexto (status + body recortado)."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = (body or "")[:2000]


def _rows_to_dicts(resultado: Any) -> list[dict[str, Any]]:
    """Normaliza el `resultado` de CargaTabla a `list[dict]` idiomático.

    La API devuelve una lista ANIDADA: cada fila es una lista de
    `{"columna": <nombre>, "dato": <valor>}`. Sin filas devuelve `null`.

        [[{"columna":"CODCLI","dato":1}, …], …]  →  [{"CODCLI": 1, …}, …]
    """
    if not resultado or not isinstance(resultado, list):
        return []
    out: list[dict[str, Any]] = []
    for row in resultado:
        if not isinstance(row, list):
            continue
        out.append({
            col["columna"]: col.get("dato")
            for col in row
            if isinstance(col, dict) and "columna" in col
        })
    return out


def _to_api_record(data: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte un registro a la forma que espera la API:
    `[{"columna": <nombre>, "dato": <valor>}, …]`. Acepta un dict plano (el
    formato que devuelven los mappers) o una lista ya en formato API."""
    if isinstance(data, list):
        return data
    return [{"columna": k, "dato": v} for k, v in data.items()]


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
        self, tabla: str, *, filtro: str = FILTRO_TODOS, ejercicio: str | None = None,
    ) -> list[dict[str, Any]]:
        """CargaTabla — lee filas de una tabla del ejercicio.

        `filtro` es un fragmento SQL **WHERE** (admite LIKE / AND / ORDER BY /
        LIMIT). Un filtro vacío hace que la API devuelva `resultado: null`, así
        que el default es `1=1`. La API devuelve SIEMPRE todas las columnas de
        la tabla (para proyectar columnas hay que usar `LanzarConsulta`).

        Devuelve las filas ya normalizadas a `list[dict]` (ver `_rows_to_dicts`).
        """
        data = self._request("POST", self.path_load_table, json={
            "ejercicio": ejercicio or self.default_ejercicio,
            "tabla": tabla,
            "filtro": filtro or FILTRO_TODOS,
        })
        return _rows_to_dicts(data.get("resultado"))

    def write_record(
        self, tabla: str, data: dict[str, Any] | list[dict[str, Any]], *,
        ejercicio: str | None = None,
    ) -> dict[str, Any]:
        """EscribirRegistro — inserta UN registro (la API es registro a
        registro; no hay bulk). Acepta el registro como dict plano
        `{"CODCLI": 1}` o ya en la forma de la API `[{"columna","dato"}]`."""
        return self._request("POST", self.path_write_record, json={
            "ejercicio": ejercicio or self.default_ejercicio,
            "tabla": tabla,
            "registro": _to_api_record(data),
        })

    def update_record(
        self, tabla: str, data: dict[str, Any] | list[dict[str, Any]], *,
        ejercicio: str | None = None,
    ) -> dict[str, Any]:
        """ActualizarRegistro — mismo formato que EscribirRegistro. El
        registro DEBE incluir la columna PK (p.ej. `CODCLI`) para que la API
        sepa qué fila tocar; las demás columnas son los cambios."""
        return self._request("POST", self.path_update_record, json={
            "ejercicio": ejercicio or self.default_ejercicio,
            "tabla": tabla,
            "registro": _to_api_record(data),
        })

    def delete_records(
        self, tabla: str, filtro: str, *, ejercicio: str | None = None,
    ) -> dict[str, Any]:
        """BorrarRegistros — **GET** con path params:
        `/admin/BorrarRegistros/{ejercicio}/{tabla}/{filtro}`."""
        ejercicio = ejercicio or self.default_ejercicio
        path = (
            f"{self.path_delete_records}/{quote(str(ejercicio), safe='')}"
            f"/{quote(tabla, safe='')}/{quote(filtro, safe='')}"
        )
        return self._request("GET", path)

    # --- transporte ----------------------------------------------------------

    def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Petición autenticada con renovación de token + retry 5xx.

        OJO: la API DELSOL señala el token caducado con **HTTP 200** y
        `respuesta: "Unauthorized"` en el cuerpo, no con un 401. Con un JWT de
        ~3 min eso ocurre a mitad de cualquier secuencia de escrituras, así que
        se trata igual que un 401: re-autenticar una vez y reintentar.

        Lo mismo con `respuesta: "KO"`, que también llega con 200 y sin decir
        por qué: se reintenta con backoff (`KO_BACKOFF_SECONDS`) antes de darlo
        por perdido. Ver C-6-fix1.
        """
        self._ensure_token()
        reauthed = False
        attempt = 0
        ko_retries = 0
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

            data = resp.json()
            if not isinstance(data, dict):
                return {"resultado": data, "respuesta": RESPUESTA_OK}
            respuesta = data.get("respuesta")

            # Token caducado disfrazado de 200 → re-auth + reintento.
            if respuesta == RESPUESTA_UNAUTHORIZED and not reauthed:
                reauthed = True
                self.authenticate()
                continue
            if respuesta == RESPUESTA_UNAUTHORIZED:
                raise FactusolError(
                    f"{method} {path} → token rechazado tras re-autenticar",
                    status=resp.status_code, body=resp.text,
                )
            if respuesta == RESPUESTA_BD_NO_EXISTE:
                raise FactusolError(
                    f"{method} {path} → ejercicio sin base de datos en FACTUSOL "
                    f"(respuesta={respuesta!r})",
                    status=resp.status_code, body=resp.text,
                )
            # `KO` pelado: transitorio hasta que se demuestre lo contrario.
            if respuesta == RESPUESTA_KO and ko_retries < len(KO_BACKOFF_SECONDS):
                wait = KO_BACKOFF_SECONDS[ko_retries]
                ko_retries += 1
                logger.warning(
                    "factusol %s %s → respuesta='KO' (transitorio); reintento "
                    "%d/%d en %.1fs", method, path, ko_retries,
                    len(KO_BACKOFF_SECONDS), wait,
                )
                time.sleep(wait)
                continue
            if respuesta == RESPUESTA_KO:
                raise FactusolError(
                    f"{method} {path} → respuesta='KO' tras "
                    f"{len(KO_BACKOFF_SECONDS)} reintentos: {resp.text[:300]}",
                    status=resp.status_code, body=resp.text,
                )
            if respuesta is not None and respuesta != RESPUESTA_OK:
                raise FactusolError(
                    f"{method} {path} → respuesta={respuesta!r}: {resp.text[:300]}",
                    status=resp.status_code, body=resp.text,
                )
            return data

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
