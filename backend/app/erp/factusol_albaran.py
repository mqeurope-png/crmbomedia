"""ERP · Fase 2 — al convertir una proforma / pedido de cliente NO-web en
pedido de BoHub: crear el ALBARÁN en FACTUSOL y confirmar el pago (opción B).

**Albarán.** Se crea con el motor de la cadena E3-B (`chain.convert_document`,
el mismo que crea albaranes en producción): copia por sufijo del documento
origen real, allowlist de columnas vivas, enlace `DOC/DTP/DCO` por línea, y
los dos arreglos del discovery de la Fase 2 (`COD*` entero, `ESTALB=0`). El
nº queda en `orders.factusol_albaran_number`.

- **Guardarraíl web**: un pedido de origen `woocommerce` NUNCA genera albarán
  en BoHub (lo crea WooCommerce, mu-plugin Fase D). Tampoco un pedido de
  cliente F_PCL que sea un pedido web (`web_pedido_reason`).
- **Idempotente**: si el pedido ya tiene su nº, no se crea otro; si el
  documento origen ya tiene un albarán enlazado en FACTUSOL (por
  `DOCLAL/DTPLAL/DCOLAL`), se vincula ese en vez de duplicar.
- El guard de esquema (columnas vivas + tipos de la fila real) y el log del
  registro exacto viven en `chain.convert_document`.

**Pago — opción B (decidida con Bart).** «Pagado» NO emite ninguna factura: se
apunta en el pedido (`packing_json.factusol_payment`: forma de pago,
contrapartida, fecha; `payment_status=paid`) y el cobro F-4-B
(`register_invoice_collection`, solo `F_LCO` + `ESTFAC=2`) se registra
automáticamente cuando EXISTA la factura de ese pedido — al emitirla desde la
ficha (`service.emit_invoice`) o al facturar el albarán / presupuesto desde el
explorador (`chain.convert_document` → `on_invoice_created`). «Sin pago» solo
apunta la forma de pago: sin cobro ni intención, pendiente.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import (
    InvoiceStatus,
    Order,
    OrderSource,
    OrderStatusHistory,
    PaymentStatus,
    StatusDomain,
)
from app.integrations.factusol.client import FactusolClient

logger = logging.getLogger(__name__)

#: Bloques de `packing_json` que usa la Fase 2.
PAYMENT_KEY = "factusol_payment"
ALBARAN_KEY = "factusol_albaran"


class PaymentIn(BaseModel):
    """Paso de confirmación de pago al convertir. `paid=False` = «sin pago»
    (solo se apunta la forma de pago). `paid=True` exige la contrapartida
    (código «6» o nombre «Bomedia (Sabadell)») y admite la fecha del cobro
    (ISO o dd/mm/yyyy; por defecto hoy)."""

    paid: bool = False
    forma_pago: str | None = Field(default=None, max_length=10)
    forma_pago_nombre: str | None = Field(default=None, max_length=120)
    contrapartida: str | None = Field(default=None, max_length=80)
    fecha: str | None = Field(default=None, max_length=25)

    @model_validator(mode="after")
    def _paid_needs_account(self) -> PaymentIn:
        if self.paid and not (self.contrapartida or "").strip():
            raise ValueError(
                "Un pago confirmado necesita la cuenta (contrapartida) donde "
                "entró el dinero."
            )
        return self


class PaymentError(Exception):
    """Pago que no se puede apuntar (cuenta desconocida, fecha ilegible)."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


class AlbaranNotApplicable(Exception):
    """El pedido no puede tener albarán de BoHub (sin documento origen)."""

    code = "albaran_not_applicable"


class WebOrderNoAlbaran(AlbaranNotApplicable):
    """Guardarraíl web: el albarán de un pedido web lo crea WooCommerce."""

    code = "web_order_no_albaran"


def _status_value(v: Any) -> str:
    return getattr(v, "value", v)


def packing_of(order: Order) -> dict[str, Any]:
    if not order.packing_json:
        return {}
    try:
        data = json.loads(order.packing_json)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_packing(order: Order, data: dict[str, Any]) -> None:
    order.packing_json = json.dumps(data) if data else None


