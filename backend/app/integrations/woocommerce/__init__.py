"""BoHub ERP — integración WooCommerce (Fase B).

Cliente REST v3 + mapper Woo→Order + jobs RQ para consumir
integration_events. Multi-tienda: una cuenta por tienda en
integration_accounts (system=woocommerce, account_id=slug).
"""
from app.integrations.woocommerce.client import WooError, WooHTTPClient
from app.integrations.woocommerce.mapper import ImportOutcome, import_woo_order

__all__ = ["ImportOutcome", "WooError", "WooHTTPClient", "import_woo_order"]
