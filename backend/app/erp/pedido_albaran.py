"""El albarán de un pedido sin crear uno nuevo (30/10/2026).

- **Pedido creado desde una factura.** La factura salió de un albarán que ya
  existe: crear otro dejaría dos para la misma entrega (y el segundo hay que
  anularlo a mano en FACTUSOL). Se busca el que la factura apunta en sus
  líneas (`DOCLFA='A'`, `DTPLFA` serie, `DCOLFA` número; volcado en
  `historico_lineas.origen_*` desde la 0157) y se vincula. Si la factura no
  lo apunta, o apunta a varios, no se inventa ni se crea nada: el pedido se
  queda sin albarán y la ficha lo dice, para vincularlo a mano.
- **Vincular a mano desde la ficha del pedido.** Los albaranes del cliente
  que no están en otro pedido, los más recientes primero (los de la propia
  factura delante), y un buscador por número; cada uno dice de qué documento
  sale.

Solo LEE FACTUSOL (lo volcado y, para lo de hoy que aún no está volcado, en
vivo). Vincular es un dato de BoHub: no escribe nada en FACTUSOL.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.orm import Session

from app.erp.factusol_albaran import (
    _attach_albaran,
    is_factura_source,
    packing_of,
)
from app.erp.models import Order
from app.erp.models.historico import (
    TIPO_ALBARAN,
    TIPO_FACTURA,
    HistoricoDocumento,
    HistoricoEjercicio,
    HistoricoLinea,
)

logger = logging.getLogger(__name__)

#: Etiquetas del documento origen de una línea (`DOC*`).
ORIGEN = {"P": "Proforma", "C": "Pedido de cliente", "A": "Albarán"}
#: Cuántos se proponen sin buscar.
PROPUESTOS = 10


class AlbaranNoVinculable(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


def numero(serie: Any, codigo: Any) -> str:
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415

    return visible_number(_serie(serie), int(codigo))


def _serie(valor: Any) -> str:
    t = str(valor if valor is not None else "").strip()
    if t.replace(".", "", 1).isdigit():
        return str(int(float(t)))
    return t


def _ejercicio_del_pedido(order: Order) -> str:
    """El ejercicio del documento de origen: guardado al crear el pedido o,
    en los de antes, el año en que se creó (se crean desde el ejercicio en
    curso). Los números se repiten cada año: sin él no se casa nada."""
    src = packing_of(order).get("factusol_source")
    if isinstance(src, dict) and src.get("ejercicio"):
        return str(src["ejercicio"])
    creado = order.created_at or order.placed_at
    return str(creado.year) if creado else ""


def _factura_del_pedido(order: Order) -> tuple[str, int, str] | None:
    """`(serie, codigo, ejercicio)` de la factura de origen."""
    ejercicio = _ejercicio_del_pedido(order)
    src = packing_of(order).get("factusol_source")
    if isinstance(src, dict) and src.get("doc_type") == "facturas":
        try:
            return _serie(src.get("serie")), int(src.get("codigo")), ejercicio
        except (TypeError, ValueError):
            pass
    if order.factusol_invoice_number and order.factusol_invoice_serie is not None:
        try:
            return (str(int(order.factusol_invoice_serie)), int(order.factusol_invoice_number),
                    ejercicio)
        except (TypeError, ValueError):
            return None
    return None


def _existe_albaran(session: Session, serie: str, codigo: int, ejercicio: str, *,
                    client: Any = None) -> bool | None:
    """¿Está el albarán en ESE ejercicio? (volcado o, si no, en vivo). `None`
    si no se puede saber."""
    if session.scalar(select(HistoricoDocumento.id).where(
            HistoricoDocumento.tipo == TIPO_ALBARAN, HistoricoDocumento.ejercicio == ejercicio,
            HistoricoDocumento.serie == serie, HistoricoDocumento.codigo == int(codigo))
            .limit(1)) is not None:
        return True
    if client is None:
        return None
    try:
        filas = client.load_table("F_ALB", filtro=f"CODALB={int(codigo)}", ejercicio=ejercicio)
    except Exception:  # noqa: BLE001
        return None
    return any(_serie(r.get("TIPALB")) == serie for r in filas)


def _ya_vinculados(session: Session, numeros: list[str], salvo: str | None = None) -> set[str]:
    if not numeros:
        return set()
    stmt = select(Order.factusol_albaran_number).where(
        Order.factusol_albaran_number.in_(numeros))
    if salvo:
        stmt = stmt.where(Order.id != salvo)
    return {n for n in session.scalars(stmt) if n}


# --- el albarán que apunta la factura ----------------------------------------------------


def albaranes_de_factura(
    session: Session, serie: str, codigo: int, ejercicio: str, *, client: Any = None,
) -> dict[str, Any]:
    """Los albaranes `[(serie, codigo)]` que apuntan las líneas de la factura
    `serie-codigo` DEL EJERCICIO `ejercicio` (los números se repiten cada año).

    `fuente`: «volcado» si está en el histórico de ese ejercicio ya cargado
    con los enlaces; si no (la de hoy, aún sin volcar), «vivo» leyendo `F_LFA`
    de ese ejercicio (solo lectura). «sin_datos» si no hay forma de saberlo."""
    doc = session.scalar(
        select(HistoricoDocumento).join(
            HistoricoEjercicio, HistoricoEjercicio.ejercicio == HistoricoDocumento.ejercicio)
        .where(HistoricoDocumento.tipo == TIPO_FACTURA, HistoricoDocumento.ejercicio == ejercicio,
               HistoricoDocumento.serie == serie, HistoricoDocumento.codigo == codigo,
               HistoricoEjercicio.con_enlaces.is_(True))
        .limit(1))
    if doc is not None:
        refs: list[tuple[str, int]] = []
        for ln in session.scalars(select(HistoricoLinea).where(
                HistoricoLinea.documento_id == doc.id).order_by(HistoricoLinea.posicion)):
            if ln.origen_tipo == "A" and ln.origen_codigo:
                ref = (ln.origen_serie or "", int(ln.origen_codigo))
                if ref not in refs:
                    refs.append(ref)
        return {"fuente": "volcado", "albaranes": refs}
    if client is None or not ejercicio:
        return {"fuente": "sin_datos", "albaranes": []}
    try:
        filas = client.load_table("F_LFA", filtro=f"CODLFA={int(codigo)}", ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001 — FACTUSOL caído: no se decide nada
        logger.info("pedido_albaran: no se pudo leer la factura en vivo (%s)",
                    type(exc).__name__)
        return {"fuente": "sin_datos", "albaranes": []}
    refs = []
    for r in filas:
        if _serie(r.get("TIPLFA")) != serie:
            continue
        if str(r.get("DOCLFA") or "").strip().upper() != "A" or not r.get("DCOLFA"):
            continue
        try:
            ref = (_serie(r.get("DTPLFA")), int(float(r.get("DCOLFA"))))
        except (TypeError, ValueError):
            continue
        if ref not in refs:
            refs.append(ref)
    return {"fuente": "vivo", "albaranes": refs}


def vincular_albaran_de_factura(
    session: Session, order: Order, *, client: Any = None, ejercicio: str | None = None,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Vincula al pedido (creado desde una factura) el albarán que originó la
    factura. NUNCA crea uno. Devuelve `{estado, numero?, detalle}`:

    - `ya_tiene`: el pedido ya tiene albarán;
    - `vinculado`: la factura apunta a uno solo, libre → vinculado;
    - `sin_enlace`: la factura no apunta a ningún albarán;
    - `varios`: apunta a más de uno (una factura de varios albaranes);
    - `en_otro_pedido`: el suyo ya está vinculado a otro pedido;
    - `otro_ejercicio`: el albarán no está en el ejercicio en curso. El
      pedido guarda solo serie-número y el PDF y el envío lo buscarían en el
      ejercicio en curso (otro documento): no se vincula solo;
    - `sin_datos`: ni volcada ni FACTUSOL responde (se reintenta luego).

    `ejercicio` es el ejercicio EN CURSO de BoHub. Hace commit solo si
    vincula."""
    if order.factusol_albaran_number:
        return {"estado": "ya_tiene", "numero": order.factusol_albaran_number}
    factura = _factura_del_pedido(order)
    if not is_factura_source(order) or factura is None:
        return {"estado": "no_procede"}
    leido = albaranes_de_factura(session, factura[0], factura[1], factura[2], client=client)
    refs = leido["albaranes"]
    if leido["fuente"] == "sin_datos":
        return {"estado": "sin_datos", "detalle": (
            "No se ha podido leer la factura (ni volcada ni en FACTUSOL): vuelve a "
            "intentarlo o vincula el albarán a mano.")}
    if not refs:
        return {"estado": "sin_enlace", "detalle": (
            "La factura no apunta a ningún albarán en FACTUSOL: vincula el albarán "
            "a mano. No se crea ninguno.")}
    numeros = [numero(s, c) for s, c in refs]
    if len(refs) > 1:
        return {"estado": "varios", "candidatos": numeros, "detalle": (
            f"La factura viene de {len(refs)} albaranes ({', '.join(numeros)}): elige "
            "a mano el de este pedido.")}
    n = numeros[0]
    if _ya_vinculados(session, [n], salvo=order.id):
        return {"estado": "en_otro_pedido", "numero": n, "detalle": (
            f"El albarán {n} de la factura ya está vinculado a otro pedido.")}
    if ejercicio and _existe_albaran(session, refs[0][0], refs[0][1], ejercicio,
                                     client=client) is not True:
        return {"estado": "otro_ejercicio", "numero": n, "detalle": (
            f"El albarán {n} de la factura no está en el ejercicio {ejercicio}: no se "
            "vincula solo (BoHub lo buscaría en el ejercicio en curso). Vincúlalo a "
            "mano si es de este ejercicio.")}
    _attach_albaran(session, order, n, how="vinculado: es el de la factura de origen",
                    actor_user_id=actor_user_id,
                    extra={"factura": {"serie": factura[0], "codigo": factura[1]},
                           "fuente": leido["fuente"]})
    session.commit()
    return {"estado": "vinculado", "numero": n}