def is_web_order(order: Order) -> bool:
    return _status_value(order.external_source) == OrderSource.WOOCOMMERCE.value


def order_source(order: Order) -> dict[str, Any] | None:
    """`packing_json.factusol_source` (Fase 1) si es un presupuesto / pedido
    de cliente de FACTUSOL con serie y código utilizables."""
    src = packing_of(order).get("factusol_source")
    if not isinstance(src, dict):
        return None
    if src.get("doc_type") not in ("presupuestos", "pedidos"):
        return None
    try:
        int(src.get("serie")), int(src.get("codigo"))
    except (TypeError, ValueError):
        return None
    return src


def albaran_blocker(order: Order) -> tuple[str, str] | None:
    """`(code, detail)` si el pedido NO puede tener albarán de BoHub."""
    if is_web_order(order):
        return (
            WebOrderNoAlbaran.code,
            "Los pedidos web no generan albarán en BoHub: lo crea WooCommerce.",
        )
    if order_source(order) is None:
        return (
            AlbaranNotApplicable.code,
            "El pedido no procede de un presupuesto / pedido de cliente de "
            "FACTUSOL: no hay documento del que crear el albarán.",
        )
    return None


# --- guardarraíl web sobre un pedido de cliente F_PCL -------------------------


def store_ref_prefixes(session: Session) -> set[str]:
    """Prefijos de referencia (`BOP`, `ART`…) configurados en las tiendas
    WooCommerce (`IntegrationAccount.metadata_json.factusol_ref_prefix`)."""
    from app.models.crm import ExternalSystem  # noqa: PLC0415
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    out: set[str] = set()
    for acc in session.scalars(
        select(IntegrationAccount).where(
            IntegrationAccount.system == ExternalSystem.WOOCOMMERCE,
        )
    ):
        if not acc.metadata_json:
            continue
        try:
            meta = json.loads(acc.metadata_json)
        except (TypeError, ValueError):
            continue
        prefix = meta.get("factusol_ref_prefix") if isinstance(meta, dict) else None
        if prefix:
            out.add(str(prefix).strip().upper())
    return out


def web_pedido_reason(session: Session, pcl_row: dict[str, Any]) -> str | None:
    """Motivo por el que un pedido de cliente F_PCL es un pedido WEB (y por
    tanto BoHub no debe crearle albarán), o `None` si es manual.

    La app Woo→FACTUSOL crea el F_PCL con `REFPCL` = `<prefijo>-<nº Woo>`
    (`BOP-099917`). Se reconoce (1) por el pedido Woo de BoHub que compone esa
    misma referencia y (2) por el prefijo de tienda configurado."""
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _compose_ref,
        _store_ref_prefix,
    )

    ref = str(pcl_row.get("REFPCL") or "").strip().upper()
    if not ref or "-" not in ref:
        return None
    prefix, _, number = ref.rpartition("-")
    if not number.isdigit():
        return None
    digits = number.lstrip("0") or "0"
    candidates = session.scalars(
        select(Order).where(
            Order.external_source == OrderSource.WOOCOMMERCE,
            Order.order_number.like(f"%{digits}"),
        )
    )
    for order in candidates:
        if _compose_ref(order.order_number, _store_ref_prefix(session, order)) == ref:
            return (
                f"El pedido de cliente {ref} es el pedido web {order.order_number}: "
                "el albarán lo crea WooCommerce, no BoHub."
            )
    if prefix in store_ref_prefixes(session):
        return (
            f"La referencia {ref} es de una tienda web (prefijo {prefix}): el "
            "albarán lo crea WooCommerce, no BoHub."
        )
    return None


# --- pago (opción B) ------------------------------------------------------------


