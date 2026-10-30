"""El histórico de ventas de FACTUSOL dentro de BoHub: la carga y la consulta
«¿quién compró qué?» (30/10/2026).

**Por qué se vuelca y no se consulta en vivo.** Los ejercicios antiguos
(2014-2022) solo los ve la API mientras alguien los tenga reabiertos en el
escritorio de FACTUSOL; hasta el 30/10 respondían `BDNoExiste`. La ventana se
puede cerrar sola: se vuelca cada ejercicio una vez, se guarda, y BoHub ya no
depende de que siga abierto.

**La carga** (`cargar_ejercicio`), por ejercicio y entera: lee `F_FAC`/`F_LFA`,
`F_ALB`/`F_LAL` y el catálogo de ese año (`F_ART`), y SUSTITUYE lo que hubiera
de ese ejercicio en una sola transacción. Volcarlo dos veces deja lo mismo
(idempotente). Son ~88.000 líneas y ~92 s para los trece ejercicios: no hace
falta incremental fino; el ejercicio en curso se recarga entero cada noche.

- Si la API dice que el ejercicio no existe, se apunta `no_disponible` y NO se
  borra lo ya volcado: se dice en pantalla para que se reabra en el escritorio.
- Fechas imposibles (hay una de 1900): se guarda la original y `fecha` vacía.
- La serie se guarda tal cual y no se interpreta (la 5 no es la misma empresa
  todos los años).
- Las líneas sin artículo son NOTAS (portes, condiciones, garantías…): se
  guardan con su texto y `es_producto` = False.

**La consulta** (`compradores`): qué clientes compraron un artículo o una
familia, con la última compra, las unidades y el documento. Las unidades salen
de las FACTURAS; un cliente que solo tiene albaranes (sin facturar) sale con
los del albarán. Un albarán facturado y su factura son la misma venta: no se
suman los dos.

Solo se LEE FACTUSOL. Nombres y códigos de clientes no van a registros ni a
mensajes de error: los registros solo llevan ejercicios y recuentos.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.erp.ejercicio import PRIMER_EJERCICIO
from app.erp.models.historico import (
    CLASES_FAMILIA,
    ESTADO_CARGADO,
    ESTADO_CARGANDO,
    ESTADO_ERROR,
    ESTADO_NO_DISPONIBLE,
    TIPO_ALBARAN,
    TIPO_FACTURA,
    HistoricoArticulo,
    HistoricoDocumento,
    HistoricoEjercicio,
    HistoricoFamilia,
    HistoricoLinea,
)

logger = logging.getLogger(__name__)



@dataclass(frozen=True)
class _Tablas:
    tipo: str
    cabecera: str
    lineas: str
    cab: str   # sufijo de cabecera (FAC / ALB)
    lin: str   # sufijo de línea (LFA / LAL)


TABLAS = (
    _Tablas(TIPO_FACTURA, "F_FAC", "F_LFA", "FAC", "LFA"),
    _Tablas(TIPO_ALBARAN, "F_ALB", "F_LAL", "ALB", "LAL"),
)


def ejercicios_posibles(en_curso: str | int) -> list[str]:
    """Del más reciente al primero (2014)."""
    return [str(a) for a in range(int(en_curso), PRIMER_EJERCICIO - 1, -1)]


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _texto(valor: Any, largo: int | None = None) -> str | None:
    t = str(valor).strip() if valor is not None else ""
    if not t:
        return None
    return t[:largo] if largo else t


def _numero(valor: Any) -> float | None:
    if valor is None or valor == "":
        return None
    try:
        return round(float(str(valor).replace(",", ".")), 4)
    except (TypeError, ValueError):
        return None


def _entero(valor: Any) -> int | None:
    try:
        return int(float(str(valor).strip()))
    except (TypeError, ValueError):
        return None


def fecha_razonable(valor: Any) -> date | None:
    """La fecha si es de verdad (2000-2100); None si es imposible o no se
    entiende (p. ej. 1900-01-01). Nunca lanza."""
    t = str(valor or "").strip()[:10]
    try:
        d = date.fromisoformat(t)
    except ValueError:
        return None
    return d if 2000 <= d.year <= 2100 else None


def _sin_base_de_datos(exc: Exception) -> bool:
    from app.erp.ejercicio import es_sin_base_de_datos  # noqa: PLC0415

    return es_sin_base_de_datos(exc)


def _estado(session: Session, ejercicio: str) -> HistoricoEjercicio:
    fila = session.get(HistoricoEjercicio, ejercicio)
    if fila is None:
        fila = HistoricoEjercicio(ejercicio=ejercicio, estado=ESTADO_ERROR)
        session.add(fila)
    return fila


def cargar_ejercicio(session: Session, client: Any, ejercicio: str) -> dict[str, Any]:
    """Vuelca un ejercicio entero (sustituyendo lo que hubiera). Devuelve el
    estado y los recuentos. Hace commit. Si la API no tiene el ejercicio o
    falla, no toca lo ya volcado y lo apunta."""
    ejercicio = str(ejercicio)
    t0 = time.monotonic()
    try:
        leido = {
            t.tipo: (
                client.load_table(t.cabecera, filtro="1=1", ejercicio=ejercicio),
                client.load_table(t.lineas, filtro="1=1", ejercicio=ejercicio),
            )
            for t in TABLAS
        }
        articulos = client.load_table("F_ART", filtro="1=1", ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001 — se apunta y no para a los demás
        session.rollback()
        fila = _estado(session, ejercicio)
        fila.intento_at = _ahora()
        if _sin_base_de_datos(exc):
            fila.estado = ESTADO_NO_DISPONIBLE
            fila.detalle = ("FACTUSOL no tiene este ejercicio abierto para la API: "
                            "reábrelo en el escritorio de FACTUSOL y vuelve a cargarlo.")
        else:
            fila.estado = ESTADO_ERROR
            fila.detalle = f"No se ha podido leer FACTUSOL ({type(exc).__name__})."
        session.commit()
        logger.info("historico: ejercicio %s sin cargar (%s)", ejercicio, fila.estado)
        return {"ejercicio": ejercicio, "estado": fila.estado, "detalle": fila.detalle}

    # Sustituir el ejercicio entero, en una sola transacción.
    session.execute(delete(HistoricoLinea).where(HistoricoLinea.ejercicio == ejercicio))
    session.execute(delete(HistoricoDocumento).where(HistoricoDocumento.ejercicio == ejercicio))
    session.execute(delete(HistoricoArticulo).where(HistoricoArticulo.ejercicio == ejercicio))

    vistos: set[str] = set()
    catalogo = []
    for a in articulos:
        codart = _texto(a.get("CODART"), 64)
        if not codart or codart in vistos:
            continue
        vistos.add(codart)
        catalogo.append({
            "ejercicio": ejercicio, "codart": codart,
            "descripcion": _texto(a.get("DESART"), 255),
            "familia": _texto(a.get("FAMART"), 32),
            "equart": _texto(a.get("EQUART"), 64),
        })
    if catalogo:
        session.execute(HistoricoArticulo.__table__.insert(), catalogo)

    cuentas = {"facturas": 0, "albaranes": 0, "lineas": 0, "lineas_producto": 0, "huerfanas": 0}
    for t in TABLAS:
        cabeceras, lineas = leido[t.tipo]
        docs: dict[tuple[str, int], HistoricoDocumento] = {}
        for c in cabeceras:
            serie, codigo = _texto(c.get(f"TIP{t.cab}"), 8) or "", _entero(c.get(f"COD{t.cab}"))
            if codigo is None or (serie, codigo) in docs:
                continue
            original = c.get(f"FEC{t.cab}")
            docs[(serie, codigo)] = HistoricoDocumento(
                ejercicio=ejercicio, tipo=t.tipo, serie=serie, codigo=codigo,
                fecha=fecha_razonable(original), fecha_original=_texto(original, 32),
                codcli=_texto(c.get(f"CLI{t.cab}"), 32),
                cliente_nombre=_texto(c.get(f"CNO{t.cab}"), 255),
                referencia=_texto(c.get(f"REF{t.cab}"), 120),
            )
        session.add_all(docs.values())
        session.flush()   # los id, para las líneas
        filas = []
        for r in lineas:
            doc = docs.get((_texto(r.get(f"TIP{t.lin}"), 8) or "", _entero(r.get(f"COD{t.lin}"))))
            if doc is None:
                cuentas["huerfanas"] += 1
                continue
            codart = _texto(r.get(f"ART{t.lin}"), 64)
            filas.append({
                "documento_id": doc.id, "ejercicio": ejercicio,
                "posicion": _entero(r.get(f"POS{t.lin}")), "codart": codart,
                "descripcion": _texto(r.get(f"DES{t.lin}")),
                "cantidad": _numero(r.get(f"CAN{t.lin}")), "precio": _numero(r.get(f"PRE{t.lin}")),
                "total": _numero(r.get(f"TOT{t.lin}")), "es_producto": bool(codart),
            })
        if filas:
            session.execute(HistoricoLinea.__table__.insert(), filas)
        cuentas["facturas" if t.tipo == TIPO_FACTURA else "albaranes"] = len(docs)
        cuentas["lineas"] += len(filas)
        cuentas["lineas_producto"] += sum(1 for f in filas if f["es_producto"])

    fila = _estado(session, ejercicio)
    fila.estado = ESTADO_CARGADO
    fila.cargado_at = fila.intento_at = _ahora()
    fila.detalle = (f"{cuentas['huerfanas']} líneas sin su documento, no guardadas."
                    if cuentas["huerfanas"] else None)
    fila.facturas, fila.albaranes = cuentas["facturas"], cuentas["albaranes"]
    fila.lineas, fila.lineas_producto = cuentas["lineas"], cuentas["lineas_producto"]
    fila.articulos = len(catalogo)
    fila.segundos = round(time.monotonic() - t0, 1)
    session.commit()
    logger.info("historico: ejercicio %s cargado: %s documentos, %s líneas", ejercicio,
                cuentas["facturas"] + cuentas["albaranes"], cuentas["lineas"])
    return {"ejercicio": ejercicio, "estado": ESTADO_CARGADO, **cuentas,
            "articulos": len(catalogo), "segundos": float(fila.segundos)}


def cargar_familias(session: Session, client: Any, ejercicio: str) -> int:
    """Las familias (`F_FAM` del ejercicio en curso), conservando la clase que
    haya puesto Bart. Devuelve cuántas. Si falla, no toca nada."""
    try:
        filas = client.load_table("F_FAM", filtro="1=1", ejercicio=str(ejercicio))
    except Exception as exc:  # noqa: BLE001
        logger.info("historico: familias sin cargar (%s)", type(exc).__name__)
        return 0
    actuales = {f.codigo: f for f in session.scalars(select(HistoricoFamilia))}
    for r in filas:
        codigo = _texto(r.get("CODFAM"), 32)
        if not codigo:
            continue
        fam = actuales.get(codigo)
        if fam is None:
            fam = HistoricoFamilia(codigo=codigo)
            session.add(fam)
            actuales[codigo] = fam
        fam.descripcion = _texto(r.get("DESFAM"), 255)
    session.commit()
    return len(filas)


def cargar(
    session: Session, client: Any, ejercicios: list[str], *, en_curso: str,
) -> list[dict[str, Any]]:
    """Carga los ejercicios pedidos (uno que falla no para a los demás) y las
    familias."""
    out = [cargar_ejercicio(session, client, e) for e in ejercicios]
    cargar_familias(session, client, en_curso)
    return out


# --- consultas -------------------------------------------------------------------------


#: Una carga «cargando» desde hace más que esto se murió por el camino.
CARGA_ATASCADA = timedelta(minutes=40)


def estado_ejercicios(session: Session, en_curso: str) -> list[dict[str, Any]]:
    filas = {f.ejercicio: f for f in session.scalars(select(HistoricoEjercicio))}
    out = []
    for e in ejercicios_posibles(en_curso):
        f = filas.get(e)
        estado, detalle = (f.estado, f.detalle) if f else ("sin_cargar", None)
        if (estado == ESTADO_CARGANDO and f.intento_at
                and _ahora() - f.intento_at > CARGA_ATASCADA):
            estado, detalle = ESTADO_ERROR, "La carga se interrumpió: vuelve a lanzarla."
        out.append({
            "ejercicio": e,
            "estado": estado,
            "cargado_at": f.cargado_at.isoformat() if f and f.cargado_at else None,
            "intento_at": f.intento_at.isoformat() if f and f.intento_at else None,
            "detalle": detalle,
            "facturas": f.facturas if f else 0, "albaranes": f.albaranes if f else 0,
            "lineas": f.lineas if f else 0, "lineas_producto": f.lineas_producto if f else 0,
            "lineas_nota": (f.lineas - f.lineas_producto) if f else 0,
            "articulos": f.articulos if f else 0,
            "segundos": float(f.segundos) if f and f.segundos is not None else None,
        })
    return out


def familias(session: Session) -> list[dict[str, Any]]:
    """Las familias, con cuántos artículos (de cualquier año, distintos) y
    cuántas líneas de venta de producto tienen, para que Bart las clasifique."""
    n_art = dict(session.execute(
        select(HistoricoArticulo.familia, func.count(func.distinct(HistoricoArticulo.codart)))
        .group_by(HistoricoArticulo.familia)
    ).all())
    n_ventas = dict(session.execute(
        select(HistoricoArticulo.familia, func.count(HistoricoLinea.id))
        .join(HistoricoArticulo, and_(
            HistoricoArticulo.ejercicio == HistoricoLinea.ejercicio,
            HistoricoArticulo.codart == HistoricoLinea.codart,
        ))
        .where(HistoricoLinea.es_producto.is_(True))
        .group_by(HistoricoArticulo.familia)
    ).all())
    out = [{
        "codigo": f.codigo, "descripcion": f.descripcion, "clase": f.clase,
        "articulos": int(n_art.get(f.codigo, 0)), "ventas": int(n_ventas.get(f.codigo, 0)),
    } for f in session.scalars(select(HistoricoFamilia))]
    return sorted(out, key=lambda x: (-x["ventas"], x["codigo"]))


def clasificar_familia(session: Session, codigo: str, clase: str | None, por: str) -> dict:
    if clase is not None and clase not in CLASES_FAMILIA:
        raise ValueError(f"clase no válida: {clase}")
    fam = session.get(HistoricoFamilia, codigo)
    if fam is None:
        raise LookupError("familia")
    fam.clase, fam.clase_at, fam.clase_por = clase, _ahora(), por
    session.commit()
    return {"codigo": fam.codigo, "descripcion": fam.descripcion, "clase": fam.clase}


def buscar_articulos(session: Session, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Artículos por código o nombre, en el catálogo de cualquier año (el
    nombre del año más reciente), con cuántas líneas de venta tienen."""
    q = (q or "").strip()
    if not q:
        return []
    filas = session.execute(
        select(HistoricoArticulo.codart, HistoricoArticulo.descripcion,
               HistoricoArticulo.familia, HistoricoArticulo.ejercicio)
        .where(or_(HistoricoArticulo.codart == q,
                   HistoricoArticulo.descripcion.ilike(f"%{q}%")))
        .order_by(HistoricoArticulo.ejercicio.desc())
    ).all()
    por_codigo: dict[str, dict[str, Any]] = {}
    for codart, desc, fam, ej in filas:
        por_codigo.setdefault(codart, {"codart": codart, "descripcion": desc,
                                       "familia": fam, "ultimo_ejercicio": ej})
    if not por_codigo:
        return []
    ventas = dict(session.execute(
        select(HistoricoLinea.codart, func.count(HistoricoLinea.id))
        .where(HistoricoLinea.codart.in_(list(por_codigo)), HistoricoLinea.es_producto.is_(True))
        .group_by(HistoricoLinea.codart)
    ).all())
    out = [{**a, "ventas": int(ventas.get(c, 0))} for c, a in por_codigo.items()]
    return sorted(out, key=lambda a: (-a["ventas"], a["codart"]))[:limit]


