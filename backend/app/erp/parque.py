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
  no cubre ni apunta a ningún albarán; se resuelve dando de baja el albarán.
  Un pedido de cliente (`C`) no se vuelca: dos entregas del mismo pedido
  (un albarán y una factura directa) se casan como si fueran una. El enlace
  tampoco lleva el ejercicio: se casa por serie y número dentro del cliente
  (si dos años repiten número, gana la fecha más cercana).
- **Máquina** es la clase de la familia del artículo, salvo que el artículo
  tenga clase propia (`historico_articulos_clase`, «esto no es una máquina»).
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

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

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
)

CLASE_MAQUINA = "maquina"
#: ESTALB «facturado» (confirmado en el escritorio, `documents.ESTADO_LABELS`).
ALBARAN_FACTURADO = "1"
#: Un albarán y una factura del mismo artículo, sin enlace, a menos de esto:
#: se avisa de que pueden ser la misma venta (no se casan solos).
DIAS_MISMA_VENTA = 60


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def codigos_cliente(codcli: str | None) -> list[str]:
    """El código FACTUSOL tal cual y sin ceros a la izquierda (`00412`/`412`)."""
    if not codcli or not codcli.strip():
        return []
    c = codcli.strip()
    out = {c}
    if c.isdigit():
        out.add(str(int(c)))
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
               ) -> tuple[dict[int, float], dict[int, list[Any]]]:
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
    que quedan sin factura y, por factura, los albaranes que cubre."""
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
            casar(f, [a for a in albaranes if a.codart == f.codart
                      and (a.serie, int(a.codigo)) == (o[1], o[2])])
    for f, o in con_origen:
        if o and o[0] in ("P", "C"):
            mismas = [a for a in albaranes if a.codart == f.codart and _origen(a) == o]
            pedidas = proformas.get((o[1], o[2], f.codart or "")) if o[0] == "P" else None
            if mismas and pedidas is not None:
                servidas = sum(float(x.cantidad) for x in facturas + albaranes
                               if x.codart == f.codart and _origen(x) == o)
                if servidas <= pedidas:
                    # Una proforma de dos máquinas: un albarán de una y la
                    # factura de la otra son dos entregas, no la misma.
                    continue
            casar(f, mismas)
    return queda, cubre


def _pedidas_en_proformas(session: Session, filas: list[Any]) -> dict[tuple[str, int, str], float]:
    """Unidades de cada artículo en las proformas de las que salen las líneas
    (`P`, serie, número). Si un número se repite en dos años, el mayor."""
    refs = {(o[1], o[2]) for f in filas if (o := _origen(f)) and o[0] == "P"}
    if not refs:
        return {}
    lin, doc = HistoricoLinea, HistoricoDocumento
    out: dict[tuple[str, int, str], float] = {}
    for serie, codigo, codart, total in session.execute(
        select(doc.serie, doc.codigo, lin.codart, func.sum(lin.cantidad))
        .join(doc, doc.id == lin.documento_id)
        .where(doc.tipo == TIPO_PRESUPUESTO, doc.codigo.in_(sorted({c for _, c in refs})),
               lin.es_producto.is_(True))
        .group_by(doc.ejercicio, doc.serie, doc.codigo, lin.codart)
    ):
        if (serie, int(codigo)) in refs:
            k = (serie, int(codigo), codart or "")
            out[k] = max(out.get(k, 0.0), float(total or 0))
    return out


def maquinas_volcadas(session: Session, codcli: str | None) -> list[dict[str, Any]]:
    """Las máquinas de las ventas volcadas de un cliente, una por línea (con
    su cantidad), sin contar dos veces el albarán y su factura."""
    codigos = codigos_cliente(codcli)
    if not codigos:
        return []
    art, lin, doc, fam = HistoricoArticulo, HistoricoLinea, HistoricoDocumento, HistoricoFamilia
    propia = HistoricoArticuloClase
    filas = session.execute(
        select(lin.ejercicio, doc.tipo, doc.serie, doc.codigo, doc.fecha, doc.fecha_original,
               doc.estado, lin.posicion, lin.codart, lin.descripcion.label("lin_desc"),
               lin.cantidad, art.descripcion.label("art_desc"), art.familia, fam.gama,
               lin.origen_tipo, lin.origen_serie, lin.origen_codigo)
        .join(doc, doc.id == lin.documento_id)
        .join(art, and_(art.ejercicio == lin.ejercicio, art.codart == lin.codart))
        .outerjoin(fam, fam.codigo == art.familia)
        .outerjoin(propia, propia.codart == lin.codart)
        .where(doc.codcli.in_(codigos), doc.tipo.in_((TIPO_FACTURA, TIPO_ALBARAN)),
               lin.es_producto.is_(True),
               # La clase propia del artículo, si la tiene, manda sobre la de su familia.
               func.coalesce(propia.clase, fam.clase) == CLASE_MAQUINA)
    ).all()
    proformas = _pedidas_en_proformas(session, filas)
    queda, cubre = _emparejar(filas, proformas)
    enlazados = set(session.scalars(select(HistoricoEjercicio.ejercicio).where(
        HistoricoEjercicio.con_enlaces.is_(True))))
    # Un abono (línea en negativo) anula la venta más reciente de ese artículo.
    abonado: dict[str, float] = {}
    for f in filas:
        if (f.cantidad or 0) < 0:
            abonado[f.codart] = abonado.get(f.codart, 0.0) - float(f.cantidad)
    facturas = [f for f in filas if f.tipo == TIPO_FACTURA and (f.cantidad or 0) > 0]
    facturados = {f.codart for f in facturas}
    filas = sorted(filas, key=lambda f: (f.fecha is not None, f.fecha or date.min), reverse=True)
    out = []
    for f in filas:
        if (f.cantidad or 0) <= 0:
            continue
        cantidad = float(f.cantidad)
        sospecha = None
        if f.tipo == TIPO_ALBARAN:
            cantidad = queda[id(f)]
            if cantidad <= 0:
                continue
            if f.ejercicio not in enlazados:
                # Año aún sin recargar (sin el enlace de las líneas): la regla
                # de antes, para no duplicar mientras tanto.
                if f.estado == ALBARAN_FACTURADO or (f.estado is None
                                                     and f.codart in facturados):
                    continue
            # Con el enlace, manda el emparejado: un `ESTALB` 1 puede venir
            # heredado de la proforma sin que haya factura. Lo que no se casa
            # sale; si hay una factura del mismo artículo cerca que no cubre
            # ningún albarán ni apunta a uno, se avisa (puede ser la misma
            # venta tecleada a mano, o dos compras: no se decide solo).
            cerca = [x for x in facturas if x.codart == f.codart and id(x) not in cubre
                     and (_origen(x) or ("",))[0] != "A"
                     and _dias(x, f) <= DIAS_MISMA_VENTA]
            if cerca:
                x = min(cerca, key=lambda x: _dias(x, f))
                sospecha = {"tipo": x.tipo, "serie": x.serie, "codigo": x.codigo,
                            "ejercicio": x.ejercicio, "numero": _ref(x)}
        if abonado.get(f.codart, 0) > 0:
            anula = min(cantidad, abonado[f.codart])
            abonado[f.codart] -= anula
            cantidad -= anula
            if cantidad <= 0:
                continue
        out.append({
            "origen": f.tipo, "clave": clave_linea(f.ejercicio, f.tipo, f.serie, f.codigo,
                                                   f.posicion, f.codart),
            "codart": f.codart, "articulo": f.art_desc or f.lin_desc, "familia": f.familia,
            "gama": f.gama, "fecha": f.fecha.isoformat() if f.fecha else None,
            "fecha_original": None if f.fecha else f.fecha_original,
            "ejercicio": f.ejercicio, "cantidad": cantidad,
            "documento": {"tipo": f.tipo, "serie": f.serie, "codigo": f.codigo,
                          "numero": _ref(f)},
            # Los albaranes de esta misma venta (emparejados por el enlace).
            "albaranes": [{"tipo": a.tipo, "serie": a.serie, "codigo": a.codigo,
                           "ejercicio": a.ejercicio, "numero": _ref(a),
                           "clave": clave_linea(a.ejercicio, a.tipo, a.serie, a.codigo,
                                                a.posicion, a.codart)}
                          for a in cubre.get(id(f), [])],
            "posible_misma_venta": sospecha,
        })
    return out


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
        # La baja de la fila o la de uno de sus albaranes (dada cuando el
        # albarán aún salía solo, antes de emparejarlo con su factura).
        b = next((bajas[c] for c in _claves_de(m) if c in bajas), None)
        rows.append({**m, "id": None, "gama_nombre": gamas.get(m["gama"] or "", m["gama"]),
                     "baja": _baja(b.at, b.motivo, b.por) if b else None})
    for m in session.scalars(select(ParqueMaquinaManual).where(
            ParqueMaquinaManual.company_id == company.id)):
        rows.append({
            "origen": "manual", "clave": None, "id": m.id, "codart": m.codart,
            "articulo": m.descripcion, "familia": None, "gama": m.gama,
            "gama_nombre": gamas.get(m.gama or "", m.gama),
            "fecha": m.fecha.isoformat() if m.fecha else None, "fecha_original": None,
            "ejercicio": str(m.fecha.year) if m.fecha else None, "cantidad": 1.0,
            "documento": None, "nota": m.nota,
            "baja": _baja(m.baja_at, m.baja_motivo, m.baja_por) if m.baja_at else None,
        })
    # Activas primero; dentro, las más recientes arriba.
    rows.sort(key=lambda r: (r["fecha"] or f"{r['ejercicio'] or '0000'}-00-00"), reverse=True)
    rows.sort(key=lambda r: r["baja"] is not None)
    activas = [r for r in rows if r["baja"] is None]
    return {
        "codcli": codcli,
        # Sin cliente de FACTUSOL vinculado no se sabe qué ha comprado: se dice,
        # porque una lista vacía se leería como «no ha comprado nunca».
        "sin_cliente_factusol": codcli is None,
        "maquinas": rows,
        "activas": len(activas),
    }


def _claves_de(m: dict[str, Any]) -> list[str]:
    return [m["clave"], *(a["clave"] for a in m.get("albaranes") or [])]


def _baja(at: datetime | None, motivo: str | None, por: str | None) -> dict[str, Any]:
    return {"at": f"{at.isoformat()}Z" if at else None, "motivo": motivo, "por": por}


def anadir_manual(session: Session, company_id: str, datos: dict[str, Any], *,
                  por: str) -> ParqueMaquinaManual:
    """ValueError si falta la descripción o la gama no existe."""
    descripcion = (datos.get("descripcion") or "").strip()[:255]
    if not descripcion:
        raise ValueError("descripcion")
    gama = datos.get("gama") or None
    if gama is not None and session.get(HistoricoGama, gama) is None:
        raise ValueError("gama")
    fecha = datos.get("fecha")
    if isinstance(fecha, str) and fecha:
        fecha = date.fromisoformat(fecha[:10])
    m = ParqueMaquinaManual(
        company_id=company_id, codart=(datos.get("codart") or "").strip()[:64] or None,
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
        borrar = [b for c in (_claves_de(fila) if fila else [])
                  if (b := session.get(ParqueBaja, c)) is not None]
        if not borrar:
            raise LookupError("maquina")
        for b in borrar:
            session.delete(b)
    session.commit()