# --- candidatos y vincular a mano --------------------------------------------------------


def _viene_de(session: Session, doc_ids: list[int]) -> dict[int, str | None]:
    """«Proforma 1-000500»: el primer origen de las líneas de cada albarán
    volcado (sin el auto-enlace de un albarán autónomo)."""
    if not doc_ids:
        return {}
    docs = {d.id: d for d in session.scalars(
        select(HistoricoDocumento).where(HistoricoDocumento.id.in_(doc_ids)))}
    out: dict[int, str | None] = dict.fromkeys(doc_ids)
    for ln in session.scalars(select(HistoricoLinea).where(
            HistoricoLinea.documento_id.in_(doc_ids), HistoricoLinea.origen_tipo.is_not(None))
            .order_by(HistoricoLinea.posicion)):
        d = docs[ln.documento_id]
        if out[ln.documento_id] or not ln.origen_codigo:
            continue
        if (ln.origen_tipo, ln.origen_serie, ln.origen_codigo) == ("A", d.serie, d.codigo):
            continue
        out[ln.documento_id] = (f"{ORIGEN.get(ln.origen_tipo, ln.origen_tipo)} "
                                f"{numero(ln.origen_serie, ln.origen_codigo)}")
    return out


def _fila(d: HistoricoDocumento, viene_de: str | None, *, de_la_factura: bool) -> dict:
    return {
        "serie": d.serie, "codigo": d.codigo, "numero": numero(d.serie, d.codigo),
        "ejercicio": d.ejercicio, "fecha": d.fecha.isoformat() if d.fecha else None,
        "cliente_nombre": d.cliente_nombre, "codcli": d.codcli,
        "total": float(d.total) if d.total is not None else None,
        "viene_de": viene_de, "de_la_factura": de_la_factura,
    }


