"""Al despegar, el dominio de la lista se quita también de la empresa
(1/11/2026), y se deshace con el lote. Figurantes: ni personas ni centros
reales; los dominios sí son los de la lista."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from app.models.crm import Company, Contact
from app.models.dominios import DespegueDominio
from app.services import despegue_dominios as dd
from tests._test_helpers import auth_headers
from tests.test_dominios_compartidos import _escenario
from tests.test_erp_historico import http, session_factory  # noqa: F401 — fixtures

LOTE_VIEJO = "202611010454-ac9c89"


def test_al_despegar_se_limpia_el_dominio_y_se_deshace_con_el_lote(session_factory) -> None:  # noqa: F811,E501
    esc = _escenario(session_factory)
    with session_factory() as s:
        p = dd.plan(s)
        assert {e.company_id for e in p.a_limpiar_dominio} == {
            esc["cliente"], esc["generica"], esc["con_pedido"]}
        lote = dd.aplicar(s, p, por="test")
        assert p.hechos["dominios"] == 3
    with session_factory() as s:
        for clave in ("cliente", "generica", "con_pedido"):
            assert s.get(Company, esc[clave]).domain is None, clave
        # Un dominio propio no se toca.
        assert s.get(Company, esc["propia"]).domain == "taller-figurante.es"
        ap = s.scalar(select(DespegueDominio).where(
            DespegueDominio.tipo == dd.TIPO_DOMINIO, DespegueDominio.objeto_id == esc["cliente"]))
        assert (ap.lote, ap.dominio) == (lote, "xtec.cat")
        assert dd.lotes(s)[0]["dominios"] == 3
        dd.revertir(s, lote, por="test")
    with session_factory() as s:
        assert s.get(Company, esc["cliente"]).domain == "xtec.cat"
        assert s.get(Company, esc["generica"]).domain == "juntadeandalucia.es"


def _lote_viejo(session_factory) -> str:  # noqa: F811
    """Como quedó un cliente tras un lote anterior al 1/11: los contactos
    despegados y «Dominio: outlook.fr» en su ficha."""
    with session_factory() as s:
        cliente = Company(name="Cliente figurante", factusol_company_id="3540",
                          domain="outlook.fr")
        propia = Company(name="Taller figurante", domain="taller-figurante.es")
        s.add_all([cliente, propia])
        s.flush()
        for i in range(2):
            c = Contact(first_name="Figurante", email=f"f{i}@outlook.fr", tags="")
            s.add(c)
            s.flush()
            s.add(DespegueDominio(lote=LOTE_VIEJO, tipo=dd.TIPO_CONTACTO, objeto_id=c.id,
                                  empresa_anterior=cliente.id, dominio="outlook.fr",
                                  motivo=dd.MOTIVO, por="test", at=datetime(2026, 11, 1, 4, 54)))
        s.commit()
        return cliente.id


def test_los_lotes_pasados_y_la_ficha_del_cliente_3540(http, session_factory, monkeypatch) -> None:  # noqa: F811,E501
    from scripts import limpiar_dominio_empresas_despegadas as script

    cliente = _lote_viejo(session_factory)
    engine = session_factory.kw["bind"]
    monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
    # Probar: lo dice y no escribe nada.
    assert script.main(["--probar"]) == 0
    with session_factory() as s:
        assert s.get(Company, cliente).domain == "outlook.fr"
        assert s.scalar(select(func.count()).select_from(DespegueDominio).where(
            DespegueDominio.tipo == dd.TIPO_DOMINIO)) == 0
    assert script.main(["--apply"]) == 2                       # sin --yes, no
    assert script.main(["--apply", "--yes", "--lote", LOTE_VIEJO]) == 0
    h = auth_headers(http, "admin")
    ficha = http.get(f"/api/companies/{cliente}", headers=h).json()
    assert ficha["domain"] is None
    # El formulario no deja volver a ponerlo (ni otro de la lista)…
    for dominio in ("outlook.fr", "OUTLOOK.FR", "alumnes.xtec.cat", "https://outlook.fr/",
                    "x@outlook.fr", "outlook.fr."):
        r = http.put(f"/api/companies/{cliente}", headers=h,
                     json={"name": "Cliente figurante", "domain": dominio})
        assert r.status_code == 422, dominio
        assert "lista de dominios compartidos" in r.json()["detail"]
    # …ni crear una empresa con uno.
    r = http.post("/api/companies", headers=h, json={"name": "Otra figurante",
                                                      "domain": "gmail.com"})
    assert r.status_code == 422
    # Un dominio propio, sí.
    r = http.put(f"/api/companies/{cliente}", headers=h,
                 json={"name": "Cliente figurante", "domain": "cliente-figurante.es"})
    assert r.status_code == 200, r.text
    # Si alguien le puso otro, deshacer el lote no lo pisa.
    with session_factory() as s:
        r = dd.revertir(s, LOTE_VIEJO, por="test")
        assert r["dominio_no_devuelto"] == 1
        assert s.get(Company, cliente).domain == "cliente-figurante.es"


def test_deshacer_el_lote_viejo_devuelve_el_dominio(session_factory) -> None:  # noqa: F811
    cliente = _lote_viejo(session_factory)
    with session_factory() as s:
        filas = dd.limpiar_dominios_de_lotes(s, None, por="test", aplicar=True)
        assert [(f["company_id"], f["dominio"], f["lote"]) for f in filas] == [
            (cliente, "outlook.fr", LOTE_VIEJO)]
        # Otra pasada no encuentra nada (ya está limpio).
        assert dd.limpiar_dominios_de_lotes(s, None, por="test") == []
        r = dd.revertir(s, LOTE_VIEJO, por="test")
        assert r["dominios"] == 1
    with session_factory() as s:
        assert s.get(Company, cliente).domain == "outlook.fr"


def test_una_ficha_con_dominio_viejo_se_puede_seguir_editando(http, session_factory) -> None:  # noqa: F811,E501
    cliente = _lote_viejo(session_factory)
    r = http.put(f"/api/companies/{cliente}", headers=auth_headers(http, "admin"),
                 json={"name": "Cliente figurante, otro nombre", "domain": "outlook.fr"})
    assert r.status_code == 200, r.text


def test_el_asistente_no_agrupa_por_una_empresa_de_dominio_compartido(session_factory) -> None:  # noqa: F811,E501
    """La «empresa» que era un dominio de la lista juntaría a desconocidos en el
    contexto del borrador: ni con el dominio puesto ni después de quitárselo."""
    from app.services.asistente.historico import empresa_agrupable

    cliente = _lote_viejo(session_factory)
    with session_factory() as s:
        propia = s.scalar(select(Company).where(Company.domain == "taller-figurante.es"))
        de_propia = Contact(first_name="Figurante", email="a@taller-figurante.es",
                            company_id=propia.id, tags="")
        de_cliente = Contact(first_name="Figurante", email="b@outlook.fr", company_id=cliente,
                             tags="")
        s.add_all([de_propia, de_cliente])
        s.commit()
        assert empresa_agrupable(de_propia, []) is not None
        assert empresa_agrupable(de_cliente, []) is None           # tiene outlook.fr
        dd.limpiar_dominios_de_lotes(s, None, por="test", aplicar=True)
        s.refresh(de_cliente)
        assert s.get(Company, cliente).domain is None
        assert empresa_agrupable(de_cliente, []) is None           # se lo quitó el despegue


def test_un_lote_deshecho_a_medias_no_se_toca_sin_pedirlo(session_factory) -> None:  # noqa: F811
    cliente = _lote_viejo(session_factory)
    with session_factory() as s:
        # Bart deshizo el lote, pero un contacto ya tenía otra empresa: queda a medias.
        otra = s.scalar(select(Company).where(Company.domain == "taller-figurante.es"))
        ap = s.scalars(select(DespegueDominio).where(DespegueDominio.lote == LOTE_VIEJO)).first()
        s.get(Contact, ap.objeto_id).company_id = otra.id
        s.commit()
        dd.revertir(s, LOTE_VIEJO, por="test")
        assert dd.limpiar_dominios_de_lotes(s, None, por="test") == []
        # Pedido por su nombre, sí.
        assert len(dd.limpiar_dominios_de_lotes(s, [LOTE_VIEJO], por="test")) == 1
        assert s.get(Company, cliente).domain == "outlook.fr"      # solo se ha mirado


def test_en_dos_lotes_va_al_que_trato_su_dominio(session_factory) -> None:  # noqa: F811
    cliente = _lote_viejo(session_factory)
    with session_factory() as s:
        # Un lote anterior le quitó un contacto de xtec.cat: el dominio outlook.fr
        # es del lote viejo, y deshacer ESE se lo devuelve.
        c = Contact(first_name="Figurante", email="d@xtec.cat", tags="")
        s.add(c)
        s.flush()
        s.add(DespegueDominio(lote="202610312054-164cdd", tipo=dd.TIPO_CONTACTO, objeto_id=c.id,
                              empresa_anterior=cliente, dominio="xtec.cat", motivo=dd.MOTIVO,
                              por="test", at=datetime(2026, 10, 31, 20, 54)))
        s.commit()
        filas = dd.limpiar_dominios_de_lotes(s, None, por="test")
        assert [f["lote"] for f in filas] == [LOTE_VIEJO]


def test_deshacer_con_la_empresa_borrada_cierra_el_apunte(session_factory) -> None:  # noqa: F811
    cliente = _lote_viejo(session_factory)
    with session_factory() as s:
        dd.limpiar_dominios_de_lotes(s, None, por="test", aplicar=True)
        s.delete(s.get(Company, cliente))
        s.commit()
        r = dd.revertir(s, LOTE_VIEJO, por="test")
        assert r["dominio_empresa_borrada"] == 1
        assert s.scalar(select(func.count()).select_from(DespegueDominio).where(
            DespegueDominio.tipo == dd.TIPO_DOMINIO, DespegueDominio.revertido_at.is_(None))) == 0
