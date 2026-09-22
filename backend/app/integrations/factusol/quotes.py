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
from typing import Any

from sqlalchemy.orm import Session

from app.erp.language import country_numeric
from app.integrations.factusol.client import FactusolClient, FactusolError
from app.integrations.factusol.vat_regime import REGIME_NACIONAL, iva_pct_for

logger = logging.getLogger(__name__)


class QuoteNotEditableError(FactusolError):
    """La proforma no está en un estado que admita edición sin confirmar.

    Se distingue de `FactusolError` para que la API responda 409 (el operador
    puede reintentar con `force`) en vez de 502 (fallo de FACTUSOL)."""

    def __init__(self, message: str, *, estado: int | None = None):
        super().__init__(message)
        self.estado = estado

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

#: `TIPPRE` es la SERIE del presupuesto = empresa emisora (1 Bomedia ·
#: 2 MQ Europe · 4 Lambert · 5 Streamtec), la misma que `annotate_quotes` lee
#: para pintar el nº visible «serie-código» (5-000039). Vale '1' en los 653
#: presupuestos de Bomedia, y es lo que se escribe si nadie elige otra.
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
    # El email es CEMPRE. `EMAPRE` (F_CLI/F_ART) no existe en F_PRE: leerlo
    # devolvía None en silencio y escribirlo reventaba el registro entero.
    "CPOPRE", "CCPPRE", "CPRPRE", "CNIPRE", "TELPRE", "CEMPRE",
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

#: `TIPLPS` es la serie de la línea: SIEMPRE la misma que el `TIPPRE` de su
#: cabecera (F_LPS se identifica por `(TIPLPS, CODLPS, POSLPS)`). '1' es el
#: default, el de Bomedia.
DEFAULT_TIPLPS = "1"


def tip_of(serie: Any) -> str:
    """`TIPPRE`/`TIPLPS` a partir de la serie elegida. FACTUSOL los guarda como
    TEXTO ('1', '2'…), que es como los leen `annotate_quotes` y el explorador
    de documentos. Lo que no sea un número cae a la serie por defecto: validar
    qué series son legítimas es cosa del endpoint, no de la capa de escritura."""
    text = str(serie or "").strip()
    return text if text.isdigit() else DEFAULT_TIPPRE


def _same_serie(value: Any, serie: Any) -> bool:
    """¿La fila es de esa serie? `TIPPRE`/`TIPLPS` llegan como texto o como
    número según la fila, así que se comparan normalizados (por eso el filtro
    de serie se resuelve en Python y no en el SQL de la API)."""
    return tip_of(value) == tip_of(serie)


def rows_of_serie(
    rows: list[dict[str, Any]], serie: Any, column: str,
) -> list[dict[str, Any]]:
    """Las filas de la serie pedida. `serie=None` las devuelve todas, para los
    llamadores que aún no saben de qué serie es la proforma (enlaces antiguos
    sin `?serie=`)."""
    if serie is None:
        return list(rows)
    return [r for r in rows if _same_serie(r.get(column), serie)]


def _pick_quote_row(
    rows: list[dict[str, Any]], codpre: Any, serie: Any,
) -> dict[str, Any] | None:
    """La cabecera de la proforma (serie + número) entre las que comparten
    CODPRE.

    En FACTUSOL la clave de F_PRE es (`TIPPRE`, `CODPRE`) y cada serie lleva su
    propio contador, así que los números SE REPITEN entre series: en producción
    (ejercicio 2026) 69 de 692 CODPRE están en más de una serie — el 1 existe
    en la 1, la 2, la 3 y la 5. Leer por CODPRE a secas devolvía cualquiera de
    ellas.

    Sin serie (un enlace viejo) se acepta la única que haya; si hay varias se
    avisa y se coge la de la serie más baja, que es lo que daba antes quedarse
    con la primera fila."""
    candidatas = rows_of_serie(rows, serie, "TIPPRE")
    if not candidatas:
        return None
    if len(candidatas) > 1:
        series = sorted({tip_of(r.get("TIPPRE")) for r in candidatas})
        if serie is None:
            logger.warning(
                "factusol: la proforma %s existe en las series %s y no se ha "
                "dicho cuál: se usa la %s. Pásala para no jugársela.",
                codpre, ", ".join(series), series[0],
            )
        candidatas.sort(key=lambda r: tip_of(r.get("TIPPRE")))
    return dict(candidatas[0])


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
    # Fase 4 (pantalla Proformas): estado del presupuesto. `ESTPRE` se LEE
    # (la API devuelve la fila entera) pero no forma parte de `QUOTE_FIELDS`,
    # que es lo que se escribe.
    out["estpre"] = _int_or_none(row.get("ESTPRE"))
    out["estado"] = quote_estado(out["estpre"])
    out["estado_label"] = QUOTE_ESTADO_LABELS[out["estado"]]
    # Lote B3b: portes de la cabecera (banda `IPOR1PRE`, donde los deja tanto
    # la app Woo→FACTUSOL como `build_quote_payload`). Se LEE de la fila
    # completa igual que ESTPRE; al editar, el modal lo precarga para no
    # perderlos al reescribir la cabecera.
    out["portes"] = _num(row.get("IPOR1PRE"))
    return out


def _quote_matches(quote: dict[str, Any], needle: str) -> bool:
    """¿La proforma casa con el texto buscado? Mira en la referencia, el nombre
    del cliente de origen y el propio número — que es como Bart identifica una
    plantilla («la de Laboratorios Duaner», «la 512», «rotulación»)."""
    haystack = " ".join(str(x or "") for x in (
        quote.get("referencia"), quote.get("cliente_nombre"), quote.get("codpre"),
    ))
    return needle in haystack.casefold()


