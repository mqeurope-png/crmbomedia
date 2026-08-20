"""Operaciones FACTUSOL de alto nivel (Fase C · C-2-fix2).

Modelo (2026-08-04): una app externa ya replica cada pedido de WooCommerce en
FACTUSOL como Pedido de Cliente (F_PCL) con el cliente y todos los importes ya
calculados, y **a veces la factura ya la crea Bart a mano** en el escritorio
FACTUSOL. BoHub ERP **no crea clientes ni recalcula nada**:

- `check_factusol_status`: mira si el pedido ya tiene **factura** (F_FAC) o
  **albarán** (F_ALB) en FACTUSOL, por la referencia común REFFAC/REFALB.
- `get_and_link_factusol_status`: si ya hay factura, la **auto-vincula** al
  pedido (sin volver a crearla) y lo marca `invoiced_by_erp`.
- `emit_invoice`: solo si NO existe factura, localiza el F_PCL y lo **convierte
  en factura F_FAC** copiando los datos (+ CODFAC nuevo), y copia sus líneas
  F_LPC → F_LFA. Vuelve a comprobar la existencia de factura JUSTO antes de
  escribir (protección anti-duplicado ante carreras / creación manual).

Toda escritura FACTUSOL se serializa vía la cola `factusol:writes`
(worker-factusol, concurrency=1) para no pisar la numeración CODFAC.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.erp.models import (
    ERP_SETTINGS_SINGLETON_ID,
    ErpSettings,
    InvoiceStatus,
    Order,
    OrderStatusHistory,
    StatusDomain,
)
from app.integrations.factusol.client import FactusolClient, FactusolError
from app.integrations.factusol.mapper import (
    FacturaOptions,
    lpc_row_to_lfa_payload,
    pcl_row_to_fac_payload,
)
from app.models.crm import User

logger = logging.getLogger(__name__)


def ejercicio_for(session: Session) -> str:
    """Ejercicio (año fiscal) activo: preferencia al ajuste editable en
    `ErpSettings`, con fallback a la config."""
    from app.core.config import get_settings  # noqa: PLC0415

    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is not None and cfg.factusol_default_ejercicio:
        return cfg.factusol_default_ejercicio
    return get_settings().factusol_default_ejercicio


# ---------------------------------------------------------------------------
# Series = empresas emisoras
#
# ERP-E2 supuso que la serie iba codificada en el RANGO del número
# (`[N·100000, (N+1)·100000)`). **Era falso**, y la validación de Bart lo
# tumbó: el escritorio muestra los documentos como `<serie>-<número>` y hay
# una factura `2-526082` (526082 sería «serie 5» bajo el modelo de rangos) y
# un pedido `5-000005` (número 5 en serie 5, imposible con rangos).
#
# ERP-E2-fix1: la serie es **`TIPFAC` / `TIPPCL` / `TIPPRE`** — la columna
# «tipo» que ya existe en cada tabla de documento. Evidencia convergente:
#
#   1. La factura que emitió el CRM llevaba `TIPFAC="1"` (nuestro default) y
#      salió como `1-100000`. La serie mostrada = el TIPFAC enviado.
#   2. El join documentado de las líneas es COMPUESTO:
#      `F_LPC.TIPLPC = F_PCL.TIPPCL AND F_LPC.CODLPC = F_PCL.CODPCL`. Un
#      código que necesita el tipo para identificar el documento es
#      exactamente un contador POR SERIE.
#   3. Todas las proformas de Bomedia tienen `TIPPRE='1'` (C-4): serie 1.
#
# Por tanto el número NO es único global: es único por (tipo, código), y el
# contador de cada serie es `MAX(CODFAC donde TIPFAC=serie) + 1`.
#
# Y de ahí el bug de la validación: el mapper COPIABA `TIPPCL→TIPFAC` (serie
# correcta, heredada del pedido) y acto seguido lo pisaba con `opts.tipfac`
# ("1" por defecto). El pedido `5-000005` acabó facturado como `1-100000`.
# ---------------------------------------------------------------------------

#: Serie de último recurso para pedidos que NO existen en FACTUSOL (sin
#: F_PCL del que heredar). BoHub opera prioritariamente como Streamtec.
DEFAULT_SERIE = 5
#: Series válidas (el «tipo» de documento es un dígito).
VALID_SERIES = range(1, 10)


def coerce_serie(value: Any) -> int | None:
    """Normaliza a número de serie válido. Devuelve `None` si no lo es.

    Tolera la configuración heredada de C-2, donde la serie se guardaba como
    letra (`"A"`) para escribirla en la inexistente columna `SERFAC`: eso ya no
    significa nada, así que se ignora y se cae al default en vez de romper."""
    if value is None or isinstance(value, bool):
        return None
    try:
        serie = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return serie if serie in VALID_SERIES else None


def serie_of_row(row: dict[str, Any], columna: str) -> int | None:
    """Serie de una fila de documento leyendo su columna «tipo»
    (`TIPPCL` en F_PCL, `TIPFAC` en F_FAC…). `None` si no es un valor útil."""
    return coerce_serie(row.get(columna))


def series_config(session: Session) -> dict[str, Any]:
    """`{"default": 5, "by_source": {...}, "names": {...}}` desde
    `ErpSettings`. Dict vacío si aún no se ha configurado."""
    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is None or not cfg.factusol_series_json:
        return {}
    try:
        data = json.loads(cfg.factusol_series_json)
    except (TypeError, ValueError):
        logger.warning("factusol: factusol_series_json ilegible; se ignora")
        return {}
    return data if isinstance(data, dict) else {}


def series_names(session: Session) -> dict[int, str]:
    """`{5: "Streamtec", 2: "MQ Europe", …}` — mapping serie→empresa emisora
    configurable en `/erp/settings`. Alimenta el selector del modal."""
    raw = series_config(session).get("names")
    if not isinstance(raw, dict):
        return {}
    out: dict[int, str] = {}
    for key, value in raw.items():
        serie = coerce_serie(key)
        if serie is not None and str(value).strip():
            out[serie] = str(value).strip()
    return out


def default_serie(session: Session) -> int:
    """Serie por defecto de los ajustes; `DEFAULT_SERIE` (5) si no hay nada."""
    return coerce_serie(series_config(session).get("default")) or DEFAULT_SERIE


def resolve_serie(
    session: Session,
    order: Order,
    requested: int | None = None,
    pcl_row: dict[str, Any] | None = None,
) -> int:
    """Serie (empresa emisora) con la que facturar este pedido:
    elección explícita del modal → **serie del pedido en FACTUSOL (`TIPPCL`)**
    → `by_source[store_id]` → `by_source[origen]` → default de ajustes → 5.

    ERP-E2-fix1: la fuente primaria es el propio pedido. Cuando entra un
    pedido Woo, la app externa ya lo crea en FACTUSOL con SU serie (el
    `BOP-099917` de MOVIATICOS es el `5-000005`), así que la factura tiene que
    salir en esa misma serie. La config de `/erp/settings` queda solo como
    fallback para pedidos manuales que no existen en FACTUSOL — usarla como
    fuente primaria es lo que facturó un pedido de Streamtec como Bomedia."""
    explicit = coerce_serie(requested)
    if explicit is not None:
        return explicit
    if pcl_row is not None:
        inherited = serie_of_row(pcl_row, "TIPPCL")
        if inherited is not None:
            return inherited
        logger.warning(
            "factusol: el pedido de cliente %r no trae TIPPCL utilizable; "
            "se cae a la configuración.", pcl_row.get("CODPCL"),
        )
    conf = series_config(session)
    by_source = conf.get("by_source")
    by_source = by_source if isinstance(by_source, dict) else {}
    source = _status_value(order.external_source)
    for key in (order.store_id, source):
        if key:
            serie = coerce_serie(by_source.get(key))
            if serie is not None:
                return serie
    configured = coerce_serie(conf.get("default"))
    if configured is not None:
        return configured
    logger.info(
        "factusol: sin serie configurada para origen %r; se usa la %d",
        source, DEFAULT_SERIE,
    )
    return DEFAULT_SERIE


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def estpcl_invoiced_value(session: Session) -> str | None:
    """Valor de `F_PCL.ESTPCL` que marca el pedido como FACTURADO («Enviado»
    en el escritorio), o `None` si aún no se ha configurado.

    Deliberadamente NO se adivina. Los estados de FACTUSOL son códigos sin
    documentar — el mismo motivo por el que el guard de proformas no interpreta
    `ESTPRE` valor por valor (gotcha nº 17). Escribir un código inventado en la
    contabilidad de Bart es peor que no marcarlo: se vería «facturado» algo que
    no lo está, o al revés.

    El valor real lo revela el discovery (`--trace-order`, sección A4)
    comparando un pedido «Enviado» con uno «Pendiente»; se guarda luego en
    `factusol_series_json.estpcl_invoiced`."""
    raw = series_config(session).get("estpcl_invoiced")
    value = str(raw).strip() if raw is not None else ""
    return value or None


def mark_pcl_invoiced(
    client: FactusolClient,
    session: Session,
    *,
    codpcl: Any,
    serie: int,
    ejercicio: str,
) -> bool:
    """Marca el pedido de cliente como facturado en FACTUSOL. Devuelve `True`
    si se escribió.

    Sin el valor de estado configurado no toca nada y avisa: el pedido se
    quedará como «Pendiente» hasta que Bart lo configure, que es un problema
    visible y reversible, al revés que meter un código erróneo."""
    estado = estpcl_invoiced_value(session)
    if not estado:
        logger.warning(
            "factusol: no se marca el pedido %s como facturado — falta "
            "`estpcl_invoiced` en /erp/settings (lo revela el discovery "
            "--trace-order).", codpcl,
        )
        return False
    # `ActualizarRegistro` necesita la PK en el registro. La de un documento
    # es COMPUESTA (tipo, código): mandar solo CODPCL podría tocar el pedido
    # con el mismo número de OTRA serie.
    client.update_record(
        "F_PCL",
        {"TIPPCL": serie, "CODPCL": codpcl, "ESTPCL": estado},
        ejercicio=ejercicio,
    )
    logger.info(
        "factusol: pedido %s-%s marcado como facturado (ESTPCL=%r)",
        serie, codpcl, estado,
    )
    return True


def next_codfac(
    client: FactusolClient, ejercicio: str, serie: int = DEFAULT_SERIE
) -> str:
    """Contador de la SERIE = max(CODFAC de las facturas con TIPFAC=serie) + 1.

    ERP-E2-fix1. Es el mecanismo que confirmó Bart: al facturar, FACTUSOL mira
    el número más alto de ESA serie y pone el siguiente. Cada serie lleva su
    propio correlativo, así que el número solo es único por `(TIPFAC, CODFAC)`.

    El filtro por serie se hace **en Python**, no en el `filtro` de la API: no
    sabemos si `TIPFAC` viaja como `'5'` o como `5`, y un filtro que no casa
    devolvería `[]` en silencio (gotcha nº 1) — es decir, arrancaría el
    contador desde cero y machacaría facturas existentes.

    Una serie sin facturas arranca en 1 (primer documento de esa empresa).

    Se llama DENTRO de `emit_invoice`, justo antes de escribir la cabecera. La
    race lectura→escritura la evita el worker serializado (concurrency=1)."""
    rows = client.load_table(
        "F_FAC", filtro="1=1 ORDER BY CODFAC DESC", ejercicio=ejercicio,
    )
    in_series = [
        n for n in (
            _int_or_none(r.get("CODFAC"))
            for r in rows if serie_of_row(r, "TIPFAC") == serie
        )
        if n is not None
    ]
    if not in_series:
        logger.warning(
            "factusol: serie %d sin facturas en %s; se arranca el contador en "
            "1. Si la serie SÍ tiene facturas, revisa que TIPFAC codifique la "
            "serie (discovery --trace-order).", serie, ejercicio,
        )
        return "1"
    return str(max(in_series) + 1)


def _compose_ref(order_number: str, ref_prefix: str | None) -> str:
    """`BOPRIN-99866` (+ prefijo opcional) → `BOP-099866`. Es la referencia
    COMÚN que comparten pedido (REFPCL), albarán (REFALB) y factura (REFFAC):
    el número Woo con padding a 6 dígitos precedido del prefijo de la tienda.
    Si no se pasa prefijo, se deriva de las 3 primeras letras del segmento
    inicial del order_number."""
    parts = (order_number or "").split("-")
    number = parts[-1] if parts else ""
    prefix = (ref_prefix or (parts[0][:3] if parts and parts[0] else "")).upper()
    n = _int_or_none(number)
    num_str = f"{n:06d}" if n is not None else number
    return f"{prefix}-{num_str}"


def _store_ref_prefix(session: Session, order: Order) -> str | None:
    """Prefijo de referencia configurado en la tienda (IntegrationAccount.
    metadata_json['factusol_ref_prefix']); None si no está configurado."""
    if not order.store_id:
        return None
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    store = session.get(IntegrationAccount, order.store_id)
    if store is None or not store.metadata_json:
        return None
    try:
        meta = json.loads(store.metadata_json)
    except (TypeError, ValueError):
        return None
    prefix = meta.get("factusol_ref_prefix") if isinstance(meta, dict) else None
    return str(prefix).upper() if prefix else None


def find_pcl_by_order(
    client: FactusolClient, order: Order, ejercicio: str,
    *, ref_prefix: str | None = None,
) -> dict[str, Any] | None:
    """Localiza en F_PCL el pedido de cliente correspondiente al `order` del
    CRM (por REFPCL = prefijo-tienda + nº Woo). None si aún no existe."""
    ref = _compose_ref(order.order_number, ref_prefix)
    rows = client.load_table("F_PCL", filtro=f"REFPCL='{ref}'", ejercicio=ejercicio)
    return rows[0] if rows else None


def check_factusol_status(
    client: FactusolClient, order: Order, ejercicio: str,
    *, ref_prefix: str | None = None,
) -> dict[str, Any]:
    """¿El pedido ya tiene factura y/o albarán en FACTUSOL?

    Consulta F_FAC (por REFFAC) y F_ALB (por REFALB) usando la referencia
    común del pedido. Devuelve un dict con las filas encontradas (o None) sin
    escribir nada — la decisión de auto-vincular la toma la capa superior.
    """
    ref = _compose_ref(order.order_number, ref_prefix)
    fac_rows = client.load_table("F_FAC", filtro=f"REFFAC='{ref}'", ejercicio=ejercicio)
    alb_rows = client.load_table("F_ALB", filtro=f"REFALB='{ref}'", ejercicio=ejercicio)
    factura = fac_rows[0] if fac_rows else None
    albaran = alb_rows[0] if alb_rows else None
    return {
        "ref": ref,
        "has_factura": factura is not None,
        "factura": factura,
        "has_albaran": albaran is not None,
        "albaran": albaran,
    }


def get_and_link_factusol_status(
    session: Session, order: Order, client: FactusolClient, ejercicio: str,
    *, ref_prefix: str | None = None, actor: User | None = None,
) -> dict[str, Any]:
    """Consulta FACTUSOL y, si el pedido YA tiene factura, la auto-vincula
    (persiste) para no ofrecer «Emitir» sobre algo ya facturado. Devuelve el
    estado para el frontend:

    - `{"status": "invoiced", "codfac", "ref", "auto_linked": bool}`
    - `{"status": "albaran", "ref", "albaran_codigo"}`  (albarán sin factura)
    - `{"status": "pending", "ref"}`                     (ni factura ni albarán)
    """
    info = check_factusol_status(client, order, ejercicio, ref_prefix=ref_prefix)
    if info["has_factura"]:
        codfac = str(info["factura"].get("CODFAC"))
        auto_linked = False
        if not order.factusol_invoice_number:
            _auto_link_factura(
                session, order, codfac, ejercicio, ref=info["ref"], actor=actor,
            )
            session.commit()
            auto_linked = True
        return {"status": "invoiced", "codfac": codfac, "ref": info["ref"],
                "auto_linked": auto_linked}
    if info["has_albaran"]:
        alb = info["albaran"] or {}
        return {"status": "albaran", "ref": info["ref"],
                "albaran_codigo": (str(alb.get("CODALB"))
                                   if alb.get("CODALB") is not None else None)}
    return {"status": "pending", "ref": info["ref"]}


def emit_invoice(
    session: Session, order_id: str, client: FactusolClient,
    *, actor: User | None = None, options: FacturaOptions | None = None,
) -> dict[str, Any]:
    """Convierte el F_PCL del pedido en factura F_FAC (cabecera + líneas),
    marca el pedido `invoiced_by_erp`, guarda el CODFAC y escribe el historial.

    Anti-duplicado: JUSTO antes de escribir vuelve a consultar F_FAC por REFFAC;
    si la factura ya existe (creada a mano por Bart o por una carrera), la
    **auto-vincula** en vez de crear un duplicado.

    NO crea cliente ni recalcula importes: copia de F_PCL/F_LPC. Atómico: si
    falla una línea, borra la factura a medias en FACTUSOL (compensación) y
    hace rollback en la BD.
    """
    order = session.get(Order, order_id, options=[selectinload(Order.lines)])
    if order is None:
        raise FactusolError(f"Order {order_id!r} no existe")
    inv = _status_value(order.invoice_status)
    if order.factusol_invoice_number or inv == InvoiceStatus.INVOICED_BY_ERP.value:
        raise FactusolError("El pedido ya tiene factura en FACTUSOL")
    if inv == InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value:
        raise FactusolError("El pedido está marcado como facturado fuera del ERP")

    ejercicio = ejercicio_for(session)
    ref_prefix = _store_ref_prefix(session, order)

    # Anti-duplicado: ¿existe ya la factura en FACTUSOL? (creada a mano o carrera)
    existing = check_factusol_status(client, order, ejercicio, ref_prefix=ref_prefix)
    if existing["has_factura"]:
        codfac = str(existing["factura"].get("CODFAC"))
        _auto_link_factura(
            session, order, codfac, ejercicio, ref=existing["ref"], actor=actor,
        )
        session.commit()
        logger.info("factusol: factura ya existía, auto-vinculada order=%s codfac=%s",
                    order_id, codfac)
        return {"codfac": codfac, "ejercicio": ejercicio, "lines": 0,
                "already_existed": True}

    pcl = find_pcl_by_order(client, order, ejercicio, ref_prefix=ref_prefix)
    if pcl is None:
        raise FactusolError(
            f"Este pedido ({order.order_number}) aún no está en FACTUSOL. La app "
            "WooCommerce→FACTUSOL debe importarlo antes de facturar."
        )
    codpcl = pcl.get("CODPCL")
    lpc_rows = client.load_table(
        "F_LPC", filtro=f"CODLPC={codpcl}", ejercicio=ejercicio,
    )

    # ERP-E2-fix1: la serie se HEREDA del pedido que ya existe en FACTUSOL
    # (`TIPPCL`); la config solo actúa si el pedido no está allí. El modal
    # sigue pudiendo forzarla.
    serie = resolve_serie(
        session, order, options.serie if options is not None else None,
        pcl_row=pcl,
    )
    options = (
        replace(options, serie=serie, pedfac=codpcl) if options is not None
        else FacturaOptions(serie=serie, pedfac=codpcl)
    )
    codfac = next_codfac(client, ejercicio, serie)
    fecha_emision = datetime.now(UTC).date().isoformat()
    cabecera = pcl_row_to_fac_payload(
        pcl, codfac, ejercicio, fecha_emision=fecha_emision, options=options,
    )
    lineas = [
        lpc_row_to_lfa_payload(row, codfac, i + 1, ejercicio, serie=serie)
        for i, row in enumerate(lpc_rows)
    ]

    client.write_record("F_FAC", cabecera, ejercicio=ejercicio)
    try:
        for linea in lineas:
            client.write_record("F_LFA", linea, ejercicio=ejercicio)
    except FactusolError:
        # Compensación: borra líneas + cabecera para no dejar factura a medias.
        # El filtro DEBE incluir el tipo: el número solo es único por (tipo,
        # código), así que borrar por CODFAC a secas se llevaría por delante la
        # factura homónima de otra serie.
        try:
            client.delete_records(
                "F_LFA", f"TIPLFA='{serie}' AND CODLFA='{codfac}'",
                ejercicio=ejercicio,
            )
            client.delete_records(
                "F_FAC", f"TIPFAC='{serie}' AND CODFAC='{codfac}'",
                ejercicio=ejercicio,
            )
        except FactusolError:
            logger.warning(
                "factusol: no se pudo limpiar la factura %s a medias", codfac,
                exc_info=True,
            )
        session.rollback()
        raise

    # ERP-E2-fix1: cerrar el pedido en FACTUSOL. Va DESPUÉS de la factura y no
    # aborta la emisión si falla — la factura ya está escrita y correcta; el
    # estado del pedido es recuperable a mano y no justifica revertirla.
    pcl_marked = False
    try:
        pcl_marked = mark_pcl_invoiced(
            client, session, codpcl=codpcl, serie=serie, ejercicio=ejercicio,
        )
    except FactusolError:
        logger.warning(
            "factusol: factura %s emitida pero el pedido %s no se pudo marcar "
            "como facturado", codfac, codpcl, exc_info=True,
        )

    now = datetime.now(UTC)
    order.invoice_status = InvoiceStatus.INVOICED_BY_ERP.value
    order.factusol_invoice_number = codfac
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.INVOICE,
        from_status=inv, to_status=InvoiceStatus.INVOICED_BY_ERP.value,
        changed_at=now, changed_by_user_id=(actor.id if actor else None),
        reason="Factura emitida en FACTUSOL",
        metadata_json=json.dumps({
            "factusol_codfac": codfac, "factusol_codpcl": str(codpcl),
            "factusol_ref": cabecera.get("REFFAC"), "factusol_ejercicio": ejercicio,
            # Qué empresa emitió. La serie es TIPFAC, pero guardarla aquí evita
            # tener que releer la factura para saberlo.
            "factusol_serie": serie,
            "factusol_pcl_marked": pcl_marked,
        }),
    ))
    _log_sync(session, order, codfac, str(codpcl), ejercicio, len(lineas))
    session.commit()
    return {"codfac": codfac, "codpcl": str(codpcl), "ejercicio": ejercicio,
            "lines": len(lineas), "serie": serie, "pcl_marked": pcl_marked}


def _auto_link_factura(
    session: Session, order: Order, codfac: str, ejercicio: str,
    *, ref: str | None, actor: User | None = None,
) -> None:
    """Marca el pedido como facturado apuntando a un CODFAC que YA existe en
    FACTUSOL (no escribe nada en FACTUSOL). Escribe historial + SyncLog."""
    inv = _status_value(order.invoice_status)
    now = datetime.now(UTC)
    order.invoice_status = InvoiceStatus.INVOICED_BY_ERP.value
    order.factusol_invoice_number = str(codfac)
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.INVOICE,
        from_status=inv, to_status=InvoiceStatus.INVOICED_BY_ERP.value,
        changed_at=now, changed_by_user_id=(actor.id if actor else None),
        reason="Factura localizada en FACTUSOL (vinculada automáticamente)",
        metadata_json=json.dumps({
            "factusol_codfac": str(codfac), "factusol_ref": ref,
            "factusol_ejercicio": ejercicio, "source": "auto_linked_from_factusol",
        }),
    ))
    _log_sync(session, order, str(codfac), "-", ejercicio, 0,
              operation="factusol_link_invoice",
              message=f"Factura {codfac} ya existente en FACTUSOL → vinculada "
                      f"(ref {ref}, ej. {ejercicio})")


def _log_sync(
    session: Session, order: Order, codfac: str, codpcl: str,
    ejercicio: str, lines: int, *,
    operation: str = "factusol_emit_invoice", message: str | None = None,
) -> None:
    from app.models.crm import (  # noqa: PLC0415
        ExternalSystem,
        SyncLog,
        SyncStatus,
        SyncTrigger,
    )

    now = datetime.now(UTC)
    session.add(SyncLog(
        system=ExternalSystem.FACTUSOL,
        account_id=order.store_id,
        operation=operation,
        status=SyncStatus.SUCCESS.value,
        started_at=now, finished_at=now,
        records_processed=1,
        triggered_by=SyncTrigger.MANUAL.value,
        message=message or (
            f"F_PCL {codpcl} → F_FAC {codfac} (ej. {ejercicio}, {lines} líneas)"
        ),
    ))


def _status_value(v: object) -> str:
    return getattr(v, "value", v)  # type: ignore[return-value]