def compradores(
    session: Session, *, codart: str | None = None, q: str | None = None,
    familia: str | None = None, desde: int | None = None, hasta: int | None = None,
) -> list[dict[str, Any]]:
    """Los clientes que compraron un artículo (por código), lo que case con un
    texto (código o nombre del artículo en el catálogo de su año) o una
    familia, entre esos años (ambos incluidos). Por cliente: nombre, empresa de
    BoHub si está vinculada, última compra (fecha y documento), unidades,
    compras y qué artículos. Solo líneas de producto; facturas antes que
    albaranes (ver el módulo)."""
    from app.models.crm import Company  # noqa: PLC0415

    art = HistoricoArticulo
    lin = HistoricoLinea
    doc = HistoricoDocumento
    stmt = (
        select(lin.codart, lin.cantidad, lin.ejercicio, doc.tipo, doc.serie, doc.codigo,
               doc.fecha, doc.fecha_original, doc.codcli, doc.cliente_nombre,
               art.descripcion, art.familia, lin.descripcion)
        .join(doc, doc.id == lin.documento_id)
        .outerjoin(art, and_(art.ejercicio == lin.ejercicio, art.codart == lin.codart))
        .where(lin.es_producto.is_(True))
    )
    if codart:
        stmt = stmt.where(lin.codart == codart)
    if q:
        stmt = stmt.where(or_(lin.codart == q.strip(), art.descripcion.ilike(f"%{q.strip()}%")))
    if familia:
        stmt = stmt.where(art.familia == familia)
    if desde:
        stmt = stmt.where(lin.ejercicio >= str(desde))
    if hasta:
        stmt = stmt.where(lin.ejercicio <= str(hasta))
    if not (codart or q or familia):
        return []

    clientes: dict[str, dict[str, Any]] = {}
    for (c_art, cant, ej, tipo, serie, codigo, fecha, fecha_orig, codcli, nombre,
         art_desc, _familia, lin_desc) in session.execute(stmt):
        clave = codcli or f"sin-codigo:{nombre or ''}"
        cl = clientes.setdefault(clave, {
            "codcli": codcli, "nombre": nombre, "unidades_factura": 0.0,
            "unidades_albaran": 0.0, "compras": set(), "articulos": {},
            "ultima": None, "_orden": None,
        })
        unidades = float(cant or 0)
        cl["unidades_factura" if tipo == TIPO_FACTURA else "unidades_albaran"] += unidades
        cl["compras"].add((ej, tipo, serie, codigo))
        cl["articulos"][c_art] = art_desc or lin_desc
        # La más reciente: por la fecha si es de verdad; si no, por el ejercicio.
        orden = (fecha or date(int(ej), 1, 1), ej, codigo)
        if cl["_orden"] is None or orden > cl["_orden"]:
            cl["_orden"] = orden
            cl["nombre"] = nombre or cl["nombre"]
            cl["ultima"] = {
                "fecha": fecha.isoformat() if fecha else None,
                "fecha_original": None if fecha else fecha_orig,
                "ejercicio": ej, "tipo": tipo, "documento": f"{serie}-{codigo}",
            }
    codigos = {c["codcli"] for c in clientes.values() if c["codcli"]}
    empresas = {
        str(e.factusol_company_id).strip(): {"id": e.id, "nombre": e.name}
        for e in session.scalars(select(Company).where(Company.factusol_company_id.in_(codigos)))
    } if codigos else {}
    out = []
    for cl in clientes.values():
        # Facturas primero: un albarán facturado y su factura son la misma venta.
        unidades = cl["unidades_factura"] or cl["unidades_albaran"]
        out.append({
            "codcli": cl["codcli"], "nombre": cl["nombre"],
            "empresa": empresas.get(cl["codcli"] or ""),
            "ultima_compra": cl["ultima"],
            "unidades": round(unidades, 3),
            "compras": len(cl["compras"]),
            "articulos": [{"codart": k, "descripcion": v}
                          for k, v in sorted(cl["articulos"].items())],
            "_orden": cl["_orden"],
        })
    out.sort(key=lambda c: c["_orden"], reverse=True)
    for c in out:
        c.pop("_orden")
    return out


def resumen_notas(session: Session) -> dict[str, int]:
    """Cuántas líneas son de producto y cuántas son notas, en todo el histórico."""
    total = session.scalar(select(func.count(HistoricoLinea.id))) or 0
    producto = session.scalar(
        select(func.count(HistoricoLinea.id)).where(HistoricoLinea.es_producto.is_(True))) or 0
    return {"lineas": int(total), "producto": int(producto), "notas": int(total - producto)}
