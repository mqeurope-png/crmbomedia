"""Migración de la hoja de seguimiento a una hoja propiedad de la cuenta de servicio.

Google nunca aplica un rango protegido al PROPIETARIO de la hoja. Con la hoja de
Bart, él se salta las columnas bloqueadas. La migración deja la hoja en manos
de la cuenta de servicio (o de una cuenta dedicada) y comprueba:
  - que la copia conserva celda a celda las pestañas (ids, histórico, manuales)
    y que los overrides siguen (viven en la BD, casados por id);
  - que las columnas bloqueadas solo las puede editar la cuenta de servicio y que
    todas las personas quedan bloqueadas;
  - que si algo falla no cambia nada (y no queda una hoja huérfana).

Sin red: «Google» es un doble en memoria; la pasada del espejo es la real.
"""
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
    DEFAULT_INCIDENCIAS_TAB,
    DEFAULT_MANAGED_TAB,
    SEPARATOR_PEDIDOS,
    push_managed_tabs,
)
from app.erp.drive_migracion import (
    ARCHIVO_SETTING,
    PREFIJO_ARCHIVO,
    archivar_origen,
    migrar,
    plan_migracion,
    probar_propiedad,
    verificar_hoja,
)
from app.erp.drive_sheets import DriveSyncError
from app.erp.models import (
    ERP_SETTINGS_SINGLETON_ID,
    ErpSettings,
    Order,
    SeguimientoLegacy,
    SeguimientoOverride,
)
from app.erp.seguimiento import SEGUIMIENTO_COLUMNS_V2

SA = "bohub-seguimiento@bomedia-crm.iam.gserviceaccount.com"
BART = "bart@bomedia.es"
MARTA = "marta@bomedia.es"
CONTABLE = "contable@bomedia.es"
ROBOT = "hoja.bohub@gmail.com"
TAB, INC = DEFAULT_MANAGED_TAB, DEFAULT_INCIDENCIAS_TAB
ARCHIVO_VIEJO = "Pedidos Bomedia 2020-2026"
ID = SEGUIMIENTO_COLUMNS_V2.index("id")
CLIENTE = SEGUIMIENTO_COLUMNS_V2.index("Cliente")
_QUOTA = ('Sheets API 403: {"error": {"code": 403, "message": "The user\'s Drive storage '
          'quota has been exceeded.", "status": "PERMISSION_DENIED"}}')


# --- Google en memoria ----------------------------------------------------------


