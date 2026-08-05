"""Proformas (presupuestos F_PRE + líneas F_LPS) y artículos (F_ART + F_LTA).

### Corrección importante (C-4-fix3, 2026-08-05)

C-4 dio por hecho que **F_PRE era mono-línea** tras probar `F_LPRE`, `F_LPR`,
`F_LPP`… sin acertar. Era **falso**: las líneas existen y viven en **`F_LPS`**
(3063 filas en 2026), con `F_LPS.CODLPS = F_PRE.CODPRE`. El nombre no seguía el
patrón que buscábamos, y por eso se escapó.

Verificado en vivo contra la base de Bomedia:

- `F_LPS WHERE CODLPS=574` → las 4 líneas del presupuesto de Roca Joiers
  (Cabezal MBO 250 + Capping 25 + Wiper 20 + Hora SAT 60 = 355), que cuadra
  con el `NET1PRE=355` de la cabecera.
- `F_LPS WHERE CODLPS=1` → las 21 líneas de AUDIOVISUALES DATA, incluidas
  líneas de texto libre (`ARTLPS=''`).

Consecuencias: `factusol_quote_lines_cache` (migración 0088) queda **obsoleta**
— era un apaño para un problema que no existía. La tabla se conserva por si
guardó algo entre #309 y este PR, pero ya no se lee ni se escribe.

Lo mismo con el precio de venta: no está en `F_ART` sino en **`F_LTA`** (tarifas
por artículo), con `ARTLTA` → `F_ART.CODART` y `PRELTA` = precio. Bomedia usa
`TARLTA=1`. Verificado: `99cy` → 80.00 €, `1503` → 20.00 €.

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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.factusol.client import FactusolClient, FactusolError

logger = logging.getLogger(__name__)

#: Cabecera de presupuestos/proformas.
TABLE_QUOTES = "F_PRE"
#: **Líneas** de presupuesto. `CODLPS` referencia a `F_PRE.CODPRE`.
#: Descubierta en C-4-fix3: C-4 la buscó como F_LPRE/F_LPR/F_LPP y falló.
TABLE_QUOTE_LINES = "F_LPS"
#: Tabla de artículos.
TABLE_ARTICLES = "F_ART"
#: Tarifas por artículo. `ARTLTA` → `F_ART.CODART`, `PRELTA` = precio de venta.
TABLE_TARIFFS = "F_LTA"

#: Tarifa que usa Bomedia. Sus precios son los que el FACTUSOL de escritorio
#: muestra en la columna «Venta». Multi-tarifa por cliente es backlog: iría a
#: `erp_settings.factusol_default_tarifa`, no aquí.
DEFAULT_TARIFA = 1

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
#: Tope del autocomplete de artículos. C-4-fix2 lo sube de 50 a 200: el
#: desplegable tiene scroll interno, así que cortar bajo solo escondía
#: resultados válidos («tinta» devuelve más de 100 artículos).
ARTICLE_SEARCH_LIMIT = 200

#: Columnas de F_PRE que exponemos. Verificadas contra la base real; el resto
#: (bandas 2/3/4 de IVA y ~90 columnas más) no las necesita la UI.
QUOTE_FIELDS = (
    "CODPRE", "TIPPRE", "REFPRE", "FECPRE", "CLIPRE", "CNOPRE", "CDOPRE",
    "CPOPRE", "CCPPRE", "CPRPRE", "CNIPRE", "TELPRE", "EMAPRE",
    "NET1PRE", "PIVA1PRE", "IIVA1PRE", "TOTPRE", "FOPPRE", "ALMPRE",
)

#: Columnas de F_ART que exponemos en el buscador de artículos.
#: `EQUART` es el **SKU comercial** (el que teclean los operativos: `CDR80WPT`,
#: `BOB180-25`), distinto del `CODART` interno (`00001`).
ARTICLE_FIELDS = (
    "CODART", "EANART", "EQUART", "DESART", "DEEART", "DETART",
    "FAMART", "TIVART", "PCOART", "DT0ART", "STOART", "UMEART",
)

#: Columnas donde busca el autocomplete de artículos. Son las 6 que identifican
#: un artículo en la base real de Bomedia — verificadas con el script de
#: descubrimiento. Antes solo se miraba en CODART/EANART/DESART y buscar por el
#: SKU comercial no encontraba nada (C-4-fix1):
#:
#:     CODART '00001'  EQUART 'CDR80WPT'
#:     DESART 'CD TQ 700 MB white Thermal WPT'   ← «CDR80» NO aparece aquí
#:     DEEART 'CD TQ 700 MB white Thermal WPT'
#:     DETART 'CD TQ 700 MB white T'
ARTICLE_SEARCH_COLUMNS = (
    "CODART", "EANART", "EQUART", "DESART", "DEEART", "DETART",
)

#: Columnas de F_LPS (líneas de presupuesto), verificadas en la base real.
QUOTE_LINE_FIELDS = (
    "TIPLPS", "CODLPS", "POSLPS", "ARTLPS", "DESLPS", "CANLPS",
    "DT1LPS", "DT2LPS", "DT3LPS", "PRELPS", "TOTLPS", "IVALPS",
)

#: `TIPLPS` vale siempre '1', igual que el `TIPPRE` de la cabecera.
DEFAULT_TIPLPS = "1"


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


def tariff_prices(
    client: FactusolClient, codarts: list[str], *, ejercicio: str,
    tarifa: int = DEFAULT_TARIFA,
) -> dict[str, float]:
    """`{CODART: precio}` de la tarifa indicada, en UNA sola consulta a F_LTA.

    El precio de venta NO está en F_ART (ahí `PCOART` es el **coste**): vive en
    F_LTA, una fila por artículo y tarifa. Se pide en lote con un `IN (…)` para
    no hacer N peticiones desde el autocomplete.
    """
    codarts = [c for c in dict.fromkeys(codarts) if c]
    if not codarts:
        return {}
    in_list = ",".join(f"'{_sql_escape(str(c))}'" for c in codarts)
    rows = client.load_table(
        TABLE_TARIFFS,
        filtro=f"TARLTA={int(tarifa)} AND ARTLTA IN ({in_list})",
        ejercicio=ejercicio,
    )
    prices: dict[str, float] = {}
    for row in rows:
        codart = str(row.get("ARTLTA") or "").strip()
        price = _num(row.get("PRELTA"))
        # PRELTA=0 significa «tarifa sin precio configurado» (pasa con los
        # artículos que solo tienen Tarifa 2). Se trata como ausente para que
        # la UI deje el campo vacío en vez de proponer 0,00 €.
        if codart and price:
            prices[codart] = price
    return prices


def _row_to_article(row: dict[str, Any]) -> dict[str, Any]:
    out = {k.lower(): row.get(k) for k in ARTICLE_FIELDS}
    out["codart"] = str(out.get("codart")).strip() if out.get("codart") else None
    out["equart"] = str(out.get("equart") or "").strip() or None
    # Alias para la UI. `sku` es lo que el operativo reconoce: el código
    # comercial (EQUART) y, si no lo tiene, el interno (CODART).
    out["sku"] = out.get("equart") or out.get("codart")
    # Descripción: larga → media → corta, la primera no vacía.
    out["descripcion"] = next(
        (str(out.get(k) or "").strip() for k in ("desart", "deeart", "detart")
         if str(out.get(k) or "").strip()),
        None,
    )
    # PCOART es precio de COSTE — nunca es el precio que se factura.
    out["precio_coste"] = _num(out.get("pcoart"))
    # El de VENTA lo rellena `search_articles` desde F_LTA (no está en F_ART).
    out["precio_venta"] = None
    out["precio_venta_source"] = None
    out["precio"] = out["precio_coste"]
    out["stock"] = _num(out.get("stoart"))
    out["iva_pct"] = _num(out.get("tivart"), DEFAULT_IVA_PCT)
    return out


def search_articles(
    client: FactusolClient, query: str, *, ejercicio: str,
) -> list[dict[str, Any]]:
    """Busca artículos en F_ART por cualquiera de sus 6 identificadores (LIKE).

    Un único filtro con OR: el operador teclea «CDR80», «8412345» o «cable» sin
    elegir criterio. Recorte en Python porque la API DELSOL no soporta LIMIT.

    C-4-fix1: se buscaba solo en CODART/EANART/DESART, así que teclear el **SKU
    comercial** no encontraba nada — ese vive en `EQUART`, y la descripción
    puede estar solo en `DEEART`/`DETART`. Ver `ARTICLE_SEARCH_COLUMNS`.

    C-4-fix3: el **precio de venta** se completa desde `F_LTA` (tarifa 1) con
    una única consulta en lote. En F_ART solo está `PCOART`, que es coste."""
    q = (query or "").strip()
    if not q:
        return []
    safe = _sql_escape(q)
    filtro = " OR ".join(
        f"UPPER({col}) LIKE UPPER('%{safe}%')" for col in ARTICLE_SEARCH_COLUMNS
    )
    rows = client.load_table(TABLE_ARTICLES, filtro=filtro, ejercicio=ejercicio)
    articles = [_row_to_article(r) for r in rows[:ARTICLE_SEARCH_LIMIT]]

    # Precio de venta en lote. Si F_LTA falla, se devuelven los artículos sin
    # precio: mejor un autocomplete usable con el precio a mano que ninguno.
    try:
        prices = tariff_prices(
            client, [a["codart"] for a in articles if a.get("codart")],
            ejercicio=ejercicio,
        )
    except FactusolError:
        logger.warning("factusol: no se pudieron leer precios de %s",
                       TABLE_TARIFFS, exc_info=True)
        prices = {}
    for article in articles:
        price = prices.get(article.get("codart") or "")
        if price:
            article["precio_venta"] = price
            article["precio_venta_source"] = f"{TABLE_TARIFFS}_TAR{DEFAULT_TARIFA}"
            article["precio"] = price
    return articles


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


def _quote_matches(quote: dict[str, Any], needle: str) -> bool:
    """¿La proforma casa con el texto buscado? Mira en la referencia, el nombre
    del cliente de origen y el propio número — que es como Bart identifica una
    plantilla («la de Laboratorios Duaner», «la 512», «rotulación»)."""
    haystack = " ".join(str(x or "") for x in (
        quote.get("referencia"), quote.get("cliente_nombre"), quote.get("codpre"),
    ))
    return needle in haystack.casefold()


def list_quotes(
    client: FactusolClient, *, ejercicio: str, codcli: str | None = None,
    days_back: int = DEFAULT_DAYS_BACK, today: date | None = None,
    text: str | None = None, limit: int = QUOTE_LIST_LIMIT,
) -> list[dict[str, Any]]:
    """Proformas de un cliente (o de TODOS si `codcli` es None) en los últimos
    `days_back` días, opcionalmente filtradas por `text`.

    Se ordena DESC por CODPRE y se recorta en Python (la API no soporta LIMIT).
    El filtro de fecha se resuelve **en Python** a propósito: el dialecto SQL de
    la API DELSOL no está documentado y una función de fecha no soportada
    devolvería `[]` en silencio (la trampa de C-3-fix1). Filtrar por `CLIPRE`,
    que es una comparación trivial, sí es seguro.

    `text` también se aplica en Python, y **antes** del recorte a `limit`: si se
    truncase primero, buscar una plantilla antigua no la encontraría nunca
    porque las 100 más recientes se la habrían comido.
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
    needle = (text or "").strip().casefold()
    if needle:
        quotes = [q for q in quotes if _quote_matches(q, needle)]
    return quotes[:limit]


