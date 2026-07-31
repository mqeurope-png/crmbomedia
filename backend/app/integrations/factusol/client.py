"""Cliente Sprint 0 de la API DELSOL (FACTUSOL en la nube).

PROTOTIPO DE DESCUBRIMIENTO — NO producción. Objetivos:
  - Validar el flujo Login → Bearer token temporal → renovación.
  - Descubrir el esquema real de tablas (CargaTabla con filtro pequeño).
  - Probar escritura end-to-end (EscribirRegistro / ActualizarRegistro /
    BorrarRegistros) sobre datos ficticios.

La API DELSOL es genérica sobre tablas (https://apidoc.sdelsol.com/): en vez
de recursos REST por entidad, expone operaciones sobre cualquier tabla del
ERP (F_CLI clientes, F_ART artículos, F_PRE presupuestos…). El token se
obtiene en el endpoint de Login y caduca — toda operación con token caducado
devuelve 401 Unauthorized y hay que renovar.

Credenciales vía entorno (.env.local, NUNCA commiteadas):
  FACTUSOL_API_BASE_URL   p.ej. https://api.sdelsol.com  (confirmar en el
                          panel de la suscripción; el hosting de Bomedia
                          debe tener el acceso API habilitado por Bart)
  FACTUSOL_API_CODE       código de cliente/empresa DELSOL
  FACTUSOL_API_USER       usuario API
  FACTUSOL_API_PASSWORD   contraseña API
  FACTUSOL_EXERCISE       ejercicio activo (p.ej. 2026) — las tablas de
                          documentos van por ejercicio

En el MVP (Sprint 1) este cliente se migrará al patrón estándar del CRM:
credenciales cifradas en `integration_accounts` + `IntegrationHTTPClient`
compartido (app/integrations/http_client.py) + worker RQ dedicado
`worker-factusol` que SERIALIZA las escrituras (estrategia de concurrencia
elegida: cola única, ver docs/erp/factusol-write-flows.md).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Margen de seguridad: renovamos el token N segundos antes de su caducidad
#: teórica para no pillar un 401 en mitad de una secuencia de escrituras.
TOKEN_SAFETY_MARGIN_SECONDS = 60

#: Reintentos ante 5xx / errores de red, con backoff exponencial.
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0


class FactusolError(RuntimeError):
    """Error de la API DELSOL con contexto (status + body recortado)."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = (body or "")[:2000]


