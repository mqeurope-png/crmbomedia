"""Discovery (solo lectura) de empresas CRM vs FACTUSOL.

Cruce por NIF/VAT normalizado (mayúsculas, sin separadores, sin prefijo de
país), duplicados del CRM, actividad viva por empresa, la tabla clave
«¿en FACTUSOL? × ¿actividad?» y el tamaño del auto-vincular. Sin red: F_CLI y
F_PRE van simulados; y NADA se escribe (se comprueba).
"""
from __future__ import annotations

import csv
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.erp.company_discovery import (
    build_report,
    format_report,
    name_similarity,
    nif_key,
    nif_looks_malformed,
    write_csv,
)
from app.erp.models import Order, OrderSource
from app.models.crm import ActivityEvent, Company, Contact, EmailMessage, EmailThread, Task
from tests._test_helpers import seed_test_users

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)

F_CLI = [
    {"CODCLI": 3734, "NIFCLI": "J70876677", "NOFCLI": "ONLYGUAY S C", "NOCCLI": ""},
    {"CODCLI": 2629, "NIFCLI": "PT503420506", "NOFCLI": "EXATRONIC LDA", "NOCCLI": ""},
    # Duplicado del propio FACTUSOL (mismo NIF, sin prefijo):
    {"CODCLI": 2819, "NIFCLI": "503420506", "NOFCLI": "EXATRONIC LDA", "NOCCLI": ""},
    {"CODCLI": 2458, "NIFCLI": "B-12.345.678", "NOFCLI": "DUPLICODER, S.L.", "NOCCLI": ""},
    {"CODCLI": 4100, "NIFCLI": "FR16339753527", "NOFCLI": "LA MAISON DE LA PLAQUE", "NOCCLI": ""},
    {"CODCLI": 4200, "NIFCLI": "B99999999", "NOFCLI": "TOTALMENTE OTRA COSA SA", "NOCCLI": ""},
    {"CODCLI": 4300, "NIFCLI": "", "NOFCLI": "SIN NIF EN FACTUSOL", "NOCCLI": ""},
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
            # Casa por CIF aunque el nombre difiera; sin vincular → auto-vincular.
            Company(id="onlyguay", name="ONLYGUAY SC", tax_id="J70876677", country="ES"),
            # VAT con prefijo casa con NIFCLI con y sin prefijo (dos CODCLI): vinculada a uno.
            Company(id="exatronic", name="Exatronic Lda", vat="PT503420506", country="PT",
                    factusol_company_id="2629"),
            # Duplicado en el CRM del anterior (mismo NIF, otra ficha), vinculada al otro CODCLI.
            Company(id="exatronic2", name="Exatronic Lda", tax_id="pt 503.420.506", country="PT",
                    factusol_company_id="2819"),
            # Vinculada y OK, con pedido por facturar.
            Company(id="dupli", name="Duplicoder SL", tax_id="b12345678", country="ES",
                    factusol_company_id="2458"),
            # Casa (4100) pero está vinculada a OTRO CODCLI existente (4200) → ambigua.
            Company(id="maison", name="La Maison de la Plaque", vat="FR16339753527",
                    country="FR", factusol_company_id="4200"),
            # Casa por NIF pero el nombre no se parece en nada → ambigua.
            Company(id="rara", name="Zeta Beta Gamma", tax_id="B99999999", country="ES"),
            # NO en FACTUSOL, con actividad (tarea abierta) → proteger.
            Company(id="viva", name="Viva SL", tax_id="B11111111", country="ES"),
            # NO en FACTUSOL, solo con un contacto → proteger (any) pero no estricta.
            Company(id="solocontacto", name="Solo Contacto SL", tax_id="B22222222"),
            # NO en FACTUSOL y sin nada → candidata a archivar.
            Company(id="ruido", name="Ruido SL", tax_id="B33333333"),
            # Sin NIF ni VAT: no cruzable.
            Company(id="sinnif", name="Sin NIF SL"),
            # NIF mal formado.
            Company(id="malo", name="NIF Raro SL", tax_id="12"),
            # Vinculada a un CODCLI que no existe, sin NIF.
            Company(id="fantasma", name="Fantasma SL", factusol_company_id="777777"),
        ])
        s.add_all([
            Contact(id="c1", first_name="Ana", email="ana@solo.es", company_id="solocontacto"),
            Contact(id="c2", first_name="Bea", email="bea@dupli.es", company_id="dupli"),
            Contact(id="c3", first_name="Cai", email="cai@viva.es", company_id="viva"),
        ])
        s.add_all([
            Order(id="o1", order_number="MAN-1", company_id="dupli",
                  external_source=OrderSource.MANUAL, total_amount=100.0, currency="EUR",
                  payment_status="paid", preparation_status="in_queue",
                  approved_at=NOW - timedelta(days=2)),                       # por facturar
            Order(id="o2", order_number="MAN-2", company_id="dupli",
                  external_source=OrderSource.MANUAL, total_amount=50.0, currency="EUR",
                  payment_status="paid", preparation_status="packed",
                  invoice_status="invoiced_by_erp", factusol_invoice_number="260001",
                  completed_at=NOW - timedelta(days=1)),          # facturado + completado
            Order(id="o3", order_number="MAN-3", company_id="onlyguay",
                  external_source=OrderSource.MANUAL, total_amount=10.0, currency="EUR",
                  payment_status="pending", preparation_status="pending_review",
                  seguimiento_excluded_at=NOW - timedelta(days=3)),           # oculto
        ])
        s.add_all([
            Task(id="t1", title="Llamar", status="pending", assigned_user_id=_user(s),
                 created_by_user_id=_user(s), company_id="viva"),
            Task(id="t2", title="Hecha", status="done", assigned_user_id=_user(s),
                 created_by_user_id=_user(s), contact_id="c2"),
        ])
        s.add(EmailThread(id="th1", contact_id="c2", initiated_by_user_id=_user(s),
                          gmail_thread_id="g1", gmail_account_user_id=_user(s),
                          first_message_at=NOW - timedelta(days=10),
                          last_message_at=NOW - timedelta(days=10)))
        s.add(EmailMessage(id="m1", thread_id="th1", gmail_account_user_id=_user(s),
                           direction="outbound", from_email="a@b.es", to_emails_json="[]",
                           sent_at=NOW - timedelta(days=10), contact_id="c2"))
        s.add(ActivityEvent(id="ev1", contact_id="c3", system="brevo", account_id="acc",
                            event_type="email_opened", occurred_at=NOW - timedelta(days=400),
                            synced_at=NOW))
        s.commit()
    yield factory
    Base.metadata.drop_all(engine)


