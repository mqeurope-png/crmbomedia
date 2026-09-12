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
import re
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
    PaymentStatus,
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


# --- marcado del documento de ORIGEN al convertir (E3-B-fix3) --------------
#
# FACTUSOL escritorio marca el documento de origen al convertirlo (el
# presupuesto pasa a «Aceptado», el albarán a «Facturado» en la columna
# FACT.); BoHub debe hacer lo mismo. Los valores de ESTPRE/ESTALB están
# CONFIRMADOS en la interfaz del escritorio de Bart (26-ago-2026); el de
# ESTPCL se confirmó en E2. Configurables en `factusol_series_json` por si
# otro ejercicio/versión usara códigos distintos; un valor VACÍO explícito
# desactiva el marcado de ese tipo (criterio E2).
#
# Por tipo de origen: (tabla, col. tipo, col. código, col. estado, clave de
# configuración, default). `estpcl_invoiced` mantiene el contrato de E2 —
# sin default: no configurado = no marcar (comportamiento ya validado).
ORIGIN_MARK_SPECS: dict[str, tuple[str, str, str, str, str, str | None]] = {
    "pedidos": ("F_PCL", "TIPPCL", "CODPCL", "ESTPCL", "estpcl_invoiced", None),
    "presupuestos": (
        "F_PRE", "TIPPRE", "CODPRE", "ESTPRE", "estpre_accepted", "1",
    ),
    "albaranes": (
        "F_ALB", "TIPALB", "CODALB", "ESTALB", "estalb_invoiced", "1",
    ),
}


def _estado_str(value: Any) -> str:
    """Normaliza un estado para comparar ('1.0' → '1', None → '')."""
    v = str(value).strip() if value is not None else ""
    if v.endswith(".0") and v[:-2].isdigit():
        v = v[:-2]
    return v


def origin_mark_value(session: Session, source_type: str) -> str | None:
    """Valor de estado con el que marcar el origen `source_type`, o `None`
    si no hay que marcar (clave EXPLÍCITAMENTE vacía en la config, o —solo
    para pedidos, contrato E2— sin configurar)."""
    _tabla, _tip, _cod, _est, key, default = ORIGIN_MARK_SPECS[source_type]
    conf = series_config(session)
    if key in conf:
        raw = conf.get(key)
        value = str(raw).strip() if raw is not None else ""
        return value or None
    return default


def mark_origin_converted(
    client: FactusolClient,
    session: Session,
    *,
    source_type: str,
    serie: Any,
    codigo: Any,
    ejercicio: str,
    current_estado: Any = None,
) -> tuple[bool, str | None]:
    """Marca el documento de ORIGEN como consumido tras crear su hijo:
    presupuesto → «Aceptado», albarán → «Facturado», pedido → «Enviado».

    Es el ÚNICO punto de marcado — la emisión E2 (`mark_pcl_invoiced`) y las
    conversiones de `chain.convert_document` pasan por aquí. Devuelve
    `(marcado, motivo)`: `(True, None)` si se escribió o ya tenía el estado
    (idempotente); `(False, motivo)` si se saltó o falló. **NUNCA lanza**:
    el documento hijo ya existe legítimamente en la contabilidad y este
    paso posterior no debe ponerlo en riesgo ni disparar compensaciones."""
    tabla, tip_col, cod_col, est_col, key, _default = (
        ORIGIN_MARK_SPECS[source_type]
    )
    estado = origin_mark_value(session, source_type)
    if not estado:
        motivo = (
            f"falta `{key}` en /erp/settings — el {tabla} {serie}-{codigo} "
            "se queda sin marcar"
        )
        logger.warning("factusol: %s", motivo)
        return False, motivo
    return _write_document_estado(
        client, tabla=tabla, tip_col=tip_col, cod_col=cod_col, est_col=est_col,
        serie=serie, codigo=codigo, estado=estado, ejercicio=ejercicio,
        current_estado=current_estado, what=tabla,
    )


