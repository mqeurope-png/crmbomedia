"""Dormidos y huecos: el parque mirado de dos maneras (1/11/2026).

Las dos salen de lo mismo: qué máquinas EN SERVICIO tiene cada cliente (el
parque, con sus reglas: emparejado, abonos, equivalencias, bajas, añadidas a
mano) y qué consumibles y recambios de la GAMA de esas máquinas ha comprado.

- **Dormidos**: tiene una máquina de una gama y lleva `meses_dormido` meses
  (dato, `historico_ajustes`) sin comprar consumibles ni recambios de esa gama,
  habiéndolos comprado antes. O la máquina está parada o se los compra a
  otro; las dos cosas se arreglan con una llamada. Ordenados por lo que más
  duele: lo que compraba en los 12 meses antes de parar, y cuánto lleva sin
  comprar.
- **Huecos**: tiene una máquina de una gama y NUNCA ha comprado un consumible
  de esa gama (tiene una UV LED y nunca una tinta; una Flux y nunca un
  filtro). Una máquina de hace menos de `meses_gracia_hueco` meses aún no
  cuenta: no le ha dado tiempo.

**La gama manda.** Un consumible cuenta para la gama de SU familia (la clase
la decide la familia, o la clase propia del artículo si Bart le puso una).
Si al mirarlo sale algo raro —todos los de una gama como hueco—, es que una
familia está mal clasificada: se dice, no se parchea la consulta.

Una máquina dada de baja no cuenta. Una máquina sin gama no se puede
cruzar: se cuenta aparte. Solo lee lo volcado; FACTUSOL no se toca.
"""
from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.erp import historico_equivalencias as equivalencias
from app.erp import parque
from app.erp.historico_maquinas import clase_efectiva_py, series_excluidas
from app.erp.models.historico import (
    TIPO_FACTURA,
    TIPOS_VENTA,
    HistoricoAjuste,
    HistoricoArticulo,
    HistoricoArticuloClase,
    HistoricoDocumento,
    HistoricoFamilia,
    HistoricoGama,
    HistoricoLinea,
    ParqueBaja,
    ParqueMaquinaManual,
)

AJUSTE_MESES_DORMIDO = "meses_dormido"
AJUSTE_MESES_GRACIA = "meses_gracia_hueco"
POR_DEFECTO = {AJUSTE_MESES_DORMIDO: 12, AJUSTE_MESES_GRACIA: 3}
CONSUMIBLE, RECAMBIO = "consumible", "recambio"


def _hoy() -> date:
    return datetime.now(UTC).date()


def ajustes(session: Session) -> dict[str, int]:
    out = dict(POR_DEFECTO)
    for clave in POR_DEFECTO:
        fila = session.get(HistoricoAjuste, clave)
        try:
            if fila is not None:
                out[clave] = int(float(fila.valor))
        except (TypeError, ValueError):
            pass
    return out


def guardar_ajustes(session: Session, cambios: dict[str, int | None]) -> dict[str, int]:
    """ValueError si un valor no es un número de meses razonable (1–120)."""
    for clave, valor in cambios.items():
        if clave not in POR_DEFECTO or valor is None:
            continue
        if not 1 <= int(valor) <= 120:
            raise ValueError("meses")
        fila = session.get(HistoricoAjuste, clave) or HistoricoAjuste(clave=clave)
        fila.valor = str(int(valor))
        session.add(fila)
    session.commit()
    return ajustes(session)


