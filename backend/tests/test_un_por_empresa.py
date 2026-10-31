"""Un contacto por empresa, el contacto principal, las columnas de la empresa
y el CSV (1/11/2026). Figurantes: ni personas, ni empresas, ni correos reales."""
from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.integrations.brevo.sync_targets import resolve_target_contacts
from app.models.brevo import BrevoSyncTarget
from app.models.crm import CallLog, Company, Contact, ContactView, Segment, User
from app.repositories import segments as segments_repository
from app.services import empresa_erp as ee
from app.services.segments import un_por_empresa as un
from app.services.segments.engine import build_filter
from tests._test_helpers import auth_headers
from tests.test_erp_historico import http, session_factory  # noqa: F401 — fixtures
from tests.test_erp_parque_consultas import AL_DIA, DORMIDO, HOY, HUECO_UV, _preparar

AHORA = datetime(2026, 10, 1, tzinfo=UTC)


def _empresa_con(s, nombre: str, **over) -> Company:
    e = Company(name=nombre, **over)
    s.add(e)
    s.flush()
    return e


def _contacto(s, empresa: Company | None, n: int, **over) -> Contact:
    c = Contact(first_name=f"Figurante {n}", email=over.pop("email", f"f{n}@ejemplo.test"),
                company_id=empresa.id if empresa else None, tags="",
                created_at=over.pop("alta", AHORA - timedelta(days=100 - n)), **over)
    s.add(c)
    s.flush()
    return c


def _ids(s, clausula: Any) -> set[str]:
    return set(s.scalars(select(Contact.id).where(clausula)))


