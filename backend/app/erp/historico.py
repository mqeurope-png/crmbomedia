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
    ESTADO_CARGADO,
    ESTADO_CARGANDO,
    ESTADO_ERROR,
    ESTADO_NO_DISPONIBLE,
    TIPO_ALBARAN,
    TIPO_FACTURA,
    TIPO_PRESUPUESTO,
    TIPOS_VENTA,
    HistoricoArticulo,
    HistoricoArticuloClase,
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
    # Proformas: para abrir su ficha de cualquier año; no son ventas.
    _Tablas(TIPO_PRESUPUESTO, "F_PRE", "F_LPS", "PRE", "LPS"),
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


def _serie(valor: Any) -> str | None:
    """Una serie («5», no «5.0»): igual en las cabeceras, las líneas y el
    documento origen de una línea, para que casen."""
    t = _texto(valor, 8)
    if t and t.replace(".", "", 1).isdigit():
        return str(int(float(t)))
    return t


def _estado_crudo(valor: Any) -> str | None:
    """«2.0» → «2»: la API devuelve los estados con tipos variables."""
    t = _texto(valor, 8)
    if t and t.endswith(".0") and t[:-2].isdigit():
        return t[:-2]
    return t


#: Líneas que son de una VENTA (factura o albarán), no de una proforma.
def _de_venta() -> Any:
    return HistoricoLinea.documento_id.in_(
        select(HistoricoDocumento.id).where(HistoricoDocumento.tipo.in_(TIPOS_VENTA)))


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


def _apuntar_fallo(session: Session, ejercicio: str, estado: str, detalle: str) -> dict:
    """Apunta un fallo sin tocar lo ya volcado (la transacción se deshace)."""
    session.rollback()
    fila = _estado(session, ejercicio)
    fila.intento_at = _ahora()
    fila.estado, fila.detalle = estado, detalle
    session.commit()
    logger.info("historico: ejercicio %s sin cargar (%s)", ejercicio, estado)
    return {"ejercicio": ejercicio, "estado": estado, "detalle": detalle}


def cargar_ejercicio(session: Session, client: Any, ejercicio: str) -> dict[str, Any]:
    """Vuelca un ejercicio entero (sustituyendo lo que hubiera). Devuelve el
    estado y los recuentos. Hace commit. Si la API no tiene el ejercicio, o
    falla leer o guardar, no toca lo ya volcado y lo apunta (solo el tipo de
    error: el mensaje podría llevar datos de clientes). Nunca lanza."""
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
        if _sin_base_de_datos(exc):
            return _apuntar_fallo(
                session, ejercicio, ESTADO_NO_DISPONIBLE,
                "FACTUSOL no tiene este ejercicio abierto para la API: "
                "reábrelo en el escritorio de FACTUSOL y vuelve a cargarlo.")
        return _apuntar_fallo(session, ejercicio, ESTADO_ERROR,
                              f"No se ha podido leer FACTUSOL ({type(exc).__name__}).")
    try:
        cuentas = _sustituir(session, ejercicio, leido, articulos)
        fila = _estado(session, ejercicio)
        fila.estado = ESTADO_CARGADO
        fila.cargado_at = fila.intento_at = _ahora()
        fila.detalle = (f"{cuentas['huerfanas']} líneas sin su documento, no guardadas."
                        if cuentas["huerfanas"] else None)
        fila.facturas, fila.albaranes = cuentas["facturas"], cuentas["albaranes"]
        fila.lineas, fila.lineas_producto = cuentas["lineas"], cuentas["lineas_producto"]
        fila.articulos = cuentas["articulos"]
        fila.con_enlaces = True
        fila.segundos = round(time.monotonic() - t0, 1)
        session.commit()
    except Exception as exc:  # noqa: BLE001 — se deshace entero; lo anterior sigue
        return _apuntar_fallo(session, ejercicio, ESTADO_ERROR,
                              f"No se ha podido guardar en BoHub ({type(exc).__name__}).")
    logger.info("historico: ejercicio %s cargado: %s documentos, %s líneas", ejercicio,
                cuentas["facturas"] + cuentas["albaranes"], cuentas["lineas"])
    return {"ejercicio": ejercicio, "estado": ESTADO_CARGADO, **cuentas,
            "segundos": float(fila.segundos)}


