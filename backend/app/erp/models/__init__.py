"""BoHub ERP — modelos (Fase A). Importar este paquete registra todas las
tablas ERP en Base.metadata (app/db/base.py lo importa)."""
from app.erp.models.carriers import Carrier
from app.erp.models.exceptions import (
    EXCEPTION_SUBTYPES,
    ErpException,
    ExceptionStatus,
    ExceptionType,
)
from app.erp.models.orders import (
    InvoiceStatus,
    Order,
    OrderLine,
    OrderSource,
    OrderStatusHistory,
    PaymentStatus,
    PreparationStatus,
    StatusDomain,
    TransportStatus,
)
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID, ErpSettings, InvoiceMode
from app.erp.models.sku_mapping import ProductSkuMapping, SkuMatchedBy

__all__ = [
    "EXCEPTION_SUBTYPES",
    "ERP_SETTINGS_SINGLETON_ID",
    "Carrier",
    "ErpException",
    "ErpSettings",
    "ExceptionStatus",
    "ExceptionType",
    "InvoiceMode",
    "InvoiceStatus",
    "Order",
    "OrderLine",
    "OrderSource",
    "OrderStatusHistory",
    "PaymentStatus",
    "PreparationStatus",
    "ProductSkuMapping",
    "SkuMatchedBy",
    "StatusDomain",
    "TransportStatus",
]