class Hoja:
    """Una hoja de cálculo: pestañas (valores) y sus protecciones."""

    service_email = SA

    def __init__(self, mundo: Mundo, sid: str, titulo: str) -> None:
        self.mundo = mundo
        self.spreadsheet_id = sid
        self.props: dict[str, Any] = {"title": titulo, "locale": "es_ES",
                                      "timeZone": "Europe/Madrid"}
        self.tabs: dict[str, list[list[Any]]] = {}
        self.ids: dict[str, int] = {}
        self.prot: dict[str, list[dict[str, Any]]] = {}
        self._n = 0

    # pestañas
    def _nueva(self, titulo: str, filas: list[list[Any]] | None = None) -> int:
        self._n += 1
        self.tabs[titulo] = copy.deepcopy(filas or [])
        self.ids[titulo] = self._n
        self.prot[titulo] = []
        return self._n

    def tab_titles(self) -> list[str]:
        return list(self.tabs)

    def first_tab_title(self) -> str:
        return next(iter(self.tabs))

    def ensure_tab(self, title: str) -> int:
        return self.ids[title] if title in self.tabs else self._nueva(title)

    def replace_tab(self, title: str, rows: list[list[Any]], *, raw: bool = False) -> None:
        self.ensure_tab(title)
        self.tabs[title] = copy.deepcopy(rows)

    def tab_values(self, title: str, *, raw: bool = False) -> list[list[Any]]:
        filas = self.tabs.get(title, [])
        return [list(r) for r in filas] if raw else [[str(c) for c in r] for r in filas]

    def format_tab(self, title: str, requests: list[dict[str, Any]]) -> None:
        duenos = self.mundo.archivos[self.spreadsheet_id]["owners"]
        for r in requests:
            if "deleteProtectedRange" in r:
                pid = r["deleteProtectedRange"]["protectedRangeId"]
                self.prot[title] = [p for p in self.prot[title] if p["protectedRangeId"] != pid]
            elif "addProtectedRange" in r:
                pr = copy.deepcopy(r["addProtectedRange"]["protectedRange"])
                self.mundo.n_prot += 1
                usuarios = (pr.get("editors") or {}).get("users") or []
                pr.update(protectedRangeId=self.mundo.n_prot,
                          requestingUserCanEdit=SA in usuarios or SA in duenos)
                self.prot[title].append(pr)

    def tab_metadata(self, title: str) -> dict[str, Any]:
        return {"protected_ranges": [{"id": p["protectedRangeId"],
                                      "description": p.get("description")}
                                     for p in self.prot.get(title, [])],
                "conditional_formats": []}

    def protections(self, title: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self.prot.get(title, []))

    # hoja completa
    def spreadsheet_properties(self) -> dict[str, Any]:
        return dict(self.props)

    def set_spreadsheet_properties(self, props: dict[str, Any]) -> None:
        self.props.update(props)
        if "title" in props:
            self.mundo.archivos[self.spreadsheet_id]["name"] = props["title"]

    def copy_tab_to(self, title: str, destination_id: str) -> int:
        """Como `sheets.copyTo`: la pestaña entera, titulada «Copia de …», sin
        las protecciones."""
        destino = self.mundo.hojas[destination_id]
        return destino._nueva(f"Copia de {title}", self.tabs[title])

    def rename_tab(self, sheet_id: int, title: str, *, index: int | None = None) -> None:
        viejo = next(t for t, i in self.ids.items() if i == sheet_id)
        orden = [t for t in self.tabs if t != viejo]
        orden.insert(index if index is not None else len(orden), title)
        filas, prot = self.tabs.pop(viejo), self.prot.pop(viejo)
        self.ids[title] = self.ids.pop(viejo)
        self.tabs[title], self.prot[title] = filas, prot
        self.tabs = {t: self.tabs[t] for t in orden}

    def delete_tab(self, title: str) -> None:
        self.tabs.pop(title, None)
        self.ids.pop(title, None)
        self.prot.pop(title, None)

    def refresh_tabs(self) -> None:
        return None


class Drive:
    def __init__(self, mundo: Mundo) -> None:
        self.m = mundo

    def _archivo(self, file_id: str) -> dict[str, Any]:
        if file_id not in self.m.archivos:
            raise DriveSyncError(f"Drive API 404: File not found: {file_id}")
        return self.m.archivos[file_id]

    def file_info(self, file_id: str) -> dict[str, Any]:
        a = self._archivo(file_id)
        puede = SA in a["owners"] or any(
            p["emailAddress"] == SA and p["role"] == "writer" for p in a["permisos"])
        return {"id": file_id, "name": a["name"],
                "owners": [{"emailAddress": o} for o in a["owners"]],
                "writersCanShare": a["writersCanShare"],
                "capabilities": {"canEdit": puede, "canShare": puede}}

    def permissions(self, file_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self._archivo(file_id)["permisos"])

    def share(self, file_id: str, email: str, role: str, *, kind: str = "user") -> None:
        a = self._archivo(file_id)
        self.m.n_perm += 1
        a["permisos"].append({"id": f"p{self.m.n_perm}", "type": kind, "role": role,
                              "emailAddress": email})

    def set_role(self, file_id: str, permission_id: str, role: str) -> None:
        for p in self._archivo(file_id)["permisos"]:
            if p["id"] == permission_id:
                p["role"] = role

    def update(self, file_id: str, fields: dict[str, Any]) -> None:
        self._archivo(file_id).update(fields)

    def delete(self, file_id: str) -> None:
        self._archivo(file_id)
        self.m.archivos.pop(file_id)
        self.m.hojas.pop(file_id)
        self.m.borrados.append(file_id)


