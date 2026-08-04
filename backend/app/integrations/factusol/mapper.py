"""Mappers puros CRM → FACTUSOL (Fase C).

Transforman entidades del CRM en los payloads de las tablas de la API DELSOL.
Funciones sin efectos (unit-testeables); NO tocan la BD ni la red.

Nombres de columnas verificados contra la API real (ver
`docs/erp/factusol-write-flows.md`). El CODFAC (nº de factura) NO lo pone el
mapper: FACTUSOL numera solo, así que el service calcula el siguiente con
`next_codfac()` y lo inyecta en cabecera + líneas justo antes de escribir.
"""
from __future__ import annotations

from typing import Any

from app.erp.models import Order
from app.models.crm import Company

#: Tipo de documento de F_FAC: 2 = factura ordinaria (valor observado en las
#: facturas reales de Bomedia). Bomedia NO usa serie (F_SER vacía) → sin SERFAC.
TIPFAC_FACTURA_ORDINARIA = 2


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


def _line_to_factusol(position: int, line: Any, ejercicio: str) -> dict[str, Any]:
    # CODLFA (FK a F_FAC.CODFAC) lo inyecta el service tras calcular el CODFAC.
    return {
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
    """`Order` del CRM → (cabecera F_FAC, líneas F_LFA), SIN numerar.

    El CODFAC (cabecera) y el CODLFA (líneas) los añade el service con
    `next_codfac()` justo antes de escribir — FACTUSOL numera secuencialmente
    por ejercicio y no queremos pisar su numeración.
    """
    fecha = order.placed_at.date().isoformat() if order.placed_at else None
    cabecera = {
        "EJEFAC": ejercicio,
        "TIPFAC": TIPFAC_FACTURA_ORDINARIA,
        "CLIFAC": factusol_codcli,
        "FECFAC": fecha,
        "TOTFAC": float(order.total_amount or 0),
        "REFFAC": order.order_number,          # referencia externa (nº CRM)
    }
    lineas = [
        _line_to_factusol(i + 1, line, ejercicio)
        for i, line in enumerate(order.lines)
    ]
    return cabecera, lineas
