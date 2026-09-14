"""Fase VIES (rediseño de flujo) — validar el NIF-IVA en el servicio oficial
de la UE y usar el veredicto para confirmar (o impedir) el régimen
intracomunitario.

- Cliente: válido / no válido / VIES caído → `desconocido` sin romper; caché
  y `force`.
- Empresa: el resultado se guarda al crear / editar; `vies_valid` solo cuenta
  si es firme y del NIF-IVA actual; «Revalidar en VIES».
- Régimen: válido → intracomunitario confirmado; no válido → NO se puede
  eximir → nacional con IVA (también en la ficha F_CLI que se propone) y
  alerta bloqueante en el `workflow` del pedido; fuera de la UE → exportación
  sin consultar VIES; VIES caído → `desconocido`, se sigue por país + NIF-IVA
  con aviso «pendiente de validar».
"""
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderLine, OrderSource
from app.erp.workflow import order_workflow
from app.integrations.factusol.vat_regime import proposed_fcli_values, regime_for
from app.integrations.vies import client as vies_client
from app.integrations.vies.client import ViesResult, check_vat, clear_cache
from app.main import app
from app.models.crm import Company
from app.services import vies as vies_service
from tests._test_helpers import auth_headers, seed_test_users

FR = "FR16339753527"
BE = "BE0812240188"


# --- cliente VIES ---------------------------------------------------------------


