"""Seguimiento (app) — dos filas con el mismo id se FUSIONAN, no se descartan.

El 24/09, al deduplicar la hoja, el espejo se quedó con la fila de BoHub y tiró
la gemela del histórico: 40 trackings y 12 fechas de recogida que solo estaban
en la gemela desaparecieron. Ahora, cuando el espejo quita una fila por repetir
el id de otra (la gemela del histórico de un pedido que ya sale arriba, o una
copia en la zona viva), lo que la fila que se queda no tiene se conserva (en
BoHub y en la hoja) y, si las dos tienen valor distinto, gana la que se queda
y la otra queda en la auditoría. Y el script de recuperación devuelve las
fechas perdidas sin pisar ninguna que ya exista.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.erp.drive_managed import SEPARATOR_PEDIDOS
from app.erp.models import Order, SeguimientoLegacy, SeguimientoOverride, SeguimientoSyncMeta
from app.erp.seguimiento import (
    RECOGIDO_HOJA_KEY,
    SEGUIMIENTO_COLUMNS_V2,
    fecha_recogido,
)
from app.erp.seguimiento_mirror import FORMATO_BD_ACTUAL, FUSION_EVENT, META_FORMATO_BD
from app.erp.seguimiento_recogido import (
    IGUAL,
    NO_ES_FECHA,
    RECUPERADA_EVENT,
    RELLENAR,
    YA_TIENE,
    aplicar,
    planificar,
)
from app.models.crm import AuditLog
from tests.test_erp_seguimiento_espejo import (
    HISTORICA,
    ID,
    TAB,
    FakeTabs,
    _c,
    _fila,
    _order,
    _pasada,
    _row,
)


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    with maker() as s:
        # Lo guardado ya está en el formato actual (como en producción tras #500).
        s.add(SeguimientoSyncMeta(clave=META_FORMATO_BD, valor=str(FORMATO_BD_ACTUAL)))
        s.commit()
    yield maker
    Base.metadata.drop_all(engine)


def _gemela(numero: str, cliente: str, **celdas: Any) -> list[Any]:
    """Fila del histórico (de antes de la app) con sus celdas tecleadas a mano."""
    fila: list[Any] = [""] * (len(SEGUIMIENTO_COLUMNS_V2) - 1)
    fila[0], fila[_c("Nº pedido")], fila[_c("Cliente")] = "Histórico", numero, cliente
    for nombre, valor in celdas.items():
        fila[_c(nombre)] = valor
    return fila


def _con_gemela(s: Session, o: Order, gemela: list[Any]) -> FakeTabs:
    """El pedido vivo y su gemela del histórico casada `confirmed` (backfill)."""
    s.add(SeguimientoLegacy(row_index=0, numero_raw=o.order_number,
                            cliente_raw=gemela[_c("Cliente")], raw_json=json.dumps(gemela),
                            match_status="confirmed", matched_order_id=o.id))
    s.commit()
    return FakeTabs({HISTORICA: [], TAB: [
        list(SEGUIMIENTO_COLUMNS_V2), [SEPARATOR_PEDIDOS], list(gemela),
    ]})


def _auditoria(s: Session, accion: str, order_id: str) -> list[dict[str, Any]]:
    return [json.loads(a.metadata_json or "{}") for a in s.scalars(
        select(AuditLog).where(AuditLog.action == accion, AuditLog.target_id == order_id))]


def test_tracking_en_una_y_fecha_recogido_en_otra_quedan_las_dos(factory):
    with factory() as s:
        o = _order(s, "BOP-200", tracking_number="EXT-1")
        sheets = _con_gemela(s, o, _gemela(
            "BOP-200", "Acme SL", **{"Fecha recogido": "24/09/2026"}))
        res = _pasada(s, sheets, [_row(o, tracking="EXT-1")])

        con_ese_id = [f for f in sheets.tabs[TAB] if len(f) > ID and f[ID] == o.id]
        assert len(con_ese_id) == 1                       # una sola fila
        fila = con_ese_id[0]
        assert fila[_c("Tracking")] == "EXT-1"            # el de BoHub
        assert str(fila[_c("Fecha recogido")]) != ""      # y la fecha de la gemela
        s.refresh(o)
        assert json.loads(o.packing_json)[RECOGIDO_HOJA_KEY] == "2026-09-24"
        assert fecha_recogido(o) == "2026-09-24"          # pantalla / Excel / hoja
        assert res["espejo"]["filas_fusionadas"] == ["BOP-200"]
        assert res["espejo"]["valores_rellenados"] == 1
        (rastro,) = _auditoria(s, FUSION_EVENT, o.id)
        assert rastro["conservado"] == {"Fecha recogido": "2026-09-24"}
        assert rastro["origen"] == "fila del histórico"

        # La gemela ya no está: la pasada siguiente no vuelve a fusionar nada.
        res = _pasada(s, sheets, [_row(o, tracking="EXT-1", recogido="2026-09-24")])
        assert res["espejo"]["filas_fusionadas"] == []
        assert len(_auditoria(s, FUSION_EVENT, o.id)) == 1


def test_el_tracking_y_el_nserie_de_la_gemela_van_al_pedido(factory):
    with factory() as s:
        o = _order(s, "BOP-201")
        sheets = _con_gemela(s, o, _gemela(
            "BOP-201", "Acme SL", Tracking="CTT-555", **{"Nº serie · WhiteRIP": "WR-9"}))
        _pasada(s, sheets, [_row(o)])
        s.refresh(o)
        assert o.tracking_number == "CTT-555"
        ov = s.scalars(select(SeguimientoOverride)).one()
        assert (ov.column_key, ov.value) == ("serie_whiterip", "WR-9")
        fila = _fila(sheets, o.id)
        assert (fila[_c("Tracking")], fila[_c("Nº serie · WhiteRIP")]) == ("CTT-555", "WR-9")


def test_valores_distintos_gana_bohub_y_el_otro_queda_en_la_auditoria(factory):
    with factory() as s:
        o = _order(s, "BOP-202", tracking_number="EXT-1")
        sheets = _con_gemela(s, o, _gemela(
            "BOP-202", "Acme Iberia", Tracking="OTRO-9", **{"Nota / Incidencia": "llamar"}))
        res = _pasada(s, sheets, [_row(o, tracking="EXT-1")])
        s.refresh(o)
        assert o.tracking_number == "EXT-1"                # BoHub no cambia
        fila = _fila(sheets, o.id)
        assert fila[_c("Cliente")] == "Acme SL" and fila[_c("Tracking")] == "EXT-1"
        assert fila[_c("Nota / Incidencia")] == "llamar"   # vacía en BoHub: se conserva
        assert res["espejo"]["valores_en_conflicto"] == 2
        (rastro,) = _auditoria(s, FUSION_EVENT, o.id)
        assert rastro["descartado"] == {
            "Cliente": {"descartado": "Acme Iberia", "se_queda": "Acme SL"},
            "Tracking": {"descartado": "OTRO-9", "se_queda": "EXT-1"},
        }
        assert rastro["conservado"] == {"Nota / Incidencia": "llamar"}


def test_con_envio_genei_el_tracking_de_la_gemela_no_entra(factory):
    with factory() as s:
        o = _order(s, "BOP-203", packing_json=json.dumps({"genei": {"shipment_code": "GE1"}}))
        sheets = _con_gemela(s, o, _gemela("BOP-203", "Acme SL", Tracking="A-MANO"))
        _pasada(s, sheets, [_row(o, envio_genei=True)])
        s.refresh(o)
        assert not o.tracking_number                       # con Genei manda Genei
        (rastro,) = _auditoria(s, FUSION_EVENT, o.id)
        assert rastro["descartado"]["Tracking"]["descartado"] == "A-MANO"


def test_una_copia_en_la_zona_viva_se_fusiona(factory):
    """Alguien copia la fila de un pedido y escribe en la copia: se conserva lo
    que escribió; lo que la copia trae igual que BoHub no cuenta."""
    with factory() as s:
        o = _order(s, "BOP-204", tracking_number="EXT-1")
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [_row(o, tracking="EXT-1")])
        filas = sheets.tabs[TAB]
        i = next(n for n, f in enumerate(filas) if len(f) > ID and f[ID] == o.id)
        copia = list(filas[i])
        copia[_c("Nº serie · WhiteRIP")] = "WR-77"
        filas.insert(i + 1, copia)
        res = _pasada(s, sheets, [_row(o, tracking="EXT-1")])
        con_ese_id = [f for f in sheets.tabs[TAB] if len(f) > ID and f[ID] == o.id]
        assert len(con_ese_id) == 1
        assert con_ese_id[0][_c("Nº serie · WhiteRIP")] == "WR-77"
        assert res["espejo"]["valores_en_conflicto"] == 0
        (rastro,) = _auditoria(s, FUSION_EVENT, o.id)
        assert rastro["origen"] == "fila repetida en la zona viva"


def test_la_vista_previa_ensena_la_fusion_sin_escribir(factory):
    with factory() as s:
        o = _order(s, "BOP-205")
        sheets = _con_gemela(s, o, _gemela("BOP-205", "Acme SL", Tracking="CTT-1"))
        from app.erp.drive_managed import push_managed_tabs

        res = push_managed_tabs(s, sheets, [_row(o)], completados=[], dry_run=True)
        s.rollback()
        assert res["espejo"]["filas_fusionadas"] == ["BOP-205"]
        s.refresh(o)
        assert not o.tracking_number
        assert _auditoria(s, FUSION_EVENT, o.id) == []
        assert TAB not in sheets.written                   # la hoja, sin tocar


# --- recuperar las fechas de recogida del 24/09 --------------------------------


def _legacy(s: Session, o: Order, recogido: Any, idx: int) -> None:
    s.add(SeguimientoLegacy(
        row_index=idx, numero_raw=o.order_number, cliente_raw="Acme SL",
        raw_json=json.dumps(_gemela(o.order_number, "Acme SL",
                                    **{"Fecha recogido": recogido})),
        match_status="confirmed", matched_order_id=o.id))


def test_la_recuperacion_no_pisa_una_fecha_que_ya_existe(factory):
    with factory() as s:
        sin_fecha = _order(s, "BOP-300")
        con_fecha = _order(s, "BOP-301", packing_json=json.dumps(
            {RECOGIDO_HOJA_KEY: "2026-09-20"}))
        rota = _order(s, "BOP-302")
        fuera = _order(s, "BOP-303")                       # no sale arriba
        _legacy(s, sin_fecha, "24/09/2026", 0)
        _legacy(s, con_fecha, "23/09/2026", 1)
        _legacy(s, rota, "24/09/202", 2)
        _legacy(s, fuera, "24/09/2026", 3)
        s.commit()
        pintados = {sin_fecha.id, con_fecha.id, rota.id}

        casos = {c.order_number: c for c in planificar(s, pintados)}
        assert set(casos) == {"BOP-300", "BOP-301", "BOP-302"}
        assert casos["BOP-300"].accion == RELLENAR and casos["BOP-300"].fecha_hoja == "2026-09-24"
        assert casos["BOP-301"].accion == YA_TIENE
        assert casos["BOP-301"].fecha_pedido == "2026-09-20"
        assert casos["BOP-302"].accion == NO_ES_FECHA

        hechos = aplicar(s, list(casos.values()), actor_email="test")
        s.commit()
        assert [c.order_number for c in hechos] == ["BOP-300"]
        s.refresh(sin_fecha)
        s.refresh(con_fecha)
        assert fecha_recogido(sin_fecha) == "2026-09-24"
        assert fecha_recogido(con_fecha) == "2026-09-20"   # intacta
        (rastro,) = _auditoria(s, RECUPERADA_EVENT, sin_fecha.id)
        assert rastro["fecha_recogido"] == "2026-09-24"
        assert _auditoria(s, RECUPERADA_EVENT, con_fecha.id) == []

        # Otra vez: ya coincide, nada que hacer.
        again = {c.order_number: c for c in planificar(s, pintados)}
        assert again["BOP-300"].accion == IGUAL
        assert aplicar(s, list(again.values()), actor_email="test") == []


# --- arreglos de la revisión ------------------------------------------------------


def test_un_numero_que_no_es_fecha_no_rompe_la_pasada(factory):
    """Un nº grande en «Fecha recogido» de la gemela no desborda (antes,
    OverflowError y ninguna pasada salía): no es una fecha, queda en la
    auditoría y la hoja se escribe."""
    with factory() as s:
        o = _order(s, "BOP-210")
        sheets = _con_gemela(s, o, _gemela("BOP-210", "Acme SL",
                                           **{"Fecha recogido": 1234567890}))
        _pasada(s, sheets, [_row(o)])
        s.refresh(o)
        assert fecha_recogido(o) is None
        (rastro,) = _auditoria(s, FUSION_EVENT, o.id)
        assert rastro["descartado"]["Fecha recogido"]["descartado"] == "1234567890"


def test_factura_enviada_que_no_es_fecha_se_conserva_marcada(factory):
    with factory() as s:
        o = _order(s, "BOP-211")
        sheets = _con_gemela(s, o, _gemela("BOP-211", "Acme SL",
                                           **{"Factura enviada": "sí, por email",
                                              "Nota / Incidencia": "llamar"}))
        _pasada(s, sheets, [_row(o)])
        ov = {v.column_key: v.value for v in s.scalars(select(SeguimientoOverride))}
        assert ov["factura_enviada"] == "sí, por email"
        assert ov["nota_incidencia"] == "llamar"
        nota = _fila(sheets, o.id)[_c("Nota / Incidencia")]
        assert nota.startswith("llamar") and "⚠ revisar" in nota


def test_en_una_fila_manual_casada_la_nota_se_conserva_con_sus_marcas(factory):
    """La Nota de la fila manual casada solo lleva marcas de la app: eso no es
    un valor; la de la gemela se conserva delante y las marcas se quedan."""
    from app.erp.seguimiento_mirror import Espejo

    with factory() as s:
        o = _order(s, "BOP-212")
        esp = Espejo(session=s)
        destino: list[Any] = [""] * len(SEGUIMIENTO_COLUMNS_V2)
        destino[_c("Nº pedido")], destino[_c("Cliente")] = "BOP-212", "Acme SL"
        destino[_c("Nota / Incidencia")] = "[BoHub: Situación#ab12, Fecha#cd34]"
        destino[ID] = o.id
        gemela = _gemela("BOP-212", "Acme SL", **{"Nota / Incidencia": "llamar al cliente"})
        esp.repetidas.append((o.id, gemela, "fila del histórico"))
        esp.fusionar_repetidas([], [destino])
        assert destino[_c("Nota / Incidencia")] == (
            "llamar al cliente [BoHub: Situación#ab12, Fecha#cd34]")
        assert esp.stats["valores_rellenados"] == 1
        assert esp.stats["valores_en_conflicto"] == 0


def test_la_recuperacion_no_inventa_fechas_ni_se_tapa_con_texto(factory):
    with factory() as s:
        serial_raro = _order(s, "BOP-310")
        enorme = _order(s, "BOP-311")
        dos = _order(s, "BOP-312")
        _legacy(s, serial_raro, 2, 0)                       # «1900-01-01»: no
        _legacy(s, enorme, 1234567890, 1)                   # antes desbordaba
        _legacy(s, dos, "recoge el cliente", 2)              # texto…
        _legacy(s, dos, "24/09/2026", 3)                     # …y la fecha buena
        s.commit()
        casos = {c.order_number: c for c in planificar(s, {serial_raro.id, enorme.id, dos.id})}
        assert casos["BOP-310"].accion == NO_ES_FECHA
        assert casos["BOP-311"].accion == NO_ES_FECHA
        assert casos["BOP-312"].accion == RELLENAR
        assert casos["BOP-312"].fecha_hoja == "2026-09-24"


def test_un_guion_no_es_un_valor_que_conservar(factory):
    """«-» / «—» es «sin dato» en la hoja: no llega al pedido como tracking ni
    como override."""
    with factory() as s:
        o = _order(s, "BOP-213")
        sheets = _con_gemela(s, o, _gemela("BOP-213", "Acme SL", Tracking="-", Factura="-",
                                           **{"Nº serie · WhiteRIP": "—",
                                              "Factura enviada": "-"}))
        res = _pasada(s, sheets, [_row(o)])
        s.refresh(o)
        assert not o.tracking_number
        assert s.scalars(select(SeguimientoOverride)).all() == []
        assert res["espejo"]["filas_fusionadas"] == []