def _write_document_estado(
    client: FactusolClient,
    *,
    tabla: str,
    tip_col: str,
    cod_col: str,
    est_col: str,
    serie: Any,
    codigo: Any,
    estado: str,
    ejercicio: str,
    current_estado: Any = None,
    what: str = "documento",
) -> tuple[bool, str | None]:
    """Escritura ÚNICA de un estado en FACTUSOL por clave COMPUESTA
    (tipo, código). Idempotente y **nunca lanza**. Es el punto compartido por
    el marcado de ORIGEN (`mark_origin_converted`) y el de COBRO de facturas
    (`mark_invoice_payment`): no hay dos implementaciones distintas escribiendo
    estados. Devuelve `(True, None)` si escribió o ya tenía el estado;
    `(False, motivo)` si falló."""
    # Idempotencia: si ya tiene el estado destino, no reescribir.
    if current_estado is not None and _estado_str(current_estado) == _estado_str(estado):
        logger.debug(
            "factusol: %s %s-%s ya tenía %s=%s; no se reescribe",
            what, serie, codigo, est_col, estado,
        )
        return True, None
    try:
        # `ActualizarRegistro` necesita la PK en el registro. La de un
        # documento es COMPUESTA (tipo, código): mandar solo el código
        # podría tocar el documento homónimo de OTRA serie.
        client.update_record(
            tabla,
            {tip_col: serie, cod_col: codigo, est_col: estado},
            ejercicio=ejercicio,
        )
    except Exception as exc:  # noqa: BLE001 — solo aviso, nunca romper
        motivo = (
            f"no se pudo marcar el/la {what} {serie}-{codigo} "
            f"({est_col}={estado}): {str(exc)[:200]}"
        )
        logger.warning("factusol: %s", motivo, exc_info=True)
        return False, motivo
    logger.info(
        "factusol: %s %s-%s → %s=%r", what, serie, codigo, est_col, estado,
    )
    return True, None


# --- ERP-F3: estado de COBRO de las facturas (F_FAC.ESTFAC) -----------------
#
# Confirmado por Bart: 0 = pendiente de cobro, 2 = cobrada. Configurable en
# /erp/settings por si otro ejercicio/versión usara códigos distintos; un
# valor VACÍO explícito desactiva el marcado (mismo criterio que el origen).
_INVOICE_PAYMENT_SPEC = ("F_FAC", "TIPFAC", "CODFAC", "ESTFAC")
INVOICE_PAID_KEY = "estfac_cobrada"
INVOICE_PENDING_KEY = "estfac_pendiente"
INVOICE_PAID_DEFAULT = "2"
INVOICE_PENDING_DEFAULT = "0"


def invoice_payment_value(session: Session, *, paid: bool) -> str | None:
    """Valor de `ESTFAC` con el que marcar «cobrada» (paid=True) o
    «pendiente» (paid=False), leído de /erp/settings con el default
    confirmado. `None` si la clave está EXPLÍCITAMENTE vacía (no marcar)."""
    conf = series_config(session)
    key = INVOICE_PAID_KEY if paid else INVOICE_PENDING_KEY
    default = INVOICE_PAID_DEFAULT if paid else INVOICE_PENDING_DEFAULT
    if key in conf:
        raw = conf.get(key)
        value = str(raw).strip() if raw is not None else ""
        return value or None
    return default


def mark_invoice_payment(
    client: FactusolClient,
    session: Session,
    *,
    serie: Any,
    codigo: Any,
    paid: bool,
    ejercicio: str,
    current_estado: Any = None,
) -> tuple[bool, str | None]:
    """Marca la factura (F_FAC) como cobrada/pendiente escribiendo `ESTFAC`
    por clave COMPUESTA `(TIPFAC, CODFAC)` — nunca solo por número. Reutiliza
    el escritor ÚNICO `_write_document_estado`: idempotente y nunca lanza.
    Devuelve `(marcada, motivo)`; `(False, motivo)` si la config está vacía o
    falló la escritura."""
    tabla, tip_col, cod_col, est_col = _INVOICE_PAYMENT_SPEC
    estado = invoice_payment_value(session, paid=paid)
    if not estado:
        key = INVOICE_PAID_KEY if paid else INVOICE_PENDING_KEY
        motivo = (
            f"falta `{key}` en /erp/settings — la factura {serie}-{codigo} "
            "se queda sin marcar"
        )
        logger.warning("factusol: %s", motivo)
        return False, motivo
    return _write_document_estado(
        client, tabla=tabla, tip_col=tip_col, cod_col=cod_col, est_col=est_col,
        serie=serie, codigo=codigo, estado=estado, ejercicio=ejercicio,
        current_estado=current_estado, what="factura",
    )


