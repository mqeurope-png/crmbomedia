"""BoHub ERP — «Traer pedido web»: los `on-hold` de las tiendas, a mano.

  GET  /api/erp/pedidos-web/en-espera?q=   lista en vivo de los `on-hold`
  POST /api/erp/pedidos-web/traer          trae uno (`{store_id, woo_id}`)

La lógica vive en `app/erp/pedidos_web.py`. Solo quien crea pedidos Y ve los
pedidos web (pedidos y admin): `Cap.ORDERS_CREATE` + `Cap.ORDERS_VIEW_WEB`.
Errores: `{"detail": {"code", "detail", "order_id"?}}`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import require_capabilities
from app.erp.capabilities import Cap
from app.erp.pedidos_web import PedidoWebError, pedidos_en_espera, traer_pedido_web
from app.models.crm import User

router = APIRouter(prefix="/api/erp/pedidos-web", tags=["erp-pedidos-web"])

#: Traer un pedido web = crear un pedido (oficina) que además es web.
require_traer_pedido_web = require_capabilities(Cap.ORDERS_CREATE, Cap.ORDERS_VIEW_WEB)


class TraerIn(BaseModel):
    #: Id de la tienda (`integration_accounts.id`), el `store_id` de la lista.
    store_id: str | int
    #: Id del pedido en WooCommerce (el `woo_id` de la lista).
    woo_id: int = Field(gt=0)


@router.get("/en-espera")
def en_espera(
    q: str | None = Query(default=None, max_length=120),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_traer_pedido_web),
) -> dict[str, Any]:
    """Los pedidos `on-hold` de las tiendas Woo activas, leídos en vivo y en
    paralelo (una tienda que no responde sale en `tiendas` con su error).
    Solo lectura."""
    _ = current_user
    return pedidos_en_espera(session, q=q)


@router.post("/traer")
def traer(
    body: TraerIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_traer_pedido_web),
) -> dict[str, Any]:
    """Trae el pedido a BoHub por el camino del importador. Idempotente: si ya
    está, devuelve el que hay (`creado: false`)."""
    try:
        return traer_pedido_web(
            session, store_id=str(body.store_id), woo_id=body.woo_id, user=current_user,
        )
    except PedidoWebError as exc:
        raise HTTPException(exc.status, exc.as_detail()) from exc
