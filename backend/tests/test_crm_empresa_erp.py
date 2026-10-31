"""Filtrar contactos por lo que su empresa tiene y compra (1/11/2026): el
resumen ERP por empresa, las reglas nuevas y las etiquetas automáticas.
Figurantes: ni clientes, ni centros, ni correos reales."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.erp import parque
from app.erp import parque_consultas as pc
from app.erp.models import CrmEmpresaErp, CrmEtiquetaAuto
from app.models.crm import Company, Contact, ContactTag, Tag
from app.services import empresa_erp as ee
from app.services.segments.engine import build_filter
from tests._test_helpers import auth_headers
from tests.test_erp_historico import http, session_factory  # noqa: F401 — fixtures
from tests.test_erp_parque_consultas import (
    AL_DIA,
    DE_BAJA,
    DORMIDO,
    HOY,
    HUECO_UV,
    RECIENTE,
    SOLO_RECAMBIO,
    _preparar,
)


def _con_contactos(session_factory) -> dict[str, str]:  # noqa: F811
    """Un contacto por empresa figurante (dos en la de AL_DIA), uno sin
    empresa y uno de WooCommerce. Devuelve codcli → company_id."""
    _preparar(session_factory)
    with session_factory() as s:
        empresas = {c.factusol_company_id: c.id for c in s.scalars(select(Company))}
        n = 0
        for cli, cid in empresas.items():
            for _ in range(2 if cli == AL_DIA else 1):
                n += 1
                s.add(Contact(first_name=f"Figurante {n}", email=f"figurante{n}@ejemplo.test",
                              company_id=cid, tags=""))
        s.add(Contact(first_name="Sin empresa", email="sinempresa@ejemplo.test", tags=""))
        s.add(Contact(first_name="Web", email="web@ejemplo.test", tags="", origin="woocommerce",
                      company_id=empresas[DORMIDO]))
        s.commit()
        return empresas


def _recalcular(session_factory) -> dict[str, Any]:  # noqa: F811
    with session_factory() as s:
        return ee.recalcular(s, hoy=HOY)


def _empresas_que_cumplen(s, regla: dict[str, Any]) -> set[str]:
    filtro = build_filter({"operator": "AND", "children": [{"type": "rule", **regla}]})
    return {c.company_id for c in s.scalars(select(Contact).where(filtro))}


def _rule(field: str, comparator: str, value: Any) -> dict[str, Any]:
    return {"field": field, "comparator": comparator, "value": value}


def test_tiene_maquina_de_la_gama_solo_en_servicio(session_factory) -> None:  # noqa: F811
    emp = _con_contactos(session_factory)
    _recalcular(session_factory)
    with session_factory() as s:
        laser = _empresas_que_cumplen(s, _rule("parque_gama", "in", ["laser"]))
        assert laser == {emp[c] for c in (DORMIDO, AL_DIA, DE_BAJA, SOLO_RECAMBIO)}
        # La de baja deja de contar al recalcular.
        empresa = s.get(Company, emp[DE_BAJA])
        m = parque.parque(s, empresa)["maquinas"][0]
        parque.dar_baja(s, empresa, clave=m["clave"], motivo="desguazada", por="bart")
    _recalcular(session_factory)
    with session_factory() as s:
        assert emp[DE_BAJA] not in _empresas_que_cumplen(s, _rule("parque_gama", "in", ["laser"]))
        assert emp[DE_BAJA] in _empresas_que_cumplen(s, _rule("parque_tiene_baja", "eq", True))


def test_hueco_de_laser_da_las_mismas_empresas_que_la_pestana(session_factory) -> None:  # noqa: F811
    emp = _con_contactos(session_factory)
    _recalcular(session_factory)
    with session_factory() as s:
        pestana = {emp[h["codcli"]] for h in pc.informe(s, hoy=HOY)["huecos"]
                   if h["gama"] == "laser"}
        assert _empresas_que_cumplen(s, _rule("compra_hueco", "in", ["laser"])) == pestana
        dormidos = {emp[d["codcli"]] for d in pc.informe(s, hoy=HOY)["dormidos"]}
        assert _empresas_que_cumplen(s, _rule("compra_dormido", "in", ["cualquiera"])) == dormidos
        assert emp[DORMIDO] in dormidos
        # Cambiar el umbral en la pestaña mueve la regla (tras el recálculo).
        pc.guardar_ajustes(s, {"meses_dormido": 60})
    _recalcular(session_factory)
    with session_factory() as s:
        assert _empresas_que_cumplen(s, _rule("compra_dormido", "in", ["cualquiera"])) == set()


def test_sin_empresa_no_entra_ni_en_negativo_y_se_combina_con_reglas_viejas(session_factory) -> None:  # noqa: F811,E501
    emp = _con_contactos(session_factory)
    _recalcular(session_factory)
    with session_factory() as s:
        sin = s.scalar(select(Contact.id).where(Contact.company_id.is_(None)))
        filtro = build_filter(_rule_tree("OR", [_rule("parque_gama", "not_in", ["laser"]),
                                                _rule("compra_nunca_clase", "in", ["consumible"])]))
        assert sin not in {c.id for c in s.scalars(select(Contact).where(filtro))}
        # AND con una regla de siempre (email contiene) y OR con otra nueva.
        filtro = build_filter(_rule_tree("AND", [
            _rule("parque_gama", "in", ["uv-led"]),
            _rule("email", "contains", "figurante"),
        ]))
        ids = {c.company_id for c in s.scalars(select(Contact).where(filtro))}
        assert ids == {emp[HUECO_UV], emp[RECIENTE]}


def _rule_tree(op: str, reglas: list[dict[str, Any]]) -> dict[str, Any]:
    return {"operator": op, "children": [{"type": "rule", **r} for r in reglas]}


def test_compras_gasto_familia_clase_y_nunca(session_factory) -> None:  # noqa: F811
    emp = _con_contactos(session_factory)
    _recalcular(session_factory)
    with session_factory() as s:
        assert _empresas_que_cumplen(s, _rule("compra_familia", "in", ["251"])) == \
            {emp[DORMIDO], emp[AL_DIA]}
        assert _empresas_que_cumplen(s, _rule("compra_clase", "in", ["recambio"])) == \
            {emp[SOLO_RECAMBIO]}
        nunca = _empresas_que_cumplen(s, _rule("compra_nunca_clase", "in", ["consumible"]))
        assert emp[HUECO_UV] in nunca and emp[DORMIDO] not in nunca
        assert _empresas_que_cumplen(s, _rule("compra_gasto_total", "gte", 10000)) == \
            {emp[HUECO_UV], emp[RECIENTE]}
        assert emp[DORMIDO] in _empresas_que_cumplen(s, _rule("compra_articulo", "contains",
                                                              "Beam Air"))


def test_las_vistas_de_siempre_no_cambian(session_factory) -> None:  # noqa: F811
    _con_contactos(session_factory)
    arbol = _rule_tree("AND", [_rule("email", "contains", "figurante")])
    with session_factory() as s:
        antes = {c.id for c in s.scalars(select(Contact).where(build_filter(arbol)))}
    _recalcular(session_factory)
    with session_factory() as s:
        despues = {c.id for c in s.scalars(select(Contact).where(build_filter(arbol)))}
    assert antes == despues and len(antes) == 8


def test_un_segmento_de_brevo_con_una_regla_nueva_tiene_los_mismos_contactos(session_factory) -> None:  # noqa: F811,E501
    import json

    from app.integrations.brevo.sync_targets import resolve_target_contacts
    from app.models.brevo import BrevoSyncTarget
    from app.models.crm import Segment

    _con_contactos(session_factory)
    _recalcular(session_factory)
    arbol = _rule_tree("AND", [_rule("compra_hueco", "in", ["laser"])])
    with session_factory() as s:
        vista = {c.id for c in s.scalars(select(Contact).where(build_filter(arbol)))}
        from app.models.crm import User
        dueno = s.scalar(select(User.id).limit(1))
        seg = Segment(name="Huecos de láser figurante", rules_json=json.dumps(arbol),
                      is_dynamic=True, owner_user_id=dueno)
        s.add(seg)
        s.flush()
        # Solo hace falta su segmento para resolver los contactos (no se guarda).
        target = BrevoSyncTarget(segment_id=seg.id, name="figurante", brevo_list_id="1")
        segmento = {c.id for c in resolve_target_contacts(s, target)}
    assert vista and segmento == vista


def _etiquetados(s, nombre: str) -> set[str]:
    tag = s.scalar(select(Tag).where(Tag.name_normalized == nombre.lower()))
    return set(s.scalars(select(ContactTag.contact_id).where(ContactTag.tag_id == tag.id))) \
        if tag else set()


def test_ya_cliente_y_origen_woo_se_mantienen_solas(session_factory) -> None:  # noqa: F811
    emp = _con_contactos(session_factory)
    r = _recalcular(session_factory)
    with session_factory() as s:
        ya = _etiquetados(s, ee.YA_CLIENTE)
        compraron = set(s.scalars(select(CrmEmpresaErp.company_id).where(
            CrmEmpresaErp.gasto_total > 0)))
        assert compraron == {emp[c] for c in (DORMIDO, AL_DIA, HUECO_UV, RECIENTE, DE_BAJA,
                                              SOLO_RECAMBIO)}
        de_clientes = set(s.scalars(select(Contact.id).where(
            Contact.company_id.in_(compraron))))
        assert ya == de_clientes                  # y la empresa sin compras, no
        assert r["ya_cliente"]["con_etiqueta"] == len(de_clientes)
        sin = s.scalar(select(Contact.id).where(Contact.company_id.is_(None)))
        assert sin not in ya
        woo = _etiquetados(s, ee.ORIGEN_WOO)
        assert woo == set(s.scalars(select(Contact.id).where(Contact.origin == "woocommerce")))
        estado = ee.estado(s)
        assert estado["ya_cliente_contactos"] == len(de_clientes)
        assert estado["origen_woo_contactos"] == 1


def test_quitada_a_mano_no_vuelve_y_anadida_a_mano_vuelve_al_regimen_normal(session_factory) -> None:  # noqa: F811,E501
    _con_contactos(session_factory)
    _recalcular(session_factory)
    with session_factory() as s:
        tag_id = s.scalar(select(Tag.id).where(Tag.name_normalized == "ya cliente"))
        victima = sorted(_etiquetados(s, ee.YA_CLIENTE))[0]
        s.query(ContactTag).filter_by(contact_id=victima, tag_id=tag_id).delete()
        s.commit()
    _recalcular(session_factory)
    with session_factory() as s:
        assert victima not in _etiquetados(s, ee.YA_CLIENTE)       # no se la devuelve
        assert ee.estado(s)["quitadas_a_mano"] == 1
        ap = s.get(CrmEtiquetaAuto, (victima, "ya_cliente"))
        assert ap.estado == ee.QUITADA
        # Alguien se la vuelve a poner a mano: el apunte se borra.
        s.add(ContactTag(contact_id=victima, tag_id=tag_id, source="manual"))
        s.commit()
    _recalcular(session_factory)
    with session_factory() as s:
        assert ee.estado(s)["quitadas_a_mano"] == 0
        assert s.get(CrmEtiquetaAuto, (victima, "ya_cliente")).estado == ee.PUESTA
        # Régimen normal de verdad: si se la vuelven a quitar, vuelve a constar.
        s.query(ContactTag).filter_by(contact_id=victima, tag_id=tag_id).delete()
        s.commit()
    _recalcular(session_factory)
    with session_factory() as s:
        assert victima not in _etiquetados(s, ee.YA_CLIENTE)
        assert ee.estado(s)["quitadas_a_mano"] == 1


def test_quitar_a_mano_la_que_se_puso_a_mano_tambien_vale(session_factory) -> None:  # noqa: F811
    """Un contacto que cumple y ya tenía la etiqueta puesta a mano antes del
    primer recálculo: si alguien se la quita, tampoco se le vuelve a poner."""
    emp = _con_contactos(session_factory)
    with session_factory() as s:
        cid = s.scalar(select(Contact.id).where(Contact.company_id == emp[DORMIDO]))
        tag = ee._tag(s, ee.YA_CLIENTE)
        s.add(ContactTag(contact_id=cid, tag_id=tag.id, source="manual"))
        s.commit()
        tag_id = tag.id
    _recalcular(session_factory)
    with session_factory() as s:
        ct = s.scalar(select(ContactTag).where(ContactTag.contact_id == cid,
                                               ContactTag.tag_id == tag_id))
        assert ct.source == "manual"                 # no se la reescribe
        s.delete(ct)
        s.commit()
    _recalcular(session_factory)
    with session_factory() as s:
        assert cid not in _etiquetados(s, ee.YA_CLIENTE)
        assert s.get(CrmEtiquetaAuto, (cid, "ya_cliente")).estado == ee.QUITADA


def test_una_etiqueta_puesta_a_mano_no_se_quita_aunque_no_cumpla(session_factory) -> None:  # noqa: F811
    _con_contactos(session_factory)
    _recalcular(session_factory)
    with session_factory() as s:
        sin = s.scalar(select(Contact.id).where(Contact.company_id.is_(None)))
        tag = s.scalar(select(Tag).where(Tag.name_normalized == "ya cliente"))
        s.add(ContactTag(contact_id=sin, tag_id=tag.id, source="manual"))
        s.commit()
    _recalcular(session_factory)
    with session_factory() as s:
        assert sin in _etiquetados(s, ee.YA_CLIENTE)


def test_si_el_recalculo_falla_se_queda_lo_anterior_y_se_dice(session_factory, monkeypatch) -> None:  # noqa: F811,E501
    import pytest

    _con_contactos(session_factory)
    _recalcular(session_factory)

    def roto(*a, **k):
        raise RuntimeError("caída")
    monkeypatch.setattr(ee, "calcular", roto)
    with session_factory() as s:
        with pytest.raises(RuntimeError):
            ee.recalcular(s, hoy=HOY)
    with session_factory() as s:
        est = ee.estado(s)
        assert est["fallo"] == "RuntimeError" and est["fallo_at"] and est["ok_at"]
        assert s.scalar(select(CrmEmpresaErp).limit(1)) is not None   # lo anterior sigue


def test_api_estado_y_opciones_de_los_datos(http, session_factory) -> None:  # noqa: F811
    _con_contactos(session_factory)
    _recalcular(session_factory)
    h = auth_headers(http, "admin")
    est = http.get("/api/crm/resumen-erp", headers=h).json()
    assert est["calculado"] is True and est["ya_cliente_contactos"] >= 1
    campos = {c["key"]: c for c in http.get("/api/entities/contact/filter-schema",
                                            headers=h).json()["fields"]}
    assert "laser" in campos["parque_gama"]["enum_values"]
    assert campos["compra_hueco"]["enum_values"][0] == "cualquiera"
    assert "woocommerce" in campos["origin"]["enum_values"]
    assert campos["empresa_pais"]["label"] == "País (empresa)"
    assert campos["parque_gama"]["grouped_under"] == "Empresa, parque y compras"
    # Las de siempre siguen ahí, con su etiqueta.
    assert campos["address_country"]["label"] != campos["empresa_pais"]["label"]


# --- Revisión (1/11/2026) ---------------------------------------------------------------


def _datos_revision() -> dict[str, Any]:
    """Los de «Dormidos y huecos» más: L2 (equivalente de L1, con familia de
    consumible) comprada por HUECO_UV; una línea tecleada «0L1» (alias de L1)
    de RECIENTE; y F1 cambiada de familia en 2026 (recambio), comprada por
    AL_DIA en 2023 y en 2026."""
    from tests.test_erp_historico import _art, _fac, _lfa
    from tests.test_erp_parque_consultas import _datos

    datos = _datos()
    d23 = datos["2023"]
    d23["F_ART"] = [*d23["F_ART"], _art("L2", "FLUX BEAMBOX FIGURANTE 40W (WEB)", "251")]
    for codigo, cli, art in ((230010, HUECO_UV, "L2"), (230011, RECIENTE, "0L1"),
                             (230012, AL_DIA, "F1")):
        d23["F_FAC"] = [*d23["F_FAC"], _fac(codigo, cli, "Cliente Figurante",
                                            "2023-04-01T00:00:00")]
        d23["F_LFA"] = [*d23["F_LFA"], _lfa(codigo, 1, art, art, 1, 100)]
    d26 = datos["2026"]
    d26["F_ART"] = [a if a["CODART"] != "F1" else {**a, "FAMART": "211"} for a in d26["F_ART"]]
    return datos


def _preparar_revision(session_factory) -> dict[str, str]:  # noqa: F811
    from app.erp import historico_alias, historico_equivalencias
    from tests.test_erp_parque_consultas import _preparar

    _preparar(session_factory, _datos_revision())
    with session_factory() as s:
        historico_equivalencias.declarar(s, "L1", "L2", por="bart")
        historico_alias.declarar_articulo(s, "0L1", "L1", por="bart")
        emp = {c.factusol_company_id: c.id for c in s.scalars(select(Company))}
        for n, cid in enumerate(emp.values()):
            s.add(Contact(first_name=f"Figurante {n}", email=f"rev{n}@ejemplo.test",
                          company_id=cid, tags=""))
        s.commit()
        return emp


def test_equivalentes_alias_y_familia_de_ahora(session_factory) -> None:  # noqa: F811
    from app.erp.models import CrmEmpresaCompra, CrmEmpresaModelo

    emp = _preparar_revision(session_factory)
    _recalcular(session_factory)
    with session_factory() as s:
        def compra(cli: str, codart: str) -> Any:
            return s.get(CrmEmpresaCompra, (emp[cli], codart))

        # L2 es el mismo modelo que la L1 marcada: es máquina, aunque su familia
        # diga consumible, y su modelo es L1.
        assert (compra(HUECO_UV, "L2").clase, compra(HUECO_UV, "L2").modelo) == ("maquina", "L1")
        # La línea «0L1» es la L1 del catálogo, también como modelo del parque.
        assert compra(RECIENTE, "L1") is not None
        assert s.get(CrmEmpresaModelo, (emp[RECIENTE], "L1")) is not None
        assert emp[RECIENTE] in _empresas_que_cumplen(s, _rule("parque_modelo", "in", ["L1"]))
        # F1 cambió de familia: manda la del ejercicio más reciente, siempre.
        assert compra(AL_DIA, "F1").familia == "211"
        # «Ha comprado L1» encuentra a quien compró el equivalente, y al revés.
        con_l1 = _empresas_que_cumplen(s, _rule("compra_articulo", "eq", "L1"))
        assert {emp[HUECO_UV], emp[RECIENTE], emp[AL_DIA]} <= con_l1
        assert emp[AL_DIA] in _empresas_que_cumplen(s, _rule("compra_articulo", "eq", "l2"))
        assert emp[HUECO_UV] in _empresas_que_cumplen(s, _rule("compra_clase", "in", ["maquina"]))
        # Un «%» es un carácter, no un comodín.
        assert _empresas_que_cumplen(s, _rule("compra_articulo", "contains", "%")) == set()


def test_sin_cliente_de_factusol_ni_compras_no_cumple_las_negativas(session_factory) -> None:  # noqa: F811,E501
    from app.erp.models.orders import Order

    _con_contactos(session_factory)
    with session_factory() as s:
        solo_pedidos = Company(name="Solo pedidos figurante")
        sin_compras = Company(name="Sin compras figurante", factusol_company_id="4799")
        s.add_all([solo_pedidos, sin_compras])
        s.flush()
        s.add(Order(order_number="M-9", external_source="manual", company_id=solo_pedidos.id,
                    total_amount=10))
        for c in (solo_pedidos, sin_compras):
            s.add(Contact(first_name="Figurante", email=f"{c.id[:6]}@ejemplo.test",
                          company_id=c.id, tags=""))
        s.commit()
        ids = (solo_pedidos.id, sin_compras.id)
        emp = {c.factusol_company_id: c.id for c in s.scalars(select(Company))}
    _recalcular(session_factory)
    with session_factory() as s:
        assert s.get(CrmEmpresaErp, ids[0]).ya_cliente is True       # es cliente…
        for regla in (_rule("parque_maquinas", "eq", 0), _rule("parque_tiene_baja", "eq", False),
                      _rule("compra_gasto_total", "lte", 100),
                      _rule("parque_gama", "not_in", ["laser"])):
            assert ids[0] not in _empresas_que_cumplen(s, regla), regla   # …sin parque conocido
        nunca = _empresas_que_cumplen(s, _rule("compra_nunca_clase", "in", ["consumible"]))
        assert ids[1] not in nunca and ids[0] not in nunca
        assert emp[SOLO_RECAMBIO] in nunca


def test_la_ultima_compra_sin_fecha_cuenta_el_1_de_enero(session_factory) -> None:  # noqa: F811
    from datetime import date

    from app.erp.models.historico import HistoricoDocumento

    emp = _con_contactos(session_factory)
    with session_factory() as s:
        for d in s.scalars(select(HistoricoDocumento).where(
                HistoricoDocumento.codcli == SOLO_RECAMBIO, HistoricoDocumento.ejercicio == "2023")):
            d.fecha = None
        s.commit()
    _recalcular(session_factory)
    with session_factory() as s:
        assert s.get(CrmEmpresaErp, emp[SOLO_RECAMBIO]).ultima_compra == date(2023, 1, 1)


def test_regla_de_asignacion_al_crear_mira_la_empresa_en_la_bd(session_factory) -> None:  # noqa: F811,E501
    from app.services.segments.engine import evaluate_contact_against_rules

    emp = _con_contactos(session_factory)
    _recalcular(session_factory)
    regla = {"operator": "AND", "children": [
        {"type": "rule", **_rule("empresa_pais", "is_null", None)}]}
    gama = {"operator": "AND", "children": [
        {"type": "rule", **_rule("parque_gama", "in", ["laser"])}]}
    with session_factory() as s:
        sin = Contact(first_name="Nuevo", email="nuevo@ejemplo.test", tags="")
        con = Contact(first_name="Nuevo", email="nuevo2@ejemplo.test", tags="",
                      company_id=emp[AL_DIA])
        s.add_all([sin, con])
        s.flush()
        assert evaluate_contact_against_rules(sin, regla) is False    # sin empresa, nunca
        assert evaluate_contact_against_rules(con, regla) is True     # su empresa no tiene país
        assert evaluate_contact_against_rules(con, gama) is True
        assert evaluate_contact_against_rules(sin, gama) is False


def test_contactos_borrados_y_etiquetas_automaticas_huerfanas(session_factory) -> None:  # noqa: F811,E501
    _con_contactos(session_factory)
    _recalcular(session_factory)
    with session_factory() as s:
        tag_id = s.scalar(select(Tag.id).where(Tag.name_normalized == "ya cliente"))
        victima = sorted(_etiquetados(s, ee.YA_CLIENTE))[0]
        s.query(ContactTag).filter_by(contact_id=victima).delete()
        s.query(Contact).filter_by(id=victima).delete()
        # Una automática sin apunte en quien no cumple (p. ej. tras bajar y
        # volver a subir la migración): es suya por el `source`, se quita.
        sin = s.scalar(select(Contact.id).where(Contact.company_id.is_(None)))
        s.add(ContactTag(contact_id=sin, tag_id=tag_id, source="auto:ya_cliente"))
        s.commit()
    _recalcular(session_factory)
    with session_factory() as s:
        assert ee.estado(s)["quitadas_a_mano"] == 0          # el borrado no cuenta
        assert s.get(CrmEtiquetaAuto, (victima, "ya_cliente")) is None
        assert sin not in _etiquetados(s, ee.YA_CLIENTE)
