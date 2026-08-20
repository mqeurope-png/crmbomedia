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

**ERP-E2 (2026-08-20) — el bug que rompía TODAS las emisiones.** El discovery
de ERP-E1 confirmó contra la base real que el payload enviaba 9 columnas
inexistentes:

    F_FAC: CEWFAC, EJEFAC, INCFAC, PENFAC, PPOFAC, SERFAC, SMDFAC
    F_LFA: ANULFA, PENLFA

Cinco venían de la copia por sufijo (F_PCL sí tiene `PENPCL`, `PPOPCL`,
`INCPCL`… pero F_FAC no tiene su contrapartida) y dos eran inyectadas a mano:

- **`EJEFAC`**: el ejercicio NO es columna de cabecera, es **parámetro de la
  llamada** (`write_record(..., ejercicio=YYYY)`). Lo demuestra `create_quote`,
  que crea proformas a diario sin mandar ningún `EJEPRE`. Ojo con la asimetría:
  en las LÍNEAS el ejercicio **sí** es columna (`EJELFA` existe en F_LFA).
- **`SERFAC`**: la serie tampoco es columna. En FACTUSOL la serie identifica la
  **empresa emisora** y va codificada en el RANGO del número de documento
  (serie N ⇒ `[N·100000, (N+1)·100000)`). Ver `service.next_codfac`.

Y bastaba UNA para que `EscribirRegistro` fallara ENTERO con
`BDEscribirRegistroError` sin decir cuál sobraba (gotcha nº 13, mismo patrón
que `EMAPRE` en C-4-fix4).

Por eso el payload ya no se manda «tal cual»: se filtra contra la lista
CANÓNICA de columnas reales (`FAC_COLUMNS` / `LFA_COLUMNS`, volcadas en vivo
por el discovery). Lo que no esté en la lista se descarta con un warning en el
log, en vez de reventar la emisión entera en producción.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Tipo de documento por defecto de F_FAC: '1' = factura ordinaria de Bomedia
#: (string, confirmado en la factura real 260695). Editable en el modal.
DEFAULT_TIPFAC = "1"


def _banded(prefix: str, count: int, suffix: str) -> tuple[str, ...]:
    """`('NET', 4, 'FAC')` → `('NET1FAC', …, 'NET4FAC')`. Las bandas de IVA /
    descuentos / portes van numeradas y son ~55 de las 167 columnas."""
    return tuple(f"{prefix}{i}{suffix}" for i in range(1, count + 1))


#: Columnas REALES de F_FAC (167), volcadas en vivo contra la base de Bomedia
#: por `scripts/factusol_discover_albaranes.py --check-invoice-pipeline`
#: (ERP-E1, 2026-08-20). **Fuente de verdad**: no añadir nada aquí que no se
#: haya visto en una fila real — escribir una columna inventada tumba la
#: emisión entera.
FAC_COLUMNS: frozenset[str] = frozenset(
    (
        "TIPFAC", "CODFAC", "REFFAC", "FECFAC", "ESTFAC", "ALMFAC", "AGEFAC",
        "PROFAC", "CLIFAC", "CNOFAC", "CDOFAC", "CPOFAC", "CCPFAC", "CPRFAC",
        "CNIFAC", "TIVFAC", "REQFAC", "TELFAC",
    )
    + _banded("NET", 4, "FAC")
    + _banded("PDTO", 4, "FAC") + _banded("IDTO", 4, "FAC")
    + _banded("PPPA", 4, "FAC") + _banded("IPPA", 4, "FAC")
    + _banded("PPOR", 4, "FAC") + _banded("IPOR", 4, "FAC")
    + _banded("PFIN", 4, "FAC") + _banded("IFIN", 4, "FAC")
    + _banded("BAS", 4, "FAC")
    + _banded("PIVA", 3, "FAC") + _banded("IIVA", 3, "FAC")
    + _banded("PREC", 3, "FAC") + _banded("IREC", 3, "FAC")
    + ("PRET1FAC", "IRET1FAC")
    + (
        "TOTFAC", "FOPFAC", "PRTFAC", "TPOFAC", "OB1FAC", "OB2FAC", "TDRFAC",
        "CDRFAC", "OBRFAC", "REPFAC", "EMBFAC", "AATFAC", "REAFAC", "PEDFAC",
        "FPEFAC", "COBFAC", "CREFAC", "TIRFAC", "CORFAC", "COPFAC", "TRAFAC",
        "VENFAC", "PRIFAC", "ASOFAC", "IMPFAC", "CBAFAC", "HORFAC", "COMFAC",
        "USUFAC", "USMFAC", "FAXFAC", "IMGFAC", "EFEFAC", "CAMFAC", "TRNFAC",
        "CISFAC", "TRCFAC", "EMAFAC", "PASFAC", "TPDFAC", "TIDFAC", "A1KFAC",
        "CEMFAC", "CPAFAC", "BNOFAC", "BENFAC", "BOFFAC", "BDCFAC", "BNUFAC",
    )
    + _banded("TIVA", 3, "FAC")
    + (
        "RCCFAC", "BIBFAC", "BICFAC", "EFSFAC", "EFVFAC", "CIEFAC", "GFEFAC",
        "TIFFAC", "TPVIDFAC", "TERFAC", "TFIFAC", "TFAFAC", "TREFAC", "CVIFAC",
        "DEPFAC", "FROFAC", "NASFAC", "EDRFAC", "DEMFAC", "FUMFAC", "ITBFAC",
        "STBFAC", "DECFAC", "SDCFAC", "TRZFAC", "EERFAC", "TRVFAC", "TOVFAC",
        "TVEFAC", "BTFFAC", "BCFFAC", "BCOFAC", "BCEFAC", "BNEFAC", "BCSFAC",
        "BTDFAC", "BRTFAC", "WHAFAC", "DVFFAC", "RDRFAC", "CIDFAC", "PDFFAC",
        "PRDFAC",
    )
)