def _quote_sort_key(quote: dict[str, Any]) -> tuple[int, int]:
    """Orden de listado: por FECHA descendente y, a igualdad, por CODPRE.

    Ordenar por CODPRE a secas **starvaba las series distintas de la 1**: los
    contadores de FACTUSOL son POR SERIE (la 1 va por `526082` mientras la 5
    va por `000005`), así que un `ORDER BY CODPRE DESC` global pone toda la
    serie 1 delante y el recorte a `limit` se come las demás. Por fecha, las
    proformas recientes de cualquier serie entran por igual."""
    fecha = quote.get("fecha")
    dia = date.fromisoformat(fecha).toordinal() if fecha else 0
    codpre = quote.get("codpre")
    return (dia, int(codpre) if str(codpre or "").isdigit() else 0)


def list_quotes(
    client: FactusolClient, *, ejercicio: str, codcli: str | None = None,
    days_back: int = DEFAULT_DAYS_BACK, today: date | None = None,
    text: str | None = None, limit: int = QUOTE_LIST_LIMIT,
    serie: int | None = None,
) -> list[dict[str, Any]]:
    """Proformas de un cliente (o de TODOS si `codcli` es None) en los últimos
    `days_back` días, opcionalmente filtradas por `text` y por `serie`
    (empresa emisora; None = TODAS, como en Documentos).

    Se recorta en Python (la API no soporta LIMIT) **ordenando por fecha**, no
    por CODPRE: ver `_quote_sort_key`. El filtro de fecha se resuelve también
    en Python a propósito: el dialecto SQL de la API DELSOL no está documentado
    y una función de fecha no soportada devolvería `[]` en silencio (la trampa
    de C-3-fix1). Filtrar por `CLIPRE`, que es una comparación trivial, sí es
    seguro. La SERIE se filtra en Python por lo mismo (y porque `TIPPRE` viene
    como texto o número según la fila).

    `text` y `serie` se aplican **antes** del recorte a `limit`: si se truncase
    primero, buscar una plantilla antigua no la encontraría nunca porque las
    100 más recientes se la habrían comido.
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
    if serie is not None:
        from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

        quotes = [q for q in quotes if coerce_serie(q.get("tippre")) == serie]
    needle = (text or "").strip().casefold()
    if needle:
        quotes = [q for q in quotes if _quote_matches(q, needle)]
    quotes.sort(key=_quote_sort_key, reverse=True)
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
        "iva_pct": _iva_pct_from_line(row),
    }


#: Tipos de IVA que existen en España. Cualquier otro valor en `IVALPS` no
#: puede ser un porcentaje.
SPANISH_IVA_RATES = frozenset({0.0, 4.0, 10.0, 21.0})


def _iva_pct_from_line(row: dict[str, Any]) -> float:
    """% de IVA de una línea F_LPS, a prueba de que `IVALPS` sea un código.

    No está confirmado si `IVALPS` guarda el porcentaje o el código de tipo
    (0=general, 1=reducido…) — ver `build_quote_line_payload`. Mientras no se
    cierre, se acepta el valor solo si **puede** ser un porcentaje español; un
    1, 2 o 3 sería un IVA que no existe, así que casi seguro es un código y se
    cae al 21 % en vez de facturar «al 1 %».
    """
    value = _num(row.get("IVALPS"))
    if value and value in SPANISH_IVA_RATES:
        return value
    return DEFAULT_IVA_PCT


def list_quote_lines(
    client: FactusolClient, codpre: str, *, ejercicio: str, serie: Any = None,
) -> list[dict[str, Any]]:
    """Líneas REALES de una proforma, leídas de `F_LPS`.

    `F_LPS.CODLPS = F_PRE.CODPRE` (descubierto en C-4-fix3). Sustituye a
    `factusol_quote_lines_cache`, que era un apaño por haber buscado la tabla de
    líneas con el nombre equivocado. Funciona con **todas** las proformas,
    incluidas las creadas en el FACTUSOL de escritorio.

    La línea se identifica por `(TIPLPS, CODLPS, POSLPS)`: sin `serie` salían
    MEZCLADAS las de todas las proformas con ese número, que en producción son
    69 números repartidos entre varias series. El número se filtra en el SQL
    (comparación trivial, segura) y la serie en Python, por lo mismo que en
    `list_quotes`: `TIPLPS` viene como texto o como número según la fila.
    """
    if not str(codpre).strip().isdigit():
        return []
    rows = client.load_table(
        TABLE_QUOTE_LINES,
        filtro=f"CODLPS={int(codpre)} ORDER BY POSLPS",
        ejercicio=ejercicio,
    )
    return [_row_to_quote_line(r) for r in rows_of_serie(rows, serie, "TIPLPS")]


def skus_for_codarts(
    client: FactusolClient, codarts: list[str], *, ejercicio: str,
) -> dict[str, str]:
    """`{CODART interno: SKU comercial}` en UNA consulta a F_ART.

    Es el camino inverso de `resolve_codarts`: `F_LPS.ARTLPS` guarda el CODART
    interno (`00001`), pero el operativo reconoce el `EQUART` (`CDR80WPT`), que
    es lo que enseña y busca el autocomplete. Sin esta traducción, duplicar una
    proforma obligaba a volver a elegir cada artículo (Lote B4).

    Un artículo sin EQUART se identifica por su CODART, como en el buscador
    (`_row_to_article`). Los CODART que no estén en F_ART no aparecen en el
    dict.
    """
    wanted = [c for c in dict.fromkeys(str(x or "").strip() for x in codarts) if c]
    if not wanted:
        return {}
    in_list = ",".join(f"'{_sql_escape(c)}'" for c in wanted)
    rows = client.load_table(
        TABLE_ARTICLES, filtro=f"CODART IN ({in_list})", ejercicio=ejercicio,
    )
    out: dict[str, str] = {}
    for row in rows:
        codart = str(row.get("CODART") or "").strip()
        if codart:
            out[codart] = str(row.get("EQUART") or "").strip() or codart
    return out


def _attach_line_skus(
    client: FactusolClient, lines: list[dict[str, Any]], *, ejercicio: str,
) -> None:
    """Añade `sku` a cada línea: el comercial del artículo, o None en las de
    texto libre. Si F_ART falla se cae al CODART: una proforma se puede
    duplicar igual, solo que enseñando el código interno."""
    try:
        skus = skus_for_codarts(
            client, [line.get("codart") for line in lines], ejercicio=ejercicio,
        )
    except FactusolError:
        logger.warning("factusol: no se pudieron traducir los CODART a SKU",
                       exc_info=True)
        skus = {}
    for line in lines:
        codart = line.get("codart")
        line["sku"] = (skus.get(codart) or codart) if codart else None


def get_quote(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
    serie: Any = None,
) -> dict[str, Any] | None:
    """Una proforma con su desglose real de F_LPS. None si no existe.

    La proforma se identifica por (`serie`, `codpre`) — la clave real de F_PRE.
    Sin `serie` se acepta la única que haya con ese número (ver `_pick_quote_row`).

    Cada línea lleva `codart` (el interno de `ARTLPS`) y `sku` (el comercial,
    ver `skus_for_codarts`), para que el modal de duplicar muestre el artículo
    tal como lo reconoce el operativo sin volver a elegirlo.

    `session` ya no se usa para leer líneas (la caché local quedó obsoleta en
    C-4-fix3); se mantiene en la firma porque los llamadores la pasan y para no
    romper la API interna.
    """
    _ = session
    rows = client.load_table(
        TABLE_QUOTES, filtro=f"CODPRE={int(codpre)}", ejercicio=ejercicio,
    ) if str(codpre).strip().isdigit() else []
    row = _pick_quote_row(rows, codpre, serie)
    if row is None:
        return None
    quote = _row_to_quote(row)
    # Las líneas, de la MISMA serie que la cabecera que se ha resuelto.
    quote["lines"] = list_quote_lines(
        client, str(quote["codpre"]), ejercicio=ejercicio, serie=quote.get("tippre"),
    )
    _attach_line_skus(client, quote["lines"], ejercicio=ejercicio)
    quote["line_source"] = TABLE_QUOTE_LINES
    return quote


# --- escritura de proformas --------------------------------------------------


def next_codpre(
    client: FactusolClient, ejercicio: str, serie: Any = DEFAULT_TIPPRE,
) -> str:
    """Siguiente CODPRE **de esa serie** = max de la serie + 1.

    Cada serie de FACTUSOL lleva su propio contador (la 1 va por el 526.080 y
    la 5 por el 39), así que numerar con el máximo GLOBAL metía las proformas
    nuevas de las series pequeñas con un número altísimo, lejos de lo que el
    escritorio enseña. El máximo se calcula sobre las filas de la serie, que
    llegan ya filtradas en Python (`TIPPRE` viene como texto o número).

    Carrera con el escritorio: si alguien crea una proforma en FACTUSOL entre
    este `MAX+1` y la escritura, los dos cogerían el mismo número. Entre dos
    altas de BoHub lo evita el worker serializado (`factusol:writes`,
    concurrency=1); contra el escritorio no hay nada que lo impida, igual que
    en `next_codfac` / `next_codcli`. Es el riesgo que ya se asumía, ahora
    acotado a la serie en la que se está creando en vez de a toda la tabla.
    """
    rows = client.load_table(
        TABLE_QUOTES, filtro="1=1 ORDER BY CODPRE DESC", ejercicio=ejercicio,
    )
    de_la_serie = rows_of_serie(rows, serie, "TIPPRE")
    if not de_la_serie:
        return "1"
    mayor = max((_int_or_none(r.get("CODPRE")) or 0) for r in de_la_serie)
    return str(mayor + 1)


def _totals(
    lines: list[dict[str, Any]], *, regime: str | None = None,
    portes: float = 0.0,
) -> dict[str, float]:
    """Base, IVA y total del conjunto de líneas.

    `portes` (ERP · portes como línea aparte) son los gastos de envío del
    documento, que en FACTUSOL NO son una línea sino una banda de cabecera
    (`IPOR1*`, el mismo sitio donde los deja la app Woo→FACTUSOL). Suman a la
    base imponible (`BAS1 = NET1 + IPOR1`) y llevan el IVA de la banda, así
    que siguen el régimen del cliente como el resto del documento.

    Solo se usa la banda 1 de IVA (`NET1PRE`/`IIVA1PRE`): mezclar tipos en una
    misma proforma exigiría repartir en las bandas 2/3/4, y las proformas de
    Bomedia son de un solo tipo. Si llegan varios, se aplica el tipo de la
    primera línea al total y se deja constancia en el log.

    Tarea C: `regime` intracomunitario / exportación → IVA 0 en los importes,
    diga lo que diga la línea (era el bug del dinero: banda 1 al 21 % para
    cualquier cliente)."""
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
    if regime and regime != REGIME_NACIONAL and iva_pct:
        logger.info("factusol: régimen %s → IVA 0 en vez de %.2f %%", regime, iva_pct)
    iva_pct = iva_pct_for(regime, iva_pct)
    base = round(base, 2)
    portes = round(_num(portes), 2)
    # Base imponible de la banda = neto de líneas + portes (como FACTUSOL).
    imponible = round(base + portes, 2)
    iva = round(imponible * iva_pct / 100, 2)
    return {"base": base, "portes": portes, "imponible": imponible,
            "iva_pct": iva_pct, "iva": iva,
            "total": round(imponible + iva, 2)}


def header_says_no_iva(piva1: Any, base: Any) -> bool:
    """La cabecera de un documento lleva un 0 % EXPLÍCITO en la banda 1
    (`PIVA1PRE`/`PIVA1PCL` presente y a 0) con base > 0: proforma / pedido
    intracomunitario o de exportación. `None` (columna ausente) NO es un 0 %."""
    if piva1 is None or str(piva1).strip() == "":
        return False
    return _num(piva1) == 0 and _num(base) > 0


def _cpapre(pais: Any) -> str:
    """`CPAPRE` (ISO numérico) desde lo que traiga el cliente: ISO2 de la
    empresa CRM («BE»), el `PAICLI` de una dirección alternativa («056») o un
    nombre («ESPAÑA» en `APAxCLI`). Lo que no se reconoce cae a España, como
    hasta ahora."""
    value = str(pais or "").strip()
    if value.isdigit() and len(value) == 3:
        return value
    return (country_numeric(value) if value else None) or DEFAULT_CPAPRE


def build_quote_payload(
    codpre: str, *, ejercicio: str, customer: dict[str, Any],
    refpre: str, lines: list[dict[str, Any]], fecha: str | None = None,
    fopfac: str | None = None, portes: float = 0.0,
    serie: Any = DEFAULT_TIPPRE,
) -> dict[str, Any]:
    """Registro F_PRE listo para `EscribirRegistro`.

    `serie` es la EMPRESA EMISORA y va a `TIPPRE`. Por defecto 1 (Bomedia),
    que es lo que se escribía siempre hasta ahora.

    Solo columnas verificadas contra la base real. Las que no ponemos las deja
    FACTUSOL con sus defaults — no inventamos valores (la lección de C-3-fix1).

    `portes` > 0 añade la banda de portes (`IPOR1PRE`) y la base imponible
    (`BAS1PRE = NET1PRE + IPOR1PRE`), que es donde viven los gastos de envío
    de los documentos web. Con `portes=0` (el caso de siempre) el registro
    sale EXACTAMENTE igual que antes: ninguna columna nueva.
    """
    totals = _totals(lines, regime=customer.get("regime"), portes=portes)
    payload: dict[str, Any] = {
        "CODPRE": codpre,
        "TIPPRE": tip_of(serie),
        "FECPRE": fecha or datetime.now(UTC).date().isoformat(),
        "CLIPRE": str(customer.get("codcli") or ""),
        "CNOPRE": str(customer.get("nombre") or "")[:255],
        "CDOPRE": str(customer.get("direccion") or "")[:255],
        "CPOPRE": str(customer.get("ciudad") or "")[:255],
        "CCPPRE": str(customer.get("cp") or "")[:20],
        "CPRPRE": str(customer.get("provincia") or "")[:255],
        "CNIPRE": str(customer.get("nif") or "")[:64],
        # País REAL del cliente (ISO numérico): el de la empresa CRM o el de la
        # dirección alternativa elegida; España solo si no hay ninguno.
        "CPAPRE": _cpapre(customer.get("pais")),
        "ALMPRE": DEFAULT_ALMPRE,
        "NET1PRE": totals["base"],
        "PIVA1PRE": totals["iva_pct"],
        "IIVA1PRE": totals["iva"],
        "TOTPRE": totals["total"],
    }
    if totals["portes"]:
        # Portes en la banda 1, la del IVA del documento: así siguen el
        # régimen del cliente y el PDF los pinta como los de los pedidos web.
        payload["IPOR1PRE"] = totals["portes"]
        payload["BAS1PRE"] = totals["imponible"]
    # REFPRE = «Su ref.» del documento. Solo se escribe si el operador la
    # teclea. C-4 la auto-rellenaba con un resumen de las líneas
    # («1x UV INK; 1x test»), que desde C-4-fix3 es ruido duplicado: el
    # desglose de verdad vive en F_LPS y el escritorio lo muestra desde ahí.
    if refpre:
        payload["REFPRE"] = refpre[:REFPRE_MAX_LENGTH]
    if customer.get("telefono"):
        payload["TELPRE"] = str(customer["telefono"])[:40]
    if customer.get("email"):
        # ⚠️ El email de F_PRE es CEMPRE, **no** EMAPRE. `EMAPRE` existe en
        # F_CLI y F_ART, pero no en F_PRE: escribirlo hace fallar TODO el
        # EscribirRegistro con BDEscribirRegistroError, y con él la creación de
        # cualquier proforma con email (C-4-fix4). Verificado en la proforma
        # real 574: CEMPRE='direccio@fidelroca.cat'.
        payload["CEMPRE"] = str(customer["email"])[:255]
    if fopfac:
        payload["FOPPRE"] = str(fopfac)
    return payload


def build_quote_line_payload(
    codpre: str, position: int, line: dict[str, Any],
    serie: Any = DEFAULT_TIPLPS,
) -> dict[str, Any]:
    """Una línea del CRM → registro `F_LPS` listo para `EscribirRegistro`.

    `serie` es la de su cabecera: `TIPLPS` tiene que ir siempre igual que el
    `TIPPRE` del presupuesto al que pertenece la línea.

    Solo columnas verificadas. Las medidas (`ALTLPS`/`ANCLPS`/`FONLPS`) y
    `MEMLPS` se dejan a FACTUSOL: no inventamos valores."""
    qty = _num(line.get("quantity"), 1.0)
    price = _num(line.get("unit_price"))
    discount = _num(line.get("discount_pct"))
    # ⚠️ IVALPS NO se escribe (C-4-fix5). No está confirmado si guarda el
    # porcentaje o el CÓDIGO de tipo de IVA (0=general, 1=reducido, …), y la
    # evidencia apunta a lo segundo: la proforma 574, que abre bien en el
    # escritorio, tiene IVALPS=0 en todas sus líneas — un 0 % de IVA no tiene
    # sentido en un presupuesto español, un código «tipo general» sí. La 4350,
    # que crashea, llevaba IVALPS=21.
    # Omitir la columna deja que FACTUSOL ponga su default, que es exactamente
    # el valor que tienen las proformas que funcionan. El IVA de verdad viaja
    # en la cabecera (PIVA1PRE/IIVA1PRE), así que los totales salen bien igual.
    # Para cerrarlo: SELECT DISTINCT IVALPS FROM F_LPS (ver el script de
    # descubrimiento). Si son 0/1/2 → es código y hay que traducir.
    return {
        "TIPLPS": tip_of(serie),
        "CODLPS": codpre,
        "POSLPS": position,
        "ARTLPS": str(line.get("codart") or "")[:64],
        "DESLPS": str(line.get("description") or "")[:255],
        "CANLPS": qty,
        "DT1LPS": discount,
        "PRELPS": price,
        "TOTLPS": round(qty * price * (1 - discount / 100), 2),
    }


def resolve_codarts(
    client: FactusolClient, skus: list[str], *, ejercicio: str,
) -> dict[str, str]:
    """`{sku_recibido: CODART interno}` para los SKU que existan en F_ART.

    **Por qué hace falta** (C-4-fix5): el autocomplete muestra `EQUART`, el
    código comercial (`Ink500mlCY`), porque es el que el operativo reconoce —
    y el frontend lo devuelve como `codart`. Pero `F_LPS.ARTLPS` tiene que
    llevar el **CODART interno** (`99cy`): FACTUSOL de escritorio busca el
    artículo por CODART y, si no lo encuentra, **crashea** al abrir la proforma
    («UPSS! Excepción no controlada»). Le pasó a la 4350.

    Una sola consulta para toda la proforma: `load_table` devuelve la fila
    completa, así que un `CODART IN (…) OR EQUART IN (…)` basta para construir
    el mapa en los dos sentidos. Los SKU que no casen no aparecen en el dict —
    el llamador los convierte en línea de texto libre.
    """
    wanted = [s for s in dict.fromkeys(str(x or "").strip() for x in skus) if s]
    if not wanted:
        return {}
    in_list = ",".join(f"'{_sql_escape(s)}'" for s in wanted)
    rows = client.load_table(
        TABLE_ARTICLES,
        filtro=f"CODART IN ({in_list}) OR EQUART IN ({in_list})",
        ejercicio=ejercicio,
    )
    by_codart: dict[str, str] = {}
    by_equart: dict[str, str] = {}
    for row in rows:
        codart = str(row.get("CODART") or "").strip()
        if not codart:
            continue
        by_codart[codart] = codart
        equart = str(row.get("EQUART") or "").strip()
        if equart:
            by_equart[equart] = codart
    # El CODART directo manda: si un EQUART coincidiera con el CODART de otro
    # artículo, quedarse con el match exacto es lo correcto.
    return {sku: by_codart.get(sku) or by_equart[sku]
            for sku in wanted if sku in by_codart or sku in by_equart}


def _write_quote_lines(
    client: FactusolClient, codpre: str, ejercicio: str,
    lines: list[dict[str, Any]], serie: Any = DEFAULT_TIPLPS,
) -> int:
    """Escribe las líneas en F_LPS. Devuelve cuántas se escribieron.

    `serie` es la de la cabecera: las líneas llevan el mismo `TIPLPS` que el
    `TIPPRE` de su presupuesto.

    Los SKU se traducen antes a CODART interno (ver `resolve_codarts`): escribir
    un EQUART en `ARTLPS` hace que el FACTUSOL de escritorio crashee al abrir la
    proforma.

    Si una línea falla NO se propaga: la cabecera F_PRE ya existe, y hacer
    fallar el job llevaría al operador a reintentar y crear una proforma
    **duplicada** en la contabilidad. Se deja constancia en el log y el job
    devuelve el recuento real para que la UI avise. Misma política que la
    caché en C-4, ahora sobre la tabla buena.
    """
    try:
        codarts = resolve_codarts(
            client, [line.get("codart") for line in lines], ejercicio=ejercicio,
        )
    except FactusolError:
        # Sin traducción es mejor no arriesgarse: se escriben las líneas como
        # texto libre (ARTLPS='') antes que con un código que rompa la proforma.
        logger.error("factusol: no se pudo resolver los CODART de la proforma "
                     "%s; las líneas irán sin artículo", codpre, exc_info=True)
        codarts = {}

    written = 0
    for i, line in enumerate(lines, start=1):
        sku = str(line.get("codart") or "").strip()
        codart = codarts.get(sku, "")
        if sku and not codart:
            logger.warning(
                "factusol: el SKU %r de la proforma %s no casa con ningún "
                "CODART ni EQUART de F_ART; la línea %d va como texto libre",
                sku, codpre, i,
            )
        elif codart and codart != sku:
            logger.info("factusol: SKU %r → CODART %r (vía EQUART)", sku, codart)
        payload = build_quote_line_payload(
            codpre, i, {**line, "codart": codart}, serie,
        )
        try:
            client.write_record(TABLE_QUOTE_LINES, payload, ejercicio=ejercicio)
            written += 1
        except FactusolError as exc:
            # ERROR, no warning: la proforma queda coja en la contabilidad y
            # alguien tiene que verlo. Se incluye el mensaje de FACTUSOL y las
            # columnas enviadas — con un `BDEscribirRegistroError` la causa
            # suele ser un nombre de columna mal puesto, y sin esto no hay por
            # dónde empezar (así se perdió el diagnóstico de la proforma 4349).
            logger.error(
                "factusol: proforma %s creada pero falló la línea POSLPS=%d "
                "(%d de %d escritas). Error: %s. Columnas enviadas: %s",
                codpre, i, written, len(lines), exc, ", ".join(payload),
                exc_info=True,
            )
            break
    return written


def create_quote(
    client: FactusolClient, session: Session, *, ejercicio: str,
    customer: dict[str, Any], lines: list[dict[str, Any]],
    referencia: str | None = None, fecha: str | None = None,
    fopfac: str | None = None, portes: float = 0.0,
    serie: Any = DEFAULT_TIPPRE,
) -> dict[str, Any]:
    """Crea la proforma: cabecera en `F_PRE` + una fila por línea en `F_LPS`.

    `serie` es la EMPRESA EMISORA del documento (`TIPPRE` en la cabecera y
    `TIPLPS` en las líneas). Por defecto 1 (Bomedia). El CODPRE sale del
    contador de ESA serie (`next_codpre`), como en el escritorio.

    `referencia` la escribe el operador cuando quiere fijar el texto de REFPRE;
    si no la pasa, se compone desde las líneas.

    `portes` (Lote B3b) van a la banda de portes de la cabecera
    (`IPOR1PRE`/`BAS1PRE`, ver `build_quote_payload`), igual que en el albarán
    manual y en los pedidos web — NO como línea de F_LPS. Con 0 (lo habitual)
    el registro sale exactamente como hasta ahora.

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

    # Sin referencia explícita, REFPRE se queda vacío (C-4-fix5): el desglose
    # está en F_LPS y repetirlo resumido en «Su ref.» solo ensucia el documento.
    refpre = (referencia or "").strip()
    codpre = next_codpre(client, ejercicio, serie)
    payload = build_quote_payload(
        codpre, ejercicio=ejercicio, customer=customer, refpre=refpre,
        lines=lines, fecha=fecha, fopfac=fopfac, portes=portes, serie=serie,
    )
    try:
        client.write_record(TABLE_QUOTES, payload, ejercicio=ejercicio)
    except FactusolError as exc:
        # El error se re-lanza (el job DEBE fallar: no hay proforma), pero antes
        # se deja en el log qué columnas se enviaron. Un
        # `BDEscribirRegistroError` aquí suele ser un nombre de columna que no
        # existe, y sin esta traza hay que bisecar el payload a mano contra
        # producción — que es lo que costó encontrar el EMAPRE de C-4-fix4.
        logger.error(
            "factusol: falló la cabecera de la proforma %s. Error: %s. "
            "Columnas enviadas: %s", codpre, exc, ", ".join(payload),
        )
        raise
    written = _write_quote_lines(client, codpre, ejercicio, lines, payload["TIPPRE"])
    logger.info(
        "factusol: proforma creada CODPRE %s serie %s (cliente %s, %d/%d líneas)",
        codpre, payload["TIPPRE"], customer.get("codcli"), written, len(lines),
    )
    result = {"codpre": codpre, "ejercicio": ejercicio, "referencia": refpre,
              "serie": int(payload["TIPPRE"]),
              "lines": written, "total": payload["TOTPRE"]}
    if written < len(lines):
        result["warning"] = (
            f"La proforma {codpre} se creó con {written} de {len(lines)} líneas. "
            "Revísala en FACTUSOL antes de enviarla."
        )
    return result