def _auto_mark_paid_enabled(session: Session) -> bool:
    """ERP-F3 — ¿está activado el auto-marcado de cobro al emitir? (off por
    defecto: es una afirmación contable automática)."""
    return bool(
        series_config(session).get("auto_mark_paid_when_order_paid", False)
    )


def _order_is_paid(order: Order) -> bool:
    """El pedido «consta como pagado» — estrictamente PAID (un pago parcial o
    un crédito aprobado NO se auto-marcan como cobrados)."""
    return _status_value(order.payment_status) == PaymentStatus.PAID.value


def mark_pcl_invoiced(
    client: FactusolClient,
    session: Session,
    *,
    codpcl: Any,
    serie: int,
    ejercicio: str,
) -> bool:
    """Marca el pedido de cliente como facturado en FACTUSOL (E2). Desde
    E3-B-fix3 es un wrapper de compatibilidad sobre el helper ÚNICO
    `mark_origin_converted` — la emisión desde pedido y las conversiones de
    la cadena comparten implementación. Devuelve `True` si se escribió."""
    marked, _motivo = mark_origin_converted(
        client, session, source_type="pedidos", serie=serie, codigo=codpcl,
        ejercicio=ejercicio,
    )
    return marked


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
    # Guarda anti-colisión: el número se escribe contra la clave COMPUESTA
    # `(TIPFAC, CODFAC)`, así que si el `max+1` estuviera ocupado en esta serie
    # (hueco raro, escritura concurrente, carga manual) el EscribirRegistro
    # moriría con `BDExisteRegistro`. Avanzamos al primer libre en vez de
    # fallar — es lo que haría el escritorio.
    ocupados = set(in_series)
    candidato = max(in_series) + 1
    while candidato in ocupados:
        logger.warning(
            "factusol: (TIPFAC=%d, CODFAC=%d) ya existe; se avanza al "
            "siguiente libre de la serie.", serie, candidato,
        )
        candidato += 1
    return str(candidato)


def record_emit_failure(
    session: Session,
    order_id: str,
    error: str,
    *,
    actor_user_id: str | None = None,
) -> bool:
    """Deja constancia en la BD de que la emisión falló. Devuelve `True` si
    escribió algo.

    ERP-E2-fix2. `emit_invoice` es atómico: si falla, NO marca el pedido — lo
    que dejaba la ficha en «Generando…» indefinidamente, esperando un job
    muerto. Aquí se escribe una entrada de historial con el error real de
    FACTUSOL (`BDExisteRegistro`, `BDEscribirRegistroError`…) para que el
    operador vea qué pasó y pueda reintentar.

    El estado NO se mueve a un `invoice_failed` nuevo: se deja tal cual estaba
    (re-emitible). Un estado de error extra obligaría a que alguien lo
    «desatasque» antes de reintentar, y el pedido ya está en un estado válido
    desde el que se puede volver a emitir."""
    order = session.get(Order, order_id)
    if order is None:
        return False
    inv = _status_value(order.invoice_status)
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.INVOICE,
        from_status=inv, to_status=inv,   # el estado no se mueve
        changed_at=datetime.now(UTC), changed_by_user_id=actor_user_id,
        reason="Error al emitir la factura en FACTUSOL",
        metadata_json=json.dumps({"error": error[:500], "outcome": "failed"}),
    ))
    _log_sync(
        session, order, "-", "-", ejercicio_for(session), 0,
        operation="factusol_emit_invoice",
        message=f"ERROR emitiendo factura: {error[:300]}",
        success=False,
    )
    session.commit()
    return True