def _hace_un_anio(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:      # 29 de febrero
        return d.replace(year=d.year - 1, day=28)


def _meses(desde: date | None, hasta: date) -> int | None:
    if desde is None:
        return None
    return (hasta.year - desde.year) * 12 + hasta.month - desde.month - (hasta.day < desde.day)


def _fecha(m: dict[str, Any]) -> date | None:
    if m.get("fecha"):
        return date.fromisoformat(m["fecha"][:10])
    if m.get("ejercicio") and str(m["ejercicio"]).isdigit():
        return date(int(m["ejercicio"]), 1, 1)
    return None


def _parque_activo(session: Session) -> dict[str, list[dict[str, Any]]]:
    """Por cliente (`clave_cliente`): sus máquinas en servicio, volcadas y a
    mano, con gama y fecha."""
    from app.erp import historico_alias  # noqa: PLC0415

    al_fam = historico_alias.familias(session)
    volcadas = parque.maquinas_de_todos(session)
    bajas = set(session.scalars(select(ParqueBaja.clave)))
    out: dict[str, list[dict[str, Any]]] = {}
    for cli, filas in volcadas.items():
        for m in filas:
            if any(c in bajas for c in parque._claves_heredables(m)):
                continue
            out.setdefault(cli, []).append({
                "codart": m["codart"], "articulo": m["articulo"], "gama": m["gama"],
                # La familia que manda (con el respaldo de otro ejercicio y la
                # caja / el fantasma del cero resueltos): la que hay que tocar.
                "familia": al_fam.del_articulo(m.get("codart_catalogo") or m["codart"],
                                               m.get("familia")),
                "fecha": _fecha(m), "origen": m["origen"]})
    from app.models.crm import Company  # noqa: PLC0415

    for man, codcli in session.execute(
            select(ParqueMaquinaManual, Company.factusol_company_id)
            .join(Company, Company.id == ParqueMaquinaManual.company_id)
            .where(ParqueMaquinaManual.baja_at.is_(None))):
        cli = parque.clave_cliente(codcli)
        if cli:
            out.setdefault(cli, []).append({
                "codart": man.codart, "articulo": man.descripcion, "gama": man.gama,
                "familia": None, "fecha": man.fecha, "origen": "manual"})
    return out


def _compras_por_gama(session: Session) -> dict[tuple[str, str], dict[str, Any]]:
    """Por (cliente, gama): la última compra de consumible o recambio, si ha
    comprado alguna vez consumible, y lo facturado en consumible y recambio
    por fecha (para «lo que compraba»)."""
    from app.erp import historico_alias  # noqa: PLC0415

    lin, doc, art, fam = HistoricoLinea, HistoricoDocumento, HistoricoArticulo, HistoricoFamilia
    propia = HistoricoArticuloClase
    al_art, al_fam = historico_alias.articulos(session), historico_alias.familias(session)
    stmt = (
        select(doc.codcli, doc.tipo, doc.fecha, lin.ejercicio, lin.total, lin.cantidad,
               lin.codart, fam.clase, fam.gama, propia.clase)
        .join(doc, doc.id == lin.documento_id)
        .join(art, and_(art.ejercicio == lin.ejercicio, art.codart == al_art.expr(lin.codart)))
        .join(fam, fam.codigo == al_fam.expr_articulo(art))
        .outerjoin(propia, propia.codart == art.codart)
        .where(lin.es_producto.is_(True), doc.tipo.in_(TIPOS_VENTA), fam.gama.is_not(None),
               # Solo lo que puede ser consumible o recambio (por la familia o
               # por la clase propia): no se traen máquinas ni accesorios.
               or_(fam.clase.in_((CONSUMIBLE, RECAMBIO)),
                   propia.clase.in_((CONSUMIBLE, RECAMBIO))))
    )
    # Un código equivalente de una máquina marcada es máquina (va con su
    # grupo), aunque su familia diga consumible: no cuenta como compra.
    maquinas = parque._codigos_maquina(session, equivalencias.cargar(session))
    if fuera := series_excluidas(session):
        stmt = stmt.where(doc.serie.notin_(sorted(fuera)))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for (codcli, tipo, fecha, ejercicio, total, cantidad, codart, clase_fam, gama,
         clase_propia) in session.execute(stmt):
        clase = clase_efectiva_py(clase_propia, clase_fam)
        if clase not in (CONSUMIBLE, RECAMBIO):
            continue
        # Un abono (devolución) no es una compra.
        if (cantidad or 0) <= 0 or equivalencias.clave(al_art.resuelve(codart)) in maquinas:
            continue
        cli = parque.clave_cliente(codcli)
        if not cli:
            continue
        cuando = fecha or (date(int(ejercicio), 1, 1) if str(ejercicio).isdigit() else None)
        g = out.setdefault((cli, gama), {"ultima": None, "consumible": False, "facturado": []})
        if cuando and (g["ultima"] is None or cuando > g["ultima"]):
            g["ultima"] = cuando
        g["consumible"] |= clase == CONSUMIBLE
        # Lo que compraba, por lo facturado (un albarán facturado y su factura
        # son la misma venta: cuenta la factura).
        if tipo == TIPO_FACTURA and cuando and total:
            g["facturado"].append((cuando, float(total)))
    return out


def _gasto_por_cliente(session: Session) -> dict[str, float]:
    """Lo facturado a cada cliente en todo el histórico (líneas de producto
    de las series que cuentan; un abono resta). Para ordenar por quién más ha
    comprado."""
    lin, doc = HistoricoLinea, HistoricoDocumento
    stmt = (select(doc.codcli, func.sum(lin.total)).join(doc, doc.id == lin.documento_id)
            .where(doc.tipo == TIPO_FACTURA, lin.es_producto.is_(True))
            .group_by(doc.codcli))
    if fuera := series_excluidas(session):
        stmt = stmt.where(doc.serie.notin_(sorted(fuera)))
    out: dict[str, float] = {}
    for codcli, total in session.execute(stmt):
        cli = parque.clave_cliente(codcli)
        if cli:
            out[cli] = out.get(cli, 0.0) + float(total or 0)
    return out


def _clientes(session: Session, claves: set[str]) -> dict[str, dict[str, Any]]:
    """Nombre (el del documento más reciente), empresa de BoHub vinculada y
    su sector (el campo de la ficha de empresa; vacío si no está puesto)."""
    from app.models.crm import Company  # noqa: PLC0415

    doc = HistoricoDocumento
    nombres: dict[str, tuple[date, str]] = {}
    for codcli, nombre, fecha in session.execute(
            select(doc.codcli, doc.cliente_nombre, doc.fecha).where(doc.codcli.is_not(None))):
        cli = parque.clave_cliente(codcli)
        if cli in claves and nombre:
            f = fecha or date.min
            if cli not in nombres or f >= nombres[cli][0]:
                nombres[cli] = (f, nombre)
    empresas: dict[str, dict[str, str]] = {}
    for e in session.scalars(select(Company).where(Company.factusol_company_id.is_not(None))):
        cli = parque.clave_cliente(e.factusol_company_id)
        if cli in claves:
            empresas.setdefault(cli, {"id": e.id, "nombre": e.name,
                                      "sector": (e.sector or "").strip() or None})
    return {c: {"nombre": nombres.get(c, (None, None))[1], "empresa": empresas.get(c),
                "sector": (empresas.get(c) or {}).get("sector")}
            for c in claves}


def informe(session: Session, *, hoy: date | None = None) -> dict[str, Any]:
    hoy = hoy or _hoy()
    aj = ajustes(session)
    umbral, gracia = aj[AJUSTE_MESES_DORMIDO], aj[AJUSTE_MESES_GRACIA]
    gamas = {g.codigo: g.nombre for g in session.scalars(select(HistoricoGama))}
    activo = _parque_activo(session)
    compras = _compras_por_gama(session)

    gasto = _gasto_por_cliente(session)
    dormidos: list[dict[str, Any]] = []
    huecos: list[dict[str, Any]] = []
    por_gama: dict[str, dict[str, int]] = {}
    sin_gama: list[dict[str, Any]] = []
    for cli, maquinas in activo.items():
        por_su_gama: dict[str, list[dict[str, Any]]] = {}
        for m in maquinas:
            if m["gama"]:
                por_su_gama.setdefault(m["gama"], []).append(m)
            else:
                sin_gama.append({"codcli": cli, "codart": m["codart"],
                                 "articulo": m["articulo"], "familia": m["familia"],
                                 "origen": m["origen"]})
        for gama, ms in por_su_gama.items():
            resumen = por_gama.setdefault(gama, {"clientes": 0, "con_consumible": 0,
                                                 "dormidos": 0, "huecos": 0})
            resumen["clientes"] += 1
            fechas = [m["fecha"] for m in ms if m["fecha"]]
            ultima_maquina = max(fechas) if fechas else None
            base = {
                "codcli": cli, "gama": gama, "gama_nombre": gamas.get(gama, gama),
                "maquinas": len(ms),
                "articulos": sorted({m["articulo"] or m["codart"] or "" for m in ms}),
                "ultima_maquina": ultima_maquina.isoformat() if ultima_maquina else None,
                "meses_con_maquina": _meses(ultima_maquina, hoy),
                "gasto_total": round(gasto.get(cli, 0.0), 2),
            }
            c = compras.get((cli, gama))
            if c and c["consumible"]:
                resumen["con_consumible"] += 1
            if c is None or not c["consumible"]:
                # Hueco: nunca un consumible de esa gama. Una máquina recién
                # comprada aún no cuenta.
                meses_maquina = _meses(ultima_maquina, hoy)
                if meses_maquina is None or meses_maquina >= gracia:
                    resumen["huecos"] += 1
                    huecos.append({**base, "solo_recambios": bool(c)})
                continue
            ultima = c["ultima"]
            referencia = max(d for d in (ultima, ultima_maquina) if d)
            meses = _meses(referencia, hoy)
            if meses is None or meses < umbral:
                continue
            desde = _hace_un_anio(ultima)
            compraba = sum(t for f, t in c["facturado"] if desde < f <= ultima)
            resumen["dormidos"] += 1
            dormidos.append({**base, "ultima_compra": ultima.isoformat(),
                             "meses_sin_comprar": _meses(ultima, hoy),
                             "compraba_12_meses": round(compraba, 2)})

    dormidos.sort(key=lambda d: (-d["compraba_12_meses"], -(d["meses_sin_comprar"] or 0),
                                 d["codcli"]))
    # Huecos: arriba quien más ha comprado en total (1/11/2026).
    huecos.sort(key=lambda h: (-h["gasto_total"], -h["maquinas"], h["codcli"]))
    clientes = _clientes(session, {d["codcli"] for d in dormidos} | {h["codcli"] for h in huecos}
                         | {m["codcli"] for m in sin_gama})
    for fila in (*dormidos, *huecos, *sin_gama):
        fila.update(clientes.get(fila["codcli"], {"nombre": None, "empresa": None,
                                                  "sector": None}))
    # Clientes distintos por sector (un cliente puede salir en dos gamas).
    por_sector: dict[str, set[str]] = {}
    for fila in (*dormidos, *huecos):
        por_sector.setdefault(fila["sector"] or "", set()).add(fila["codcli"])
    sectores = {k: len(v) for k, v in por_sector.items()}
    return {
        "ajustes": aj,
        "hoy": hoy.isoformat(),
        "resumen": {
            "clientes_con_maquina": len(activo),
            "maquinas_sin_gama": len(sin_gama),
            # Cuáles son: se arreglan dando gama a su familia.
            "sin_gama": sin_gama,
            # Sector (ficha de empresa) de los que salen en las listas; «» =
            # sin sector o sin empresa vinculada.
            "sectores": [{"sector": k or None, "clientes": v}
                         for k, v in sorted(sectores.items(), key=lambda x: (-x[1], x[0]))],
            "por_gama": [{"gama": g, "gama_nombre": gamas.get(g, g), **v}
                         for g, v in sorted(por_gama.items(), key=lambda x: -x[1]["clientes"])],
        },
        "dormidos": dormidos,
        "huecos": huecos,
    }


def filtrar(datos: dict[str, Any], *, gama: str | None = None, sector: str | None = None,
            meses_min: int | None = None, meses_max: int | None = None,
            gasto_min: float | None = None) -> dict[str, Any]:
    """Las listas, filtradas (el resumen se queda entero). `sector` «-» =
    sin sector. Los meses son los de la máquina más reciente de esa gama."""
    def pasa(f: dict[str, Any]) -> bool:
        if gama and f["gama"] != gama:
            return False
        if sector is not None and sector != "":
            if (f["sector"] or "-") != sector:
                return False
        m = f["meses_con_maquina"]
        if meses_min is not None and (m is None or m < meses_min):
            return False
        if meses_max is not None and (m is None or m > meses_max):
            return False
        return not (gasto_min is not None and f["gasto_total"] < gasto_min)

    return {**datos, "dormidos": [d for d in datos["dormidos"] if pasa(d)],
            "huecos": [h for h in datos["huecos"] if pasa(h)],
            "filtro": {"gama": gama, "sector": sector, "meses_min": meses_min,
                       "meses_max": meses_max, "gasto_min": gasto_min}}


def _euros(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def csv_de(filas: list[dict[str, Any]], tipo: str) -> str:
    """CSV (separado por «;», para Excel en español) de dormidos o huecos."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    if tipo == "dormidos":
        w.writerow(["Código cliente", "Cliente", "Empresa BoHub", "Sector", "Gama", "Máquinas",
                    "Artículos", "Última máquina", "Meses con la máquina",
                    "Última compra de consumible o recambio", "Meses sin comprar",
                    "Compraba (12 meses antes de parar, €)", "Gasto total (€)"])
        for d in filas:
            w.writerow([d["codcli"], d["nombre"] or "", (d["empresa"] or {}).get("nombre", ""),
                        d["sector"] or "", d["gama_nombre"], d["maquinas"],
                        " | ".join(d["articulos"]), d["ultima_maquina"] or "",
                        "" if d["meses_con_maquina"] is None else d["meses_con_maquina"],
                        d["ultima_compra"], d["meses_sin_comprar"],
                        _euros(d["compraba_12_meses"]), _euros(d["gasto_total"])])
    else:
        w.writerow(["Código cliente", "Cliente", "Empresa BoHub", "Sector", "Gama", "Máquinas",
                    "Artículos", "Última máquina", "Meses con la máquina",
                    "Compra recambios pero no consumibles", "Gasto total (€)"])
        for h in filas:
            w.writerow([h["codcli"], h["nombre"] or "", (h["empresa"] or {}).get("nombre", ""),
                        h["sector"] or "", h["gama_nombre"], h["maquinas"],
                        " | ".join(h["articulos"]), h["ultima_maquina"] or "",
                        "" if h["meses_con_maquina"] is None else h["meses_con_maquina"],
                        "sí" if h["solo_recambios"] else "no", _euros(h["gasto_total"])])
    return buf.getvalue()
