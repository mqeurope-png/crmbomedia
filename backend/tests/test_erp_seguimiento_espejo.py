"""Seguimiento (app) — espejo bidireccional BoHub ↔ hoja (Fase 2)."""
from __future__ import annotations

import copy
import json
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.erp.drive_managed import (
    DEFAULT_MANAGED_TAB,
    SEPARATOR_PEDIDOS,
    is_separator,
    push_managed_tabs,
)
from app.erp.models import (
    Order,
    SeguimientoLegacy,
    SeguimientoManual,
    SeguimientoOverride,
    SeguimientoSnapshot,
)
from app.erp.seguimiento import SEGUIMIENTO_COLUMNS_V2
from app.erp.seguimiento_mirror import REVISAR_PREFIX, apply_overrides_to_rows

HISTORICA = "Pedidos Bomedia 2020-2026"
TAB = DEFAULT_MANAGED_TAB


def _c(nombre: str) -> int:
    return SEGUIMIENTO_COLUMNS_V2.index(nombre)


ID = _c("id")


class FakeTabs:
    def __init__(self, tabs: dict[str, list[list[Any]]] | None = None) -> None:
        self.tabs: dict[str, list[list[Any]]] = tabs or {HISTORICA: []}
        self.written: dict[str, list[list[Any]]] = {}
        self.formats: dict[str, list[dict[str, Any]]] = {}

    def tab_titles(self) -> list[str]:
        return list(self.tabs)

    def first_tab_title(self) -> str:
        return next(iter(self.tabs))

    def ensure_tab(self, title: str) -> int:
        self.tabs.setdefault(title, [])
        return list(self.tabs).index(title)

    def replace_tab(self, title: str, rows: list[list[Any]], *, raw: bool = False) -> None:
        self.tabs[title] = copy.deepcopy(rows)
        self.written[title] = copy.deepcopy(rows)

    def format_tab(self, title: str, requests: list[dict[str, Any]]) -> None:
        self.formats[title] = requests

    def tab_values(self, title: str, *, raw: bool = False) -> list[list[Any]]:
        filas = self.tabs.get(title, [])
        return [list(r) for r in filas] if raw else [[str(c) for c in r] for r in filas]


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    Base.metadata.drop_all(engine)


def _row(order: Order, **extra: Any) -> dict[str, Any]:
    """Fila de BoHub (dict) de un pedido real, como la da `build_rows`."""
    from app.erp.seguimiento import SITUACION_LABELS

    base = {
        "id": order.id, "situacion": "listo", "situacion_label": SITUACION_LABELS["listo"],
        "order_number": order.order_number, "fecha": "2026-09-01", "cliente": "Acme SL",
        "origen_label": "Web", "productos": "Cabezal", "importe": 121.0,
        "empresa_serie": "1 · Bomedia", "factura": "", "fecha_factura": None,
        "factura_enviada": None, "cobro_label": "—", "preparacion": "En cola",
        "envio": "Sin enviar", "tracking": order.tracking_number or "",
        "serie_whiterip": "", "nota_incidencia": "", "envio_genei": False,
    }
    base.update(extra)
    return base


def _order(s: Session, numero: str, **kw: Any) -> Order:
    o = Order(order_number=numero, payment_status="paid", **kw)
    s.add(o)
    s.flush()
    return o


def _pasada(s: Session, sheets: FakeTabs, rows, completados=None) -> dict[str, Any]:
    res = push_managed_tabs(s, sheets, rows, completados=completados or [])
    s.commit()
    return res


def _fila(sheets: FakeTabs, rid: str) -> list[Any]:
    return next(f for f in sheets.tabs[TAB] if len(f) > ID and f[ID] == rid)


def _editar(sheets: FakeTabs, rid: str, col: str, valor: Any) -> None:
    _fila(sheets, rid)[_c(col)] = valor


def _manual(numero: str = "", **celdas: Any) -> list[Any]:
    fila = [""] * len(SEGUIMIENTO_COLUMNS_V2)
    fila[_c("Nº pedido")], fila[_c("Origen")] = numero, "MANUAL"
    for k, v in celdas.items():
        fila[_c(k.replace("_", " "))] = v
    return fila