def _user(s: Session) -> str:
    from app.models.crm import User  # noqa: PLC0415

    return s.scalar(select(User.id).order_by(User.email.asc()).limit(1))


# --- normalización ----------------------------------------------------------------------


def test_nif_key_normaliza_y_quita_prefijo_de_pais() -> None:
    assert nif_key("j-70.876 677") == "J70876677"
    assert nif_key("ESB12345678") == "B12345678"
    assert nif_key("B12345678") == "B12345678"
    assert nif_key("PT 503.420.506") == "503420506"
    assert nif_key("503420506") == "503420506"
    assert nif_key("FR16339753527") == "16339753527"
    assert nif_key("NL123456789B01") == "123456789B01"
    assert nif_key("EL123456789") == "123456789"                 # Grecia
    assert nif_key("X1234567L") == "X1234567L"                    # NIE: X1 no es prefijo
    assert nif_key("") is None and nif_key(None) is None
    assert nif_looks_malformed("12") and nif_looks_malformed("ABCDEF")
    assert not nif_looks_malformed("J70876677")
    assert name_similarity("ONLYGUAY SC", "ONLYGUAY S C") == 1.0
    assert name_similarity("Zeta Beta Gamma", "TOTALMENTE OTRA COSA SA") < 0.45


# --- el informe ----------------------------------------------------------------------------


