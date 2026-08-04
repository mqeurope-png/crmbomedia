"""Mappers puros CRM/FACTUSOL → FACTUSOL (Fase C · C-2-fix2).

Cambio de premisa (2026-08-04): una app externa ya replica cada pedido de
WooCommerce en FACTUSOL como **Pedido de Cliente (F_PCL)** con el cliente
asociado y todos los importes/IVAs ya calculados. BoHub ERP NO crea clientes
ni recalcula nada: solo **convierte el F_PCL que ya existe en factura F_FAC**
copiando los datos y añadiendo el CODFAC nuevo.

Estrategia de mapeo — **transformación de sufijo**: las tablas de pedido y de
factura comparten la convención de columnas de DELSOL (mismo prefijo de campo,
distinto sufijo de tabla), así que copiamos cada columna sustituyendo el
sufijo (`*PCL → *FAC`, `*LPC → *LFA`). Esto arrastra automáticamente TODAS las
bandas de IVA (`NET1PCL→NET1FAC`, `PIVA1PCL→PIVA1FAC`, …), retenciones,
descuentos, el cliente (`CLIPCL→CLIFAC`) y **la referencia común
`REFPCL→REFFAC`** — que es el ÚNICO enlace pedido↔factura que usa la app
externa. Las columnas de estado/auditoría del pedido se excluyen.

C-2-fix2 (verificado contra la factura real 260695 de BOPRIN-99866):
- **`TIPFAC` es `'1'`** (string), no `2` — la factura ordinaria de Bomedia usa
  tipo 1. Editable por el operador en el modal de emisión.
- **NO se inyecta `PEDFAC`**: en la factura real está vacío; la app externa no
  enlaza factura↔pedido por PEDFAC, solo por `REFFAC` (que ya viaja en la
  copia por sufijo). Poner PEDFAC descuadraría respecto a las facturas que crea
  Bart a mano en el escritorio FACTUSOL.

Las inyecciones (CODFAC/EJEFAC/TIPFAC/FECFAC en la cabecera; CODLFA/POSLFA/
EJELFA en las líneas) y las opciones del operador (serie / forma de pago /
observaciones / fecha / tipo) se aplican DESPUÉS de la copia, sobreescribiendo
lo que herede la transformación de sufijo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Tipo de documento por defecto de F_FAC: '1' = factura ordinaria de Bomedia
#: (string, confirmado en la factura real 260695). Editable en el modal.
DEFAULT_TIPFAC = "1"


@dataclass(frozen=True)
class FacturaOptions:
    """Opciones de emisión que el operador elige en el modal (como el diálogo
    «Nueva factura» del escritorio FACTUSOL). `None` = no tocar la columna que
    haya heredado la copia por sufijo; un valor la sobreescribe.

    - `tipfac`: tipo de documento (por defecto '1', factura ordinaria).
    - `serfac`: serie de facturación (Bomedia no usa series → normalmente '').
    - `fecfac`: fecha de emisión ISO (`YYYY-MM-DD`); `None` → la calcula el
      service (hoy).
    - `fopfac`: código de forma de pago (F_FOP).
    - `comfac`: observaciones / comentario de la factura.
    """

    tipfac: str = DEFAULT_TIPFAC
    serfac: str | None = None
    fecfac: str | None = None
    fopfac: str | None = None
    comfac: str | None = None


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
    pcl_row: dict[str, Any], codfac: str, ejercicio: str,
    *, fecha_emision: str, options: FacturaOptions | None = None,
) -> dict[str, Any]:
    """Fila F_PCL → payload de EscribirRegistro para F_FAC.

    Copia por sufijo (`*PCL → *FAC`, salvo `PCL_ONLY_COLUMNS` — arrastra
    CLIFAC, TOTFAC, **REFFAC** y las bandas de IVA) e inyecta:
    - CODFAC = el nuevo número secuencial (via `next_codfac`).
    - EJEFAC = ejercicio.
    - TIPFAC = `options.tipfac` (por defecto '1', factura ordinaria).
    - FECFAC = `options.fecfac` o la fecha de EMISIÓN (hoy), no la del pedido.
    - SERFAC / FOPFAC / COMFAC = solo si el operador los indica (`options`).

    **No** inyecta PEDFAC (la app externa no enlaza por PEDFAC; el enlace es
    REFFAC, ya presente en la copia).
    """
    opts = options or FacturaOptions()
    payload: dict[str, Any] = {}
    for col, val in pcl_row.items():
        if not col.endswith("PCL") or col in PCL_ONLY_COLUMNS:
            continue
        payload[_retag(col, "PCL", "FAC")] = val
    payload["CODFAC"] = codfac
    payload["EJEFAC"] = ejercicio
    payload["TIPFAC"] = opts.tipfac
    payload["FECFAC"] = opts.fecfac or fecha_emision
    if opts.serfac is not None:
        payload["SERFAC"] = opts.serfac
    if opts.fopfac is not None:
        payload["FOPFAC"] = opts.fopfac
    if opts.comfac is not None:
        payload["COMFAC"] = opts.comfac
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