def _sustituir(
    session: Session, ejercicio: str, leido: dict[str, tuple[list, list]], articulos: list,
) -> dict[str, int]:
    """Borra lo del ejercicio y escribe lo leído (sin commit)."""
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

    cuentas = {"facturas": 0, "albaranes": 0, "presupuestos": 0, "lineas": 0,
               "lineas_producto": 0,
               "huerfanas": 0, "articulos": len(catalogo)}
    for t in TABLAS:
        cabeceras, lineas = leido[t.tipo]
        docs: dict[tuple[str, int], HistoricoDocumento] = {}
        for c in cabeceras:
            serie, codigo = _serie(c.get(f"TIP{t.cab}")) or "", _entero(c.get(f"COD{t.cab}"))
            if codigo is None or (serie, codigo) in docs:
                continue
            original = c.get(f"FEC{t.cab}")
            docs[(serie, codigo)] = HistoricoDocumento(
                ejercicio=ejercicio, tipo=t.tipo, serie=serie, codigo=codigo,
                fecha=fecha_razonable(original), fecha_original=_texto(original, 32),
                codcli=_texto(c.get(f"CLI{t.cab}"), 32),
                cliente_nombre=_texto(c.get(f"CNO{t.cab}"), 255),
                referencia=_texto(c.get(f"REF{t.cab}"), 120),
                total=_numero(c.get(f"TOT{t.cab}")),
                estado=_estado_crudo(c.get(f"EST{t.cab}")),
            )
        session.add_all(docs.values())
        session.flush()   # los id, para las líneas
        filas = []
        for r in lineas:
            doc = docs.get((_serie(r.get(f"TIP{t.lin}")) or "", _entero(r.get(f"COD{t.lin}"))))
            if doc is None:
                # El aviso de la pestaña Carga habla de ventas: las proformas
                # huérfanas no cuentan ahí.
                if t.tipo in TIPOS_VENTA:
                    cuentas["huerfanas"] += 1
                continue
            codart = _texto(r.get(f"ART{t.lin}"), 64)
            filas.append({
                "documento_id": doc.id, "ejercicio": ejercicio,
                "posicion": _entero(r.get(f"POS{t.lin}")), "codart": codart,
                "descripcion": _texto(r.get(f"DES{t.lin}")),
                "cantidad": _numero(r.get(f"CAN{t.lin}")), "precio": _numero(r.get(f"PRE{t.lin}")),
                "total": _numero(r.get(f"TOT{t.lin}")), "es_producto": bool(codart),
                "origen_tipo": (_texto(r.get(f"DOC{t.lin}"), 1) or "").upper() or None,
                "origen_serie": _serie(r.get(f"DTP{t.lin}")),
                "origen_codigo": _entero(r.get(f"DCO{t.lin}")) or None,
            })
        if filas:
            session.execute(HistoricoLinea.__table__.insert(), filas)
        if t.tipo not in TIPOS_VENTA:
            cuentas["presupuestos"] = len(docs)
            continue
        cuentas["facturas" if t.tipo == TIPO_FACTURA else "albaranes"] = len(docs)
        cuentas["lineas"] += len(filas)
        cuentas["lineas_producto"] += sum(1 for f in filas if f["es_producto"])
    return cuentas