def _compose_ref(order_number: str, ref_prefix: str | None) -> str:
    """`BOPRIN-99866` (+ prefijo opcional) → `BOP-099866`. Es la referencia
    COMÚN que comparten pedido (REFPCL), albarán (REFALB) y factura (REFFAC):
    el número Woo con padding a 6 dígitos precedido del prefijo de la tienda.
    Si no se pasa prefijo, se deriva de las 3 primeras letras del segmento
    inicial del order_number."""
    parts = (order_number or "").split("-")
    number = parts[-1] if parts else ""
    prefix = (ref_prefix or derived_ref_prefix(order_number)).upper()
    n = _int_or_none(number)
    num_str = f"{n:06d}" if n is not None else number
    return f"{prefix}-{num_str}"


#: Prefijo de referencia válido (`BOP`, `FLE`, `ART`…): letras/dígitos, 1-6.
REF_PREFIX_RE = re.compile(r"^[A-Z0-9]{1,6}$")


def derived_ref_prefix(order_number: str) -> str:
    """Prefijo que se DERIVA del número de pedido cuando la tienda no tiene
    ninguno configurado (`FLUXLA-5789` → `FLU`). Ojo: la app Woo→FACTUSOL
    puede usar otro (`FLE`), y entonces hay que configurarlo."""
    parts = (order_number or "").split("-")
    return (parts[0][:3] if parts and parts[0] else "").upper()


def configured_ref_prefixes(session: Session) -> dict[str, str]:
    """`{slug_tienda: PREFIJO}` configurado en Ajustes ERP
    (`factusol_series_json.ref_prefix_by_store`, editable en `/erp/settings`).
    Complementa a `IntegrationAccount.metadata_json['factusol_ref_prefix']`,
    que no tiene UI (solo se podía fijar a mano en la BD)."""
    raw = series_config(session).get("ref_prefix_by_store")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for slug, prefix in raw.items():
        key = str(slug or "").strip().lower()
        value = str(prefix or "").strip().upper()
        if key and REF_PREFIX_RE.match(value):
            out[key] = value
    return out


def _store_ref_prefix(session: Session, order: Order) -> str | None:
    """Prefijo de referencia de la tienda del pedido: el de
    `IntegrationAccount.metadata_json['factusol_ref_prefix']` y, si no lo
    tiene, el configurado por tienda en Ajustes ERP. None si no hay ninguno
    (se derivará del número de pedido)."""
    if not order.store_id:
        return None
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    store = session.get(IntegrationAccount, order.store_id)
    if store is None:
        return None
    prefix = store_metadata_ref_prefix(store)
    if prefix:
        return prefix
    return configured_ref_prefixes(session).get(str(store.account_id or "").lower())


def store_metadata_ref_prefix(store: Any) -> str | None:
    """`factusol_ref_prefix` del `metadata_json` de la cuenta de tienda."""
    if store is None or not getattr(store, "metadata_json", None):
        return None
    try:
        meta = json.loads(store.metadata_json)
    except (TypeError, ValueError):
        return None
    prefix = meta.get("factusol_ref_prefix") if isinstance(meta, dict) else None
    return str(prefix).strip().upper() if prefix else None


def pcl_ref_for_order(session: Session, order: Order) -> str:
    """Referencia común (`REFPCL`) con la que se busca el F_PCL de este pedido
    web: prefijo de la tienda (o derivado) + nº Woo con padding a 6."""
    return _compose_ref(order.order_number, _store_ref_prefix(session, order))


def find_pcl_by_order(
    client: FactusolClient, order: Order, ejercicio: str,
    *, ref_prefix: str | None = None,
) -> dict[str, Any] | None:
    """Localiza en F_PCL el pedido de cliente correspondiente al `order` del
    CRM (por REFPCL = prefijo-tienda + nº Woo). None si aún no existe."""
    ref = _compose_ref(order.order_number, ref_prefix)
    rows = client.load_table("F_PCL", filtro=f"REFPCL='{ref}'", ejercicio=ejercicio)
    return rows[0] if rows else None