class Mundo:
    def __init__(self, *, sa_con_cuota: bool = True) -> None:
        self.hojas: dict[str, Hoja] = {}
        self.archivos: dict[str, dict[str, Any]] = {}
        self.sa_con_cuota = sa_con_cuota
        self.borrados: list[str] = []
        self.n_prot = self.n_perm = self.n_hojas = 0
        self.drive = Drive(self)

    def hoja(self, titulo: str, dueno: str, personas: dict[str, str]) -> Hoja:
        self.n_hojas += 1
        sid = f"hoja-{self.n_hojas}"
        h = Hoja(self, sid, titulo)
        self.hojas[sid] = h
        permisos = [{"id": f"o{self.n_hojas}", "type": "user", "role": "owner",
                     "emailAddress": dueno}]
        for email, rol in personas.items():
            self.n_perm += 1
            permisos.append({"id": f"p{self.n_perm}", "type": "user", "role": rol,
                             "emailAddress": email})
        self.archivos[sid] = {"name": titulo, "owners": [dueno], "permisos": permisos,
                              "writersCanShare": True}
        return h

    def crear(self, titulo: str, props: dict[str, Any]) -> str:
        """`spreadsheets.create` como la cuenta de servicio."""
        if not self.sa_con_cuota:
            raise DriveSyncError(_QUOTA)
        h = self.hoja(titulo, SA, {})
        h.props.update(props)
        h._nueva("Hoja 1")
        return h.spreadsheet_id

    def abrir(self, sid: str) -> Hoja:
        return self.hojas[sid]


# --- escenario: la hoja de Bart tras unas cuantas pasadas del espejo -----------


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    Base.metadata.drop_all(engine)


def _row(order: Order) -> dict[str, Any]:
    from app.erp.seguimiento import SITUACION_LABELS

    return {
        "id": order.id, "situacion": "listo", "situacion_label": SITUACION_LABELS["listo"],
        "order_number": order.order_number, "fecha": "2026-09-01", "cliente": "Acme SL",
        "origen_label": "Web", "productos": "Cabezal", "importe": 121.0,
        "empresa_serie": "1 · Bomedia", "factura": "", "fecha_factura": None,
        "factura_enviada": None, "cobro_label": "—", "preparacion": "En cola",
        "envio": "Sin enviar", "tracking": "", "serie_whiterip": "", "nota_incidencia": "",
        "envio_genei": False,
    }


def _hist(numero: str, cliente: str) -> list[Any]:
    fila = [""] * 18
    fila[0], fila[1], fila[CLIENTE] = "Histórico", numero, cliente
    return fila


def _fila(hoja: Hoja, rid: str) -> list[Any]:
    return next(f for f in hoja.tabs[TAB] if len(f) > ID and f[ID] == rid)


