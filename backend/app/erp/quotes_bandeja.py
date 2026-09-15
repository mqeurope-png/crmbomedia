"""Pantalla Proformas (rediseño de flujo, Fase 4): lo que cada proforma
necesita para la bandeja de proformas, encima del listado F_PRE.

Por proforma se añade:
- `estado` / `estado_label` (ya vienen de `quotes._row_to_quote`, por `ESTPRE`).
- `order`: el pedido de BoHub si la proforma ya se convirtió (origen
  `factusol_proforma`, `external_id` = CODPRE) → cola «convertidas».
- `queue` / `queue_label`: por `workflow.quote_queue` (mismo criterio que la
  bandeja de pedidos: la cola dice lo que toca).
- `company`: la empresa CRM vinculada al CLIPRE (id, nombre, país) si existe.
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