def probe_pcl_refs_by_number(
    client: FactusolClient, order_number: str, ejercicio: str,
) -> list[str]:
    """Diagnóstico cuando `find_pcl_by_order` no encuentra nada: referencias
    `REFPCL` que existen en F_PCL con el MISMO número Woo bajo OTRO prefijo
    (`FLE-005789` cuando se buscó `FLU-005789`). Solo lectura y best-effort
    (un fallo del sondeo no oculta el 404 de siempre). Nunca se usa para
    elegir un documento — un pedido HOMÓNIMO de otra tienda comparte número —
    solo para decirle al operador qué prefijo tiene que configurar."""
    parts = (order_number or "").split("-")
    n = _int_or_none(parts[-1] if parts else "")
    if n is None:
        return []
    suffix = f"-{n:06d}"
    try:
        rows = client.load_table(
            "F_PCL", filtro=f"REFPCL LIKE '%{suffix}'", ejercicio=ejercicio,
        )
    except FactusolError as exc:
        logger.warning("factusol: sondeo REFPCL LIKE '%%%s' KO: %s", suffix, exc)
        return []
    refs: list[str] = []
    for row in rows:
        ref = str(row.get("REFPCL") or "").strip().upper()
        if ref.endswith(suffix) and ref not in refs:
            refs.append(ref)
    return refs


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

    # Fase 2: el pedido tiene el albarán que BoHub creó al convertir → la
    # factura sale de ESE albarán con la cadena E3-B (no hay F_PCL por REFPCL
    # para un PRO-/PCL-). La factura queda vinculada y, si el pago se apuntó
    # al convertir (opción B), se registra el cobro F-4-B.
    if order.factusol_albaran_number:
        return _emit_from_albaran(
            session, order, client, ejercicio=ejercicio, actor=actor,
            options=options,
        )

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
    # BUGFIX: la línea de pedido solo es única por la pareja (TIPLPC, CODLPC) —
    # el join documentado es `F_LPC.TIPLPC = F_PCL.TIPPCL AND F_LPC.CODLPC =
    # F_PCL.CODPCL`. Filtrar por CODLPC a secas arrastraba las líneas del pedido
    # HOMÓNIMO de otra serie (mismo «Nº de su pedido»/CODLPC, distinta empresa):
    # esas líneas ajenas se copiaban a la F_LFA de la factura emitida, de modo
    # que el detalle salía cruzado (la cabecera sí era correcta, porque se copia
    # del pedido concreto). Se acota por la serie del propio pedido. El `None`
    # es defensivo: nunca descarta la línea legítima si no trae TIPLPC, solo las
    # de OTRA serie (que sí lo traen).
    pedido_serie = serie_of_row(pcl, "TIPPCL")
    lpc_rows = client.load_table(
        "F_LPC", filtro=f"CODLPC={codpcl}", ejercicio=ejercicio,
    )
    if pedido_serie is not None:
        lpc_rows = [
            row for row in lpc_rows
            if serie_of_row(row, "TIPLPC") in (pedido_serie, None)
        ]

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

    # ERP-F3 (Parte D): auto-marcar la factura como COBRADA al emitirla SI el
    # pedido ya constaba pagado (web con pago al comprar) y el ajuste está
    # activado. Nunca para pedidos manuales o pendientes de pago; desactivado
    # por defecto. `mark_invoice_payment` no lanza — no arriesga la factura.
    if _auto_mark_paid_enabled(session) and _order_is_paid(order):
        marked, motivo = mark_invoice_payment(
            client, session, serie=serie, codigo=codfac, paid=True,
            ejercicio=ejercicio,
        )
        if marked:
            logger.info("factusol: factura %s auto-marcada cobrada (pedido "
                        "%s ya pagado)", codfac, order.order_number)
        else:
            logger.warning("factusol: no se pudo auto-marcar cobrada la "
                           "factura %s: %s", codfac, motivo)

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
    # Fase 2 (opción B): pago apuntado al convertir → ahora que existe la
    # factura, cobro F-4-B (solo F_LCO + ESTFAC=2). Nunca lanza.
    cobro = None
    try:
        from app.erp.factusol_albaran import register_pending_collection  # noqa: PLC0415

        cobro = register_pending_collection(
            session, client, order, serie=serie, codigo=int(codfac),
            ejercicio=ejercicio, actor_user_id=(actor.id if actor else None),
        )
    except Exception:  # noqa: BLE001 — la factura ya está emitida
        logger.warning("factusol: cobro apuntado del pedido %s no registrado",
                       order.order_number, exc_info=True)

    _log_sync(session, order, codfac, str(codpcl), ejercicio, len(lineas))
    session.commit()
    return {"codfac": codfac, "codpcl": str(codpcl), "ejercicio": ejercicio,
            "lines": len(lineas), "serie": serie, "pcl_marked": pcl_marked,
            "cobro": cobro}