class Escena:
    """La hoja de Bart (él, propietario; Marta, editora; la contable, lectora),
    con dos pedidos de BoHub, dos filas del histórico importadas con id, un
    override ya leído (Cliente de BOP-1) y una edición todavía SIN leer (Cliente
    de BOP-2, tecleada tras la última pasada)."""

    def __init__(self, s: Session, mundo: Mundo) -> None:
        self.s, self.mundo = s, mundo
        s.add(ErpSettings(id=ERP_SETTINGS_SINGLETON_ID, drive_spreadsheet_id="(la de Bart)"))
        self.o1 = Order(order_number="BOP-1", payment_status="paid")
        self.o2 = Order(order_number="BOP-2", payment_status="paid")
        s.add_all([self.o1, self.o2])
        h1, h2 = _hist("VIEJO-1", "Roca"), _hist("9562", "Duplicoder")
        s.add_all([
            SeguimientoLegacy(row_index=0, numero_raw="VIEJO-1", cliente_raw="Roca",
                              raw_json=json.dumps(h1), match_status="synthetic"),
            SeguimientoLegacy(row_index=1, numero_raw="9562", cliente_raw="Duplicoder",
                              raw_json=json.dumps(h2), match_status="synthetic"),
        ])
        s.commit()
        self.vieja = mundo.hoja("Seguimiento", BART, {MARTA: "writer", CONTABLE: "reader",
                                                      SA: "writer"})
        s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID).drive_spreadsheet_id = \
            self.vieja.spreadsheet_id
        self.vieja._nueva(ARCHIVO_VIEJO, [["Empresa", "Fecha entrada albarán"], ["BO", "x"]])
        self.vieja._nueva(TAB, [list(SEGUIMIENTO_COLUMNS_V2), [SEPARATOR_PEDIDOS], h1, h2])
        self.pasada(self.vieja)                                  # ids + foto
        _fila(self.vieja, self.o1.id)[CLIENTE] = "Acme Iberia (a mano)"
        self.pasada(self.vieja)                                  # override leído
        _fila(self.vieja, self.o2.id)[CLIENTE] = "Beta (sin leer aún)"
        self.legacy_ids = [leg.id for leg in s.scalars(
            select(SeguimientoLegacy).order_by(SeguimientoLegacy.row_index))]

    def push(self, s: Session, hoja: Any) -> dict[str, Any]:
        return push_managed_tabs(s, hoja, [_row(self.o1), _row(self.o2)], completados=[])

    def pasada(self, hoja: Hoja) -> None:
        self.push(self.s, hoja)
        self.s.commit()

    def migrar(self, **kw: Any) -> dict[str, Any]:
        kw.setdefault("push", self.push)
        return migrar(self.s, origen=self.vieja, drive=self.mundo.drive, sa_email=SA,
                      abrir=self.mundo.abrir, **kw)


def _config(s: Session) -> tuple[str | None, dict[str, Any]]:
    cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    s.refresh(cfg)
    return cfg.drive_spreadsheet_id, json.loads(cfg.factusol_series_json or "{}")


# --- la migración ---------------------------------------------------------------