#: `ESTPRE` = estado del presupuesto. **Solo hay un valor confirmado**: la
#: proforma 574 tiene `ESTPRE=1` y el escritorio la muestra como «Aceptado».
#: El resto del mapeo (¿2=rechazado? ¿3=facturado?) está sin verificar.
#:
#: Por eso el guard NO se apoya en adivinar cada valor: se apoya en que **0 es
#: el estado inicial** —el default con el que FACTUSOL crea las filas, igual que
#: `IVALPS=0` o `TIPPRE='1'`— y cualquier otro valor significa que al documento
#: le ha pasado algo. Con eso, equivocarse solo puede pedir una confirmación de
#: más, nunca sobrescribir en silencio una proforma ya aceptada o facturada.
#:
#: Para cerrarlo: `SELECT DISTINCT ESTPRE, COUNT(*) FROM F_PRE GROUP BY ESTPRE`
#: (lo hace el script de descubrimiento).
ESTPRE_PENDING = 0
ESTPRE_ACCEPTED = 1
#: «Rechazado» en el escritorio de FACTUSOL (enumeración Pendiente / Aceptado /
#: Rechazado). NO está confirmado con un volcado: si `SELECT DISTINCT ESTPRE`
#: dice otra cosa, se ajusta aquí. Cualquier valor no reconocido queda como
#: `otro` (nunca se inventa un estado).
ESTPRE_REJECTED = 2
#: Etiquetas de los estados que sí conocemos, para el mensaje al operador.
ESTPRE_LABELS = {0: "pendiente", 1: "aceptada", ESTPRE_REJECTED: "rechazada"}