#: Columnas REALES de F_LFA (36). `EJELFA` SÍ existe aquí — en las líneas el
#: ejercicio es columna, al revés que en la cabecera.
LFA_COLUMNS: frozenset[str] = frozenset({
    "TIPLFA", "CODLFA", "POSLFA", "ARTLFA", "DESLFA", "CANLFA", "DT1LFA",
    "DT2LFA", "DT3LFA", "PRELFA", "TOTLFA", "IVALFA", "DOCLFA", "DTPLFA",
    "DCOLFA", "COSLFA", "BULLFA", "COMLFA", "MEMLFA", "EJELFA", "ALTLFA",
    "ANCLFA", "FONLFA", "FFALFA", "FCOLFA", "IINLFA", "PIVLFA", "TIVLFA",
    "FIMLFA", "CE1LFA", "CE2LFA", "IMALFA", "SUMLFA", "NIMLFA", "TCOLFA",
    "RTILFA",
})

#: Las 9 que rompían producción. Se conservan nombradas para el test de
#: regresión y para que el próximo que lea esto sepa QUÉ falló exactamente.
PHANTOM_FAC_COLUMNS: frozenset[str] = frozenset({
    "CEWFAC", "EJEFAC", "INCFAC", "PENFAC", "PPOFAC", "SERFAC", "SMDFAC",
})
PHANTOM_LFA_COLUMNS: frozenset[str] = frozenset({"ANULFA", "PENLFA"})


def filter_to_real_columns(
    payload: dict[str, Any], allowed: frozenset[str], *, tabla: str
) -> dict[str, Any]:
    """Descarta del payload las columnas que no existen en la tabla destino.

    Red de seguridad contra el gotcha nº 13: una sola columna inventada hace
    fallar el `EscribirRegistro` ENTERO, y la API no dice cuál. Preferimos
    perder un dato que no se puede guardar a no poder facturar. Lo descartado
    se loguea para que se vea (y se corrija el mapeo) en vez de desaparecer."""
    kept = {k: v for k, v in payload.items() if k.upper() in allowed}
    dropped = sorted(set(payload) - set(kept))
    if dropped:
        logger.warning(
            "factusol: columnas descartadas del payload de %s (no existen en "
            "la tabla): %s", tabla, ", ".join(dropped),
        )
    return kept


@dataclass(frozen=True)
class FacturaOptions:
    """Opciones de emisión que el operador elige en el modal (como el diálogo
    «Nueva factura» del escritorio FACTUSOL). `None` = no tocar la columna que
    haya heredado la copia por sufijo; un valor la sobreescribe.

    - `tipfac`: tipo de documento (por defecto '1', factura ordinaria).
    - `serie`: **empresa emisora**. No es una columna: determina el rango de
      numeración del CODFAC (serie 5 ⇒ 5xxxxx). `None` → la resuelve el
      service (override por origen → default de ajustes → 5 Streamtec).
    - `fecfac`: fecha de emisión ISO (`YYYY-MM-DD`); `None` → la calcula el
      service (hoy).
    - `fopfac`: código de forma de pago (F_FOP).
    - `comfac`: observaciones / comentario de la factura.
    """

    #: ERP-E2-fix1 — `None` = heredar el tipo/serie del pedido (lo normal). Un
    #: valor explícito lo fuerza. Antes tenía default `'1'` y pisaba SIEMPRE la
    #: serie heredada: por eso el pedido `5-000005` salió facturado `1-100000`.
    tipfac: str | None = None
    serie: int | None = None
    fecfac: str | None = None
    fopfac: str | None = None
    comfac: str | None = None
    #: CODPCL del pedido de cliente origen → `PEDFAC` (trazabilidad). Lo
    #: rellena el service, no el modal.
    pedfac: Any = None

    @classmethod
    def from_payload(cls, data: dict[str, Any] | None) -> FacturaOptions:
        """Construye desde el dict del modal ignorando claves desconocidas.

        Tolerante a propósito: un job encolado ANTES del deploy de ERP-E2 lleva
        la clave `serfac` (la serie como string, que ya no existe). Sin esto el
        worker reventaría con TypeError al desencolarlo, cambiando un fallo de
        emisión por un crash del job."""
        if not data:
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known and v is not None}
        ignored = sorted(set(data) - known)
        if ignored:
            logger.info(
                "factusol: opciones de emisión ignoradas (obsoletas): %s",
                ", ".join(ignored),
            )
        return cls(**clean)


