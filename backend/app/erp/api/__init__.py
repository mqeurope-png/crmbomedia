"""BoHub ERP — capa API (Fase A)."""
from app.erp.api.bank import router as bank_router
from app.erp.api.catalogs import router as catalogs_router
from app.erp.api.conocimiento import router as conocimiento_router
from app.erp.api.cuadre import router as cuadre_router
from app.erp.api.exceptions import router as exceptions_router
from app.erp.api.factusol import router as factusol_router
from app.erp.api.genei import router as genei_router
from app.erp.api.ia import router as ia_router
from app.erp.api.leads import router as leads_router
from app.erp.api.ofertas import router as ofertas_router
from app.erp.api.order_timeline import router as order_timeline_router
from app.erp.api.orders import router as orders_router
from app.erp.api.pedidos_web import router as pedidos_web_router
from app.erp.api.plantillas import router as plantillas_router
from app.erp.api.sat import router as sat_router
from app.erp.api.seguimiento import router as seguimiento_router
from app.erp.api.shipping import router as shipping_router
from app.erp.api.soporte import router as soporte_router
from app.erp.api.webs import router as webs_router
from app.erp.api.woocommerce_admin import router as woocommerce_admin_router

__all__ = [
    "bank_router",
    "catalogs_router",
    "conocimiento_router",
    "cuadre_router",
    "seguimiento_router",
    "exceptions_router",
    "factusol_router",
    "genei_router",
    "ia_router",
    "leads_router",
    "ofertas_router",
    "plantillas_router",
    "order_timeline_router",
    "orders_router",
    "pedidos_web_router",
    "sat_router",
    "shipping_router",
    "soporte_router",
    "webs_router",
    "woocommerce_admin_router",
]
