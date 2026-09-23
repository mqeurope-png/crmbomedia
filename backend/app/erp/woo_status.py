"""ERP · WooCommerce — vocabulario de estados y qué significan en BoHub.

Un sitio ÚNICO para normalizar el estado de la tienda y para la puerta de
entrada de los pedidos WEB al Seguimiento. Sin dependencias (ni modelos ni
sesión) a propósito: lo importan el seguimiento, la reconciliación, la
anulación y la API de pedidos sin ciclos.

La puerta es «haber pasado por caja»: un pedido web entra en Seguimiento
cuando está `processing` (pagado / en preparación), `completed` (servido) o
`refunded` (pagado y devuelto después). `pending` / `on-hold` son carritos que
todavía no han entrado en el flujo real, y `cancelled` / `failed` / `draft` /
`trash` nunca llegaron a entrar.

`refunded` NO es «anulado»: es un estado PROPIO de BoHub, «Reembolsado». El
pedido se pagó y se sirvió, y luego se devolvió el dinero — sigue siendo un
hecho del negocio que hay que ver (queda el abono), no un pedido que no
existió. Se DERIVA de `woo_status`, así que los que la auto-anulación por
reembolso dejó marcados como anulados quedan reclasificados sin tocar la BD.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "REFUNDED",
    "WEB_HIDDEN_MOTIVOS",
    "WEB_VISIBLE_STATUSES",
    "is_refunded",
    "motivo_label",
    "normalize",
]

#: Reembolso TOTAL en la tienda (el PARCIAL deja el pedido en `processing`).
REFUNDED = "refunded"

#: Estados con los que un pedido WEB entra en el Seguimiento.
WEB_VISIBLE_STATUSES: frozenset[str] = frozenset({
    "processing", "completed", REFUNDED,
})

#: Motivo de ocultación (el propio estado, normalizado) → etiqueta legible de
#: «Ver ocultos por estado». Los que no estén aquí se enseñan en crudo.
WEB_HIDDEN_MOTIVOS: dict[str, str] = {
    "pending": "Sin pagar",
    "on_hold": "En espera",
    "cancelled": "Cancelado en la tienda",
    "failed": "Pago fallido",
    "draft": "Borrador",
    "checkout_draft": "Carrito sin terminar",
    "trash": "En la papelera",
    "deleted": "Borrado en la tienda",
}


def normalize(value: Any) -> str:
    """Estado de WooCommerce en su forma canónica: `wc-on-hold` → `on_hold`.

    WooCommerce entrega el estado SIN el prefijo `wc-` en la REST API y CON él
    en los exports y en la BD de la tienda; el guion se pasa a `_` para poder
    usarlo tal cual como clave y como motivo. Cadena vacía si no consta."""
    st = str(value or "").strip().lower()
    if st.startswith("wc-"):
        st = st[3:]
    return st.replace("-", "_")


def is_refunded(order: Any) -> bool:
    """Estado propio «Reembolsado»: el pedido está reembolsado en la tienda."""
    return normalize(getattr(order, "woo_status", None)) == REFUNDED


def motivo_label(motivo: str | None) -> str:
    """Etiqueta legible de un motivo de ocultación por estado."""
    return WEB_HIDDEN_MOTIVOS.get(motivo or "", motivo or "")
