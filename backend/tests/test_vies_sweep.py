"""Fase VIES — revalidación en segundo plano de los NIF-IVA «pendiente».

El barrido (`app.services.vies_sweep`) reintenta, de una en una y espaciadas,
las empresas de la UE con NIF-IVA y sin veredicto firme, guarda el progreso
con backoff por empresa, corta ante saturación seguida y se pausa si VIES nos
bloquea. Nada llega a ec.europa.eu: el cliente VIES va simulado.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.integrations.vies.client import ViesResult
from app.models.crm import Company
from app.services import vies_sweep
from app.services.vies_sweep import MemoryPauseStore, run_sweep, sweep_candidates
from tests.test_agilecrm_scheduler_queue import _all_worker_queues_from_compose

T0 = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
FR1, BE1, NL1, IT1, DE1, FR2 = (
    "FR90501738249", "BE0812240188", "NL123456789B01", "IT12345678901",
    "DE455128445", "FR16339753527",
)


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as s:
        s.add_all([
            # Pendientes que deberían tener veredicto:
            Company(id="fr1", name="SAS BANDIT BANDIT", country="FR", vat=FR1,
                    vies_status="desconocido", vies_vat=FR1, vies_attempts=1,
                    vies_checked_at=T0 - timedelta(hours=3)),
            # Nunca consultada; y una con el NIF-IVA en `tax_id` (con prefijo):
            Company(id="be1", name="Ligue Braille", country="BE", vat=BE1),
            Company(id="fr2", name="La Maison de la Plaque", country="Francia", tax_id=FR2),
            # Veredicto firme de OTRO NIF-IVA (cambió desde la validación):
            Company(id="nl1", name="Cadeau BV", country="NL", vat=NL1,
                    vies_status="valido", vies_vat="NL999999999B01",
                    vies_checked_at=T0 - timedelta(days=10)),
            # No entran:
            Company(id="es1", name="Duplicoder SL", country="ES", tax_id="B12345678"),
            Company(id="no1", name="Nordic AS", country="NO", tax_id="987654321"),
            Company(id="de1", name="Berlin GmbH", country="DE", vat=DE1,
                    vies_status="valido", vies_vat=DE1, vies_checked_at=T0 - timedelta(days=1)),
            Company(id="it1", name="Roma SRL", country="IT", vat=IT1,
                    vies_status="desconocido", vies_vat=IT1, vies_attempts=2,
                    vies_checked_at=T0 - timedelta(minutes=10),
                    vies_next_retry_at=T0 + timedelta(hours=2)),                # aún no toca
            Company(id="fr_off", name="Cerrada SAS", country="FR", vat="FR11111111111",
                    is_active=False),
            Company(id="fr_sin", name="Sin NIF SAS", country="FR"),
        ])
        s.commit()
    yield factory
    Base.metadata.drop_all(engine)


class FakeChecker:
    """`check_vat_live` simulado: veredicto por NIF-IVA, cuenta llamadas y
    vigila que nunca haya dos en vuelo."""

    def __init__(self, verdicts: dict[str, str] | None = None):
        self.verdicts = verdicts or {}
        self.calls: list[tuple[str, bool, str | None]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def __call__(self, vat: str, *, force: bool = False, country_code: str | None = None):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.calls.append((vat, force, country_code))
        verdict = self.verdicts.get(vat, "VIES HTTP 500")
        now = datetime.now(UTC)
        if verdict == "valido":
            result = ViesResult(status="valido", valid=True, vat=vat, country_code=vat[:2],
                                number=vat[2:], name=f"{vat} SA", checked_at=now)
        elif verdict == "no_valido":
            result = ViesResult(status="no_valido", valid=False, vat=vat, country_code=vat[:2],
                                number=vat[2:], error="VIES: NIF-IVA no dado de alta",
                                checked_at=now)
        elif verdict.isupper() and "_" in verdict:                     # código de error VIES
            result = ViesResult(status="desconocido", valid=None, vat=vat, country_code=vat[:2],
                                number=vat[2:], error=verdict, checked_at=now,
                                error_codes=(verdict,))
        else:
            result = ViesResult(status="desconocido", valid=None, vat=vat, country_code=vat[:2],
                                number=vat[2:], error=verdict, checked_at=now)
        self.in_flight -= 1
        return result


def _settings(**over: Any) -> SimpleNamespace:
    base = {"vies_enabled": True, "vies_sweep_enabled": True, "vies_sweep_batch": 20,
            "vies_sweep_spacing_seconds": 2.0, "vies_sweep_pause_minutes": 60,
            "vies_sweep_interval_minutes": 30}
    return SimpleNamespace(**{**base, **over})


def _sweep(session_factory, checker, *, now=T0, store=None, settings=None, **kw) -> dict[str, Any]:
    waited: list[float] = kw.pop("waited", [])
    with patch.object(vies_sweep, "get_settings", return_value=settings or _settings()), \
            session_factory() as s:
        return run_sweep(s, now=now, checker=checker, sleeper=waited.append,
                         pause_store=store or MemoryPauseStore(), **kw)


def _company(session_factory, cid: str) -> Company:
    with session_factory() as s:
        c = s.get(Company, cid)
        s.expunge(c)
        return c


# --- selección --------------------------------------------------------------------


def test_vies_sweep_no_toca_es_ni_no_ue(session_factory) -> None:
    """Solo UE fuera de España con NIF-IVA y sin veredicto firme para su
    NIF-IVA actual; las nunca consultadas primero. España, fuera de la UE,
    las firmes, las que aún no toca, las inactivas y las sin NIF no entran."""
    with session_factory() as s:
        ids = [c.id for c in sweep_candidates(s, now=T0, limit=50)]
    assert ids == ["be1", "fr2", "nl1", "fr1"]
    checker = FakeChecker({FR1: "valido", BE1: "valido", FR2: "valido", NL1: "valido"})
    _sweep(session_factory, checker)
    vats = {c[0] for c in checker.calls}
    assert vats == {FR1, BE1, FR2, NL1}
    assert not [c for c in checker.calls if c[2] in ("ES", "NO", "DE", "IT")]


# --- revalidación ------------------------------------------------------------------------


def test_vies_sweep_revalida_pendientes(session_factory) -> None:
    """Las `desconocido` / nunca consultadas de la UE se reintentan; VIES da
    válido → `valido` (y no válido → `no_valido`); ya no vuelven a entrar."""
    checker = FakeChecker({FR1: "valido", BE1: "no_valido", FR2: "valido"})
    stats = _sweep(session_factory, checker)
    assert stats["candidates"] == 4 and stats["checked"] == 4
    assert stats["valido"] == 2 and stats["no_valido"] == 1 and stats["desconocido"] == 1
    assert stats["pending"] == 1 and stats["stopped"] is None
    fr1 = _company(session_factory, "fr1")
    assert fr1.vies_status == "valido" and fr1.vies_name == f"{FR1} SA"
    assert fr1.vies_attempts == 0 and fr1.vies_next_retry_at is None
    assert _company(session_factory, "be1").vies_status == "no_valido"
    assert _company(session_factory, "fr2").vies_vat == FR2                # NIF con prefijo
    nl1 = _company(session_factory, "nl1")                                # sin veredicto aún
    assert nl1.vies_status == "desconocido" and nl1.vies_vat == NL1 and nl1.vies_attempts == 1
    # Todas van forzadas (saltan la caché en proceso) y con el país de la empresa.
    assert all(force is True for _, force, _ in checker.calls)
    assert (FR2, True, "FR") in checker.calls
    # Siguiente barrido: las firmes no vuelven; nl1 espera su backoff.
    again = _sweep(session_factory, checker, now=T0 + timedelta(minutes=5))
    assert again["candidates"] == 0 and len(checker.calls) == 4


def test_vies_sweep_serializa_y_espacia(session_factory) -> None:
    """De una en una (nunca dos en vuelo), con espera entre llamadas, y como
    mucho el lote; el resto queda para el siguiente barrido."""
    checker = FakeChecker({BE1: "valido", FR2: "valido", NL1: "valido", FR1: "valido"})
    waited: list[float] = []
    stats = _sweep(session_factory, checker, batch=2, spacing=1.5, waited=waited)
    assert stats["checked"] == 2 and [c[0] for c in checker.calls] == [BE1, FR2]
    assert waited == [1.5]                                      # entre la 1ª y la 2ª
    assert checker.max_in_flight == 1
    assert stats["pending"] == 2                                # las otras dos, luego
    stats = _sweep(session_factory, checker, batch=2, spacing=1.5, waited=waited)
    assert [c[0] for c in checker.calls] == [BE1, FR2, NL1, FR1]
    assert waited == [1.5, 1.5]


def test_vies_sweep_backoff_por_empresa(session_factory) -> None:
    """La que sigue sin veredicto no se reintenta en el siguiente barrido:
    espera 30 min, 1 h, 2 h… (tope 24 h), sin consultarla cada 30 min."""
    checker = FakeChecker()                                     # todo → desconocido
    _sweep(session_factory, checker, batch=1)                   # be1, nunca consultada
    be1 = _company(session_factory, "be1")
    assert be1.vies_status == "desconocido" and be1.vies_attempts == 1
    assert be1.vies_next_retry_at == T0 + timedelta(minutes=30)
    # 5 min después: no toca.
    _sweep(session_factory, checker, now=T0 + timedelta(minutes=5), batch=1)
    assert [c[0] for c in checker.calls].count(BE1) == 1
    # Tras el backoff sí, y el siguiente espera más (1 h), luego 2 h… hasta 24 h.
    _sweep(session_factory, checker, now=T0 + timedelta(minutes=31), batch=50)
    be1 = _company(session_factory, "be1")
    assert be1.vies_attempts == 2
    assert be1.vies_next_retry_at == T0 + timedelta(minutes=31) + timedelta(hours=1)
    with session_factory() as s:
        c = s.get(Company, "be1")
        c.vies_attempts = 9
        c.vies_next_retry_at = None
        s.commit()
    _sweep(session_factory, checker, now=T0 + timedelta(days=1), batch=50)
    be1 = _company(session_factory, "be1")
    assert be1.vies_attempts == 10
    assert be1.vies_next_retry_at == T0 + timedelta(days=1) + timedelta(hours=24)
    # fr1 venía con 1 intento: consultada a T0+31 min (→ 2, espera 1 h) y al
    # día siguiente (→ 3, espera 2 h).
    fr1 = _company(session_factory, "fr1")
    assert fr1.vies_attempts == 3
    assert fr1.vies_next_retry_at == T0 + timedelta(days=1) + timedelta(hours=2)


def test_vies_sweep_ip_blocked_pausa(session_factory) -> None:
    """Ante bloqueo global (`IP_BLOCKED` / `GLOBAL_MAX_CONCURRENT_REQ`) el
    barrido para, nada se marca «no válido», y los barridos se pausan un
    rato; luego se reanudan."""
    checker = FakeChecker({BE1: "IP_BLOCKED", FR2: "valido"})
    store = MemoryPauseStore()
    stats = _sweep(session_factory, checker, store=store)
    assert stats["stopped"] == "blocked" and stats["checked"] == 1
    assert [c[0] for c in checker.calls] == [BE1]                   # fr2 ya no se consulta
    assert _company(session_factory, "be1").vies_status == "desconocido"
    assert store.get() == T0 + timedelta(minutes=60)
    assert stats["paused_until"] == (T0 + timedelta(minutes=60)).isoformat()
    # En pausa: ni una llamada.
    paused = _sweep(session_factory, checker, now=T0 + timedelta(minutes=10), store=store)
    assert paused["stopped"] == "paused" and len(checker.calls) == 1
    # Pasada la pausa se reanuda.
    _sweep(session_factory, checker, now=T0 + timedelta(minutes=61), store=store)
    assert len(checker.calls) > 1 and (FR2, True, "FR") in checker.calls
    assert _company(session_factory, "fr2").vies_status == "valido"
    # `GLOBAL_MAX_CONCURRENT_REQ` también pausa.
    checker2 = FakeChecker({BE1: "GLOBAL_MAX_CONCURRENT_REQ"})
    store2 = MemoryPauseStore()
    with session_factory() as s:
        s.get(Company, "be1").vies_next_retry_at = None
        s.commit()
    stats = _sweep(session_factory, checker2, now=T0 + timedelta(days=2), store=store2,
                   pause_minutes=15)
    assert stats["stopped"] == "blocked"
    assert store2.get() == T0 + timedelta(days=2, minutes=15)


def test_vies_sweep_corta_tras_saturacion_seguida(session_factory) -> None:
    """Tres `MS_MAX_CONCURRENT_REQ` seguidas cortan el barrido (el resto en el
    siguiente), sin pausa larga y sin marcar «no válido»."""
    checker = FakeChecker({BE1: "MS_MAX_CONCURRENT_REQ", FR2: "MS_MAX_CONCURRENT_REQ",
                           NL1: "MS_MAX_CONCURRENT_REQ", FR1: "valido"})
    store = MemoryPauseStore()
    stats = _sweep(session_factory, checker, store=store)
    assert stats["stopped"] == "rate_limited" and stats["checked"] == 3
    assert [c[0] for c in checker.calls] == [BE1, FR2, NL1]
    assert store.get() is None
    for cid in ("be1", "fr2", "nl1"):
        assert _company(session_factory, cid).vies_status == "desconocido"
    # Una saturación aislada no corta: se sigue con la siguiente.
    checker = FakeChecker({FR1: "MS_MAX_CONCURRENT_REQ"})
    with session_factory() as s:
        for cid in ("be1", "fr2", "nl1"):
            s.get(Company, cid).vies_next_retry_at = None
        s.commit()
    stats = _sweep(session_factory, checker, store=store)
    assert stats["checked"] == 4 and stats["stopped"] is None


def test_vies_sweep_desactivado_no_llama(session_factory) -> None:
    checker = FakeChecker({BE1: "valido"})
    stats = _sweep(session_factory, checker, settings=_settings(vies_sweep_enabled=False))
    assert stats["stopped"] == "disabled" and checker.calls == []
    stats = _sweep(session_factory, checker, settings=_settings(vies_enabled=False))
    assert stats["stopped"] == "disabled" and checker.calls == []


# --- arming + worker ---------------------------------------------------------------------


def test_vies_sweep_se_arma_en_la_cola_vies_sweep() -> None:
    """El heartbeat se encola en `vies:sweep` cada N minutos, con guard SETNX
    (una segunda llamada mientras el lock vive no encola otro)."""
    captured: dict[str, Any] = {}

    class FakeRedis:
        def __init__(self) -> None:
            self.keys: dict[str, str] = {}

        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.keys:
                return False
            self.keys[key] = value
            captured["lock_key"], captured["lock_ttl"] = key, ex
            return True

        def delete(self, key):
            self.keys.pop(key, None)

    class FakeQueue:
        def __init__(self, name, connection=None, default_timeout=None):
            captured["queue"] = name
            captured["timeout"] = default_timeout

        def enqueue_in(self, interval, fn):
            captured["interval"], captured["fn"] = interval, fn
            captured["enqueued"] = captured.get("enqueued", 0) + 1

    conn = FakeRedis()
    with patch("app.workers.queues.redis_connection", return_value=conn), \
            patch("rq.Queue", FakeQueue), \
            patch.object(vies_sweep, "get_settings",
                         return_value=_settings(vies_sweep_interval_minutes=30)):
        vies_sweep.schedule_sweep()
        vies_sweep.schedule_sweep()                  # lock vivo → no encola otro
    assert captured["queue"] == "vies:sweep"
    assert captured["interval"] == timedelta(minutes=30)
    assert captured["fn"] is vies_sweep._sweep_runner
    assert captured["lock_key"] == "vies:sweep:heartbeat" and captured["lock_ttl"] == 1770
    assert captured["timeout"] == vies_sweep.JOB_TIMEOUT_SECONDS
    assert captured["enqueued"] == 1


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_un_worker_escucha_vies_sweep(compose: str) -> None:
    """Sin consumidor de `vies:sweep` el barrido no correría nunca."""
    repo_root = Path(__file__).resolve().parents[2]
    queues = _all_worker_queues_from_compose(repo_root / compose)
    assert "vies:sweep" in queues, f"ningún worker escucha vies:sweep en {compose}"
