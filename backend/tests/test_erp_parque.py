"""El parque instalado en la ficha de empresa (30/10/2026). Figurantes."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.erp import historico, parque
from app.erp.models import HistoricoGama, ParqueBaja
from app.models.crm import Company
from tests._test_helpers import auth_headers
from tests.test_erp_historico import (  # noqa: F401 — fixtures
    CLI_ROTULOS,
    CLI_TALLER,
    MAQUINA,
    FactusolFalso,
    _alb,
    _art,
    _fac,
    _lal,
    _lfa,
    datos_figurantes,
    http,
    session_factory,
)

ACCESORIO = "4001"


def _datos() -> dict[str, Any]:
    d = datos_figurantes()
    hoy = d["2026"]
    # Un accesorio vendido a Talleres (familia ACC, clase accesorio).
    hoy["F_ART"].append(_art(ACCESORIO, "Mesa de panal figurante", "ACC"))
    hoy["F_FAM"].append({"CODFAM": "ACC", "DESFAM": "Accesorios figurante"})
    hoy["F_FAC"].append(_fac(260003, CLI_TALLER, "Talleres Figurante S.L.", "2026-09-01T00:00:00"))
    hoy["F_LFA"].append(_lfa(260003, 1, ACCESORIO, "Mesa de panal", 1, 300))
    # El albarán 26001 (Rótulos) ya está facturado (ESTALB 1): no cuenta otra vez.
    for a in hoy["F_ALB"]:
        a["ESTALB"] = "1" if a["CODALB"] == 26001 else "0"
    return d


def _preparar(session_factory) -> dict[str, str]:  # noqa: F811
    with session_factory() as s:
        s.add_all([HistoricoGama(codigo="laser", nombre="Láser", orden=1),
                   HistoricoGama(codigo="uv-led", nombre="UV LED", orden=2)])
        s.commit()
        historico.cargar(s, FactusolFalso(_datos()), ["2026", "2017"], en_curso="2026")
        from app.erp import historico_clasificacion as cl

        cl.clasificar(s, "LAS", {"clase": "maquina", "gama": "laser"}, por="x")
        cl.clasificar(s, "ACC", {"clase": "accesorio", "gama": "laser"}, por="x")
        cl.clasificar(s, "PLO", {"clase": "maquina", "gama": "laser"}, por="x")
        rotulos = Company(name="Rótulos Figurante", factusol_company_id=CLI_ROTULOS)
        sin = Company(name="Sin FACTUSOL Figurante")
        s.add_all([rotulos, sin])
        s.commit()
        taller = s.scalars(select(Company).where(
            Company.factusol_company_id == CLI_TALLER)).one()
        return {"taller": taller.id, "rotulos": rotulos.id, "sin": sin.id}


def test_la_ficha_de_una_empresa_ensena_sus_maquinas_con_gama_y_fecha(
    session_factory,  # noqa: F811
) -> None:
    ids = _preparar(session_factory)
    with session_factory() as s:
        p = parque.parque(s, s.get(Company, ids["taller"]))
    articulos = {(m["codart"], m["ejercicio"]) for m in p["maquinas"]}
    # La 1390 de 2026, la 1390 y el plotter de 2017; nunca la mesa (accesorio).
    assert articulos == {(MAQUINA, "2026"), (MAQUINA, "2017"), ("0917", "2017")}
    hoy = next(m for m in p["maquinas"] if m["ejercicio"] == "2026")
    assert (hoy["gama_nombre"], hoy["fecha"], hoy["origen"]) == ("Láser", "2026-03-10", "factura")
    assert hoy["documento"]["numero"] == "5-260001"
    assert p["activas"] == 3 and p["sin_cliente_factusol"] is False


def test_un_accesorio_no_es_maquina_y_el_albaran_facturado_no_cuenta_dos_veces(
    session_factory,  # noqa: F811
) -> None:
    ids = _preparar(session_factory)
    with session_factory() as s:
        taller = parque.parque(s, s.get(Company, ids["taller"]))
        rotulos = parque.parque(s, s.get(Company, ids["rotulos"]))
    assert ACCESORIO not in {m["codart"] for m in taller["maquinas"]}
    # Rótulos: la 1390 facturada en 2026 y la de 2017 (2 uds en una línea); el
    # albarán 26001 de esa misma venta no se suma.
    origenes = sorted((m["ejercicio"], m["origen"]) for m in rotulos["maquinas"])
    assert origenes == [("2017", "factura"), ("2026", "factura")]


def test_una_maquina_a_mano_sale_junto_a_las_demas_marcada(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        parque.anadir_manual(s, ids["taller"], {"descripcion": "Láser comprada en 2012",
                                                "gama": "laser", "fecha": "2012-06-01"},
                             por="x")
        p = parque.parque(s, s.get(Company, ids["taller"]))
    manual = next(m for m in p["maquinas"] if m["origen"] == "manual")
    assert (manual["articulo"], manual["gama_nombre"], manual["documento"]) == (
        "Láser comprada en 2012", "Láser", None)
    assert p["activas"] == 4


def test_una_baja_se_conserva_al_recargar_y_deja_de_contar(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        company = s.get(Company, ids["taller"])
        clave = next(m["clave"] for m in parque.parque(s, company)["maquinas"]
                     if m["ejercicio"] == "2017" and m["codart"] == MAQUINA)
        parque.dar_baja(s, company, clave=clave, motivo="Desguazada", por="x")
        # Recargar el ejercicio cambia los ids de las líneas, no la clave.
        historico.cargar_ejercicio(s, FactusolFalso(_datos()), "2017")
        p = parque.parque(s, company)
        baja = next(m for m in p["maquinas"] if m["clave"] == clave)
        assert baja["baja"]["motivo"] == "Desguazada"
        assert p["activas"] == 2
        # Las dadas de baja van al final.
        assert p["maquinas"][-1]["clave"] == clave
        parque.reactivar(s, company, clave=clave)
        assert parque.parque(s, company)["activas"] == 3
        assert s.get(ParqueBaja, clave) is None


def test_una_empresa_sin_cliente_de_factusol_lo_dice(http, session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    h = auth_headers(http, "admin")
    p = http.get(f"/api/erp/parque/empresas/{ids['sin']}", headers=h).json()
    assert p["sin_cliente_factusol"] is True and p["maquinas"] == []
    c = http.get(f"/api/erp/parque/empresas/{ids['sin']}/compras", headers=h).json()
    assert c["sin_cliente_factusol"] is True


def test_compras_arranca_en_el_ejercicio_en_curso_y_todos_trae_lo_anterior(
    http, session_factory,  # noqa: F811
) -> None:
    ids = _preparar(session_factory)
    h = auth_headers(http, "admin")
    hoy = http.get(f"/api/erp/parque/empresas/{ids['taller']}/compras", headers=h).json()
    assert hoy["ejercicio"] == "2026"
    assert {d["ejercicio"] for d in hoy["documentos"]} == {"2026"}
    todo = http.get(f"/api/erp/parque/empresas/{ids['taller']}/compras",
                    params={"ejercicio": "todos"}, headers=h).json()
    assert {d["ejercicio"] for d in todo["documentos"]} == {"2026", "2017"}
    assert todo["ejercicios"] == ["2026", "2017"]
    solo = http.get(f"/api/erp/parque/empresas/{ids['taller']}/compras",
                    params={"ejercicio": "2017"}, headers=h).json()
    assert {d["ejercicio"] for d in solo["documentos"]} == {"2017"}


def test_api_anadir_y_dar_de_baja_es_de_quien_trabaja_pedidos(http, session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    url = f"/api/erp/parque/empresas/{ids['taller']}"
    assert http.get(url, headers=auth_headers(http, "user")).status_code == 200
    assert http.get(url, headers=auth_headers(http, "sat")).status_code == 403
    cuerpo = {"descripcion": "Láser de otro proveedor", "gama": "laser"}
    assert http.post(f"{url}/maquinas", json=cuerpo,
                     headers=auth_headers(http, "user")).status_code == 403
    r = http.post(f"{url}/maquinas", json=cuerpo, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201
    mid = r.json()["id"]
    p = http.post(f"{url}/bajas", json={"maquina_id": mid, "motivo": "Vendida"},
                  headers=auth_headers(http, "pedidos")).json()
    assert next(m for m in p["maquinas"] if m["id"] == mid)["baja"]["motivo"] == "Vendida"
    # Una clave que no es de su parque no se acepta.
    assert http.post(f"{url}/bajas", json={"clave": "2017|factura|1|1|1|x"},
                     headers=auth_headers(http, "pedidos")).status_code == 404