def _emit_from_albaran(
    session: Session, order: Order, client: FactusolClient,
    *, ejercicio: str, actor: User | None, options: FacturaOptions | None,
) -> dict[str, Any]:
    """Fase 2 — factura del pedido a partir de SU albarán (`albaranes →
    facturas` de la cadena E3-B, probada en producción). Las opciones del
    modal (serie, fecha, forma de pago, observaciones) pisan lo copiado. La
    cadena vincula la factura al pedido y registra el cobro apuntado
    (`on_invoice_created`); si ese enganche fallara, se vincula aquí."""
    from app.integrations.factusol.chain import convert_document  # noqa: PLC0415
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415

    head, _, tail = str(order.factusol_albaran_number).partition("-")
    serie_alb, codigo_alb = coerce_serie(head), _int_or_none(tail)
    if serie_alb is None or codigo_alb is None:
        raise FactusolError(
            f"El nº de albarán del pedido no es válido: {order.factusol_albaran_number!r}"
        )
    overrides: dict[str, Any] = {}
    if options is not None:
        if options.fopfac:
            overrides["FOPFAC"] = options.fopfac
        if options.comfac:
            overrides["COMFAC"] = options.comfac
    result = convert_document(
        session, client, source_type="albaranes", target_type="facturas",
        tip=serie_alb, cod=codigo_alb, ejercicio=ejercicio,
        serie_override=(options.serie if options is not None else None),
        fecha=(options.fecfac if options is not None else None),
        header_overrides=overrides or None,
        actor_user_id=(actor.id if actor else None),
    )
    session.refresh(order)
    codfac = str(result["codigo"])
    if not order.factusol_invoice_number:
        from app.erp.factusol_albaran import attach_invoice  # noqa: PLC0415

        attach_invoice(
            session, order, serie=result["serie"], codigo=result["codigo"],
            ejercicio=ejercicio, how=f"desde el albarán {order.factusol_albaran_number}",
            actor_user_id=(actor.id if actor else None),
        )
        session.commit()
    _log_sync(
        session, order, codfac, "-", ejercicio, result["lines"],
        message=(
            f"Albarán {order.factusol_albaran_number} → F_FAC "
            f"{visible_number(result['serie'], result['codigo'])} "
            f"(ej. {ejercicio}, {result['lines']} líneas)"
        ),
    )
    session.commit()
    link = result.get("order") or {}
    return {
        "codfac": codfac, "ejercicio": ejercicio, "lines": result["lines"],
        "serie": result["serie"], "from_albaran": order.factusol_albaran_number,
        "numero": visible_number(result["serie"], result["codigo"]),
        "cobro": link.get("cobro") if isinstance(link, dict) else None,
        "origin_mark_warning": result.get("origin_mark_warning"),
    }


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
    success: bool = True,
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
        status=(SyncStatus.SUCCESS if success else SyncStatus.FAILED).value,
        started_at=now, finished_at=now,
        records_processed=1,
        triggered_by=SyncTrigger.MANUAL.value,
        message=message or (
            f"F_PCL {codpcl} → F_FAC {codfac} (ej. {ejercicio}, {lines} líneas)"
        ),
    ))


def _status_value(v: object) -> str:
    return getattr(v, "value", v)  # type: ignore[return-value]
