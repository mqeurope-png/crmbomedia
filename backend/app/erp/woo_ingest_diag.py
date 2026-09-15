"""Diagnóstico SOLO LECTURA de la incidencia «los pedidos web dejan de aparecer
en la bandeja» (desde ~13-sep, con el filtro «solo processing» de PR #387).

NO cambia nada — ni la lógica de ingesta ni la BD: solo lee `sync_logs`,
`integration_events` y `orders`, y cuenta:

1. Cuántos webhooks de pedido se han DESCARTADO por el filtro «solo processing»
   (`_log_webhook_sync` los deja como `webhook_process` SUCCESS con el mensaje
   «order ignored (status=…, not processing)») y con qué estado de Woo. El
   reparto por estado es la señal clave: si predomina `on-hold` (transferencia)
   o `pending` (contrareembolso) o `completed`, esos pedidos legítimos se están
   perdiendo porque nunca pasan por `processing`.
2. Cuántos PEDIDOS DISTINTOS (por id de Woo) llegaron por webhook desde la
   fecha y NO existen como `Order` (nunca se crearon). Es el «cuántos afectados»
   más fiel (los logs cuentan eventos, no pedidos).
3. Si hay pedidos web creados-pero-OCULTOS desde la fecha (procesados
   externamente por la fecha de corte, o excluidos del seguimiento por la
   limpieza reversible) — el otro modo en que un pedido «no aparece».
4. La `external_cutoff_date` de cada tienda, para descartar una fecha futura mal
   puesta que ocultaría los pedidos nuevos.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import IntegrationEvent, Order, OrderSource
from app.models.crm import ExternalSystem, SyncLog
from app.models.integration_settings import IntegrationAccount

#: El filtro de #387 se desplegó ~13-sep; ventana por defecto del diagnóstico.
DEFAULT_SINCE = datetime(2026, 9, 13, tzinfo=timezone.utc)

#: Mensaje exacto de `_log_webhook_sync` al descartar (jobs.py):
#: «webhook order.updated: order ignored (status=on-hold, not processing)».
_IGNORED_RE = re.compile(r"order ignored \(status=(?P<status>[^,]*), not processing\)")


def parse_skipped_status(message: str | None) -> str | None:
    """Estado de Woo del mensaje «order ignored (status=X, not processing)»."""
    if not message:
        return None
    m = _IGNORED_RE.search(message)
    return m.group("status").strip() if m else None


def parse_order_id(payload_json: str | None) -> str | None:
    """Id de pedido Woo del payload del webhook (`{"id": …}`)."""
    if not payload_json:
        return None
    try:
        data = json.loads(payload_json)
    except (TypeError, ValueError):
        return None
    oid = data.get("id") if isinstance(data, dict) else None
    return str(oid) if oid not in (None, "") else None


@dataclass
class WooIngestDiag:
    since: str
    ignored_events_total: int
    ignored_by_status: dict[str, int]
    ignored_by_store_status: dict[str, dict[str, int]]
    distinct_dropped_orders: int
    dropped_sample: list[str]
    created_web_orders: int
    hidden_externalized: int
    hidden_excluded: int
    store_cutoffs: list[dict[str, Any]] = field(default_factory=list)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _woo_accounts(session: Session) -> list[IntegrationAccount]:
    return list(session.scalars(
        select(IntegrationAccount).where(
            IntegrationAccount.system == ExternalSystem.WOOCOMMERCE,
        )
    ).all())


def scan_ignored_webhooks(
    session: Session, since: datetime,
) -> tuple[int, Counter[str], dict[str, Counter[str]]]:
    """Cuenta los `sync_logs` de webhook descartado por «solo processing» desde
    `since`, por estado y por (tienda, estado). Devuelve (total, by_status,
    by_store_status)."""
    rows = session.execute(
        select(SyncLog.account_id, SyncLog.message).where(
            SyncLog.operation == "webhook_process",
            SyncLog.message.like("%order ignored%not processing%"),
            SyncLog.started_at >= since,
        )
    ).all()
    total = 0
    by_status: Counter[str] = Counter()
    by_store_status: dict[str, Counter[str]] = {}
    for account_id, message in rows:
        status = parse_skipped_status(message)
        if status is None:
            continue
        total += 1
        by_status[status or "?"] += 1
        store = account_id or "?"
        by_store_status.setdefault(store, Counter())[status or "?"] += 1
    return total, by_status, by_store_status


def distinct_dropped_orders(
    session: Session, since: datetime,
) -> tuple[int, list[str]]:
    """Pedidos DISTINTOS (id de Woo) que llegaron por webhook `order.*` desde
    `since` y no existen como `Order` (nunca se crearon). Cuenta el pedido, no
    el evento (un pedido genera varios eventos). Aproxima el «cuántos
    afectados»; depende de la retención de `integration_events`."""
    slug_to_store = {a.account_id: a.id for a in _woo_accounts(session)}
    events = session.execute(
        select(IntegrationEvent.account_id, IntegrationEvent.payload_json).where(
            IntegrationEvent.system == "woocommerce",
            IntegrationEvent.event_type.like("order.%"),
            IntegrationEvent.event_type != "order.deleted",
            IntegrationEvent.created_at >= since,
        )
    ).all()
    seen: set[tuple[str, str]] = set()
    for account_id, payload in events:
        oid = parse_order_id(payload)
        if oid is not None:
            seen.add((account_id or "?", oid))
    dropped: list[str] = []
    for slug, oid in sorted(seen):
        store_id = slug_to_store.get(slug)
        exists = session.scalar(
            select(Order.id).where(
                Order.external_source == OrderSource.WOOCOMMERCE,
                Order.external_id == oid,
                *( [Order.store_id == store_id] if store_id else [] ),
            )
        )
        if exists is None:
            dropped.append(f"{slug}#{oid}")
    return len(dropped), dropped[:30]


def hidden_web_orders(session: Session, since: datetime) -> tuple[int, int, int]:
    """Pedidos web creados desde `since` y cuántos están OCULTOS de la bandeja:
    procesados externamente (fecha de corte) o excluidos del seguimiento."""
    created = 0
    externalized = 0
    excluded = 0
    for order in session.scalars(
        select(Order).where(
            Order.external_source == OrderSource.WOOCOMMERCE,
            Order.created_at >= since,
        )
    ):
        created += 1
        if order.externally_processed_at is not None:
            externalized += 1
        if order.seguimiento_excluded_at is not None:
            excluded += 1
    return created, externalized, excluded


def store_cutoffs(session: Session) -> list[dict[str, Any]]:
    """`external_cutoff_date` de cada tienda Woo (crudo + parseado), para
    descartar una fecha futura que ocultaría los pedidos nuevos."""
    out: list[dict[str, Any]] = []
    for a in _woo_accounts(session):
        raw = None
        if a.metadata_json:
            try:
                meta = json.loads(a.metadata_json)
                raw = meta.get("external_cutoff_date") if isinstance(meta, dict) else None
            except (TypeError, ValueError):
                raw = None
        out.append({"store": a.account_id, "external_cutoff_date": raw})
    return out


def diagnose(session: Session, *, since: datetime = DEFAULT_SINCE) -> WooIngestDiag:
    since = _aware(since)
    total, by_status, by_store_status = scan_ignored_webhooks(session, since)
    dropped_n, dropped_sample = distinct_dropped_orders(session, since)
    created, externalized, excluded = hidden_web_orders(session, since)
    return WooIngestDiag(
        since=since.isoformat(),
        ignored_events_total=total,
        ignored_by_status=dict(by_status),
        ignored_by_store_status={k: dict(v) for k, v in by_store_status.items()},
        distinct_dropped_orders=dropped_n,
        dropped_sample=dropped_sample,
        created_web_orders=created,
        hidden_externalized=externalized,
        hidden_excluded=excluded,
        store_cutoffs=store_cutoffs(session),
    )


def format_report(diag: WooIngestDiag) -> str:
    lines = [
        "Incidencia «pedidos web no aparecen» — diagnóstico SOLO LECTURA "
        f"(desde {diag.since[:10]})",
        "",
        "1. Webhooks de pedido DESCARTADOS por «solo processing» (#387):",
        f"   eventos descartados: {diag.ignored_events_total}",
    ]
    if diag.ignored_by_status:
        for status, n in sorted(diag.ignored_by_status.items(), key=lambda kv: -kv[1]):
            lines.append(f"     - status={status}: {n}")
    else:
        lines.append("     (ninguno registrado en sync_logs en la ventana)")
    lines += [
        "",
        f"2. Pedidos DISTINTOS llegados por webhook y NUNCA creados: "
        f"{diag.distinct_dropped_orders}",
    ]
    if diag.dropped_sample:
        lines.append(f"     muestra: {', '.join(diag.dropped_sample)}")
    lines += [
        "",
        "3. Pedidos web creados-pero-OCULTOS desde la fecha:",
        f"     creados: {diag.created_web_orders} · procesados externamente "
        f"(fecha de corte): {diag.hidden_externalized} · excluidos del "
        f"seguimiento: {diag.hidden_excluded}",
        "",
        "4. Fecha de corte por tienda (descartar fecha futura):",
    ]
    for c in diag.store_cutoffs:
        lines.append(f"     - {c['store']}: {c['external_cutoff_date'] or '∅'}")
    lines += [
        "",
        "Reparto por tienda de los descartes:",
    ]
    for store, counts in sorted(diag.ignored_by_store_status.items()):
        detalle = ", ".join(f"{st}={n}" for st, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        lines.append(f"     - {store}: {detalle}")
    lines += ["", "SOLO LECTURA — no se ha escrito ni cambiado nada."]
    return "\n".join(lines)