# --- «regla Tracking»: lo manual manda y se lee de vuelta ----------------------


def test_editar_cliente_a_mano_se_respeta_y_se_lee_de_vuelta(factory):
    with factory() as s:
        o = _order(s, "BOP-1")
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [_row(o)])                           # 1ª: foto
        _editar(sheets, o.id, "Cliente", "Acme Iberia (a mano)")
        _pasada(s, sheets, [_row(o)])                           # 2ª: lee la edición
        assert _fila(sheets, o.id)[_c("Cliente")] == "Acme Iberia (a mano)"
        ov = s.scalars(select(SeguimientoOverride)).one()        # leída de vuelta
        assert (ov.order_id, ov.column_key, ov.value) == (o.id, "cliente", "Acme Iberia (a mano)")
        # BoHub cambia su valor: el manual sigue mandando (no lo pisa).
        _pasada(s, sheets, [_row(o, cliente="Acme SL (nuevo nombre)")])
        assert _fila(sheets, o.id)[_c("Cliente")] == "Acme Iberia (a mano)"
        # Se vacía a mano: vuelve a rellenar BoHub y el valor manual se olvida.
        _editar(sheets, o.id, "Cliente", "")
        _pasada(s, sheets, [_row(o, cliente="Acme SL (nuevo nombre)")])
        assert _fila(sheets, o.id)[_c("Cliente")] == "Acme SL (nuevo nombre)"
        assert s.scalars(select(SeguimientoOverride)).all() == []


def test_la_primera_pasada_no_infiere_ediciones(factory):
    """Sin snapshot no hay con qué comparar: lo que ya hubiera en la hoja no se
    toma por una edición (se pinta lo de BoHub)."""
    with factory() as s:
        o = _order(s, "BOP-1")
        vieja = [list(SEGUIMIENTO_COLUMNS_V2)] + [[""] * len(SEGUIMIENTO_COLUMNS_V2)]
        vieja[1][_c("Nº pedido")], vieja[1][ID] = "BOP-1", o.id
        vieja[1][_c("Cliente")] = "Valor viejo"
        sheets = FakeTabs({HISTORICA: [], TAB: vieja})
        res = _pasada(s, sheets, [_row(o)])
        assert res["espejo"]["bootstrap"] is True
        assert _fila(sheets, o.id)[_c("Cliente")] == "Acme SL"
        assert s.scalars(select(SeguimientoOverride)).all() == []


def test_el_override_se_ve_tambien_en_la_pantalla():
    """Pantalla / Excel: `apply_overrides_to_rows` pone lo manual (no la Nota)."""
    class _S:
        def scalars(self, _q):
            return [SeguimientoOverride(order_id="o1", column_key="factura", value="F-99"),
                    SeguimientoOverride(order_id="o1", column_key="nota_incidencia",
                                        value="solo en la hoja"),
                    SeguimientoOverride(order_id="o1", column_key="factura_enviada",
                                        value="no es fecha")]
    rows = [{"id": "o1", "factura": "", "nota_incidencia": "motivo",
             "factura_enviada": None, "cliente": "A", "serie_whiterip": ""}]
    apply_overrides_to_rows(_S(), rows)                           # type: ignore[arg-type]
    assert rows[0]["factura"] == "F-99"
    assert rows[0]["nota_incidencia"] == "motivo"                 # la Nota no va a pantalla
    assert rows[0]["factura_enviada"] is None                     # fecha inválida: no entra
    assert rows[0]["overrides"] == ["factura"]
    assert rows[0]["bohub"]["factura"] == ""                      # el de BoHub, guardado


# --- Tracking: sin Genei se lee al pedido; con Genei manda Genei ----------------


def test_tracking_manual_sin_genei_va_al_pedido(factory):
    with factory() as s:
        o = _order(s, "BOP-2")
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [_row(o)])
        _editar(sheets, o.id, "Tracking", "EXT-123")
        _pasada(s, sheets, [_row(o)])
        s.refresh(o)
        assert o.tracking_number == "EXT-123"                     # leído de vuelta al pedido
        assert _fila(sheets, o.id)[_c("Tracking")] == "EXT-123"