def test_crear_deja_la_hoja_en_manos_de_la_cuenta_de_servicio_y_bloquea_a_todos(factory):
    with factory() as s:
        mundo = Mundo()
        e = Escena(s, mundo)
        vieja_antes = copy.deepcopy(e.vieja.tabs)
        overrides_antes = s.scalars(select(SeguimientoOverride)).all()
        assert [o.value for o in overrides_antes] == ["Acme Iberia (a mano)"]

        res = e.migrar(crear_hoja=mundo.crear)
        s.commit()
        nueva = mundo.hojas[res["destino_id"]]

        # Propietaria desde el origen: la cuenta de servicio.
        assert mundo.archivos[nueva.spreadsheet_id]["owners"] == [SA]
        # Solo las pestañas de la app, en su orden; la «Hoja 1» vacía, fuera. El
        # archivo en bruto de Bart se queda en la vieja.
        assert nueva.tab_titles() == [TAB, INC]
        assert ARCHIVO_VIEJO not in nueva.tabs
        # Mismo idioma y zona horaria que la vieja (fechas y fórmulas iguales).
        assert nueva.props["locale"] == "es_ES"
        assert nueva.props["timeZone"] == "Europe/Madrid"

        # Ids, histórico y overrides conservados.
        historico = [f for f in nueva.tabs[TAB] if f and f[0] == "Histórico"]
        assert [f[ID] for f in historico] == e.legacy_ids
        assert [f[:18] for f in historico] == [_hist("VIEJO-1", "Roca"),
                                                _hist("9562", "Duplicoder")]
        assert _fila(nueva, e.o1.id)[CLIENTE] == "Acme Iberia (a mano)"
        # La edición de la vieja que aún no se había leído no se pierde: la
        # pasada sobre la nueva la lee de vuelta.
        assert _fila(nueva, e.o2.id)[CLIENTE] == "Beta (sin leer aún)"
        valores = {(o.order_id, o.value) for o in s.scalars(select(SeguimientoOverride))}
        assert (e.o1.id, "Acme Iberia (a mano)") in valores
        assert (e.o2.id, "Beta (sin leer aún)") in valores
        assert res["copia_identica"] is True
        assert res["resumen_nueva"][TAB]["historico"] == 2
        assert res["resumen_nueva"][TAB]["ids"] == 4

        # Protección: SOLO la cuenta de servicio edita las columnas bloqueadas.
        prot = nueva.protections(TAB)
        assert prot and all(p["editors"]["users"] == [SA] for p in prot)
        assert all(p["warningOnly"] is False and p["requestingUserCanEdit"] for p in prot)
        v = res["verificacion"]
        assert v["ok"] is True
        assert v["sa_es_propietaria"] is True and v["exentos"] == []
        assert v["bloqueados"] == [BART, MARTA]           # Bart incluido: ya no es dueño

        # Compartida con el equipo: quien editaba, EDITOR (Bart también, nunca
        # propietario); quien leía, lector. Y nadie puede cambiar el acceso.
        roles = {p["emailAddress"]: p["role"]
                 for p in mundo.archivos[nueva.spreadsheet_id]["permisos"]}
        assert roles == {SA: "owner", BART: "writer", MARTA: "writer", CONTABLE: "reader"}
        assert mundo.archivos[nueva.spreadsheet_id]["writersCanShare"] is False

        # BoHub apunta a la nueva; la vieja queda anotada como archivo.
        sid, series = _config(s)
        assert sid == nueva.spreadsheet_id
        assert series[ARCHIVO_SETTING] == e.vieja.spreadsheet_id

        # La vieja: intacta de datos, archivada y de solo lectura para el equipo.
        assert e.vieja.tabs == vieja_antes
        archivo = archivar_origen(e.vieja, mundo.drive, SA)
        assert e.vieja.props["title"].startswith(PREFIJO_ARCHIVO)
        roles_vieja = {p["emailAddress"]: p["role"]
                       for p in mundo.archivos[e.vieja.spreadsheet_id]["permisos"]}
        assert roles_vieja == {BART: "owner", MARTA: "reader", CONTABLE: "reader",
                               SA: "writer"}
        assert archivo["a_lectura"] == [MARTA]
        assert any(BART in a for a in archivo["avisos"])

        # Y la sincronización normal sigue sobre la nueva sin inventar ediciones.
        antes = copy.deepcopy(nueva.tabs[TAB])
        res2 = e.push(s, nueva)
        assert res2["espejo"]["ediciones_leidas"] == 0
        assert nueva.tabs[TAB] == antes


def test_sin_cuota_la_cuenta_de_servicio_no_puede_ser_propietaria_y_nada_cambia(factory):
    """Las cuentas de servicio creadas desde el 15/04/2025 no tienen cuota de
    Drive: Google no las deja ser propietarias. La prueba lo dice, y `--crear`
    falla ANTES de tocar nada."""
    with factory() as s:
        mundo = Mundo(sa_con_cuota=False)
        e = Escena(s, mundo)
        prueba = probar_propiedad(mundo.crear, mundo.drive)
        assert prueba["puede"] is False
        assert "15/04/2025" in prueba["motivo"]
        with pytest.raises(DriveSyncError) as exc:
            e.migrar(crear_hoja=mundo.crear)
        assert "--destino" in str(exc.value)
        s.rollback()
        assert _config(s)[0] == e.vieja.spreadsheet_id
        assert set(mundo.hojas) == {e.vieja.spreadsheet_id}


def test_la_prueba_de_propiedad_crea_y_borra_la_hoja(factory):
    mundo = Mundo()
    prueba = probar_propiedad(mundo.crear, mundo.drive)
    assert prueba == {"puede": True, "borrada": True, "id": "hoja-1"}
    assert mundo.hojas == {} and mundo.borrados == ["hoja-1"]


