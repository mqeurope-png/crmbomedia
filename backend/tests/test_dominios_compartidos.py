"""Los dominios que no emparejan y el despegue de lo que pegaron (1/11/2026).
Figurantes: ni personas, ni centros, ni correos reales (los dominios de las
consejerías sí: son la lista)."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select

from app.core.audit import Action, record_event
from app.erp import parque
from app.erp.models import CrmEmpresaErp
from app.erp.models.orders import Order
from app.integrations.brevo.jobs import resolve_brevo_company
from app.models.crm import Company, Contact, ContactTag, Tag
from app.models.dominios import DespegueDominio
from app.services import despegue_dominios as dd
from app.services import dominios_compartidos as dc
from app.services import empresa_erp as ee
from tests._test_helpers import auth_headers
from tests.test_erp_historico import http, session_factory  # noqa: F401 — fixtures
from tests.test_erp_parque_consultas import DORMIDO, HOY, _preparar

# --- la lista y el emparejamiento ---------------------------------------------------------


def test_un_dominio_de_la_lista_no_vincula_por_el_dominio(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        # Una empresa ya tiene ese dominio (así pegaba hasta hoy a todo el que lo usaba).
        s.add(Company(name="Empresa figurante", domain="xtec.cat"))
        s.commit()
        antes = s.scalar(select(func.count()).select_from(Company))
        assert resolve_brevo_company(s, {}, fallback_email="docente@xtec.cat") is None
        assert resolve_brevo_company(s, {}, fallback_email="docente@alumnes.xtec.cat") is None
        # Ni declarando una empresa con la web de la consejería y sin CIF: no
        # se inventa una «Centro público» nueva.
        assert resolve_brevo_company(
            s, {"EMPRESA": "Centro público", "WEB": "https://www.juntadeandalucia.es"},
            fallback_email="docente@juntadeandalucia.es") is None
        assert s.scalar(select(func.count()).select_from(Company)) == antes
        # gmail.com sigue como siempre: no empareja por dominio.
        assert resolve_brevo_company(s, {}, fallback_email="alguien@gmail.com") is None


def test_un_dominio_propio_sigue_vinculando_como_hoy(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        cid = resolve_brevo_company(s, {}, fallback_email="ana@taller-figurante.es")
        assert cid is not None
        assert s.get(Company, cid).domain == "taller-figurante.es"
        assert resolve_brevo_company(s, {}, fallback_email="luis@taller-figurante.es") == cid
        # Con EMPRESA y CIF, por el CIF, aunque el correo sea de la consejería.
        s.add(Company(name="Centro figurante con CIF", tax_id="Q0000000J"))
        s.commit()
        con_cif = s.scalar(select(Company.id).where(Company.tax_id == "Q0000000J"))
        assert resolve_brevo_company(s, {"EMPRESA": "Centro", "CIF": "Q0000000J"},
                                     fallback_email="docente@xtec.cat") == con_cif


def test_anadir_un_dominio_no_requiere_tocar_codigo(http, session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        s.add(Company(name="Empresa figurante", domain="educa.ejemplo-region.es"))
        s.commit()
        assert resolve_brevo_company(s, {}, fallback_email="d@educa.ejemplo-region.es")
    h = auth_headers(http, "admin")
    assert http.post("/api/admin/dominios-compartidos", json={"dominio": "x"},
                     headers=h).status_code == 422
    assert http.post("/api/admin/dominios-compartidos", json={"dominio": "a.xtec.cat"},
                     headers=h).status_code == 422          # ya lo cubre xtec.cat
    r = http.post("/api/admin/dominios-compartidos",
                  json={"dominio": "@Educa.Ejemplo-Region.es", "nota": "figurante"}, headers=h)
    assert r.status_code == 201 and r.json()["dominio"] == "educa.ejemplo-region.es"
    with session_factory() as s:
        assert resolve_brevo_company(s, {}, fallback_email="d@educa.ejemplo-region.es") is None
    filas = http.get("/api/admin/dominios-compartidos", headers=h).json()
    assert {"xtec.cat", "gmail.com", "educa.ejemplo-region.es"} <= {f["dominio"] for f in filas}
    assert http.get("/api/admin/dominios-compartidos",
                    headers=auth_headers(http, "user")).status_code == 403
    assert http.delete("/api/admin/dominios-compartidos/educa.ejemplo-region.es",
                       headers=h).status_code == 204
    with session_factory() as s:
        assert resolve_brevo_company(s, {}, fallback_email="d@educa.ejemplo-region.es")


# --- el despegue ------------------------------------------------------------------------------


def _escenario(session_factory) -> dict[str, Any]:  # noqa: F811
    """La empresa DORMIDO (cliente de FACTUSOL, con parque e histórico) lleva
    el dominio de la consejería y le cuelgan docentes; dos «Centro figurante»
    genéricas, sin cliente, con el dominio de otra consejería."""
    _preparar(session_factory)
    with session_factory() as s:
        cliente = s.scalar(select(Company).where(Company.factusol_company_id == DORMIDO))
        cliente.domain = "xtec.cat"
        generica = Company(name="Centro figurante", domain="juntadeandalucia.es")
        con_pedido = Company(name="Centro figurante", domain="edu.xunta.gal")
        propia = Company(name="Taller figurante", domain="taller-figurante.es")
        s.add_all([generica, con_pedido, propia])
        s.flush()

        def contacto(nombre: str, email: str, company: Company, **over) -> str:
            c = Contact(first_name=nombre, email=email, company_id=company.id, tags="", **over)
            s.add(c)
            s.flush()
            return c.id

        ids = {
            "comprador": contacto("Figurante 1", "f1@xtec.cat", cliente,
                                  factusol_contact_id=DORMIDO),
            "pedido": contacto("Figurante 2", "f2@xtec.cat", cliente),
            "a_mano": contacto("Figurante 3", "f3@xtec.cat", cliente,
                               manually_edited_fields_json=json.dumps(["company_id"])),
            "auditado": contacto("Figurante 4", "f4@xtec.cat", cliente),
            "propio": contacto("Figurante 5", "f5@cliente-figurante.cat", cliente),
            "gmail": contacto("Figurante 6", "f6@gmail.com", propia),
            "taller": contacto("Figurante 7", "f7@taller-figurante.es", propia),
            "docentes": [contacto(f"Docente {i}", f"d{i}@xtec.cat", cliente) for i in range(6)],
            "genericos": [contacto(f"Genérico {i}", f"g{i}@juntadeandalucia.es", generica)
                          for i in range(3)],
            "xunta": [contacto(f"Xunta {i}", f"x{i}@edu.xunta.gal", con_pedido)
                      for i in range(2)],
        }
        s.add(Order(order_number="M-1", external_source="manual", company_id=cliente.id,
                    contact_id=ids["pedido"], total_amount=10))
        s.add(Order(order_number="M-2", external_source="manual", company_id=con_pedido.id,
                    total_amount=10))
        record_event(s, action=Action.CONTACT_UPDATED, target_type="contact",
                     target_id=ids["auditado"],
                     metadata={"field": "company_id", "from": None, "to": cliente.id})
        s.commit()
        return {"ids": ids, "cliente": cliente.id, "generica": generica.id,
                "con_pedido": con_pedido.id, "propia": propia.id}


def test_el_plan_despega_solo_lo_que_no_tiene_prueba(session_factory) -> None:  # noqa: F811
    esc = _escenario(session_factory)
    ids = esc["ids"]
    with session_factory() as s:
        p = dd.plan(s)
        por = {e.company_id: e for e in p.empresas}
        cli = por[esc["cliente"]]
        assert {c for c, _d in cli.despegar} == set(ids["docentes"])
        assert dict(cli.quedan) == {"factusol": 1, "pedido": 1, "a_mano": 2}
        assert not cli.archivar                     # tiene cliente de FACTUSOL
        assert por[esc["generica"]].archivar        # vacía, sin cliente, sin negocio
        assert not por[esc["con_pedido"]].archivar  # tiene un pedido: no se archiva
        assert esc["propia"] not in por             # dominio propio y gmail: no se tocan
        assert p.a_despegar == 6 + 3 + 2
        assert p.por_dominio == {"xtec.cat": 6, "juntadeandalucia.es": 3, "edu.xunta.gal": 2}
        # El plan solo lee.
        assert s.scalar(select(func.count()).select_from(DespegueDominio)) == 0


def test_despegar_y_revertir(session_factory) -> None:  # noqa: F811
    esc = _escenario(session_factory)
    ids = esc["ids"]
    with session_factory() as s:
        lote = dd.aplicar(s, dd.plan(s), por="test")
    with session_factory() as s:
        for cid in ids["docentes"] + ids["genericos"]:
            c = s.get(Contact, cid)
            assert c.company_id is None and c.is_active      # ni borrado ni desactivado
            ap = s.scalar(select(DespegueDominio).where(DespegueDominio.objeto_id == cid))
            assert ap.lote == lote and ap.motivo == dd.MOTIVO
            assert ap.empresa_anterior in (esc["cliente"], esc["generica"])
        for clave in ("comprador", "pedido", "a_mano", "auditado", "propio"):
            assert s.get(Contact, ids[clave]).company_id == esc["cliente"], clave
        assert s.get(Contact, ids["gmail"]).company_id == esc["propia"]
        assert s.get(Company, esc["generica"]).is_archived is True
        assert s.get(Company, esc["cliente"]).is_archived is False
        assert s.get(Company, esc["con_pedido"]).is_archived is False
        # Un vínculo puesto después a mano no lo pisa la reversión.
        s.get(Contact, ids["docentes"][0]).company_id = esc["propia"]
        s.commit()
        r = dd.revertir(s, lote, por="test")
        assert r == {"contactos": 10, "empresas": 1, "con_otra_empresa": 1,
                     "sin_empresa_anterior": 0}
    with session_factory() as s:
        for cid in ids["docentes"][1:]:
            assert s.get(Contact, cid).company_id == esc["cliente"]
        for cid in ids["genericos"]:
            assert s.get(Contact, cid).company_id == esc["generica"]
        assert s.get(Contact, ids["docentes"][0]).company_id == esc["propia"]
        assert s.get(Company, esc["generica"]).is_archived is False
        assert dd.lotes(s)[0]["lote"] == lote


def test_el_cliente_conserva_vinculo_parque_e_historico(session_factory) -> None:  # noqa: F811
    esc = _escenario(session_factory)
    with session_factory() as s:
        ee.recalcular(s, hoy=HOY)
        antes = s.get(CrmEmpresaErp, esc["cliente"])
        antes = (antes.codcli, antes.maquinas, antes.gasto_total, antes.ultima_compra)
        empresa = s.get(Company, esc["cliente"])
        maquinas = [m["clave"] for m in parque.parque(s, empresa)["maquinas"]]
        dd.aplicar(s, dd.plan(s), por="test")
        ee.recalcular(s, hoy=HOY)
    with session_factory() as s:
        empresa = s.get(Company, esc["cliente"])
        assert empresa.factusol_company_id == DORMIDO and not empresa.is_archived
        fila = s.get(CrmEmpresaErp, esc["cliente"])
        assert (fila.codcli, fila.maquinas, fila.gasto_total, fila.ultima_compra) == antes
        assert [m["clave"] for m in parque.parque(s, empresa)["maquinas"]] == maquinas


def test_tras_despegar_y_recalcular_sin_empresa_no_es_ya_cliente(session_factory) -> None:  # noqa: F811,E501
    esc = _escenario(session_factory)
    docente = esc["ids"]["docentes"][1]
    with session_factory() as s:
        ee.recalcular(s, hoy=HOY)
        tag_id = s.scalar(select(Tag.id).where(Tag.name_normalized == "ya cliente"))
        assert s.get(ContactTag, (docente, tag_id)) is not None   # hoy, contaminada
        antes = dd.ya_cliente(s)["contactos"]
        p = dd.plan(s)
        # Los 6 docentes del cliente y los 2 de la genérica con un pedido (que
        # por el pedido es YA CLIENTE).
        assert sum(x["ya_cliente_auto"] for x in dd.ya_cliente_por_empresa(s, p)) == 8
        dd.aplicar(s, p, por="test")
        ee.recalcular(s, hoy=HOY)
    with session_factory() as s:
        assert s.get(ContactTag, (docente, tag_id)) is None
        assert dd.ya_cliente(s)["contactos"] == antes - 8
        # Los que se quedan con su empresa la conservan.
        assert s.get(ContactTag, (esc["ids"]["comprador"], tag_id)) is not None


def test_las_vistas_que_miran_la_empresa_avisan_del_cambio(session_factory) -> None:  # noqa: F811
    from app.models.crm import Segment, User

    esc = _escenario(session_factory)
    with session_factory() as s:
        ee.recalcular(s, hoy=HOY)
        dueno = s.scalar(select(User.id).limit(1))
        s.add(Segment(name="Huecos de láser figurante", owner_user_id=dueno, rules_json=json.dumps(
            {"operator": "AND", "children": [{"type": "rule", "field": "parque_gama",
                                              "comparator": "in", "value": ["laser"]}]})))
        s.commit()
        p = dd.plan(s)
        riesgo = {x["nombre"]: x for x in dd.en_riesgo(s, p)}
        assert riesgo["Huecos de láser figurante"]["se_despegan"] == 6
        antes = dd.tamanos(s)
        dd.aplicar(s, p, por="test")
        cambios = dd.cambios_de_tamano(antes, dd.tamanos(s), minimo=1)
    assert [(c["nombre"], c["despues"] - c["antes"]) for c in cambios] == [
        ("Huecos de láser figurante", -6)]
    assert esc


def test_los_scripts_prueban_sin_escribir_y_aplican_con_yes(session_factory, monkeypatch,  # noqa: F811
                                                            capsys) -> None:
    from scripts import despegar_dominios_compartidos as script
    from scripts import diagnostico_dominios

    esc = _escenario(session_factory)
    engine = session_factory.kw["bind"]
    monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
    # Sin FACTUSOL en los tests: si no se puede leer F_CLI, el script se para.
    def sin_factusol(_s):
        raise ConnectionError("sin FACTUSOL")
    monkeypatch.setattr(dd, "correos_de_factusol", sin_factusol)
    assert script.main(["--probar"]) == 3
    # F_CLI vacío (p. ej. ejercicio equivocado) tampoco vale.
    monkeypatch.setattr(dd, "correos_de_factusol", lambda _s: {})
    assert script.main(["--probar"]) == 3
    capsys.readouterr()
    monkeypatch.setattr(dd, "correos_de_factusol", lambda _s: {"9999": {"otro@ejemplo.test"}})
    assert diagnostico_dominios.main([]) == 0
    salida = capsys.readouterr().out
    assert "xtec.cat" in salida and "Figurante" not in salida and "Centro" not in salida
    assert script.main(["--probar"]) == 0
    salida = capsys.readouterr().out
    assert "Se despegan: 11" in salida and "Figurante" not in salida
    with session_factory() as s:
        assert s.get(Contact, esc["ids"]["docentes"][0]).company_id == esc["cliente"]
    assert script.main(["--apply"]) == 2                       # sin --yes, nada
    assert script.main(["--apply", "--yes"]) == 0
    with session_factory() as s:
        assert s.get(Contact, esc["ids"]["docentes"][0]).company_id is None
        lote = dd.lotes(s)[0]["lote"]
    assert script.main(["--revertir", lote, "--yes"]) == 0
    with session_factory() as s:
        assert s.get(Contact, esc["ids"]["docentes"][0]).company_id == esc["cliente"]


def test_la_lista_cubre_los_subdominios_y_se_lee_de_la_tabla(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        lista = dc.cargar(s)
        assert lista.de_la_lista("ALUMNES.xtec.cat") == "xtec.cat"
        assert lista.compartido("xtec.cat") and not lista.compartido("gmail.com")
        assert lista.tipo("gmail.com") == "correo"
        assert not lista.contiene("xtec.cat.ejemplo.es")
        dc.quitar(s, "xtec.cat")
        assert not dc.cargar(s).contiene("xtec.cat")


# --- revisión (1/11/2026) -------------------------------------------------------------------


def test_lo_que_concilio_factusol_por_correo_se_queda(session_factory) -> None:  # noqa: F811
    """La conciliación por correo llevó docentes de @xtec.cat a SU centro de
    FACTUSOL: no se despegan. Por su apunte, o porque su correo es el del
    cliente en F_CLI (cuando la conciliación no dejó apunte)."""
    from app.models.crm import AuditLog

    esc = _escenario(session_factory)
    d1, d2, d3 = esc["ids"]["docentes"][:3]
    with session_factory() as s:
        centro = Company(name="Centro figurante FACTUSOL", factusol_company_id="4790")
        s.add(centro)
        s.flush()
        for d in (d1, d2, d3):
            s.get(Contact, d).company_id = centro.id
        s.get(Contact, d1).email = "centro4790@xtec.cat"
        s.add(AuditLog(action="erp.factusol_bulk_sync_by_email_reassign", target_type="contact",
                       target_id=d2, metadata_json=json.dumps({"new_company_id": centro.id})))
        s.add(Contact(first_name="Alta a mano", email="alta@xtec.cat", company_id=centro.id,
                      origin="Manual", tags=""))
        s.commit()
        p = dd.plan(s, correos_factusol={"4790": {"centro4790@xtec.cat"}})
        fila = next(e for e in p.empresas if e.company_id == centro.id)
        assert dict(fila.quedan) == {"correo_factusol": 1, "conciliado": 1, "alta_manual": 1}
        assert [c for c, _d in fila.despegar] == [d3]
        assert p.con_factusol


def test_una_empresa_con_cif_o_maquinas_a_mano_no_se_archiva(session_factory) -> None:  # noqa: F811,E501
    from app.erp.models.historico import ParqueMaquinaManual

    esc = _escenario(session_factory)
    with session_factory() as s:
        s.get(Company, esc["generica"]).tax_id = "Q0000001J"
        otra = Company(name="Centro figurante", domain="educa.madrid.org")
        s.add(otra)
        s.flush()
        s.add(Contact(first_name="Figurante", email="m@educa.madrid.org", company_id=otra.id,
                      tags=""))
        s.add(ParqueMaquinaManual(company_id=otra.id, codart="L1", descripcion="Figurante",
                                  gama="laser"))
        s.commit()
        por = {e.company_id: e for e in dd.plan(s).empresas}
        assert len(por[esc["generica"]].despegar) == 3 and not por[esc["generica"]].archivar
        assert len(por[otra.id].despegar) == 1 and not por[otra.id].archivar


def test_revertir_no_desarchiva_lo_archivado_por_otro_motivo(session_factory) -> None:  # noqa: F811
    esc = _escenario(session_factory)
    with session_factory() as s:
        lote = dd.aplicar(s, dd.plan(s), por="test")
        empresa = s.get(Company, esc["generica"])
        empresa.archived_reason = "Archivada a mano por otro motivo"
        s.commit()
        r = dd.revertir(s, lote, por="test")
        assert r["empresas"] == 0
        assert s.get(Company, esc["generica"]).is_archived is True


def test_un_cambio_entre_el_plan_y_aplicar_no_se_pisa(session_factory) -> None:  # noqa: F811
    esc = _escenario(session_factory)
    movido = esc["ids"]["docentes"][0]
    with session_factory() as s:
        p = dd.plan(s)
    with session_factory() as otra:
        otra.get(Contact, movido).company_id = esc["propia"]
        otra.commit()
    with session_factory() as s:
        dd.aplicar(s, p, por="test")
        assert s.get(Contact, movido).company_id == esc["propia"]
        assert s.scalar(select(DespegueDominio).where(DespegueDominio.objeto_id == movido)) is None


def test_brevo_con_web_de_la_lista_no_inventa_empresas(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        antes = s.scalar(select(func.count()).select_from(Company))
        for _ in range(3):
            assert resolve_brevo_company(s, {"EMPRESA": "Taller", "WEB": "gmail.com"},
                                         fallback_email="taller@gmail.com") is None
        assert s.scalar(select(func.count()).select_from(Company)) == antes
        # Correo propio + web de la consejería + EMPRESA: por el dominio del correo.
        propia = resolve_brevo_company(s, {"EMPRESA": "Taller", "WEB": "xtec.cat"},
                                       fallback_email="ana@taller-figurante.es")
        assert s.get(Company, propia).domain == "taller-figurante.es"
        # Web propia y correo de la consejería: como siempre, por la web.
        web = resolve_brevo_company(s, {"EMPRESA": "Instituto figurante",
                                        "WEB": "https://instituto-figurante.cat"},
                                    fallback_email="d@xtec.cat")
        assert s.get(Company, web).domain == "instituto-figurante.cat"
        assert resolve_brevo_company(s, {"EMPRESA": "Instituto figurante",
                                         "WEB": "instituto-figurante.cat"},
                                     fallback_email="d2@xtec.cat") == web
        # Con un CIF nuevo se crea la empresa (el CIF la identifica), sin el dominio.
        nueva = resolve_brevo_company(s, {"EMPRESA": "Centro", "CIF": "Q0000002J",
                                          "WEB": "xtec.cat"}, fallback_email="d@xtec.cat")
        c = s.get(Company, nueva)
        assert c.tax_id == "Q0000002J" and c.domain is None
        assert resolve_brevo_company(s, {"EMPRESA": "Centro", "CIF": "Q0000002J"},
                                     fallback_email="d2@xtec.cat") == nueva


def test_las_vistas_de_la_pantalla_se_miden(session_factory) -> None:  # noqa: F811
    from app.models.crm import ContactView, User

    _escenario(session_factory)
    with session_factory() as s:
        ee.recalcular(s, hoy=HOY)
        dueno = s.scalar(select(User.id).limit(1))
        s.add(ContactView(name="Vista láser figurante", owner_user_id=dueno,
                          filters_json=json.dumps({"rules_json": {
                              "operator": "AND", "children": [
                                  {"type": "rule", "field": "parque_gama", "comparator": "in",
                                   "value": ["laser"]}]}})))
        s.commit()
        riesgo = {x["nombre"]: x for x in dd.en_riesgo(s, dd.plan(s))}
        assert riesgo["Vista láser figurante"]["se_despegan"] == 6


def test_el_alta_manual_con_empresa_queda_marcada(http, session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        cid = s.scalar(select(Company.id).limit(1))
    r = http.post("/api/contacts", json={"first_name": "Figurante", "email": "alta@xtec.cat",
                                         "company_id": cid},
                  headers=auth_headers(http, "user"))
    assert r.status_code in (200, 201), r.text
    with session_factory() as s:
        c = s.scalar(select(Contact).where(Contact.email == "alta@xtec.cat"))
        assert "company_id" in json.loads(c.manually_edited_fields_json)


def test_f_cli_con_codcli_numerico(session_factory, monkeypatch) -> None:  # noqa: F811
    """FACTUSOL da el CODCLI como número; la empresa lo guarda como texto (a
    veces con ceros). Tienen que cruzar."""
    class ClienteFalso:
        def load_table(self, tabla, **_kw):
            assert tabla == "F_CLI"
            return [{"CODCLI": 4790, "EMACLI": "Centro4790@xtec.cat; otra@ejemplo.test"},
                    {"CODCLI": 4791, "EMACLI": ""}]

    monkeypatch.setattr("app.integrations.factusol.client.FactusolClient.from_settings",
                        classmethod(lambda cls: ClienteFalso()))
    monkeypatch.setattr("app.integrations.factusol.service.ejercicio_for", lambda _s: "2026")
    with session_factory() as s:
        correos = dd.correos_de_factusol(s)
        assert correos == {"4790": {"centro4790@xtec.cat", "otra@ejemplo.test"}}
        centro = Company(name="Centro figurante", factusol_company_id="04790")
        s.add(centro)
        s.flush()
        s.add(Contact(first_name="Figurante", email="centro4790@xtec.cat", company_id=centro.id,
                      tags=""))
        s.commit()
        fila = next(e for e in dd.plan(s, correos_factusol=correos).empresas
                    if e.company_id == centro.id)
        assert dict(fila.quedan) == {"correo_factusol": 1} and not fila.despegar
