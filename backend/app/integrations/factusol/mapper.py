"""Mappers puros CRM/FACTUSOL → FACTUSOL (Fase C · C-2-fix1).

Cambio de premisa (2026-08-04): una app externa ya replica cada pedido de
WooCommerce en FACTUSOL como **Pedido de Cliente (F_PCL)** con el cliente
asociado y todos los importes/IVAs ya calculados. BoHub ERP NO crea clientes
ni recalcula nada: solo **convierte el F_PCL que ya existe en factura F_FAC**
copiando los datos y añadiendo el CODFAC nuevo + el link PEDFAC.

Estrategia de mapeo — **transformación de sufijo**: las tablas de pedido y de
factura comparten la convención de columnas de DELSOL (mismo prefijo de campo,
distinto sufijo de tabla), así que copiamos cada columna sustituyendo el
sufijo (`*PCL → *FAC`, `*LPC → *LFA`). Esto arrastra automáticamente TODAS las
bandas de IVA (`NET1PCL→NET1FAC`, `PIVA1PCL→PIVA1FAC`, …), retenciones,
descuentos, etc., sin depender de enumerar 167 columnas. Las columnas de
estado/auditoría del pedido se excluyen (no tienen equivalente en F_FAC).

Las inyecciones (CODFAC/EJEFAC/TIPFAC/PEDFAC/FECFAC en la cabecera; CODLFA/
POSLFA/EJELFA en las líneas) se hacen DESPUÉS de la copia, sobreescribiendo lo
que herede la transformación de sufijo.
"""
from __future__ import annotations

from typing import Any

#: Tipo de documento de F_FAC: 2 = factura ordinaria (visto en Bomedia).
TIPFAC_FACTURA_ORDINARIA = 2

#: Columnas de F_PCL propias del PEDIDO (estado / auditoría) que NO se copian a
#: F_FAC — no tienen equivalente o descuadrarían la factura. Ampliable si el
#: EscribirRegistro de Bart rechaza alguna columna extra en la validación real.
PCL_ONLY_COLUMNS = frozenset({
    "ESTPCL",  # estado del pedido
    "IMPPCL",  # marca de impreso
    "USUPCL",  # usuario creación
    "USMPCL",  # usuario modificación
    "HORPCL",  # hora
    "PASPCL",  # pasado a factura / albarán
    "SUOPCL",  # servido / origen (si existe)
})

#: Columnas de F_LPC propias de la línea de pedido que NO se copian a F_LFA.
LPC_ONLY_COLUMNS: frozenset[str] = frozenset()


def _retag(column: str, from_suffix: str, to_suffix: str) -> str:
    """`NET1PCL`,'PCL','FAC' → `NET1FAC`. Sustituye solo el sufijo final."""
    return column[: -len(from_suffix)] + to_suffix


def pcl_row_to_fac_payload(
    pcl_row: dict[str, Any], codfac: str, pedfac_ref: str, ejercicio: str,
    *, fecha_emision: str, tipfac: int = TIPFAC_FACTURA_ORDINARIA,
) -> dict[str, Any]:
    """Fila F_PCL → payload de EscribirRegistro para F_FAC.

    Copia por sufijo (`*PCL → *FAC`, salvo `PCL_ONLY_COLUMNS`) e inyecta:
    - CODFAC  = el nuevo número secuencial (via `next_codfac`).
    - PEDFAC  = link al pedido origen, formato "<serie>-<codpcl_padded_6>".
    - EJEFAC  = ejercicio.
    - TIPFAC  = 2 (factura ordinaria).
    - FECFAC  = fecha de EMISIÓN (hoy), no la del pedido.
    """
    payload: dict[str, Any] = {}
    for col, val in pcl_row.items():
        if not col.endswith("PCL") or col in PCL_ONLY_COLUMNS:
            continue
        payload[_retag(col, "PCL", "FAC")] = val
    payload["CODFAC"] = codfac
    payload["EJEFAC"] = ejercicio
    payload["TIPFAC"] = tipfac
    payload["PEDFAC"] = pedfac_ref
    payload["FECFAC"] = fecha_emision
    return payload


def lpc_row_to_lfa_payload(
    lpc_row: dict[str, Any], codfac: str, posicion: int, ejercicio: str,
) -> dict[str, Any]:
    """Línea F_LPC → payload de EscribirRegistro para F_LFA (copia por sufijo
    `*LPC → *LFA`; inyecta CODLFA=codfac, POSLFA=posición, EJELFA)."""
    payload: dict[str, Any] = {}
    for col, val in lpc_row.items():
        if not col.endswith("LPC") or col in LPC_ONLY_COLUMNS:
            continue
        payload[_retag(col, "LPC", "LFA")] = val
    payload["CODLFA"] = codfac
    payload["POSLFA"] = posicion
    payload["EJELFA"] = ejercicio
    return payload
