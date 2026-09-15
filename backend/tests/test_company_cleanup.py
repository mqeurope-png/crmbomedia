"""Limpieza de empresas: auto-vincular por NIF, rellenar NIF desde FACTUSOL y
archivar (reversible) las que no están en FACTUSOL ni tienen negocio vivo.
FACTUSOL SIEMPRE solo lectura (un doble que solo lee); nada se le escribe.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.company_cleanup import (
    apply_archive,
    apply_backfill,
    apply_links,
    plan_archive,
    plan_backfill,
    plan_links,
)
from app.erp.models import Order, OrderSource
from app.erp.workflow import order_workflow
from app.main import app
from app.models.crm import Company, Contact, Task
from tests._test_helpers import auth_headers, seed_test_users

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)

# ONLYGUAY casa por CIF aunque el nombre difiera; EXATRONIC tiene DOS CODCLI.
F_CLI = [
    {"CODCLI": 3734, "NIFCLI": "J70876677", "NOFCLI": "ONLYGUAY S C"},
    {"CODCLI": 2629, "NIFCLI": "PT503420506", "NOFCLI": "EXATRONIC LDA"},
    {"CODCLI": 2819, "NIFCLI": "503420506", "NOFCLI": "EXATRONIC LDA"},
    {"CODCLI": 2458, "NIFCLI": "B12345678", "NOFCLI": "DUPLICODER SL"},
]


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as s:
        seed_test_users(s)
        s.add_all([
            # Casa por CIF (nombre distinto), sin CODCLI → auto-vincular.
            Company(id="only", name="ONLYGUAY SC", tax_id="J70876677"),
            # Casa con DOS CODCLI → revisión, no se vincula.
            Company(id="exa", name="Exatronic Lda", vat="PT503420506"),
            # Vinculada por CODCLI y SIN NIF en el CRM → backfill.
            Company(id="dupli", name="Duplicoder SL", factusol_company_id="2458"),
            # Vinculada pero el CRM ya tiene OTRO NIF → revisión (no se pisa).
            Company(id="otro", name="Otro SL", tax_id="B99999999",
                    factusol_company_id="2458"),
            # NO en FACTUSOL, con pedido → protegida.
            Company(id="conped", name="Con Pedido SL", tax_id="B11111111"),
            # NO en FACTUSOL, con tarea abierta → protegida.
            Company(id="contarea", name="Con Tarea SL", tax_id="B22222222"),
            # NO en FACTUSOL, solo un contacto → NO protege → candidata.
            Company(id="solocont", name="Solo Contacto SL", tax_id="B33333333"),
            # NO en FACTUSOL, con NIF y sin nada → candidata.
            Company(id="ruidonif", name="Ruido con NIF SL", tax_id="B44444444"),
            # NO en FACTUSOL, sin NIF y sin nada → candidata.
            Company(id="ruido", name="Ruido SL"),
        ])
        s.add_all([
            Contact(id="ct1", first_name="Ana", company_id="solocont"),
            Order(id="o1", order_number="MAN-1", company_id="conped",
                  external_source=OrderSource.MANUAL, total_amount=10.0, currency="EUR",
                  payment_status="pending", preparation_status="pending_review"),
            Task(id="t1", title="Llamar", status="pending", company_id="contarea",
                 assigned_user_id=_uid(s), created_by_user_id=_uid(s)),
        ])
        s.commit()
    yield factory
    Base.metadata.drop_all(engine)


def _uid(s: Session) -> str:
    from app.models.crm import User  # noqa: PLC0415

    return s.scalar(select(User.id).order_by(User.email.asc()).limit(1))


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- 1) auto-vincular por NIF ---------------------------------------------------------------


def test_auto_vincular_solo_con_un_unico_codcli(session_factory) -> None:
    with session_factory() as s:
        plan = plan_links(s, F_CLI)
        # ONLYGUAY casa con 3734 (nombre distinto no importa); DUPLICODER ya
        # vinculada no entra; EXATRONIC casa con dos → revisión.
        assert {a.company_id: a.codcli for a in plan.to_link} == {"only": "3734"}
        assert [r.company_id for r in plan.review] == ["exa"]
        assert "varios CODCLI" in plan.review[0].reason
        n = apply_links(s, plan, now=NOW)
        s.commit()
    assert n == 1
    with session_factory() as s:
        assert s.get(Company, "only").factusol_company_id == "3734"
        assert s.get(Company, "only").factusol_sync_source == "auto_link_nif"
        assert s.get(Company, "exa").factusol_company_id is None      # revisión: no se toca
        # Idempotente: un segundo pase no vuelve a vincular.
        assert apply_links(s, plan_links(s, F_CLI), now=NOW) == 0


# --- 2) rellenar NIF desde FACTUSOL ---------------------------------------------------------


def test_backfill_no_pisa_nif_existente(session_factory) -> None:
    with session_factory() as s:
        plan = plan_backfill(s, F_CLI)
        assert {a.company_id: a.nif for a in plan.to_fill} == {"dupli": "B12345678"}
        assert [r.company_id for r in plan.review] == ["otro"]        # ya tiene otro NIF
        assert "no se pisa" in plan.review[0].reason
        n = apply_backfill(s, plan)
        s.commit()
    assert n == 1
    with session_factory() as s:
        assert s.get(Company, "dupli").tax_id == "B12345678"
        assert s.get(Company, "otro").tax_id == "B99999999"           # intacto
        # Idempotente: ya no tiene nada que rellenar.
        assert plan_backfill(s, F_CLI).to_fill == []


# --- 3) archivar (reversible) ---------------------------------------------------------------


def test_archive_regla_protege_negocio_y_no_por_contacto(session_factory) -> None:
    with session_factory() as s:
        plan = plan_archive(s, F_CLI, now=NOW)
    ids = {c.company_id for c in plan.candidates}
    # Candidatas: solo-contacto (no protege), ruido con NIF, ruido sin NIF.
    assert ids == {"solocont", "ruidonif", "ruido"}
    # Protegidas: en FACTUSOL (only casa, dupli/otro vinculadas), con pedido, con tarea.
    assert not ids & {"only", "exa", "dupli", "otro", "conped", "contarea"}
    assert plan.buckets == {
        "sin_nif_sin_nada": 1, "sin_nif_solo_contacto": 0,
        "con_nif_sin_nada": 1, "con_nif_solo_contacto": 1,
    }
    assert any("solo contactos" in c.reason for c in plan.candidates if c.company_id == "solocont")


def test_archive_dry_run_no_escribe(session_factory) -> None:
    writes: list[str] = []
    with session_factory() as s:
        @event.listens_for(s.get_bind(), "before_cursor_execute")
        def _watch(conn, cur, stmt, params, ctx, many):  # noqa: ANN001
            if stmt.strip().split(" ", 1)[0].upper() in ("INSERT", "UPDATE", "DELETE"):
                writes.append(stmt[:40])

        plan_archive(s, F_CLI, now=NOW)
        plan_links(s, F_CLI)
        plan_backfill(s, F_CLI)
        s.commit()
    assert writes == []


def test_archive_apply_marca_reversible_e_idempotente(session_factory) -> None:
    with session_factory() as s:
        plan = plan_archive(s, F_CLI, now=NOW)
        n = apply_archive(s, plan, now=NOW)
        s.commit()
    assert n == 3
    with session_factory() as s:
        r = s.get(Company, "ruido")
        assert r.is_archived is True and r.archived_at == NOW and r.archived_reason
        # No se ha borrado: la fila sigue ahí.
        assert s.scalar(select(Company.id).where(Company.id == "ruido")) == "ruido"
        # Idempotente: repetir no archiva más (ya archivadas fuera del plan).
        assert apply_archive(s, plan_archive(s, F_CLI, now=NOW), now=NOW) == 0
        # Reversible.
        r.is_archived = False
        r.archived_at = None
        s.commit()
    with session_factory() as s:
        assert s.get(Company, "ruido").is_archived is False


def test_una_empresa_en_factusol_nunca_se_archiva(session_factory) -> None:
    """Aunque no tenga NINGUNA actividad, si casa por NIF con F_CLI se queda."""
    with session_factory() as s:
        # ONLYGUAY casa por NIF y no tiene pedidos/tareas/contactos: aun así protegida.
        assert "only" not in {c.company_id for c in plan_archive(s, F_CLI, now=NOW).candidates}


# --- archivadas fuera de listados / buscador y sin incidencias -------------------------------


def test_archivadas_fuera_de_listados_y_buscador(http, session_factory) -> None:
    with session_factory() as s:
        s.get(Company, "ruido").is_archived = True
        s.get(Company, "ruido").archived_at = NOW
        s.commit()
    h = auth_headers(http, "user")
    # Listado unificado (/api/companies): fuera por defecto, dentro con toggle.
    ids = lambda **p: {c["id"] for c in http.get(  # noqa: E731
        "/api/companies", params={"limit": 100, **p}, headers=h).json()["items"]}
    assert "ruido" not in ids()
    assert "ruido" in ids(include_archived="true")
    # Buscador (q): tampoco aparece.
    assert "ruido" not in ids(q="Ruido")
    assert "ruido" in ids(q="Ruido", include_archived="true")
    # Búsqueda de entidad (la lista principal): fuera por defecto.
    body = {"limit": 100}
    r = http.post("/api/entities/company/search", json=body, headers=h).json()
    assert "ruido" not in {c["id"] for c in r["items"]}
    r2 = http.post("/api/entities/company/search", json={**body, "include_archived": True},
                   headers=h).json()
    assert "ruido" in {c["id"] for c in r2["items"]}


def test_archivar_y_restaurar_endpoints(http, session_factory) -> None:
    h = auth_headers(http, "admin")
    r = http.post("/api/companies/ruido/archive", json={"reason": "prueba"}, headers=h)
    assert r.status_code == 200 and r.json()["is_archived"] is True
    assert r.json()["archived_reason"] == "prueba"
    # Idempotente.
    assert http.post("/api/companies/ruido/archive", headers=h).status_code == 200
    with session_factory() as s:
        assert s.get(Company, "ruido").is_archived is True           # no borrada
    r = http.post("/api/companies/ruido/restore", headers=auth_headers(http, "user"))
    assert r.status_code == 200 and r.json()["is_archived"] is False
    assert r.json()["archived_at"] is None


def test_archivada_no_genera_incidencia_sin_vincular(session_factory) -> None:
    """Un pedido de una empresa archivada y sin CODCLI NO entra en incidencias."""
    with session_factory() as s:
        s.add(Company(id="arch", name="Archivada SL", is_archived=True, archived_at=NOW))
        s.add(Order(id="oa", order_number="MAN-A", company_id="arch",
                    external_source=OrderSource.MANUAL, total_amount=10.0, currency="EUR",
                    payment_status="paid", preparation_status="pending_review"))
        s.commit()
        wf = order_workflow(s, s.get(Order, "oa"))
    codes = [a["code"] for a in wf["alerts"]]
    assert "empresa_sin_vincular" not in codes
    assert wf["blocked"] is False and wf["next_action"] == "aprobar"