def _row_to_quote_line(row: dict[str, Any]) -> dict[str, Any]:
    """Fila de F_LPS → la forma que ya consumía el frontend desde C-4."""
    return {
        "position": _int_or_none(row.get("POSLPS")) or 0,
        "codart": str(row.get("ARTLPS") or "").strip() or None,
        "description": str(row.get("DESLPS") or "").strip(),
        "quantity": _num(row.get("CANLPS")),
        "unit_price": _num(row.get("PRELPS")),
        "discount_pct": _num(row.get("DT1LPS")),
        "line_total": _num(row.get("TOTLPS")),
        # IVALPS vacío en líneas de texto libre → 21 % por defecto.
        "iva_pct": _num(row.get("IVALPS")) or DEFAULT_IVA_PCT,
    }


def list_quote_lines(
    client: FactusolClient, codpre: str, *, ejercicio: str,
) -> list[dict[str, Any]]:
    """Líneas REALES de una proforma, leídas de `F_LPS`.

    `F_LPS.CODLPS = F_PRE.CODPRE` (descubierto en C-4-fix3). Sustituye a
    `factusol_quote_lines_cache`, que era un apaño por haber buscado la tabla de
    líneas con el nombre equivocado. Funciona con **todas** las proformas,
    incluidas las creadas en el FACTUSOL de escritorio.
    """
    if not str(codpre).strip().isdigit():
        return []
    rows = client.load_table(
        TABLE_QUOTE_LINES,
        filtro=f"CODLPS={int(codpre)} ORDER BY POSLPS",
        ejercicio=ejercicio,
    )
    return [_row_to_quote_line(r) for r in rows]


