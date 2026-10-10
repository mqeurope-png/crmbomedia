"""Ritmo y reintentos para recorrer la API de Gmail sin agotar la cuota.

La cuota de la Gmail API se cuenta por usuario y por minuto («Total Query
Cost», «Units per minute per user»): cada `messages.get` y cada
`messages.list` gastan de ella. El relleno universal iba a tope y Google lo
cortaba con un `403 rateLimitExceeded` a los veinte segundos (09/10/2026); la
excepción subía hasta arriba y mataba el proceso sin informe.

Dos piezas, en un solo objeto (`Ritmo`):

- **Marcar el paso**: un mínimo de tiempo entre el arranque de una petición y
  la siguiente (`peticiones_por_segundo`). Es mejor tardar diez minutos y
  terminar que correr y morir.
- **Reintentar cuando Google dice «espera»**: un `403` con razón de cuota
  (`rateLimitExceeded`, `userRateLimitExceeded`), un `429` o un `5xx` no son
  errores de los que rendirse. Espera creciente (1 s, 2 s, 4 s…) con algo de
  azar, respetando `Retry-After` si viene, hasta `max_reintentos`; si Gmail
  sigue negándose, `CuotaGmailAgotada`, y el que recorre decide parar con
  informe. Un `403 dailyLimitExceeded` o un `4xx` cualquiera no se reintenta.

Reloj y «dormir» son inyectables para que los tests no esperen de verdad.
"""
from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Razones (`error.errors[].reason`) con las que Google pide esperar.
RAZONES_DE_CUOTA = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
#: Las que se reconocen también en el texto de la excepción (falsos de tests,
#: errores sin cuerpo JSON).
RAZONES_CONOCIDAS = (*RAZONES_DE_CUOTA, "dailyLimitExceeded")
#: Códigos que se reintentan siempre (Google recomienda backoff en 5xx).
ESTADOS_TRANSITORIOS = frozenset({429, 500, 502, 503, 504})
#: Tope a un `Retry-After` desorbitado: más de esto, mejor parar con informe.
RETRY_AFTER_MAXIMO = 120.0


class CuotaGmailAgotada(RuntimeError):
    """Gmail siguió negando la cuota tras todos los reintentos previstos."""

    def __init__(self, *, intentos: int, ultima: BaseException, etiqueta: str) -> None:
        self.intentos = intentos
        self.ultima = ultima
        self.etiqueta = etiqueta
        self.detalle = descripcion_corta(ultima)
        super().__init__(
            f"Gmail sigue negando la cuota tras {intentos} intentos en {etiqueta}: "
            f"{self.detalle}"
        )


