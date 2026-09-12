"""Tarea C · Parte 2 — cliente FACTUSOL: tipo de documento (`IFICLI`), régimen
de IVA (`IVACLI`/`TIVCLI`) y país (`PAICLI`) en F_CLI.

Mapeo confirmado con volcados reales (2026-09-12): nacional 0/0/1,
intracomunitario 2/2/4, exportación IVACLI=3/TIVCLI=3 con IFICLI sin forzar.
Cubre: el régimen por país + NIF-IVA, el alta (`customers/create`) escribiendo
esas columnas y el país ISO real, el guard de esquema (si no cuadra, nada
escrito), la corrección de un cliente existente con `ActualizarRegistro`
mínimo (preview + fix con auditoría) y que sin cambios no se escribe.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.customers import (
    _country_code,
    build_customer_payload,
    create_customer,
    regime_preview,
    update_customer_regime,
)
from app.integrations.factusol.vat_regime import (
    REGIME_EXPORTACION,
    REGIME_INTRACOMUNITARIO,
    REGIME_NACIONAL,
    eu_vat_for,
    normalize_vat,
    regime_for,
    regime_from_fcli_row,
)
from app.main import app
from app.models.crm import AuditLog, Company
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_customers import FakeFactusol, _cli, _patch_client

# Filas REALES de los volcados de Bart (reducidas a las columnas que importan).
NACIONAL_3011 = _cli(3011, nombre="ANTONIO MOLINA PUERTO", nif="48288265H",
                     PAICLI="724", IFICLI=0, IVACLI=0, TIVCLI=1)
INTRACOM_3392_MAL = _cli(3392, nombre="SPRL Clossetcadeaux", nif="BE0812240188",
                         PAICLI="056", IFICLI=0, IVACLI=0, TIVCLI=1)
INTRACOM_4279_OK = _cli(4279, nombre="KOPFBRAND GmbH", nif="DE455128445",
                        PAICLI="276", IFICLI=2, IVACLI=2, TIVCLI=4)
EXPORT_525_MAL = _cli(525, nombre="Nasjonalbiblioteket", nif="NO 976 029 100",
                      PAICLI="Norway", IFICLI=0, IVACLI=0, TIVCLI=1)


class RegimeFake(FakeFactusol):
    """FakeFactusol + `ActualizarRegistro` en memoria y plantilla del alta
    configurable (para simular un esquema que no cuadra)."""

    def __init__(self, rows=None, *, max_codcli=None, template: dict | None = None):
        super().__init__(rows, max_codcli=max_codcli)
        self.updates: list[tuple[str, dict]] = []
        self._template = template

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla == "F_CLI" and "ORDER BY CODCLI DESC" in filtro and self._template is not None:
            self.filters.append(filtro)
            return [dict(self._template)]
        return super().load_table(tabla, filtro=filtro, ejercicio=ejercicio)

    def update_record(self, tabla, data, *, ejercicio=None):
        self.updates.append((tabla, dict(data)))
        return {"ok": True}


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
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _company(session_factory, **fields) -> str:
    with session_factory() as s:
        comp = Company(**{"name": "Nueva SL", **fields})
        s.add(comp)
        s.commit()
        return comp.id


def _create(client, comp_id: str, **over) -> Any:
    body = {"crm_type": "company", "crm_id": comp_id, "nombre": "Nueva SL",
            "nif": "B12345678", "direccion": "C Falsa 1", "ciudad": "Barcelona",
            "cp": "08001", "provincia": "Barcelona"}
    body.update(over)
    return client.post("/api/erp/factusol/customers/create", json=body,
                       headers=auth_headers(client, "pedidos"))


def _written(fake: FakeFactusol) -> dict[str, Any]:
    return next(rec for t, rec in fake.writes if t == "F_CLI")


# --- régimen por país + NIF-IVA ----------------------------------------------


def test_regimen_por_pais_y_nif_iva() -> None:
    assert regime_for("ES") == REGIME_NACIONAL
    assert regime_for(None) == REGIME_NACIONAL                      # sin país
    assert regime_for("BE", nif="BE0812240188") == REGIME_INTRACOMUNITARIO
    assert regime_for("DE", vat="DE 455.128.445") == REGIME_INTRACOMUNITARIO
    assert regime_for("Belgium", nif="BE0812240188") == REGIME_INTRACOMUNITARIO
    assert regime_for("FR") == REGIME_NACIONAL                      # UE sin NIF-IVA
    assert regime_for("FR", nif="12345678") == REGIME_NACIONAL
    assert regime_for("NO") == REGIME_EXPORTACION
    assert regime_for("US", nif="12-3456789") == REGIME_EXPORTACION
    # Un NIF-IVA de OTRO país no convierte al cliente en intracomunitario.
    assert regime_for("FR", nif="BE0812240188") == REGIME_NACIONAL
    assert normalize_vat("be 0812.240-188") == "BE0812240188"
    assert normalize_vat("48288265H") is None and normalize_vat("") is None
    assert eu_vat_for("BE", vat="", nif="BE0812240188") == "BE0812240188"
    assert eu_vat_for("BE", vat="BE0999999999", nif="BE0812240188") == "BE0999999999"
    # Lo que codifica una fila real.
    assert regime_from_fcli_row(NACIONAL_3011) == REGIME_NACIONAL
    assert regime_from_fcli_row(INTRACOM_4279_OK) == REGIME_INTRACOMUNITARIO
    assert regime_from_fcli_row({"IVACLI": 3}) == REGIME_EXPORTACION
    assert regime_from_fcli_row({"IVACLI": 7}) is None and regime_from_fcli_row(None) is None


# --- alta: IFICLI / IVACLI / TIVCLI + PAICLI --------------------------------


def test_cliente_factusol_ifi_nacional(client, session_factory) -> None:
    """Empresa española: N.I.F. (IFICLI=0), IVA nacional (IVACLI=0), 21 %
    (TIVCLI=1) y PAICLI=724 — el alta lo escribe, ya no se queda en el
    default de FACTUSOL."""
    payload = build_customer_payload({"nombre": "X", "nif": "B1", "pais": "ES"}, "7")
    assert (payload["IFICLI"], payload["IVACLI"], payload["TIVCLI"]) == (0, 0, 1)
    assert payload["PAICLI"] == "724"

    comp_id = _company(session_factory, country="ES", tax_id="B12345678")
    fake = RegimeFake([], max_codcli=4531)
    with _patch_client(fake):
        r = _create(client, comp_id)
    assert r.status_code == 201, r.text
    assert r.json()["regime"] == REGIME_NACIONAL
    written = _written(fake)
    assert (written["IFICLI"], written["IVACLI"], written["TIVCLI"]) == (0, 0, 1)
    assert written["PAICLI"] == "724" and written["CODCLI"] == "4532"


def test_cliente_factusol_ifi_intracomunitario(client, session_factory) -> None:
    """Empresa belga con NIF-IVA (`Company.vat`, que antes nadie leía): NIF/IVA
    operador intracomunitario (IFICLI=2), IVACLI=2, Exento (TIVCLI=4) y
    PAICLI=056. El payload NO manda país ni VAT: salen de la empresa CRM."""
    comp_id = _company(session_factory, name="SPRL Clossetcadeaux", country="BE",
                       vat="BE0812240188", tax_id="BE0812240188")
    fake = RegimeFake([], max_codcli=4531)
    with _patch_client(fake):
        r = _create(client, comp_id, nombre="SPRL Clossetcadeaux", nif="BE0812240188")
    assert r.status_code == 201, r.text
    assert r.json()["regime"] == REGIME_INTRACOMUNITARIO
    assert r.json()["regime_label"] == "Intracomunitario (exento)"
    written = _written(fake)
    assert (written["IFICLI"], written["IVACLI"], written["TIVCLI"]) == (2, 2, 4)
    assert written["PAICLI"] == "056"
    # Con el NIF con prefijo del país basta (KOPFBRAND: DE455128445).
    payload = build_customer_payload(
        {"nombre": "KOPFBRAND GmbH", "nif": "DE455128445", "pais": "DE"}, "4279",
    )
    assert (payload["IFICLI"], payload["IVACLI"], payload["TIVCLI"]) == (2, 2, 4)
    assert payload["PAICLI"] == "276"


def test_cliente_factusol_ifi_exportacion(client, session_factory) -> None:
    """Fuera de la UE: IVACLI=3 y TIVCLI=3 (0 %); `IFICLI` NO se fuerza (sin
    volcado de referencia de un cliente de exportación bien configurado)."""
    comp_id = _company(session_factory, name="Nasjonalbiblioteket", country="NO",
                       tax_id="NO 976 029 100")
    fake = RegimeFake([], max_codcli=4531)
    with _patch_client(fake):
        r = _create(client, comp_id, nombre="Nasjonalbiblioteket", nif="NO 976 029 100")
    assert r.status_code == 201, r.text
    assert r.json()["regime"] == REGIME_EXPORTACION
    written = _written(fake)
    assert (written["IVACLI"], written["TIVCLI"]) == (3, 3)
    assert "IFICLI" not in written
    assert written["PAICLI"] == "578"


def test_pais_paicli_iso_numerico(client, session_factory) -> None:
    """`PAICLI` = ISO 3166-1 numérico REAL con la tabla completa: Noruega →
    578 (antes «Norway» literal o 724), Austria → 040. Un país explícito en
    el payload manda sobre el de la empresa."""
    assert _country_code("Norway") == "578" and _country_code("NO") == "578"
    assert build_customer_payload({"nombre": "X", "pais": "Norway"}, "1")["PAICLI"] == "578"
    assert build_customer_payload({"nombre": "X", "pais": "AT"}, "1")["PAICLI"] == "040"

    comp_id = _company(session_factory, country="Norway")
    fake = RegimeFake([], max_codcli=10)
    with _patch_client(fake):
        r = _create(client, comp_id)
    assert r.status_code == 201, r.text
    assert _written(fake)["PAICLI"] == "578"

    fake2 = RegimeFake([], max_codcli=20)          # otro CODCLI: el 11 ya está vinculado
    comp2 = _company(session_factory, country="NO")
    with _patch_client(fake2):
        r = _create(client, comp2, pais="AT")
    assert r.status_code == 201, r.text
    assert _written(fake2)["PAICLI"] == "040"
    assert r.json()["regime"] == REGIME_NACIONAL    # AT (UE) sin NIF-IVA → nacional


# --- guard + sobrescribir lo mínimo -----------------------------------------


def test_escritura_fcli_guard_minimo(client, session_factory) -> None:
    """Corrección de un cliente existente: se lee la fila REAL y se manda a
    `ActualizarRegistro` SOLO la clave + las columnas que cambian, con el
    tipo de la fila real; sin cambios no se escribe; si el esquema no cuadra
    (columna que no existe / tipo distinto) no se escribe nada. El alta hace
    el mismo guard contra la fila viva."""
    # 525 (Noruega, mal): IVACLI 0→3, TIVCLI 1→3, PAICLI 'Norway'→'578'; IFICLI no se toca.
    fake = RegimeFake([EXPORT_525_MAL])
    result = update_customer_regime(fake, codcli="525", ejercicio="2026", country_iso2="NO")
    assert result["changed"] is True and result["regime"] == REGIME_EXPORTACION
    assert fake.updates == [("F_CLI", {"CODCLI": 525, "IVACLI": 3, "TIVCLI": 3,
                                       "PAICLI": "578"})]
    assert result["written"] == {"IVACLI": 3, "TIVCLI": 3, "PAICLI": "578"}
    assert fake.writes == []                                   # nunca EscribirRegistro

    # 3392 (Bélgica, mal): 0/0/1 → 2/2/4; el país ya está bien → no se manda.
    fake = RegimeFake([INTRACOM_3392_MAL])
    result = update_customer_regime(
        fake, codcli="3392", ejercicio="2026", country_iso2="BE", nif="BE0812240188",
    )
    assert fake.updates == [("F_CLI", {"CODCLI": 3392, "IFICLI": 2, "IVACLI": 2,
                                       "TIVCLI": 4})]

    # 4279 (Alemania, bien configurado a mano): nada que corregir, nada escrito.
    fake = RegimeFake([INTRACOM_4279_OK])
    result = update_customer_regime(
        fake, codcli="4279", ejercicio="2026", country_iso2="DE", vat="DE455128445",
    )
    assert result["changed"] is False and result["coherent"] is True
    assert fake.updates == []

    # Guard de tipo: la fila real trae IVACLI como texto → no se escribe.
    fake = RegimeFake([{**INTRACOM_3392_MAL, "IVACLI": "0"}])
    with pytest.raises(FactusolError, match="No se ha escrito nada"):
        update_customer_regime(fake, codcli="3392", ejercicio="2026",
                               country_iso2="BE", nif="BE0812240188")
    assert fake.updates == []
    # Guard de columna: la fila real no tiene TIVCLI → no se escribe.
    row = dict(INTRACOM_3392_MAL)
    del row["TIVCLI"]
    fake = RegimeFake([row])
    with pytest.raises(FactusolError, match="columna desconocida TIVCLI"):
        update_customer_regime(fake, codcli="3392", ejercicio="2026",
                               country_iso2="BE", nif="BE0812240188")
    assert fake.updates == []
    # Cliente inexistente → error claro.
    with pytest.raises(FactusolError, match="no existe"):
        update_customer_regime(RegimeFake([]), codcli="9", ejercicio="2026",
                               country_iso2="ES")

    # Alta: la fila viva no tiene las columnas de régimen → 502 y nada escrito.
    comp_id = _company(session_factory, country="BE", vat="BE0812240188")
    fake = RegimeFake([], max_codcli=4531, template={"CODCLI": 4531, "NOFCLI": "X"})
    with _patch_client(fake):
        r = _create(client, comp_id)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "factusol_create_failed"
    assert "columna desconocida IFICLI" in r.json()["detail"]["detail"]
    assert fake.writes == []
    with session_factory() as s:
        assert s.get(Company, comp_id).factusol_company_id is None
    # Y directamente, con tipo distinto en la fila viva.
    fake = RegimeFake([], max_codcli=4531,
                      template={**_cli(4531), "TIVCLI": "1"})
    with pytest.raises(FactusolError, match="No se ha escrito nada"):
        create_customer(fake, {"nombre": "X", "pais": "ES"}, ejercicio="2026")
    assert fake.writes == []


# --- endpoints: preview + fix ----------------------------------------------


def test_regime_preview_muestra_actual_y_propuesto(client, session_factory) -> None:
    comp_id = _company(session_factory, name="SPRL Clossetcadeaux", country="BE",
                       vat="BE0812240188", tax_id="BE0812240188",
                       factusol_company_id="3392")
    fake = RegimeFake([INTRACOM_3392_MAL])
    with _patch_client(fake):
        r = client.get(f"/api/erp/factusol/customers/regime-preview?company_id={comp_id}",
                       headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["codcli"] == "3392" and body["regime"] == REGIME_INTRACOMUNITARIO
    assert body["reason"] == "BE (UE) con NIF-IVA BE0812240188 → intracomunitario"
    assert body["current"]["regime"] == REGIME_NACIONAL
    assert body["current"]["IFICLI"] == 0 and body["current"]["PAICLI"] == "056"
    assert body["proposed"] == {"IFICLI": 2, "IVACLI": 2, "TIVCLI": 4, "PAICLI": "056"}
    assert [c["column"] for c in body["changes"]] == ["IFICLI", "IVACLI", "TIVCLI"]
    assert body["changes"][0]["proposed_label"] == "2 · NIF/IVA operador intracomunitario"
    assert body["coherent"] is False
    assert fake.updates == [] and fake.writes == []          # solo lectura

    # Preview de la lógica pura con la fila de Noruega.
    preview = regime_preview(EXPORT_525_MAL, country_iso2="NO")
    assert preview["regime"] == REGIME_EXPORTACION
    assert [c["column"] for c in preview["changes"]] == ["IVACLI", "TIVCLI", "PAICLI"]


def test_fix_regime_escribe_lo_minimo_y_audita(client, session_factory) -> None:
    comp_id = _company(session_factory, name="Nasjonalbiblioteket", country="NO",
                       tax_id="NO 976 029 100", factusol_company_id="525")
    fake = RegimeFake([EXPORT_525_MAL])
    with _patch_client(fake):
        r = client.post("/api/erp/factusol/customers/fix-regime",
                        json={"company_id": comp_id},
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True and body["regime"] == REGIME_EXPORTACION
    assert body["written"] == {"IVACLI": 3, "TIVCLI": 3, "PAICLI": "578"}
    assert fake.updates == [("F_CLI", {"CODCLI": 525, "IVACLI": 3, "TIVCLI": 3,
                                       "PAICLI": "578"})]
    with session_factory() as s:
        log = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.factusol_customer_regime",
        )).one()
        meta = json.loads(log.metadata_json)
        assert meta["factusol_codcli"] == "525" and meta["regime"] == REGIME_EXPORTACION
        assert "régimen de IVA corregido" in meta["summary"]

    # Segunda vez: ya coherente → nada escrito ni auditado.
    fake2 = RegimeFake([{**EXPORT_525_MAL, "IVACLI": 3, "TIVCLI": 3, "PAICLI": "578"}])
    with _patch_client(fake2):
        r = client.post("/api/erp/factusol/customers/fix-regime",
                        json={"company_id": comp_id},
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200 and r.json()["changed"] is False
    assert fake2.updates == []


def test_fix_regime_guards(client, session_factory) -> None:
    """Sin vínculo → 409; esquema que no cuadra → 502 y nada escrito; solo
    lectura no puede corregir → 403."""
    unlinked = _company(session_factory, country="BE")
    r = client.post("/api/erp/factusol/customers/fix-regime",
                    json={"company_id": unlinked},
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "company_unlinked"

    comp_id = _company(session_factory, country="BE", vat="BE0812240188",
                       factusol_company_id="3392")
    fake = RegimeFake([{**INTRACOM_3392_MAL, "IVACLI": "0"}])
    with _patch_client(fake):
        r = client.post("/api/erp/factusol/customers/fix-regime",
                        json={"company_id": comp_id},
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 502 and r.json()["detail"]["code"] == "factusol_regime_failed"
    assert fake.updates == []

    r = client.post("/api/erp/factusol/customers/fix-regime",
                    json={"company_id": comp_id},
                    headers=auth_headers(client, "sat"))
    assert r.status_code == 403