def test_destino_de_una_cuenta_dedicada(factory):
    """Sin cuota en la cuenta de servicio: una cuenta dedicada (que no usa nadie)
    crea la hoja y la comparte con BoHub. Bart y su equipo quedan bloqueados;
    la dedicada es la única que se la salta (cuenta de emergencia)."""
    with factory() as s:
        mundo = Mundo(sa_con_cuota=False)
        e = Escena(s, mundo)
        destino = mundo.hoja("Seguimiento BoHub", ROBOT, {SA: "writer"})
        destino._nueva("Hoja 1")
        destino.props.update(locale="en_US", timeZone="UTC")
        plan = plan_migracion(s, origen=e.vieja, drive=mundo.drive, sa_email=SA,
                              destino_id=destino.spreadsheet_id, abrir=mundo.abrir)
        assert plan["errores"] == []
        assert any("cuenta de emergencia" in a for a in plan["avisos"])
        assert any("pueden cambiar el acceso" in a for a in plan["avisos"])

        res = e.migrar(destino_id=destino.spreadsheet_id)
        s.commit()
        assert destino.tab_titles() == [TAB, INC]
        assert destino.props["locale"] == "es_ES"            # como la vieja
        assert _fila(destino, e.o1.id)[CLIENTE] == "Acme Iberia (a mano)"
        v = res["verificacion"]
        assert v["exentos"] == [ROBOT]
        assert v["bloqueados"] == [BART, MARTA]
        assert v["problemas"] == [] and v["ok"] is False     # la dedicada se la salta
        assert _config(s)[0] == destino.spreadsheet_id
        # No es suya: BoHub no puede cerrar el «compartir» de los editores.
        assert mundo.archivos[destino.spreadsheet_id]["writersCanShare"] is True


@pytest.mark.parametrize(("dueno", "personas", "tabs", "error"), [
    (BART, {SA: "writer"}, {}, "ya usa la hoja"),              # Bart no quedaría bloqueado
    (ROBOT, {}, {}, "compártela"),                             # BoHub no puede editarla
    (ROBOT, {SA: "writer"}, {TAB: [["ya hay algo"]]}, "ya tiene datos"),
])
def test_destino_que_no_vale(factory, dueno, personas, tabs, error):
    with factory() as s:
        mundo = Mundo()
        e = Escena(s, mundo)
        destino = mundo.hoja("Destino", dueno, personas)
        for t, filas in tabs.items():
            destino._nueva(t, filas)
        with pytest.raises(DriveSyncError) as exc:
            e.migrar(destino_id=destino.spreadsheet_id)
        assert error in str(exc.value)
        s.rollback()
        assert _config(s)[0] == e.vieja.spreadsheet_id


def test_si_algo_falla_no_cambia_nada_ni_queda_hoja_huerfana(factory):
    with factory() as s:
        mundo = Mundo()
        e = Escena(s, mundo)

        def push_que_falla(_s: Session, _hoja: Any) -> dict[str, Any]:
            raise DriveSyncError("Sheets API 500: backendError")

        with pytest.raises(DriveSyncError):
            e.migrar(crear_hoja=mundo.crear, push=push_que_falla)
        s.rollback()
        assert _config(s)[0] == e.vieja.spreadsheet_id
        assert set(mundo.hojas) == {e.vieja.spreadsheet_id}     # la nueva, borrada
        assert len(mundo.borrados) == 1


def test_una_edicion_en_la_vieja_durante_la_migracion_la_aborta(factory):
    """Si alguien escribe en la hoja vieja mientras se migra, esa edición no
    llegaría a la nueva: se aborta sin re-apuntar y se repite después."""
    with factory() as s:
        mundo = Mundo()
        e = Escena(s, mundo)

        def push_con_intruso(ss: Session, hoja: Any) -> dict[str, Any]:
            _fila(e.vieja, e.o1.id)[CLIENTE] = "tecleado en plena migración"
            return e.push(ss, hoja)

        with pytest.raises(DriveSyncError) as exc:
            e.migrar(crear_hoja=mundo.crear, push=push_con_intruso)
        assert "durante la migración" in str(exc.value)
        s.rollback()
        assert _config(s)[0] == e.vieja.spreadsheet_id
        assert set(mundo.hojas) == {e.vieja.spreadsheet_id}


