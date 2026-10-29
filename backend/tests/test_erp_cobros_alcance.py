"""El marco de los cobros en Conciliación (29/10/2026): el ejercicio y el
alcance «solo pedidos de BoHub».

- Los movimientos se acotan al ejercicio (su fecha de operación) y los
  contadores también. El extracto se enseña entero: la casilla de facturas
  externas no esconde ningún movimiento.
- Sin la casilla, las propuestas solo ofrecen facturas con pedido. Un
  movimiento que solo casa con externas sale «sin propuesta», diciendo que
  las hay; el contador los cuenta y filtra la lista a ellos.
- Lo conciliado no se esconde nunca.

Datos figurantes.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.erp.bank import service
from app.erp.models import BankMovement, BankReconciliation
from app.erp.models.orders import Order, OrderSource
from app.models.crm import Base
from tests._test_helpers import seed_test_users


@pytest.fixture()
def s() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as session:
        seed_test_users(session)
        yield session
    Base.metadata.drop_all(engine)


def _mov(s: Session, acc: str, n: int, fecha: date, importe: float, status: str = "pending",
         props: list[tuple[int, int, str, str]] = ()) -> BankMovement:
    """`props`: (serie, código, confianza, estado de la conciliación)."""
    m = BankMovement(account_id=acc, fecha_oper=fecha, fecha_valor=fecha,
                     concepto=f"ABONO TRANSFERENCIA FIGURANTE {n}", importe=importe,
                     saldo=1000 + n, dedupe_key=f"k{n}", source_row=n, status=status)
    s.add(m)
    s.flush()
    for serie, codigo, conf, estado in props:
        s.add(BankReconciliation(movement_id=m.id, serie=serie, codigo=codigo,
                                 numero=f"{serie}-{codigo}", importe=importe,
                                 confidence=conf, reason="figurante", status=estado))
    return m


@pytest.fixture()
def datos(s: Session) -> str:
    acc = service.create_account(s, {"name": "Sabadell Ejemplo", "iban": "ES9121000418450200051332",
                                     "serie": 5})
    # La 5-260090 tiene pedido en BoHub; la 5-260091 y la 5-250500 solo están
    # en FACTUSOL (externas).
    s.add(Order(id="o-1", order_number="BOPRIN-100090", external_source=OrderSource.WOOCOMMERCE,
                factusol_invoice_number="260090", factusol_invoice_serie=5))
    _mov(s, acc.id, 1, date(2026, 10, 21), 2310.00, props=[(5, 260090, "alta", "proposed")])
    _mov(s, acc.id, 2, date(2026, 10, 20), 408.48, props=[(5, 260091, "alta", "proposed")])
    _mov(s, acc.id, 3, date(2026, 10, 17), 96.80)                          # sin nada
    _mov(s, acc.id, 4, date(2026, 10, 16), 650.00, status="reconciled",
         props=[(5, 260092, "alta", "confirmed")])                         # externa, conciliada
    _mov(s, acc.id, 5, date(2025, 12, 30), 1210.00, props=[(5, 250500, "alta", "proposed")])
    s.commit()
    return acc.id


def _por_concepto(r: dict) -> dict[int, dict]:
    return {int(i["concepto"].rsplit(" ", 1)[1]): i for i in r["items"]}


def test_el_ejercicio_acota_movimientos_y_contadores(s, datos) -> None:
    r = service.list_movements(s, account_id=datos, ejercicio="2026")
    assert sorted(_por_concepto(r)) == [1, 2, 3, 4]
    assert (r["counters"]["pending"], r["counters"]["reconciled"]) == (3, 1)
    r25 = service.list_movements(s, account_id=datos, ejercicio="2025")
    assert sorted(_por_concepto(r25)) == [5] and r25["counters"]["pending"] == 1


def test_sin_la_casilla_solo_se_ofrecen_facturas_con_pedido(s, datos) -> None:
    r = service.list_movements(s, account_id=datos, ejercicio="2026", externas=False)
    m = _por_concepto(r)
    # Todos los movimientos salen y cuentan: el extracto, entero.
    assert sorted(m) == [1, 2, 3, 4] and r["counters"]["pending"] == 3
    assert [p["numero"] for p in m[1]["proposals"]] == ["5-260090"]
    assert m[1]["confidence"] == "alta"
    # El que solo casa con una externa: sin propuesta, pero diciendo que la hay.
    assert m[2]["proposals"] == [] and m[2]["confidence"] is None
    assert m[2]["externas_ocultas"] == 1
    assert m[3]["externas_ocultas"] == 0                       # de verdad no hay nada
    # Lo conciliado contra una externa no se toca ni se esconde.
    assert [x["numero"] for x in m[4]["reconciled"]] == ["5-260092"]
    assert r["counters"]["solo_externas"] == 1
    # El contador, pulsado: la lista solo con esos.
    solo = service.list_movements(s, account_id=datos, ejercicio="2026", externas=False,
                                  solo_externas=True)
    assert sorted(_por_concepto(solo)) == [2]
    # «Sin propuesta» incluye al que solo casa con externas.
    sin = service.list_movements(s, account_id=datos, ejercicio="2026", externas=False,
                                 confidence="none")
    assert sorted(_por_concepto(sin)) == [2, 3]


def test_con_la_casilla_se_ofrecen_tambien_las_externas(s, datos) -> None:
    r = service.list_movements(s, account_id=datos, ejercicio="2026", externas=True)
    m = _por_concepto(r)
    assert [p["numero"] for p in m[2]["proposals"]] == ["5-260091"]
    assert m[2]["proposals"][0]["pedido"] is None               # se distingue: sin pedido
    assert m[1]["proposals"][0]["pedido"]["order_number"] == "BOPRIN-100090"
    assert r["counters"]["solo_externas"] == 0


def test_confirmar_las_de_confianza_alta_respeta_lo_que_se_ve(s, datos) -> None:
    n = service.confirm_all_high(s, None, account_id=datos, externas=False, ejercicio="2026")
    assert n == 1                                               # solo la del pedido
    estados = {m.concepto.rsplit(" ", 1)[1]: m.status for m in s.scalars(select(BankMovement))}
    assert (estados["1"], estados["2"], estados["5"]) == ("reconciled", "pending", "pending")


def test_confirmar_una_fila_no_confirma_externas_que_no_se_ven(s, datos) -> None:
    acc = datos
    mixto = _mov(s, acc, 6, date(2026, 10, 10), 500.00,
                 props=[(5, 260090, "alta", "proposed"), (5, 260093, "alta", "proposed")])
    s.commit()
    with pytest.raises(ValueError, match="facturas externas"):
        service.confirm_movement(s, mixto.id, None, externas=False)
    assert s.get(BankMovement, mixto.id).status == "pending"
    # Con la casilla (lo ve todo) se confirma como siempre.
    solo = s.scalars(select(BankMovement).where(BankMovement.concepto.endswith(" 1"))).one()
    service.confirm_movement(s, solo.id, None, externas=False)
    assert s.get(BankMovement, solo.id).status == "reconciled"


def test_el_contador_pulsado_no_lo_vacian_los_otros_filtros(s, datos) -> None:
    r = service.list_movements(s, account_id=datos, ejercicio="2026", externas=False,
                               solo_externas=True, confidence="alta", status="reconciled")
    assert sorted(_por_concepto(r)) == [2]