def test_tracking_con_envio_genei_manda_genei(factory):
    with factory() as s:
        o = _order(s, "BOP-3", tracking_number="GENEI-777",
                   packing_json=json.dumps({"genei": {"shipment_code": "GE123"}}))
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [_row(o, envio_genei=True)])
        _editar(sheets, o.id, "Tracking", "TECLEADO-A-MANO")
        res = _pasada(s, sheets, [_row(o, envio_genei=True)])
        s.refresh(o)
        assert o.tracking_number == "GENEI-777"                   # el pedido no cambia
        assert _fila(sheets, o.id)[_c("Tracking")] == "GENEI-777"  # la celda vuelve
        assert res["espejo"]["tracking_genei_ignorados"] == 1


# --- columnas bloqueadas + no-borrado -------------------------------------------


def test_editar_columna_bloqueada_se_revierte_y_no_se_lee(factory):
    with factory() as s:
        o = _order(s, "BOP-4")
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [_row(o)])
        _editar(sheets, o.id, "Importe", 999.0)
        _editar(sheets, o.id, "Situación", "Cobrado")
        _pasada(s, sheets, [_row(o)])
        fila = _fila(sheets, o.id)
        assert fila[_c("Importe")] == 121.0 and fila[_c("Situación")] != "Cobrado"
        assert s.scalars(select(SeguimientoOverride)).all() == []


def test_borrar_una_fila_de_bohub_en_la_hoja_la_vuelve_a_poner(factory):
    with factory() as s:
        o = _order(s, "BOP-5")
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [_row(o)])
        sheets.tabs[TAB] = [f for f in sheets.tabs[TAB] if not (len(f) > ID and f[ID] == o.id)]
        _pasada(s, sheets, [_row(o)])
        assert _fila(sheets, o.id)[_c("Nº pedido")] == "BOP-5"


# --- filas manuales: id + ingesta; inválidas se marcan -------------------------


def test_fila_manual_nueva_recibe_id_y_se_ingiere(factory):
    with factory() as s:
        sheets = FakeTabs({HISTORICA: [], TAB: [
            list(SEGUIMIENTO_COLUMNS_V2),
            _manual("MAN-1", Cliente="Taller Pepe", Fecha="01/09/2026"),
        ]})
        res = _pasada(s, sheets, [])
        fila = next(f for f in sheets.tabs[TAB] if len(f) > 1 and f[1] == "MAN-1")
        assert fila[ID]                                           # id escrito en la hoja
        m = s.scalars(select(SeguimientoManual)).one()            # ingerida en BoHub
        assert m.row_id == fila[ID] and json.loads(m.values_json)[1] == "MAN-1"
        assert res["espejo"]["manuales_nuevas"] == 1
        # Idempotente: la segunda pasada conserva el mismo id y no duplica.
        _pasada(s, sheets, [])
        again = next(f for f in sheets.tabs[TAB] if len(f) > 1 and f[1] == "MAN-1")
        assert again[ID] == fila[ID]
        assert len(s.scalars(select(SeguimientoManual)).all()) == 1


def test_fila_manual_invalida_se_marca_y_no_se_ingiere_hasta_corregir(factory):
    with factory() as s:
        mala = _manual("", Fecha="no es fecha")                   # sin Nº ni Cliente
        sheets = FakeTabs({HISTORICA: [], TAB: [list(SEGUIMIENTO_COLUMNS_V2), mala]})
        res = _pasada(s, sheets, [])
        fila = sheets.tabs[TAB][1]
        nota = fila[_c("Nota / Incidencia")]
        assert REVISAR_PREFIX in nota and "falta Nº pedido o Cliente" in nota
        assert "Fecha no es una fecha" in nota
        assert not fila[ID]                                       # sin id
        assert s.scalars(select(SeguimientoManual)).all() == []   # no ingerida
        assert res["espejo"]["manuales_invalidas"] == 1
        # Se corrige a mano → la siguiente pasada la ingiere y quita la marca.
        fila[_c("Nº pedido")], fila[_c("Fecha")] = "MAN-9", "02/09/2026"
        _pasada(s, sheets, [])
        fila = next(f for f in sheets.tabs[TAB] if len(f) > 1 and f[1] == "MAN-9")
        assert REVISAR_PREFIX not in fila[_c("Nota / Incidencia")]
        assert fila[ID] and s.scalars(select(SeguimientoManual)).one().row_id == fila[ID]