def resolve_payment(session: Session, payment: PaymentIn) -> dict[str, Any]:
    """Valida y resuelve el paso de pago contra los catálogos: contrapartida
    (código o nombre del Excel → código real, 400 si no casa) y fecha (ISO o
    dd/mm/yyyy → `YYYY-MM-DD`). Nunca se apunta un cobro contra una cuenta
    adivinada."""
    from app.erp.contrapartidas import (  # noqa: PLC0415
        resolve_contrapartida,
        resolve_contrapartida_code,
    )
    from app.integrations.factusol.collections_write import (  # noqa: PLC0415
        factusol_datetime,
    )

    data: dict[str, Any] = {
        "paid": bool(payment.paid),
        "forma_pago": (payment.forma_pago or "").strip() or None,
        "forma_pago_nombre": (payment.forma_pago_nombre or "").strip() or None,
        "contrapartida": None,
        "contrapartida_nombre": None,
        "fecha": None,
    }
    if not payment.paid:
        return data
    code = resolve_contrapartida_code(session, payment.contrapartida)
    if code is None:
        raise PaymentError(
            "unknown_account",
            f"La cuenta {payment.contrapartida!r} no casa con ninguna "
            "contrapartida del catálogo (/erp/settings).",
        )
    data["contrapartida"] = code
    data["contrapartida_nombre"] = resolve_contrapartida(session, code) or code
    try:
        fecha_iso = factusol_datetime(payment.fecha or datetime.now(UTC).date())
    except ValueError as exc:
        raise PaymentError("invalid_date", str(exc)) from exc
    data["fecha"] = fecha_iso[:10]
    return data


def _history(
    session: Session, order: Order, *, domain: StatusDomain, from_status: str,
    to_status: str, reason: str, actor_user_id: str | None,
    metadata: dict[str, Any],
) -> None:
    session.add(OrderStatusHistory(
        order_id=order.id, domain=domain,
        from_status=from_status, to_status=to_status,
        changed_at=datetime.now(UTC), changed_by_user_id=actor_user_id,
        reason=reason[:255], metadata_json=json.dumps(metadata, default=str),
    ))