def test_una_copia_que_no_es_identica_aborta(factory, monkeypatch):
    with factory() as s:
        mundo = Mundo()
        e = Escena(s, mundo)
        original = Hoja.copy_tab_to

        def copia_rota(self: Hoja, title: str, destination_id: str) -> int:
            sid = original(self, title, destination_id)
            filas = mundo.hojas[destination_id].tabs[f"Copia de {title}"]
            if filas:
                filas[-1][0] = "¡corrupta!"
            return sid

        monkeypatch.setattr(Hoja, "copy_tab_to", copia_rota)
        with pytest.raises(DriveSyncError) as exc:
            e.migrar(crear_hoja=mundo.crear)
        assert "no es idéntica" in str(exc.value)
        s.rollback()
        assert _config(s)[0] == e.vieja.spreadsheet_id


def test_una_hoja_que_ya_es_de_la_cuenta_de_servicio_no_se_migra_otra_vez(factory):
    with factory() as s:
        mundo = Mundo()
        e = Escena(s, mundo)
        mundo.archivos[e.vieja.spreadsheet_id]["owners"] = [SA]
        plan = plan_migracion(s, origen=e.vieja, drive=mundo.drive, sa_email=SA)
        assert plan["ya_migrada"] is True
        with pytest.raises(DriveSyncError) as exc:
            e.migrar(crear_hoja=mundo.crear)
        assert "ya es propiedad" in str(exc.value)


def test_la_verificacion_detecta_una_proteccion_que_no_bloquea(factory):
    """Cualquier rango del espejo que otra persona pueda editar, o que solo avise,
    es un fallo, y el propietario humano sale como exento."""
    with factory() as s:
        mundo = Mundo()
        e = Escena(s, mundo)
        e.vieja.prot[TAB] = [
            {"protectedRangeId": 1, "description": "BoHub · espejo: columnas bloqueadas",
             "warningOnly": False, "editors": {"users": [SA, MARTA]},
             "requestingUserCanEdit": True},
            {"protectedRangeId": 2, "description": "BoHub · espejo: Tracking de Genei",
             "warningOnly": True, "editors": {}, "requestingUserCanEdit": True},
            {"protectedRangeId": 3, "description": "de Bart, no del espejo",
             "warningOnly": False, "editors": {"users": [BART]}},
        ]
        v = verificar_hoja(e.vieja, mundo.drive, e.vieja.spreadsheet_id, SA, TAB)
        assert v["rangos_protegidos"] == 2
        assert any(MARTA in p for p in v["problemas"])
        assert any("solo avisa" in p for p in v["problemas"])
        assert v["exentos"] == [BART]
        assert v["ok"] is False


# --- el script ------------------------------------------------------------------


