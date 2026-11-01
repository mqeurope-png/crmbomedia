"""Los hechos del análisis del parque de una empresa (1/11/2026): todo lo que
se puede CALCULAR, con su documento o su fecha detrás. La IA no escribe nada
de esto: lo lee y, como mucho, lo resume citándolo (ver `ia.py`).

Cinco bloques, en el orden en que se enseñan:

- `tiene`: las máquinas en servicio, con su antigüedad y el documento (o
  «añadida a mano»).
- `consume`: qué compra de la gama de esas máquinas, cuántas veces y cada
  cuánto (la mediana entre compras), con el último documento.
- `no_compra`: el consumible de su gama que nunca le ha comprado. Solo
  artículos de FACTUSOL de clase consumible, con gama de verdad (no «Sin
  gama») y vendidos a algún cliente en los últimos N meses (no se ofrece lo
  descatalogado). Son los únicos productos que se pueden proponer (P1, P2…).
- `cambio`: compraba cada N meses y lleva M sin pedir.
- `no_se_sabe`: lo que no se sabe y se dice (una máquina vieja que nadie ha
  dado de baja, una sin gama, consumible de una gama sin máquina…).

Cada hecho lleva un id (D1, D2…) que la IA cita, y su fuente: un documento
(con su enlace a la ficha), una fecha o una máquina añadida a mano.

Sin cliente de FACTUSOL vinculado, no hay análisis: se dice, en vez de escribir
uno de la nada. Solo lee; no escribe en FACTUSOL ni en ningún sitio.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

#: Cambia si cambia cómo se calculan los hechos: obliga a regenerar.
VERSION = "1"

TIENE, CONSUME, NO_COMPRA, CAMBIO, NO_SE_SABE = (
    "tiene", "consume", "no_compra", "cambio", "no_se_sabe")
BLOQUES = (TIENE, CONSUME, NO_COMPRA, CAMBIO, NO_SE_SABE)
#: En qué se puede apoyar «por dónde entrar»: un dato calculado, no un aviso.
ANCLAS = frozenset({TIENE, CONSUME, NO_COMPRA, CAMBIO})

SIN_CLIENTE, SIN_DATOS, CON_DATOS = "sin_cliente", "sin_datos", "con_datos"

MAX_ARTICULOS_CONSUMO = 6
MAX_PRODUCTOS_POR_GAMA = 5


@dataclass
class Hecho:
    id: str
    bloque: str
    texto: str
    #: {"tipo": "documento", "doc": {tipo, serie, codigo, ejercicio, numero}}
    #: | {"tipo": "fecha", "fecha": iso} | {"tipo": "manual", "fecha": iso|None}
    fuente: dict[str, Any] | None = None
    gama: str | None = None


@dataclass
class Producto:
    id: str
    codart: str
    descripcion: str
    gama: str
    gama_nombre: str
    ventas: int
    ultima_venta: str | None


@dataclass
class Hechos:
    estado: str
    hechos: list[Hecho] = field(default_factory=list)
    productos: list[Producto] = field(default_factory=list)
    #: Lo que identifica los datos de entrada (para saber si hay que regenerar).
    huella: str = ""

    def como_dict(self) -> dict[str, Any]:
        return {"estado": self.estado, "hechos": [asdict(h) for h in self.hechos],
                "productos": [asdict(p) for p in self.productos], "huella": self.huella}


def _fecha(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "fecha desconocida"


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _anios(desde: date, hoy: date) -> int:
    return max(0, hoy.year - desde.year - ((hoy.month, hoy.day) < (desde.month, desde.day)))


def _meses(desde: date, hoy: date) -> int:
    return max(0, (hoy.year - desde.year) * 12 + hoy.month - desde.month
               - (1 if hoy.day < desde.day else 0))


def _ref(serie: str, codigo: int) -> str:
    return f"{serie}-{int(codigo):06d}"


def _doc(tipo: str, serie: str, codigo: int, ejercicio: str) -> dict[str, Any]:
    return {"tipo": tipo, "serie": serie, "codigo": int(codigo), "ejercicio": ejercicio,
            "numero": _ref(serie, codigo)}


def _veces(n: int) -> str:
    return "1 vez" if n == 1 else f"{n} veces"


# --- compras del cliente ---------------------------------------------------------------


def _compras(session: Session, codcli: str) -> list[dict[str, Any]]:
    """Las líneas de consumible y recambio que ha comprado el cliente (facturas
    y albaranes no facturados: un albarán facturado y su factura son la misma
    compra), con su artículo, su gama y su documento. Lo de las series que no
    cuentan y los abonos, fuera; un código de máquina, fuera."""
    from app.erp import historico_alias, parque  # noqa: PLC0415
    from app.erp import historico_equivalencias as equivalencias  # noqa: PLC0415
    from app.erp.historico_maquinas import clase_efectiva_py, series_excluidas  # noqa: PLC0415
    from app.erp.models.historico import (  # noqa: PLC0415
        TIPO_ALBARAN,
        TIPO_FACTURA,
        HistoricoArticulo,
        HistoricoArticuloClase,
        HistoricoDocumento,
        HistoricoFamilia,
        HistoricoLinea,
    )

    lin, doc, art, fam = HistoricoLinea, HistoricoDocumento, HistoricoArticulo, HistoricoFamilia
    propia = HistoricoArticuloClase
    al_art, al_fam = historico_alias.articulos(session), historico_alias.familias(session)
    stmt = (
        select(doc.tipo, doc.serie, doc.codigo, doc.ejercicio, doc.fecha, lin.codart,
               art.descripcion, lin.descripcion, lin.cantidad, lin.total, fam.clase, fam.gama,
               propia.clase)
        .join(doc, doc.id == lin.documento_id)
        .join(art, and_(art.ejercicio == lin.ejercicio, art.codart == al_art.expr(lin.codart)))
        .join(fam, fam.codigo == al_fam.expr_articulo(art))
        .outerjoin(propia, propia.codart == art.codart)
        .where(lin.es_producto.is_(True), doc.codcli.in_(parque.codigos_cliente(codcli)),
               or_(doc.tipo == TIPO_FACTURA,
                   and_(doc.tipo == TIPO_ALBARAN,
                        func.coalesce(doc.estado, "") != parque.ALBARAN_FACTURADO)),
               fam.gama.is_not(None))
    )
    if fuera := series_excluidas(session):
        stmt = stmt.where(doc.serie.notin_(sorted(fuera)))
    maquinas = parque._codigos_maquina(session, equivalencias.cargar(session))
    out = []
    for (tipo, serie, codigo, ejercicio, fecha, codart, desc_art, desc_lin, cantidad, total,
         clase_fam, gama, clase_propia) in session.execute(stmt):
        clase = clase_efectiva_py(clase_propia, clase_fam)
        if clase not in ("consumible", "recambio") or (cantidad or 0) <= 0:
            continue
        if equivalencias.clave(al_art.resuelve(codart)) in maquinas:
            continue
        cuando = fecha or (date(int(ejercicio), 1, 1) if str(ejercicio).isdigit() else None)
        out.append({"doc": _doc(tipo, serie, codigo, ejercicio), "fecha": cuando,
                    "codart": (codart or "").strip(),
                    "descripcion": (desc_art or desc_lin or codart or "").strip(),
                    "clase": clase, "gama": gama, "total": float(total or 0)})
    out.sort(key=lambda c: (c["fecha"] or date.min, c["doc"]["numero"]))
    return out


def _catalogo_consumibles(session: Session, gamas: set[str], *, desde: date, hasta: date
                          ) -> dict[str, list[dict[str, Any]]]:
    """Por gama: los artículos de clase consumible (con gama de verdad, la de
    su familia) vendidos a algún cliente desde `desde`, con cuántas veces y la
    última. Es la lista de lo que se puede proponer."""
    if not gamas:
        return {}
    from app.erp import historico_alias  # noqa: PLC0415
    from app.erp.historico_maquinas import clase_efectiva_py, series_excluidas  # noqa: PLC0415
    from app.erp.models.historico import (  # noqa: PLC0415
        TIPO_FACTURA,
        HistoricoArticulo,
        HistoricoArticuloClase,
        HistoricoDocumento,
        HistoricoFamilia,
        HistoricoLinea,
    )

    lin, doc, art, fam = HistoricoLinea, HistoricoDocumento, HistoricoArticulo, HistoricoFamilia
    propia = HistoricoArticuloClase
    al_art, al_fam = historico_alias.articulos(session), historico_alias.familias(session)
    stmt = (
        select(art.codart, art.descripcion, fam.gama, fam.clase, propia.clase,
               func.count(lin.id), func.max(doc.fecha))
        .join(doc, doc.id == lin.documento_id)
        .join(art, and_(art.ejercicio == lin.ejercicio, art.codart == al_art.expr(lin.codart)))
        .join(fam, fam.codigo == al_fam.expr_articulo(art))
        .outerjoin(propia, propia.codart == art.codart)
        .where(lin.es_producto.is_(True), doc.tipo == TIPO_FACTURA, doc.fecha >= desde,
               doc.fecha <= hasta,
               fam.gama.in_(sorted(gamas)), (lin.cantidad > 0))
        .group_by(art.codart, art.descripcion, fam.gama, fam.clase, propia.clase)
    )
    if fuera := series_excluidas(session):
        stmt = stmt.where(doc.serie.notin_(sorted(fuera)))
    por: dict[tuple[str, str], dict[str, Any]] = {}
    for codart, desc, gama, clase_fam, clase_propia, n, ultima in session.execute(stmt):
        if clase_efectiva_py(clase_propia, clase_fam) != "consumible" or not gama:
            continue
        k = (gama, (codart or "").strip().upper())
        p = por.setdefault(k, {"codart": (codart or "").strip(), "descripcion": "",
                               "gama": gama, "ventas": 0, "ultima": None})
        p["ventas"] += int(n or 0)
        p["descripcion"] = p["descripcion"] or (desc or "").strip() or p["codart"]
        if ultima and (p["ultima"] is None or ultima > p["ultima"]):
            p["ultima"] = ultima
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (gama, _k), p in por.items():
        out[gama].append(p)
    for gama in out:
        out[gama].sort(key=lambda p: (-p["ventas"], p["descripcion"].lower()))
    return out


# --- el cálculo -----------------------------------------------------------------------


def calcular(session: Session, company: Any, *, hoy: date | None = None,
             cfg: dict[str, Any] | None = None) -> Hechos:
    from app.erp import historico_equivalencias as equivalencias  # noqa: PLC0415
    from app.erp import parque  # noqa: PLC0415
    from app.services.analisis_parque.config import normalizar  # noqa: PLC0415

    cfg = normalizar(cfg)
    hoy = hoy or date.today()
    codcli = (getattr(company, "factusol_company_id", None) or "").strip() or None
    if codcli is None:
        return Hechos(estado=SIN_CLIENTE, huella=_huella(SIN_CLIENTE))
    gamas_nombre = parque._gamas(session)
    p = parque.parque(session, company)
    maquinas = [m for m in p["maquinas"] if m["baja"] is None and not m.get("no_cuenta")]
    compras = _compras(session, codcli)
    if not maquinas and not compras:
        return Hechos(estado=SIN_DATOS, huella=_huella(SIN_DATOS, codcli))

    hechos: list[Hecho] = []

    def nuevo(bloque: str, texto: str, fuente: dict[str, Any] | None,
              gama: str | None = None) -> None:
        hechos.append(Hecho(id=f"D{len(hechos) + 1}", bloque=bloque, texto=texto,
                            fuente=fuente, gama=gama))

    # 1. Qué tiene.
    gamas_maquinas: set[str] = set()
    for m in maquinas:
        f = date.fromisoformat(m["fecha"][:10]) if m.get("fecha") else None
        gama = m.get("gama")
        if gama:
            gamas_maquinas.add(gama)
        nombre = m.get("articulo") or m.get("codart") or "una máquina"
        codigo = f" ({m['codart']})" if m.get("codart") else ""
        gama_txt = f"de la gama {gamas_nombre.get(gama, gama)}" if gama else "sin gama"
        if m.get("documento"):
            d = m["documento"]
            fuente = {"tipo": "documento",
                      "doc": _doc(d["tipo"], d["serie"], d["codigo"], m.get("ejercicio") or "")}
            como = f"comprada el {_fecha(f)}"
        else:
            fuente = {"tipo": "manual", "fecha": _iso(f)}
            como = (f"añadida a mano{'' if m.get('del_catalogo') else ' (escrita a mano)'}, "
                    f"de {_fecha(f) if f else 'fecha desconocida'}")
        edad = f" ({_anios(f, hoy)} años)" if f else ""
        nuevo(TIENE, f"Tiene {nombre}{codigo}, {gama_txt}, {como}{edad}.", fuente, gama)
        # Lo que no se sabe de esa máquina.
        if f and _anios(f, hoy) >= int(cfg["anios_sin_confirmar"]):
            nuevo(NO_SE_SABE,
                  f"{nombre}{codigo} es de {f.year}: tiene {_anios(f, hoy)} años y nadie la ha "
                  "dado de baja. No se sabe si sigue en servicio.", fuente, gama)
        if not gama:
            nuevo(NO_SE_SABE, f"{nombre}{codigo} no tiene gama: no se sabe qué consume.",
                  fuente)

    # 2. Qué consume, y 4. qué ha cambiado (por gama).
    por_gama: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in compras:
        por_gama[c["gama"]].append(c)
    eq = equivalencias.cargar(session)
    comprados = {eq.canonico(c["codart"]) for c in compras if c["codart"]}
    for gama in sorted(gamas_maquinas | set(por_gama)):
        nombre_g = gamas_nombre.get(gama, gama)
        cs = por_gama.get(gama, [])
        if gama not in gamas_maquinas:
            # «Qué consume» es de la gama de SUS máquinas; de otra gama, solo se
            # avisa (y se dice que no consta la máquina).
            ultima = cs[-1]
            nuevo(NO_SE_SABE,
                  f"Compra consumible o recambio de {nombre_g} ({_veces(len(cs))}, la última el "
                  f"{_fecha(ultima['fecha'])}), pero no consta ninguna máquina suya de esa gama: "
                  "puede que la comprara en otro sitio.",
                  {"tipo": "documento", "doc": ultima["doc"]}, gama)
            continue
        if not cs:
            # Una máquina y nunca un consumible de su gama: el hueco.
            nuevo(CONSUME, f"No consta ninguna compra de consumible ni recambio de {nombre_g} "
                           "desde 2014.", {"tipo": "fecha", "fecha": "2014-01-01"}, gama)
            continue
        por_articulo: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in cs:
            por_articulo[c["codart"] or c["descripcion"]].append(c)
        orden = sorted(por_articulo.values(),
                       key=lambda xs: (-len({x["doc"]["numero"] for x in xs}),
                                       -(xs[-1]["fecha"] or date.min).toordinal()))
        for xs in orden[:MAX_ARTICULOS_CONSUMO]:
            ultima = xs[-1]
            n = len({(x["doc"]["tipo"], x["doc"]["numero"]) for x in xs})
            nuevo(CONSUME, f"Compra {ultima['descripcion']} ({ultima['codart']}), "
                           f"{ultima['clase']} de {nombre_g}: {_veces(n)}, la última el "
                           f"{_fecha(ultima['fecha'])}.",
                  {"tipo": "documento", "doc": ultima["doc"]}, gama)
        fechas = sorted({c["fecha"] for c in cs if c["fecha"]})
        ultima = cs[-1]
        sin = _meses(ultima["fecha"], hoy) if ultima["fecha"] else None
        if len(fechas) >= 3:
            dias = [(b - a).days for a, b in zip(fechas, fechas[1:], strict=False)]
            cada = max(1, round(median(dias) / 30.44))
            nuevo(CONSUME, f"Compra consumible o recambio de {nombre_g} cada {cada} meses de "
                           f"mediana ({len(fechas)} compras, del {_fecha(fechas[0])} al "
                           f"{_fecha(fechas[-1])}).",
                  {"tipo": "documento", "doc": ultima["doc"]}, gama)
            if sin is not None and sin >= max(3, round(cada * 1.5)):
                nuevo(CAMBIO, f"Compraba consumible o recambio de {nombre_g} cada {cada} meses "
                              f"y lleva {sin} sin pedir (la última compra, el "
                              f"{_fecha(ultima['fecha'])}).",
                      {"tipo": "documento", "doc": ultima["doc"]}, gama)
        elif sin is not None:
            nuevo(CONSUME, f"Pocas compras de {nombre_g} para saber cada cuánto compra "
                           f"({len(fechas)}); la última, hace {sin} meses.",
                  {"tipo": "documento", "doc": ultima["doc"]}, gama)

    # 3. Qué no compra: el consumible de la gama de sus máquinas que nunca ha comprado.
    productos: list[Producto] = []
    desde = hoy - timedelta(days=int(cfg["meses_catalogo"]) * 30.44)
    catalogo = _catalogo_consumibles(session, gamas_maquinas, desde=desde, hasta=hoy)
    for gama in sorted(catalogo):
        nombre_g = gamas_nombre.get(gama, gama)
        nuevos = [x for x in catalogo[gama] if eq.canonico(x["codart"]) not in comprados]
        for x in nuevos[:MAX_PRODUCTOS_POR_GAMA]:
            pid = f"P{len(productos) + 1}"
            productos.append(Producto(id=pid, codart=x["codart"], descripcion=x["descripcion"],
                                      gama=gama, gama_nombre=nombre_g, ventas=x["ventas"],
                                      ultima_venta=_iso(x["ultima"])))
            nuevo(NO_COMPRA, f"Nunca ha comprado {x['descripcion']} ({x['codart']}), consumible "
                             f"de {nombre_g} [{pid}]: se ha vendido {_veces(x['ventas'])} en los "
                             f"últimos {cfg['meses_catalogo']} meses, la última el "
                             f"{_fecha(x['ultima'])}.",
                  {"tipo": "fecha", "fecha": _iso(x["ultima"])}, gama)

    return Hechos(estado=CON_DATOS, hechos=hechos, productos=productos,
                  huella=_huella(CON_DATOS, codcli, maquinas, compras,
                                 [p.codart for p in productos], cfg))


def _huella(estado: str, codcli: str | None = None, maquinas: list[dict[str, Any]] = (),
            compras: list[dict[str, Any]] = (), productos: list[str] = (),
            cfg: dict[str, Any] | None = None) -> str:
    """Lo que, si cambia, obliga a regenerar: las máquinas (y sus bajas y
    gamas), las compras (por documento y artículo), qué productos se pueden
    proponer y los umbrales. No entra «hoy» (si no, cambiaría cada mes: para eso
    está la caducidad)."""
    datos = {
        "v": VERSION, "estado": estado, "codcli": codcli,
        "maquinas": sorted([m.get("clave") or m.get("id") or "", m.get("codart") or "",
                            m.get("gama") or "", m.get("fecha") or ""] for m in maquinas),
        "compras": sorted([c["doc"]["tipo"], c["doc"]["ejercicio"], c["doc"]["numero"],
                           c["codart"], c["gama"]] for c in compras),
        "productos": sorted(productos),
        "cfg": {k: (cfg or {}).get(k) for k in ("anios_sin_confirmar", "meses_catalogo")},
    }
    return hashlib.sha256(json.dumps(datos, sort_keys=True, default=str).encode()).hexdigest()