def test_cruce_y_recuentos(session_factory, tmp_path) -> None:
    with session_factory() as s:
        report = build_report(s, F_CLI, proformas_by_codcli={"3734": 2, "2629": 1}, now=NOW)
        # Nada escrito: ninguna empresa cambia.
        assert s.scalar(select(Company.factusol_company_id).where(Company.id == "onlyguay")) is None
    c = report.counts
    by = {r.company_id: r for r in report.rows}

    # 1-3
    assert c["total"] == 12 and c["factusol_clientes"] == 7
    assert c["sin_nif"] == 2                                         # sinnif, fantasma
    assert c["casan"] == 6              # onlyguay, exatronic×2, dupli, maison, rara
    assert c["no_casan"] == 4           # viva, solocontacto, ruido, malo
    assert by["onlyguay"].factusol_codclis == ["3734"]
    assert by["onlyguay"].factusol_nombre == "ONLYGUAY S C"
    assert by["onlyguay"].name_similarity == 1.0
    assert set(by["exatronic"].factusol_codclis) == {"2629", "2819"}
    assert c["casan_con_varios_codcli"] == 2
    # 4 duplicados del CRM
    assert c["duplicados_grupos"] == 1 and c["duplicados_fichas"] == 2
    assert by["exatronic"].crm_duplicate_group == "503420506"
    # 5 actividad
    d = by["dupli"]
    assert d.orders_total == 2 and d.orders_por_facturar == 1 and d.orders_open == 1
    assert d.orders_invoiced == 1 and d.orders_completed == 1
    assert d.contacts == 1 and d.tasks_total == 1 and d.tasks_open == 0
    assert d.last_email_at is not None and d.alive_strict is True
    assert by["onlyguay"].orders_hidden == 1 and by["onlyguay"].orders_open == 0
    assert by["onlyguay"].proformas == 2 and by["exatronic"].proformas == 1
    assert by["viva"].tasks_open == 1 and by["viva"].alive_strict is True
    assert by["viva"].last_activity_at is not None                  # viejo: no cuenta como reciente
    assert by["solocontacto"].alive_any is True and by["solocontacto"].alive_strict is False
    assert by["ruido"].alive_any is False
    assert c["con_pedidos"] == 2 and c["con_pedidos_por_facturar"] == 1
    # exatronic2 hereda la proforma del 2629 (casa por NIF con los dos CODCLI).
    assert by["exatronic2"].proformas == 1
    assert c["con_proformas"] == 3 and c["con_tareas_abiertas"] == 1 and c["con_contactos"] == 3
    assert c["solo_contactos"] == 1
    # 6 cruce
    x = c["cruce"]
    assert by["onlyguay"].classification == "en_factusol"
    assert by["viva"].classification == "proteger"
    assert by["solocontacto"].classification == "proteger"
    assert by["ruido"].classification == "candidata_archivar"
    assert by["malo"].classification == "candidata_archivar"
    assert by["sinnif"].classification == "sin_nif_sin_actividad"
    assert by["fantasma"].classification == "sin_nif_sin_actividad"
    assert x["en_factusol"] == 6 and x["no_factusol_con_actividad"] == 2
    assert x["no_factusol_sin_actividad"] == 2 and x["sin_nif_sin_actividad"] == 2
    assert x["pedidos_por_facturar_en_proteger"] == 0
    assert c["cruce_estricto"] == {"no_factusol_con_actividad": 1, "no_factusol_sin_actividad": 3}
    # 7 vínculo
    assert c["casan_sin_vincular"] == 2                               # onlyguay, rara
    assert by["onlyguay"].link_status == "sin_vincular_casa"
    assert by["dupli"].link_status == "vinculada_ok"
    assert by["exatronic"].link_status == "vinculada_ok"
    assert by["exatronic2"].link_status == "vinculada_ok"
    assert by["maison"].link_status == "vinculada_otro_codcli"
    assert by["fantasma"].link_status == "vinculada_codcli_inexistente"
    assert by["ruido"].link_status == "sin_vincular_no_casa"
    assert by["sinnif"].link_status == "sin_vincular_sin_nif"
    # Ambiguos: varios CODCLI, nombre muy distinto, vinculada a otro CODCLI, NIF raro, dup CRM.
    amb = {a["company_id"]: a for a in report.ambiguous}
    assert "exatronic" in amb and any("varios CODCLI" in n for n in amb["exatronic"]["notes"])
    assert any("NIF repetido" in n for n in amb["exatronic"]["notes"])
    assert any("nombre muy distinto" in n for n in amb["rara"]["notes"])
    assert any("vinculada a 4200 pero el NIF casa con 4100" in n for n in amb["maison"]["notes"])
    assert any("mal formado" in n for n in amb["malo"]["notes"])
    assert any("no existe en F_CLI" in n for n in amb["fantasma"]["notes"])
    assert "onlyguay" not in amb

    # Texto e CSV.
    text = format_report(report)
    assert "1. Empresas en el CRM: 12" in text
    assert "Casan con FACTUSOL por NIF/VAT normalizado: 6" in text
    assert "candidatas a archivar): 2" in text
    assert "auto-vincular por NIF): 2" in text
    assert "ONLYGUAY" not in text.split("Casos ambiguos")[1]
    path = write_csv(report, tmp_path / "out" / "empresas.csv")
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    assert len(rows) == 12
    only = next(r for r in rows if r["company_id"] == "onlyguay")
    assert only["factusol_codclis"] == "3734" and only["classification"] == "en_factusol"
    assert only["link_status"] == "sin_vincular_casa" and only["proformas"] == "2"
    exa = next(r for r in rows if r["company_id"] == "exatronic")
    assert exa["factusol_codclis"] in ("2629|2819", "2819|2629") and "varios CODCLI" in exa["notes"]


def test_no_escribe_nada(session_factory) -> None:
    """Solo lectura de verdad: ni INSERT ni UPDATE ni DELETE durante el informe."""
    writes: list[str] = []

    with session_factory() as s:
        @event.listens_for(s.get_bind(), "before_cursor_execute")
        def _watch(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            verb = statement.strip().split(" ", 1)[0].upper()
            if verb in ("INSERT", "UPDATE", "DELETE"):
                writes.append(statement[:60])

        build_report(s, F_CLI, proformas_by_codcli={}, now=NOW)
        s.commit()
    assert writes == []
