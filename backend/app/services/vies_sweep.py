"""Fase VIES — revalidación en segundo plano de los NIF-IVA «pendiente».

VIES (sobre todo Francia) devuelve `MS_MAX_CONCURRENT_REQ` muy a menudo, así
que muchas empresas se quedan en `desconocido` («pendiente de validar») y
«Revalidar en VIES» vuelve a encontrarlo saturado. Este barrido periódico
las va resolviendo solas, sin saturar VIES ni que nos bloqueen la IP:

- Cada `VIES_SWEEP_INTERVAL_MINUTES` (30) un job RQ self-rescheduling (mismo
  patrón que `workflows.scheduler`: heartbeat con SETNX + `enqueue_in`) en la
  cola `vies:sweep`, que escucha `worker-workflows` (`--with-scheduler`).
- Selecciona las empresas activas de la UE (no España) con NIF-IVA y sin
  veredicto firme para su NIF-IVA actual, cuyo `vies_next_retry_at` ya ha
  pasado. España (nacional) y fuera de la UE (exportación) no entran.
- Consulta de una en una, con `VIES_SWEEP_SPACING_SECONDS` (2 s) entre
  llamadas, como mucho `VIES_SWEEP_BATCH` (20) por barrido; el resto en el
  siguiente. Nunca en paralelo (esa es la causa del error).
- Persiste el progreso por empresa (commit tras cada una): `valido` /
  `no_valido` no vuelven a entrar; `desconocido` suma un intento y fija el
  siguiente reintento con backoff creciente (30 min → 1 h → 2 h → 4 h → 8 h
  → 24 h), así ninguna se consulta cada 30 min si acaba de fallar.
- Prudencia anti-bloqueo: 3 respuestas seguidas de limitación de ritmo
  cortan el barrido; `IP_BLOCKED` / `GLOBAL_MAX_CONCURRENT_REQ*` pausan los
  barridos `VIES_SWEEP_PAUSE_MINUTES` (60). Nada de esto se convierte en
  «no válido».
- Cada barrido deja una línea de log con el resumen (cuántas consultadas,
  cuántos veredictos, cuántas siguen pendientes).

«Revalidar en VIES» (manual, forzado) sigue igual; el usuario ya no depende
de él. Un barrido a mano: `python -m app.services.vies_sweep [--dry-run]`.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.erp.language import normalize_country
from app.integrations.vies.client import (
    VIES_DESCONOCIDO,
    VIES_NO_VALIDO,
    VIES_VALIDO,
    ViesResult,
)
from app.models.crm import Company
from app.services.vies import apply_result, check_vat_live, company_eu_vat
from app.workers.queues import queue_name

logger = logging.getLogger(__name__)

SWEEP_QUEUE = queue_name("vies", "sweep")
LOCK_KEY = "vies:sweep:heartbeat"
PAUSE_KEY = "vies:sweep:paused_until"
#: Errores que significan «VIES nos está frenando a nosotros»: pausa larga.
BLOCK_ERRORS = frozenset({
    "IP_BLOCKED", "GLOBAL_MAX_CONCURRENT_REQ", "GLOBAL_MAX_CONCURRENT_REQ_TIME",
})
#: Respuestas seguidas de limitación de ritmo que cortan el barrido.
MAX_CONSECUTIVE_RATE_LIMITED = 3
MIN_INTERVAL_MINUTES = 5
#: Job RQ: un lote de 20 con esperas y reintentos cabe de sobra en 30 min.
JOB_TIMEOUT_SECONDS = 1800
_SPAIN = ("ES", "ESPAÑA", "ESPANA", "SPAIN")

Checker = Callable[..., ViesResult | None]


# --- pausa anti-bloqueo ---------------------------------------------------------------


class MemoryPauseStore:
    """Pausa en memoria (tests y fallback si Redis no responde)."""

    def __init__(self) -> None:
        self._until: datetime | None = None
        self._lock = threading.Lock()

    def get(self) -> datetime | None:
        with self._lock:
            return self._until

    def set(self, until: datetime | None) -> None:
        with self._lock:
            self._until = until


class RedisPauseStore:
    """Pausa compartida entre procesos (api + worker) en Redis, con caída a
    memoria si Redis no responde."""

    def __init__(self, fallback: MemoryPauseStore) -> None:
        self._fallback = fallback

    def get(self) -> datetime | None:
        try:
            from app.workers.queues import redis_connection  # noqa: PLC0415

            raw = redis_connection().get(PAUSE_KEY)
            if raw:
                return datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw)
            return None
        except Exception:  # noqa: BLE001
            return self._fallback.get()

    def set(self, until: datetime | None) -> None:
        self._fallback.set(until)
        try:
            from app.workers.queues import redis_connection  # noqa: PLC0415

            conn = redis_connection()
            if until is None:
                conn.delete(PAUSE_KEY)
            else:
                ttl = max(int((until - datetime.now(UTC)).total_seconds()), 1)
                conn.set(PAUSE_KEY, until.isoformat(), ex=ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vies.sweep: no se pudo guardar la pausa en redis: %s", exc)


_memory_pause = MemoryPauseStore()
default_pause_store = RedisPauseStore(_memory_pause)


# --- selección ----------------------------------------------------------------------------


def _sql_vat_key(column):  # noqa: ANN001, ANN202 — misma normalización que `find_companies_by_nif`
    stripped = column
    for sep in (" ", ".", "-"):
        stripped = func.replace(stripped, sep, "")
    return func.upper(stripped)


def sweep_candidates(
    session: Session, *, now: datetime | None = None, limit: int = 20,
) -> list[Company]:
    """Empresas que deberían tener veredicto y no lo tienen: UE (no España),
    con NIF-IVA, sin `valido` / `no_valido` para el NIF-IVA actual, y cuyo
    reintento ya toca. Las nunca consultadas primero."""
    now = now or datetime.now(UTC)
    firm = (VIES_VALIDO, VIES_NO_VALIDO)
    stmt = (
        select(Company)
        .where(
            Company.is_active.is_(True),
            Company.country.is_not(None),
            func.upper(Company.country).notin_(_SPAIN),
            or_(Company.vat.is_not(None), Company.tax_id.is_not(None)),
            or_(
                Company.vies_status.is_(None),
                Company.vies_status.notin_(firm),
                # Veredicto firme de OTRO NIF-IVA: cambió desde la validación.
                and_(Company.vat.is_not(None), Company.vies_vat != _sql_vat_key(Company.vat)),
            ),
            or_(Company.vies_next_retry_at.is_(None), Company.vies_next_retry_at <= now),
        )
        .order_by(Company.vies_checked_at.asc(), Company.id.asc())
    )
    picked: list[Company] = []
    # País / NIF-IVA se normalizan en Python (`company_eu_vat`): fuera de la
    # UE no cuenta. Se recorre por trozos hasta reunir el lote.
    for company in session.scalars(stmt).yield_per(200):
        if not company_eu_vat(company):
            continue
        picked.append(company)
        if len(picked) >= limit:
            break
    return picked


# --- barrido --------------------------------------------------------------------------------


def run_sweep(
    session: Session, *, now: datetime | None = None, batch: int | None = None,
    spacing: float | None = None, sleeper: Callable[[float], None] | None = None,
    checker: Checker | None = None, pause_store: Any = None,
    pause_minutes: int | None = None,
) -> dict[str, Any]:
    """Un barrido: consulta como mucho `batch` empresas pendientes, de una en
    una y espaciadas, guardando cada resultado. Devuelve el resumen (que
    también va al log)."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    batch = batch if batch is not None else int(getattr(settings, "vies_sweep_batch", 20))
    spacing = spacing if spacing is not None else float(
        getattr(settings, "vies_sweep_spacing_seconds", 2.0))
    pause_minutes = pause_minutes if pause_minutes is not None else int(
        getattr(settings, "vies_sweep_pause_minutes", 60))
    wait = sleeper or time.sleep
    check = checker or check_vat_live
    store = pause_store or default_pause_store
    stats: dict[str, Any] = {
        "candidates": 0, "checked": 0, VIES_VALIDO: 0, VIES_NO_VALIDO: 0,
        VIES_DESCONOCIDO: 0, "pending": 0, "stopped": None, "paused_until": None,
    }
    enabled = (getattr(settings, "vies_enabled", True)
               and getattr(settings, "vies_sweep_enabled", True))
    if not enabled:
        stats["stopped"] = "disabled"
        logger.info("vies.sweep: desactivado (VIES_ENABLED / VIES_SWEEP_ENABLED)")
        return stats
    paused_until = store.get()
    if paused_until is not None and paused_until > now:
        stats["stopped"] = "paused"
        stats["paused_until"] = paused_until.isoformat()
        logger.info("vies.sweep: en pausa hasta %s (VIES nos frenó)", paused_until.isoformat())
        return stats

    # Todas las que ya toca (acotado) para poder decir cuántas quedan; se
    # consulta solo el lote.
    due = sweep_candidates(session, now=now, limit=max(batch, 1) * 50)
    candidates = due[:batch]
    stats["candidates"] = len(due)
    consecutive_rate_limited = 0
    for index, company in enumerate(candidates):
        if index:
            wait(spacing)
        vat = company_eu_vat(company)
        result = check(vat, force=True, country_code=normalize_country(company.country))
        if result is None:
            stats["stopped"] = "disabled"
            break
        apply_result(company, result, now=now)
        session.commit()
        stats["checked"] += 1
        stats[result.status] = stats.get(result.status, 0) + 1
        logger.info("vies.sweep: %s (%s) %s → %s%s", company.name, company.id, vat,
                    result.status, f" ({result.error})" if result.error else "")
        codes = set(result.error_codes)
        if codes & BLOCK_ERRORS:
            until = now + timedelta(minutes=pause_minutes)
            store.set(until)
            stats["stopped"] = "blocked"
            stats["paused_until"] = until.isoformat()
            logger.warning("vies.sweep: VIES nos frena (%s): pausa hasta %s",
                           ", ".join(sorted(codes & BLOCK_ERRORS)), until.isoformat())
            break
        if result.rate_limited:
            consecutive_rate_limited += 1
            if consecutive_rate_limited >= MAX_CONSECUTIVE_RATE_LIMITED:
                stats["stopped"] = "rate_limited"
                logger.warning("vies.sweep: %d respuestas seguidas de limitación de ritmo: "
                               "se corta el barrido, el resto en el siguiente",
                               consecutive_rate_limited)
                break
        else:
            consecutive_rate_limited = 0

    # Pendientes = sin veredicto tras consultarlas + las que no se llegaron a
    # consultar en este barrido (lote, corte o pausa).
    stats["pending"] = stats[VIES_DESCONOCIDO] + (len(due) - stats["checked"])
    logger.info(
        "vies.sweep: %d candidatas, %d consultadas → %d válidas, %d no válidas, "
        "%d sin veredicto; %d siguen pendientes%s",
        stats["candidates"], stats["checked"], stats[VIES_VALIDO], stats[VIES_NO_VALIDO],
        stats[VIES_DESCONOCIDO], stats["pending"],
        f" · parado: {stats['stopped']}" if stats["stopped"] else "",
    )
    return stats