def test_script_plan_y_migracion(factory, monkeypatch, capsys):
    """El script de extremo a extremo con el mismo Google en memoria: el plan no
    escribe nada; `--crear --apply` migra, re-apunta, archiva y lo cuenta."""
    import argparse

    import scripts.migrar_hoja_seguimiento as cli

    class _SinCerrojo:
        def __enter__(self) -> bool:
            return True

        def __exit__(self, *a: Any) -> bool:
            return False

    with factory() as s:
        mundo = Mundo()
        e = Escena(s, mundo)
        monkeypatch.setattr(cli, "GoogleSheetsClient", lambda info, sid: mundo.abrir(sid))
        monkeypatch.setattr(cli, "create_spreadsheet",
                            lambda info, titulo, **kw: mundo.crear(titulo, {}))
        monkeypatch.setattr("app.erp.seguimiento_sync_job.reconcile_lock",
                            lambda: _SinCerrojo())
        monkeypatch.setattr("app.erp.drive_migracion._push_por_defecto", e.push)

        def run(**kw: Any) -> int:
            base = {"probar": False, "crear": False, "destino": None, "verificar": False,
                    "compartir": None, "rol": "writer", "editor": [], "apply": False}
            return cli._run(argparse.Namespace(**{**base, **kw}), s, {}, SA,
                            mundo.drive, e.vieja)

        assert run(crear=True) == 0
        salida = capsys.readouterr().out
        assert "2 del histórico" in salida and "Nada escrito" in salida
        assert _config(s)[0] == e.vieja.spreadsheet_id

        assert run(crear=True, apply=True, editor=["nuevo@bomedia.es"]) == 0
        salida = capsys.readouterr().out
        assert "✔ Migrada a https://docs.google.com/spreadsheets/d/" in salida
        assert "✔ La protección bloquea a TODAS las personas." in salida
        assert "Compartida con nuevo@bomedia.es (writer)" in salida
        assert f"a solo lectura: {MARTA}" in salida
        nueva = _config(s)[0]
        assert nueva != e.vieja.spreadsheet_id
        assert mundo.archivos[nueva]["owners"] == [SA]

        # Después: verificar la hoja actual y dar acceso a alguien más.
        monkeypatch.setattr(cli, "managed_tab_titles", lambda _s: (TAB, INC))
        assert cli._run(argparse.Namespace(
            probar=False, crear=False, destino=None, verificar=True, compartir=None,
            rol="writer", editor=[], apply=False), s, {}, SA, mundo.drive,
            mundo.abrir(nueva)) == 0
        assert "bloquea a TODAS" in capsys.readouterr().out


# --- transporte real: forma de las llamadas -------------------------------------


class _Resp:
    def __init__(self, body: dict[str, Any], status: int = 200) -> None:
        self.status_code = status
        self.text = json.dumps(body) if body else ""
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def test_transporte_crear_copiar_y_compartir_llaman_a_la_api_correcta(monkeypatch):
    import requests

    from app.erp import drive_sheets

    llamadas: list[dict[str, Any]] = []

    def fake_request(method: str, url: str, **kw: Any) -> _Resp:
        llamadas.append({"method": method, "url": url, **kw})
        if url.endswith("/v4/spreadsheets"):
            return _Resp({"spreadsheetId": "NUEVA"})
        if "fields=sheets.properties" in url:
            return _Resp({"sheets": [{"properties": {"sheetId": 7, "title": TAB}}]})
        if url.endswith(":copyTo"):
            return _Resp({"sheetId": 99, "title": f"Copia de {TAB}"})
        return _Resp({})

    monkeypatch.setattr(drive_sheets, "_service_account_token", lambda info, scopes: "tok")
    monkeypatch.setattr(requests, "request", fake_request)

    assert drive_sheets.create_spreadsheet({}, "Seguimiento BoHub", locale="es_ES",
                                           time_zone="Europe/Madrid") == "NUEVA"
    assert llamadas[-1]["method"] == "POST"
    assert llamadas[-1]["json"] == {"properties": {
        "title": "Seguimiento BoHub", "locale": "es_ES", "timeZone": "Europe/Madrid"}}

    hoja = drive_sheets.GoogleSheetsClient({}, "VIEJA")
    assert hoja.copy_tab_to(TAB, "NUEVA") == 99
    assert llamadas[-1]["url"].endswith("/VIEJA/sheets/7:copyTo")
    assert llamadas[-1]["json"] == {"destinationSpreadsheetId": "NUEVA"}

    d = drive_sheets.GoogleDriveFiles({})
    d.share("NUEVA", MARTA, "writer")
    assert llamadas[-1]["url"].endswith("/drive/v3/files/NUEVA/permissions")
    assert llamadas[-1]["json"] == {"type": "user", "role": "writer", "emailAddress": MARTA}
    assert llamadas[-1]["params"]["sendNotificationEmail"] == "true"
    assert llamadas[-1]["params"]["supportsAllDrives"] == "true"
    d.update("NUEVA", {"writersCanShare": False})
    assert llamadas[-1]["method"] == "PATCH"
    assert llamadas[-1]["json"] == {"writersCanShare": False}