#: Estado de la proforma tal como lo enseña la pantalla Proformas (Fase 4).
QUOTE_ESTADO_PENDIENTE = "pendiente"
QUOTE_ESTADO_ACEPTADA = "aceptada"
QUOTE_ESTADO_RECHAZADA = "rechazada"
QUOTE_ESTADO_OTRO = "otro"
QUOTE_ESTADO_LABELS: dict[str, str] = {
    QUOTE_ESTADO_PENDIENTE: "pendiente",
    QUOTE_ESTADO_ACEPTADA: "aceptada",
    QUOTE_ESTADO_RECHAZADA: "rechazada",
    QUOTE_ESTADO_OTRO: "otro estado",
}


def quote_estado(estpre: int | None) -> str:
    """`ESTPRE` → estado legible. Sin valor (o 0) = pendiente, que es el
    estado inicial con el que FACTUSOL crea las filas."""
    if estpre is None or estpre == ESTPRE_PENDING:
        return QUOTE_ESTADO_PENDIENTE
    if estpre == ESTPRE_ACCEPTED:
        return QUOTE_ESTADO_ACEPTADA
    if estpre == ESTPRE_REJECTED:
        return QUOTE_ESTADO_RECHAZADA
    return QUOTE_ESTADO_OTRO


def _quote_row(
    client: FactusolClient, codpre: str, *, ejercicio: str, serie: Any = None,
) -> dict[str, Any] | None:
    """Fila F_PRE completa de la proforma (serie + número), o None si no
    existe. Sin `serie`, ver `_pick_quote_row`."""
    if not str(codpre).strip().isdigit():
        return None
    rows = client.load_table(
        TABLE_QUOTES, filtro=f"CODPRE={int(codpre)}", ejercicio=ejercicio,
    )
    return _pick_quote_row(rows, codpre, serie)