def _codcli(session: Session, order: Order) -> str | None:
    from app.models.crm import Company  # noqa: PLC0415

    src = packing_of(order).get("factusol_source")
    if isinstance(src, dict) and src.get("cliente_codigo"):
        return str(src["cliente_codigo"]).strip()
    if order.company_id:
        company = session.get(Company, order.company_id)
        if company is not None and (company.factusol_company_id or "").strip():
            return company.factusol_company_id.strip()
    return None


def candidatos(
    session: Session, order: Order, *, q: str | None = None, client: Any = None,
    ejercicio: str | None = None,
) -> dict[str, Any]:
    """Los albaranes que se proponen para vincular al pedido, del ejercicio
    en curso (`ejercicio`): el pedido guarda solo serie-número y el PDF y el
    envío lo buscan en el ejercicio en curso, así que uno de otro año sería
    otro documento.

    Sin `q`: los del cliente del pedido (los de la propia factura delante),
    los más recientes primero. Con `q`: los que casan con el número (de
    cualquier cliente). Nunca uno ya vinculado a OTRO pedido. Lo volcado,
    más lo de hoy en vivo (aún sin volcar) si hay `client`."""
    from datetime import date  # noqa: PLC0415

    from app.erp.parque import codigos_cliente  # noqa: PLC0415

    ejercicio = ejercicio or str(date.today().year)
    doc = HistoricoDocumento
    codcli = _codcli(session, order)
    de_la_factura: list[str] = []
    factura = _factura_del_pedido(order) if is_factura_source(order) else None
    if factura is not None:
        de_la_factura = [numero(s, c) for s, c in albaranes_de_factura(
            session, factura[0], factura[1], factura[2], client=client)["albaranes"]]

    stmt = select(doc).where(doc.tipo == TIPO_ALBARAN, doc.ejercicio == ejercicio)
    texto = (q or "").strip()
    if texto:
        serie_q, _, cod_q = texto.rpartition("-")
        try:
            cod = int(cod_q)
        except ValueError:
            return {"codcli": codcli, "items": [], "de_la_factura": de_la_factura}
        stmt = stmt.where(doc.codigo == cod)
        if serie_q.strip():
            stmt = stmt.where(doc.serie == _serie(serie_q))
    elif codcli:
        stmt = stmt.where(doc.codcli.in_(codigos_cliente(codcli)))
    else:
        stmt = stmt.where(false())   # sin cliente: solo los de la factura
    # Sin fecha al final (MySQL no tiene NULLS LAST).
    docs = list(session.scalars(stmt.order_by(doc.fecha.is_(None), doc.fecha.desc(),
                                              doc.codigo.desc()).limit(60)))
    # Los de la propia factura, aunque sean de otro código de cliente.
    faltan = [n for n in de_la_factura if n not in {numero(d.serie, d.codigo) for d in docs}]
    for n in faltan:
        serie_n, _, cod_n = n.rpartition("-")
        extra = session.scalar(select(doc).where(
            doc.tipo == TIPO_ALBARAN, doc.ejercicio == ejercicio, doc.serie == serie_n,
            doc.codigo == int(cod_n)).limit(1))
        if extra is not None:
            docs.append(extra)
    vistos: set[str] = set()
    unicos = []
    for d in docs:
        n = numero(d.serie, d.codigo)
        if n not in vistos:
            vistos.add(n)
            unicos.append(d)
    ocupados = _ya_vinculados(session, list(vistos), salvo=order.id)
    unicos = [d for d in unicos if numero(d.serie, d.codigo) not in ocupados]
    viene = _viene_de(session, [d.id for d in unicos])
    filas = [_fila(d, viene[d.id], de_la_factura=numero(d.serie, d.codigo) in de_la_factura)
             for d in unicos]
    if client is not None and ejercicio and (codcli or texto):
        filas = _con_lo_de_hoy(session, filas, client=client, ejercicio=ejercicio,
                               codcli=codcli, q=texto, ocupados_salvo=order.id,
                               de_la_factura=de_la_factura)
    _ordenar(filas)
    return {"codcli": codcli, "items": filas[: (60 if texto else PROPUESTOS)],
            "de_la_factura": de_la_factura}


