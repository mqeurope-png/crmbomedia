"""El parque instalado: qué máquinas tiene cada cliente (30/10/2026).

Sale de las ventas volcadas (`historico_*`): las líneas de producto cuyo
artículo es, en el catálogo de su año, de una familia de clase **máquina**.
Un accesorio, un recambio o un consumible no son máquinas.

- **Una venta, una vez.** Un albarán y su factura son la misma venta:
  cuenta la factura. Se emparejan por el enlace que FACTUSOL guarda en cada
  línea (`DOC*`/`DTP*`/`DCO*`, desde la 0157): la factura que apunta al
  albarán, o los dos que apuntan al mismo presupuesto o pedido; mismo
  artículo y sin pasar de las unidades. Si la proforma común pedía unidades
  para las dos, son entregas distintas. El `ESTALB` 1 no se usa (puede venir
  heredado de la proforma sin factura), salvo en los años aún sin recargar
  tras la 0157, que siguen la regla de antes. Un abono (línea en negativo)
  anula la venta más reciente de ese artículo.
- **Limitación: sin enlace no se empareja.** Si el albarán y la factura se
  teclearon a mano por separado, no hay nada que los una y saldrán las dos
  (dos compras del mismo modelo también son dos). El albarán avisa cuando
  hay una factura del mismo artículo a menos de `DIAS_MISMA_VENTA` días que
  no cubre ni apunta a ningún albarán, con el importe y la referencia de los
  dos, y se contesta (1/11/2026, `ParqueMismaVenta`): «Sí, es la misma venta»
  lo deja fuera del parque como **duplicado** (no es una baja: no toca
  `parque_bajas` ni cuenta en «tiene baja»); «No, son dos compras» quita el
  aviso para ese par para siempre. Un albarán con la máquina a 0 € que dice
  «sustitución» sale ya **propuesto** como la misma venta que la factura
  ANTERIOR del modelo (a cualquier distancia) que siga en servicio y sin
  abonos del modelo: no cuenta mientras nadie diga que no.
  Un pedido de cliente (`C`) no se vuelca: dos entregas del mismo pedido
  (un albarán y una factura directa) se casan como si fueran una. El enlace
  tampoco lleva el ejercicio: se casa por serie y número dentro del cliente
  (si dos años repiten número, gana la fecha más cercana).
- **Máquina se decide por artículo** (31/10/2026): solo cuenta un artículo
  marcado como máquina (`historico_articulos_clase`, ver
  `historico_maquinas`). La clase de la familia ya no basta: lo no marcado no
  entra. Y solo las series que cuentan como venta (`historico_series`).
- **La misma máquina con dos códigos** (1/11/2026): el código interno de
  FACTUSOL y el de la web (FLUX BEAMBOX 40W y «34 · Beambox … (40W)»). Si
  los dos están marcados, el cliente que compró por los dos canales sale con
  dos. No se juntan solas: el parque AVISA (`mismo_modelo`) cuando un
  cliente tiene, en servicio, dos máquinas de códigos distintos con la misma
  potencia y una palabra de modelo en común. Puede ser la misma máquina o dos
  compras; lo decide Bart (dando de baja una).
- **Incompleto a sabiendas.** Lo vendido antes de 2014, como línea de nota o
  en otro sitio no sale: se **añade a mano** (`ParqueMaquinaManual`). Lo que
  ya no está en servicio se **da de baja** y deja de contar en el parque
  activo (lo que miran dormidos y huecos).
- **La baja de una máquina volcada** se guarda por una clave estable a las
  recargas (`ejercicio|tipo|serie|codigo|posicion|codart`), porque las líneas
  cambian de id en cada carga. Limitación conocida: si se edita el documento
  en FACTUSOL y cambian las líneas de orden, la baja se pierde o cae en otra.

Solo lee FACTUSOL (lo volcado). Lo que se escribe (manuales y bajas) es de
BoHub.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.erp import historico_equivalencias as equivalencias
from app.erp.historico_maquinas import series_excluidas
from app.erp.models.historico import (
    TIPO_ALBARAN,
    TIPO_FACTURA,
    TIPO_PRESUPUESTO,
    HistoricoArticulo,
    HistoricoArticuloClase,
    HistoricoDocumento,
    HistoricoEjercicio,
    HistoricoFamilia,
    HistoricoGama,
    HistoricoLinea,
    ParqueBaja,
    ParqueMaquinaManual,
    ParqueMismaVenta,
)

CLASE_MAQUINA = "maquina"
#: ESTALB «facturado» (confirmado en el escritorio, `documents.ESTADO_LABELS`).
ALBARAN_FACTURADO = "1"
#: Un albarán y una factura del mismo artículo, sin enlace, a menos de esto:
#: se avisa de que pueden ser la misma venta (no se casan solos).
DIAS_MISMA_VENTA = 60
#: El «tipo» de la baja que enseña la ficha cuando un albarán no cuenta por ser
#: la misma venta que una factura (contestado o propuesto). No se guarda en
#: `parque_bajas`: sale de `ParqueMismaVenta` o de la propuesta.
MISMA_VENTA = "misma_venta"
#: Un envío de sustitución: la máquina a 0 € y esto en la referencia o en la
#: línea (sin tildes ni mayúsculas). Sale propuesto como la misma venta.
SUSTITUCION = re.compile(r"\bsu(?:b)?stitucion")
MOTIVO_SUSTITUCION = "Envío de sustitución a 0 €"


#: Palabras que no distinguen un modelo de otro (marca, tipo, idioma).
PALABRAS_GENERICAS = frozenset({
    "FLUX", "LASER", "LASERS", "CORTADORA", "CORTADOR", "GRABADORA", "GRABADOR", "MAQUINA",
    "IMPRESORA", "PRINTER", "CUTTER", "ENGRAVER", "SOBREMESA", "PORTATIL", "PROFESIONAL",
    "DESKTOP", "PORTABLE", "CO2", "DIODO", "DIODE", "DE", "DEL", "LA", "EL", "LOS", "LAS",
    "CON", "PARA", "AND", "THE", "FOR", "WITH",
})


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def codigos_cliente(codcli: str | None) -> list[str]:
    """El código FACTUSOL tal cual, sin ceros a la izquierda y con ellos
    (`412`, `0412`, `00412`…)."""
    if not codcli or not codcli.strip():
        return []
    c = codcli.strip()
    out = {c}
    if c.isdigit():
        # Todas las formas con ceros a la izquierda (hasta 10 cifras): «412»,
        # «0412», «00412»… son el mismo cliente, como en `clave_cliente`.
        sin = str(int(c))
        out |= {sin.zfill(n) for n in range(len(sin), 11)}
    return sorted(out)


def clave_linea(ejercicio: str, tipo: str, serie: str, codigo: int, posicion: int | None,
                codart: str | None) -> str:
    return f"{ejercicio}|{tipo}|{serie}|{codigo}|{posicion if posicion is not None else ''}|" \
           f"{codart or ''}"


def _gamas(session: Session) -> dict[str, str]:
    return {g.codigo: g.nombre for g in session.scalars(select(HistoricoGama))}


def _ref(f: Any) -> str:
    return f"{f.serie}-{int(f.codigo):06d}"


def _origen(f: Any) -> tuple[str, str, int] | None:
    """El documento origen que apunta la línea, sin el auto-enlace de un
    albarán autónomo (`A` a sí mismo)."""
    if not f.origen_tipo or not f.origen_codigo:
        return None
    o = (f.origen_tipo, f.origen_serie or "", int(f.origen_codigo))
    if f.tipo == TIPO_ALBARAN and o == ("A", f.serie, int(f.codigo)):
        return None
    return o


def _dias(a: Any, b: Any) -> int:
    if a.fecha is None or b.fecha is None:
        return 10**6
    return abs((a.fecha - b.fecha).days)


def _emparejar(filas: list[Any], proformas: dict[tuple[str, int, str], float],
               canon: Any = None) -> tuple[dict[int, float], dict[int, list[Any]]]:
    """Qué parte de cada línea de albarán está ya en una factura, por el
    enlace que guarda FACTUSOL en las líneas:

    1. la línea de factura apunta al albarán (`A`, serie, número): se facturó
       ese albarán;
    2. las dos apuntan al mismo presupuesto o pedido (`P`/`C`, serie, número):
       salen de la misma venta.

    Mismo artículo siempre, y sin pasar de las unidades de la factura. Si hay
    varias candidatas, la de fecha más cercana. En el 2, si la proforma pedía
    unidades para todo (`proformas`: lo pedido por proforma y artículo), son
    entregas distintas y no se casan. Devuelve las unidades de cada albarán
    que quedan sin factura y, por factura, los albaranes que cubre.

    «Mismo artículo» es el mismo MODELO (`canon`, 1/11/2026): un albarán con
    el código interno y su factura con el de la web también se casan."""
    canon = canon or (lambda c: c)
    facturas = [f for f in filas if f.tipo == TIPO_FACTURA and (f.cantidad or 0) > 0]
    albaranes = [f for f in filas if f.tipo == TIPO_ALBARAN and (f.cantidad or 0) > 0]
    queda = {id(a): float(a.cantidad) for a in albaranes}
    cap = {id(f): float(f.cantidad) for f in facturas}
    cubre: dict[int, list[Any]] = {}

    def casar(f: Any, candidatas: list[Any]) -> None:
        for a in sorted(candidatas, key=lambda a: _dias(a, f)):
            if cap[id(f)] <= 0:
                return
            n = min(cap[id(f)], queda[id(a)])
            if n <= 0:
                continue
            cap[id(f)] -= n
            queda[id(a)] -= n
            if a not in cubre.setdefault(id(f), []):
                cubre[id(f)].append(a)

    con_origen = [(f, _origen(f)) for f in facturas]
    # Primero el enlace directo (factura → albarán), después el origen común.
    for f, o in con_origen:
        if o and o[0] == "A":
            casar(f, [a for a in albaranes if canon(a.codart) == canon(f.codart)
                      and (a.serie, int(a.codigo)) == (o[1], o[2])])
    for f, o in con_origen:
        if o and o[0] in ("P", "C"):
            mismas = [a for a in albaranes if canon(a.codart) == canon(f.codart)
                      and _origen(a) == o]
            pedidas = (proformas.get((o[1], o[2], canon(f.codart) or "")) if o[0] == "P"
                       else None)
            if mismas and pedidas is not None:
                servidas = sum(float(x.cantidad) for x in facturas + albaranes
                               if canon(x.codart) == canon(f.codart) and _origen(x) == o)
                if servidas <= pedidas:
                    # Una proforma de dos máquinas: un albarán de una y la
                    # factura de la otra son dos entregas, no la misma.
                    continue
            casar(f, mismas)
    return queda, cubre


def _pedidas_en_proformas(session: Session, filas: list[Any], canon: Any = None,
                          ) -> dict[tuple[str, int, str], float]:
    """Unidades de cada modelo (`canon`) en las proformas de las que salen las
    líneas (`P`, serie, número). Si un número se repite en dos años, el mayor."""
    canon = canon or (lambda c: c)
    refs = {(o[1], o[2]) for f in filas if (o := _origen(f)) and o[0] == "P"}
    if not refs:
        return {}
    lin, doc = HistoricoLinea, HistoricoDocumento
    por_anio: dict[tuple[str, str, int, str], float] = {}
    for ejercicio, serie, codigo, codart, total in session.execute(
        select(doc.ejercicio, doc.serie, doc.codigo, lin.codart, func.sum(lin.cantidad))
        .join(doc, doc.id == lin.documento_id)
        .where(doc.tipo == TIPO_PRESUPUESTO, doc.codigo.in_(sorted({c for _, c in refs})),
               lin.es_producto.is_(True))
        .group_by(doc.ejercicio, doc.serie, doc.codigo, lin.codart)
    ):
        if (serie, int(codigo)) in refs:
            k = (ejercicio, serie, int(codigo), canon(codart) or "")
            por_anio[k] = por_anio.get(k, 0.0) + float(total or 0)
    out: dict[tuple[str, int, str], float] = {}
    for (_ej, serie, codigo, modelo), total in por_anio.items():
        k = (serie, codigo, modelo)
        out[k] = max(out.get(k, 0.0), total)
    return out


def _filas_maquina(session: Session, codigos: list[str] | None, eq: Any) -> list[Any]:
    """Las líneas de máquina (marcadas) de factura y albarán de esos clientes
    (o de todos, con `codigos` None), de las series que cuentan."""
    from app.erp import historico_alias  # noqa: PLC0415

    art, lin, doc, fam = HistoricoArticulo, HistoricoLinea, HistoricoDocumento, HistoricoFamilia
    maquinas = _codigos_maquina(session, eq)
    if not maquinas:
        return []
    # Los códigos de línea con alias del catálogo (1/11/2026) y la familia
    # sin mayúsculas ni el fantasma del cero.
    al_art, al_fam = historico_alias.articulos(session), historico_alias.familias(session)
    maquinas = maquinas | {equivalencias.clave(c) for c, d in al_art.crudo.items()
                           if equivalencias.clave(d) in maquinas}
    filas = session.execute(
        select(doc.codcli, lin.ejercicio, doc.tipo, doc.serie, doc.codigo, doc.fecha,
               doc.fecha_original, doc.estado, lin.posicion, lin.codart,
               lin.descripcion.label("lin_desc"),
               lin.cantidad, lin.total.label("lin_total"), doc.referencia,
               art.descripcion.label("art_desc"), art.familia, fam.gama,
               lin.origen_tipo, lin.origen_serie, lin.origen_codigo)
        .join(doc, doc.id == lin.documento_id)
        .join(art, and_(art.ejercicio == lin.ejercicio, art.codart == al_art.expr(lin.codart)))
        .outerjoin(fam, fam.codigo == al_fam.expr_articulo(art))
        .where(*([doc.codcli.in_(codigos)] if codigos is not None else []),
               doc.tipo.in_((TIPO_FACTURA, TIPO_ALBARAN)),
               lin.es_producto.is_(True),
               # Máquina SOLO si el artículo está marcado (31/10/2026): la clase
               # de la familia ya no basta. Un código equivalente sin decisión
               # propia va con su grupo. Y solo las series que cuentan.
               func.upper(lin.codart).in_(sorted(maquinas)),
               *([doc.serie.notin_(sorted(fuera))] if (fuera := series_excluidas(session))
                 else []))
    ).all()
    return filas


def _contexto(session: Session, filas: list[Any], eq: Any) -> dict[str, Any]:
    from app.erp import historico_alias  # noqa: PLC0415

    al_art = historico_alias.articulos(session)

    def canon(c: str | None) -> str:
        return eq.canonico(al_art.resuelve(c))

    return {"canon": canon, "catalogo": al_art.resuelve,
            "proformas": _pedidas_en_proformas(session, filas, canon),
            # Las respuestas al aviso: (albarán, factura) → sí/no, quién y cuándo.
            "respuestas": {(r.albaran, r.factura): r for r in session.scalars(
                select(ParqueMismaVenta))},
            # Las bajas (de retirada): contra una factura dada de baja no se
            # propone una sustitución (la máquina ya no está; la nueva es la
            # que cuenta).
            "bajas": set(session.scalars(select(ParqueBaja.clave))),
            "enlazados": set(session.scalars(select(HistoricoEjercicio.ejercicio).where(
                HistoricoEjercicio.con_enlaces.is_(True))))}


def maquinas_volcadas(session: Session, codcli: str | None) -> list[dict[str, Any]]:
    """Las máquinas de las ventas volcadas de un cliente, una por línea (con
    su cantidad), sin contar dos veces el albarán y su factura."""
    codigos = codigos_cliente(codcli)
    if not codigos:
        return []
    eq = equivalencias.cargar(session)
    filas = _filas_maquina(session, codigos, eq)
    if not filas:
        return []
    return _de_un_cliente(filas, _contexto(session, filas, eq))


def clave_cliente(codcli: str | None) -> str:
    """El cliente sin ceros a la izquierda (`00412` y `412` son el mismo)."""
    c = (codcli or "").strip()
    return str(int(c)) if c.isdigit() else c


def maquinas_de_todos(session: Session) -> dict[str, list[dict[str, Any]]]:
    """Las máquinas volcadas de TODOS los clientes, por `clave_cliente`, con
    las mismas reglas que la ficha (emparejado, abonos, equivalencias). Una
    consulta, no una por cliente."""
    eq = equivalencias.cargar(session)
    filas = _filas_maquina(session, None, eq)
    if not filas:
        return {}
    ctx = _contexto(session, filas, eq)
    por_cliente: dict[str, list[Any]] = {}
    for f in filas:
        por_cliente.setdefault(clave_cliente(f.codcli), []).append(f)
    return {k: _de_un_cliente(v, ctx) for k, v in por_cliente.items() if k}


def _iso(at: datetime | None) -> str | None:
    return f"{at.isoformat()}Z" if at else None


def _clave_de(f: Any) -> str:
    return clave_linea(f.ejercicio, f.tipo, f.serie, f.codigo, f.posicion, f.codart)


def _importe(f: Any) -> float | None:
    return float(f.lin_total) if getattr(f, "lin_total", None) is not None else None


def es_sustitucion(f: Any) -> bool:
    """La máquina va a 0 € y la referencia o la línea dicen «sustitución»."""
    if _importe(f) != 0:
        return False
    return any(SUSTITUCION.search(_normal(t)) for t in (getattr(f, "referencia", None),
                                                         getattr(f, "lin_desc", None)))


def _doc(f: Any) -> dict[str, Any]:
    return {"tipo": f.tipo, "serie": f.serie, "codigo": f.codigo, "ejercicio": f.ejercicio,
            "numero": _ref(f)}


def _de_un_cliente(filas: list[Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    canon, enlazados = ctx["canon"], ctx["enlazados"]
    respuestas = ctx.get("respuestas") or {}
    con_baja = ctx.get("bajas") or set()
    catalogo = ctx.get("catalogo") or (lambda c: c)
    queda, cubre = _emparejar(filas, ctx["proformas"], canon)
    # Un abono (línea en negativo) anula la venta más reciente de ese artículo.
    abonado: dict[str, float] = {}
    for f in filas:
        if (f.cantidad or 0) < 0:
            abonado[canon(f.codart)] = abonado.get(canon(f.codart), 0.0) - float(f.cantidad)
    # Con abonos del modelo no se sabe cuál anulan: no se propone nada solo.
    con_abono = set(abonado)

    def retirada(x: Any) -> bool:
        """La factura (o un albarán suyo) está dada de baja."""
        return _clave_de(x) in con_baja or any(_clave_de(a) in con_baja
                                                for a in cubre.get(id(x), []))
    facturas = [f for f in filas if f.tipo == TIPO_FACTURA and (f.cantidad or 0) > 0]
    facturados = {canon(f.codart) for f in facturas}
    filas = sorted(filas, key=lambda f: (f.fecha is not None, f.fecha or date.min), reverse=True)
    out = []
    for f in filas:
        if (f.cantidad or 0) <= 0:
            continue
        cantidad = float(f.cantidad)
        sospecha = confirmada = None
        revisadas: list[dict[str, Any]] = []
        if f.tipo == TIPO_ALBARAN:
            cantidad = queda[id(f)]
            if cantidad <= 0:
                continue
            if f.ejercicio not in enlazados:
                # Año aún sin recargar (sin el enlace de las líneas): la regla
                # de antes, para no duplicar mientras tanto.
                if f.estado == ALBARAN_FACTURADO or (f.estado is None
                                                     and canon(f.codart) in facturados):
                    continue
            # Con el enlace, manda el emparejado: un `ESTALB` 1 puede venir
            # heredado de la proforma sin que haya factura. Lo que no se casa
            # sale; si hay una factura del mismo artículo cerca que no cubre
            # ningún albarán ni apunta a uno, se avisa (puede ser la misma
            # venta tecleada a mano, o dos compras: no se decide solo).
            # Cualquier factura del artículo cerca (31/10/2026): también la que
            # ya cubre otro albarán. Un segundo albarán suelto junto a una
            # factura ya emparejada es justo el duplicado que hay que ver.
            mia = _clave_de(f)
            mismo = [x for x in facturas if canon(x.codart) == canon(f.codart)
                     and f not in cubre.get(id(x), [])]    # no su propia factura
            # Lo contestado: «sí» (misma venta) o «no» (dos compras) por par,
            # con una factura que sigue ahí.
            dichas = {_clave_de(x): (x, r) for x in mismo
                      if (r := respuestas.get((mia, _clave_de(x)))) is not None}
            revisadas = [{**_doc(x), "clave": c, "por": r.por, "at": _iso(r.at)}
                         for c, (x, r) in dichas.items() if not r.misma]
            si = next(((c, x, r) for c, (x, r) in dichas.items() if r.misma), None)
            if si is not None:
                c, x, r = si
                confirmada = {**_doc(x), "clave": c, "por": r.por, "at": _iso(r.at)}
            else:
                mismo = [x for x in mismo if _clave_de(x) not in dichas]
                # Una sustitución (a 0 €, «sustitución»), si nadie ha dicho ya
                # que no: con la factura ANTERIOR del modelo, a cualquier
                # distancia, que siga en servicio y sin abonos del modelo.
                anteriores = []
                if es_sustitucion(f) and not revisadas and canon(f.codart) not in con_abono:
                    anteriores = [x for x in mismo if x.fecha and f.fecha
                                  and x.fecha <= f.fecha and not retirada(x)]
                cerca = anteriores or [x for x in mismo if _dias(x, f) <= DIAS_MISMA_VENTA]
                if cerca:
                    x = min(cerca, key=lambda x: _dias(x, f))
                    sospecha = {**_doc(x), "clave": _clave_de(x), "importe": _importe(x),
                                "referencia": x.referencia,
                                "albaran": {"importe": _importe(f), "referencia": f.referencia},
                                # Ya resuelto: se propone como la misma venta.
                                "propuesta": "sustitucion" if anteriores else None}
        if abonado.get(canon(f.codart), 0) > 0:
            anula = min(cantidad, abonado[canon(f.codart)])
            abonado[canon(f.codart)] -= anula
            cantidad -= anula
            if cantidad <= 0:
                continue
        out.append({
            "origen": f.tipo, "clave": clave_linea(f.ejercicio, f.tipo, f.serie, f.codigo,
                                                   f.posicion, f.codart),
            "codart": f.codart, "articulo": f.art_desc or f.lin_desc, "familia": f.familia,
            # El código del catálogo (con el alias de la línea resuelto).
            "codart_catalogo": catalogo(f.codart),
            "gama": f.gama, "fecha": f.fecha.isoformat() if f.fecha else None,
            "fecha_original": None if f.fecha else f.fecha_original,
            "ejercicio": f.ejercicio, "cantidad": cantidad,
            "documento": {"tipo": f.tipo, "serie": f.serie, "codigo": f.codigo,
                          "numero": _ref(f)},
            # Los albaranes de esta misma venta (emparejados por el enlace).
            "albaranes": [{"tipo": a.tipo, "serie": a.serie, "codigo": a.codigo,
                           "ejercicio": a.ejercicio, "numero": _ref(a),
                           "clave": clave_linea(a.ejercicio, a.tipo, a.serie, a.codigo,
                                                a.posicion, a.codart),
                           "codart_catalogo": catalogo(a.codart)}
                          for a in cubre.get(id(f), [])],
            "posible_misma_venta": sospecha,
            # Contestado «sí, es la misma venta» (con esa factura): no cuenta.
            "misma_venta": confirmada,
            # Los pares de los que alguien dijo «no, son dos compras».
            "dos_compras": revisadas,
        })
    return out


def _modelo(nombre: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """Potencias (`40W`) y palabras de modelo de un nombre de máquina, sin
    tildes ni mayúsculas ni las palabras genéricas."""
    t = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode().upper()
    potencias = frozenset(re.sub(r"[.,]0+$", "", p.replace(",", "."))
                          for p in re.findall(r"(\d+(?:[.,]\d+)?)\s*W(?![A-Z])", t))
    palabras = frozenset(w for w in re.findall(r"[A-Z][A-Z0-9]*", t)
                         if len(w) >= 3 and w not in PALABRAS_GENERICAS
                         and not re.fullmatch(r"\d+W", w))
    return potencias, palabras


def _mismo_modelo(a: str | None, b: str | None) -> bool:
    pa, wa = _modelo(a)
    pb, wb = _modelo(b)
    return bool(pa) and pa == pb and bool(wa & wb)


def avisar_mismo_modelo(rows: list[dict[str, Any]], eq: Any = None) -> None:
    """A cada máquina en servicio le apunta las otras del mismo cliente que
    parecen el mismo modelo con OTRO código (la de FACTUSOL y la de la web)."""
    activas = [r for r in rows if r["baja"] is None and r.get("codart")]
    for r in rows:
        r["mismo_modelo"] = []
    for i, a in enumerate(activas):
        for b in activas[i + 1:]:
            if equivalencias.clave(a.get("codart_catalogo") or a["codart"]) == \
                    equivalencias.clave(b.get("codart_catalogo") or b["codart"]):
                continue    # el mismo artículo (también con el alias de la línea)
            if eq is not None and eq.declarado(a["codart"], b["codart"]):
                continue    # par declarado: ya se emparejan; no se avisa
            if not _mismo_modelo(a["articulo"], b["articulo"]):
                continue
            for x, y in ((a, b), (b, a)):
                x["mismo_modelo"].append({
                    "codart": y["codart"], "articulo": y["articulo"],
                    "documento": y.get("documento"), "ejercicio": y.get("ejercicio"),
                })


def _codigos_maquina(session: Session, eq: Any) -> set[str]:
    """Los códigos (en mayúsculas) que cuentan como máquina: los marcados, y
    los equivalentes de un marcado que no tengan decisión propia (si Bart
    dijo que uno del grupo no es máquina, eso manda)."""
    marcas = {equivalencias.clave(p.codart): p.clase
              for p in session.scalars(select(HistoricoArticuloClase))}
    out: set[str] = set()
    for k, clase in marcas.items():
        if clase != CLASE_MAQUINA:
            continue
        for c in eq.grupo(k):
            kc = equivalencias.clave(c)
            if marcas.get(kc, CLASE_MAQUINA) == CLASE_MAQUINA:
                out.add(kc)
    return out


def _normal(texto: str | None) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return t.lower()


def catalogo_maquinas(session: Session) -> list[dict[str, Any]]:
    """Las máquinas del catálogo (1/11/2026): los artículos que cuentan como
    máquina en el parque (los marcados y sus equivalentes), UNO POR MODELO,
    con el nombre, la familia y la gama de su catálogo más reciente. Es lo
    que ofrece «Añadir una máquina» para no escribirla a mano."""
    from app.erp import historico_alias  # noqa: PLC0415

    eq = equivalencias.cargar(session)
    codigos = _codigos_maquina(session, eq)
    if not codigos:
        return []
    al_fam = historico_alias.familias(session)
    familias = {f.codigo: f for f in session.scalars(select(HistoricoFamilia))}
    gamas = _gamas(session)
    ultimo: dict[str, HistoricoArticulo] = {}
    for a in session.scalars(select(HistoricoArticulo).where(
            func.upper(HistoricoArticulo.codart).in_(sorted(codigos)))
            .order_by(HistoricoArticulo.ejercicio)):
        ultimo[equivalencias.clave(a.codart)] = a        # el más reciente gana
    grupos: dict[str, list[str]] = {}
    for k in sorted(codigos):
        grupos.setdefault(equivalencias.clave(eq.canonico(k)), []).append(k)
    out: list[dict[str, Any]] = []
    for canon, ks in grupos.items():
        # El del canónico si está en el catálogo; si no, el primero que esté.
        # Uno que cuente como máquina (el canónico puede ser uno que Bart dijo
        # que no lo es): el del canónico si está en el catálogo; si no, el
        # primero que esté; y si ninguno está, el primero del grupo.
        rep = canon if canon in ultimo and canon in ks else next(
            (k for k in ks if k in ultimo), ks[0])
        a = ultimo.get(rep)
        codart = a.codart.strip() if a else rep
        familia = al_fam.del_articulo(codart, a.familia if a else None)
        f = familias.get(familia or "")
        gama = (f.gama or None) if f else None
        out.append({
            "codart": codart, "descripcion": ((a.descripcion or "").strip() if a else "") or codart,
            "familia": familia, "gama": gama, "gama_nombre": gamas.get(gama or "", gama),
            "equivalentes": sorted(k for k in ks if k != equivalencias.clave(codart)),
        })
    return sorted(out, key=lambda m: (_normal(m["descripcion"]), m["codart"]))


def _por_codigo(catalogo: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {equivalencias.clave(c): m for m in catalogo for c in (m["codart"], *m["equivalentes"])}


def gama_de_manual(m: Any, del_catalogo: dict[str, Any] | None) -> str | None:
    """La gama con la que cuenta una máquina añadida a mano. La del catálogo,
    la de la familia de su artículo AHORA (si Bart la cambia o se la quita,
    cambia aquí, como en una de factura); la que se le puso a mano solo si su
    familia no tiene gama. Si su artículo ya no es una máquina, ninguna. La
    escrita a mano, la que se le puso."""
    if not m.codart:
        return m.gama
    if del_catalogo is None:
        return None
    return del_catalogo["gama"] or m.gama


def maquina_del_catalogo(session: Session, codart: str | None) -> dict[str, Any] | None:
    """La máquina del catálogo con ese código (o uno equivalente), o None."""
    if not codart or not codart.strip():
        return None
    return _por_codigo(catalogo_maquinas(session)).get(equivalencias.clave(codart))


def buscar_maquinas(session: Session, q: str | None, *, limite: int = 25) -> list[dict[str, Any]]:
    """Las máquinas del catálogo que casan con `q` por código (también los
    equivalentes, sin guiones ni espacios) o por nombre: todas las palabras
    tienen que salir. Primero las de código exacto, luego las que empiezan
    por él, luego las que lo contienen, y al final las que casan por nombre."""
    texto = _normal(q).strip()
    if not texto:
        return []
    palabras = texto.split()

    def compacto(t: str) -> str:
        return re.sub(r"[^a-z0-9]", "", t)

    puntuadas: list[tuple[int, dict[str, Any]]] = []
    for m in catalogo_maquinas(session):
        codigos = [_normal(c) for c in (m["codart"], *m["equivalentes"])]
        hay = " ".join([*codigos, _normal(m["descripcion"])])
        if not all(p in hay or compacto(p) in compacto(hay) for p in palabras):
            continue
        cq = compacto(texto)
        if any(compacto(c) == cq for c in codigos):
            puntos = 0
        elif any(compacto(c).startswith(cq) for c in codigos):
            puntos = 1
        elif any(cq in compacto(c) for c in codigos):
            puntos = 2
        else:
            puntos = 3
        puntuadas.append((puntos, m))
    puntuadas.sort(key=lambda pm: pm[0])          # estable: dentro, por nombre
    return [m for _, m in puntuadas[:limite]]


def parque(session: Session, company: Any) -> dict[str, Any]:
    """Las máquinas de una empresa: las volcadas y las añadidas a mano, con
    su baja, la gama con nombre y de dónde sale cada una."""
    gamas = _gamas(session)
    codcli = (getattr(company, "factusol_company_id", None) or "").strip() or None
    volcadas = maquinas_volcadas(session, codcli)
    claves = [c for m in volcadas for c in _claves_de(m)]
    bajas = {b.clave: b for b in session.scalars(
        select(ParqueBaja).where(ParqueBaja.clave.in_(claves)))} if claves else {}
    rows: list[dict[str, Any]] = []
    for m in volcadas:
        # La baja de la fila o la de uno de sus albaranes del MISMO código
        # (dada cuando el albarán aún salía solo, antes de emparejarlo). La de
        # un albarán con el código equivalente no se hereda: antes de declarar
        # el par salían como dos y esa baja era la del duplicado (1/11/2026).
        b = next((bajas[c] for c in _claves_heredables(m) if c in bajas), None)
        pm, mv = m.get("posible_misma_venta"), m.get("misma_venta")
        if mv:
            # Contestado: un duplicado. La baja que hubiera (de Bart) sigue
            # guardada y vuelve si se deshace la respuesta.
            baja = _baja(None, None, mv["por"], tipo=MISMA_VENTA, duplica=_solo_doc(mv))
            baja["at"] = mv["at"]
        elif b is not None:
            baja = _baja(b.at, b.motivo, b.por)
            if pm:
                # Dada de baja como retirada: se pregunta si era la misma venta,
                # pero no se propone nada solo.
                m = {**m, "posible_misma_venta": {**pm, "propuesta": None}}
        elif pm and pm.get("propuesta"):
            # Propuesto (no escrito): no cuenta mientras nadie diga que no.
            baja = {**_baja(None, MOTIVO_SUSTITUCION, None, tipo=MISMA_VENTA,
                            duplica=_solo_doc(pm)), "propuesta": True}
        else:
            baja = None
        rows.append({**m, "id": None, "gama_nombre": gamas.get(m["gama"] or "", m["gama"]),
                     "baja": baja})
    manuales = list(session.scalars(select(ParqueMaquinaManual).where(
        ParqueMaquinaManual.company_id == company.id)))
    del_cat = _por_codigo(catalogo_maquinas(session)) if any(m.codart for m in manuales) else {}
    for m in manuales:
        # Con código, es una máquina del catálogo: la gama y la familia son las
        # de su artículo, como las de una factura (si cambian, cambian aquí).
        c = del_cat.get(equivalencias.clave(m.codart)) if m.codart else None
        gama = gama_de_manual(m, c)
        rows.append({
            "origen": "manual", "clave": None, "id": m.id, "codart": m.codart,
            "del_catalogo": bool(m.codart),
            # Su artículo ya no cuenta como máquina (Bart lo desmarcó): como
            # una de factura, deja de contar; se enseña y se dice por qué.
            "no_cuenta": bool(m.codart) and c is None,
            "articulo": m.descripcion, "familia": c["familia"] if c else None, "gama": gama,
            "gama_nombre": gamas.get(gama or "", gama),
            "fecha": m.fecha.isoformat() if m.fecha else None, "fecha_original": None,
            "ejercicio": str(m.fecha.year) if m.fecha else None, "cantidad": 1.0,
            "documento": None, "nota": m.nota,
            "baja": _baja(m.baja_at, m.baja_motivo, m.baja_por) if m.baja_at else None,
        })
    # Activas primero; dentro, las más recientes arriba.
    rows.sort(key=lambda r: (r["fecha"] or f"{r['ejercicio'] or '0000'}-00-00"), reverse=True)
    rows.sort(key=lambda r: r["baja"] is not None)
    avisar_mismo_modelo(rows, equivalencias.cargar(session))
    activas = [r for r in rows if r["baja"] is None and not r.get("no_cuenta")]
    return {
        "codcli": codcli,
        # Sin cliente de FACTUSOL vinculado no se sabe qué ha comprado: se dice,
        # porque una lista vacía se leería como «no ha comprado nunca».
        "sin_cliente_factusol": codcli is None,
        # Marcada «no es cliente final»: se ven, pero no cuentan (1/11/2026).
        "no_cliente_final": bool(getattr(company, "no_cliente_final", False)),
        "maquinas": rows,
        "activas": len(activas),
    }


def _solo_doc(d: dict[str, Any]) -> dict[str, Any]:
    return {k: d[k] for k in ("tipo", "serie", "codigo", "ejercicio", "numero")}


def _claves_heredables(m: dict[str, Any]) -> list[str]:
    """La clave de la fila y las de sus albaranes del MISMO artículo (con el
    alias de la línea resuelto, no por equivalencia)."""
    mio = equivalencias.clave(m.get("codart_catalogo") or m["codart"])
    return [m["clave"], *(a["clave"] for a in m.get("albaranes") or []
                          if equivalencias.clave(a.get("codart_catalogo")
                                                 or a["clave"].rsplit("|", 1)[-1]) == mio)]


def _claves_de(m: dict[str, Any]) -> list[str]:
    return [m["clave"], *(a["clave"] for a in m.get("albaranes") or [])]


def _baja(at: datetime | None, motivo: str | None, por: str | None, *,
          tipo: str | None = None, duplica: dict[str, Any] | None = None) -> dict[str, Any]:
    """`tipo` None: retirada (una baja guardada); `misma_venta`: no cuenta por
    ser la misma venta que la factura `duplica` (contestado o propuesto)."""
    return {"at": f"{at.isoformat()}Z" if at else None, "motivo": motivo, "por": por,
            "tipo": tipo, "duplica": duplica, "propuesta": False}


def anadir_manual(session: Session, company_id: str, datos: dict[str, Any], *,
                  por: str) -> ParqueMaquinaManual:
    """ValueError si falta la descripción, la gama no existe o el código no
    es una máquina del catálogo.

    Dos formas (1/11/2026): elegida del catálogo (`codart`), y entonces la gama
    sale de la familia de su artículo y cuenta como una de factura; o escrita a
    mano (sin código), para lo que no está en el catálogo: comprada en otro
    sitio o vendida antes de 2014."""
    codart = (datos.get("codart") or "").strip()[:64] or None
    cat = None
    if codart is not None:
        cat = maquina_del_catalogo(session, codart)
        if cat is None:
            raise ValueError("codart")
        codart = cat["codart"]
    descripcion = ((datos.get("descripcion") or "").strip()
                   or (cat["descripcion"] if cat else ""))[:255]
    if not descripcion:
        raise ValueError("descripcion")
    # La del catálogo no se copia: cuenta la de su familia en cada momento
    # (`gama_de_manual`). Solo se guarda la que se elige a mano cuando su
    # familia no tiene.
    gama = None if cat and cat["gama"] else (datos.get("gama") or None)
    if gama is not None and session.get(HistoricoGama, gama) is None:
        raise ValueError("gama")
    fecha = datos.get("fecha")
    if isinstance(fecha, str) and fecha:
        fecha = date.fromisoformat(fecha[:10])
    m = ParqueMaquinaManual(
        company_id=company_id, codart=codart,
        descripcion=descripcion, gama=gama, fecha=fecha or None,
        nota=(datos.get("nota") or "").strip()[:255] or None,
        creado_por=por, creado_at=_ahora(),
    )
    session.add(m)
    session.commit()
    return m


def borrar_manual(session: Session, company_id: str, maquina_id: str) -> None:
    m = session.get(ParqueMaquinaManual, maquina_id)
    if m is None or m.company_id != company_id:
        raise LookupError("maquina")
    session.delete(m)
    session.commit()


def dar_baja(session: Session, company: Any, *, clave: str | None = None,
             maquina_id: str | None = None, motivo: str | None, por: str) -> None:
    """Da de baja una máquina de esta empresa (volcada por su clave o manual
    por su id). LookupError si no es de su parque."""
    motivo = (motivo or "").strip()[:255] or None
    if maquina_id:
        m = session.get(ParqueMaquinaManual, maquina_id)
        if m is None or m.company_id != company.id:
            raise LookupError("maquina")
        m.baja_at, m.baja_motivo, m.baja_por = _ahora(), motivo, por
    else:
        claves = {x["clave"] for x in maquinas_volcadas(session, company.factusol_company_id)}
        if not clave or clave not in claves:
            raise LookupError("maquina")
        b = session.get(ParqueBaja, clave) or ParqueBaja(clave=clave)
        b.company_id, b.motivo, b.por, b.at = company.id, motivo, por, _ahora()
        session.add(b)
    session.commit()


def _fila_volcada(session: Session, company: Any, clave: str | None) -> dict[str, Any] | None:
    return next((x for x in maquinas_volcadas(session, company.factusol_company_id)
                 if x["clave"] == clave), None) if clave else None


def contestar_misma_venta(session: Session, company: Any, *, clave: str, factura: str,
                          es_misma: bool, por: str) -> None:
    """La respuesta al aviso «¿misma venta?» del albarán `clave` con la
    factura `factura` (las dos, claves de línea), o el cambio de una ya dada
    con esa factura. LookupError si ese aviso no está (la ficha estaba vieja).

    Sí: el albarán no cuenta, como duplicado. No: ese par no vuelve a
    avisar. No toca `parque_bajas`: la baja que hubiera sigue como estaba."""
    fila = _fila_volcada(session, company, clave)
    vigente = {d["clave"] for d in (fila or {}).get("dos_compras") or []}
    for k in ("posible_misma_venta", "misma_venta"):
        if (fila or {}).get(k):
            vigente.add(fila[k]["clave"])
    if fila is None or factura not in vigente:
        raise LookupError("aviso")
    if es_misma:
        # Un «sí» por albarán: es la misma venta que UNA factura.
        for r in session.scalars(select(ParqueMismaVenta).where(
                ParqueMismaVenta.albaran == clave, ParqueMismaVenta.misma.is_(True),
                ParqueMismaVenta.factura != factura)):
            session.delete(r)
    r = session.get(ParqueMismaVenta, (clave, factura)) or ParqueMismaVenta(
        albaran=clave, factura=factura)
    r.misma, r.company_id, r.por, r.at = es_misma, company.id, por, _ahora()
    session.add(r)
    session.commit()


def deshacer_misma_venta(session: Session, company: Any, *, clave: str, factura: str) -> None:
    """Quita la respuesta (sí o no) de ese par: el aviso vuelve. LookupError si
    no es de su parque."""
    fila = _fila_volcada(session, company, clave)
    r = session.get(ParqueMismaVenta, (clave, factura)) if fila else None
    if r is None:
        raise LookupError("aviso")
    session.delete(r)
    session.commit()


def reactivar(session: Session, company: Any, *, clave: str | None = None,
              maquina_id: str | None = None) -> None:
    if maquina_id:
        m = session.get(ParqueMaquinaManual, maquina_id)
        if m is None or m.company_id != company.id:
            raise LookupError("maquina")
        m.baja_at = m.baja_motivo = m.baja_por = None
    else:
        # Por su parque y no por quién la dio: tras una fusión o un cambio de
        # cliente vinculado, la empresa que la ve es la que puede deshacerla.
        fila = next((x for x in maquinas_volcadas(session, company.factusol_company_id)
                     if x["clave"] == clave), None)
        borrar = [b for c in (_claves_heredables(fila) if fila else [])
                  if (b := session.get(ParqueBaja, c)) is not None]
        if not borrar:
            raise LookupError("maquina")
        for b in borrar:
            session.delete(b)
    session.commit()