def test_borrar_una_fila_manual_es_borrado_logico(factory):
    """Decisión (en el PR): borrar a mano una fila manual la quita de la hoja
    (se respeta) y en BoHub queda con `deleted_at` (recuperable)."""
    with factory() as s:
        sheets = FakeTabs({HISTORICA: [], TAB: [
            list(SEGUIMIENTO_COLUMNS_V2), _manual("MAN-1", Cliente="A", Fecha="01/09/2026"),
            _manual("MAN-2", Cliente="B", Fecha="01/09/2026"),
        ]})
        _pasada(s, sheets, [])
        sheets.tabs[TAB] = [f for f in sheets.tabs[TAB] if not (len(f) > 1 and f[1] == "MAN-1")]
        res = _pasada(s, sheets, [])
        assert not any(len(f) > 1 and f[1] == "MAN-1" for f in sheets.tabs[TAB])  # no vuelve
        borrada = s.scalars(select(SeguimientoManual).where(
            SeguimientoManual.values_json.contains("MAN-1"))).one()
        assert borrada.deleted_at is not None
        assert res["espejo"]["borradas"] == 1


def test_borrado_masivo_se_restaura(factory):
    """Si desaparecen de golpe muchas filas (hoja vaciada / lectura rota), no es
    una persona: no se borra nada y se vuelven a poner."""
    with factory() as s:
        manuales = [_manual(f"MAN-{i}", Cliente="X", Fecha="01/09/2026") for i in range(25)]
        sheets = FakeTabs({HISTORICA: [], TAB: [list(SEGUIMIENTO_COLUMNS_V2), *manuales]})
        _pasada(s, sheets, [])
        sheets.tabs[TAB] = [list(SEGUIMIENTO_COLUMNS_V2)]         # alguien vacía la pestaña
        res = _pasada(s, sheets, [])
        assert res["espejo"]["borrado_masivo"] is True
        assert sum(1 for f in sheets.tabs[TAB] if len(f) > 1 and str(f[1]).startswith("MAN-")) == 25
        assert all(m.deleted_at is None for m in s.scalars(select(SeguimientoManual)))


# --- Nota: BoHub rellena si no hay nota manual ---------------------------------


def test_nota_bohub_rellena_si_no_hay_manual_y_la_manual_manda(factory):
    with factory() as s:
        o = _order(s, "BOP-6")
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [_row(o, nota_incidencia="Falta pago")])
        assert _fila(sheets, o.id)[_c("Nota / Incidencia")] == "Falta pago"
        _editar(sheets, o.id, "Nota / Incidencia", "Llamar el lunes")
        _pasada(s, sheets, [_row(o, nota_incidencia="Falta pago")])
        assert _fila(sheets, o.id)[_c("Nota / Incidencia")] == "Llamar el lunes"
        _editar(sheets, o.id, "Nota / Incidencia", "")
        _pasada(s, sheets, [_row(o, nota_incidencia="Falta pago")])
        assert _fila(sheets, o.id)[_c("Nota / Incidencia")] == "Falta pago"


def test_factura_enviada_no_valida_se_conserva_y_se_marca(factory):
    with factory() as s:
        o = _order(s, "BOP-7")
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [_row(o)])
        _editar(sheets, o.id, "Factura enviada", "la semana pasada")
        _pasada(s, sheets, [_row(o)])
        fila = _fila(sheets, o.id)
        assert fila[_c("Factura enviada")] == "la semana pasada"  # no se pierde
        assert "Factura enviada no es una fecha" in fila[_c("Nota / Incidencia")]


# --- idempotencia ----------------------------------------------------------------