def _estado_of(row: dict[str, Any]) -> int:
    return _int_or_none(row.get("ESTPRE")) or ESTPRE_PENDING


def quote_state(
    client: FactusolClient, codpre: str, *, ejercicio: str, serie: Any = None,
) -> int | None:
    """`ESTPRE` de la proforma (serie + número), o None si no existe."""
    row = _quote_row(client, codpre, ejercicio=ejercicio, serie=serie)
    return None if row is None else _estado_of(row)


def update_quote(
    client: FactusolClient, codpre: str, *, ejercicio: str,
    customer: dict[str, Any], lines: list[dict[str, Any]],
    referencia: str | None = None, force: bool = False,
    portes: float = 0.0, serie: Any = None,
) -> dict[str, Any]:
    """Reescribe una proforma: cabecera con `ActualizarRegistro` y líneas
    borradas + vueltas a escribir.

    Las líneas se reemplazan enteras en vez de intentar un diff: `F_LPS` se
    identifica por `(TIPLPS, CODLPS, POSLPS)`, así que un diff tendría que
    casar posiciones y acabaría reescribiéndolas casi siempre. Borrar y
    reescribir es más simple y deja el mismo resultado.

    `portes` (Lote B3b): banda `IPOR1PRE`/`BAS1PRE` como en el alta. Si la
    proforma TENÍA portes y el operador los quita, la banda se pone a 0
    explícitamente — `ActualizarRegistro` solo toca las columnas que se
    envían, y sin esto los portes viejos se quedarían en la cabecera con los
    totales nuevos. Solo se escribe el 0 cuando la fila tenía valor: en una
    proforma que nunca los tuvo el registro sale como hasta ahora.

    `serie` IDENTIFICA qué proforma se edita (la clave de F_PRE es serie +
    número), no la cambia: la nueva cabecera conserva el `TIPPRE` de la fila y
    las líneas se reescriben con ese mismo `TIPLPS`.

    ⚠️ El borrado de líneas lleva la serie en el filtro. Con `CODLPS={n}` a
    secas se llevaba por delante las líneas de TODAS las proformas con ese
    número: editar la 574 de Bomedia vaciaba la 574 de MQ Europe. Es la misma
    lección que ya estaba escrita para las facturas en `service.py` («el filtro
    DEBE incluir el tipo»), que aquí no se había aplicado.

    `force` salta el guard de estado (ver `ESTPRE_PENDING`).
    """
    row = _quote_row(client, codpre, ejercicio=ejercicio, serie=serie)
    if row is None:
        raise FactusolError(
            f"La proforma {codpre} no existe en el ejercicio {ejercicio}"
        )
    estado = _estado_of(row)
    if estado != ESTPRE_PENDING and not force:
        etiqueta = ESTPRE_LABELS.get(estado, f"en estado {estado}")
        raise QuoteNotEditableError(
            f"La proforma {codpre} está {etiqueta} en FACTUSOL. Modificarla "
            "cambia un documento que el cliente ya puede haber recibido.",
            estado=estado,
        )

    propia = tip_of(row.get("TIPPRE"))
    header = build_quote_payload(
        str(codpre), ejercicio=ejercicio, customer=customer,
        refpre=(referencia or "").strip(), lines=lines, portes=portes,
        serie=propia,
    )
    if "IPOR1PRE" not in header and _num(row.get("IPOR1PRE")):
        header["IPOR1PRE"] = 0.0
        header["BAS1PRE"] = header["NET1PRE"]
    # En un UPDATE no se tocan ni el código ni la fecha de creación: el primero
    # es la clave y la segunda es cuándo nació el documento, no cuándo se editó.
    header.pop("FECPRE", None)
    client.update_record(TABLE_QUOTES, header, ejercicio=ejercicio)

    # La clave de la línea es (TIPLPS, CODLPS, POSLPS): el filtro lleva las dos
    # columnas, con el mismo formato entrecomillado que ya usan el borrado de
    # facturas y el de la cadena de documentos.
    client.delete_records(
        TABLE_QUOTE_LINES,
        f"TIPLPS='{propia}' AND CODLPS='{int(codpre)}'", ejercicio=ejercicio,
    )
    written = _write_quote_lines(client, str(codpre), ejercicio, lines, propia)
    logger.info("factusol: proforma %s-%s actualizada (%d/%d líneas, estado %s)",
                propia, codpre, written, len(lines), estado)
    result = {"codpre": str(codpre), "ejercicio": ejercicio,
              "serie": int(propia), "updated": True,
              "lines": written, "estado": estado}
    if written < len(lines):
        result["warning"] = (
            f"La proforma {codpre} se guardó con {written} de {len(lines)} "
            "líneas. Revísala en FACTUSOL."
        )
    return result


