"""BoHub ERP — timeline unificado del pedido (Fase A PR 3).

GET /api/erp/orders/{id}/timeline?limit&offset&types

Duplica el patrón de `app/api/contact_timeline.py` (decisión cerrada nº6):
volúmenes per-pedido pequeños → cargar fuentes filtradas, normalizar a un
shape común, ordenar en memoria y paginar. Fuentes Fase A:

  - `status`     order_status_history (transiciones de los 4 dominios)
  - `exception`  exceptions (alta + resolución como eventos separados)
  - `audit`      audit_logs con target el pedido (acciones no-transición)
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import require_erp_view
from app.erp.models import ErpException, Order, OrderStatusHistory
from app.models.crm import AuditLog, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/erp/orders", tags=["erp-order-timeline"])

ALL_TYPES = {"status", "exception", "audit"}


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _event(
    *, type_: str, at: datetime, title: str,
    detail: dict[str, Any] | None = None, actor_user_id: str | None = None,
) -> dict[str, Any]:
    return {
        "type": type_,
        "at": _aware(at).isoformat(),
        "title": title,
        "detail": detail or {},
        "actor_user_id": actor_user_id,
    }


@router.get("/{order_id}/timeline")
def order_timeline(
    order_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    types: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    order = session.get(Order, order_id)
    if order is None:
        raise not_found("Order")

    wanted = (
        {t.strip() for t in types.split(",") if t.strip()} & ALL_TYPES
        if types else ALL_TYPES
    )
    events: list[dict[str, Any]] = []

    if "status" in wanted:
        for h in session.scalars(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id)
        ):
            domain = getattr(h.domain, "value", h.domain)
            events.append(_event(
                type_="status", at=h.changed_at,
                title=f"{domain}: {h.from_status or '—'} → {h.to_status}",
                detail={
                    "domain": domain,
                    "from_status": h.from_status,
                    "to_status": h.to_status,
                    "reason": h.reason,
                    "metadata": json.loads(h.metadata_json) if h.metadata_json else {},
                },
                actor_user_id=h.changed_by_user_id,
            ))

    if "exception" in wanted:
        for e in session.scalars(
            select(ErpException).where(ErpException.order_id == order_id)
        ):
            etype = getattr(e.type, "value", e.type)
            events.append(_event(
                type_="exception", at=e.created_at,
                title=f"Excepción: {etype}" + (f":{e.subtype}" if e.subtype else ""),
                detail={
                    "exception_id": e.id, "exception_type": etype,
                    "subtype": e.subtype, "phase": "reported",
                    "metadata": json.loads(e.metadata_json) if e.metadata_json else {},
                },
                actor_user_id=e.reported_by_user_id,
            ))
            if e.resolved_at:
                events.append(_event(
                    type_="exception", at=e.resolved_at,
                    title=f"Excepción resuelta: {etype}",
                    detail={
                        "exception_id": e.id, "exception_type": etype,
                        "phase": "resolved",
                        "resolution_note": e.resolution_note,
                    },
                    actor_user_id=e.resolved_by_user_id,
                ))

    if "audit" in wanted:
        for a in session.scalars(
            select(AuditLog).where(
                AuditLog.target_type == "order",
                AuditLog.target_id == order_id,
                # Las transiciones ya salen de status_history — evita duplicar.
                AuditLog.action != "erp.order_status_changed",
            )
        ):
            events.append(_event(
                type_="audit", at=a.created_at, title=a.action,
                detail=json.loads(a.metadata_json) if a.metadata_json else {},
                actor_user_id=a.actor_user_id,
            ))

    events.sort(key=lambda e: e["at"], reverse=True)
    page = events[offset:offset + limit]
    return {
        "order_id": order_id,
        "total": len(events),
        "items": page,
    }