def test_uno_por_empresa_y_el_orden_de_preferencia(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        a = _empresa_con(s, "Empresa figurante A")
        b = _empresa_con(s, "Empresa figurante B")
        viejo = _contacto(s, a, 1)                                  # el más antiguo
        sin_email = _contacto(s, a, 2, is_email_valid=False)
        llamado = _contacto(s, a, 3)
        s.add(CallLog(contact_id=llamado.id, user_id="u1", result_code="contacted",
                      called_at=AHORA))
        unico_b = _contacto(s, b, 4)
        suelto = _contacto(s, None, 5)
        s.commit()
        base = Contact.id.is_not(None)
        # Por defecto: nadie marcado, todos con email salvo uno → manda la
        # interacción más reciente.
        assert _ids(s, un.clausula(base)) == {llamado.id, unico_b.id}
        assert suelto.id not in _ids(s, un.clausula(base))        # sin empresa, fuera
        # El contacto principal marcado manda…
        sin_email.contacto_principal = True
        s.commit()
        assert _ids(s, un.clausula(base)) == {sin_email.id, unico_b.id}
        # …salvo que se quite ese criterio: entonces, el email válido y la interacción.
        orden = [un.EMAIL_VALIDO, un.INTERACCION, un.ANTIGUEDAD]
        assert _ids(s, un.clausula(base, orden)) == {llamado.id, unico_b.id}
        # Y con solo «el más antiguo».
        assert _ids(s, un.clausula(base, [un.ANTIGUEDAD])) == {viejo.id, unico_b.id}
        assert un.recuentos(s, base) == {"contactos": 5, "empresas": 2, "sin_empresa": 1}


def test_el_motivo_es_el_real(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        a = _empresa_con(s, "Empresa figurante A")
        b = _empresa_con(s, "Empresa figurante B")
        c = _empresa_con(s, "Empresa figurante C")
        _contacto(s, a, 1)
        llamado = _contacto(s, a, 2)
        s.add(CallLog(contact_id=llamado.id, user_id="u1", result_code="contacted",
                      called_at=AHORA))
        unico = _contacto(s, b, 3)
        viejo = _contacto(s, c, 4)
        _contacto(s, c, 5)
        s.commit()
        base = Contact.id.is_not(None)
        elegidos = list(_ids(s, un.clausula(base)))
        m = {k: v["texto"] for k, v in un.motivos(s, base, None, elegidos).items()}
        assert m[llamado.id] == "Interacción más reciente (01/10/2026)"
        assert m[unico.id] == "Único contacto de la empresa que cumple"
        assert m[viejo.id].startswith("El más antiguo")
        # Nadie está marcado: nunca se dice «principal».
        assert not any("principal" in t.lower() for t in m.values())


def test_la_lista_de_contactos_con_uno_por_empresa_y_las_columnas(http, session_factory) -> None:  # noqa: F811,E501
    _preparar(session_factory)
    with session_factory() as s:
        emp = {c.factusol_company_id: c for c in s.scalars(select(Company))}
        for n, cli in enumerate((DORMIDO, DORMIDO, AL_DIA, HUECO_UV)):
            _contacto(s, emp[cli], n)
        _contacto(s, None, 9)
        s.commit()
        dormido_id, dormido_nombre = emp[DORMIDO].id, emp[DORMIDO].name
        ee.recalcular(s, hoy=HOY)
    h = auth_headers(http, "admin")
    todos = http.post("/api/entities/contact/search", json={"limit": 50}, headers=h).json()
    assert todos["total"] == 5
    assert todos["recuentos"] == {"contactos": 5, "empresas": 3, "sin_empresa": 1}
    r = http.post("/api/entities/contact/search",
                  json={"limit": 50, "uno_por_empresa": True,
                        "sort_by": "compra_gasto_total", "sort_dir": "desc"}, headers=h).json()
    assert r["total"] == 3                                   # uno por empresa, sin el suelto
    assert all(i["contacto_elegido_por"] for i in r["items"])
    gastos = [i["compra_gasto_total"] for i in r["items"]]
    assert gastos == sorted(gastos, reverse=True)
    dormido = next(i for i in r["items"] if i["company_id"] == dormido_id)
    assert dormido["empresa_nombre"] == dormido_nombre
    assert dormido["parque_gamas"] and dormido["compra_dormido_gamas"]
    ids = http.post("/api/entities/contact/search/ids", json={"uno_por_empresa": True},
                    headers=h).json()
    assert ids["count"] == 3
    # Las columnas nuevas, en el selector de columnas.
    campos = {f["key"]: f for f in http.get("/api/entities/contact/filter-schema",
                                            headers=h).json()["fields"]}
    for clave in ("empresa_nombre", "parque_gamas", "compra_gasto_total",
                  "compra_hueco_gamas", "empresa_sector"):
        assert campos[clave]["displayable"], clave


def test_la_vista_lleva_su_uno_por_empresa_al_segmento_y_a_brevo(http, session_factory) -> None:  # noqa: F811,E501
    _preparar(session_factory)
    with session_factory() as s:
        emp = {c.factusol_company_id: c for c in s.scalars(select(Company))}
        con_email = _contacto(s, emp[DORMIDO], 1)
        sin_email = _contacto(s, emp[DORMIDO], 2, email=None)
        sin_email.contacto_principal = True                  # el preferido, sin email
        _contacto(s, emp[AL_DIA], 3)
        dueno = s.scalar(select(User.id).where(User.email == "admin@example.com"))
        reglas = {"operator": "AND", "children": [
            {"type": "rule", "field": "parque_gama", "comparator": "in", "value": ["laser"]}]}
        # Sin decirlo, una vista con reglas de empresa va «uno por empresa».
        vista = ContactView(name="Vista figurante", owner_user_id=dueno,
                            filters_json=json.dumps({"rules_json": reglas}))
        s.add(vista)
        s.commit()
        con_email_id, sin_email_id = con_email.id, sin_email.id
        ee.recalcular(s, hoy=HOY)
        vista_id = vista.id
    h = auth_headers(http, "admin")
    r = http.post(f"/api/contact-views/{vista_id}/save-as-segment",
                  json={"name": "Segmento figurante"}, headers=h)
    assert r.status_code in (200, 201), r.text
    with session_factory() as s:
        seg = s.scalar(select(Segment).where(Segment.name == "Segmento figurante"))
        assert seg.uno_por_empresa is True
        n, _t = segments_repository.evaluate_segment(s, seg)
        assert n == 2                                          # DORMIDO y AL_DIA, uno cada una
        # Brevo elige entre los que tienen email: va el siguiente, no nadie.
        target = BrevoSyncTarget(segment_id=seg.id, name="figurante", brevo_list_id="1")
        empujados = {c.id for c in resolve_target_contacts(s, target)}
        assert con_email_id in empujados and sin_email_id not in empujados
        assert len(empujados) == 2


def test_csv_con_las_columnas_elegidas(http, session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        emp = {c.factusol_company_id: c for c in s.scalars(select(Company))}
        a = _contacto(s, emp[DORMIDO], 1, email="uno@ejemplo.test")
        b = _contacto(s, emp[AL_DIA], 2, email="dos@ejemplo.test")
        a.first_name = "Figurante, con coma"
        s.commit()
        ee.recalcular(s, hoy=HOY)
        ids = [b.id, a.id]
    r = http.post("/api/contacts/bulk-export-csv",
                  json={"contact_ids": ids,
                        "columns": ["name", "email", "empresa_nombre", "compra_gasto_total",
                                    "parque_gamas", "no_existe"]},
                  headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    filas = list(csv.reader(io.StringIO(r.text)))
    assert filas[0] == ["Nombre completo", "Email", "Empresa", "Gasto total (€)",
                        "Gamas (parque)"]
    assert [f[1] for f in filas[1:]] == ["dos@ejemplo.test", "uno@ejemplo.test"]   # su orden
    assert filas[2][0].startswith("Figurante, con coma")     # la coma, entre comillas
    # Sin columnas, el CSV de siempre.
    r = http.post("/api/contacts/bulk-export-csv", json={"contact_ids": ids},
                  headers=auth_headers(http, "admin"))
    assert r.text.splitlines()[0].startswith("id,first_name,last_name,email")


def test_el_contacto_principal_es_uno_por_empresa(http, session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        a = _empresa_con(s, "Empresa figurante A")
        b = _empresa_con(s, "Empresa figurante B")
        c1, c2 = _contacto(s, a, 1), _contacto(s, a, 2)
        otro = _contacto(s, b, 3)
        s.commit()
        a_id, ids = a.id, (c1.id, c2.id, otro.id)
    h = auth_headers(http, "user")
    assert http.post(f"/api/companies/{a_id}/contacto-principal",
                     json={"contact_id": ids[0]}, headers=h).status_code == 200
    assert http.post(f"/api/companies/{a_id}/contacto-principal",
                     json={"contact_id": ids[1]}, headers=h).status_code == 200
    assert http.post(f"/api/companies/{a_id}/contacto-principal",
                     json={"contact_id": ids[2]}, headers=h).status_code == 422
    lista = http.get(f"/api/companies/{a_id}/contacts", headers=h).json()
    assert {c["id"]: c["contacto_principal"] for c in lista} == {ids[0]: False, ids[1]: True}
    assert http.post(f"/api/companies/{a_id}/contacto-principal", json={"contact_id": None},
                     headers=h).status_code == 200
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Contact).where(
            Contact.contacto_principal.is_(True))) == 0


def test_las_vistas_de_siempre_siguen_con_todos(session_factory) -> None:  # noqa: F811
    assert not un.tiene_reglas_de_empresa(
        {"operator": "AND", "children": [{"type": "rule", "field": "tags",
                                          "comparator": "contains_any", "value": ["x"]}]})
    assert un.tiene_reglas_de_empresa(
        {"operator": "OR", "children": [{"operator": "AND", "children": [
            {"type": "rule", "field": "compra_hueco", "comparator": "in", "value": ["laser"]}]}]})
    assert un.normaliza_orden(None) == list(un.ORDEN_DEFECTO)
    assert un.normaliza_orden("interaccion,raro,principal") == ["interaccion", "principal"]
    assert un.orden_a_texto(un.ORDEN_DEFECTO) is None


def test_el_cuadre_de_huecos(session_factory, monkeypatch, capsys) -> None:  # noqa: F811
    from scripts import cuadre_huecos

    _preparar(session_factory)
    with session_factory() as s:
        emp = {c.factusol_company_id: c for c in s.scalars(select(Company))}
        _contacto(s, emp[HUECO_UV], 1)
        s.commit()
        ee.recalcular(s, hoy=HOY)
    engine = session_factory.kw["bind"]
    monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
    assert cuadre_huecos.main(["--gama", "uv-led", "--meses", "12", "--detalle"]) == 0
    salida = capsys.readouterr().out
    assert "Pestaña «Dormidos y huecos»: 1 clientes de FACTUSOL" in salida
    assert "= contactos esperados, uno por empresa: 1" in salida
    assert "sin regla de antigüedad: 1 contactos" in salida
    assert "Figurante" not in salida
    # Un segmento sin «uno por empresa» cuenta como siempre.
    with session_factory() as s:
        seg = Segment(name="x", owner_user_id="u", rules_json=json.dumps({}))
        assert un.filtro_de_segmento(seg, build_filter({})) is not None