def duplicate_quote(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
    fecha: str | None = None, serie: Any = None,
) -> dict[str, Any]:
    """Copia una proforma existente con CODPRE nuevo y fecha de hoy.

    Copia la fila entera de F_PRE (así arrastra cliente, importes y todas las
    columnas que no mapeamos) y sus líneas de F_LPS. Desde C-4-fix3 funciona con
    **cualquier** proforma, también las creadas en el FACTUSOL de escritorio:
    las líneas salen de F_LPS, no de una caché que solo tenía las del CRM.

    El original se identifica por (`serie`, `codpre`), y la copia se queda en la
    MISMA serie: la cabecera arrastra su `TIPPRE` (viene en la fila), el número
    nuevo sale del contador de esa serie y las líneas se escriben con ese mismo
    `TIPLPS` — antes iban con el default '1' aunque la cabecera fuera de otra.
    """
    _ = session
    if not str(codpre).strip().isdigit():
        raise FactusolError(f"CODPRE inválido: {codpre!r}")
    rows = client.load_table(
        TABLE_QUOTES, filtro=f"CODPRE={int(codpre)}", ejercicio=ejercicio,
    )
    source = _pick_quote_row(rows, codpre, serie)
    if source is None:
        raise FactusolError(f"La proforma {codpre} no existe en el ejercicio {ejercicio}")

    propia = tip_of(source.get("TIPPRE"))
    lines = list_quote_lines(client, str(codpre), ejercicio=ejercicio, serie=propia)
    nuevo = next_codpre(client, ejercicio, propia)
    source["CODPRE"] = nuevo
    source["FECPRE"] = fecha or datetime.now(UTC).date().isoformat()
    client.write_record(TABLE_QUOTES, source, ejercicio=ejercicio)

    written = _write_quote_lines(client, nuevo, ejercicio, lines, propia)
    logger.info("factusol: proforma %s-%s duplicada → %s-%s (%d/%d líneas)",
                propia, codpre, propia, nuevo, written, len(lines))
    return {"codpre": nuevo, "source_codpre": str(codpre), "ejercicio": ejercicio,
            "serie": int(propia), "lines": written}


