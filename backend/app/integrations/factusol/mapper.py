"""Mappers puros CRM → FACTUSOL (Fase C PR C-1).

Transforman entidades del CRM en los payloads de las tablas de la API DELSOL.
Funciones sin efectos (unit-testeables); NO tocan la BD ni la red.

Nombres de columnas según `docs/erp/factusol-schema.md` (convención F_XXX +
prefijo de 3 letras). Los campos marcados «confirmar» se validan en vivo con
el smoke-test dry-run antes de emitir facturas reales (C-2).
"""
from __future__ import annotations

import re
from typing import Any

from app.erp.models import Order
from app.models.crm import Company


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def company_to_factusol_client(company: Company, codcli: str) -> dict[str, Any]:
    """`Company` del CRM → registro F_CLI. El `codcli` (PK) lo decide el
    servicio (reusa el existente o genera el siguiente secuencial)."""
    return {
        "CODCLI": codcli,
        "PCOCLI": (company.name or "")[:40],   # nombre comercial
        "NOFCLI": (company.name or "")[:40],   # nombre fiscal
        "CIFCLI": (company.tax_id or "")[:20],
        "DOMCLI": (company.address_line or "")[:60],
        "POBCLI": (company.city or "")[:40],
        "CPOCLI": (company.postal_code or "")[:10],
        "PAICLI": (company.country or "")[:30],
        "WEBCLI": (company.website or "")[:60],
    }


def _line_to_factusol(
    codfac: str, position: int, line: Any, ejercicio: str,
) -> dict[str, Any]:
    return {
        "CODLFA": codfac,          # documento padre (F_FAC.CODFAC)
        "EJELFA": ejercicio,
        "POSLFA": position,
        "ARTLFA": line.product_codart or "",   # CODART; vacío si sin mapear
        "REFLFA": line.product_sku or "",
        "DESLFA": (line.description or line.product_sku or "")[:50],
        "CANLFA": float(line.quantity or 0),
        "PRELFA": float(line.unit_price or 0),
        "IVALFA": float(line.tax_rate or 0),
        "TOTLFA": float(line.line_total or 0),
    }


def order_to_factusol_invoice(
    order: Order, factusol_codcli: str, ejercicio: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """`Order` del CRM → (cabecera F_FAC, líneas F_LFA).

    El número de factura (CODFAC) se deriva de forma determinista del número
    de pedido como candidato; la política real de numeración (¿la asigna la
    API o el integrador?) se confirma en el smoke-test dry-run antes de
    emitir de verdad (C-2). No se emite ninguna factura en C-1.
    """
    codfac = _digits(order.order_number) or _digits(order.id)[:8] or "0"
    fecha = order.placed_at.date().isoformat() if order.placed_at else None
    cabecera = {
        "CODFAC": codfac,
        "EJEFAC": ejercicio,
        "CLIFAC": factusol_codcli,
        "FECFAC": fecha,
        "TOTFAC": float(order.total_amount or 0),
        "REFFAC": order.order_number,          # referencia externa (nº CRM)
    }
    lineas = [
        _line_to_factusol(codfac, i + 1, line, ejercicio)
        for i, line in enumerate(order.lines)
    ]
    return cabecera, lineas
