"""Tarea A — «Crear albarán en FACTUSOL» a demanda para pedidos MANUALES.

Un pedido tecleado en BoHub no tiene documento en FACTUSOL: el albarán se
crea desde sus LÍNEAS (builders de «Nueva proforma» + maquinaria de la Fase 2:
`COD*` entero, `ESTALB=0`, tipos como la fila real, guard de esquema ESTRICTO,
registro exacto en el log) como albarán AUTÓNOMO (`DOCLAL='A'` apuntando a su
propia clave). Idempotente; los web nunca; el guard que no cuadra no escribe.
"""
from __future__ import annotations

import json
import re
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.factusol_albaran import (
    AlbaranNotApplicable,
    WebOrderNoAlbaran,
    create_albaran_for_order,
)
from app.erp.models import Order, OrderLine, OrderSource, OrderStatusHistory
from app.integrations.factusol import chain
from app.integrations.factusol.albaran_manual import order_lines_for_document
from app.integrations.factusol.chain import (
    ALB_REFERENCE_COLUMNS,
    LAL_REFERENCE_COLUMNS,
)
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.jobs import create_order_albaran_job
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_chain import WriteFakeClient, _live_alb_row, _live_lal_row

CLIENTE = {
    "CODCLI": 2458, "NIFCLI": "B12345678", "NOFCLI": "DUPLICODER, S.L.",
    "NOCCLI": "Duplicoder", "DOMCLI": "C/ Mayor 1", "POBCLI": "Girona",
    "CPOCLI": "17001", "PROCLI": "Girona", "PAICLI": "724",
    "EMACLI": "info@duplicoder.es", "TELCLI": "972000000",
}
ARTICULOS = [
    {"CODART": "99cy", "EQUART": "Ink500mlCY", "DESART": "Tinta cyan 500 ml"},
    {"CODART": "CDR80", "EQUART": "", "DESART": "CD-R 80"},
]


class AlbaranFakeClient(WriteFakeClient):
    """WriteFakeClient + el filtro `CODART IN (...) OR EQUART IN (...)` de
    `resolve_codarts` (el fake base solo entiende igualdades)."""

    def load_table(self, tabla: str, *, filtro: str = "1=1",
                   ejercicio: str | None = None) -> list[dict[str, Any]]:
        if tabla == "F_ART" and " IN (" in filtro:
            self.calls.append((tabla, filtro))
            wanted = set(re.findall(r"'([^']*)'", filtro))
            return [
                r for r in self.tables.get("F_ART", [])
                if r.get("CODART") in wanted or r.get("EQUART") in wanted
            ]
        return super().load_table(tabla, filtro=filtro, ejercicio=ejercicio)


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_ALB": [_live_alb_row(500003, "5", CLIALB=2458, CNOALB="OTRO SL",
                                CPAALB="724", ALMALB="GEN", FOPALB="002")],
        "F_LAL": [_live_lal_row(500003, "5", ARTLAL="x", DESLAL="y", CANLAL=1.0,
                                PRELAL=2.0, TOTLAL=2.0, DT1LAL=0.0)],
        "F_CLI": [dict(CLIENTE)],
        "F_ART": [dict(a) for a in ARTICULOS],
    }


def _client(tables: dict[str, list[dict[str, Any]]] | None = None) -> AlbaranFakeClient:
    # La allowlist de columnas vivas se cachea por proceso: cada fake parte
    # de cero para que la fila viva de ESTE test sea la que manda.
    chain._LIVE_COLUMNS_CACHE.clear()
    return AlbaranFakeClient(
        tables if tables is not None else _tables(),
        known_columns={"F_ALB": ALB_REFERENCE_COLUMNS, "F_LAL": LAL_REFERENCE_COLUMNS},
    )


def _patched(fake: AlbaranFakeClient):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


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
        seed.add_all([
            Company(id="dupli", name="Duplicoder", factusol_company_id="2458"),
            Company(id="sinlink", name="Sin vínculo SL"),
        ])
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