def quote_lines_for_order(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
    serie: Any = None,
) -> dict[str, Any]:
    """Líneas de la proforma listas para volcarlas a un pedido.

    Desde C-4-fix3 salen de `F_LPS`, así que son las reales para cualquier
    proforma. El fallback de «una línea con el REFPRE y el total» desapareció:
    ya no hace falta reconstruir nada.

    Si F_LPS no devuelve nada (proforma sin líneas, edge case), se reconstruye
    una línea con la cabecera para no dejar el pedido vacío.
    """
    quote = get_quote(client, session, codpre, ejercicio=ejercicio, serie=serie)
    if quote is None:
        raise FactusolError(f"La proforma {codpre} no existe en el ejercicio {ejercicio}")
    lines = quote["lines"]
    # Tarea C: una proforma SIN IVA (intracomunitario / exportación, hecha en
    # el escritorio o por BoHub) lleva `PIVA1PRE=0` en la cabecera; las líneas
    # F_LPS no dicen nada fiable (`IVALPS` es un código) y caerían al 21 %.
    # La cabecera manda: las líneas del pedido salen al 0 %.
    if lines and header_says_no_iva(quote.get("piva1pre"), quote.get("base")):
        lines = [{**line, "iva_pct": 0.0, "iva_explicit": True} for line in lines]
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
        # La serie REAL de la proforma que se ha leído, para que quien convierta
        # no tenga que volver a adivinarla.
        "serie": int(tip_of(quote.get("tippre"))),
        "ejercicio": ejercicio,
        "line_source": quote["line_source"],
        "lines": lines,
        "total": quote["total"],
        "referencia": quote["referencia"],
        "clipre": quote["clipre"],
    }