class _Transport:
    """Transporte simulado: `(url, json) -> (status, body)` con contador."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, payload: dict[str, Any]) -> tuple[int, Any]:
        self.calls.append(payload)
        return self.responder(payload)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Generator[None, None, None]:
    clear_cache()
    yield
    clear_cache()


def test_cliente_valido(monkeypatch) -> None:
    t = _Transport(lambda p: (200, {"valid": True, "name": "SAS LA MAISON DE LA PLAQUE",
                                    "address": "RUE DU CHEMIN NOIR\n21120 IS-SUR-TILLE",
                                    "userError": "VALID"}))
    r = check_vat("fr 16.339.753.527", transport=t)
    assert r.status == "valido" and r.valid is True
    assert t.calls == [{"countryCode": "FR", "vatNumber": "16339753527"}]
    assert r.vat == FR and r.name == "SAS LA MAISON DE LA PLAQUE"
    assert r.address == "RUE DU CHEMIN NOIR 21120 IS-SUR-TILLE"     # saltos → espacio
    assert r.checked_at is not None and r.from_cache is False


def _valid_transport() -> _Transport:
    return _Transport(lambda p: (200, {"valid": True, "name": "SAS BANDIT BANDIT",
                                       "address": "PARIS", "userError": "VALID"}))


@pytest.mark.parametrize("entrada,pais", [
    ("FR90501738249", None),
    ("FR90501738249", "FR"),
    ("FR 90 501 738 249", None),
    ("fr-90.501.738.249", "FR"),
    (" FR90501738249 ", "fr"),
    ("90501738249", "FR"),                       # ya sin prefijo: tal cual
])
def test_vies_normaliza_quita_prefijo_pais(entrada, pais) -> None:
    """El API de VIES espera `countryCode=FR` + `vatNumber` SIN el prefijo
    (con `FR90501738249` dentro del número responde «no válido»; la web lo
    quita sola, el API no). Todas las variantes envían lo mismo y salen
    válidas → intracomunitario."""
    t = _valid_transport()
    r = check_vat(entrada, country_code=pais, transport=t, force=True)
    assert t.calls == [{"countryCode": "FR", "vatNumber": "90501738249"}]
    assert r.status == "valido" and r.valid is True
    assert r.vat == "FR90501738249" and r.name == "SAS BANDIT BANDIT"
    # Con ese veredicto el régimen es intracomunitario (exento). (La regla de
    # régimen sigue leyendo el NIF-IVA del CRM con su prefijo, como siempre.)
    assert regime_for("FR", vat=r.vat, vies_valid=r.valid) == "intracomunitario"


def test_vies_numero_sin_prefijo_sigue_ok() -> None:
    """Un número que ya viene sin prefijo se envía tal cual (con el país de la
    empresa) y comparte caché con la forma con prefijo."""
    t = _valid_transport()
    a = check_vat("90501738249", country_code="FR", transport=t)
    b = check_vat("FR90501738249", transport=t)
    assert t.calls == [{"countryCode": "FR", "vatNumber": "90501738249"}]
    assert a.status == "valido" and b.status == "valido" and b.from_cache is True
    # Sin país y sin prefijo no hay forma de saber a qué estado preguntar.
    assert vies_client.vies_request_parts("90501738249") is None
    assert check_vat("90501738249", transport=t).status == "no_valido"
    assert len(t.calls) == 1


def test_vies_es_prefijo() -> None:
    """España: el NIF puede venir con `ES` delante o sin él; solo se quita el
    prefijo si el número empieza por el código de país, sin confundirlo con
    la primera letra del propio NIF."""
    parts = vies_client.vies_request_parts
    assert parts("ESB12345678", country_code="ES") == ("ES", "B12345678")
    assert parts("es b-12.345.678", country_code="ES") == ("ES", "B12345678")
    assert parts("B12345678", country_code="ES") == ("ES", "B12345678")
    assert parts("ESB12345678") == ("ES", "B12345678")
    assert parts("E12345678", country_code="ES") == ("ES", "E12345678")   # letra E ≠ prefijo ES
    assert parts("B12345678") is None                                      # sin país no se sabe
    # Grecia: VIES usa `EL`; en el CRM puede venir `GR`.
    assert parts("EL123456789", country_code="GR") == ("EL", "123456789")
    assert parts("GR123456789", country_code="GR") == ("EL", "123456789")
    assert parts("EL123456789") == ("EL", "123456789")
    t = _valid_transport()
    check_vat("ESB12345678", country_code="ES", transport=t)
    check_vat("B12345678", country_code="ES", transport=t, force=True)
    assert t.calls == [{"countryCode": "ES", "vatNumber": "B12345678"}] * 2


def test_cliente_no_valido() -> None:
    t = _Transport(lambda p: (200, {"actionSucceed": True, "valid": False, "name": "---",
                                    "address": "---", "userError": "VALID"}))
    r = check_vat("BE0999999999", transport=t)
    assert r.status == "no_valido" and r.valid is False
    assert r.name is None and r.address is None                       # «---» = nada
    # Formato imposible: no se llama a VIES, no válido directamente.
    r2 = check_vat("12345", transport=t)
    assert r2.status == "no_valido" and len(t.calls) == 1


# --- lectura de la respuesta: solo dos veredictos reales -----------------------------


BANDIT = "FR90501738249"
#: Respuesta REAL de VIES en el VPS para FR90501738249 (Francia limitando
#: peticiones): no es un veredicto sobre el número.
MS_MAX_BODY = {"actionSucceed": False, "errorWrappers": [{"error": "MS_MAX_CONCURRENT_REQ"}]}


def _no_sleep(_seconds: float) -> None:
    return None


def test_vies_ms_max_concurrent_req_es_desconocido(http, session_factory) -> None:
    """`MS_MAX_CONCURRENT_REQ` → `desconocido` (pendiente de validar), NUNCA
    «no válido»: el régimen sigue por país + NIF-IVA y el pedido no se
    bloquea."""
    t = _Transport(lambda p: (200, MS_MAX_BODY))
    r = check_vat(BANDIT, transport=t, sleeper=_no_sleep)
    assert r.status == "desconocido" and r.valid is None
    assert r.error == "MS_MAX_CONCURRENT_REQ" and r.rate_limited is True
    assert t.calls[0] == {"countryCode": "FR", "vatNumber": "90501738249"}
    assert regime_for("FR", vat=BANDIT, vies_valid=r.valid) == "intracomunitario"

    # De punta a punta: la empresa queda «desconocido» y el pedido sale sin bloqueo.
    with session_factory() as s:
        s.add(Company(id="bandit", name="SAS BANDIT BANDIT", country="FR", vat=BANDIT,
                      factusol_company_id="4471"))
        s.commit()
        _order(s, oid="o4", company_id="bandit")
    settings = SimpleNamespace(vies_enabled=True, vies_base_url="http://vies.test",
                               vies_timeout_seconds=1.0)
    real_check = vies_client.check_vat

    def check_with_fake_transport(vat, **kw):
        kw.pop("base_url", None), kw.pop("timeout", None)
        return real_check(vat, transport=t, sleeper=_no_sleep, **kw)

    h = auth_headers(http, "user")
    with patch.object(vies_service, "get_settings", return_value=settings), \
            patch.object(vies_service, "check_vat", check_with_fake_transport):
        r = http.post("/api/companies/bandit/vies-revalidate", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["vies"]["status"] == "desconocido" and r.json()["regime"] == "intracomunitario"
    assert r.json()["company"]["vies_status"] == "desconocido"
    with session_factory() as s:
        wf = order_workflow(s, s.get(Order, "o4"))
    assert wf["blocked"] is False and wf["regime"] == "intracomunitario"
    assert not [a for a in wf["alerts"] if a["code"] == "vat_no_valido_vies"]
    aviso = next(a for a in wf["alerts"] if a["code"] == "cliente_intracomunitario")
    assert "VIES no respondió" in aviso["text"]


@pytest.mark.parametrize("body", [
    {"actionSucceed": False, "errorWrappers": [{"error": "GLOBAL_MAX_CONCURRENT_REQ"}]},
    {"actionSucceed": False, "errorWrappers": [{"error": "MS_UNAVAILABLE"}]},
    {"actionSucceed": False, "errorWrappers": [{"error": "SERVICE_UNAVAILABLE"}]},
    {"actionSucceed": False, "errorWrappers": [{"error": "TIMEOUT"}]},
    {"actionSucceed": False, "errorWrappers": [{"error": "VAT_BLOCKED"}]},
    {"actionSucceed": False, "errorWrappers": [{"error": "IP_BLOCKED"}]},
    {"actionSucceed": False, "errorWrappers": [{"error": "INVALID_REQUESTER_INFO"}]},
    {"actionSucceed": True, "valid": False, "errorWrappers": [{"error": "MS_UNAVAILABLE"}]},
    {"valid": False, "userError": "MS_UNAVAILABLE"},            # formato clásico
    {"valid": False, "userError": "INVALID_INPUT"},
    {"actionSucceed": False},                                     # sin detalle
    {"valid": False},                                             # sin actionSucceed ni userError
    {},                                                           # sin veredicto
])
def test_vies_error_wrappers_varios_son_desconocido(body) -> None:
    """Cualquier `errorWrappers` / `actionSucceed:false` / respuesta sin
    veredicto claro → `desconocido`, nunca «no válido»."""
    t = _Transport(lambda p: (200, body))
    r = check_vat(BANDIT, transport=t, force=True, sleeper=_no_sleep)
    assert r.status == "desconocido" and r.valid is None, body
    assert r.error


def test_vies_valid_true_es_valido() -> None:
    for body in (
        {"actionSucceed": True, "valid": True, "name": "SAS BANDIT BANDIT", "address": "PARIS"},
        {"valid": True, "userError": "VALID", "name": "SAS BANDIT BANDIT"},
        {"valid": "true", "name": "SAS BANDIT BANDIT"},
    ):
        t = _Transport(lambda p, b=body: (200, b))
        r = check_vat(BANDIT, transport=t, force=True)
        assert r.status == "valido" and r.valid is True, body
        assert r.name == "SAS BANDIT BANDIT"
    assert regime_for("FR", vat=BANDIT, vies_valid=True) == "intracomunitario"


def test_vies_action_succeed_valid_false_es_no_valido() -> None:
    """El ÚNICO «no válido»: petición con éxito y `valid: false`."""
    t = _Transport(lambda p: (200, {"actionSucceed": True, "valid": False, "name": "---",
                                    "address": "---"}))
    r = check_vat(BANDIT, transport=t, force=True)
    assert r.status == "no_valido" and r.valid is False
    assert regime_for("FR", vat=BANDIT, vies_valid=False) == "nacional"
    # Formato clásico sin `actionSucceed`: solo con `userError: VALID` explícito.
    t2 = _Transport(lambda p: (200, {"valid": False, "userError": "VALID"}))
    assert check_vat(BANDIT, transport=t2, force=True).status == "no_valido"
    # Con `actionSucceed: false` un `valid: false` NO cuenta.
    t3 = _Transport(lambda p: (200, {"actionSucceed": False, "valid": False}))
    assert check_vat(BANDIT, transport=t3, force=True).status == "desconocido"


def test_vies_reintenta_ante_max_concurrent() -> None:
    """Limitación de ritmo: espera y reintenta (2 veces) antes de rendirse."""
    answers = [MS_MAX_BODY, MS_MAX_BODY,
               {"actionSucceed": True, "valid": True, "name": "SAS BANDIT BANDIT"}]
    t = _Transport(lambda p: (200, answers[len(t.calls) - 1]))
    waited: list[float] = []
    r = check_vat(BANDIT, transport=t, force=True, sleeper=waited.append)
    assert r.status == "valido" and len(t.calls) == 3
    assert waited == list(vies_client.RETRY_DELAYS)
    # Saturado todo el rato: se queda en desconocido tras los reintentos, sin
    # bloquear, y con caché corta (se reintentará luego).
    always = _Transport(lambda p: (200, MS_MAX_BODY))
    waited.clear()
    r = check_vat(BANDIT, transport=always, force=True, sleeper=waited.append)
    assert r.status == "desconocido" and len(always.calls) == 3 and len(waited) == 2
    # Otros errores (no de ritmo) no se reintentan.
    down = _Transport(lambda p: (200, {"actionSucceed": False,
                                       "errorWrappers": [{"error": "MS_UNAVAILABLE"}]}))
    waited.clear()
    r = check_vat(BANDIT, transport=down, force=True, sleeper=waited.append)
    assert r.status == "desconocido" and len(down.calls) == 1 and waited == []


def test_no_valido_guardado_antes_del_fix_se_reconsulta() -> None:
    """Los VAT que quedaron «no válido» por el bug (antes del fix) se vuelven
    a consultar al cargar la ficha aunque sean recientes."""
    before = vies_service.INVALID_VERDICTS_TRUSTED_FROM - timedelta(hours=1)
    suspect = _company(vies_status="no_valido", vies_vat=FR, vies_checked_at=before)
    assert vies_service.needs_vies_check(suspect, now=before + timedelta(minutes=5)) is True
    trusted = _company(vies_status="no_valido", vies_vat=FR,
                       vies_checked_at=vies_service.INVALID_VERDICTS_TRUSTED_FROM)
    assert vies_service.needs_vies_check(
        trusted, now=vies_service.INVALID_VERDICTS_TRUSTED_FROM + timedelta(hours=1)) is False


def test_diagnostico_interpreta_la_respuesta_real(capsys) -> None:
    """El comando de diagnóstico sigue funcionando, y con `--interpret` enseña
    cómo lee BoHub el cuerpo real que devolvió VIES en el VPS."""
    import json
    lines: list[str] = []
    code = vies_client._main([BANDIT, "--interpret", json.dumps(MS_MAX_BODY)], out=lines.append)
    text = "\n".join(lines)
    assert code == 1
    assert '{"countryCode": "FR", "vatNumber": "90501738249"}' in text
    assert "→ BoHub: desconocido (MS_MAX_CONCURRENT_REQ)" in text
    assert "pendiente de validar" in text
    lines.clear()
    ok = {"actionSucceed": True, "valid": True, "name": "SAS BANDIT BANDIT"}
    assert vies_client._main([BANDIT, "FR", "--interpret", json.dumps(ok)], out=lines.append) == 0
    assert "→ BoHub: valido" in "\n".join(lines)
    assert vies_client._main([], out=lines.append) == 2


@pytest.mark.parametrize("respuesta", [
    (200, {"valid": False, "userError": "MS_UNAVAILABLE"}),      # estado miembro caído
    (200, {"valid": False, "userError": "SERVICE_UNAVAILABLE"}),
    (500, None),                                                   # VIES caído
    (200, "no es json"),
    "exception",
])
def test_cliente_vies_caido_es_desconocido(respuesta) -> None:
    """VIES caído / sin veredicto → `desconocido`, NUNCA «no válido» ni excepción."""
    def responder(_p):
        if respuesta == "exception":
            raise TimeoutError("read timed out")
        return respuesta
    r = check_vat(FR, transport=_Transport(responder))
    assert r.status == "desconocido" and r.valid is None
    assert r.error


def test_cliente_cachea_y_force_salta_la_cache(monkeypatch) -> None:
    t = _Transport(lambda p: (200, {"valid": True, "userError": "VALID"}))
    a = check_vat(FR, transport=t)
    b = check_vat(FR, transport=t)
    assert len(t.calls) == 1 and a.from_cache is False and b.from_cache is True
    c = check_vat(FR, transport=t, force=True)
    assert len(t.calls) == 2 and c.from_cache is False
    # «Desconocido» caduca antes (10 min) que un veredicto firme (24 h).
    now = [1000.0]
    monkeypatch.setattr(vies_client.time, "monotonic", lambda: now[0])
    down = _Transport(lambda p: (500, None))
    check_vat(BE, transport=down)                      # cacheado hasta 1600
    check_vat(BE, transport=down)
    assert len(down.calls) == 1
    now[0] += vies_client.CACHE_TTL_UNKNOWN_SECONDS + 1
    check_vat(BE, transport=down)                      # caducado → vuelve a llamar
    assert len(down.calls) == 2


# --- servicio: qué significa para la empresa ---------------------------------------


def _company(**kw) -> Company:
    base = {"id": "fr", "name": "La Maison", "country": "FR", "vat": FR}
    return Company(**{**base, **kw})


def test_company_vies_valid_solo_si_firme_y_del_nif_actual() -> None:
    now = datetime.now(UTC)
    assert vies_service.company_vies_valid(_company()) is None            # pendiente
    assert vies_service.company_vies_valid(
        _company(vies_status="valido", vies_vat=FR, vies_checked_at=now)) is True
    assert vies_service.company_vies_valid(
        _company(vies_status="no_valido", vies_vat=FR, vies_checked_at=now)) is False
    assert vies_service.company_vies_valid(
        _company(vies_status="desconocido", vies_vat=FR, vies_checked_at=now)) is None
    # El NIF-IVA cambió desde la validación → ya no cuenta (pendiente).
    stale = _company(vat="FR99999999999", vies_status="valido", vies_vat=FR, vies_checked_at=now)
    assert vies_service.company_vies_valid(stale) is None
    assert vies_service.vies_state(stale)["status"] == "pendiente"
    assert vies_service.vies_state(stale)["stale"] is True
    # Fuera de la UE / España / sin NIF-IVA: VIES no aplica.
    fuera = _company(country="NO", vat=None, tax_id="987")
    assert vies_service.vies_state(fuera)["applies"] is False
    espana = _company(country="ES", vat=None, tax_id="B1")
    assert vies_service.vies_state(espana)["applies"] is False
    assert vies_service.company_eu_vat(_company(country="FR", vat=None)) is None


def test_needs_vies_check_por_antiguedad() -> None:
    now = datetime.now(UTC)
    assert vies_service.needs_vies_check(_company()) is True
    fresh = _company(vies_status="valido", vies_vat=FR, vies_checked_at=now)
    assert vies_service.needs_vies_check(fresh) is False
    old = _company(vies_status="valido", vies_vat=FR, vies_checked_at=now - timedelta(days=31))
    assert vies_service.needs_vies_check(old) is True
    unknown = _company(vies_status="desconocido", vies_vat=FR,
                       vies_checked_at=now - timedelta(hours=2))
    assert vies_service.needs_vies_check(unknown) is True
    # «No válido» se reintenta al día (un veredicto negativo puede cambiar).
    # Con fechas posteriores al fix de la lectura: los anteriores se
    # reconsultan siempre (ver `test_no_valido_guardado_antes_del_fix_se_reconsulta`).
    after_fix = vies_service.INVALID_VERDICTS_TRUSTED_FROM + timedelta(days=3)
    invalid_fresh = _company(vies_status="no_valido", vies_vat=FR, vies_checked_at=after_fix)
    assert vies_service.needs_vies_check(invalid_fresh, now=after_fix + timedelta(hours=2)) is False
    assert vies_service.needs_vies_check(invalid_fresh, now=after_fix + timedelta(days=2)) is True
    assert vies_service.needs_vies_check(_company(country="NO")) is False


# --- régimen + ficha F_CLI -----------------------------------------------------------


def test_regimen_segun_vies() -> None:
    assert regime_for("FR", vat=FR, vies_valid=True) == "intracomunitario"
    assert regime_for("FR", vat=FR, vies_valid=None) == "intracomunitario"   # pendiente: sigue
    assert regime_for("FR", vat=FR, vies_valid=False) == "nacional"          # no se puede eximir
    assert regime_for("NO", nif="987654321", vies_valid=False) == "exportacion"  # VIES n/a
    # La ficha F_CLI propuesta también: con VAT no válido, cliente nacional con IVA.
    regime, values = proposed_fcli_values("FR", vat=FR, vies_valid=False)
    assert regime == "nacional" and values["IVACLI"] == 0 and values["TIVCLI"] == 1
    regime, values = proposed_fcli_values("FR", vat=FR, vies_valid=True)
    assert regime == "intracomunitario" and values["IVACLI"] == 2 and values["TIVCLI"] == 4


# --- API ---------------------------------------------------------------------------


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add_all([
            Company(id="fr", name="La Maison de la Plaque", country="FR",
                    tax_id=FR, vat=FR, factusol_company_id="2760"),
            Company(id="no", name="Nordic AS", country="NO", tax_id="987654321"),
        ])
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class _FakeVies:
    """`check_vat` simulado en el servicio: veredicto por NIF-IVA + contador."""

    def __init__(self, verdicts: dict[str, str] | None = None):
        self.verdicts = verdicts or {}
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, vat, *, country_code=None, force=False, base_url=None, timeout=None,
                 transport=None):
        self.calls.append((vat, force))
        parts = vies_client.vies_request_parts(vat, country_code=country_code)
        assert parts is not None and not parts[1].startswith(parts[0]), parts
        status = self.verdicts.get(vat, "desconocido")
        return ViesResult(
            status=status, valid={"valido": True, "no_valido": False}.get(status),
            vat=vat, country_code=vat[:2], number=vat[2:],
            name="LA MAISON (VIES)" if status == "valido" else None,
            address="IS-SUR-TILLE" if status == "valido" else None,
            error=None if status != "desconocido" else "VIES HTTP 500",
            checked_at=datetime.now(UTC),
        )


@contextmanager
def _vies_on(fake: _FakeVies) -> Generator[None, None, None]:
    """VIES activado (los tests lo tienen apagado) con el cliente simulado."""
    settings = SimpleNamespace(vies_enabled=True, vies_base_url="http://vies.test",
                               vies_timeout_seconds=1.0)
    with patch.object(vies_service, "get_settings", return_value=settings), \
            patch.object(vies_service, "check_vat", fake):
        yield


def _order(s: Session, *, oid: str, company_id: str) -> None:
    o = Order(id=oid, order_number=f"MAN-{oid}", company_id=company_id,
              external_source=OrderSource.WOOCOMMERCE, total_amount=100.0, currency="EUR",
              payment_status="paid", preparation_status="in_queue",
              approved_at=datetime.now(UTC), factusol_albaran_number="2-100418")
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=oid, position=0, product_sku="99cy", product_codart="99cy",
                    description="Tinta", quantity=1, unit_price=100, line_total=100))
    s.commit()


def test_alta_valida_en_vies_y_confirma_intracomunitario(http, session_factory) -> None:
    fake = _FakeVies({BE: "valido"})
    h = auth_headers(http, "user")
    with _vies_on(fake):
        r = http.post("/api/companies", json={"name": "Ligue Braille", "country": "BE",
                                              "vat": BE}, headers=h)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["vies"]["status"] == "valido" and body["vies"]["valid"] is True
        assert body["vies"]["name"] == "LA MAISON (VIES)" and body["vies_status"] == "valido"
        assert fake.calls == [(BE, False)]
        # Persistido: al leer la empresa sale lo mismo sin volver a llamar.
        r = http.get(f"/api/companies/{body['id']}", headers=h).json()
        assert r["vies"]["status"] == "valido" and r["vies"]["checked_at"]
        assert len(fake.calls) == 1
        # Régimen intracomunitario CONFIRMADO en la comprobación fiscal de su ficha.
        fc = http.get("/api/companies/fiscal-check", headers=h,
                      params={"country": "BE", "vat": BE, "exclude_id": body["id"]}).json()
        assert fc["regime"] == "intracomunitario" and fc["vies"]["status"] == "valido"
        assert "verificado en VIES" in fc["regime_reason"]
        assert len(fake.calls) == 1                     # reutiliza el resultado guardado
    # Guardar sin cambiar el NIF-IVA no vuelve a llamar; cambiarlo sí.
    with _vies_on(fake):
        r = http.put(f"/api/companies/{body['id']}", headers=h,
                     json={"name": "Ligue Braille", "country": "BE", "vat": BE})
        assert r.status_code == 200 and len(fake.calls) == 1
        r = http.put(f"/api/companies/{body['id']}", headers=h,
                     json={"name": "Ligue Braille", "country": "BE", "vat": "BE0123456789"})
        assert r.status_code == 200 and fake.calls[-1] == ("BE0123456789", False)
        assert r.json()["vies"]["status"] == "desconocido"   # el fake no conoce ese VAT
    with session_factory() as s:
        c = s.get(Company, body["id"])
        assert c.vies_vat == "BE0123456789" and c.vies_status == "desconocido"


def test_vat_no_valido_no_exime_y_bloquea_en_el_pedido(http, session_factory) -> None:
    """VIES dice NO → nacional con IVA (aviso accionable) y el pedido entra en
    incidencias con alerta bloqueante; la ficha F_CLI propuesta es nacional."""
    fake = _FakeVies({FR: "no_valido"})
    h = auth_headers(http, "user")
    with _vies_on(fake):
        r = http.post("/api/companies/fr/vies-revalidate", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["vies"]["status"] == "no_valido" and body["vies"]["valid"] is False
        assert body["regime"] == "nacional"
        assert "NO válido en VIES" in body["regime_reason"]
        assert body["company"]["vies_status"] == "no_valido"
        assert fake.calls == [(FR, True)]                     # forzado: salta la caché
        fc = http.get("/api/companies/fiscal-check", headers=h,
                      params={"country": "FR", "vat": FR, "exclude_id": "fr"}).json()
        assert fc["regime"] == "nacional" and fc["vies"]["status"] == "no_valido"
    with session_factory() as s:
        _order(s, oid="o1", company_id="fr")
        wf = order_workflow(s, s.get(Order, "o1"))
    assert wf["regime"] == "nacional"
    alerta = next(a for a in wf["alerts"] if a["code"] == "vat_no_valido_vies")
    assert alerta["blocking"] is True and alerta["action"] == "revalidar_vies"
    assert alerta["action_label"] == "Revalidar en VIES" and FR in alerta["text"]
    assert wf["blocked"] is True and wf["queue"] == "incidencias"
    assert wf["company"]["vies"]["status"] == "no_valido"
    assert not [a for a in wf["alerts"] if a["code"] == "cliente_intracomunitario"]


def test_vies_caido_queda_desconocido_sin_romper(http, session_factory) -> None:
    fake = _FakeVies()                                   # todo → desconocido
    h = auth_headers(http, "user")
    with _vies_on(fake):
        r = http.post("/api/companies", json={"name": "Nueva BE", "country": "BE", "vat": BE},
                      headers=h)
        assert r.status_code == 201, r.text             # el alta sigue
        assert r.json()["vies"]["status"] == "desconocido"
        r = http.post("/api/companies/fr/vies-revalidate", headers=h)
        assert r.status_code == 200
        assert r.json()["vies"]["status"] == "desconocido"
        assert r.json()["regime"] == "intracomunitario"  # se sigue por país + NIF-IVA
        fc = http.get("/api/companies/fiscal-check", headers=h,
                      params={"country": "FR", "vat": FR}).json()
        assert fc["regime"] == "intracomunitario" and fc["vies"]["status"] == "desconocido"
        assert fc["vies"]["error"] == "VIES HTTP 500"
    # En el pedido: no bloquea, pero avisa de que está pendiente de validar.
    with session_factory() as s:
        _order(s, oid="o2", company_id="fr")
        wf = order_workflow(s, s.get(Order, "o2"))
    assert wf["blocked"] is False and wf["queue"] == "por_facturar"
    aviso = next(a for a in wf["alerts"] if a["code"] == "cliente_intracomunitario")
    assert "pendiente de validar" in aviso["text"] and aviso["action"] == "revalidar_vies"
    assert not [a for a in wf["alerts"] if a["code"] == "vat_no_valido_vies"]


def test_fuera_de_la_ue_es_exportacion_sin_consultar_vies(http) -> None:
    fake = _FakeVies({BE: "valido"})
    h = auth_headers(http, "user")
    with _vies_on(fake):
        r = http.post("/api/companies/no/vies-revalidate", headers=h)
        assert r.status_code == 200
        assert r.json()["vies"]["applies"] is False and r.json()["regime"] == "exportacion"
        fc = http.get("/api/companies/fiscal-check", headers=h,
                      params={"country": "NO", "tax_id": "987654321"}).json()
        assert fc["regime"] == "exportacion" and fc["vies"]["applies"] is False
        # España tampoco: nacional, sin VIES.
        fc = http.get("/api/companies/fiscal-check", headers=h,
                      params={"country": "ES", "tax_id": "B12345678"}).json()
        assert fc["regime"] == "nacional" and fc["vies"]["applies"] is False
        assert fake.calls == []


def test_revalidar_confirma_intracomunitario_y_no_fuerza_si_reciente(http) -> None:
    fake = _FakeVies({FR: "valido"})
    h = auth_headers(http, "user")
    with _vies_on(fake):
        # La ficha al cargar (force=false): sin resultado → consulta.
        r = http.post("/api/companies/fr/vies-revalidate", params={"force": "false"}, headers=h)
        assert r.status_code == 200 and r.json()["vies"]["status"] == "valido"
        assert r.json()["regime"] == "intracomunitario"
        assert "verificado en VIES" in r.json()["regime_reason"]
        assert fake.calls == [(FR, False)]
        # Otra carga: reciente → no llama.
        r = http.post("/api/companies/fr/vies-revalidate", params={"force": "false"}, headers=h)
        assert r.json()["vies"]["status"] == "valido" and len(fake.calls) == 1
        # El botón (force): siempre consulta.
        r = http.post("/api/companies/fr/vies-revalidate", headers=h)
        assert fake.calls[-1] == (FR, True)
        assert r.json()["company"]["vies"]["name"] == "LA MAISON (VIES)"
    # 404 para una empresa que no existe.
    assert http.post("/api/companies/nope/vies-revalidate", headers=h).status_code == 404


def test_vat_cacheado_como_no_valido_se_revalida_a_valido(http, session_factory) -> None:
    """Un VAT que quedó guardado como «no válido» (el bug del prefijo) pasa a
    válido con «Revalidar en VIES» (forzado) — y también solo, al cargar la
    ficha, cuando el veredicto negativo tiene más de un día. El número se
    envía normalizado (sin prefijo) y el régimen vuelve a intracomunitario
    sin alerta bloqueante."""
    bandit = "FR90501738249"
    with session_factory() as s:
        s.add(Company(id="bandit", name="SAS BANDIT BANDIT", country="FR", vat=bandit,
                      factusol_company_id="4471", vies_status="no_valido", vies_vat=bandit,
                      vies_checked_at=datetime.now(UTC) - timedelta(days=2)))
        s.commit()
        _order(s, oid="o3", company_id="bandit")
        wf = order_workflow(s, s.get(Order, "o3"))
    assert wf["regime"] == "nacional" and wf["blocked"] is True       # antes del fix

    sent: list[dict] = []

    def fake_check(vat, *, country_code=None, force=False, base_url=None, timeout=None,
                   transport=None):
        parts = vies_client.vies_request_parts(vat, country_code=country_code)
        sent.append({"countryCode": parts[0], "vatNumber": parts[1], "force": force})
        return ViesResult(status="valido", valid=True, vat=parts[0] + parts[1],
                          country_code=parts[0], number=parts[1], name="SAS BANDIT BANDIT",
                          checked_at=datetime.now(UTC))

    h = auth_headers(http, "user")
    settings = SimpleNamespace(vies_enabled=True, vies_base_url="http://vies.test",
                               vies_timeout_seconds=1.0)
    with patch.object(vies_service, "get_settings", return_value=settings), \
            patch.object(vies_service, "check_vat", fake_check):
        # La ficha al cargar (sin forzar): el «no válido» tiene > 1 día → se reconsulta.
        r = http.post("/api/companies/bandit/vies-revalidate", params={"force": "false"},
                      headers=h)
        assert r.status_code == 200, r.text
        assert sent == [{"countryCode": "FR", "vatNumber": "90501738249", "force": False}]
        assert r.json()["vies"]["status"] == "valido" and r.json()["regime"] == "intracomunitario"
        # El botón (forzado) también, y siempre con el mismo número normalizado.
        r = http.post("/api/companies/bandit/vies-revalidate", headers=h)
        assert sent[-1] == {"countryCode": "FR", "vatNumber": "90501738249", "force": True}
        assert "verificado en VIES" in r.json()["regime_reason"]
    with session_factory() as s:
        c = s.get(Company, "bandit")
        assert c.vies_status == "valido" and c.vies_name == "SAS BANDIT BANDIT"
        wf = order_workflow(s, s.get(Order, "o3"))
    assert wf["regime"] == "intracomunitario" and wf["blocked"] is False
    assert not [a for a in wf["alerts"] if a["code"] == "vat_no_valido_vies"]
    assert wf["company"]["vies"]["status"] == "valido"


def test_vies_desactivado_no_llama_y_queda_pendiente(http) -> None:
    """`VIES_ENABLED=false` (lo que tienen los tests): nada sale a la UE y el
    chip dice «pendiente»."""
    fake = _FakeVies({FR: "valido"})
    h = auth_headers(http, "user")
    with patch.object(vies_service, "check_vat", fake):
        r = http.post("/api/companies/fr/vies-revalidate", headers=h)
        assert r.status_code == 200 and r.json()["vies"]["status"] == "pendiente"
        assert r.json()["regime"] == "intracomunitario"
        fc = http.get("/api/companies/fiscal-check", headers=h,
                      params={"country": "FR", "vat": FR}).json()
        assert fc["vies"]["status"] == "pendiente" and fc["vies"]["applies"] is True
    assert fake.calls == []