def _manual(s: Session, oid: str, number: str, lines: list[dict[str, Any]], *,
            company_id: str | None = "dupli",
            source: OrderSource = OrderSource.MANUAL, packing: dict | None = None) -> Order:
    o = Order(
        id=oid, external_source=source, external_id=number.split("-")[-1],
        order_number=number, company_id=company_id, total_amount=0,
        packing_json=json.dumps(packing) if packing else None,
    )
    s.add(o)
    s.flush()
    for i, ln in enumerate(lines):
        s.add(OrderLine(
            order_id=oid, position=i, product_sku=ln.get("sku", ""),
            product_codart=ln.get("codart"), description=ln["desc"],
            quantity=ln.get("qty", 1), unit_price=ln["price"],
            tax_rate=ln.get("iva", 21), line_total=round(ln.get("qty", 1) * ln["price"], 2),
            notes=ln.get("notes"),
        ))
    return o


def _run_job(engine, fake: AlbaranFakeClient, oid: str):
    with _patched(fake), patch("app.db.session.get_engine", return_value=engine):
        return create_order_albaran_job(oid, actor_user_id=None)


def _written(fake: AlbaranFakeClient, tabla: str) -> list[dict[str, Any]]:
    return [rec for t, rec in fake.written if t == tabla]


def test_crear_albaran_manual_desde_lineas(http, session_factory, engine) -> None:
    """Pedido manual con líneas y empresa vinculada: el endpoint lo encola
    (202) y el job crea F_ALB + F_LAL desde las líneas: `COD*` entero,
    `ESTALB=0`, cliente y bandas de IVA en la cabecera, SKU → CODART interno
    (vía EQUART), enlace autónomo `DOCLAL='A'` a su propia clave, nº guardado
    en el pedido; después el «PDF del albarán» (#396) funciona."""
    with session_factory() as s:
        _manual(s, "o-1", "MANUAL-000010", [
            {"sku": "Ink500mlCY", "desc": "Tinta cyan", "qty": 2, "price": 40.0},
            {"sku": "CDR80", "desc": "CD-R 80", "qty": 100, "price": 0.5},
        ])
        s.commit()

    with patch("app.integrations.factusol.jobs.enqueue_create_order_albaran",
               return_value="job-alb") as enq:
        r = http.post("/api/erp/orders/o-1/albaran", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 202, r.text
    assert r.json()["job_id"] == "job-alb"
    enq.assert_called_once()

    fake = _client()
    result = _run_job(engine, fake, "o-1")
    assert result["status"] == "created" and result["standalone"] is True
    assert result["numero"] == "5-500004" and result["lines"] == 2
    assert result["free_text_lines"] == []

    (cab,) = _written(fake, "F_ALB")
    assert cab["TIPALB"] == "5" and cab["CODALB"] == 500004      # entero
    assert cab["ESTALB"] == 0 and cab["FECALB"]
    assert cab["CLIALB"] == 2458                                 # como la fila real (int)
    assert cab["CNOALB"] == "Duplicoder" and cab["CNIALB"] == "B12345678"
    assert cab["CDOALB"] == "C/ Mayor 1" and cab["CPAALB"] == "724"
    assert cab["NET1ALB"] == 130.0 and cab["PIVA1ALB"] == 21.0
    assert cab["IIVA1ALB"] == 27.3 and cab["TOTALB"] == 157.3
    assert cab["REFALB"] == "MANUAL-000010"
    assert "FECPRE" not in cab and "CODPRE" not in cab              # todo retagado
    assert set(cab) <= ALB_REFERENCE_COLUMNS

    lineas = _written(fake, "F_LAL")
    assert [ln["POSLAL"] for ln in lineas] == [1, 2]
    assert all(ln["TIPLAL"] == "5" and ln["CODLAL"] == 500004 for ln in lineas)
    # Enlace AUTÓNOMO: 'A' + su propia serie/número.
    assert all(ln["DOCLAL"] == "A" and ln["DTPLAL"] == "5" and ln["DCOLAL"] == 500004
               for ln in lineas)
    assert lineas[0]["ARTLAL"] == "99cy"        # EQUART Ink500mlCY → CODART 99cy
    assert lineas[0]["DESLAL"] == "Tinta cyan"
    assert lineas[0]["CANLAL"] == 2.0 and lineas[0]["PRELAL"] == 40.0
    assert lineas[0]["TOTLAL"] == 80.0
    assert lineas[1]["ARTLAL"] == "CDR80" and lineas[1]["TOTLAL"] == 50.0
    assert all(set(ln) <= LAL_REFERENCE_COLUMNS for ln in lineas)
    assert fake.deleted == []

    with session_factory() as s:
        o = s.get(Order, "o-1")
        assert o.factusol_albaran_number == "5-500004"
        block = json.loads(o.packing_json)["factusol_albaran"]
        assert block["standalone"] is True and block["codcli"] == "2458"
        hist = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == "o-1",
        )).all()
        assert any("desde las líneas del pedido" in h.reason for h in hist)

    # #396: el PDF del albarán recién creado se genera desde F_ALB/F_LAL.
    with _patched(fake):
        pdf = http.get("/api/erp/orders/o-1/factusol-albaran-pdf",
                       headers=auth_headers(http, "user"))
    assert pdf.status_code == 200, pdf.text
    assert pdf.content[:5] == b"%PDF-"
    # La ficha recibe el nº y el endpoint ya no deja crear otro.
    r = http.post("/api/erp/orders/o-1/albaran", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_has_albaran"


def test_crear_albaran_manual_linea_texto_libre(http, session_factory, engine) -> None:
    """SKU que no casa con ningún CODART/EQUART de F_ART (o línea sin SKU) →
    línea de TEXTO LIBRE (`ARTLAL=''`) con su descripción, como las proformas;
    el descuento de la nota «dto. X%» y el IVA de la línea se respetan."""
    with session_factory() as s:
        _manual(s, "o-2", "MANUAL-000011", [
            {"sku": "NO-EXISTE", "desc": "Reparación láser", "qty": 1, "price": 90.0},
            {"sku": "", "desc": "Portes", "qty": 1, "price": 10.0, "iva": 21},
            {"sku": "Ink500mlCY", "desc": "Tinta", "qty": 2, "price": 40.0,
             "notes": "dto. 50%"},
        ])
        s.commit()
    with session_factory() as s:
        o = s.get(Order, "o-2")
        lines = order_lines_for_document(o)
        assert lines[2]["discount_pct"] == 50.0 and lines[2]["iva_pct"] == 21.0

    fake = _client()
    result = _run_job(engine, fake, "o-2")
    assert result["status"] == "created"
    assert result["free_text_lines"] == ["NO-EXISTE"]
    lineas = _written(fake, "F_LAL")
    assert lineas[0]["ARTLAL"] == "" and lineas[0]["DESLAL"] == "Reparación láser"
    assert lineas[1]["ARTLAL"] == "" and lineas[1]["DESLAL"] == "Portes"
    assert lineas[2]["ARTLAL"] == "99cy" and lineas[2]["DT1LAL"] == 50.0
    assert lineas[2]["TOTLAL"] == 40.0                          # 2 × 40 con 50 % dto.
    (cab,) = _written(fake, "F_ALB")
    assert cab["NET1ALB"] == 140.0 and cab["TOTALB"] == 169.4


def test_crear_albaran_idempotente(http, session_factory, engine) -> None:
    """Con nº guardado no se crea otro (`already`, sin tocar FACTUSOL); el
    endpoint responde 409 `already_has_albaran`."""
    with session_factory() as s:
        _manual(s, "o-3", "MANUAL-000012", [{"sku": "CDR80", "desc": "CD", "price": 1.0}])
        s.commit()
    fake = _client()
    first = _run_job(engine, fake, "o-3")
    assert first["status"] == "created" and first["numero"] == "5-500004"
    writes = len(fake.written)

    again = _run_job(engine, fake, "o-3")
    assert again["status"] == "already" and again["numero"] == "5-500004"
    assert len(fake.written) == writes
    r = http.post("/api/erp/orders/o-3/albaran", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_has_albaran"


def test_crear_albaran_web_no_permitido(http, session_factory, engine) -> None:
    """Un pedido web nunca recibe albarán de BoHub (lo crea WooCommerce): 409
    en el endpoint y `WebOrderNoAlbaran` en el servicio. Sin empresa vinculada
    a F_CLI (o sin líneas) tampoco: 409 propio, sin tocar FACTUSOL."""
    with session_factory() as s:
        _manual(s, "o-web", "BOPRIN-99990", [{"sku": "CDR80", "desc": "CD", "price": 1.0}],
                source=OrderSource.WOOCOMMERCE)
        _manual(s, "o-nolink", "MANUAL-000013", [{"sku": "CDR80", "desc": "CD", "price": 1.0}],
                company_id="sinlink")
        _manual(s, "o-nolines", "MANUAL-000014", [])
        s.commit()
    r = http.post("/api/erp/orders/o-web/albaran", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "web_order_no_albaran"
    r = http.post("/api/erp/orders/o-nolink/albaran", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "company_not_linked"
    assert "F_CLI" in r.json()["detail"]["detail"]
    r = http.post("/api/erp/orders/o-nolines/albaran", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "albaran_not_applicable"

    fake = _client()
    with session_factory() as s:
        with pytest.raises(WebOrderNoAlbaran):
            create_albaran_for_order(s, fake, s.get(Order, "o-web"), ejercicio="2026")
        with pytest.raises(AlbaranNotApplicable):
            create_albaran_for_order(s, fake, s.get(Order, "o-nolink"), ejercicio="2026")
    assert fake.written == []


def test_guard_esquema_no_cuadra_no_escribe(http, session_factory, engine) -> None:
    """Guard de esquema ESTRICTO: el registro se adapta al TIPO de la fila
    real cuando el valor lo permite; si un valor no encaja con el tipo que
    devuelve DELSOL (aquí `CNIALB` numérico en la fila viva y un NIF de
    texto), no se escribe NADA, el fallo queda en el historial y el pedido
    sigue sin albarán (reintentable)."""
    with session_factory() as s:
        _manual(s, "o-4", "MANUAL-000015", [{"sku": "CDR80", "desc": "CD", "price": 1.0}])
        s.commit()
    tables = _tables()
    tables["F_ALB"] = [_live_alb_row(500003, "5", CLIALB=2458, CNIALB=0, ALMALB="GEN")]
    fake = _client(tables)
    with pytest.raises(FactusolError) as exc:
        _run_job(engine, fake, "o-4")
    assert "no cuadra" in str(exc.value) and "CNIALB" in str(exc.value)
    assert "No se ha escrito nada" in str(exc.value)
    assert fake.written == []
    with session_factory() as s:
        o = s.get(Order, "o-4")
        assert o.factusol_albaran_number is None
        hist = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == "o-4",
        )).all()
        assert any("CNIALB" in (h.reason or "") for h in hist)
    # Columnas fuera de la fila viva NO se envían nunca (allowlist): con un
    # F_ALB sin ALMALB el registro simplemente no la lleva.
    tables = _tables()
    for row in tables["F_ALB"]:
        row.pop("ALMALB", None)
    fake = _client(tables)
    assert _run_job(engine, fake, "o-4")["status"] == "created"
    assert "ALMALB" not in _written(fake, "F_ALB")[0]
    with session_factory() as s:
        s.get(Order, "o-4").factusol_albaran_number = None
        s.commit()

    # Tipo distinto a la fila real (CLIALB texto en la fila viva → el
    # registro se adapta al tipo real y SÍ cuadra: DELSOL quiere de vuelta lo
    # que devuelve).
    tables = _tables()
    tables["F_ALB"] = [_live_alb_row(500003, "5", CLIALB="2458", ALMALB="GEN")]
    fake = _client(tables)
    result = _run_job(engine, fake, "o-4")
    assert result["status"] == "created"
    assert _written(fake, "F_ALB")[0]["CLIALB"] == "2458"
