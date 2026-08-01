"""BoHub ERP — capa API (Fase A)."""
from app.erp.api.exceptions import router as exceptions_router
from app.erp.api.order_timeline import router as order_timeline_router
from app.erp.api.orders import router as orders_router
from app.erp.api.sat import router as sat_router
from app.erp.api.woocommerce_admin import router as woocommerce_admin_router

__all__ = [
    "exceptions_router",
    "order_timeline_router",
    "orders_router",
    "sat_router",
    "woocommerce_admin_router",
]