#: Columnas de F_PCL propias del PEDIDO (estado / auditoría) que NO se copian a
#: F_FAC — no tienen equivalente o descuadrarían la factura. El filtro
#: `filter_to_real_columns` cubre además las que sí se copiarían pero no
#: existen en F_FAC (PENFAC, PPOFAC, INCFAC, CEWFAC, SMDFAC — ERP-E2).
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
    - CODFAC = el nuevo número secuencial DENTRO de la serie (`next_codfac`).
    - TIPFAC = `options.tipfac` (por defecto '1', factura ordinaria).
    - FECFAC = `options.fecfac` o la fecha de EMISIÓN (hoy), no la del pedido.
    - FOPFAC / COMFAC = solo si el operador los indica (`options`).

    **No** inyecta:
    - `EJEFAC` ni `SERFAC` — no son columnas de F_FAC (ERP-E2). El ejercicio va
      como parámetro de `write_record`; la serie, codificada en el rango del
      CODFAC.
    - `PEDFAC` — la app externa no enlaza por PEDFAC; el enlace es REFFAC, ya
      presente en la copia.

    El resultado pasa por `filter_to_real_columns`: lo que la copia por sufijo
    arrastre y no exista en F_FAC se descarta con un warning en vez de tumbar
    el `EscribirRegistro` entero.
    """
    opts = options or FacturaOptions()
    payload: dict[str, Any] = {}
    for col, val in pcl_row.items():
        if not col.endswith("PCL") or col in PCL_ONLY_COLUMNS:
            continue
        payload[_retag(col, "PCL", "FAC")] = val
    payload["CODFAC"] = codfac
    # TIPFAC = la SERIE (empresa emisora). La copia por sufijo ya trajo el
    # `TIPPCL` del pedido: solo se pisa si el operador forzó otra en el modal.
    if opts.tipfac is not None:
        payload["TIPFAC"] = opts.tipfac
    elif opts.serie is not None:
        payload["TIPFAC"] = str(opts.serie)
    payload.setdefault("TIPFAC", DEFAULT_TIPFAC)
    payload["FECFAC"] = opts.fecfac or fecha_emision
    # ERP-E2-fix1 — trazabilidad factura→pedido. C-2-fix2 decidió NO ponerlo
    # porque la factura real de Bart lo tenía vacío; se reintroduce porque sin
    # él no hay forma de saber de qué pedido salió una factura del CRM (y el
    # CODPCL es justo lo que E3 necesita para la cadena PRE→ALB→FAC).
    if opts.pedfac is not None:
        payload["PEDFAC"] = opts.pedfac
    if opts.fopfac is not None:
        payload["FOPFAC"] = opts.fopfac
    if opts.comfac is not None:
        payload["COMFAC"] = opts.comfac
    return filter_to_real_columns(payload, FAC_COLUMNS, tabla="F_FAC")


def lpc_row_to_lfa_payload(
    lpc_row: dict[str, Any], codfac: str, posicion: int, ejercicio: str,
    *, serie: int | None = None,
) -> dict[str, Any]:
    """Línea F_LPC → payload de EscribirRegistro para F_LFA (copia por sufijo
    `*LPC → *LFA`; inyecta CODLFA=codfac, POSLFA=posición, EJELFA).

    `EJELFA` sí se inyecta: en las LÍNEAS el ejercicio es columna real (al
    contrario que `EJEFAC` en la cabecera). El filtro final descarta las que
    arrastraría la copia y no existen en F_LFA (`ANULFA`, `PENLFA` — ERP-E2)."""
    payload: dict[str, Any] = {}
    for col, val in lpc_row.items():
        if not col.endswith("LPC") or col in LPC_ONLY_COLUMNS:
            continue
        payload[_retag(col, "LPC", "LFA")] = val
    payload["CODLFA"] = codfac
    payload["POSLFA"] = posicion
    payload["EJELFA"] = ejercicio
    # La línea se ata a su cabecera por la clave COMPUESTA (tipo, código) —
    # así lo documenta el join de F_LPC/F_PCL. Sin el tipo correcto la línea
    # colgaría de la factura con el mismo número de OTRA serie.
    if serie is not None:
        payload["TIPLFA"] = str(serie)
    return filter_to_real_columns(payload, LFA_COLUMNS, tabla="F_LFA")