def estado_http(exc: BaseException) -> int | None:
    """`HttpError.resp.status` de googleapiclient (duck typing: los falsos de
    los tests llevan un `resp` con `status`)."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def razones_de(exc: BaseException) -> set[str]:
    """Las `reason` del cuerpo de error de Google (`rateLimitExceeded`…), tal
    cual vienen. Primero `error_details` (lo rellena googleapiclient), después
    el JSON crudo de `content`, y si no hay nada, lo que diga el texto de la
    excepción."""
    razones: set[str] = set()
    detalles = getattr(exc, "error_details", None)
    if isinstance(detalles, list):
        for item in detalles:
            if isinstance(item, dict) and item.get("reason"):
                razones.add(str(item["reason"]))
    if not razones:
        contenido = getattr(exc, "content", None)
        if isinstance(contenido, bytes | bytearray):
            try:
                datos = json.loads(bytes(contenido).decode("utf-8"))
                for item in (datos.get("error") or {}).get("errors") or []:
                    if isinstance(item, dict) and item.get("reason"):
                        razones.add(str(item["reason"]))
            except (ValueError, AttributeError, TypeError):
                pass
    if not razones:
        texto = repr(exc).lower()
        for razon in RAZONES_CONOCIDAS:
            if razon.lower() in texto:
                razones.add(razon)
    return razones


def _razones_minusculas(exc: BaseException) -> set[str]:
    return {razon.lower() for razon in razones_de(exc)}


def es_error_de_cuota(exc: BaseException) -> bool:
    """«Espera»: un 429, o un 403 cuya razón es de ritmo (no el límite diario,
    que no se arregla esperando un minuto)."""
    estado = estado_http(exc)
    if estado == 429:
        return True
    razones = _razones_minusculas(exc)
    if "dailylimitexceeded" in razones:
        return False
    if estado == 403 or estado is None:
        return bool(razones & {razon.lower() for razon in RAZONES_DE_CUOTA})
    return False


def es_error_transitorio(exc: BaseException) -> bool:
    """Lo que merece reintento: cuota o un 5xx de Google."""
    if es_error_de_cuota(exc):
        return True
    estado = estado_http(exc)
    return estado is not None and estado in ESTADOS_TRANSITORIOS and estado != 429


def segundos_retry_after(exc: BaseException) -> float | None:
    """`Retry-After` en segundos si Google lo manda (la `Response` de httplib2
    es un dict con las cabeceras en minúsculas)."""
    resp = getattr(exc, "resp", None)
    getter = getattr(resp, "get", None)
    if getter is None:
        return None
    try:
        crudo = getter("retry-after")
    except Exception:  # noqa: BLE001 — un falso sin cabeceras
        return None
    if crudo in (None, ""):
        return None
    try:
        return max(0.0, float(crudo))
    except (TypeError, ValueError):
        return None  # formato fecha HTTP: no merece la pena parsearlo


def descripcion_corta(exc: BaseException) -> str:
    """«HTTP 403 rateLimitExceeded» o, sin estado, el tipo y el texto cortos.
    Nunca el cuerpo entero (lleva la URL con la consulta)."""
    estado = estado_http(exc)
    razones = ", ".join(sorted(razones_de(exc)))
    if estado is not None:
        return f"HTTP {estado}" + (f" {razones}" if razones else "")
    texto = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}" + (f": {texto[:120]}" if texto else "")


def _dormir(segundos: float) -> None:
    time.sleep(segundos)


@dataclass
class Ritmo:
    """Marcapasos + reintentos para las llamadas a Gmail de un recorrido.

    `peticiones_por_segundo=None` o 0 desactiva el paso (los tests lo usan).
    `reloj`, `dormir` y `azar` son inyectables."""

    peticiones_por_segundo: float | None = 2.0
    max_reintentos: int = 7
    espera_base: float = 1.0
    espera_maxima: float = 64.0
    reloj: Callable[[], float] = time.monotonic
    dormir: Callable[[float], None] | None = None
    azar: Callable[[], float] = random.random
    # Contadores para el informe.
    peticiones: int = 0
    esperas: int = 0
    segundos_esperando: float = 0.0
    _siguiente: float | None = field(default=None, init=False, repr=False)

    def _duerme(self, segundos: float) -> None:
        (self.dormir or _dormir)(segundos)

    def esperar_turno(self) -> None:
        """No arranca una petición antes de que pase 1/rps desde la anterior."""
        rps = self.peticiones_por_segundo
        if not rps or rps <= 0:
            return
        intervalo = 1.0 / rps
        ahora = self.reloj()
        if self._siguiente is None or ahora >= self._siguiente:
            self._siguiente = ahora + intervalo
            return
        espera = self._siguiente - ahora
        self._siguiente += intervalo
        self._duerme(espera)

    def _espera_para(self, intento: int, exc: BaseException) -> float:
        base = min(self.espera_maxima, self.espera_base * (2 ** intento))
        espera = base * (0.5 + 0.5 * self.azar())  # entre la mitad y el total
        retry_after = segundos_retry_after(exc)
        if retry_after:
            espera = max(espera, min(retry_after, RETRY_AFTER_MAXIMO))
        return espera

    def llamar(self, fn: Callable[[], T], *, etiqueta: str = "gmail") -> T:
        """Ejecuta `fn` a su ritmo, reintentando los errores transitorios.

        Tras `max_reintentos` seguidos: si el último era de cuota,
        `CuotaGmailAgotada`; si era otro transitorio (5xx persistente), sube
        la excepción original. Los no transitorios (404, 400, 403 de permisos)
        suben a la primera."""
        intento = 0
        while True:
            self.esperar_turno()
            self.peticiones += 1
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 — se clasifica abajo
                if not es_error_transitorio(exc):
                    raise
                if intento >= self.max_reintentos:
                    if es_error_de_cuota(exc):
                        raise CuotaGmailAgotada(
                            intentos=intento + 1, ultima=exc, etiqueta=etiqueta,
                        ) from exc
                    raise
                espera = self._espera_para(intento, exc)
                self.esperas += 1
                self.segundos_esperando += espera
                logger.warning(
                    "gmail.ritmo espera %.1fs antes de reintentar %s (%s, intento %d/%d)",
                    espera, etiqueta, descripcion_corta(exc), intento + 1,
                    self.max_reintentos,
                )
                self._duerme(espera)
                intento += 1


def ritmo_desde_ajustes(
    rps: float | None = None, *, max_reintentos: int | None = None,
) -> Ritmo:
    """El `Ritmo` con los valores de configuración (`GMAIL_BACKFILL_RPS`,
    `GMAIL_BACKFILL_MAX_RETRIES`), salvo lo que se pase explícito."""
    from app.core.config import get_settings  # noqa: PLC0415

    ajustes: Any = get_settings()
    return Ritmo(
        peticiones_por_segundo=(
            rps if rps is not None else float(ajustes.gmail_backfill_rps)
        ),
        max_reintentos=(
            max_reintentos if max_reintentos is not None
            else int(ajustes.gmail_backfill_max_retries)
        ),
    )
