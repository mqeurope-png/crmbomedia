"""Comprobaciones del Cuadre que leen FACTUSOL (fuente `factusol`).

Cuidado con la API de DELSOL: estas corren en el job nocturno de worker-sync
(o encoladas con «Comprobar ahora»), nunca en la petición web; cada tabla se
lee UNA vez por pasada (`ctx.tabla`) y solo del ejercicio en curso. SOLO
LECTURA: no escriben en FACTUSOL (el worker de escritura `factusol:writes`
ni se toca) ni en la BD de BoHub.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

from app.erp.cuadre.checks_mysql import enlace_pedido
from app.erp.cuadre.contexto import Contexto
from app.erp.cuadre.registry import (
    ENTIDAD_FACTURA,
    ENTIDAD_PEDIDO,
    ENTIDAD_PRESUPUESTO,
    FUENTE_FACTUSOL,
    Hallazgo,
    comprobacion,
)

DOCUMENTOS = "/erp/documentos"
PROFORMAS = "/erp/proformas"
SEGUIMIENTO = "/erp/seguimiento"
POR_COBRAR = "/erp/orders?queue=por_cobrar"

#: Tolerancia de redondeo (la misma del escaneo de #382).
TOLERANCIA = 0.005
#: Recargo PayPal: la base de la cabecera = Σ líneas × 1,04.
RECARGO_PAYPAL = 1.04
_DESCUENTO_RE = re.compile(r"DESCUENTO", re.IGNORECASE)


def _num(value: Any) -> float:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _int(value: Any) -> int | None:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _s(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _eur(value: float) -> str:
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _numero(serie: int | None, codigo: int) -> str:
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415

    return visible_number(serie, codigo)


def _fecha_doc(value: Any) -> datetime | None:
    from app.integrations.factusol.quotes import _factusol_date  # noqa: PLC0415

    iso = _factusol_date(value)
    if not iso:
        return None
    return datetime.combine(date.fromisoformat(iso), datetime.min.time(), tzinfo=UTC)


def _banda(fac: dict[str, Any], prefijo: str) -> float:
    return round(sum(_num(fac.get(f"{prefijo}{i}FAC")) for i in range(1, 5)), 2)


# --- lecturas compartidas -------------------------------------------------------------


def facturas(ctx: Contexto) -> dict[tuple[int | None, int], dict[str, Any]]:
    """F_FAC del ejercicio por clave COMPUESTA (serie, código)."""
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    def indexar() -> dict[tuple[int | None, int], dict[str, Any]]:
        out: dict[tuple[int | None, int], dict[str, Any]] = {}
        for row in ctx.tabla("F_FAC"):
            codigo = _int(row.get("CODFAC"))
            if codigo is not None:
                out[(coerce_serie(row.get("TIPFAC")), codigo)] = row
        return out

    return ctx.cached("facturas", indexar)


def cobros(ctx: Contexto) -> dict[tuple[int, int], float]:
    """Σ IMPLCO de F_LCO por clave COMPUESTA (TFALCO, CFALCO)."""
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    def indexar() -> dict[tuple[int, int], float]:
        out: dict[tuple[int, int], float] = defaultdict(float)
        for row in ctx.tabla("F_LCO"):
            serie = coerce_serie(row.get("TFALCO"))
            codigo = _int(row.get("CFALCO"))
            if serie is not None and codigo is not None:
                out[(serie, codigo)] += _num(row.get("IMPLCO"))
        return dict(out)

    return ctx.cached("cobros", indexar)


def pedidos_por_factura(ctx: Contexto) -> dict[tuple[int, int], Any]:
    """Pedido de BoHub con la factura VINCULADA (serie + código)."""
    from app.erp.linked_invoice import get_linked_invoice  # noqa: PLC0415

    def indexar() -> dict[tuple[int, int], Any]:
        out: dict[tuple[int, int], Any] = {}
        for o in ctx.pedidos():
            if o.cancelled_at is not None:
                continue
            factura = get_linked_invoice(o)
            if factura is not None:
                out.setdefault((factura.serie, factura.codigo), o)
        return out

    return ctx.cached("pedidos_por_factura", indexar)


def facturas_de_bohub(ctx: Contexto) -> list[tuple[tuple[int, int], dict[str, Any]]]:
    """ALCANCE del Cuadre en FACTUSOL: solo las facturas VINCULADAS a un pedido
    de BoHub (y que están en el ejercicio leído). Lo que es solo de FACTUSOL
    —series que BoHub no usa, facturas anteriores al ERP, facturas hechas a
    mano sin pedido— no es un descuadre: el panel vigila lo que BoHub gestiona."""
    facs = facturas(ctx)
    return [
        (clave, facs[clave]) for clave in sorted(pedidos_por_factura(ctx)) if clave in facs
    ]


def _enlaces_factura(ctx: Contexto, serie: int | None, codigo: int) -> tuple[str, Any]:
    pedido = pedidos_por_factura(ctx).get((serie, codigo)) if serie is not None else None
    return (enlace_pedido(pedido.id) if pedido is not None else DOCUMENTOS), pedido


def _estado_fac(ctx: Contexto, estfac: Any) -> str:
    """ESTFAC → cobrada / parcial / pendiente / otro (con los valores de
    Configuración ERP: 2 / 1 / 0 por defecto)."""
    from app.integrations.factusol.service import invoice_estado_value  # noqa: PLC0415

    est = _s(estfac)
    if est.endswith(".0") and est[:-2].isdigit():
        est = est[:-2]
    for kind in ("cobrada", "parcial", "pendiente"):
        valor = ctx.cached(f"estfac:{kind}", lambda k=kind: invoice_estado_value(ctx.session, k))
        if valor is not None and est == str(valor):
            return kind
    return "pendiente" if est == "" else "otro"


# --- 1 · Factura con líneas que no son suyas (#382) -----------------------------------


def lineas_cuadran(lineas: list[dict[str, Any]], fac: dict[str, Any]) -> bool:
    """¿Las líneas de F_LFA son las de esta factura? Se comparan con la base de
    la cabecera admitiendo las formas legítimas en que no coinciden a pelo:

    - DESCUENTO global: en la cabecera (banda `IDTO`, y pronto pago `IPPA`) o
      como línea «DESCUENTO» del detalle;
    - recargo PayPal del 4 %: base = Σ líneas × 1,04;
    - portes / financiación: van en su banda (`BAS = NET − IDTO − IPPA + IPOR
      + IFIN`), no son línea;
    - una BANDA DE PORTES (IPOR > 0) con neto propio sin línea (p. ej. el 4 %
      de PayPal sobre líneas + portes en la banda de los portes: 2-526098,
      2-526103): su neto no es de las líneas, así que también se compara con
      la base SIN esa banda.

    Una factura contaminada con las líneas del pedido homónimo de otra serie
    (#382) no casa con ninguna de esas formas: allí las líneas suman DE MÁS, y
    quitar el neto de una banda de portes solo baja la base."""
    suma = round(sum(_num(r.get("TOTLFA")) for r in lineas), 2)
    descuento = round(sum(
        _num(r.get("TOTLFA")) for r in lineas
        if _DESCUENTO_RE.search(f"{_s(r.get('ARTLFA'))} {_s(r.get('DESLFA'))}")
    ), 2)
    bases: set[float] = set()
    for fuera in _combinaciones_portes(fac):
        def banda(prefijo: str, fuera: frozenset[int] = fuera) -> float:
            return round(sum(
                _num(fac.get(f"{prefijo}{i}FAC")) for i in range(1, 5) if i not in fuera
            ), 2)
        neto = banda("NET")
        bases |= {
            neto,
            round(neto - banda("IDTO") - banda("IPPA"), 2),
            round(banda("BAS") - banda("IPOR"), 2),
        }
    sin_descuento = round(suma - descuento, 2)
    totales = {suma, sin_descuento, round(sin_descuento - abs(descuento), 2)}
    n = len(lineas)
    for factor, tolerancia in ((1.0, TOLERANCIA), (RECARGO_PAYPAL, 0.01 + 0.005 * n)):
        for total in totales:
            for base in bases:
                if abs(round(total * factor, 2) - base) <= tolerancia:
                    return True
    return False


def _combinaciones_portes(fac: dict[str, Any]) -> list[frozenset[int]]:
    """Bandas que se pueden dejar FUERA de la base de las líneas: solo las que
    llevan portes (IPOR > 0); todas las combinaciones, empezando por ninguna."""
    portes = [i for i in range(1, 5) if _num(fac.get(f"IPOR{i}FAC")) > TOLERANCIA]
    out = [frozenset()]
    for i in portes:
        out += [c | {i} for c in out]
    return out


@comprobacion(
    id="factura_lineas_ajenas", orden=1,
    titulo="Factura con líneas que no son suyas",
    descripcion="Las líneas de una factura de un pedido de BoHub (F_LFA) no suman la base "
                "de su cabecera (secuela del bug #382), descontando DESCUENTO, recargo "
                "PayPal y portes.",
    severidad="alta", fuente=FUENTE_FACTUSOL, grupo="dinero",
)
def factura_lineas_ajenas(ctx: Contexto) -> Iterator[Hallazgo]:
    from app.erp.invoice_scan import index_lines_by_codigo, lines_for_invoice  # noqa: PLC0415

    indice = ctx.cached("lineas_fac", lambda: index_lines_by_codigo(ctx.tabla("F_LFA")))
    for (serie, codigo), fac in facturas_de_bohub(ctx):
        lineas = lines_for_invoice(indice, serie, codigo)
        base = _banda(fac, "NET")
        if not lineas and abs(base) <= TOLERANCIA:
            continue
        if lineas_cuadran(lineas, fac):
            continue
        suma = round(sum(_num(r.get("TOTLFA")) for r in lineas), 2)
        numero = _numero(serie, codigo)
        enlace, pedido = _enlaces_factura(ctx, serie, codigo)
        yield Hallazgo(
            entidad_tipo=ENTIDAD_FACTURA, entidad_id=numero,
            etiqueta=f"Factura {numero}",
            detalle=f"Sus {len(lineas)} líneas suman {_eur(suma)} y la base de la cabecera es "
                    f"{_eur(base)} (diferencia {_eur(suma - base)}). "
                    f"Cliente: {_s(fac.get('CNOFAC')) or '—'}.",
            pista_de_arreglo="La cabecera (base, IVA, total) es la buena: rehaz el detalle de la "
                             "factura en el escritorio de FACTUSOL con las líneas de su pedido.",
            enlace=enlace, arreglo_enlace=DOCUMENTOS, arreglo_boton="Ver en Documentos",
            huella_datos={"suma": suma, "base": base, "lineas": len(lineas)},
            datos={"importe": round(suma - base, 2),
                   "pedido": pedido.order_number if pedido is not None else None},
        )


# --- 2 · Cobro descuadrado BoHub ↔ FACTUSOL -------------------------------------------


@comprobacion(
    id="cobro_descuadrado", orden=2,
    titulo="Cobro descuadrado BoHub ↔ FACTUSOL",
    descripcion="El estado de cobro de la factura en FACTUSOL (ESTFAC) no coincide con "
                "el del pedido en BoHub, o está cobrada sin ninguna línea de cobro.",
    severidad="alta", fuente=FUENTE_FACTUSOL, grupo="dinero",
)
def cobro_descuadrado(ctx: Contexto) -> Iterator[Hallazgo]:
    facs = facturas(ctx)
    lineas_cobro = cobros(ctx)
    for (serie, codigo), o in pedidos_por_factura(ctx).items():
        fac = facs.get((serie, codigo))
        bohub = _s(o.factusol_cobro_status)
        if fac is None or bohub not in ("cobrada", "pendiente"):
            continue                          # sin factura (otra comprobación) o sin comprobar
        estado = _estado_fac(ctx, fac.get("ESTFAC"))
        numero = _numero(serie, codigo)
        if estado == "cobrada" and bohub == "pendiente":
            detalle = (f"FACTUSOL tiene la factura {numero} cobrada y BoHub la sigue dando "
                       "por pendiente.")
        elif estado in ("pendiente", "parcial") and bohub == "cobrada":
            detalle = (f"BoHub da la factura {numero} por cobrada y en FACTUSOL está "
                       f"{'con cobro parcial' if estado == 'parcial' else 'pendiente'}.")
        else:
            continue
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id, etiqueta=o.order_number or o.id,
            detalle=detalle,
            pista_de_arreglo="Pon al día el cobro con «Actualizar cobros FACTUSOL» en la "
                             "bandeja «Por cobrar» (o abriendo «Registrar cobro en FACTUSOL» "
                             "en la ficha, que relee FACTUSOL). Si FACTUSOL está mal, "
                             "corrígelo en el escritorio.",
            enlace=enlace_pedido(o.id), arreglo_enlace=POR_COBRAR,
            arreglo_boton="Ir a «Por cobrar»",
            huella_datos={"factusol": estado, "bohub": bohub, "factura": numero},
        )
    for (serie, codigo), fac in facturas_de_bohub(ctx):
        if _estado_fac(ctx, fac.get("ESTFAC")) != "cobrada":
            continue
        if (serie, codigo) in lineas_cobro or _num(fac.get("TOTFAC")) <= TOLERANCIA:
            continue
        numero = _numero(serie, codigo)
        enlace, _pedido = _enlaces_factura(ctx, serie, codigo)
        yield Hallazgo(
            entidad_tipo=ENTIDAD_FACTURA, entidad_id=numero, etiqueta=f"Factura {numero}",
            detalle=f"Cobrada en FACTUSOL (ESTFAC) sin ninguna línea de cobro en F_LCO "
                    f"(total {_eur(_num(fac.get('TOTFAC')))}). "
                    f"Cliente: {_s(fac.get('CNOFAC')) or '—'}.",
            pista_de_arreglo="Registra el cobro que falta con «Registrar cobro» en Documentos "
                             "(o en el escritorio), o quítale el «cobrada» si no se cobró.",
            enlace=enlace, arreglo_enlace=DOCUMENTOS, arreglo_boton="Ir a Documentos",
            huella_datos={"estfac": "cobrada", "lineas": 0,
                          "total": round(_num(fac.get("TOTFAC")), 2)},
        )


# --- 3 · Factura emitida sin cobro pasados N días ------------------------------------


@comprobacion(
    id="factura_sin_cobro", orden=3,
    titulo="Factura emitida sin cobro",
    descripcion="Factura de un pedido de BoHub de hace más de N días con importe "
                "pendiente de cobro.",
    severidad="alta", fuente=FUENTE_FACTUSOL, grupo="dinero",
    dias_defecto=30, dias_texto="Avisar pasados N días desde la factura",
)
def factura_sin_cobro(ctx: Contexto) -> Iterator[Hallazgo]:
    umbral = ctx.dias("factura_sin_cobro", 30)
    lineas_cobro = cobros(ctx)                # F_LCO vacía → la pasada no la juzga
    for (serie, codigo), fac in facturas_de_bohub(ctx):
        total = round(_num(fac.get("TOTFAC")), 2)
        if total <= TOLERANCIA:
            continue                          # abono / importe cero
        fecha = _fecha_doc(fac.get("FECFAC"))
        dias = ctx.dias_desde(fecha)
        if dias is None or dias <= umbral:
            continue
        if (serie, codigo) not in lineas_cobro and _estado_fac(ctx, fac.get("ESTFAC")) == "cobrada":
            continue                          # «cobrada sin líneas»: es de la comprobación 2
        cobrado = round(lineas_cobro.get((serie, codigo), 0.0), 2)
        pendiente = round(total - cobrado, 2)
        if pendiente <= TOLERANCIA:
            continue
        numero = _numero(serie, codigo)
        enlace, pedido = _enlaces_factura(ctx, serie, codigo)
        en_pedido = pedido is not None
        yield Hallazgo(
            entidad_tipo=ENTIDAD_FACTURA, entidad_id=numero, etiqueta=f"Factura {numero}",
            detalle=f"Del {fecha.date().isoformat() if fecha else '—'} ({dias} días): quedan "
                    f"{_eur(pendiente)} por cobrar de {_eur(total)}. "
                    f"Cliente: {_s(fac.get('CNOFAC')) or '—'}.",
            pista_de_arreglo=(
                "Cuando llegue el pago, apúntalo con «Registrar cobro en FACTUSOL» en la ficha "
                "del pedido." if en_pedido else
                "Cuando llegue el pago, apúntalo con «Registrar cobro» en Documentos → Facturas."
            ),
            enlace=enlace, arreglo_enlace=enlace if en_pedido else DOCUMENTOS,
            arreglo_boton="Abrir el pedido" if en_pedido else "Ir a Documentos",
            huella_datos={"pendiente": pendiente, "total": total},
            datos={"importe": pendiente, "dias": dias,
                   "pedido": pedido.order_number if pedido is not None else None},
        )


# --- 9 · Factura sin vincular / vínculo roto ------------------------------------------


@comprobacion(
    id="factura_sin_vincular", orden=9,
    titulo="Factura de FACTUSOL sin vincular o vínculo roto",
    descripcion="Factura de FACTUSOL que no está vinculada a su pedido, o pedido cuya "
                "factura vinculada no existe en FACTUSOL.",
    severidad="media", fuente=FUENTE_FACTUSOL, grupo="documentos",
)
def factura_sin_vincular(ctx: Contexto) -> Iterator[Hallazgo]:
    from app.erp.linked_invoice import get_linked_invoice  # noqa: PLC0415
    from app.integrations.factusol.invoice_reconcile import _is_invoiced  # noqa: PLC0415
    from app.integrations.factusol.service import _compose_ref, _store_ref_prefix  # noqa: PLC0415

    facs = facturas(ctx)
    por_ref: dict[str, list[tuple[int | None, int]]] = defaultdict(list)
    for clave, fac in facs.items():
        ref = _s(fac.get("REFFAC")).upper()
        if ref:
            por_ref[ref].append(clave)
    # Rango de códigos de cada serie en el ejercicio leído: un código vinculado
    # que cae DENTRO y no existe es un hueco o una anulada; fuera, es de otro
    # ejercicio (no se ha leído) o de una serie nueva, y no se juzga.
    rango: dict[int, tuple[int, int]] = {}
    for serie_f, codigo_f in facs:
        if serie_f is None:
            continue
        lo, hi = rango.get(serie_f, (codigo_f, codigo_f))
        rango[serie_f] = (min(lo, codigo_f), max(hi, codigo_f))
    for o in ctx.pedidos():
        if o.cancelled_at is not None:
            continue
        # a) La factura existe en FACTUSOL (misma referencia) y el pedido no la tiene.
        if not _is_invoiced(o) and o.order_number:
            ref = _compose_ref(o.order_number, _store_ref_prefix(ctx.session, o))
            claves = sorted(por_ref.get(ref.upper(), []), key=lambda k: (k[0] or 0, k[1]))
            if claves:
                numeros = [_numero(s, c) for s, c in claves]
                varias = len(claves) > 1
                yield Hallazgo(
                    entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id,
                    etiqueta=o.order_number,
                    detalle=(
                        f"FACTUSOL tiene {'las facturas' if varias else 'la factura'} "
                        f"{', '.join(numeros)} con su referencia {ref} y el pedido no tiene "
                        "factura vinculada." + (" Hay más de una: decide cuál." if varias else "")
                    ),
                    pista_de_arreglo="Vincúlala con «Vincular facturas de FACTUSOL…» en "
                                     "Seguimiento (primero enseña la vista previa).",
                    enlace=enlace_pedido(o.id), arreglo_enlace=SEGUIMIENTO,
                    arreglo_boton="Ir a Seguimiento",
                    huella_datos={"facturas": numeros},
                )
            continue
        # b) El pedido tiene vinculada una factura que no está en FACTUSOL.
        factura = get_linked_invoice(o)
        if factura is None or (factura.serie, factura.codigo) in facs:
            continue
        lo, hi = rango.get(factura.serie, (0, -1))
        if not lo < factura.codigo < hi:
            continue                          # fuera del rango leído: no se juzga
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id, etiqueta=o.order_number or o.id,
            detalle=f"Tiene vinculada la factura {factura.numero}, que no existe en FACTUSOL "
                    f"(ejercicio {ctx.ejercicio}): hueco o anulada.",
            pista_de_arreglo="Corrige o quita la factura vinculada desde la ficha del pedido "
                             "(o comprueba en el escritorio si se anuló).",
            enlace=enlace_pedido(o.id), arreglo_enlace=enlace_pedido(o.id),
            arreglo_boton="Abrir el pedido",
            huella_datos={"factura": factura.numero, "existe": False},
        )


# --- 12 · Proforma aceptada sin convertir ---------------------------------------------


@comprobacion(
    id="proforma_sin_convertir", orden=12,
    titulo="Proforma aceptada sin convertir",
    descripcion="Proforma aceptada hace más de N días que todavía no es un pedido de BoHub "
                "(es el embudo comercial: apagada por defecto).",
    severidad="baja", fuente=FUENTE_FACTUSOL, grupo="documentos",
    dias_defecto=90, dias_texto="Avisar pasados N días desde la proforma",
    activa_defecto=False,
)
def proforma_sin_convertir(ctx: Contexto) -> Iterator[Hallazgo]:
    from app.erp.quotes_bandeja import _orders_by_quote  # noqa: PLC0415
    from app.integrations.factusol.quotes import (  # noqa: PLC0415
        QUOTE_ESTADO_ACEPTADA,
        quote_estado,
    )
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    umbral = ctx.dias("proforma_sin_convertir", 90)
    candidatas: list[tuple[int | None, int, dict[str, Any], datetime, int]] = []
    for row in ctx.tabla("F_PRE"):
        codigo = _int(row.get("CODPRE"))
        if codigo is None or quote_estado(_int(row.get("ESTPRE"))) != QUOTE_ESTADO_ACEPTADA:
            continue
        fecha = _fecha_doc(row.get("FECPRE"))
        dias = ctx.dias_desde(fecha)
        if fecha is None or dias is None or dias <= umbral:
            continue
        candidatas.append((coerce_serie(row.get("TIPPRE")), codigo, row, fecha, dias))
    if not candidatas:
        return
    convertidas = _orders_by_quote(
        ctx.session, {(serie, str(codigo)) for serie, codigo, *_ in candidatas},
    )
    for serie, codigo, row, fecha, dias in candidatas:
        if (serie, str(codigo)) in convertidas:
            continue
        numero = _numero(serie or 1, codigo)
        total = round(_num(row.get("TOTPRE")), 2)
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PRESUPUESTO, entidad_id=numero,
            etiqueta=f"Proforma {numero}",
            detalle=f"Aceptada, del {fecha.date().isoformat()} ({dias} días), por "
                    f"{_eur(total)} y sin pedido en BoHub. "
                    f"Cliente: {_s(row.get('CNOPRE')) or '—'}.",
            pista_de_arreglo="Conviértela con «Convertir en pedido» en Proformas (cola "
                             "«Aceptadas · por convertir»), o recházala si ya no va.",
            enlace=PROFORMAS, arreglo_enlace=PROFORMAS, arreglo_boton="Ir a Proformas",
            huella_datos={"estado": "aceptada", "total": total},
            datos={"importe": total, "dias": dias},
        )

