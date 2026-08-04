"""Generador de albarán propio (Fase D · D-1-fix1).

Último recurso cuando el plugin PDF de WooCommerce no expone el albarán con las
credenciales de API (versión free sin REST, sin acceso público por order_key).
Construye un albarán funcional a partir del JSON del pedido de WooCommerce
(`GET /wp-json/wc/v3/orders/{id}`, que sí funciona): cabecera, dirección de
envío, líneas y un código de barras Code128 del número de pedido.

No pretende ser idéntico al del plugin — es un albarán que acompaña el envío. El
operativo puede seguir imprimiendo el «bonito» desde WP admin si lo prefiere.
"""
from __future__ import annotations

import io
from typing import Any

from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _address(block: dict[str, Any]) -> str:
    block = block or {}
    name = f"{_s(block.get('first_name'))} {_s(block.get('last_name'))}".strip()
    company = _s(block.get("company"))
    lines = [
        name,
        company,
        _s(block.get("address_1")),
        _s(block.get("address_2")),
        f"{_s(block.get('postcode'))} {_s(block.get('city'))}".strip(),
        _s(block.get("state")),
        _s(block.get("country")),
    ]
    return "<br/>".join(x for x in lines if x)


def generate_albaran_pdf(order: dict[str, Any]) -> bytes:
    """Woo order JSON → bytes de un PDF de albarán A4."""
    order = order or {}
    number = _s(order.get("number") or order.get("id") or "s/n")
    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Albarán {number}",
    )
    story: list[Any] = []
    story.append(Paragraph(f"Albarán — Pedido {number}", styles["Title"]))
    story.append(Spacer(1, 6 * mm))

    ship = order.get("shipping") or {}
    bill = order.get("billing") or {}
    dest = _address(ship) or _address(bill) or "—"
    story.append(Paragraph("<b>Enviar a:</b>", styles["Normal"]))
    story.append(Paragraph(dest, styles["Normal"]))
    story.append(Spacer(1, 6 * mm))

    data: list[list[str]] = [["SKU", "Artículo", "Cant."]]
    for li in order.get("line_items") or []:
        data.append([
            _s(li.get("sku")),
            _s(li.get("name")),
            _s(li.get("quantity")),
        ])
    if len(data) == 1:
        data.append(["", "(sin líneas)", ""])
    table = Table(data, colWidths=[35 * mm, 110 * mm, 20 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
    ]))
    story.append(table)
    story.append(Spacer(1, 10 * mm))

    try:
        barcode = createBarcodeDrawing(
            "Code128", value=number, barHeight=16 * mm, humanReadable=True,
        )
        story.append(barcode)
    except Exception:  # noqa: BLE001 — el código de barras es opcional
        story.append(Paragraph(f"<b>{number}</b>", styles["Normal"]))

    doc.build(story)
    return buf.getvalue()
