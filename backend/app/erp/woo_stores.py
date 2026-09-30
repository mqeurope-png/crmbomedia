"""ERP · la TIENDA de un pedido web: UNA sola clave en toda la app.

La clave es el `account_id` de la cuenta WooCommerce en `integration_accounts`
(`artisjet-europe`, `boprint`, `fluxlasers`), la misma con la que el pedido
guarda su tienda (`orders.store_id → integration_accounts.id`). La usan las
reglas de contrapartida, la PayPal por tienda, los remitentes (factura y aviso
de envío), el prefijo de referencia FACTUSOL y la serie por tienda. Sin listas
fijas ni alias: los desplegables «Tienda» salen de las cuentas Woo reales
(rev. 30/09/2026 — antes artisJet se guardaba como `artisjet` en unos sitios y
como `artisjet-europe` en otros, y sus reglas nunca casaban).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session


def store_key(value: Any) -> str:
    """Clave normalizada: el `account_id` tal cual, en minúsculas y sin
    espacios alrededor. Sin alias."""
    return str(value or "").strip().lower()


def _woo_accounts(session: Session) -> list[Any]:
    from app.models.integration_settings import (  # noqa: PLC0415
        ExternalSystem,
        IntegrationAccount,
    )

    return list(session.scalars(
        select(IntegrationAccount)
        .where(IntegrationAccount.system == ExternalSystem.WOOCOMMERCE)
        .order_by(IntegrationAccount.account_id)
    ))


def woo_stores(session: Session) -> list[dict[str, str]]:
    """Tiendas Woo dadas de alta: `[{key: account_id, label: display_name}]`,
    para los desplegables «Tienda»."""
    return [
        {"key": store_key(a.account_id), "label": a.display_name or a.account_id}
        for a in _woo_accounts(session)
        if store_key(a.account_id)
    ]


def store_display_name(session: Session, key: Any) -> str:
    """Nombre visible de la tienda («Artisjet Europe»); la propia clave si no
    hay cuenta con ese `account_id`."""
    wanted = store_key(key)
    for store in woo_stores(session):
        if store["key"] == wanted:
            return store["label"]
    return wanted


def order_store_key(session: Session, order: Any) -> str | None:
    """Tienda del pedido (`orders.store_id → integration_accounts.account_id`),
    o None si el pedido no es de tienda / no tiene cuenta."""
    if order is None or not getattr(order, "store_id", None):
        return None
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    store = session.get(IntegrationAccount, order.store_id)
    if store is None:
        return None
    return store_key(store.account_id) or None


def order_number_prefix(account_id: Any) -> str:
    """Prefijo del nº de pedido web de esa tienda: el mapper lo compone con
    `account_id.upper()[:6]` (`artisjet-europe` → `ARTISJ`)."""
    return str(account_id or "").strip().upper()[:6]


def store_for_order_number(session: Session, order_number: Any) -> str | None:
    """Tienda por el prefijo del nº de pedido (`ARTISJ-9638` →
    `artisjet-europe`), para el pedido que no trae cuenta. Sale de las cuentas
    Woo reales; None si ninguna (o más de una) casa."""
    prefix = str(order_number or "").split("-")[0].strip().upper()
    if not prefix:
        return None
    hits = [
        s["key"] for s in woo_stores(session) if order_number_prefix(s["key"]) == prefix
    ]
    return hits[0] if len(hits) == 1 else None


__all__ = [
    "order_number_prefix",
    "order_store_key",
    "store_display_name",
    "store_for_order_number",
    "store_key",
    "woo_stores",
]