def get_quote(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
) -> dict[str, Any] | None:
    """Una proforma con su desglose real de F_LPS. None si el CODPRE no existe.

    `session` ya no se usa para leer líneas (la caché local quedó obsoleta en
    C-4-fix3); se mantiene en la firma porque los llamadores la pasan y para no
    romper la API interna.
    """
    _ = session
    rows = client.load_table(
        TABLE_QUOTES, filtro=f"CODPRE={int(codpre)}", ejercicio=ejercicio,
    ) if str(codpre).strip().isdigit() else []
    if not rows:
        return None
    quote = _row_to_quote(rows[0])
    quote["lines"] = list_quote_lines(client, str(quote["codpre"]), ejercicio=ejercicio)
    quote["line_source"] = TABLE_QUOTE_LINES
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


def build_quote_line_payload(
    codpre: str, position: int, line: dict[str, Any],
) -> dict[str, Any]:
    """Una línea del CRM → registro `F_LPS` listo para `EscribirRegistro`.

    Solo columnas verificadas. Las medidas (`ALTLPS`/`ANCLPS`/`FONLPS`) y
    `MEMLPS` se dejan a FACTUSOL: no inventamos valores."""
    qty = _num(line.get("quantity"), 1.0)
    price = _num(line.get("unit_price"))
    discount = _num(line.get("discount_pct"))
    return {
        "TIPLPS": DEFAULT_TIPLPS,
        "CODLPS": codpre,
        "POSLPS": position,
        "ARTLPS": str(line.get("codart") or "")[:64],
        "DESLPS": str(line.get("description") or "")[:255],
        "CANLPS": qty,
        "DT1LPS": discount,
        "PRELPS": price,
        "TOTLPS": round(qty * price * (1 - discount / 100), 2),
        "IVALPS": _num(line.get("iva_pct"), DEFAULT_IVA_PCT),
    }


