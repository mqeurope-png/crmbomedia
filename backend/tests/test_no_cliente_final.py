"""«No es cliente final» (1/11/2026): la financiera del leasing, un
revendedor, nosotros. Fuera del parque, de dormidos y huecos y de las
campañas; sin borrar nada y pudiéndose desmarcar. Figurantes."""
from __future__ import annotations

import json

from sqlalchemy import select

from app.erp import parque_consultas as pc
from app.integrations.brevo.sync_targets import resolve_target_contacts
from app.models.brevo import BrevoSyncTarget
from app.models.crm import AuditLog, Company, Contact, Segment
from app.repositories import segments as segments_repository
from app.services import empresa_erp as ee
from app.services.analisis_parque import hechos as hx
from app.services.segments import un_por_empresa
from app.services.segments.engine import build_filter
from tests._test_helpers import auth_headers
from tests.test_erp_historico import http, session_factory  # noqa: F401 — fixtures
from tests.test_parque_maquina_catalogo import CON_FACTURA, HOY, _preparar

REGLA = {"operator": "AND", "children": [
    {"type": "rule", "field": "parque_gama", "comparator": "in", "value": ["uv-led"]}]}


def _estado(s, company_id: str) -> dict:
    ee.recalcular(s, hoy=HOY)
    dormidos = {d["codcli"] for d in pc.informe(s, hoy=HOY)["dormidos"]}
    con_regla = set(s.scalars(select(Contact.company_id).where(build_filter(REGLA))))
    seg = Segment(name="Figurante", owner_user_id="u", rules_json=json.dumps(REGLA))
    s.add(seg)
    s.flush()
    en_segmento = {c.company_id for c in segments_repository.list_segment_contacts(s, seg)[0]}
    target = BrevoSyncTarget(segment_id=seg.id, name="figurante", brevo_list_id="1")
    a_brevo = {c.company_id for c in resolve_target_contacts(s, target)}
    s.rollback()
    return {"dormido": CON_FACTURA in dormidos, "regla": company_id in con_regla,
            "segmento": company_id in en_segmento, "brevo": company_id in a_brevo}


def test_marcarla_la_saca_del_parque_y_de_las_campanas_y_se_deshace(http, session_factory) -> None:  # noqa: F811,E501
    ids = _preparar(session_factory)
    cid = ids[CON_FACTURA]
    with session_factory() as s:
        assert _estado(s, cid) == {"dormido": True, "regla": True, "segmento": True,
                                   "brevo": True}
    url = f"/api/companies/{cid}/no-cliente-final"
    # Con el permiso de editar la empresa.
    assert http.post(url, json={"valor": True},
                     headers=auth_headers(http, "user")).status_code == 403
    r = http.post(url, json={"valor": True}, headers=auth_headers(http, "admin"))
    assert r.status_code == 200 and r.json() == {"no_cliente_final": True}
    with session_factory() as s:
        assert _estado(s, cid) == {"dormido": False, "regla": False, "segmento": False,
                                   "brevo": False}
        # No se borra nada: la empresa, sus contactos y sus máquinas siguen.
        empresa = s.get(Company, cid)
        assert s.scalar(select(Contact.id).where(Contact.company_id == cid))
        from app.erp import parque
        p = parque.parque(s, empresa)
        assert p["no_cliente_final"] is True and p["maquinas"]
        # El análisis no se hace (ni se llama a la IA).
        assert hx.calcular(s, empresa, hoy=HOY).estado == hx.NO_CLIENTE_FINAL
        assert s.scalar(select(AuditLog.metadata_json).where(
            AuditLog.target_id == cid, AuditLog.metadata_json.like("%no_cliente_final%")))
    # Y se desmarca.
    assert http.post(url, json={"valor": False},
                     headers=auth_headers(http, "admin")).json() == {"no_cliente_final": False}
    with session_factory() as s:
        assert _estado(s, cid) == {"dormido": True, "regla": True, "segmento": True,
                                   "brevo": True}
    ficha = http.get(f"/api/companies/{cid}", headers=auth_headers(http, "admin")).json()
    assert ficha["no_cliente_final"] is False


def test_los_contactos_sin_empresa_siguen_en_las_campanas(session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    with session_factory() as s:
        suelto = Contact(first_name="Figurante", email="suelto@ejemplo.test", tags="")
        s.add(suelto)
        s.commit()
        assert suelto.id in set(s.scalars(select(Contact.id).where(
            un_por_empresa.sin_no_clientes_finales())))


def test_codigo_compartido_solo_sale_si_todas_sus_fichas_estan_marcadas(session_factory) -> None:  # noqa: F811,E501
    ids = _preparar(session_factory)
    with session_factory() as s:
        marcada = s.get(Company, ids[CON_FACTURA])
        marcada.no_cliente_final = True
        otra = Company(name="Otra ficha figurante", factusol_company_id=CON_FACTURA)
        s.add(otra)
        s.commit()
        # La otra ficha activa con el mismo código no lo dice: el parque cuenta.
        assert CON_FACTURA not in pc.clientes_no_finales(s)
        otra.is_archived = True          # una absorbida archivada no cuenta
        s.commit()
        assert CON_FACTURA in pc.clientes_no_finales(s)


def test_un_segmento_estatico_tampoco_los_manda_a_brevo(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        cid = ids[CON_FACTURA]
        contacto = s.scalar(select(Contact).where(Contact.company_id == cid))
        seg = Segment(name="Estático", owner_user_id="u", is_dynamic=False,
                      static_contact_ids=json.dumps([contacto.id]))
        s.add(seg)
        s.flush()
        target = BrevoSyncTarget(segment_id=seg.id, name="figurante", brevo_list_id="1")
        assert [c.id for c in resolve_target_contacts(s, target)] == [contacto.id]
        s.get(Company, cid).no_cliente_final = True
        s.flush()
        assert resolve_target_contacts(s, target) == []