def cargar_familias(session: Session, client: Any, ejercicio: str,
                    otros: list[str] | tuple[str, ...] = ()) -> int:
    """Las familias (`F_FAM` del ejercicio en curso), conservando la clase que
    haya puesto Bart. Devuelve cuántas.

    1/11/2026: las familias que solo usan artículos de años anteriores (p. ej.
    una «19» borrada de FACTUSOL) entran SIEMPRE en la tabla, también si
    `F_FAM` falla; antes, si fallaba, se quedaban fuera y sus artículos sin
    familia que casara. Y si no tienen nombre, se toma el de la `F_FAM` de los
    otros ejercicios cargados (`otros`), sin pisar ninguno. Los códigos se
    guardan tal cual: «014» y «14» son familias distintas."""
    try:
        filas = client.load_table("F_FAM", filtro="1=1", ejercicio=str(ejercicio))
    except Exception as exc:  # noqa: BLE001
        logger.info("historico: familias sin cargar (%s)", type(exc).__name__)
        filas = []
    try:
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
        # Las familias que solo salen en catálogos de años anteriores (borradas
        # de F_FAM): también se listan, para poder clasificarlas; si no, sus
        # ventas no saldrían al filtrar por clase o gama.
        usadas = set(session.scalars(select(HistoricoArticulo.familia).distinct()
                                     .where(HistoricoArticulo.familia.is_not(None))))
        vistas = {c.strip().upper() for c in actuales}
        for codigo in sorted(usadas):
            if codigo.strip().upper() in vistas:
                continue    # sin distinguir mayúsculas, como la clave en MySQL
            vistas.add(codigo.strip().upper())
            fam = HistoricoFamilia(codigo=codigo, descripcion=None)
            session.add(fam)
            actuales[codigo] = fam
        # Sin nombre: el de la F_FAM de su año, si lo tiene.
        for otro in otros:
            if not any(f.descripcion is None for f in actuales.values()):
                break
            if str(otro) == str(ejercicio):
                continue
            try:
                viejas = client.load_table("F_FAM", filtro="1=1", ejercicio=str(otro))
            except Exception as exc:  # noqa: BLE001
                logger.info("historico: familias de %s sin cargar (%s)", otro,
                            type(exc).__name__)
                continue
            for r in viejas:
                fam = actuales.get(_texto(r.get("CODFAM"), 32) or "")
                if fam is not None and fam.descripcion is None:
                    fam.descripcion = _texto(r.get("DESFAM"), 255)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.info("historico: familias sin guardar (%s)", type(exc).__name__)
        return 0
    return len(filas)


def cargar(
    session: Session, client: Any, ejercicios: list[str], *, en_curso: str,
) -> list[dict[str, Any]]:
    """Carga los ejercicios pedidos (uno que falla no para a los demás) y las
    familias."""
    out = [cargar_ejercicio(session, client, e) for e in ejercicios]
    cargar_familias(session, client, en_curso, otros=sorted(ejercicios, reverse=True))
    return out


# --- consultas -------------------------------------------------------------------------


def _iso_utc(d: datetime | None) -> str | None:
    """Las horas se guardan en UTC sin zona: se dicen como UTC (con `Z`)."""
    return f"{d.isoformat()}Z" if d else None


def _like(q: str) -> str:
    """`%texto%` con los comodines del usuario escapados (`50%`, `1390_A`).
    Escape `/` (no la barra invertida, que MySQL trata aparte en literales)."""
    q = q.replace("/", "//").replace("%", "/%").replace("_", "/_")
    return f"%{q}%"


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
            "cargado_at": _iso_utc(f.cargado_at) if f else None,
            "intento_at": _iso_utc(f.intento_at) if f else None,
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
    cuántas líneas de venta de producto tienen, su clase y gama, y lo que
    proponen las reglas, para que Bart las clasifique."""
    from app.erp.historico_clasificacion import propuestas  # noqa: PLC0415

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
        .where(HistoricoLinea.es_producto.is_(True), _de_venta())
        .group_by(HistoricoArticulo.familia)
    ).all())
    props = propuestas(session)
    out = [{
        "codigo": f.codigo, "descripcion": f.descripcion, "clase": f.clase, "gama": f.gama,
        "articulos": int(n_art.get(f.codigo, 0)), "ventas": int(n_ventas.get(f.codigo, 0)),
        "propuesta": props.get(f.codigo),
    } for f in session.scalars(select(HistoricoFamilia))]
    return sorted(out, key=lambda x: (-x["ventas"], x["codigo"]))


def clasificar_familia(session: Session, codigo: str, clase: str | None, por: str) -> dict:
    """Solo la clase (lo que hacía el #580). ValueError / LookupError."""
    from app.erp.historico_clasificacion import clasificar  # noqa: PLC0415

    return clasificar(session, codigo, {"clase": clase}, por=por)


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
                   HistoricoArticulo.descripcion.ilike(_like(q), escape="/")))
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
        .where(HistoricoLinea.codart.in_(list(por_codigo)),
               HistoricoLinea.es_producto.is_(True), _de_venta())
        .group_by(HistoricoLinea.codart)
    ).all())
    out = [{**a, "ventas": int(ventas.get(c, 0))} for c, a in por_codigo.items()]
    return sorted(out, key=lambda a: (-a["ventas"], a["codart"]))[:limit]