@dataclass
class FactusolClient:
    """Cliente mínimo síncrono (los scripts de descubrimiento son CLI)."""

    base_url: str = field(default_factory=lambda: os.environ.get("FACTUSOL_API_BASE_URL", ""))
    code: str = field(default_factory=lambda: os.environ.get("FACTUSOL_API_CODE", ""))
    user: str = field(default_factory=lambda: os.environ.get("FACTUSOL_API_USER", ""))
    password: str = field(default_factory=lambda: os.environ.get("FACTUSOL_API_PASSWORD", ""))
    exercise: str = field(default_factory=lambda: os.environ.get("FACTUSOL_EXERCISE", ""))

    _token: str | None = field(default=None, init=False, repr=False)
    _token_expires_at: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.base_url:
            raise FactusolError(
                "FACTUSOL_API_BASE_URL no configurada. Copia .env.example → "
                ".env.local y rellena las credenciales (no commitear)."
            )
        self.base_url = self.base_url.rstrip("/")

    # --- auth ----------------------------------------------------------------

    def authenticate(self) -> None:
        """Llama al endpoint de Login y guarda token + expiración.

        NOTA Sprint 0: la ruta y el shape exactos del Login se confirman
        contra apidoc.sdelsol.com con las credenciales reales (el portal
        requiere registro y el host está fuera del allowlist de red de este
        entorno de desarrollo). El shape esperado según la doc pública:
        POST {base}/login/autenticar con {codigo, usuario, password} →
        {"token": "...", "caducidad"/expiresIn: ...}. Ajustar aquí si el
        descubrimiento revela otra ruta/payload — está aislado a propósito.
        """
        payload = {
            "codigo": self.code,
            "usuario": self.user,
            "password": self.password,
        }
        resp = self._raw_request("POST", "/login/autenticar", json=payload, auth=False)
        data = resp.json()
        token = data.get("token") or data.get("Token") or data.get("access_token")
        if not token:
            raise FactusolError(
                "Login sin token en la respuesta — revisar shape real",
                status=resp.status_code, body=resp.text,
            )
        self._token = token
        # Caducidad: si la respuesta la trae (segundos o timestamp) úsala;
        # si no, asumimos 20 min (validar en el test de 5 auth / 30 min).
        expires_in = data.get("caducidad") or data.get("expiresIn") or 20 * 60
        try:
            expires_in = float(expires_in)
        except (TypeError, ValueError):
            expires_in = 20 * 60
        self._token_expires_at = time.time() + expires_in
        logger.info("factusol.auth ok — token válido %.0fs", expires_in)

    def _ensure_token(self) -> None:
        """Renueva automáticamente si el token caducó (o está a punto)."""
        if (
            self._token is None
            or time.time() >= self._token_expires_at - TOKEN_SAFETY_MARGIN_SECONDS
        ):
            self.authenticate()

    # --- operaciones genéricas sobre tablas ---------------------------------

    def carga_tabla(
        self, tabla: str, *, filtro: str | None = None,
        columnas: list[str] | None = None, ejercicio: str | None = None,
    ) -> list[dict[str, Any]]:
        """CargaTabla — lee registros de una tabla del ERP.

        `filtro` es la cláusula de la API (sintaxis SQL-like según doc
        oficial). Para descubrimiento usar filtros pequeños (p.ej.
        "CODCLI<>''" con límite) y listar las columnas devueltas.
        """
        body: dict[str, Any] = {"tabla": tabla}
        if filtro:
            body["filtro"] = filtro
        if columnas:
            body["columnas"] = columnas
        body["ejercicio"] = ejercicio or self.exercise
        resp = self._request("POST", "/registros/cargaTabla", json=body)
        data = resp.json()
        rows = data.get("registros") or data.get("datos") or data.get("data") or []
        return rows if isinstance(rows, list) else []

    def escribir_registro(
        self, tabla: str, registro: dict[str, Any], *, ejercicio: str | None = None,
    ) -> dict[str, Any]:
        """EscribirRegistro — inserta UN registro (la API es de registro a
        registro; no se ha visto operación bulk en la doc pública — punto a
        confirmar en el descubrimiento y crítico para estimar el sync)."""
        body = {
            "tabla": tabla,
            "registro": registro,
            "ejercicio": ejercicio or self.exercise,
        }
        resp = self._request("POST", "/registros/escribirRegistro", json=body)
        return resp.json()

    def actualizar_registro(
        self, tabla: str, registro: dict[str, Any], *, filtro: str,
        ejercicio: str | None = None,
    ) -> dict[str, Any]:
        """ActualizarRegistro — actualiza registros que cumplan el filtro."""
        body = {
            "tabla": tabla,
            "registro": registro,
            "filtro": filtro,
            "ejercicio": ejercicio or self.exercise,
        }
        resp = self._request("POST", "/registros/actualizarRegistro", json=body)
        return resp.json()

    def borrar_registros(
        self, tabla: str, *, filtro: str, ejercicio: str | None = None,
    ) -> dict[str, Any]:
        """BorrarRegistros — borra por filtro. SOLO datos ficticios de
        prueba en Sprint 0 (cleanup de los flujos de escritura)."""
        body = {
            "tabla": tabla,
            "filtro": filtro,
            "ejercicio": ejercicio or self.exercise,
        }
        resp = self._request("POST", "/registros/borrarRegistros", json=body)
        return resp.json()

    # --- transporte ----------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Petición autenticada con renovación automática + retry 5xx.

        Un 401 fuerza UNA re-autenticación inmediata y reintento (token
        caducado en vuelo); los 5xx reintentan con backoff exponencial.
        """
        self._ensure_token()
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._raw_request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt > MAX_RETRIES:
                    msg = f"Error de red tras {MAX_RETRIES} reintentos: {exc}"
                    raise FactusolError(msg) from exc
                self._sleep_backoff(attempt)
                continue
            if resp.status_code == 401 and attempt <= 1:
                logger.info("factusol 401 — token caducado en vuelo, re-auth")
                self.authenticate()
                continue
            if resp.status_code >= 500 and attempt <= MAX_RETRIES:
                self._sleep_backoff(attempt)
                continue
            if resp.status_code >= 400:
                raise FactusolError(
                    f"{method} {path} → {resp.status_code}",
                    status=resp.status_code, body=resp.text,
                )
            return resp

    def _raw_request(
        self, method: str, path: str, *, auth: bool = True, **kwargs: Any
    ) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        with httpx.Client(timeout=30.0) as client:
            return client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        logger.info("factusol retry en %.1fs (intento %d)", wait, attempt)
        time.sleep(wait)
