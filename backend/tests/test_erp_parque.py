"""El parque instalado en la ficha de empresa (30/10/2026). Figurantes."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.erp import historico, parque
from app.erp.models import HistoricoEjercicio, HistoricoGama, ParqueBaja
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
    # El albarán 26001 (Rótulos) se facturó en la 260002: su línea lo apunta
    # (DOCLFA «A»), como hace FACTUSOL al facturar un albarán.
    for a in hoy["F_ALB"]:
        a["ESTALB"] = "1" if a["CODALB"] == 26001 else "0"
    for ln in hoy["F_LFA"]:
        if (ln["CODLFA"], ln["ARTLFA"]) == (260002, MAQUINA):
            ln.update({"DOCLFA": "A", "DTPLFA": "5", "DCOLFA": 26001})
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


# --- lo que encontró la revisión -------------------------------------------------------


def test_un_abono_anula_la_maquina_mas_reciente(session_factory) -> None:  # noqa: F811
    datos = _datos()
    hoy = datos["2026"]
    hoy["F_FAC"].append(_fac(260009, CLI_ROTULOS, "Rótulos Figurante", "2026-10-01T00:00:00"))
    hoy["F_LFA"].append(_lfa(260009, 1, MAQUINA, "Abono láser", -1, 1000))
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(datos), ["2026", "2017"], en_curso="2026")
        from app.erp import historico_clasificacion as cl

        cl.clasificar(s, "LAS", {"clase": "maquina"}, por="x")
        rotulos = Company(name="Rótulos Figurante", factusol_company_id=CLI_ROTULOS)
        s.add(rotulos)
        s.commit()
        p = parque.parque(s, rotulos)
    # Queda la de 2017 (2 uds); la de 2026 está abonada.
    assert [(m["ejercicio"], m["cantidad"]) for m in p["maquinas"]] == [("2017", 2.0)]


def test_la_baja_la_deshace_la_empresa_que_la_ve(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        taller = s.get(Company, ids["taller"])
        clave = parque.parque(s, taller)["maquinas"][0]["clave"]
        parque.dar_baja(s, taller, clave=clave, motivo=None, por="x")
        # Como si la hubiera dado otra empresa antes de fusionarse con esta.
        s.get(ParqueBaja, clave).company_id = ids["rotulos"]
        s.commit()
        parque.reactivar(s, taller, clave=clave)
        assert s.get(ParqueBaja, clave) is None


# --- albarán y factura de la misma venta; artículos con clase propia (30/10) -------------

CLI_HEXA = "4109"        # «Grabados Figurante», solo en estas pruebas
HEXA = "3301"            # «Láser Figurante Hexa 60W»
SENSOR = "3302"          # «Sensor Figurante 3D» (accesorio en una familia de máquinas)


def _datos_hexa(*, enlace: str) -> dict[str, Any]:
    """Una factura de 15/12/2022 (serie 1) y su albarán de 2/1/2023 (serie 3).
    `enlace`: «proforma» (las dos salen de la proforma 1-000500), «directo»
    (la factura apunta al albarán) o «ninguno» (tecleados a mano)."""
    d = _datos()
    origen_fac = {"proforma": {"DOCLFA": "P", "DTPLFA": "1", "DCOLFA": 500},
                  "directo": {"DOCLFA": "A", "DTPLFA": 3.0, "DCOLFA": 230001},
                  "ninguno": {}}[enlace]
    origen_alb = ({"DOCLAL": "P", "DTPLAL": "1", "DCOLAL": 500} if enlace == "proforma"
                  # Un albarán autónomo se apunta a sí mismo: no es un enlace.
                  else {"DOCLAL": "A", "DTPLAL": "3", "DCOLAL": 230001})
    cat = [_art(HEXA, "Láser Figurante Hexa 60W", "LAS"),
           _art(SENSOR, "Sensor Figurante 3D", "LAS")]
    d["2022"] = {
        "F_FAC": [_fac(123021, CLI_HEXA, "Grabados Figurante", "2022-12-15T00:00:00", "1")],
        "F_LFA": [{**_lfa(123021, 1, HEXA, "HEXA 60W", 1, 9000, "1"), **origen_fac},
                  _lfa(123021, 2, SENSOR, "Sensor 3D", 1, 300, "1")],
        "F_ART": cat,
    }
    d["2023"] = {
        "F_ALB": [{**_alb(230001, CLI_HEXA, "Grabados Figurante", "2023-01-02T00:00:00", "3"),
                   "ESTALB": "0"}],
        "F_LAL": [{**_lal(230001, 1, HEXA, "HEXA 60W", 1, "3"), **origen_alb}],
        "F_ART": cat,
    }
    return d


def _parque_hexa(session_factory, enlace: str, **extra) -> dict:  # noqa: F811
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(_datos_hexa(enlace=enlace)),
                         ["2026", "2023", "2022"], en_curso="2026")
        from app.erp import historico_clasificacion as cl

        cl.clasificar(s, "LAS", {"clase": "maquina"}, por="x")
        for codart, clase in extra.items():
            cl.clasificar_articulo(s, codart, clase, por="x")
        empresa = Company(name="Grabados Figurante", factusol_company_id=CLI_HEXA)
        s.add(empresa)
        s.commit()
        return parque.parque(s, empresa)


def _hexas(p: dict) -> list[dict]:
    return [m for m in p["maquinas"] if m["codart"] == HEXA]


def test_albaran_y_factura_de_la_misma_proforma_son_una_maquina(session_factory) -> None:  # noqa: F811
    hexas = _hexas(_parque_hexa(session_factory, "proforma"))
    assert len(hexas) == 1
    assert hexas[0]["documento"]["numero"] == "1-123021"
    assert [a["numero"] for a in hexas[0]["albaranes"]] == ["3-230001"]


def test_la_factura_que_apunta_al_albaran_es_una_maquina(session_factory) -> None:  # noqa: F811
    hexas = _hexas(_parque_hexa(session_factory, "directo"))
    assert [(m["origen"], [a["numero"] for a in m["albaranes"]]) for m in hexas] == [
        ("factura", ["3-230001"])]


def test_sin_enlace_salen_las_dos_y_se_avisa(session_factory) -> None:  # noqa: F811
    # Sin nada que las una no se casan solas (podrían ser dos compras), pero
    # el albarán avisa de la factura cercana del mismo artículo.
    hexas = _hexas(_parque_hexa(session_factory, "ninguno"))
    assert sorted(m["origen"] for m in hexas) == ["albaran", "factura"]
    alb = next(m for m in hexas if m["origen"] == "albaran")
    assert alb["posible_misma_venta"]["numero"] == "1-123021"
    assert next(m for m in hexas if m["origen"] == "factura")["posible_misma_venta"] is None


def test_dos_compras_del_mismo_modelo_lejanas_son_dos_maquinas(session_factory) -> None:  # noqa: F811
    d = _datos_hexa(enlace="ninguno")
    d["2023"]["F_ALB"][0]["FECALB"] = "2023-09-01T00:00:00"
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026", "2023", "2022"], en_curso="2026")
        from app.erp import historico_clasificacion as cl

        cl.clasificar(s, "LAS", {"clase": "maquina"}, por="x")
        empresa = Company(name="Grabados Figurante", factusol_company_id=CLI_HEXA)
        s.add(empresa)
        s.commit()
        hexas = _hexas(parque.parque(s, empresa))
    assert len(hexas) == 2
    assert all(m["posible_misma_venta"] is None for m in hexas)


def test_sin_clase_propia_el_accesorio_de_una_familia_de_maquinas_sale(session_factory) -> None:  # noqa: F811
    con = _parque_hexa(session_factory, "proforma")
    assert SENSOR in {m["codart"] for m in con["maquinas"]}


def test_la_clase_propia_del_articulo_manda_sobre_la_familia(session_factory) -> None:  # noqa: F811
    p = _parque_hexa(session_factory, "proforma", **{SENSOR: "accesorio"})
    assert SENSOR not in {m["codart"] for m in p["maquinas"]}
    assert HEXA in {m["codart"] for m in p["maquinas"]}
    with session_factory() as s:
        # «Quién compró qué» con clase máquina tampoco lo cuenta.
        assert historico.compradores(s, codart=SENSOR, clase="maquina") == []
        from app.erp import historico_clasificacion as cl

        # Quitarla vuelve a la de la familia.
        cl.clasificar_articulo(s, SENSOR, None, por="x")
        empresa = s.scalars(select(Company).where(
            Company.factusol_company_id == CLI_HEXA)).one()
        assert SENSOR in {m["codart"] for m in parque.parque(s, empresa)["maquinas"]}


def test_api_clase_de_articulo_la_pone_quien_configura(http, session_factory) -> None:  # noqa: F811
    _parque_hexa(session_factory, "proforma")
    url = f"/api/erp/historico/articulos-clase/{SENSOR}"
    assert http.put(url, json={"clase": "accesorio"},
                    headers=auth_headers(http, "pedidos")).status_code == 403
    r = http.put(url, json={"clase": "accesorio"}, headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    fila = r.json()["items"][0]
    assert (fila["codart"], fila["clase"], fila["nombre"], fila["clase_familia"]) == (
        SENSOR, "accesorio", "Sensor Figurante 3D", "maquina")
    assert http.get("/api/erp/historico/articulos-clase",
                    headers=auth_headers(http, "user")).json()["items"][0]["codart"] == SENSOR
    assert http.put(url, json={"clase": "nada"},
                    headers=auth_headers(http, "admin")).status_code == 422
    assert http.put("/api/erp/historico/articulos-clase/NOEXISTE", json={"clase": "otro"},
                    headers=auth_headers(http, "admin")).status_code == 404
    assert http.put(url, json={"clase": None},
                    headers=auth_headers(http, "admin")).json()["items"] == []


def test_la_ficha_dice_de_donde_sale_cada_linea(session_factory) -> None:  # noqa: F811
    from app.erp.historico_documento import ficha

    _parque_hexa(session_factory, "proforma")
    with session_factory() as s:
        fac = ficha(s, tipo="factura", ejercicio="2022", serie="1", codigo=123021, user=None,
                    en_curso="2026")
        alb = ficha(s, tipo="albaran", ejercicio="2023", serie="3", codigo=230001, user=None,
                    en_curso="2026")
    assert [ln["viene_de"] for ln in fac["lineas"]] == ["Proforma 1-000500", None]
    assert [ln["viene_de"] for ln in alb["lineas"]] == ["Proforma 1-000500"]



# --- lo que encontró la revisión (emparejado) -----------------------------------------


def _empresa_hexa(s) -> Company:
    from app.erp import historico_clasificacion as cl

    cl.clasificar(s, "LAS", {"clase": "maquina"}, por="x")
    empresa = Company(name="Grabados Figurante", factusol_company_id=CLI_HEXA)
    s.add(empresa)
    s.commit()
    return empresa


def test_un_albaran_con_estalb_1_heredado_y_sin_factura_si_cuenta(session_factory) -> None:  # noqa: F811
    # Con el enlace manda el emparejado: el «Facturado» puede venir de la proforma.
    d = _datos_hexa(enlace="proforma")
    d["2022"]["F_LFA"][0]["ARTLFA"] = SENSOR      # la factura ya no es de la Hexa
    d["2023"]["F_ALB"][0]["ESTALB"] = "1"
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026", "2023", "2022"], en_curso="2026")
        hexas = _hexas(parque.parque(s, _empresa_hexa(s)))
    assert [m["origen"] for m in hexas] == ["albaran"]


def test_un_anio_sin_recargar_sigue_la_regla_de_antes(session_factory) -> None:  # noqa: F811
    d = _datos_hexa(enlace="ninguno")
    d["2023"]["F_ALB"][0]["ESTALB"] = "1"
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026", "2023", "2022"], en_curso="2026")
        empresa = _empresa_hexa(s)
        assert len(_hexas(parque.parque(s, empresa))) == 2   # recargado: sale, con aviso
        s.get(HistoricoEjercicio, "2023").con_enlaces = False
        s.commit()
        assert [m["origen"] for m in _hexas(parque.parque(s, empresa))] == ["factura"]


def test_una_proforma_de_dos_maquinas_con_dos_entregas_son_dos(session_factory) -> None:  # noqa: F811
    d = _datos_hexa(enlace="proforma")
    d["2022"]["F_PRE"] = [{"TIPPRE": "1", "CODPRE": 500, "FECPRE": "2022-11-01T00:00:00",
                           "CLIPRE": CLI_HEXA, "CNOPRE": "Grabados Figurante"}]
    d["2022"]["F_LPS"] = [{"TIPLPS": "1", "CODLPS": 500, "POSLPS": 1, "ARTLPS": HEXA,
                           "DESLPS": "HEXA 60W", "CANLPS": 2, "PRELPS": 9000}]
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026", "2023", "2022"], en_curso="2026")
        empresa = _empresa_hexa(s)
        assert len(_hexas(parque.parque(s, empresa))) == 2
    # Si la proforma era de una, son la misma venta.
    d["2022"]["F_LPS"][0]["CANLPS"] = 1
    with session_factory() as s:
        historico.cargar_ejercicio(s, FactusolFalso(d), "2022")
        assert len(_hexas(parque.parque(s, s.get(Company, empresa.id)))) == 1


def test_avisa_aunque_el_albaran_venga_de_una_proforma(session_factory) -> None:  # noqa: F811
    # Albarán desde la proforma; la factura, tecleada a mano días antes.
    d = _datos_hexa(enlace="proforma")
    for k in ("DOCLFA", "DTPLFA", "DCOLFA"):
        d["2022"]["F_LFA"][0].pop(k)
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(d), ["2026", "2023", "2022"], en_curso="2026")
        hexas = _hexas(parque.parque(s, _empresa_hexa(s)))
    alb = next(m for m in hexas if m["origen"] == "albaran")
    assert alb["posible_misma_venta"]["numero"] == "1-123021"


def test_la_baja_del_albaran_pasa_a_su_factura_al_emparejarlos(session_factory) -> None:  # noqa: F811
    with session_factory() as s:
        # Antes de emparejar (año sin recargar), se dio de baja el albarán.
        historico.cargar(s, FactusolFalso(_datos_hexa(enlace="ninguno")),
                         ["2026", "2023", "2022"], en_curso="2026")
        empresa = _empresa_hexa(s)
        alb = next(m for m in _hexas(parque.parque(s, empresa)) if m["origen"] == "albaran")
        parque.dar_baja(s, empresa, clave=alb["clave"], motivo="Retirada", por="x")
        # Ya con el enlace: una sola máquina, la factura, y sigue de baja.
        historico.cargar(s, FactusolFalso(_datos_hexa(enlace="proforma")),
                         ["2023", "2022"], en_curso="2026")
        hexas = _hexas(parque.parque(s, empresa))
        assert [(m["origen"], m["baja"]["motivo"]) for m in hexas] == [("factura", "Retirada")]
        # Y se deshace desde la fila de la factura.
        parque.reactivar(s, empresa, clave=hexas[0]["clave"])
        assert _hexas(parque.parque(s, empresa))[0]["baja"] is None
