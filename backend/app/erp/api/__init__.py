"""BoHub ERP — capa API (Fase A)."""
from app.erp.api.bank import router as bank_router
from app.erp.api.catalogs import router as catalogs_router
from app.erp.api.exceptions import router as exceptions_router
from app.erp.api.factusol import router as factusol_router
from app.erp.api.order_timeline import router as order_timeline_router
from app.erp.api.orders import router as orders_router
from app.erp.api.sat import router as sat_router
from app.erp.api.seguimiento import router as seguimiento_router
from app.erp.api.shipping import router as shipping_router
from app.erp.api.woocommerce_admin import router as woocommerce_admin_router

__all__ = [
    "bank_router",
    "catalogs_router",
    "seguimiento_router",
    "exceptions_router",
    "factusol_router",
    "order_timeline_router",
    "orders_router",
    "sat_router",
    "shipping_router",
    "woocommerce_admin_router",
]
