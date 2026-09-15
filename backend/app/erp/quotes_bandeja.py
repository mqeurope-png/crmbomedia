"""Pantalla Proformas (rediseño de flujo, Fase 4): lo que cada proforma
necesita para la bandeja de proformas, encima del listado F_PRE.

Por proforma se añade:
- `estado` / `estado_label` (ya vienen de `quotes._row_to_quote`, por `ESTPRE`).
- `order`: el pedido de BoHub si la proforma ya se convirtió (origen
  `factusol_proforma`, `external_id` = CODPRE) → cola «convertidas».
- `queue` / `queue_label`: por `workflow.quote_queue` (mismo criterio que la
  bandeja de pedidos: la cola dice lo que toca).
- `company`: la empresa CRM vinculada al CLIPRE (id, nombre, país) si existe.

Y, para el explorador de documentos (Fase 5 / Lote 2 · PR-2),
`annotate_documents_crm`: el mismo cruce sobre los cuatro tipos, con el
pedido de BoHub ligado a cada documento (importado desde él, o el que lleva
vinculado este albarán / esta factura).
- `regime` / `regime_label` / `country_iso2`: el régimen de IVA de la
  empresa (país + NIF-IVA + VIES, `workflow.company_regime`); sin empresa
  vinculada se lee la cabecera del documento (0 % explícito → exento, sin
  poder distinguir intracomunitario de exportación → `regime=None` con
  `exento=True`).

Y para la pantalla: `queue_counts` (todas las proformas, aunque se filtre
por cola) y `estpre_values` (qué valores de `ESTPRE` hay realmente: es la
comprobación pendiente del mapeo «rechazada»).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.erp.language import normalize_country
from app.erp.models import Order, OrderSource
from app.erp.workflow import QUOTE_QUEUE_LABELS, QUOTE_QUEUES, company_regime, quote_queue
from app.integrations.factusol.documents import visible_number
from app.integrations.factusol.quotes import header_says_no_iva
from app.integrations.factusol.vat_regime import REGIME_LABELS
from app.models.crm import Company

#: Serie (`TIPPRE`) = empresa emisora. Los nombres se leen de los ajustes
#: (`series_names`, `/erp/settings`); esto es el fallback.
DEFAULT_SERIE_NAMES: dict[int, str] = {1: "Bomedia", 2: "MQ Europe", 4: "Lambert", 5: "Streamtec"}


def _serie_of(value: Any) -> int | None:
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _serie_names(session: Session) -> dict[int, str]:
    try:
        from app.integrations.factusol.service import series_names  # noqa: PLC0415

        return {**DEFAULT_SERIE_NAMES, **series_names(session)}
    except Exception:  # noqa: BLE001 — sin ajustes no se cae la pantalla
        return dict(DEFAULT_SERIE_NAMES)


def _companies_by_codcli(session: Session, codclis: set[str]) -> dict[str, Company]:
    """Empresas CRM vinculadas a esos CODCLI. `'0055'` y `'55'` son el mismo
    cliente según cómo viaje por la API: se indexan las dos formas."""
    keys: set[str] = set()
    for code in codclis:
        code = str(code or "").strip()
        if not code:
            continue
        keys.add(code)
        if code.isdigit():
            keys.add(str(int(code)))
    if not keys:
        return {}
    rows = session.scalars(
        select(Company).where(Company.factusol_company_id.in_(sorted(keys)))
    ).all()
    out: dict[str, Company] = {}
    for company in rows:
        code = str(company.factusol_company_id or "").strip()
        out[code] = company
        if code.isdigit():
            out[str(int(code))] = company
    return out


def _orders_by_codpre(session: Session, codpres: set[str]) -> dict[str, Order]:
    """Pedidos de BoHub creados desde esas proformas (`external_id` = CODPRE)."""
    if not codpres:
        return {}
    rows = session.scalars(
        select(Order).where(
            Order.external_source == OrderSource.FACTUSOL_PROFORMA,
            or_(Order.external_id.in_(sorted(codpres))),
        )
    ).all()
    return {str(o.external_id): o for o in rows if o.external_id}


def document_order_key(doc_type: str, doc: dict[str, Any]) -> str | None:
    """Clave con la que `_orders_by_document` indexa el pedido de cada
    documento: el `external_id` de importación en presupuestos / pedidos de
    cliente, y el nº visible `serie-código` en albaranes y facturas (que es
    lo que guarda el pedido en `factusol_albaran_number` / lo que compone
    `factusol_invoice_serie` + `factusol_invoice_number`). `None` si el
    documento no tiene clave utilizable."""
    from app.erp.orders_from_factusol import (  # noqa: PLC0415
        SOURCE_BY_DOC_TYPE,
        external_id_for,
    )

    codigo = doc.get("codigo")
    if not isinstance(codigo, int):
        return None
    if doc_type in SOURCE_BY_DOC_TYPE:
        try:
            return external_id_for(doc_type, int(doc.get("serie") or 0), codigo)
        except (TypeError, ValueError):
            return None
    if doc_type in ("albaranes", "facturas"):
        if doc.get("serie") is None:
            return None
        return visible_number(doc["serie"], codigo)
    return None


def _orders_by_document(
    session: Session, doc_type: str, docs: list[dict[str, Any]],
) -> dict[str, Order]:
    """Pedidos de BoHub ligados a estos documentos FACTUSOL, indexados por
    `document_order_key`:

    - presupuestos / pedidos de cliente: el pedido que se IMPORTÓ desde el
      documento (`external_id` = CODPRE a secas, origen `factusol_proforma`;
      o `serie-código`, origen `factusol_pedido`).
    - albaranes (Lote 2 · PR-2): el pedido cuyo `factusol_albaran_number` es
      el nº visible del albarán.
    - facturas (Lote 2 · PR-2): el pedido cuya factura vinculada es esta —
      por serie + número (`factusol_invoice_serie` + `factusol_invoice_number`)
      y, si el pedido solo guarda el número desnudo, por la REFFAC = referencia
      común del pedido (regla de `factusol_pdf.find_order_for_invoice`, que
      nunca adivina).

    Sin escribir nada: es un cruce de lectura contra la BD de BoHub."""
    from app.erp.orders_from_factusol import SOURCE_BY_DOC_TYPE  # noqa: PLC0415

    if doc_type in SOURCE_BY_DOC_TYPE:
        ext_ids = {k for k in (document_order_key(doc_type, d) for d in docs) if k}
        if not ext_ids:
            return {}
        rows = session.scalars(
            select(Order).where(
                Order.external_source == SOURCE_BY_DOC_TYPE[doc_type],
                Order.external_id.in_(sorted(ext_ids)),
            )
        ).all()
        return {str(o.external_id): o for o in rows if o.external_id}
    if doc_type == "albaranes":
        return _orders_by_albaran(session, docs)
    if doc_type == "facturas":
        return _orders_by_invoice(session, docs)
    return {}


def _orders_by_albaran(session: Session, docs: list[dict[str, Any]]) -> dict[str, Order]:
    numbers = {k for k in (document_order_key("albaranes", d) for d in docs) if k}
    if not numbers:
        return {}
    rows = session.scalars(
        select(Order).where(Order.factusol_albaran_number.in_(sorted(numbers)))
    ).all()
    return {str(o.factusol_albaran_number): o for o in rows if o.factusol_albaran_number}


def _orders_by_invoice(session: Session, docs: list[dict[str, Any]]) -> dict[str, Order]:
    """Facturas → pedido, en UNA consulta para los candidatos de toda la lista
    y resolviendo en memoria los casos claros; solo los ambiguos (número
    desnudo compartido entre series, varios pedidos con el mismo número…)
    pasan por `find_order_for_invoice`, que aplica la regla completa
    (serie → REFFAC → cliente) sin adivinar."""
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        find_order_for_invoice,
        order_invoice_serie,
    )

    wanted: list[tuple[dict[str, Any], int, int, list[str]]] = []
    numbers: set[str] = set()
    for d in docs:
        codigo, serie = d.get("codigo"), d.get("serie")
        if not isinstance(codigo, int) or serie is None:
            continue
        keys = [str(codigo), f"{serie}-{codigo}", f"{serie}-{codigo:06d}"]
        numbers.update(keys)
        wanted.append((d, int(serie), codigo, keys))
    if not numbers:
        return {}
    rows = session.scalars(
        select(Order).where(Order.factusol_invoice_number.in_(sorted(numbers)))
    ).all()
    by_number: dict[str, list[Order]] = {}
    for o in rows:
        by_number.setdefault(str(o.factusol_invoice_number), []).append(o)

    out: dict[str, Order] = {}
    for d, serie, codigo, keys in wanted:
        candidates: list[Order] = []
        for k in keys:
            for o in by_number.get(k, []):
                if o not in candidates:
                    candidates.append(o)
        if not candidates:
            continue
        series = [order_invoice_serie(o) for o in candidates]
        same = [o for o, s in zip(candidates, series, strict=True) if s == serie]
        if all(s is not None for s in series):
            # Todos los candidatos declaran serie: la factura es de quien
            # guarda ESTA serie (si es uno solo); los de otra serie son la
            # factura homónima de otro pedido.
            if len(same) == 1:
                out[d["numero"]] = same[0]
                continue
            if not same:
                continue
        order = find_order_for_invoice(
            session, serie=serie, codigo=codigo,
            referencia=d.get("referencia"), cliente_codigo=d.get("cliente_codigo"),
        )
        if order is not None:
            out[d["numero"]] = order
    return out


#: Tono de la pastilla de estado por tipo, con el MISMO criterio que el
#: escritorio (los mismos códigos que `documents.ESTADO_LABELS`): presupuestos/
#: pedidos ESTPRE/ESTPCL, albaranes ESTALB, facturas ESTFAC (2=cobrada,
#: 0=pendiente de cobro, 1=cobro parcial). Fuera del mapa → neutro.
_ESTADO_TONES: dict[str, dict[str, str]] = {
    "presupuestos": {"0": "warn", "1": "ok", "2": "muted"},
    "pedidos": {"0": "warn", "2": "ok"},
    "albaranes": {"0": "muted", "1": "ok"},
    "facturas": {"0": "warn", "1": "warn", "2": "ok"},
}


def _estado_tone(doc_type: str, estado: Any) -> str:
    code = str(estado if estado is not None else "").strip()
    if code.endswith(".0"):
        code = code[:-2]
    return _ESTADO_TONES.get(doc_type, {}).get(code, "muted")


def annotate_documents_crm(
    session: Session, docs: list[dict[str, Any]], doc_type: str,
) -> None:
    """Cruza cada documento (cabecera FACTUSOL ya normalizada) con el CRM, IGUAL
    que `annotate_quotes` hace con las proformas, para el explorador de
    documentos (Fase 5). Añade in place, sin escribir en FACTUSOL:

    - `company`: empresa CRM vinculada por CODCLI (`{id,name,country,factusol_id}`).
    - `country_iso2` / `regime` / `regime_label` / `regime_source` / `exento`:
      el régimen de IVA (empresa → cabecera 0 % explícito), la MISMA regla que
      la ficha y las proformas (`workflow.company_regime`).
    - `order`: el pedido de BoHub ligado al documento — el importado desde él
      (presupuestos / pedidos de cliente) o, Lote 2 · PR-2, el que tiene
      vinculado este albarán / esta factura; `None` si no hay ninguno.
    - `estado_tone`: el tono de la pastilla de estado (mismo criterio que el
      escritorio)."""
    codclis = {str(d.get("cliente_codigo")) for d in docs if d.get("cliente_codigo")}
    companies = _companies_by_codcli(session, codclis)
    orders = _orders_by_document(session, doc_type, docs)
    for d in docs:
        code = str(d.get("cliente_codigo") or "").strip()
        company = companies.get(code) or (
            companies.get(str(int(code))) if code.isdigit() else None
        )
        regime = company_regime(company) if company is not None else None
        exento_cabecera = header_says_no_iva(d.get("iva_pct"), d.get("base"))
        d["company"] = _company_block(company) if company is not None else None
        d["country_iso2"] = normalize_country(company.country) if (
            company is not None and company.country) else None
        d["regime"] = regime
        d["regime_label"] = REGIME_LABELS[regime] if regime else (
            "Exento (según el documento)" if exento_cabecera else None
        )
        d["regime_source"] = "empresa" if regime else ("cabecera" if exento_cabecera else None)
        d["exento"] = (regime in ("intracomunitario", "exportacion")) if regime else exento_cabecera
        d["estado_tone"] = _estado_tone(doc_type, d.get("estado"))
        key = document_order_key(doc_type, d)
        order = orders.get(key) if key else None
        d["order"] = (
            {"id": order.id, "order_number": order.order_number}
            if order is not None else None
        )


def _company_block(company: Company) -> dict[str, Any]:
    return {
        "id": company.id, "name": company.name, "country": company.country,
        "factusol_id": company.factusol_company_id,
    }


def annotate_quotes(session: Session, quotes: list[dict[str, Any]]) -> dict[str, Any]:
    """Añade estado / cola / empresa / régimen / pedido a cada proforma (in
    place) y devuelve `{queue_counts, estpre_values}`."""
    codclis = {str(q.get("clipre")) for q in quotes if q.get("clipre")}
    codpres = {str(q.get("codpre")) for q in quotes if q.get("codpre")}
    companies = _companies_by_codcli(session, codclis)
    orders = _orders_by_codpre(session, codpres)
    serie_names = _serie_names(session)

    counts: Counter[str] = Counter()
    estpre_values: Counter[str] = Counter()
    for q in quotes:
        estpre_values[str(q.get("estpre")) if q.get("estpre") is not None else "null"] += 1
        # Serie (empresa emisora) y número visible «serie-código», como en el
        # escritorio de FACTUSOL (5-000039).
        serie = _serie_of(q.get("tippre"))
        q["serie"] = serie
        q["serie_label"] = serie_names.get(serie) if serie is not None else None
        codpre = str(q.get("codpre") or "")
        if serie is not None and codpre.isdigit():
            q["numero"] = f"{serie}-{int(codpre):06d}"
        else:
            q["numero"] = codpre or None
        clipre = str(q.get("clipre") or "").strip()
        company = companies.get(clipre) or (
            companies.get(str(int(clipre))) if clipre.isdigit() else None
        )
        order = orders.get(str(q.get("codpre")))
        q["company"] = _company_block(company) if company is not None else None
        q["order"] = (
            {"id": order.id, "order_number": order.order_number} if order is not None else None
        )
        # Régimen: la empresa manda (país + NIF-IVA + VIES); sin empresa, la
        # cabecera del documento (0 % explícito = exento).
        regime = company_regime(company) if company is not None else None
        exento_cabecera = header_says_no_iva(q.get("piva1pre"), q.get("base"))
        q["country_iso2"] = normalize_country(company.country) if (
            company is not None and company.country) else None
        q["regime"] = regime
        q["regime_label"] = REGIME_LABELS[regime] if regime else (
            "Exento (según la proforma)" if exento_cabecera else None
        )
        q["regime_source"] = "empresa" if regime else ("cabecera" if exento_cabecera else None)
        q["exento"] = (regime in ("intracomunitario", "exportacion")) if regime else exento_cabecera
        queue = quote_queue(q.get("estado") or "otro", converted=order is not None)
        q["queue"] = queue
        q["queue_label"] = QUOTE_QUEUE_LABELS.get(queue) if queue else None
        if queue:
            counts[queue] += 1
    return {
        "queue_counts": {queue: counts.get(queue, 0) for queue in QUOTE_QUEUES},
        "estpre_values": dict(estpre_values),
    }