# --- arming (job RQ self-rescheduling) ------------------------------------------------------


def _interval() -> timedelta:
    minutes = int(getattr(get_settings(), "vies_sweep_interval_minutes", 30) or 30)
    return timedelta(minutes=max(minutes, MIN_INTERVAL_MINUTES))


def schedule_sweep() -> None:
    """Arma el siguiente barrido `interval` más tarde. Idempotente vía SETNX
    (api y worker pueden llamarlo a la vez)."""
    interval = _interval()
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        lock_ttl = max(int(interval.total_seconds()) - 30, 10)
        if not conn.set(LOCK_KEY, "1", nx=True, ex=lock_ttl):
            return
        try:
            Queue(SWEEP_QUEUE, connection=conn, default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(
                interval, _sweep_runner,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("vies.sweep arm failed: %s", exc)
            conn.delete(LOCK_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("vies.sweep redis unreachable: %s", exc)


def _sweep_runner() -> None:
    """Entrada RQ. El re-arm va en `finally` para que un fallo no mate la
    cadena hasta el siguiente reinicio del API."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_sweep(session)
    except Exception:  # noqa: BLE001
        logger.exception("vies.sweep failed")
    finally:
        schedule_sweep()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_sweep()
    except Exception as exc:  # noqa: BLE001
        logger.warning("vies.sweep arm failed: %s", exc)


# --- a mano -------------------------------------------------------------------------------------


def _main(argv: list[str], out: Callable[[str], None] = print) -> int:
    """`python -m app.services.vies_sweep [--dry-run] [--batch N]`: un barrido
    ahora (o, con `--dry-run`, solo la lista de candidatas)."""
    from app.db.session import get_engine  # noqa: PLC0415

    args = list(argv)
    dry_run = "--dry-run" in args
    batch: int | None = None
    if "--batch" in args:
        i = args.index("--batch")
        batch = int(args[i + 1])
    with Session(get_engine()) as session:
        if dry_run:
            rows = sweep_candidates(session, limit=batch or 1000)
            out(f"{len(rows)} empresas pendientes de veredicto VIES que ya toca reintentar:")
            for c in rows:
                siguiente = c.vies_next_retry_at.isoformat() if c.vies_next_retry_at else "—"
                out(f"  {c.id}  {c.name}  {company_eu_vat(c)}  estado={c.vies_status or '—'}  "
                    f"intentos={c.vies_attempts or 0}  siguiente={siguiente}")
            return 0
        stats = run_sweep(session, batch=batch)
    out(str(stats))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    raise SystemExit(_main(sys.argv[1:]))