def test_reconcile_idempotente_en_los_dos_sentidos(factory):
    with factory() as s:
        o = _order(s, "BOP-8")
        sheets = FakeTabs({HISTORICA: [], TAB: [
            list(SEGUIMIENTO_COLUMNS_V2), _manual("MAN-1", Cliente="A", Fecha="01/09/2026"),
        ]})
        _pasada(s, sheets, [_row(o)])
        _editar(sheets, o.id, "Factura", "F-1 (a mano)")
        _pasada(s, sheets, [_row(o)])
        g1 = copy.deepcopy(sheets.written[TAB])
        snap1 = {x.row_id: x.values_json for x in s.scalars(select(SeguimientoSnapshot))}
        res = _pasada(s, sheets, [_row(o)])
        assert sheets.written[TAB] == g1                          # hoja: igual
        assert {x.row_id: x.values_json
                for x in s.scalars(select(SeguimientoSnapshot))} == snap1   # BoHub: igual
        assert res["espejo"]["ediciones_leidas"] == 0
        assert len(s.scalars(select(SeguimientoOverride)).all()) == 1


# --- histórico: id por registro, ediciones de vuelta, sin duplicar -------------


def _hist(numero: str, cliente: str) -> list[Any]:
    fila = [""] * 18
    fila[0], fila[_c("Nº pedido")], fila[_c("Cliente")] = "Histórico", numero, cliente
    return fila


def test_historico_recibe_su_id_se_conserva_y_lee_ediciones(factory):
    with factory() as s:
        h1, h2 = _hist("VIEJO-1", "Roca"), _hist("9562", "Duplicoder")
        s.add_all([
            SeguimientoLegacy(row_index=0, numero_raw="VIEJO-1", cliente_raw="Roca",
                              raw_json=json.dumps(h1), match_status="synthetic"),
            SeguimientoLegacy(row_index=1, numero_raw="9562", cliente_raw="Duplicoder",
                              raw_json=json.dumps(h2), match_status="synthetic"),
        ])
        s.commit()
        leg1, leg2 = s.scalars(
            select(SeguimientoLegacy).order_by(SeguimientoLegacy.row_index)).all()
        sheets = FakeTabs({HISTORICA: [], TAB: [
            list(SEGUIMIENTO_COLUMNS_V2), [SEPARATOR_PEDIDOS], list(h1), list(h2),
        ]})
        res = _pasada(s, sheets, [])
        assert res["espejo"]["historico_ids_asignados"] == 2
        filas = [f for f in sheets.tabs[TAB] if len(f) > 1 and f[0] == "Histórico"]
        assert [f[ID] for f in filas] == [leg1.id, leg2.id]       # id estampado
        assert [f[:18] for f in filas] == [h1, h2]                 # datos intactos
        # Una edición a mano en el histórico se lee de vuelta a BoHub.
        filas[1][_c("Cliente")] = "Duplicoder SL"
        _pasada(s, sheets, [])
        s.refresh(leg2)
        assert leg2.cliente_raw == "Duplicoder SL"


def test_historico_confirmado_con_pedido_vivo_no_se_duplica(factory):
    """Un pedido vivo y su gemelo del histórico (backfill `confirmed`) quedan en
    UNA sola fila: la de BoHub."""
    with factory() as s:
        o = _order(s, "BOP-100")
        h = _hist("BOP-100", "Acme SL")
        s.add(SeguimientoLegacy(row_index=0, numero_raw="BOP-100", cliente_raw="Acme SL",
                                raw_json=json.dumps(h), match_status="confirmed",
                                matched_order_id=o.id))
        s.commit()
        sheets = FakeTabs({HISTORICA: [], TAB: [
            list(SEGUIMIENTO_COLUMNS_V2), [SEPARATOR_PEDIDOS], list(h),
        ]})
        res = _pasada(s, sheets, [_row(o)])
        con_ese_id = [f for f in sheets.tabs[TAB] if len(f) > ID and f[ID] == o.id]
        assert len(con_ese_id) == 1 and con_ese_id[0][0] != "Histórico"
        assert res["espejo"]["historico_duplicados_suprimidos"] == 1


