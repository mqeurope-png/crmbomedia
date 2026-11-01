"""El análisis del parque de una empresa (1/11/2026). La IA está simulada
(`llm._invoke_claude`): nada sale a la red. Figurantes: ni clientes ni
centros reales.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.erp import historico
from app.erp import historico_clasificacion as cl
from app.erp import historico_maquinas as hm
from app.models.analisis_parque import AnalisisEmpresa
from app.models.crm import Company
from app.models.ia import LlamadaIA
from app.services import llm
from app.services.analisis_parque import hechos as hx
from app.services.analisis_parque import ia, servicio
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
COMPRA, OTRO, SIN_NADA = "4901", "4902", "4904"
LASER, FILTRO, FILTRO_NUEVO, FILTRO_VIEJO, PIEZA, UV, TINTA, CAJON = (
    "LAS100", "FIL200", "FIL300", "FIL400", "PZA500", "UVM600", "TIN700", "CAJ800")
CAT = [_art(LASER, "LASER FIGURANTE 40W", "021"),
       _art(FILTRO, "Filtro figurante A", "251"),
       _art(FILTRO_NUEVO, "Filtro figurante B", "251"),
       _art(FILTRO_VIEJO, "Filtro figurante descatalogado", "251"),
       _art(PIEZA, "Pieza figurante", "211"),
       _art(UV, "IMPRESORA UV FIGURANTE", "UVM"),
       _art(TINTA, "Tinta UV figurante", "UV"),
       _art(CAJON, "Consumible figurante sin gama", "SIN")]
FAM = [{"CODFAM": c, "DESFAM": d} for c, d in (
    ("021", "FLUX"), ("251", "Filtros"), ("211", "Recambios láser"), ("UVM", "Impresoras UV"),
    ("UV", "Consumible UV"), ("SIN", "Cajón de sastre"))]


def _anio(ventas: list[tuple[int, str, str, str, float]]) -> dict[str, Any]:
    return {"F_FAC": [_fac(c, cli, "Cliente Figurante", f"{fecha}T00:00:00")
                      for c, cli, fecha, _a, _p in ventas],
            "F_LFA": [_lfa(c, 1, a, a, 1, p) for c, _cli, _f, a, p in ventas],
            "F_ALB": [], "F_LAL": [], "F_ART": CAT, "F_FAM": FAM}


def _datos() -> dict[str, dict[str, Any]]:
    # COMPRA: un láser en 2021 y filtros cada ~4 meses hasta 03/2022; nada desde.
    # OTRO: compra el filtro B y el consumible sin gama en 2026, y el descatalogado en 2019.
    return {
        "2019": _anio([(190001, OTRO, "2019-05-01", FILTRO_VIEJO, 50)]),
        "2021": _anio([(210001, COMPRA, "2021-01-15", LASER, 4000),
                       (210002, COMPRA, "2021-03-01", FILTRO, 100),
                       (210003, COMPRA, "2021-07-01", FILTRO, 100),
                       (210004, COMPRA, "2021-11-01", FILTRO, 100)]),
        "2022": _anio([(220001, COMPRA, "2022-03-01", FILTRO, 100)]),
        "2026": _anio([(260001, OTRO, "2026-05-01", FILTRO_NUEVO, 90),
                       (260002, OTRO, "2026-06-01", CAJON, 30)]),
    }


def _preparar(session_factory) -> dict[str, str]:  # noqa: F811
    datos = _datos()
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(datos), list(datos), en_curso="2026")
        for g in ("Láser", "UV LED"):
            if not any(x["nombre"] == g for x in cl.gamas(s)):
                cl.crear_gama(s, g)
        for codigo, clase, gama in (("021", "maquina", "laser"), ("UVM", "maquina", "uv-led"),
                                    ("251", "consumible", "laser"),
                                    ("211", "recambio", "laser"),
                                    ("UV", "consumible", "uv-led"),
                                    ("SIN", "consumible", None)):
            cl.clasificar(s, codigo, {"clase": clase, "gama": gama}, por="bart")
        hm.marcar(s, LASER, True, por="bart")
        hm.marcar(s, UV, True, por="bart")
        ids = {}
        for nombre, cli in (("compra", COMPRA), ("sin_nada", SIN_NADA), ("sin_cliente", None)):
            e = Company(name=f"Cliente Figurante {nombre}", factusol_company_id=cli)
            s.add(e)
            s.flush()
            ids[nombre] = e.id
        s.commit()
    return ids


class _IA:
    """Sustituye a `llm._invoke_claude`: cuenta las llamadas, guarda lo que
    se le manda y devuelve lo que se le diga."""

    def __init__(self, respuesta: dict[str, Any] | None = None) -> None:
        self.llamadas: list[dict[str, Any]] = []
        self.respuesta = respuesta

    def __call__(self, **kw: Any) -> str:
        self.llamadas.append(kw)
        llm.registrar_uso(llm.Uso(modelo=kw["model"], tokens_entrada=3000, tokens_salida=400))
        if self.respuesta is not None:
            return json.dumps(self.respuesta)
        hechos = json.loads(kw["user_prompt"].split("\n", 1)[1])["hechos"]
        cambio = next(h["id"] for h in hechos if h["bloque"] == "cambio")
        return json.dumps({
            "lectura": [{"texto": "Dejó de comprar filtros.", "citas": [cambio]}],
            "entrada": {"texto": "Preguntarle si sigue usando el láser y ofrecerle el filtro B.",
                        "citas": [cambio], "productos": ["P1"]},
        })


@pytest.fixture
def con_clave(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _hechos(s, company_id: str, **cfg) -> hx.Hechos:
    return hx.calcular(s, s.get(Company, company_id), hoy=HOY, cfg=cfg or None)


def test_los_hechos_calculados_con_su_documento_o_su_fecha(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        h = _hechos(s, ids["compra"])
        assert h.estado == hx.CON_DATOS
        bloques = {b: [x.texto for x in h.hechos if x.bloque == b] for b in hx.BLOQUES}
        assert any(LASER in t and "Láser" in t for t in bloques[hx.TIENE])
        assert any(FILTRO in t and "4 veces" in t for t in bloques[hx.CONSUME])
        assert any("cada 4 meses" in t for t in bloques[hx.CONSUME])
        assert any("lleva 56 sin pedir" in t for t in bloques[hx.CAMBIO])
        # Cada afirmación lleva su documento o su fecha.
        for x in h.hechos:
            assert x.fuente and (x.fuente.get("doc") or x.fuente.get("fecha")), x.texto
        doc = next(x for x in h.hechos if x.bloque == hx.CAMBIO).fuente["doc"]
        assert (doc["tipo"], doc["ejercicio"], doc["codigo"]) == ("factura", "2022", 220001)


def test_lo_que_no_compra_sale_del_catalogo_real_y_de_su_gama(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        h = _hechos(s, ids["compra"])
        # El filtro B: consumible de láser, vendido a otro este año, nunca a él.
        # No el A (ya lo compra), ni el descatalogado (2019), ni el del cajón de
        # sastre (sin gama), ni la tinta UV (no tiene UV).
        assert [p.codart for p in h.productos] == [FILTRO_NUEVO]
        assert h.productos[0].gama == "laser"


def test_lo_que_no_se_sabe_se_dice(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        h = _hechos(s, ids["compra"], anios_sin_confirmar=4)
        aviso = next(x for x in h.hechos if x.bloque == hx.NO_SE_SABE)
        assert "nadie la ha dado de baja" in aviso.texto and "No se sabe" in aviso.texto


def test_sin_cliente_de_factusol_no_se_inventa_nada(http, session_factory, con_clave) -> None:  # noqa: F811,E501
    ids = _preparar(session_factory)
    falso = _IA()
    with patch.object(llm, "_invoke_claude", falso):
        for clave, datos in (("sin_cliente", hx.SIN_CLIENTE), ("sin_nada", hx.SIN_DATOS)):
            r = http.post(f"/api/companies/{ids[clave]}/analisis-parque/generar",
                          headers=auth_headers(http, "admin"))
            assert r.status_code == 200, r.text
            assert r.json()["datos"] == datos and r.json()["analisis"] is None
    assert falso.llamadas == []                                  # ni se llama a la IA


def test_se_guarda_y_no_se_regenera_si_nada_cambia(session_factory, con_clave) -> None:  # noqa: F811,E501
    ids = _preparar(session_factory)
    falso = _IA()
    with session_factory() as s, patch.object(llm, "_invoke_claude", falso):
        empresa = s.get(Company, ids["compra"])
        r = servicio.generar(s, empresa, por="test", hoy=HOY)
        assert r["estado"] == servicio.AL_DIA and r["analisis"]["entrada"]["productos"][0][
            "codart"] == FILTRO_NUEVO
        servicio.generar(s, empresa, por="test", hoy=HOY)
        assert len(falso.llamadas) == 1                          # guardado: no se repite
        assert s.scalar(select(LlamadaIA.proposito)) == "analisis_parque"
        fila = s.get(AnalisisEmpresa, empresa.id)
        assert fila.coste_usd and fila.tokens_entrada == 3000
        # «Volver a generar» sí llama.
        servicio.generar(s, empresa, por="test", hoy=HOY, forzar=True)
        assert len(falso.llamadas) == 2
        # Caducado: con más meses que la caducidad, toca regenerar.
        fila.generado_at = fila.generado_at - timedelta(days=200)
        s.commit()
        assert servicio.leer(s, empresa, hoy=HOY)["estado"] == servicio.CADUCADO


def test_una_compra_nueva_lo_marca_para_regenerar(session_factory, con_clave) -> None:  # noqa: F811,E501
    ids = _preparar(session_factory)
    with session_factory() as s, patch.object(llm, "_invoke_claude", _IA()):
        empresa = s.get(Company, ids["compra"])
        servicio.generar(s, empresa, por="test", hoy=HOY)
        assert servicio.leer(s, empresa, hoy=HOY)["estado"] == servicio.AL_DIA
        datos = _datos()
        datos["2026"]["F_FAC"].append(_fac(260003, COMPRA, "Cliente Figurante",
                                           "2026-10-01T00:00:00"))
        datos["2026"]["F_LFA"].append(_lfa(260003, 1, FILTRO, FILTRO, 1, 100))
        historico.cargar(s, FactusolFalso(datos), ["2026"], en_curso="2026")
        assert servicio.leer(s, empresa, hoy=HOY)["estado"] == servicio.DESACTUALIZADO


def test_lo_que_no_cita_un_dato_se_descarta(session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        h = _hechos(s, ids["compra"])
        cambio = next(x.id for x in h.hechos if x.bloque == hx.CAMBIO)
        aviso = [x.id for x in h.hechos if x.bloque == hx.NO_SE_SABE]
        catalogo = servicio._codigos_catalogo(s)
        r = ia.validar({"lectura": [
            {"texto": "Compra filtros con regularidad.", "citas": [cambio]},
            {"texto": "Es un cliente estupendo.", "citas": []},          # sin cita
            {"texto": "Inventada.", "citas": ["D99"]},                     # cita que no existe
            {"texto": f"Le iría bien la {TINTA}.", "citas": [cambio]},     # artículo ajeno
        ], "entrada": {"texto": "Ofrecerle una oferta.", "citas": aviso}}, h,
            codigos_catalogo=catalogo)
        assert [x["texto"] for x in r.lectura] == ["Compra filtros con regularidad."]
        assert r.entrada is None and len(r.descartado) == 4
        # Un producto que no está en la lista: fuera.
        r = ia.validar({"lectura": [], "entrada": {"texto": "Ofrecerle el filtro.",
                                                   "citas": [cambio], "productos": ["P9"]}}, h,
                       codigos_catalogo=catalogo)
        assert r.entrada is None and "no está en la lista" in r.descartado[0]
        # Anclado en un dato calculado y con un producto de la lista: se queda.
        r = ia.validar({"entrada": {"texto": f"Ofrecerle el {FILTRO_NUEVO}.",
                                    "citas": [cambio], "productos": ["P1"]}}, h,
                       codigos_catalogo=catalogo)
        assert r.entrada and r.entrada["productos"][0]["codart"] == FILTRO_NUEVO


def test_a_la_ia_no_le_llegan_nombres(session_factory, con_clave) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    falso = _IA()
    with session_factory() as s, patch.object(llm, "_invoke_claude", falso):
        servicio.generar(s, s.get(Company, ids["compra"]), por="test", hoy=HOY)
    enviado = falso.llamadas[0]["user_prompt"]
    assert "Cliente Figurante" not in enviado and COMPRA not in enviado
    assert "220001" not in enviado                       # ni los números de documento


def test_permisos_y_sin_clave(http, session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    url = f"/api/companies/{ids['compra']}/analisis-parque"
    # Solo administradores mientras se prueba.
    assert http.get(url, headers=auth_headers(http, "user")).status_code == 403
    r = http.get(url, headers=auth_headers(http, "admin"))
    assert r.status_code == 200 and r.json()["estado"] == servicio.SIN_GENERAR
    # Sin clave de la IA: se dice y no se guarda nada.
    r = http.post(f"{url}/generar", headers=auth_headers(http, "admin"))
    assert r.status_code == 503
    with session_factory() as s:
        assert s.get(AnalisisEmpresa, ids["compra"]) is None


def test_el_script_compara_modelos_sin_guardar(session_factory, con_clave, monkeypatch,  # noqa: F811
                                               capsys, tmp_path) -> None:
    from scripts import comparar_analisis_parque as script

    ids = _preparar(session_factory)
    engine = session_factory.kw["bind"]
    monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
    falso = _IA()
    salida = tmp_path / "comparar.md"
    with patch.object(llm, "_invoke_claude", falso):
        assert script.main(["--cliente", COMPRA, "--cliente", SIN_NADA,
                            "--salida", str(salida)]) == 0
    texto = capsys.readouterr().out
    assert [k["model"] for k in falso.llamadas] == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    assert "### claude-sonnet-5-5" in texto and "### claude-haiku-5-5" in texto
    assert "Por dónde entrar:** Preguntarle" in texto and "Sin datos" in texto
    assert "Cliente Figurante" not in texto and salida.read_text().startswith("\n## Cliente")
    with session_factory() as s:
        assert s.get(AnalisisEmpresa, ids["compra"]) is None          # no guarda el análisis


def test_la_configuracion_se_cambia_sin_desplegar(http, session_factory) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    h = auth_headers(http, "admin")
    cfg = http.get("/api/erp/settings", headers=h).json()["analisis_parque"]
    assert cfg == {"modelo": "claude-sonnet-5-5", "caducidad_meses": 3,
                   "anios_sin_confirmar": 6, "meses_catalogo": 24, "solo_admin": True}
    r = http.patch("/api/erp/settings", headers=h, json={"analisis_parque": {
        "modelo": "claude-haiku-5-5", "solo_admin": False}})
    assert r.status_code == 200, r.text
    assert http.get("/api/erp/settings", headers=h).json()["analisis_parque"]["modelo"] == \
        "claude-haiku-5-5"
    # Sin «solo administradores», quien ve el parque lo ve.
    url = f"/api/companies/{ids['compra']}/analisis-parque"
    assert http.get(url, headers=auth_headers(http, "user")).status_code == 200
    # Y el comercial (rol solo-ERP), que es quien va a llamar al cliente.
    assert http.get(url, headers=auth_headers(http, "comercial")).status_code == 200
    assert http.get("/api/analisis-parque/permiso",
                    headers=auth_headers(http, "user")).json() == {"puede": True,
                                                                   "solo_admin": False}
    r = http.patch("/api/erp/settings", headers=h, json={"analisis_parque": {"modelo": "x y"}})
    assert r.status_code == 400


def test_al_fusionar_empresas_el_analisis_de_la_absorbida_se_borra(session_factory, con_clave) -> None:  # noqa: F811,E501
    from app.services import company_dedupe

    ids = _preparar(session_factory)
    with session_factory() as s, patch.object(llm, "_invoke_claude", _IA()):
        absorbida = s.get(Company, ids["compra"])
        servicio.generar(s, absorbida, por="test", hoy=HOY)
        keep = s.get(Company, ids["sin_nada"])
        company_dedupe.merge_companies_into(s, keep, [absorbida])
        s.commit()
        assert s.get(AnalisisEmpresa, ids["compra"]) is None



def test_la_huella_no_cambia_por_lo_que_compran_otros(session_factory, con_clave) -> None:  # noqa: F811,E501
    ids = _preparar(session_factory)
    with session_factory() as s, patch.object(llm, "_invoke_claude", _IA()):
        empresa = s.get(Company, ids["compra"])
        servicio.generar(s, empresa, por="test", hoy=HOY)
        # Otro cliente compra mucho de otro filtro: cambia el «más vendido», no lo suyo.
        datos = _datos()
        for i in range(3):
            datos["2026"]["F_FAC"].append(_fac(260010 + i, OTRO, "Cliente Figurante",
                                               "2026-09-01T00:00:00"))
            datos["2026"]["F_LFA"].append(_lfa(260010 + i, 1, FILTRO_VIEJO, FILTRO_VIEJO, 1, 50))
        historico.cargar(s, FactusolFalso(datos), ["2026"], en_curso="2026")
        assert servicio.leer(s, empresa, hoy=HOY)["estado"] == servicio.AL_DIA


def test_no_dice_nunca_ha_comprado_lo_que_compro_con_otra_familia(session_factory) -> None:  # noqa: F811,E501
    ids = _preparar(session_factory)
    datos = _datos()
    # En 2019 el filtro B estaba en el cajón de sastre (sin gama) y él lo compró.
    cat_2019 = [a if a["CODART"] != FILTRO_NUEVO else {**a, "FAMART": "SIN"} for a in CAT]
    datos["2019"]["F_ART"] = cat_2019
    datos["2019"]["F_FAC"].append(_fac(190002, COMPRA, "Cliente Figurante", "2019-06-01T00:00:00"))
    datos["2019"]["F_LFA"].append(_lfa(190002, 1, FILTRO_NUEVO, FILTRO_NUEVO, 1, 90))
    with session_factory() as s:
        historico.cargar(s, FactusolFalso(datos), ["2019"], en_curso="2026")
        h = _hechos(s, ids["compra"])
        assert FILTRO_NUEVO not in [p.codart for p in h.productos]


def test_respuestas_raras_de_la_ia(http, session_factory, con_clave) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    with session_factory() as s:
        h = _hechos(s, ids["compra"])
        cambio = next(x.id for x in h.hechos if x.bloque == hx.CAMBIO)
        catalogo = servicio._codigos_catalogo(s) | {"61403"}
        # «lectura» que no es una lista: no rompe.
        r = ia.validar({"lectura": {"texto": "x", "citas": [cambio]}}, h,
                       codigos_catalogo=catalogo)
        assert r.lectura == []
        # Una cita real y otra inventada: la frase entera fuera.
        r = ia.validar({"lectura": [{"texto": "Mezcla.", "citas": [cambio, "D99"]}]}, h,
                       codigos_catalogo=catalogo)
        assert r.lectura == [] and r.descartado
        # Un código solo numérico del catálogo que no es suyo: fuera; un año, no.
        r = ia.validar({"entrada": {"texto": "Ofrecerle el 61403 antes de 2027.",
                                    "citas": [cambio]}}, h, codigos_catalogo=catalogo)
        assert r.entrada is None and "61403" in r.descartado[0]
    # Una respuesta que no es JSON: no se guarda nada, pero lo que costó sí se anota.
    with patch.object(llm, "_invoke_claude", lambda **kw: (
            llm.registrar_uso(llm.Uso(modelo=kw["model"], tokens_entrada=10, tokens_salida=5))
            or "no es json")):
        r = http.post(f"/api/companies/{ids['compra']}/analisis-parque/generar",
                      headers=auth_headers(http, "admin"))
    assert r.status_code == 502
    with session_factory() as s:
        assert s.get(AnalisisEmpresa, ids["compra"]) is None
        assert s.scalar(select(LlamadaIA.ok)) is False


def test_el_lote_sigue_si_una_empresa_falla(session_factory, monkeypatch) -> None:  # noqa: F811
    ids = _preparar(session_factory)
    engine = session_factory.kw["bind"]
    monkeypatch.setattr("app.db.session.get_engine", lambda: engine)

    def falla(*a, **kw):
        raise RuntimeError("roto")

    monkeypatch.setattr(servicio, "generar", falla)
    r = servicio.generar_lote([ids["compra"], ids["sin_nada"], "no-existe"], por="test")
    assert r == {"generados": 0, "al_dia": 0, "sin_datos": 1, "fallidos": 1}