def compradores(
    session: Session, *, codart: str | None = None, q: str | None = None,
    familia: str | None = None, desde: int | None = None, hasta: int | None = None,
    clase: str | None = None, gama: str | None = None,
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
        .where(lin.es_producto.is_(True), doc.tipo.in_(TIPOS_VENTA))
    )
    if codart:
        # También las ventas de sus códigos equivalentes (el mismo modelo con
        # el código de la web o el interno, 1/11/2026).
        from app.erp import historico_equivalencias  # noqa: PLC0415

        grupo = historico_equivalencias.cargar(session).grupo(codart)
        stmt = stmt.where(func.upper(lin.codart).in_(
            sorted({historico_equivalencias.clave(c) for c in grupo})) if len(grupo) > 1
            else lin.codart == codart)
    if q:
        stmt = stmt.where(or_(lin.codart == q.strip(),
                              art.descripcion.ilike(_like(q.strip()), escape="/")))
    if familia:
        stmt = stmt.where(art.familia == familia)
    if clase or gama:
        # La clase y la gama son de la familia del artículo en el catálogo de
        # su año; la clase propia del artículo, si la tiene, manda.
        stmt = stmt.outerjoin(HistoricoFamilia, HistoricoFamilia.codigo == art.familia)
        if clase:
            # La clase que vale: la propia del artículo o la de su familia;
            # «máquina», solo marcada por artículo (31/10/2026).
            from app.erp.historico_maquinas import clase_efectiva  # noqa: PLC0415

            stmt = stmt.outerjoin(HistoricoArticuloClase,
                                  HistoricoArticuloClase.codart == lin.codart).where(
                clase_efectiva(HistoricoArticuloClase.clase, HistoricoFamilia.clase) == clase)
        if gama:
            stmt = stmt.where(HistoricoFamilia.gama == gama)
    from app.erp.historico_maquinas import series_excluidas  # noqa: PLC0415

    if fuera := series_excluidas(session):
        # Las series que no cuentan como venta, fuera (31/10/2026).
        stmt = stmt.where(doc.serie.notin_(sorted(fuera)))
    if desde:
        stmt = stmt.where(lin.ejercicio >= str(desde))
    if hasta:
        stmt = stmt.where(lin.ejercicio <= str(hasta))
    if not (codart or q or familia or clase or gama):
        return []

    clientes: dict[str, dict[str, Any]] = {}
    for (c_art, cant, ej, tipo, serie, codigo, fecha, fecha_orig, codcli, nombre,
         art_desc, _familia, lin_desc) in session.execute(stmt):
        clave = codcli or f"sin-codigo:{nombre or ''}"
        cl = clientes.setdefault(clave, {
            "codcli": codcli, "nombre": nombre, "por_articulo": {}, "articulos": {},
            "ultima": None, "_orden": None,
        })
        # Por artículo y tipo: unidades y documentos (se elige al final).
        pa = cl["por_articulo"].setdefault(c_art, {
            TIPO_FACTURA: [0.0, set()], TIPO_ALBARAN: [0.0, set()]})
        pa[tipo][0] += float(cant or 0)
        pa[tipo][1].add((ej, tipo, serie, codigo))
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
        # Por artículo, las facturas; los albaranes solo si de ese artículo
        # no hay ninguna: un albarán facturado y su factura son la misma venta.
        unidades, docs = 0.0, set()
        for pa in cl["por_articulo"].values():
            unid, ds = pa[TIPO_FACTURA] if pa[TIPO_FACTURA][1] else pa[TIPO_ALBARAN]
            unidades += unid
            docs |= ds
        out.append({
            "codcli": cl["codcli"], "nombre": cl["nombre"],
            "empresa": empresas.get(cl["codcli"] or ""),
            "ultima_compra": cl["ultima"],
            "unidades": round(unidades, 3),
            "compras": len(docs),
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
    total = session.scalar(select(func.count(HistoricoLinea.id)).where(_de_venta())) or 0
    producto = session.scalar(
        select(func.count(HistoricoLinea.id)).where(
            HistoricoLinea.es_producto.is_(True), _de_venta())) or 0
    return {"lineas": int(total), "producto": int(producto), "notas": int(total - producto)}
