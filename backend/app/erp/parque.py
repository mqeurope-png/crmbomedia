"""El parque instalado: qué máquinas tiene cada cliente (30/10/2026).

Sale de las ventas volcadas (`historico_*`): las líneas de producto cuyo
artículo es, en el catálogo de su año, de una familia de clase **máquina**.
Un accesorio, un recambio o un consumible no son máquinas.

- **Una venta, una vez.** Un albarán facturado y su factura son la misma
  venta: cuenta la factura. Un albarán se cuenta solo si FACTUSOL lo da como
  pendiente de facturar (`ESTALB` 0) o, sin ese dato (volcado antes de la
  0155), si de ese artículo no hay factura del cliente. Un abono (línea en
  negativo) anula la venta más reciente de ese artículo.
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

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.erp.models.historico import (
    TIPO_ALBARAN,
    TIPO_FACTURA,
    HistoricoArticulo,
    HistoricoDocumento,
    HistoricoFamilia,
    HistoricoGama,
    HistoricoLinea,
    ParqueBaja,
    ParqueMaquinaManual,
)

CLASE_MAQUINA = "maquina"
#: ESTALB «facturado» (confirmado en el escritorio, `documents.ESTADO_LABELS`).
ALBARAN_FACTURADO = "1"


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


def maquinas_volcadas(session: Session, codcli: str | None) -> list[dict[str, Any]]:
    """Las máquinas de las ventas volcadas de un cliente, una por línea (con
    su cantidad), sin contar dos veces el albarán y su factura."""
    codigos = codigos_cliente(codcli)
    if not codigos:
        return []
    art, lin, doc, fam = HistoricoArticulo, HistoricoLinea, HistoricoDocumento, HistoricoFamilia
    filas = session.execute(
        select(lin.ejercicio, doc.tipo, doc.serie, doc.codigo, doc.fecha, doc.fecha_original,
               doc.estado, lin.posicion, lin.codart, lin.descripcion, lin.cantidad,
               art.descripcion, art.familia, fam.gama)
        .join(doc, doc.id == lin.documento_id)
        .join(art, and_(art.ejercicio == lin.ejercicio, art.codart == lin.codart))
        .join(fam, fam.codigo == art.familia)
        .where(doc.codcli.in_(codigos), doc.tipo.in_((TIPO_FACTURA, TIPO_ALBARAN)),
               lin.es_producto.is_(True), fam.clase == CLASE_MAQUINA)
    ).all()
    facturados = {f.codart for f in filas if f.tipo == TIPO_FACTURA}
    # Un abono (línea en negativo) anula la venta más reciente de ese artículo.
    abonado: dict[str, float] = {}
    for f in filas:
        if (f.cantidad or 0) < 0:
            abonado[f.codart] = abonado.get(f.codart, 0.0) - float(f.cantidad)
    filas = sorted(filas, key=lambda f: (f.fecha is not None, f.fecha or date.min), reverse=True)
    out = []
    for f in filas:
        if (f.cantidad or 0) <= 0:
            continue
        if f.tipo == TIPO_ALBARAN:
            if f.estado == ALBARAN_FACTURADO:
                continue
            if f.estado is None and f.codart in facturados:
                continue
        cantidad = float(f.cantidad)
        if abonado.get(f.codart, 0) > 0:
            anula = min(cantidad, abonado[f.codart])
            abonado[f.codart] -= anula
            cantidad -= anula
            if cantidad <= 0:
                continue
        out.append({
            "origen": f.tipo, "clave": clave_linea(f.ejercicio, f.tipo, f.serie, f.codigo,
                                                   f.posicion, f.codart),
            "codart": f.codart, "articulo": f[11] or f[9], "familia": f.familia,
            "gama": f.gama, "fecha": f.fecha.isoformat() if f.fecha else None,
            "fecha_original": None if f.fecha else f.fecha_original,
            "ejercicio": f.ejercicio, "cantidad": cantidad,
            "documento": {"tipo": f.tipo, "serie": f.serie, "codigo": f.codigo,
                          "numero": f"{f.serie}-{int(f.codigo):06d}"},
        })
    return out


def parque(session: Session, company: Any) -> dict[str, Any]:
    """Las máquinas de una empresa: las volcadas y las añadidas a mano, con
    su baja, la gama con nombre y de dónde sale cada una."""
    gamas = _gamas(session)
    codcli = (getattr(company, "factusol_company_id", None) or "").strip() or None
    volcadas = maquinas_volcadas(session, codcli)
    bajas = {b.clave: b for b in session.scalars(
        select(ParqueBaja).where(ParqueBaja.clave.in_([m["clave"] for m in volcadas])))
    } if volcadas else {}
    rows: list[dict[str, Any]] = []
    for m in volcadas:
        b = bajas.get(m["clave"])
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
        b = session.get(ParqueBaja, clave or "")
        claves = {x["clave"] for x in maquinas_volcadas(session, company.factusol_company_id)}
        if b is None or b.clave not in claves:
            raise LookupError("maquina")
        session.delete(b)
    session.commit()