def convert_quote_to_order(
    client: FactusolClient, session: Session, codpre: str, *, ejercicio: str,
    actor_user_id: str | None = None, serie: Any = None,
) -> dict[str, Any]:
    """Convierte la proforma en un pedido de BoHub (`Order`).

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

    La proforma se identifica por (`serie`, `codpre`), y la SERIE REAL —no un
    '1' fijo como antes— es la que viaja al `external_id`, al nº de pedido y al
    bloque `factusol_source`. Convertir la 574 de MQ Europe creaba un pedido que
    decía ser de la 574 de Bomedia, y la segunda conversión de cualquiera de las
    dos devolvía el pedido de la otra.

    Fase 1: el pedido queda marcado con origen `factusol_proforma` y
    `external_id` = la clave de `external_id_for` (el CODPRE a secas en la serie
    1, `serie-código` en las demás; la columna «Proforma» del seguimiento lo
    enseña), nº `PRO-nnnnnn`, y la conversión es IDEMPOTENTE: la segunda vez
    devuelve el pedido ya creado (`already_existed`) en vez de duplicarlo.
    Comparte el constructor con el alta desde documento
    (`app.erp.orders_from_factusol`).
    """
    from app.erp.models import OrderSource  # noqa: PLC0415
    from app.erp.orders_from_factusol import (  # noqa: PLC0415
        build_order,
        external_id_for,
        factusol_source_block,
        find_quote_order,
        order_number_for,
        resolve_company_id,
        visible_number,
    )

    data = quote_lines_for_order(client, session, codpre, ejercicio=ejercicio,
                                 serie=serie)
    codpre = data["codpre"]
    serie = int(data["serie"])
    external_id = external_id_for("presupuestos", serie, int(codpre))
    # Idempotencia por (serie, número), aceptando la clave histórica de las
    # proformas convertidas antes de este fix (ver `find_quote_order`).
    existing = find_quote_order(session, serie, int(codpre))
    if existing is not None:
        logger.info("factusol: la proforma %s-%s ya era el pedido %s (no se duplica)",
                    serie, codpre, existing.order_number)
        return {
            "order_id": existing.id, "order_number": existing.order_number,
            "codpre": codpre, "serie": serie, "lines": len(data["lines"]),
            "total": float(existing.total_amount or 0),
            "company_id": existing.company_id, "already_existed": True,
        }

    # El cliente del pedido se resuelve por el vínculo CRM ↔ CODCLI que ya
    # mantiene C-3. Sin vínculo el pedido se crea igualmente (sin empresa) y el
    # operador la asigna: es preferible a perder la conversión.
    company_id = resolve_company_id(session, data["clipre"])
    referencia = data["referencia"]
    order = build_order(
        session,
        source=OrderSource.FACTUSOL_PROFORMA,
        external_id=external_id,
        order_number=order_number_for("presupuestos", serie, int(codpre)),
        company_id=company_id,
        contact_id=None,
        placed_at=datetime.now(UTC),
        lines=data["lines"],
        notes=f"Creado desde la proforma FACTUSOL {visible_number(serie, int(codpre))}"
              + (f" · ref. {referencia}" if referencia else ""),
        packing_extra={"factusol_source": factusol_source_block(
            doc_type="presupuestos", serie=serie, codigo=int(codpre),
            referencia=referencia, forma_pago=None, forma_pago_nombre=None,
            cliente_codigo=data["clipre"], total=data["total"],
        )},
        actor_user_id=actor_user_id,
        history_reason=(
            "Pedido creado desde la proforma FACTUSOL "
            f"{visible_number(serie, int(codpre))}"
        ),
        total_with_tax=data["total"],
    )
    session.commit()
    logger.info("factusol: proforma %s-%s → pedido %s (%d líneas, %.2f €)",
                serie, codpre, order.order_number, len(data["lines"]),
                float(order.total_amount))
    return {
        "order_id": order.id, "order_number": order.order_number,
        "codpre": codpre, "serie": serie, "lines": len(data["lines"]),
        "total": float(order.total_amount), "company_id": company_id,
        "already_existed": False,
    }