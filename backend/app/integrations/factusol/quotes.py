"""Proformas (presupuestos F_PRE) y artículos (F_ART) — Fase C · C-4.

### El hallazgo que condiciona todo este módulo

`F_PRE` es **MONO-LÍNEA**. Verificado en la base real de Bomedia (653
presupuestos del ejercicio 2026): cada fila de `F_PRE` es un presupuesto
completo con sus totales ya calculados, y **no existe una tabla de líneas**
(`F_LPRE` no está; `F_LPP` es de pedidos a proveedor, no de presupuestos). El
detalle del presupuesto vive como texto libre en `REFPRE`, 250 caracteres.

Consecuencias prácticas:

- Crear una proforma = escribir UNA fila en `F_PRE` con el desglose serializado
  en `REFPRE` (`build_refpre_from_lines`) y los totales agregados.
- Para poder duplicar la proforma o volcarla a un pedido con cantidades y
  precios reales, el CRM guarda el desglose en su propia tabla
  (`factusol_quote_lines_cache`, migración 0088). Las proformas creadas en el
  FACTUSOL de escritorio no tienen caché → `get_quote` devuelve
  `line_source="ref_text"` y la UI degrada a modo simple.

### Trampa de la API (nos costó C-3-fix1 entero)

Un filtro de `CargaTabla` sobre una columna **inexistente** no da error:
devuelve `[]`. Por eso aquí solo se usan nombres de columna verificados contra
la base real, listados en `docs/erp/factusol-schema.md`.

Toda ESCRITURA de este módulo se serializa por la cola `factusol:writes`
(worker-factusol, concurrency=1): `next_codpre` es un `MAX+1` y dos creaciones
en paralelo pisarían la numeración.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from json import dumps as json_dumps
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.integrations.factusol.client import FactusolClient, FactusolError

logger = logging.getLogger(__name__)

#: Tabla de presupuestos/proformas. NO hay tabla de líneas (mono-línea).
TABLE_QUOTES = "F_PRE"
#: Tabla de artículos.
TABLE_ARTICLES = "F_ART"

#: `REFPRE` es un `varchar(250)`; pasarse trunca en silencio en FACTUSOL, así
#: que el recorte lo hacemos nosotros y de forma visible (con «…»).
REFPRE_MAX_LENGTH = 250

#: `TIPPRE` vale siempre '1' en los 653 presupuestos de Bomedia.
DEFAULT_TIPPRE = "1"
#: Almacén por defecto de la base real.
DEFAULT_ALMPRE = "GEN"
#: ISO 3166-1 numérico de España — el `CPAPRE` de todos los presupuestos.
DEFAULT_CPAPRE = "724"
#: IVA por defecto cuando la línea no trae uno.
DEFAULT_IVA_PCT = 21.0

#: Ventana por defecto del listado de proformas de un cliente.
DEFAULT_DAYS_BACK = 180
#: Tope de resultados: la API DELSOL **no soporta LIMIT**, se recorta en Python.
QUOTE_LIST_LIMIT = 100
ARTICLE_SEARCH_LIMIT = 50

#: Columnas de F_PRE que exponemos. Verificadas contra la base real; el resto
#: (bandas 2/3/4 de IVA y ~90 columnas más) no las necesita la UI.
QUOTE_FIELDS = (
    "CODPRE", "TIPPRE", "REFPRE", "FECPRE", "CLIPRE", "CNOPRE", "CDOPRE",
    "CPOPRE", "CCPPRE", "CPRPRE", "CNIPRE", "TELPRE", "EMAPRE",
    "NET1PRE", "PIVA1PRE", "IIVA1PRE", "TOTPRE", "FOPPRE", "ALMPRE",
)

#: Columnas de F_ART que exponemos en el buscador de artículos.
ARTICLE_FIELDS = (
    "CODART", "EANART", "DESART", "DEEART", "FAMART", "TIVART", "PCOART",
    "DT0ART", "STOART", "UMEART",
)


def _sql_escape(value: str) -> str:
    """Escapa un literal para el `filtro` (fragmento SQL WHERE crudo)."""
    return (value or "").replace("'", "''")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return default


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _factusol_date(value: Any) -> str | None:
    """Fecha de FACTUSOL → ISO `YYYY-MM-DD`. Devuelve None si no se entiende.

    La API sirve las fechas como `2026-08-05T00:00:00` (o ya como fecha); nos
    quedamos con la parte de día, que es lo único que usa la UI. Se valida
    antes de devolverla: un formato inesperado en UNA fila no puede tumbar el
    listado entero al filtrar por fecha."""
    if value in (None, ""):
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        logger.debug("factusol: fecha no reconocida %r", value)
        return None


# --- artículos ---------------------------------------------------------------


def _row_to_article(row: dict[str, Any]) -> dict[str, Any]:
    out = {k.lower(): row.get(k) for k in ARTICLE_FIELDS}
    out["codart"] = str(out.get("codart")).strip() if out.get("codart") else None
    # Alias para la UI: la descripción corta manda; el precio de coste PCOART
    # es el que sirve de sugerencia al operador (no hay tarifa de venta única).
    out["descripcion"] = str(out.get("desart") or "").strip() or None
    out["precio"] = _num(out.get("pcoart"))
    out["stock"] = _num(out.get("stoart"))
    out["iva_pct"] = _num(out.get("tivart"), DEFAULT_IVA_PCT)
    return out


def search_articles(
    client: FactusolClient, query: str, *, ejercicio: str,
) -> list[dict[str, Any]]:
    """Busca artículos en F_ART por código, EAN o descripción (LIKE).

    Un único filtro con OR sobre las tres columnas: el operador teclea «cable»
    o «8412345» sin tener que elegir criterio. Recorte en Python porque la API
    DELSOL no soporta LIMIT."""
    q = (query or "").strip()
    if not q:
        return []
    safe = _sql_escape(q)
    filtro = (
        f"UPPER(CODART) LIKE UPPER('%{safe}%') "
        f"OR UPPER(EANART) LIKE UPPER('%{safe}%') "
        f"OR UPPER(DESART) LIKE UPPER('%{safe}%')"
    )
    rows = client.load_table(TABLE_ARTICLES, filtro=filtro, ejercicio=ejercicio)
    return [_row_to_article(r) for r in rows[:ARTICLE_SEARCH_LIMIT]]


# --- lectura de proformas ----------------------------------------------------


def _row_to_quote(row: dict[str, Any]) -> dict[str, Any]:
    out = {k.lower(): row.get(k) for k in QUOTE_FIELDS}
    out["codpre"] = str(out.get("codpre")) if out.get("codpre") is not None else None
    out["clipre"] = str(out.get("clipre")) if out.get("clipre") is not None else None
    out["fecha"] = _factusol_date(out.get("fecpre"))
    out["referencia"] = str(out.get("refpre") or "").strip()
    out["cliente_nombre"] = str(out.get("cnopre") or "").strip() or None
    out["base"] = _num(out.get("net1pre"))
    out["iva"] = _num(out.get("iiva1pre"))
    out["total"] = _num(out.get("totpre"))
    return out


def list_quotes(
    client: FactusolClient, *, ejercicio: str, codcli: str | None = None,
    days_back: int = DEFAULT_DAYS_BACK, today: date | None = None,
) -> list[dict[str, Any]]:
    """Proformas de un cliente (o de todos) en los últimos `days_back` días.

    Se ordena DESC por CODPRE y se recorta en Python (la API no soporta LIMIT).
    El filtro de fecha se resuelve **en Python** a propósito: el dialecto SQL de
    la API DELSOL no está documentado y una función de fecha no soportada
    devolvería `[]` en silencio (la trampa de C-3-fix1). Filtrar por `CLIPRE`,
    que es una comparación trivial, sí es seguro.
    """
    filtro = "1=1"
    if codcli:
        filtro = f"CLIPRE={int(codcli)}" if str(codcli).strip().isdigit() \
            else f"CLIPRE='{_sql_escape(str(codcli))}'"
    rows = client.load_table(
        TABLE_QUOTES, filtro=f"{filtro} ORDER BY CODPRE DESC", ejercicio=ejercicio,
    )
    quotes = [_row_to_quote(r) for r in rows]
    if days_back and days_back > 0:
        ref = (today or datetime.now(UTC).date()).toordinal() - days_back
        quotes = [
            q for q in quotes
            if q["fecha"] is None or date.fromisoformat(q["fecha"]).toordinal() >= ref
        ]
    return quotes[:QUOTE_LIST_LIMIT]


def cached_lines(
    session: Session, codpre: str, ejercicio: str,
) -> list[dict[str, Any]]:
    """Desglose guardado por el CRM al crear la proforma. Lista vacía si la
    proforma se hizo en el FACTUSOL de escritorio."""
    from app.erp.models import FactusolQuoteLineCache  # noqa: PLC0415

    rows = session.scalars(
        select(FactusolQuoteLineCache)
        .where(
            FactusolQuoteLineCache.factusol_codpre == str(codpre),
            FactusolQuoteLineCache.ejercicio == str(ejercicio),
        )
        .order_by(FactusolQuoteLineCache.position)
    ).all()
    return [
        {
            "position": r.position,
            "codart": r.artlpc or None,
            "description": r.description,
            "quantity": float(r.quantity),
            "unit_price": float(r.unit_price),
            "discount_pct": float(r.discount_pct),
            "line_total": float(r.line_total),
            "iva_pct": float(r.iva_pct),
        }
        for r in rows
    ]


def get_quote(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
) -> dict[str, Any] | None:
    """Una proforma con su desglose. None si el CODPRE no existe.

    `line_source` dice de dónde salen las líneas:
    - `"cache"`: la creó el CRM y tenemos el desglose real.
    - `"ref_text"`: se creó en el escritorio; solo hay el texto de `REFPRE`.
    """
    rows = client.load_table(
        TABLE_QUOTES, filtro=f"CODPRE={int(codpre)}", ejercicio=ejercicio,
    ) if str(codpre).strip().isdigit() else []
    if not rows:
        return None
    quote = _row_to_quote(rows[0])
    lines = cached_lines(session, str(quote["codpre"]), ejercicio)
    quote["lines"] = lines
    quote["line_source"] = "cache" if lines else "ref_text"
    return quote


# --- escritura de proformas --------------------------------------------------


def build_refpre_from_lines(lines: list[dict[str, Any]]) -> str:
    """Serializa el desglose en el texto de `REFPRE` (250 caracteres).

    F_PRE es mono-línea: este texto es TODO lo que ve Bart en el FACTUSOL de
    escritorio, así que se escribe legible (`2x Cable HDMI 3m; 1x Montaje`) y se
    trunca con «…» si no cabe, en vez de dejar que FACTUSOL lo corte a medias.
    """
    parts = []
    for line in lines:
        desc = str(line.get("description") or line.get("codart") or "").strip()
        if not desc:
            continue
        qty = _num(line.get("quantity"), 1.0)
        qty_text = f"{qty:g}"
        parts.append(f"{qty_text}x {desc}")
    text = "; ".join(parts)
    if len(text) <= REFPRE_MAX_LENGTH:
        return text
    return text[: REFPRE_MAX_LENGTH - 1].rstrip() + "…"


def next_codpre(client: FactusolClient, ejercicio: str) -> str:
    """Siguiente CODPRE = max + 1. Misma estrategia que `next_codfac` /
    `next_codcli`: sin LIMIT en la API, se pide ordenado DESC y se toma la
    primera fila. La race la evita el worker serializado."""
    rows = client.load_table(
        TABLE_QUOTES, filtro="1=1 ORDER BY CODPRE DESC", ejercicio=ejercicio,
    )
    if not rows:
        return "1"
    return str((_int_or_none(rows[0].get("CODPRE")) or 0) + 1)


def _totals(lines: list[dict[str, Any]]) -> dict[str, float]:
    """Base, IVA y total del conjunto de líneas.

    Solo se usa la banda 1 de IVA (`NET1PRE`/`IIVA1PRE`): mezclar tipos en una
    misma proforma exigiría repartir en las bandas 2/3/4, y las proformas de
    Bomedia son de un solo tipo. Si llegan varios, se aplica el tipo de la
    primera línea al total y se deja constancia en el log."""
    base = 0.0
    for line in lines:
        qty = _num(line.get("quantity"), 1.0)
        price = _num(line.get("unit_price"))
        discount = _num(line.get("discount_pct"))
        base += qty * price * (1 - discount / 100)
    rates = {_num(line.get("iva_pct"), DEFAULT_IVA_PCT) for line in lines}
    iva_pct = _num(lines[0].get("iva_pct"), DEFAULT_IVA_PCT) if lines else DEFAULT_IVA_PCT
    if len(rates) > 1:
        logger.warning(
            "factusol: proforma con varios tipos de IVA %s; se aplica %.2f a "
            "toda la base (F_PRE solo usa la banda 1)", sorted(rates), iva_pct,
        )
    base = round(base, 2)
    iva = round(base * iva_pct / 100, 2)
    return {"base": base, "iva_pct": iva_pct, "iva": iva,
            "total": round(base + iva, 2)}


def build_quote_payload(
    codpre: str, *, ejercicio: str, customer: dict[str, Any],
    refpre: str, lines: list[dict[str, Any]], fecha: str | None = None,
    fopfac: str | None = None,
) -> dict[str, Any]:
    """Registro F_PRE listo para `EscribirRegistro`.

    Solo columnas verificadas contra la base real. Las que no ponemos las deja
    FACTUSOL con sus defaults — no inventamos valores (la lección de C-3-fix1).
    """
    totals = _totals(lines)
    payload: dict[str, Any] = {
        "CODPRE": codpre,
        "TIPPRE": DEFAULT_TIPPRE,
        "REFPRE": refpre[:REFPRE_MAX_LENGTH],
        "FECPRE": fecha or datetime.now(UTC).date().isoformat(),
        "CLIPRE": str(customer.get("codcli") or ""),
        "CNOPRE": str(customer.get("nombre") or "")[:255],
        "CDOPRE": str(customer.get("direccion") or "")[:255],
        "CPOPRE": str(customer.get("ciudad") or "")[:255],
        "CCPPRE": str(customer.get("cp") or "")[:20],
        "CPRPRE": str(customer.get("provincia") or "")[:255],
        "CNIPRE": str(customer.get("nif") or "")[:64],
        "CPAPRE": DEFAULT_CPAPRE,
        "ALMPRE": DEFAULT_ALMPRE,
        "NET1PRE": totals["base"],
        "PIVA1PRE": totals["iva_pct"],
        "IIVA1PRE": totals["iva"],
        "TOTPRE": totals["total"],
    }
    if customer.get("telefono"):
        payload["TELPRE"] = str(customer["telefono"])[:40]
    if customer.get("email"):
        payload["EMAPRE"] = str(customer["email"])[:255]
    if fopfac:
        payload["FOPPRE"] = str(fopfac)
    return payload


def _save_lines_cache(
    session: Session, codpre: str, ejercicio: str, lines: list[dict[str, Any]],
) -> None:
    """Guarda el desglose en la caché local (el que F_PRE no puede almacenar).

    Borra primero lo que hubiera para ese CODPRE: si un reintento reescribe la
    misma proforma no queremos líneas duplicadas ni chocar con el UNIQUE."""
    from app.erp.models import FactusolQuoteLineCache  # noqa: PLC0415

    session.execute(
        delete(FactusolQuoteLineCache).where(
            FactusolQuoteLineCache.factusol_codpre == str(codpre),
            FactusolQuoteLineCache.ejercicio == str(ejercicio),
        )
    )
    now = datetime.now(UTC)
    for i, line in enumerate(lines, start=1):
        qty = _num(line.get("quantity"), 1.0)
        price = _num(line.get("unit_price"))
        discount = _num(line.get("discount_pct"))
        session.add(FactusolQuoteLineCache(
            factusol_codpre=str(codpre),
            ejercicio=str(ejercicio),
            position=i,
            artlpc=str(line.get("codart") or "")[:64],
            description=str(line.get("description") or "")[:255],
            quantity=qty,
            unit_price=price,
            discount_pct=discount,
            line_total=round(qty * price * (1 - discount / 100), 2),
            iva_pct=_num(line.get("iva_pct"), DEFAULT_IVA_PCT),
            created_at=now,
        ))


def create_quote(
    client: FactusolClient, session: Session, *, ejercicio: str,
    customer: dict[str, Any], lines: list[dict[str, Any]],
    referencia: str | None = None, fecha: str | None = None,
    fopfac: str | None = None,
) -> dict[str, Any]:
    """Crea la proforma en F_PRE (una sola fila) y cachea su desglose.

    `referencia` la escribe el operador en modo «rápido» (proforma de una
    línea de texto); si no la pasa, se compone desde las líneas.

    Orden deliberado: primero FACTUSOL, después la caché local. Si FACTUSOL
    falla no hay nada que limpiar; si falla el commit local, la proforma existe
    en la contabilidad y solo perdemos el desglose (degrada a modo simple), que
    es el fallo menos malo de los dos.
    """
    if not customer.get("codcli"):
        raise FactusolError(
            "La proforma necesita un cliente de FACTUSOL (CODCLI). Vincula la "
            "empresa antes de crearla."
        )
    if not lines and not (referencia or "").strip():
        raise FactusolError("La proforma necesita al menos una línea o una referencia.")

    refpre = (referencia or "").strip() or build_refpre_from_lines(lines)
    codpre = next_codpre(client, ejercicio)
    payload = build_quote_payload(
        codpre, ejercicio=ejercicio, customer=customer, refpre=refpre,
        lines=lines, fecha=fecha, fopfac=fopfac,
    )
    client.write_record(TABLE_QUOTES, payload, ejercicio=ejercicio)
    cached = _try_cache_lines(session, codpre, ejercicio, lines)
    logger.info("factusol: proforma creada CODPRE %s (cliente %s, %d líneas)",
                codpre, customer.get("codcli"), len(lines))
    return {"codpre": codpre, "ejercicio": ejercicio, "referencia": refpre,
            "lines": len(lines), "total": payload["TOTPRE"], "cached": cached}


def _try_cache_lines(
    session: Session, codpre: str, ejercicio: str, lines: list[dict[str, Any]],
) -> bool:
    """Guarda el desglose sin dejar que un fallo local tumbe la operación.

    Cuando se llama, la proforma YA existe en FACTUSOL. Propagar el error haría
    que el job se marcase fallido y el operador reintentase — creando una
    proforma **duplicada** en la contabilidad. Perder el desglose solo degrada
    esa proforma a modo «simple», que es reparable; el duplicado no.
    """
    if not lines:
        return False
    try:
        _save_lines_cache(session, codpre, ejercicio, lines)
        session.commit()
        return True
    except Exception:  # noqa: BLE001 — la proforma ya está escrita en FACTUSOL
        session.rollback()
        logger.warning(
            "factusol: proforma %s creada pero no se pudo cachear su desglose; "
            "se leerá en modo simple", codpre, exc_info=True,
        )
        return False


def duplicate_quote(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
    fecha: str | None = None,
) -> dict[str, Any]:
    """Copia una proforma existente con CODPRE nuevo y fecha de hoy.

    Copia la fila entera de F_PRE (así arrastra cliente, importes y todas las
    columnas que no mapeamos) y solo sustituye CODPRE y FECPRE. El desglose
    cacheado, si lo hay, se duplica también.
    """
    if not str(codpre).strip().isdigit():
        raise FactusolError(f"CODPRE inválido: {codpre!r}")
    rows = client.load_table(
        TABLE_QUOTES, filtro=f"CODPRE={int(codpre)}", ejercicio=ejercicio,
    )
    if not rows:
        raise FactusolError(f"La proforma {codpre} no existe en el ejercicio {ejercicio}")

    source = dict(rows[0])
    nuevo = next_codpre(client, ejercicio)
    source["CODPRE"] = nuevo
    source["FECPRE"] = fecha or datetime.now(UTC).date().isoformat()
    client.write_record(TABLE_QUOTES, source, ejercicio=ejercicio)

    lines = cached_lines(session, str(codpre), ejercicio)
    _try_cache_lines(session, nuevo, ejercicio, lines)
    logger.info("factusol: proforma %s duplicada → %s", codpre, nuevo)
    return {"codpre": nuevo, "source_codpre": str(codpre), "ejercicio": ejercicio,
            "lines": len(lines)}


def quote_lines_for_order(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
) -> dict[str, Any]:
    """Líneas de la proforma listas para volcarlas a un pedido.

    Si el desglose está cacheado se devuelve tal cual. Si no (proforma hecha en
    el escritorio), se devuelve **una** línea con el texto de `REFPRE` y la base
    imponible como importe, que el operador ajusta a mano — es lo máximo que se
    puede reconstruir de una tabla mono-línea.
    """
    quote = get_quote(client, session, codpre, ejercicio=ejercicio)
    if quote is None:
        raise FactusolError(f"La proforma {codpre} no existe en el ejercicio {ejercicio}")
    lines = quote["lines"]
    if not lines:
        lines = [{
            "position": 1,
            "codart": None,
            "description": quote["referencia"] or f"Proforma {codpre}",
            "quantity": 1.0,
            "unit_price": quote["base"],
            "discount_pct": 0.0,
            "line_total": quote["base"],
            "iva_pct": _num(quote.get("piva1pre"), DEFAULT_IVA_PCT),
        }]
    return {
        "codpre": str(quote["codpre"]),
        "ejercicio": ejercicio,
        "line_source": quote["line_source"],
        "lines": lines,
        "total": quote["total"],
        "referencia": quote["referencia"],
        "clipre": quote["clipre"],
    }


def convert_quote_to_order(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Convierte la proforma en un **pedido manual del CRM** (`Order`).

    Deliberadamente **no escribe un F_PCL en FACTUSOL**. Dos razones:

    1. El pedido de cliente de los pedidos Woo lo crea la app externa
       Woo→FACTUSOL; duplicar esa escritura es justo lo que provocó el
       `BDEscribirRegistroError` de C-2-fix1.
    2. El mapeo F_PRE→F_PCL exigiría dar por buena una correspondencia de
       columnas por sufijo que **no está verificada** contra la base real. En
       lectura una columna inexistente devuelve `[]` en silencio, pero en
       `EscribirRegistro` revienta — y esto va contra la contabilidad de
       producción. No se escribe a ciegas.

    El pedido creado sigue el circuito normal del ERP (preparar → embalar →
    enviar → `emit_invoice`), que es lo que Bart necesita de «convertir».
    """
    from app.erp.api.orders import _next_manual_number  # noqa: PLC0415
    from app.erp.models import (  # noqa: PLC0415
        Order,
        OrderLine,
        OrderSource,
        OrderStatusHistory,
        StatusDomain,
    )
    from app.models.crm import Company  # noqa: PLC0415

    data = quote_lines_for_order(client, session, codpre, ejercicio=ejercicio)
    codpre = data["codpre"]

    # El cliente del pedido se resuelve por el vínculo CRM ↔ CODCLI que ya
    # mantiene C-3. Sin vínculo el pedido se crea igualmente (sin empresa) y el
    # operador la asigna: es preferible a perder la conversión.
    company_id = None
    if data["clipre"]:
        company_id = session.scalar(
            select(Company.id).where(Company.factusol_company_id == data["clipre"])
        )

    order = Order(
        external_source=OrderSource.MANUAL,
        order_number=_next_manual_number(session),
        company_id=company_id,
        placed_at=datetime.now(UTC),
    )
    session.add(order)
    session.flush()

    total = 0.0
    for i, line in enumerate(data["lines"]):
        line_total = round(
            _num(line["quantity"], 1.0) * _num(line["unit_price"])
            * (1 - _num(line.get("discount_pct")) / 100),
            2,
        )
        total += line_total
        session.add(OrderLine(
            order_id=order.id, position=i,
            product_sku="", product_codart=line.get("codart"),
            description=line["description"],
            quantity=_num(line["quantity"], 1.0),
            unit_price=_num(line["unit_price"]),
            tax_rate=_num(line.get("iva_pct"), DEFAULT_IVA_PCT),
            line_total=line_total,
        ))
    order.total_amount = round(total, 2)

    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.PREPARATION,
        from_status=None,
        to_status=getattr(order.preparation_status, "value", order.preparation_status),
        changed_at=datetime.now(UTC), changed_by_user_id=actor_user_id,
        reason=f"Pedido creado desde la proforma FACTUSOL {codpre}",
        metadata_json=json_dumps({
            "event": "order_created_from_quote",
            "factusol_codpre": codpre,
            "factusol_ejercicio": ejercicio,
            "line_source": data["line_source"],
        }),
    ))
    session.commit()
    logger.info("factusol: proforma %s → pedido %s (%d líneas)",
                codpre, order.order_number, len(data["lines"]))
    return {
        "codpre": codpre, "ejercicio": ejercicio,
        "order_id": order.id, "order_number": order.order_number,
        "line_source": data["line_source"], "lines": len(data["lines"]),
        "total": order.total_amount,
    }
