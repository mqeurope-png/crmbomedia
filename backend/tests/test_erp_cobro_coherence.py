"""ERP · Lote 3 · Ficha (backend) — coherencia del estado de cobro y factura
enviada en el detalle del pedido.

#7 — Tras un re-vínculo (`invoice_link_fix`) la caché de cobro del pedido
queda a `None` (desconocida hasta re-comprobar). La primera lectura EN VIVO
del cobro (`GET /orders/{id}/factusol-cobro`) resuelve la factura re-vinculada,
calcula su estado real en FACTUSOL y lo PERSISTE (con commit) en
`Order.factusol_cobro_status` + `factusol_cobro_checked_at`, de modo que la
ficha (en vivo) y la bandeja (caché + filtros) vuelvan a coincidir.

#8 — El detalle del pedido expone cuándo y a quién se envió por email la
factura (evento de auditoría `erp.invoice_emailed`): `invoice_emailed_at` +
`invoice_emailed_to`.

Reutiliza el FACTUSOL simulado y los helpers de `test_erp_cobro_manual`; las
fixtures se definen aquí (evita el shadowing de fixtures importadas)."""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra la app FastAPI
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order
from app.integrations.factusol.client import FactusolError
from app.main import app
from app.models.crm import AuditLog, Company
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_cobro_manual import (
    FakeCobroClient,
    _fac,
    _get_cobro,
    _lco,
    _order,
    _patched,
)

# --- fixtures ---------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session_factory(engine) -> Generator[sessionmaker, None, None]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(Company(id="acme", name="Acme SL", factusol_company_id="55555"))
        seed.commit()
    yield factory


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- #7 · coherencia del cobro tras re-vínculo ------------------------------------


def test_lectura_viva_persiste_cobro_definido_tras_revinculo(http, session_factory) -> None:
    """Pedido con factura pero cobro cacheado a `None` (como lo deja el
    re-vínculo). La factura está COBRADA en FACTUSOL (ESTFAC=2, saldo 0). La
    lectura en vivo dice «cobrada» Y deja la caché del pedido persistida a
    «cobrada» con `checked_at` fijado (commit), así ficha y bandeja coinciden."""
    with session_factory() as s:
        _order(s, "o-rl", "BOPRIN-99980", invoice="260900", serie=1, cobro_status=None)
        s.commit()
    fake = FakeCobroClient(
        f_fac=[_fac(1, 260900, 90.0, estfac="2")],
        f_lco=[_lco(1, 260900, 1, 90.0)],
    )
    with _patched(fake):
        r = _get_cobro(http, "o-rl")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "cobrada"
    assert body["persisted_status"] == "cobrada"

    # Coherencia: la caché del pedido queda persistida en la BD (sesión nueva).
    with session_factory() as s:
        o = s.get(Order, "o-rl")
        assert o.factusol_cobro_status == "cobrada"
        assert o.factusol_cobro_checked_at is not None

    # La bandeja filtra por la caché → ya coincide con la ficha.
    cobradas = http.get(
        "/api/erp/orders?cobro=cobrada", headers=auth_headers(http, "user"),
    ).json()["items"]
    assert "o-rl" in {x["id"] for x in cobradas}
    # Y deja de aparecer como «con factura sin comprobar».
    sin_comprobar = http.get(
        "/api/erp/orders?cobro=sin_comprobar", headers=auth_headers(http, "user"),
    ).json()["items"]
    assert "o-rl" not in {x["id"] for x in sin_comprobar}


def test_lectura_viva_no_pisa_cache_si_factusol_cae(http, session_factory) -> None:
    """Guarda: si la lectura en vivo NO es concluyente (FACTUSOL caído → 502),
    la caché existente se deja como está; nunca se sobrescribe con `None`."""
    with session_factory() as s:
        _order(s, "o-keep", "BOPRIN-99981", invoice="260901", serie=1,
               cobro_status="cobrada")
        s.commit()
    with patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        side_effect=FactusolError("caído", status=500),
    ):
        r = _get_cobro(http, "o-keep")
    assert r.status_code == 502
    with session_factory() as s:
        assert s.get(Order, "o-keep").factusol_cobro_status == "cobrada"


# --- #8 · factura enviada al cliente en el detalle --------------------------------


def test_detalle_expone_factura_enviada_al_cliente(http, session_factory) -> None:
    """El detalle del pedido trae `invoice_emailed_at` (ISO del envío más
    reciente) e `invoice_emailed_to` (destinatarios de ESE envío). Con varios
    envíos gana el más reciente; sin envíos, `None` y `[]`."""
    old = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    new = datetime(2026, 2, 20, 15, 30, tzinfo=UTC)
    with session_factory() as s:
        _order(s, "o-mail", "BOPRIN-99990", invoice="260950", serie=1)
        _order(s, "o-nomail", "BOPRIN-99991")
        # Dos envíos del MISMO pedido: gana el más reciente.
        s.add(AuditLog(
            action="erp.invoice_emailed", target_type="order", target_id="o-mail",
            metadata_json=json.dumps(
                {"factura": "1-260950", "to": ["viejo@x.com"], "lang": "es"},
            ),
            created_at=old,
        ))
        s.add(AuditLog(
            action="erp.invoice_emailed", target_type="order", target_id="o-mail",
            metadata_json=json.dumps(
                {"factura": "1-260950", "to": ["cliente@x.com"], "lang": "en"},
            ),
            created_at=new,
        ))
        # Ruido que NO debe filtrarse: otro pedido y otra acción.
        s.add(AuditLog(
            action="erp.invoice_emailed", target_type="order", target_id="o-otro",
            metadata_json=json.dumps({"to": ["ruido@x.com"]}), created_at=new,
        ))
        s.add(AuditLog(
            action="erp.invoice_link_fixed", target_type="order", target_id="o-mail",
            metadata_json=json.dumps({"to": ["nada@x.com"]}), created_at=new,
        ))
        s.commit()
    with session_factory() as s:
        latest = s.scalars(
            select(AuditLog).where(
                AuditLog.action == "erp.invoice_emailed",
                AuditLog.target_id == "o-mail",
            ).order_by(AuditLog.created_at.desc())
        ).first()
        expected_at = latest.created_at.isoformat()

    detail = http.get("/api/erp/orders/o-mail", headers=auth_headers(http, "user")).json()
    assert detail["invoice_emailed_at"] == expected_at
    assert detail["invoice_emailed_to"] == ["cliente@x.com"]

    never = http.get("/api/erp/orders/o-nomail", headers=auth_headers(http, "user")).json()
    assert never["invoice_emailed_at"] is None
    assert never["invoice_emailed_to"] == []


def test_detalle_factura_enviada_sin_lista_to(http, session_factory) -> None:
    """Un evento `erp.invoice_emailed` sin `to` (o con metadata rara) da fecha
    de envío pero `invoice_emailed_to == []` (no revienta)."""
    when = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    with session_factory() as s:
        _order(s, "o-noto", "BOPRIN-99992", invoice="260960", serie=1)
        s.add(AuditLog(
            action="erp.invoice_emailed", target_type="order", target_id="o-noto",
            metadata_json=json.dumps({"factura": "1-260960", "lang": "es"}),
            created_at=when,
        ))
        s.commit()
    with session_factory() as s:
        log = s.scalars(
            select(AuditLog).where(AuditLog.target_id == "o-noto")
        ).first()
        expected_at = log.created_at.isoformat()
    detail = http.get("/api/erp/orders/o-noto", headers=auth_headers(http, "user")).json()
    assert detail["invoice_emailed_at"] == expected_at
    assert detail["invoice_emailed_to"] == []
