"""Régimen de IVA por PAREJA emisor → cliente (y la auditoría de lo emitido).

El régimen no es del cliente: es de la pareja «quién factura → a quién». BoHub
emite desde Streamtec / Bomedia (ES, series 5 y 1) y MQ EUROPE BV (BE, serie
2), y el código daba España por supuesta: una proforma de la serie 2 a un
cliente español con NIF-IVA válido salía con 21 % cuando debía salir exenta
(caso real de producción: CDCOPIADVD S.L.U, ESB65623175, serie 2).

Cubre: el régimen y el motivo de las dos puntas, cuándo manda la ficha de
FACTUSOL, cuándo hace falta VIES, el país de cada serie, el IVA del documento
según su serie y el informe (SOLO LECTURA) de fichas y documentos
descuadrados.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.erp.factusol_pdf import (
    issuer_companies,
    issuer_iso2_for_serie,
    issuer_iso2_por_serie,
)
from app.integrations.factusol.albaran_manual import apply_regime
from app.integrations.factusol.auditoria_iva import (
    Emisor,
    auditar,
    informe_texto,
    revisar_documento,
    revisar_ficha,
)
from app.integrations.factusol.quotes import build_quote_payload, regime_de_serie
from app.integrations.factusol.vat_regime import (
    REGIME_EXPORTACION,
    REGIME_INTRACOMUNITARIO,
    REGIME_NACIONAL,
    ficha_manda,
    pareja_elegible,
    regime_for,
    regime_reason,
    regimes_por_serie,
    vies_hace_falta,
)
from app.models.crm import Company

#: NIF-IVA reales usados en los volcados (el español es el del caso de
#: producción que destapó el fallo).
ES_VAT = "ESB65623175"
BE_VAT = "BE0812240188"
DE_VAT = "DE455128445"


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


# --- la pareja ---------------------------------------------------------------


def test_regimen_de_la_pareja() -> None:
    def r(cliente: str | None, emisor: str | None, **kw) -> str:
        return regime_for(cliente, issuer_iso2=emisor, **kw)

    # Lo que Streamtec emite hoy no cambia.
    assert r("ES", "ES") == REGIME_NACIONAL
    assert r("DE", "ES", vat=DE_VAT) == REGIME_INTRACOMUNITARIO
    assert r("NO", "ES") == REGIME_EXPORTACION
    assert r(None, "ES") == REGIME_NACIONAL
    # Y lo que emite MQ Europe (BE) ya no da España por supuesta.
    assert r("ES", "BE", vat=ES_VAT) == REGIME_INTRACOMUNITARIO
    assert r("BE", "BE", vat=BE_VAT) == REGIME_NACIONAL   # el mismo país
    assert r("DE", "BE", vat=DE_VAT) == REGIME_INTRACOMUNITARIO
    assert r("ES", "BE") == REGIME_NACIONAL              # sin NIF-IVA: IVA belga
    assert r("NO", "BE") == REGIME_EXPORTACION
    # Sin emisor se supone el de la serie por defecto (España), como antes.
    assert r("ES", None) == REGIME_NACIONAL


def test_el_motivo_nombra_las_dos_puntas() -> None:
    assert regime_reason("ES", issuer_iso2="BE", vat=ES_VAT, vies_valid=True) == (
        f"Bélgica → España (UE) con NIF-IVA {ES_VAT} verificado en VIES → "
        "intracomunitario")
    assert regime_reason("BE", issuer_iso2="BE", vat=BE_VAT) == (
        "Bélgica → Bélgica (el mismo país) → nacional")
    assert regime_reason("ES", issuer_iso2="BE") == (
        "Bélgica → España (UE) sin NIF-IVA → nacional (IVA de Bélgica)")
    assert regime_reason("NO", issuer_iso2="BE") == (
        "Bélgica → Noruega (fuera de la UE) → exportación")


def test_manda_la_ficha_de_factusol() -> None:
    """Donde la pareja permite elegir, manda la ficha F_CLI: es la decisión del
    operador y lo que FACTUSOL usa al facturar. La excepción es el cliente
    recién creado desde el CRM de BoHub (esa ficha la acaba de escribir BoHub,
    no hay decisión que respetar)."""
    # Pareja elegible (BE → ES con NIF-IVA): la ficha gana al cálculo…
    assert regime_for("ES", issuer_iso2="BE", vat=ES_VAT, vies_valid=True,
                      fcli_regime=REGIME_NACIONAL) == REGIME_NACIONAL
    # …incluso contra un VIES que dice que no (sigue siendo la decisión del
    # operador, y es lo que FACTUSOL va a aplicar de todas formas).
    assert regime_for("ES", issuer_iso2="BE", vat=ES_VAT, vies_valid=False,
                      fcli_regime=REGIME_INTRACOMUNITARIO) == REGIME_INTRACOMUNITARIO
    # Cliente nuevo creado desde BoHub: manda el cálculo.
    assert regime_for("ES", issuer_iso2="BE", vat=ES_VAT, vies_valid=True,
                      fcli_regime=REGIME_NACIONAL,
                      cliente_nuevo_bohub=True) == REGIME_INTRACOMUNITARIO
    # Fuera de la zona de elección el régimen es aritmética: una ficha mal
    # configurada (el 525 de Noruega venía como nacional) no manda.
    assert regime_for("NO", issuer_iso2="ES",
                      fcli_regime=REGIME_NACIONAL) == REGIME_EXPORTACION
    assert regime_for("ES", issuer_iso2="ES",
                      fcli_regime=REGIME_INTRACOMUNITARIO) == REGIME_NACIONAL
    assert ficha_manda(REGIME_EXPORTACION) is None       # la ficha no decide eso
    assert ficha_manda(None) is None
    assert ficha_manda(REGIME_NACIONAL, cliente_nuevo_bohub=True) is None
    # Y el motivo lo dice.
    assert "ficha de FACTUSOL" in regime_reason(
        "ES", issuer_iso2="BE", vat=ES_VAT, fcli_regime=REGIME_NACIONAL)


def test_vies_solo_cuando_la_pareja_puede_eximir() -> None:
    """Un cliente español facturado por Streamtec es nacional pase lo que pase:
    ahí VIES no aporta nada. Para MQ Europe (BE) ese mismo cliente sí lo
    necesita, porque la exención depende de que su NIF-IVA sea válido."""
    assert vies_hace_falta("ES", issuer_iso2="ES", vat=ES_VAT) is False
    assert vies_hace_falta("ES", issuer_iso2="BE", vat=ES_VAT) is True
    assert vies_hace_falta("BE", issuer_iso2="BE", vat=BE_VAT) is False
    assert vies_hace_falta("DE", issuer_iso2="ES", vat=DE_VAT) is True
    assert vies_hace_falta("NO", issuer_iso2="BE") is False   # VIES no aplica
    assert vies_hace_falta("DE", issuer_iso2="BE") is False   # sin NIF-IVA
    assert pareja_elegible("DE", issuer_iso2="BE") is True
    assert pareja_elegible(None, issuer_iso2="BE") is False


def test_pais_de_cada_serie(session_factory) -> None:
    """Las identidades por defecto: 1 Bomedia (ES), 5 Streamtec (ES) y 2 MQ
    Europe (BE). El país es explícito (`pais_iso2`): deducirlo del literal
    sería adivinar, y aquí adivinar es facturar mal."""
    with session_factory() as s:
        assert issuer_iso2_for_serie(s, 1) == "ES"
        assert issuer_iso2_for_serie(s, 5) == "ES"
        assert issuer_iso2_for_serie(s, 2) == "BE"
        assert issuer_iso2_for_serie(s, None) is None
        assert issuer_iso2_por_serie(s) == {"1": "ES", "2": "BE", "5": "ES"}
        assert [(e["serie"], e["pais_iso2"]) for e in issuer_companies(s)] == [
            (1, "ES"), (2, "BE"), (5, "ES")]


# --- el IVA del documento ----------------------------------------------------


def _linea() -> dict[str, Any]:
    return {"description": "UV INK", "quantity": 1, "unit_price": 100.0,
            "iva_pct": 21.0}


def test_proforma_de_la_serie_2_a_cliente_espanol_sale_exenta(session_factory) -> None:
    """El caso de producción: cliente español con NIF-IVA, proforma de la serie
    2 (MQ Europe, BE). Por la serie 5 (Streamtec) la misma proforma lleva el
    21 %."""
    from app.erp.api.factusol import _customer_from_company  # noqa: PLC0415

    with session_factory() as s:
        comp = Company(name="CDCOPIADVD S.L.U", country="ES", vat=ES_VAT,
                       tax_id="B65623175", factusol_company_id="4471")
        s.add(comp)
        s.commit()
        serie2 = _customer_from_company(s, comp.id, serie=2)
        serie5 = _customer_from_company(s, comp.id, serie=5)

    assert serie2["regime"] == REGIME_INTRACOMUNITARIO
    assert serie5["regime"] == REGIME_NACIONAL
    # El mapa va en el cliente porque la serie definitiva la resuelve el worker
    # (al editar, es la de la fila que ya existe en FACTUSOL).
    assert serie2["regime_por_serie"] == {
        "1": REGIME_NACIONAL, "2": REGIME_INTRACOMUNITARIO, "5": REGIME_NACIONAL}

    payload2 = build_quote_payload(
        "1", ejercicio="2026", customer=serie2, refpre="", lines=[_linea()],
        serie=2,
    )
    assert payload2["PIVA1PRE"] == 0 and payload2["IIVA1PRE"] == 0
    assert payload2["TOTPRE"] == 100.0
    payload5 = build_quote_payload(
        "1", ejercicio="2026", customer=serie2, refpre="", lines=[_linea()],
        serie=5,
    )
    assert payload5["PIVA1PRE"] == 21.0 and payload5["IIVA1PRE"] == 21.0
    assert payload5["TOTPRE"] == 121.0


def test_regime_de_serie_respaldos() -> None:
    cliente = {"regime": REGIME_NACIONAL,
               "regime_por_serie": {"2": REGIME_INTRACOMUNITARIO}}
    assert regime_de_serie(cliente, 2) == REGIME_INTRACOMUNITARIO
    # Serie sin entrada en el mapa → el régimen suelto (jobs ya encolados).
    assert regime_de_serie(cliente, 5) == REGIME_NACIONAL
    assert regime_de_serie({"regime": REGIME_EXPORTACION}, 2) == REGIME_EXPORTACION
    assert regime_de_serie({}, 2) is None


def test_la_proforma_sigue_la_ficha_donde_la_pareja_lo_permite() -> None:
    """En la zona de elección la proforma lee la ficha F_CLI y la respeta, para
    no contradecir al albarán del mismo pedido (ni a lo que FACTUSOL aplicará).
    Fuera de esa zona no se consulta la ficha: ni una lectura de más."""
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.quotes import (  # noqa: PLC0415
        customer_con_regimen,
    )

    cliente = {
        "codcli": "4471",
        "regime": REGIME_NACIONAL,
        "regime_por_serie": {"2": REGIME_INTRACOMUNITARIO, "5": REGIME_NACIONAL},
        "ficha_decide_por_serie": {"2": True, "5": False},
    }
    fake = FakeFactusolDocs({"F_CLI": [_ficha(4471)]})          # ficha: nacional
    con_ficha = customer_con_regimen(fake, cliente, serie=2, ejercicio="2026")
    assert con_ficha["regime"] == REGIME_NACIONAL
    assert [t for t, _f in fake.lecturas] == ["F_CLI"]

    # Serie 5 (pareja España → España): no hay nada que decidir, no se lee.
    fake5 = FakeFactusolDocs({"F_CLI": [_ficha(4471)]})
    assert customer_con_regimen(
        fake5, cliente, serie=5, ejercicio="2026")["regime"] == REGIME_NACIONAL
    assert fake5.lecturas == []

    # FACTUSOL caído: se sigue con el régimen calculado, no se bloquea nada.
    class Caido(FakeFactusolDocs):
        def load_table(self, *a, **kw):
            raise FactusolError("DELSOL no responde")

    caido = Caido({})
    assert customer_con_regimen(
        caido, cliente, serie=2, ejercicio="2026",
    )["regime"] == REGIME_INTRACOMUNITARIO


def test_regimes_por_serie() -> None:
    assert regimes_por_serie({"1": "ES", "2": "BE", "5": "ES"}, "ES",
                             vat=ES_VAT, vies_valid=True) == {
        "1": REGIME_NACIONAL, "2": REGIME_INTRACOMUNITARIO, "5": REGIME_NACIONAL}
    assert regimes_por_serie({}, "ES") == {}


def test_albaran_manual_manda_la_ficha_en_la_zona_elegible() -> None:
    """En la zona de elección manda la ficha F_CLI; fuera de ella, el cálculo
    (y se avisa de la discrepancia en los dos casos)."""
    customer = {"codcli": "4471", "regime": REGIME_NACIONAL}
    regime, lines, warning = apply_regime(
        customer, [_linea()], regime=REGIME_INTRACOMUNITARIO, numero="2-1",
        ficha_decide=True,
    )
    assert regime == REGIME_NACIONAL and lines[0]["iva_pct"] == 21.0
    assert warning and "ficha de FACTUSOL" in warning

    regime, lines, warning = apply_regime(
        customer, [_linea()], regime=REGIME_EXPORTACION, numero="5-1",
        ficha_decide=False,
    )
    assert regime == REGIME_EXPORTACION and lines[0]["iva_pct"] == 0.0
    assert warning and "país de quien factura" in warning


# --- la auditoría (SOLO LECTURA) ---------------------------------------------

MQ = Emisor(serie=2, nombre="MQ Europe BV", pais_iso2="BE")


def _pre(codpre: int, cli: int, *, piva: float, base: float = 100.0) -> dict[str, Any]:
    return {"TIPPRE": "2", "CODPRE": codpre, "CLIPRE": cli,
            "CNOPRE": "CDCOPIADVD S.L.U", "FECPRE": "2026-09-01",
            "BAS1PRE": base, "PIVA1PRE": piva, "IIVA1PRE": base * piva / 100,
            "TOTPRE": base + base * piva / 100}


def _fac(codfac: int, cli: int, *, piva: float, base: float = 200.0) -> dict[str, Any]:
    return {"TIPFAC": 2, "CODFAC": codfac, "CLIFAC": cli,
            "CNOFAC": "SPRL Clossetcadeaux", "FECFAC": "2026-09-02",
            "BAS1FAC": base, "PIVA1FAC": piva, "IIVA1FAC": base * piva / 100,
            "TOTFAC": base + base * piva / 100}


def _ficha(codcli: int, **over) -> dict[str, Any]:
    base = {"CODCLI": codcli, "NOFCLI": "CDCOPIADVD S.L.U", "NOCCLI": "",
            "NIFCLI": "B65623175", "PAICLI": "724",
            "IFICLI": 0, "IVACLI": 0, "TIVCLI": 1}
    base.update(over)
    return base


def test_revisar_documento() -> None:
    # Proforma de MQ Europe con 21 % a un cliente que le toca exento.
    hallazgo = revisar_documento(
        tipo="presupuestos", row=_pre(39, 4471, piva=21.0),
        esperado=REGIME_INTRACOMUNITARIO,
    )
    assert hallazgo is not None
    assert hallazgo["numero"] == "2-39" and hallazgo["emitido"] is False
    assert "lleva IVA" in hallazgo["problema"]
    # Factura sin IVA a un cliente belga de MQ Europe: ahí el IVA SÍ toca.
    hallazgo = revisar_documento(
        tipo="facturas", row=_fac(7, 3392, piva=0.0), esperado=REGIME_NACIONAL,
    )
    assert hallazgo is not None and hallazgo["emitido"] is True
    assert "sale sin IVA" in hallazgo["problema"]
    # Lo que cuadra, y lo que no se puede decidir, no son hallazgos.
    assert revisar_documento(tipo="presupuestos", row=_pre(40, 4471, piva=0.0),
                             esperado=REGIME_INTRACOMUNITARIO) is None
    assert revisar_documento(tipo="presupuestos", row=_pre(41, 4471, piva=21.0),
                             esperado=None) is None
    # Un documento a cero no descuadra nada.
    assert revisar_documento(tipo="presupuestos",
                             row=_pre(42, 4471, piva=0.0, base=0.0),
                             esperado=REGIME_NACIONAL) is None


def test_revisar_ficha() -> None:
    hallazgo = revisar_ficha(row=_ficha(4471), emisor=MQ, iso2="ES",
                             fuente_pais="CRM", vat=ES_VAT, vies_valid=True)
    assert hallazgo is not None
    assert hallazgo["regimen_ficha"] == REGIME_NACIONAL
    assert hallazgo["regimen_esperado"] == REGIME_INTRACOMUNITARIO
    assert "MQ Europe BV (Bélgica)" in hallazgo["problema"]
    # Una ficha nacional de un cliente belga SÍ cuadra para MQ Europe.
    assert revisar_ficha(row=_ficha(3392, PAICLI="056"), emisor=MQ, iso2="BE",
                         fuente_pais="ficha F_CLI") is None
    # Sin país no se decide: eso ya es el hallazgo.
    sin_pais = revisar_ficha(row=_ficha(9, PAICLI=""), emisor=MQ, iso2=None,
                             fuente_pais="ficha F_CLI")
    assert sin_pais is not None and sin_pais["regimen_esperado"] is None
    assert "sin país" in sin_pais["motivo"]


class FakeFactusolDocs:
    """Devuelve las cabeceras de cada tabla y las fichas de F_CLI. No escribe:
    si algo intentara escribir, el test revienta (la auditoría es de lectura)."""

    def __init__(self, tablas: dict[str, list[dict[str, Any]]]) -> None:
        self.default_ejercicio = "2026"
        self._tablas = tablas
        self.lecturas: list[tuple[str, str]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        self.lecturas.append((tabla, filtro))
        rows = self._tablas.get(tabla, [])
        if tabla == "F_CLI" and filtro.startswith("CODCLI="):
            codcli = filtro.split("=", 1)[1]
            return [r for r in rows if str(r.get("CODCLI")) == codcli]
        return list(rows)

    def write_record(self, *a, **kw):  # pragma: no cover - no debe llamarse
        raise AssertionError("la auditoría no escribe")

    def update_record(self, *a, **kw):  # pragma: no cover - no debe llamarse
        raise AssertionError("la auditoría no escribe")


def test_auditar_separa_proformas_de_lo_emitido(session_factory) -> None:
    fake = FakeFactusolDocs({
        "F_PRE": [_pre(39, 4471, piva=21.0),          # mal: debía ser exenta
                  _pre(40, 4471, piva=0.0),           # bien
                  {**_pre(41, 4471, piva=21.0), "TIPPRE": "5"}],  # otra serie
        "F_ALB": [],
        "F_FAC": [_fac(7, 3392, piva=0.0)],           # mal: debía llevar IVA
        "F_CLI": [_ficha(4471), _ficha(3392, PAICLI="056", NOFCLI="SPRL")],
    })
    with session_factory() as s:
        s.add(Company(name="CDCOPIADVD S.L.U", country="ES", vat=ES_VAT,
                      tax_id="B65623175", factusol_company_id="4471"))
        s.commit()
        resultado = auditar(s, fake, ejercicio="2026", emisor=MQ)

    assert resultado["resumen"]["revisados"] == {
        "presupuestos": 2, "albaranes": 0, "facturas": 1}
    assert [d["numero"] for d in resultado["proformas"]] == ["2-39"]
    assert [d["numero"] for d in resultado["emitidos"]] == ["2-7"]
    # La ficha del cliente español: nacional, y MQ Europe le factura exento.
    assert [f["codcli"] for f in resultado["fichas"]] == ["4471"]
    assert resultado["fichas"][0]["fuente_pais"] == "CRM"
    # La del cliente belga cuadra (nacional desde Bélgica) y no sale.
    informe = informe_texto(resultado)
    assert "SOLO LECTURA" in informe and "MQ Europe BV (Bélgica)" in informe
    assert "Proformas con el IVA descuadrado: 1" in informe
    assert "YA EMITIDOS con el IVA descuadrado: 1" in informe