def _ordenar(filas: list[dict[str, Any]]) -> None:
    """Los de la factura delante; después, los más recientes (sin fecha, al
    final; a igual fecha, el número más alto)."""
    filas.sort(key=lambda f: (f.get("fecha") or "", int(f.get("codigo") or 0)), reverse=True)
    filas.sort(key=lambda f: (not f["de_la_factura"], not f.get("fecha")))


def _con_lo_de_hoy(
    session: Session, filas: list[dict[str, Any]], *, client: Any, ejercicio: str,
    codcli: str | None, q: str, ocupados_salvo: str, de_la_factura: list[str],
) -> list[dict[str, Any]]:
    """Añade los albaranes del ejercicio en curso que aún no están volcados
    (la carga del año en curso es nocturna). Solo lectura; si FACTUSOL no
    responde se queda con lo volcado."""
    from app.integrations.factusol.documents import _int_or_none  # noqa: PLC0415

    if q:
        _, _, cod_q = q.rpartition("-")
        filtro = f"CODALB={int(cod_q)}" if cod_q.isdigit() else None
    else:
        filtro = f"CLIALB={int(codcli)}" if codcli and codcli.isdigit() else None
    if not filtro:
        return filas
    try:
        cabeceras = client.load_table("F_ALB", filtro=filtro, ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001
        logger.info("pedido_albaran: sin lectura en vivo de albaranes (%s)", type(exc).__name__)
        return filas
    ya = {f["numero"] for f in filas}
    nuevos = []
    for c in cabeceras:
        cod = _int_or_none(c.get("CODALB"))
        if cod is None:
            continue
        n = numero(c.get("TIPALB"), cod)
        if n in ya:
            continue
        ya.add(n)
        fecha = str(c.get("FECALB") or "")[:10] or None
        nuevos.append({
            "serie": _serie(c.get("TIPALB")), "codigo": cod, "numero": n,
            "ejercicio": ejercicio, "fecha": fecha,
            "cliente_nombre": c.get("CNOALB"), "codcli": str(c.get("CLIALB") or "") or None,
            "total": c.get("TOTALB"), "viene_de": None, "de_la_factura": n in de_la_factura,
        })
    ocupados = _ya_vinculados(session, [f["numero"] for f in nuevos], salvo=ocupados_salvo)
    nuevos = [f for f in nuevos if f["numero"] not in ocupados]
    # De dónde sale cada uno: sus líneas, solo para los tres primeros (una
    # lectura por albarán, dentro de la petición).
    _ordenar(nuevos)
    for f in nuevos[:3]:
        try:
            lineas = client.load_table("F_LAL", filtro=f"CODLAL={int(f['codigo'])}",
                                       ejercicio=ejercicio)
        except Exception:  # noqa: BLE001
            break
        for ln in lineas:
            if _serie(ln.get("TIPLAL")) != f["serie"]:
                continue
            tipo = str(ln.get("DOCLAL") or "").strip().upper()
            dco = _int_or_none(ln.get("DCOLAL"))
            if not tipo or not dco:
                continue
            if (tipo, _serie(ln.get("DTPLAL")), dco) == ("A", f["serie"], f["codigo"]):
                continue
            f["viene_de"] = f"{ORIGEN.get(tipo, tipo)} {numero(ln.get('DTPLAL'), dco)}"
            break
    return filas + nuevos


def vincular_a_mano(
    session: Session, order: Order, *, serie: str, codigo: int,
    actor_user_id: str | None, client: Any = None, ejercicio: str | None = None,
) -> str:
    """Vincula al pedido un albarán que ya existe. AlbaranNoVinculable si el
    pedido ya tiene uno, es web o muestra, el albarán está en otro pedido o
    no existe (ni volcado ni en vivo). Hace commit."""
    from app.erp.factusol_albaran import is_web_order  # noqa: PLC0415
    from app.erp.sample_orders import is_sample_order  # noqa: PLC0415

    if is_sample_order(order) or is_web_order(order):
        raise AlbaranNoVinculable("no_aplica", (
            "Este pedido no lleva albarán de FACTUSOL (web o muestra)."))
    if order.factusol_albaran_number:
        raise AlbaranNoVinculable("ya_tiene", (
            f"El pedido ya tiene el albarán {order.factusol_albaran_number}: "
            "desvincúlalo antes."))
    serie = _serie(serie)
    n = numero(serie, codigo)
    if _ya_vinculados(session, [n], salvo=order.id):
        raise AlbaranNoVinculable("en_otro_pedido", (
            f"El albarán {n} ya está vinculado a otro pedido."))
    from datetime import date  # noqa: PLC0415

    ejercicio = ejercicio or str(date.today().year)
    # Del ejercicio en curso: es donde lo buscarán el PDF y el envío.
    if _existe_albaran(session, serie, int(codigo), ejercicio, client=client) is not True:
        raise AlbaranNoVinculable("no_existe", (
            f"No se encuentra el albarán {n} en el ejercicio {ejercicio} de FACTUSOL."))
    _attach_albaran(session, order, n, how="vinculado a mano desde la ficha del pedido",
                    actor_user_id=actor_user_id)
    session.commit()
    return n
