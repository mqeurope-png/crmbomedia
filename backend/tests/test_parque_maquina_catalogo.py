"""«Añadir una máquina» desde el catálogo y el recuento de YA CLIENTE de la
cabecera (1/11/2026). Figurantes: ni clientes ni centros reales."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select

from app.erp import historico, parque
from app.erp import historico_clasificacion as cl
from app.erp import historico_maquinas as hm
from app.erp import parque_consultas as pc
from app.models.crm import Company, Contact
from app.services import despegue_dominios
from app.services import empresa_erp as ee
from app.services.segments import un_por_empresa
from app.services.segments.engine import build_filter
from tests._test_helpers import auth_headers
from tests.test_erp_historico import (  # noqa: F401 — fixtures
    FactusolFalso,
    _art,
    _fac,
    _lfa,
    http,
    session_factory,
)

HOY = date(2026, 11, 1)
CON_FACTURA, CON_CATALOGO, A_MANO, SIN_CONTACTOS = "4801", "4802", "4803", "4804"
# Tres máquinas cuyo código o nombre lleva un «3000» (o casi) y una tinta.
A3000, ARTIS, MBO, TINTA = "A3000U", "ART-3000UP", "MBO3020", "T1"
CAT = [_art(A3000, "IMPRESORA UV FIGURANTE A3000U", "UVM"),
       _art(ARTIS, "Artis Figurante 3000U Pro", "UVM"),
       _art(MBO, "MBO Figurante 3020", "021"),
       _art(TINTA, "TINTA UV FIGURANTE", "UV")]
FAM = [{"CODFAM": c, "DESFAM": d} for c, d in (
    ("021", "FLUX"), ("UVM", "Impresoras UV"), ("UV", "Consumible UV"))]


def _anio(ventas: list[tuple[int, str, str, str, float]]) -> dict[str, Any]:
    return {"F_FAC": [_fac(c, cli, "Cliente Figurante", f"{fecha}T00:00:00")
                      for c, cli, fecha, _a, _p in ventas],
            "F_LFA": [_lfa(c, 1, a, a, 1, p) for c, _cli, _f, a, p in ventas],
            "F_ALB": [], "F_LAL": [], "F_ART": CAT, "F_FAM": FAM}


def _preparar(session_factory) -> dict[str, str]:  # noqa: F811
    # CON_FACTURA compró su UV en una factura; CON_CATALOGO, en otro sitio (no
    # sale en sus ventas). Los dos compraron tinta en 2022 y nada desde.
    datos = {
        "2021": _anio([(210001, CON_FACTURA, "2021-03-01", A3000, 15000)]),
        "2022": _anio([(220001, CON_FACTURA, "2022-02-01", TINTA, 300),
                       (220002, CON_CATALOGO, "2022-02-01", TINTA, 300),
                       (220003, SIN_CONTACTOS, "2022-02-01", TINTA, 300)]),
    }
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(datos), list(datos), en_curso="2026")
        for g in ("Láser", "UV LED"):
            if not any(x["nombre"] == g for x in cl.gamas(s)):
                cl.crear_gama(s, g)
        for codigo, clase, gama in (("021", "maquina", "laser"), ("UVM", "maquina", "uv-led"),
                                    ("UV", "consumible", "uv-led")):
            cl.clasificar(s, codigo, {"clase": clase, "gama": gama}, por="bart")
        for codart in (A3000, ARTIS, MBO):
            hm.marcar(s, codart, True, por="bart")
        ids = {}
        for cli in (CON_FACTURA, CON_CATALOGO, A_MANO, SIN_CONTACTOS):
            e = Company(name=f"Cliente Figurante {cli}", factusol_company_id=cli)
            s.add(e)
            s.flush()
            ids[cli] = e.id
            if cli != SIN_CONTACTOS:
                s.add(Contact(first_name=f"Figurante {cli}", email=f"f{cli}@ejemplo.test",
                              company_id=e.id, tags=""))
        s.commit()
    return ids


def test_buscar_3000_ofrece_todas_las_que_casan(http, session_factory) -> None:  # noqa: F811
    _preparar(session_factory)
    r = http.get("/api/erp/parque/catalogo-maquinas", params={"q": "3000"},
                 headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    codigos = [m["codart"] for m in r.json()["items"]]
    # Las dos que llevan «3000», no una sola; la 3020 no casa.
    assert set(codigos) == {A3000, ARTIS}
    # Por nombre también, y sin guiones: «artis pro», «art3000».
    with session_factory() as s:
        assert [m["codart"] for m in parque.buscar_maquinas(s, "artis pro")] == [ARTIS]
        assert [m["codart"] for m in parque.buscar_maquinas(s, "art3000")] == [ARTIS]
        # La tinta no es una máquina: no sale.
        assert parque.buscar_maquinas(s, "tinta") == []
        assert parque.buscar_maquinas(s, "") == []


def test_elegir_del_catalogo_pone_la_gama_de_su_familia(http, session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    h = auth_headers(http, "admin")
    # Sin gama ni descripción: salen del artículo.
    r = http.post(f"/api/erp/parque/empresas/{ids[CON_CATALOGO]}/maquinas",
                  json={"codart": A3000, "fecha": "2021-03-01"}, headers=h)
    assert r.status_code == 201, r.text
    m = next(x for x in http.get(f"/api/erp/parque/empresas/{ids[CON_CATALOGO]}",
                                 headers=h).json()["maquinas"])
    assert (m["codart"], m["gama"], m["familia"], m["del_catalogo"]) == (
        A3000, "uv-led", "UVM", True)
    assert m["articulo"] == "IMPRESORA UV FIGURANTE A3000U"
    # Aunque se mande otra gama, manda la de la familia del artículo.
    http.post(f"/api/erp/parque/empresas/{ids[A_MANO]}/maquinas",
              json={"codart": A3000, "gama": "laser"}, headers=h)
    m = http.get(f"/api/erp/parque/empresas/{ids[A_MANO]}", headers=h).json()["maquinas"][0]
    assert m["gama"] == "uv-led"
    # Un código que no es una máquina del catálogo, no.
    r = http.post(f"/api/erp/parque/empresas/{ids[A_MANO]}/maquinas",
                  json={"codart": TINTA}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "codart_no_valida"


def test_la_del_catalogo_cuenta_como_una_de_factura(http, session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        parque.anadir_manual(s, ids[CON_CATALOGO], {"codart": A3000, "fecha": "2021-03-01"},
                             por="bart")
        r = pc.informe(s, hoy=HOY)
        dormidos = {(d["codcli"], d["gama"]) for d in r["dormidos"]}
        # Las dos, la de factura y la del catálogo, igual: dormidas en UV.
        assert {(CON_FACTURA, "uv-led"), (CON_CATALOGO, "uv-led")} <= dormidos
        ee.recalcular(s, hoy=HOY)
        for regla in ({"field": "parque_gama", "comparator": "in", "value": ["uv-led"]},
                      {"field": "parque_modelo", "comparator": "in", "value": [A3000]},
                      {"field": "compra_dormido", "comparator": "in", "value": ["uv-led"]}):
            arbol = {"operator": "AND", "children": [{"type": "rule", **regla}]}
            empresas = set(s.scalars(select(Contact.company_id).where(
                un_por_empresa.clausula(build_filter(arbol)))))
            assert {ids[CON_FACTURA], ids[CON_CATALOGO]} <= empresas, regla


def test_la_escrita_a_mano_sigue_y_se_distingue(http, session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    h = auth_headers(http, "admin")
    r = http.post(f"/api/erp/parque/empresas/{ids[A_MANO]}/maquinas",
                  json={"descripcion": "Láser de otro distribuidor", "gama": "laser",
                        "fecha": "2012-05-01"}, headers=h)
    assert r.status_code == 201, r.text
    m = http.get(f"/api/erp/parque/empresas/{ids[A_MANO]}", headers=h).json()["maquinas"][0]
    assert (m["codart"], m["del_catalogo"], m["gama"]) == (None, False, "laser")
    # Sin código y sin descripción, no.
    r = http.post(f"/api/erp/parque/empresas/{ids[A_MANO]}/maquinas", json={}, headers=h)
    assert r.status_code == 422


def test_el_recuento_de_la_cabecera_cuadra_con_el_script(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        ee.recalcular(s, hoy=HOY)
        est = ee.estado(s)
        script = despegue_dominios.ya_cliente(s)
        # Han comprado tres empresas; una no tiene contactos, así que nadie
        # suyo lleva la etiqueta y no cuenta (antes la cabecera decía 3).
        assert (est["ya_cliente_contactos"], est["ya_cliente_empresas"]) == (2, 2)
        assert script == {"contactos": est["ya_cliente_contactos"],
                          "empresas": est["ya_cliente_empresas"]}
        assert ids[SIN_CONTACTOS]
