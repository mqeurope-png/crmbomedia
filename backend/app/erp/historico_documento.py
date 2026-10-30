"""La ficha de un documento de FACTUSOL (factura, albarán o proforma) de
cualquier año, para abrirlo desde la ficha de un cliente sin ir a buscarlo al
listado (30/10/2026).

De dónde sale:

- **Lo volcado** (`historico_documentos` / `historico_lineas`): cualquier año
  de 2014 a hoy. El nombre del artículo es el del catálogo DE SU AÑO.
- **En vivo**, solo si no está volcado y es del ejercicio en curso (creado
  después de la última carga, que es nocturna): la lectura de ERP ·
  Documentos. Así un documento de hoy no abre vacío.

Solo lectura: ni escribe en FACTUSOL ni repite aquí las acciones (cobrar,
mención, vincular), que siguen en su sitio.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
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
    HistoricoLinea,
)

#: tipo del volcado → `doc_type` de ERP · Documentos (y del PDF).
DOC_TYPE = {
    TIPO_FACTURA: "facturas",
    TIPO_ALBARAN: "albaranes",
    TIPO_PRESUPUESTO: "presupuestos",
}
ETIQUETA = {TIPO_FACTURA: "Factura", TIPO_ALBARAN: "Albarán", TIPO_PRESUPUESTO: "Proforma"}


class DocumentoNoEncontrado(LookupError):
    pass


class FactusolNoResponde(RuntimeError):
    """El lector en vivo no ha podido leer FACTUSOL (no es que no exista)."""


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _empresa(session: Session, codcli: str | None) -> dict[str, Any] | None:
    from app.models.crm import Company  # noqa: PLC0415

    if not codcli:
        return None
    codigos = {codcli.strip()}
    if codcli.strip().isdigit():
        codigos.add(str(int(codcli)))
    e = session.scalars(
        select(Company).where(Company.factusol_company_id.in_(sorted(codigos)))).first()
    return {"id": e.id, "nombre": e.name} if e else None


def _pedido(
    session: Session, tipo: str, serie: str, codigo: int, user: Any, *,
    ejercicio: str, en_curso: str,
    referencia: str | None = None, codcli: str | None = None,
) -> dict | None:
    """El pedido de BoHub que apunta a este documento (o None). Un pedido web
    que el usuario no puede ver cuenta como que no lo hay.

    Solo para el ejercicio en curso y el anterior: el pedido no guarda el
    ejercicio y la numeración de FACTUSOL vuelve a empezar cada año, así que
    una factura de 2017 con el mismo serie-número que una de hoy enseñaría el
    pedido de hoy (BoHub no tiene pedidos de antes)."""
    if int(ejercicio) < int(en_curso) - 1:
        return None
    from app.erp.api.orders import web_oculto  # noqa: PLC0415
    from app.erp.quotes_bandeja import _orders_by_document  # noqa: PLC0415

    try:
        serie_n: Any = int(serie)
    except ValueError:
        serie_n = serie
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415

    encontrados = _orders_by_document(session, DOC_TYPE[tipo], [{
        "serie": serie_n, "codigo": int(codigo), "numero": visible_number(serie, codigo),
        "referencia": referencia, "cliente_codigo": codcli,
    }])
    for o in encontrados.values():
        if not web_oculto(o, user):
            return {"id": o.id, "order_number": o.order_number}
    return None


def _clasificacion(session: Session, ejercicio: str, codarts: set[str]) -> dict[str, dict]:
    """Nombre del catálogo de su año, familia, clase y gama de cada artículo."""
    if not codarts:
        return {}
    filas = session.execute(
        select(HistoricoArticulo.codart, HistoricoArticulo.descripcion,
               HistoricoArticulo.familia,
               func.coalesce(HistoricoArticuloClase.clase, HistoricoFamilia.clase),
               HistoricoFamilia.gama)
        .outerjoin(HistoricoFamilia, HistoricoFamilia.codigo == HistoricoArticulo.familia)
        .outerjoin(HistoricoArticuloClase,
                   HistoricoArticuloClase.codart == HistoricoArticulo.codart)
        .where(HistoricoArticulo.ejercicio == ejercicio,
               HistoricoArticulo.codart.in_(sorted(codarts)))
    ).all()
    return {c: {"nombre": d, "familia": f, "clase": cl, "gama": g}
            for c, d, f, cl, g in filas}


def _estado_label(tipo: str, raw: str | None) -> str | None:
    from app.integrations.factusol.documents import estado_label  # noqa: PLC0415

    return estado_label(DOC_TYPE[tipo], raw) if raw not in (None, "") else None


def ficha(
    session: Session, *, tipo: str, ejercicio: str, serie: str, codigo: int, user: Any,
    en_curso: str, lector_vivo: Any = None,
) -> dict[str, Any]:
    """La ficha. `lector_vivo(doc_type, serie, codigo)` lee FACTUSOL en vivo
    (solo se usa para un documento del ejercicio en curso que aún no está
    volcado). DocumentoNoEncontrado si no está en ningún sitio."""
    if tipo not in DOC_TYPE:
        raise DocumentoNoEncontrado(tipo)
    doc = session.scalar(select(HistoricoDocumento).where(
        HistoricoDocumento.ejercicio == ejercicio, HistoricoDocumento.tipo == tipo,
        HistoricoDocumento.serie == serie, HistoricoDocumento.codigo == codigo))
    del_curso = ejercicio == str(en_curso) and lector_vivo is not None
    if doc is not None:
        out = _ficha_volcada(session, doc, user, en_curso=str(en_curso))
        if del_curso:
            # El cobro de hoy no puede salir de la carga de anoche: se lee la
            # cabecera en vivo; si FACTUSOL no responde, se queda lo volcado.
            try:
                vivo = lector_vivo(DOC_TYPE[tipo], serie, codigo, solo_cabecera=True)
            except FactusolNoResponde:
                vivo = None
            if vivo:
                out["estado"] = vivo.get("estado", out["estado"])
                out["estado_label"] = vivo.get("estado_label") or out["estado_label"]
                if vivo.get("total") is not None:
                    out["total"] = vivo.get("total")
                out["estado_en_vivo"] = True
        return out
    if del_curso:
        vivo = lector_vivo(DOC_TYPE[tipo], serie, codigo)   # FactusolNoResponde sube
        if vivo is not None:
            return _ficha_viva(session, vivo, tipo=tipo, ejercicio=ejercicio,
                               serie=serie, codigo=codigo, user=user, en_curso=str(en_curso))
    raise DocumentoNoEncontrado(f"{tipo} {serie}-{codigo} {ejercicio}")


def _numero(serie: str, codigo: int) -> str:
    """`serie-código` tal cual (la serie no se interpreta: una serie ya
    retirada se sigue viendo)."""
    return f"{serie}-{int(codigo):06d}" if serie else f"{int(codigo):06d}"


#: `DOC*` de una línea: de qué documento sale.
ORIGEN_LINEA = {"P": "Proforma", "C": "Pedido de cliente", "A": "Albarán"}


def _viene_de(doc: HistoricoDocumento, ln: HistoricoLinea) -> str | None:
    """«Albarán 3-230001»: el documento del que sale la línea, como lo apunta
    FACTUSOL (sin el auto-enlace de un albarán autónomo). Es lo que usa el
    parque para no contar dos veces una venta."""
    if not ln.origen_tipo or not ln.origen_codigo:
        return None
    if doc.tipo == "albaran" and (ln.origen_tipo, ln.origen_serie, ln.origen_codigo) == (
            "A", doc.serie, doc.codigo):
        return None
    etiqueta = ORIGEN_LINEA.get(ln.origen_tipo, f"Documento {ln.origen_tipo}")
    return f"{etiqueta} {ln.origen_serie or ''}-{int(ln.origen_codigo):06d}"


def _ficha_volcada(
    session: Session, doc: HistoricoDocumento, user: Any, *, en_curso: str,
) -> dict[str, Any]:
    lineas = list(session.scalars(select(HistoricoLinea).where(
        HistoricoLinea.documento_id == doc.id).order_by(HistoricoLinea.posicion,
                                                        HistoricoLinea.id)))
    clas = _clasificacion(session, doc.ejercicio,
                          {ln.codart for ln in lineas if ln.codart})
    salida_lineas = []
    for ln in lineas:
        c = clas.get(ln.codart or "", {})
        salida_lineas.append({
            "posicion": ln.posicion, "codart": ln.codart, "descripcion": ln.descripcion,
            "articulo": c.get("nombre"), "familia": c.get("familia"),
            "clase": c.get("clase"), "gama": c.get("gama"),
            "cantidad": _num(ln.cantidad), "precio": _num(ln.precio), "total": _num(ln.total),
            "es_nota": not ln.es_producto,
            "viene_de": _viene_de(doc, ln),
        })
    carga = session.get(HistoricoEjercicio, doc.ejercicio)
    suma = sum((Decimal(str(ln["total"] or 0)) for ln in salida_lineas if not ln["es_nota"]),
               Decimal("0"))
    return {
        "fuente": "volcado",
        "cargado_at": carga.cargado_at.isoformat() + "Z" if carga and carga.cargado_at else None,
        "tipo": doc.tipo, "tipo_label": ETIQUETA[doc.tipo], "doc_type": DOC_TYPE[doc.tipo],
        "ejercicio": doc.ejercicio, "serie": doc.serie, "codigo": doc.codigo,
        "numero": _numero(doc.serie, doc.codigo),
        "fecha": doc.fecha.isoformat() if doc.fecha else None,
        "fecha_original": None if doc.fecha else doc.fecha_original,
        "estado_en_vivo": False,
        "cliente": {"codcli": doc.codcli, "nombre": doc.cliente_nombre,
                    "empresa": _empresa(session, doc.codcli)},
        "referencia": doc.referencia,
        "total": _num(doc.total),
        # Sin el total de la cabecera (volcado antes de la 0155): la suma de
        # las líneas de producto, sin impuestos, y se dice.
        "suma_lineas": float(suma),
        "estado": doc.estado,
        "estado_label": _estado_label(doc.tipo, doc.estado),
        "pedido": _pedido(session, doc.tipo, doc.serie, doc.codigo, user,
                          ejercicio=doc.ejercicio, en_curso=en_curso,
                          referencia=doc.referencia, codcli=doc.codcli),
        "lineas": salida_lineas,
    }


def _ficha_viva(
    session: Session, vivo: dict[str, Any], *, tipo: str, ejercicio: str, serie: str,
    codigo: int, user: Any, en_curso: str,
) -> dict[str, Any]:
    lineas = vivo.get("lines") or []
    clas = _clasificacion(session, ejercicio, {ln.get("codart") for ln in lineas
                                               if ln.get("codart")})
    salida_lineas = []
    for ln in lineas:
        c = clas.get(ln.get("codart") or "", {})
        salida_lineas.append({
            "posicion": ln.get("position"), "codart": ln.get("codart"),
            "descripcion": ln.get("description"), "articulo": c.get("nombre"),
            "familia": c.get("familia"), "clase": c.get("clase"), "gama": c.get("gama"),
            "cantidad": ln.get("quantity"), "precio": ln.get("unit_price"),
            "total": ln.get("line_total"), "es_nota": not ln.get("codart"),
        })
    codcli = vivo.get("cliente_codigo")
    return {
        "fuente": "vivo", "cargado_at": None, "estado_en_vivo": True,
        "tipo": tipo, "tipo_label": ETIQUETA[tipo], "doc_type": DOC_TYPE[tipo],
        "ejercicio": ejercicio, "serie": serie, "codigo": codigo,
        "numero": vivo.get("numero"),
        "fecha": (vivo.get("fecha") or None) and str(vivo.get("fecha"))[:10],
        "fecha_original": None,
        "cliente": {"codcli": codcli, "nombre": vivo.get("cliente_nombre"),
                    "empresa": _empresa(session, codcli)},
        "referencia": vivo.get("referencia"),
        "total": vivo.get("total"),
        "suma_lineas": float(sum(Decimal(str(ln["total"] or 0)) for ln in salida_lineas
                                 if not ln["es_nota"])),
        "estado": vivo.get("estado"),
        "estado_label": vivo.get("estado_label"),
        "pedido": _pedido(session, tipo, serie, codigo, user,
                          ejercicio=ejercicio, en_curso=en_curso,
                          referencia=vivo.get("referencia"), codcli=codcli),
        "lineas": salida_lineas,
    }


def documentos_de_cliente(
    session: Session, codcli: str, *, ejercicio: str | None = None,
) -> list[dict[str, Any]]:
    """Los documentos volcados de un cliente (todos los años o uno), más
    recientes primero, con lo justo para listarlos y abrir su ficha."""
    codigos = {codcli.strip()}
    if codcli.strip().isdigit():
        codigos.add(str(int(codcli)))
    stmt = select(HistoricoDocumento).where(HistoricoDocumento.codcli.in_(sorted(codigos)))
    if ejercicio:
        stmt = stmt.where(HistoricoDocumento.ejercicio == ejercicio)
    docs = session.scalars(stmt.order_by(
        HistoricoDocumento.ejercicio.desc(), HistoricoDocumento.fecha.desc(),
        HistoricoDocumento.codigo.desc())).all()
    return [{
        "tipo": d.tipo, "tipo_label": ETIQUETA.get(d.tipo, d.tipo), "ejercicio": d.ejercicio,
        "serie": d.serie, "codigo": d.codigo, "numero": _numero(d.serie, d.codigo),
        "fecha": d.fecha.isoformat() if d.fecha else None,
        "fecha_original": None if d.fecha else d.fecha_original,
        "total": _num(d.total), "estado_label": _estado_label(d.tipo, d.estado),
    } for d in docs]