def test_sin_historico_importado_se_conserva_como_siempre(factory):
    """Sin `seguimiento_legacy` (Fase 1 sin aplicar), el histórico se preserva tal
    cual, sin ids: nada cambia respecto a Part B."""
    with factory() as s:
        h = ["Histórico", "VIEJO-1"]
        sheets = FakeTabs({HISTORICA: [], TAB: [
            list(SEGUIMIENTO_COLUMNS_V2), [SEPARATOR_PEDIDOS], list(h),
        ]})
        _pasada(s, sheets, [])
        sep = next(i for i, f in enumerate(sheets.tabs[TAB]) if is_separator(f))
        assert sheets.tabs[TAB][sep + 1] == h


def test_una_edicion_en_una_fila_completada_tambien_se_lee(factory):
    """Los completados son filas de BoHub aunque vivan bajo el separador: sus
    columnas editables siguen la misma regla."""
    with factory() as s:
        o = _order(s, "BOP-C1")
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [], completados=[_row(o)])
        _editar(sheets, o.id, "Factura", "F-HIST (a mano)")
        _pasada(s, sheets, [], completados=[_row(o)])
        assert _fila(sheets, o.id)[_c("Factura")] == "F-HIST (a mano)"
        assert s.scalars(select(SeguimientoOverride)).one().value == "F-HIST (a mano)"


def test_la_vista_previa_detecta_pero_no_escribe_nada(factory):
    with factory() as s:
        o = _order(s, "BOP-P")
        sheets = FakeTabs({HISTORICA: [], TAB: [
            list(SEGUIMIENTO_COLUMNS_V2), _manual("MAN-P", Cliente="A", Fecha="01/09/2026"),
        ]})
        _pasada(s, sheets, [_row(o)])
        _editar(sheets, o.id, "Cliente", "Otro")
        antes = copy.deepcopy(sheets.tabs[TAB])
        res = push_managed_tabs(s, sheets, [_row(o)], completados=[], dry_run=True)
        s.commit()
        assert res["espejo"]["ediciones_leidas"] == 1
        assert s.scalars(select(SeguimientoOverride)).all() == []   # nada guardado
        assert sheets.tabs[TAB] == antes                           # hoja sin tocar


# --- blindaje: un snapshot desfasado no crea valores manuales falsos ------------


def test_un_snapshot_desfasado_no_convierte_un_cambio_de_bohub_en_manual(factory):
    """Si una pasada escribió la hoja pero no llegó a confirmar, el snapshot queda
    atrasado. Un cambio de BoHub ya pintado NO debe leerse como edición manual."""
    with factory() as s:
        o = _order(s, "BOP-S")
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [_row(o, factura="")])
        # BoHub factura; la pasada pinta F-1 pero «no confirma»: la hoja dice F-1
        # y el snapshot sigue diciendo «».
        push_managed_tabs(s, sheets, [_row(o, factura="F-1")], completados=[])
        s.rollback()
        assert _fila(sheets, o.id)[_c("Factura")] == "F-1"
        res = _pasada(s, sheets, [_row(o, factura="F-1")])
        assert res["espejo"]["ediciones_leidas"] == 0
        assert s.scalars(select(SeguimientoOverride)).all() == []


# --- protección de la hoja --------------------------------------------------------


class FakeTabsConMeta(FakeTabs):
    """Transporte que sabe leer las protecciones actuales (como el real)."""

    service_email = "bohub@proyecto.iam.gserviceaccount.com"

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.calls: list[list[dict[str, Any]]] = []
        self.meta: dict[str, Any] = {"protected_ranges": [], "conditional_formats": []}

    def format_tab(self, title: str, requests: list[dict[str, Any]]) -> None:
        super().format_tab(title, requests)
        if title == TAB:
            self.calls.append(requests)

    def tab_metadata(self, title: str) -> dict[str, Any]:
        return self.meta


def _protecciones(reqs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r["addProtectedRange"]["protectedRange"] for r in reqs if "addProtectedRange" in r]