def _write_quote_lines(
    client: FactusolClient, codpre: str, ejercicio: str,
    lines: list[dict[str, Any]],
) -> int:
    """Escribe las líneas en F_LPS. Devuelve cuántas se escribieron.

    Si una línea falla NO se propaga: la cabecera F_PRE ya existe, y hacer
    fallar el job llevaría al operador a reintentar y crear una proforma
    **duplicada** en la contabilidad. Se deja constancia en el log y el job
    devuelve el recuento real para que la UI avise. Misma política que la
    caché en C-4, ahora sobre la tabla buena.
    """
    written = 0
    for i, line in enumerate(lines, start=1):
        try:
            client.write_record(
                TABLE_QUOTE_LINES, build_quote_line_payload(codpre, i, line),
                ejercicio=ejercicio,
            )
            written += 1
        except FactusolError:
            logger.warning(
                "factusol: proforma %s creada pero falló la línea %d; queda con "
                "%d de %d líneas", codpre, i, written, len(lines), exc_info=True,
            )
            break
    return written


def create_quote(
    client: FactusolClient, session: Session, *, ejercicio: str,
    customer: dict[str, Any], lines: list[dict[str, Any]],
    referencia: str | None = None, fecha: str | None = None,
    fopfac: str | None = None,
) -> dict[str, Any]:
    """Crea la proforma: cabecera en `F_PRE` + una fila por línea en `F_LPS`.

    `referencia` la escribe el operador cuando quiere fijar el texto de REFPRE;
    si no la pasa, se compone desde las líneas.

    Orden deliberado: cabecera primero, líneas después. Si la cabecera falla no
    hay nada que limpiar; si falla una línea, la proforma queda incompleta pero
    existe, que es preferible a un duplicado por reintento (ver
    `_write_quote_lines`).
    """
    _ = session
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
    written = _write_quote_lines(client, codpre, ejercicio, lines)
    logger.info("factusol: proforma creada CODPRE %s (cliente %s, %d/%d líneas)",
                codpre, customer.get("codcli"), written, len(lines))
    result = {"codpre": codpre, "ejercicio": ejercicio, "referencia": refpre,
              "lines": written, "total": payload["TOTPRE"]}
    if written < len(lines):
        result["warning"] = (
            f"La proforma {codpre} se creó con {written} de {len(lines)} líneas. "
            "Revísala en FACTUSOL antes de enviarla."
        )
    return result