def record_payment_intent(
    session: Session, order: Order, resolved: dict[str, Any],
    *, actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Apunta el paso de pago en el pedido (sin escribir en FACTUSOL).

    - Pagado: `payment_status=paid` + bloque `factusol_payment` con la
      intención de cobro (contrapartida, fecha) → el cobro F-4-B se registra
      cuando exista la factura.
    - Sin pago: solo la forma de pago; el estado de pago no cambia (pendiente)
      y no queda ninguna intención de cobro."""
    packing = packing_of(order)
    block = {
        **resolved,
        "recorded_at": datetime.now(UTC).isoformat(),
        "recorded_by_user_id": actor_user_id,
        "cobro": None,
    }
    packing[PAYMENT_KEY] = block
    save_packing(order, packing)
    prev = _status_value(order.payment_status)
    if resolved["paid"]:
        if prev != PaymentStatus.PAID.value:
            order.payment_status = PaymentStatus.PAID
        _history(
            session, order, domain=StatusDomain.PAYMENT, from_status=prev,
            to_status=PaymentStatus.PAID.value,
            reason=(
                "Pago confirmado al convertir: cobro FACTUSOL "
                f"({resolved['contrapartida_nombre']}, {resolved['fecha']}) "
                "pendiente de que exista la factura"
            ),
            actor_user_id=actor_user_id,
            metadata={"event": "factusol_payment_intent", **resolved},
        )
    else:
        _history(
            session, order, domain=StatusDomain.PAYMENT, from_status=prev,
            to_status=prev,
            reason="Sin pago al convertir: pendiente" + (
                f" (forma de pago {resolved['forma_pago_nombre'] or resolved['forma_pago']})"
                if (resolved["forma_pago_nombre"] or resolved["forma_pago"]) else ""
            ),
            actor_user_id=actor_user_id,
            metadata={"event": "factusol_payment_intent", **resolved},
        )
    return block


def payment_intent(order: Order) -> dict[str, Any] | None:
    block = packing_of(order).get(PAYMENT_KEY)
    return block if isinstance(block, dict) else None


def pending_collection(order: Order) -> dict[str, Any] | None:
    """La intención de cobro aún no registrada en FACTUSOL, si la hay."""
    block = payment_intent(order)
    if not block or not block.get("paid") or not block.get("contrapartida"):
        return None
    cobro = block.get("cobro") or {}
    if cobro.get("registered"):
        return None
    return block


def register_pending_collection(
    session: Session, client: FactusolClient, order: Order,
    *, serie: int, codigo: int, ejercicio: str,
    actor_user_id: str | None = None,
) -> dict[str, Any] | None:
    """Registra en FACTUSOL el cobro apuntado al convertir, ahora que existe
    la factura `serie-codigo` del pedido — con el motor F-4-B tal cual (solo
    `F_LCO` + `ESTFAC=2`, idempotente, nunca lanza). `None` si el pedido no
    tenía pago pendiente. El resultado queda en el pedido."""
    from app.integrations.factusol.collections_write import (  # noqa: PLC0415
        register_invoice_collection,
    )

    block = pending_collection(order)
    if block is None:
        return None
    numero = f"{serie}-{int(codigo):06d}"
    try:
        result = register_invoice_collection(
            client, session, serie=int(serie), codigo=int(codigo),
            contrapartida=str(block["contrapartida"]), fecha=block["fecha"],
            forma=block.get("forma_pago_nombre"), ejercicio=ejercicio,
        )
    except Exception as exc:  # noqa: BLE001 — la factura ya existe; solo aviso
        logger.warning(
            "fase2 cobro %s (pedido %s): %s", numero, order.order_number, exc,
            exc_info=True,
        )
        result = {"registered": False, "status": "error", "motivo": str(exc)[:200]}
    registered = bool(result.get("registered")) or result.get("status") == "already"
    cobro = {
        "registered": registered,
        "status": result.get("status"),
        "numero": numero,
        "linlco": result.get("linlco"),
        "importe": result.get("importe"),
        "fecha": result.get("fecha"),
        "estfac_marked": result.get("estfac_marked"),
        "motivo": result.get("motivo"),
        "at": datetime.now(UTC).isoformat(),
    }
    packing = packing_of(order)
    packing.setdefault(PAYMENT_KEY, {})["cobro"] = cobro
    save_packing(order, packing)
    paid = PaymentStatus.PAID.value
    if result.get("registered"):
        reason = (
            f"Cobro de {result.get('importe')} € registrado en FACTUSOL para la "
            f"factura {numero} ({block.get('contrapartida_nombre')})"
        )
    elif result.get("status") == "already":
        reason = f"La factura {numero} ya constaba cobrada en FACTUSOL"
    else:
        reason = (
            f"No se pudo registrar el cobro de la factura {numero} en FACTUSOL: "
            f"{result.get('motivo') or result.get('status')}"
        )
    _history(
        session, order, domain=StatusDomain.PAYMENT, from_status=paid,
        to_status=paid, reason=reason, actor_user_id=actor_user_id,
        metadata={"event": "factusol_collection", **cobro},
    )
    (logger.info if registered else logger.warning)(
        "fase2 cobro %s (pedido %s): %s", numero, order.order_number, reason,
    )
    return cobro


# --- albarán ----------------------------------------------------------------------


def _attach_albaran(
    session: Session, order: Order, numero: str, *, how: str,
    actor_user_id: str | None, extra: dict[str, Any] | None = None,
) -> None:
    order.factusol_albaran_number = numero
    packing = packing_of(order)
    packing[ALBARAN_KEY] = {
        "numero": numero, "how": how,
        "at": datetime.now(UTC).isoformat(), **(extra or {}),
    }
    save_packing(order, packing)
    prep = _status_value(order.preparation_status)
    _history(
        session, order, domain=StatusDomain.PREPARATION, from_status=prep,
        to_status=prep, reason=f"Albarán FACTUSOL {numero} {how}",
        actor_user_id=actor_user_id,
        metadata={"event": "factusol_albaran", "numero": numero, "how": how,
                  **(extra or {})},
    )


def create_albaran_for_order(
    session: Session, client: FactusolClient, order: Order,
    *, ejercicio: str, actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Crea (o vincula) el albarán FACTUSOL del pedido. Corre SIEMPRE dentro
    del worker serial `factusol:writes` (contador MAX+1). Idempotente:

    1. el pedido ya tiene nº → `already`, sin tocar FACTUSOL;
    2. el documento origen ya tiene un albarán enlazado por `DOC/DTP/DCO` →
       `linked` (se guarda ese nº), sin escribir;
    3. si no, `convert_document` (guard de esquema + log del registro exacto)
       → `created`. La forma de pago del paso de pago pisa `FOPALB`."""
    from app.integrations.factusol.chain import (  # noqa: PLC0415
        convert_document,
        find_existing_children,
    )

    blocker = albaran_blocker(order)
    if blocker is not None:
        code, detail = blocker
        exc_cls = WebOrderNoAlbaran if code == WebOrderNoAlbaran.code else AlbaranNotApplicable
        raise exc_cls(detail)
    if order.factusol_albaran_number:
        return {
            "status": "already", "numero": order.factusol_albaran_number,
            "order_id": order.id, "order_number": order.order_number,
        }
    src = order_source(order) or {}
    doc_type, tip, cod = src["doc_type"], int(src["serie"]), int(src["codigo"])
    existing = find_existing_children(
        client, doc_type, "albaranes", tip=tip, cod=cod, ejercicio=ejercicio,
    )
    if existing:
        _attach_albaran(
            session, order, existing[0], how="localizado en FACTUSOL (ya existía)",
            actor_user_id=actor_user_id,
            extra={"source": {"doc_type": doc_type, "serie": tip, "codigo": cod}},
        )
        session.commit()
        logger.info(
            "fase2 albarán: pedido %s vinculado al albarán existente %s",
            order.order_number, existing[0],
        )
        return {
            "status": "linked", "numero": existing[0], "existing": existing,
            "order_id": order.id, "order_number": order.order_number,
        }
    intent = payment_intent(order) or {}
    overrides = {"FOPALB": intent["forma_pago"]} if intent.get("forma_pago") else None
    result = convert_document(
        session, client, source_type=doc_type, target_type="albaranes",
        tip=tip, cod=cod, ejercicio=ejercicio, header_overrides=overrides,
    )
    _attach_albaran(
        session, order, result["numero"], how="creado por BoHub al convertir",
        actor_user_id=actor_user_id,
        extra={
            "serie": result["serie"], "codigo": result["codigo"],
            "lines": result["lines"],
            "source": {"doc_type": doc_type, "serie": tip, "codigo": cod},
            "origin_mark_warning": result.get("origin_mark_warning"),
        },
    )
    session.commit()
    logger.info(
        "fase2 albarán: pedido %s → albarán %s (%d líneas)",
        order.order_number, result["numero"], result["lines"],
    )
    return {
        "status": "created", **result,
        "order_id": order.id, "order_number": order.order_number,
    }


def apply_conversion_extras(
    session: Session, client: FactusolClient, *, order_id: str,
    payment: dict[str, Any] | None, create_albaran: bool, ejercicio: str,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Tras crear el pedido en un job del worker serial (proforma → pedido):
    apunta el pago (si llegó) y crea el albarán en el mismo job. Un fallo del
    albarán NO tumba el job — el pedido existe; el error viaja en el
    resultado y el albarán se reintenta desde la ficha."""
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    out: dict[str, Any] = {"albaran": None, "albaran_error": None, "albaran_skipped": None}
    order = session.get(Order, order_id)
    if order is None:
        return out
    if payment is not None and payment_intent(order) is None:
        record_payment_intent(session, order, payment, actor_user_id=actor_user_id)
        session.commit()
    out["payment"] = payment_intent(order)
    if not create_albaran:
        return out
    try:
        out["albaran"] = create_albaran_for_order(
            session, client, order, ejercicio=ejercicio, actor_user_id=actor_user_id,
        )
    except AlbaranNotApplicable as exc:
        out["albaran_skipped"] = str(exc)
    except FactusolError as exc:
        out["albaran_error"] = str(exc)[:400]
        logger.warning("fase2 albarán del pedido %s falló: %s", order.order_number, exc)
        record_albaran_failure(session, order, str(exc), actor_user_id=actor_user_id)
        session.commit()
    return out


def record_albaran_failure(
    session: Session, order: Order, error: str, *, actor_user_id: str | None = None,
) -> None:
    """Deja en el historial por qué no se creó el albarán (guard de esquema,
    FACTUSOL caído…) para que se vea en la ficha y se pueda reintentar."""
    prep = _status_value(order.preparation_status)
    _history(
        session, order, domain=StatusDomain.PREPARATION, from_status=prep,
        to_status=prep,
        reason=f"Error al crear el albarán en FACTUSOL: {error[:200]}",
        actor_user_id=actor_user_id,
        metadata={"event": "factusol_albaran_failed", "error": error[:500]},
    )


# --- al existir la factura: vincular el pedido y registrar el cobro -------------


def find_order_for_albaran(session: Session, serie: int, codigo: int) -> Order | None:
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415

    return session.scalar(select(Order).where(
        Order.factusol_albaran_number == visible_number(serie, codigo),
    ))


def find_order_for_source(
    session: Session, doc_type: str, serie: int, codigo: int,
) -> Order | None:
    from app.erp.orders_from_factusol import (  # noqa: PLC0415
        SOURCE_BY_DOC_TYPE,
        external_id_for,
        find_existing,
    )

    source = SOURCE_BY_DOC_TYPE.get(doc_type)
    if source is None:
        return None
    return find_existing(session, source, external_id_for(doc_type, serie, codigo))


def attach_invoice(
    session: Session, order: Order, *, serie: int, codigo: int, ejercicio: str,
    how: str, actor_user_id: str | None = None,
) -> None:
    """Vincula al pedido la factura FACTUSOL recién creada (CODFAC desnudo en
    `factusol_invoice_number`, como la emisión) sin escribir en FACTUSOL."""
    inv = _status_value(order.invoice_status)
    order.invoice_status = InvoiceStatus.INVOICED_BY_ERP
    order.factusol_invoice_number = str(int(codigo))
    _history(
        session, order, domain=StatusDomain.INVOICE, from_status=inv,
        to_status=InvoiceStatus.INVOICED_BY_ERP.value,
        reason=f"Factura {serie}-{int(codigo):06d} creada en FACTUSOL {how}",
        actor_user_id=actor_user_id,
        metadata={
            "factusol_codfac": str(int(codigo)), "factusol_serie": int(serie),
            "factusol_ejercicio": ejercicio, "source": "fase2_chain",
        },
    )


def on_invoice_created(
    session: Session, client: FactusolClient, *, source_type: str,
    source_serie: int, source_codigo: int, serie: int, codigo: int,
    ejercicio: str, actor_user_id: str | None = None,
) -> dict[str, Any] | None:
    """Enganche de la cadena: acaba de crearse la factura `serie-codigo` desde
    `source_type source_serie-source_codigo`. Si ese origen es el albarán (o el
    documento) de un pedido de BoHub, vincula la factura al pedido y registra
    el cobro apuntado (opción B). `None` si no hay pedido detrás. No hace
    commit: lo hace el caller junto con el resto de la conversión."""
    if source_type == "albaranes":
        order = find_order_for_albaran(session, source_serie, source_codigo)
    else:
        order = find_order_for_source(session, source_type, source_serie, source_codigo)
    if order is None:
        return None
    linked = False
    if not order.factusol_invoice_number:
        attach_invoice(
            session, order, serie=serie, codigo=codigo, ejercicio=ejercicio,
            how=(
                f"desde el albarán {order.factusol_albaran_number}"
                if source_type == "albaranes" else f"desde el {source_type[:-1]}"
            ),
            actor_user_id=actor_user_id,
        )
        linked = True
    cobro = register_pending_collection(
        session, client, order, serie=serie, codigo=codigo, ejercicio=ejercicio,
        actor_user_id=actor_user_id,
    )
    return {
        "order_id": order.id, "order_number": order.order_number,
        "linked": linked, "cobro": cobro,
    }