def test_protege_las_columnas_bloqueadas_de_las_filas_de_bohub(factory):
    from app.erp.seguimiento_mirror import PROTECT_DESC, PROTECT_DESC_GENEI

    with factory() as s:
        o1, o2 = _order(s, "BOP-A"), _order(s, "BOP-B")
        g = _order(s, "BOP-G", packing_json=json.dumps({"genei": {"shipment_code": "G1"}}))
        sheets = FakeTabsConMeta({HISTORICA: [], TAB: [
            list(SEGUIMIENTO_COLUMNS_V2), _manual("MAN-1", Cliente="A", Fecha="01/09/2026"),
        ]})
        sheets.meta["protected_ranges"] = [
            {"id": 11, "description": "BoHub · espejo: columnas bloqueadas (solo BoHub)"},
            {"id": 12, "description": "Protección puesta por Bart"},
        ]
        _pasada(s, sheets, [_row(o1), _row(o2), _row(g, envio_genei=True)])
        reqs = sheets.calls[-1]
        # Solo se quitan las protecciones del espejo, nunca las de una persona.
        borradas = [r["deleteProtectedRange"]["protectedRangeId"]
                    for r in reqs if "deleteProtectedRange" in r]
        assert borradas == [11]
        prot = _protecciones(reqs)
        assert all(p["editors"]["users"] == [FakeTabsConMeta.service_email] for p in prot)
        assert all(p["warningOnly"] is False for p in prot)
        grid = sheets.tabs[TAB]
        filas_bohub = {i for i, f in enumerate(grid)
                       if len(f) > ID and f[ID] in (o1.id, o2.id, g.id)}
        fila_manual = next(i for i, f in enumerate(grid) if len(f) > 1 and f[1] == "MAN-1")
        bloqueadas = [p for p in prot if p["description"] == PROTECT_DESC]
        cubiertas = {r for p in bloqueadas
                     for r in range(p["range"]["startRowIndex"], p["range"]["endRowIndex"])}
        assert filas_bohub <= cubiertas and fila_manual not in cubiertas
        cols = {c for p in bloqueadas
                for c in range(p["range"]["startColumnIndex"], p["range"]["endColumnIndex"])}
        editables = {_c(n) for n in ("Cliente", "Factura", "Factura enviada", "Tracking",
                                     "Nº serie · WhiteRIP", "Nota / Incidencia")}
        assert not (cols & editables) and ID in cols and _c("Importe") in cols
        # El Tracking de la fila con envío Genei, protegido aparte.
        genei = [p for p in prot if p["description"] == PROTECT_DESC_GENEI]
        fila_g = next(i for i, f in enumerate(grid) if len(f) > ID and f[ID] == g.id)
        assert len(genei) == 1 and genei[0]["range"]["startRowIndex"] == fila_g
        assert genei[0]["range"]["startColumnIndex"] == _c("Tracking")
        # Marca naranja de «⚠ revisar» + validación en la zona viva.
        assert any("addConditionalFormatRule" in r for r in reqs)
        assert any("setDataValidation" in r for r in reqs)


def test_sin_leer_las_protecciones_no_las_apila(factory):
    """Un transporte que no sabe leer las protecciones actuales no recibe
    protecciones nuevas (se apilarían en cada pasada)."""
    with factory() as s:
        o = _order(s, "BOP-N")
        sheets = FakeTabs({HISTORICA: [], TAB: []})
        _pasada(s, sheets, [_row(o)])
        assert not any("addProtectedRange" in r for r in sheets.formats[TAB])


def test_la_regla_naranja_anterior_se_sustituye(factory):
    from app.erp.seguimiento_mirror import REVISAR_FORMULA

    with factory() as s:
        sheets = FakeTabsConMeta({HISTORICA: [], TAB: []})
        sheets.meta["conditional_formats"] = [
            {"booleanRule": {"condition": {"type": "TEXT_CONTAINS",
                                           "values": [{"userEnteredValue": "de Bart"}]}}},
            {"booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                                           "values": [{"userEnteredValue": REVISAR_FORMULA}]}}},
        ]
        _pasada(s, sheets, [])
        reqs = sheets.calls[-1]
        assert [r["deleteConditionalFormatRule"]["index"]
                for r in reqs if "deleteConditionalFormatRule" in r] == [1]
        assert sum(1 for r in reqs if "addConditionalFormatRule" in r) == 1