def duplicate_quote(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
    fecha: str | None = None,
) -> dict[str, Any]:
    """Copia una proforma existente con CODPRE nuevo y fecha de hoy.

    Copia la fila entera de F_PRE (así arrastra cliente, importes y todas las
    columnas que no mapeamos) y sus líneas de F_LPS. Desde C-4-fix3 funciona con
    **cualquier** proforma, también las creadas en el FACTUSOL de escritorio:
    las líneas salen de F_LPS, no de una caché que solo tenía las del CRM.
    """
    _ = session
    if not str(codpre).strip().isdigit():
        raise FactusolError(f"CODPRE inválido: {codpre!r}")
    rows = client.load_table(
        TABLE_QUOTES, filtro=f"CODPRE={int(codpre)}", ejercicio=ejercicio,
    )
    if not rows:
        raise FactusolError(f"La proforma {codpre} no existe en el ejercicio {ejercicio}")

    lines = list_quote_lines(client, str(codpre), ejercicio=ejercicio)
    source = dict(rows[0])
    nuevo = next_codpre(client, ejercicio)
    source["CODPRE"] = nuevo
    source["FECPRE"] = fecha or datetime.now(UTC).date().isoformat()
    client.write_record(TABLE_QUOTES, source, ejercicio=ejercicio)

    written = _write_quote_lines(client, nuevo, ejercicio, lines)
    logger.info("factusol: proforma %s duplicada → %s (%d/%d líneas)",
                codpre, nuevo, written, len(lines))
    return {"codpre": nuevo, "source_codpre": str(codpre), "ejercicio": ejercicio,
            "lines": written}


def quote_lines_for_order(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
) -> dict[str, Any]:
    """Líneas de la proforma listas para volcarlas a un pedido.

    Desde C-4-fix3 salen de `F_LPS`, así que son las reales para cualquier
    proforma. El fallback de «una línea con el REFPRE y el total» desapareció:
    ya no hace falta reconstruir nada.

    Si F_LPS no devuelve nada (proforma sin líneas, edge case), se reconstruye
    una línea con la cabecera para no dejar el pedido vacío.
    """
    quote = get_quote(client, session, codpre, ejercicio=ejercicio)
    if quote is None:
        raise FactusolError(f"La proforma {codpre} no existe en el ejercicio {ejercicio}")
    lines = quote["lines"]
    if not lines:
        logger.warning("factusol: la proforma %s no tiene líneas en %s",
                       codpre, TABLE_QUOTE_LINES)
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

    C-4-fix3: las líneas ya son las reales de `F_LPS`, con su SKU, cantidad y
    precio. Antes, sin caché, el pedido salía con una única línea genérica.
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
            # C-4-fix3: con las líneas reales de F_LPS ya hay SKU que copiar.
            product_sku=str(line.get("codart") or "")[:128],
            product_codart=line.get("codart"),
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
