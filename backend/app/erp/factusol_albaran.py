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
contrapartida, fecha; `payment_status=paid`). «Sin pago» solo apunta la forma
de pago: pendiente.

**El cobro es SIEMPRE manual (decisión de Bart, 2026-09-14).** Ni al emitir la
factura desde la ficha (`service.emit_invoice`) ni al facturar el albarán /
presupuesto desde el explorador (`chain.convert_document` →
`on_invoice_created`, que solo VINCULA la factura al pedido) se escribe
`F_LCO` ni se marca `ESTFAC`: el cobro se registra únicamente cuando el usuario
pulsa «Registrar cobro» (ficha, bandeja o Documentos), con el motor F-4-B.
El `workflow` del pedido lo ofrece como siguiente paso, nunca lo da por hecho.
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
    (solo se apunta la forma de pago). `paid=True` apunta el pago en el pedido
    con la forma (transferencia/PayPal/contado/crédito…) y, si se conoce, la
    cuenta (contrapartida) y la fecha.

    La contrapartida es OPCIONAL (fix post-#470): muchas veces se sabe que está
    pagado y con qué forma, pero no en qué cuenta entró. Como esto es solo un
    apunte del pedido —NUNCA escribe el cobro en FACTUSOL—, no hace falta la
    cuenta ahora; se pedirá al «Registrar cobro» a mano, cuando exista la
    factura y ahí sí haga falta.

    `no_charge=True` = «sin cobro» (envío de cortesía / no se cobra): decisión
    explícita de que este pedido NO se factura ni se cobra. Es incompatible con
    `paid` (Bloque C · C1)."""

    paid: bool = False
    no_charge: bool = False
    forma_pago: str | None = Field(default=None, max_length=10)
    forma_pago_nombre: str | None = Field(default=None, max_length=120)
    contrapartida: str | None = Field(default=None, max_length=80)
    fecha: str | None = Field(default=None, max_length=25)

    @model_validator(mode="after")
    def _check(self) -> PaymentIn:
        if self.paid and self.no_charge:
            raise ValueError(
                "Un pedido no puede estar «pagado» y «sin cobro» a la vez."
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


def manual_albaran_codcli(session: Session, order: Order) -> str | None:
    """CODCLI (F_CLI) de la empresa del pedido: el cliente al que se le hace
    el albarán de un pedido MANUAL. None si no hay empresa o no está
    vinculada a FACTUSOL."""
    if not order.company_id:
        return None
    from app.models.crm import Company  # noqa: PLC0415

    company = session.get(Company, order.company_id)
    if company is None or not company.factusol_company_id:
        return None
    return str(company.factusol_company_id)


def company_regime(session: Session, company_id: str | None) -> str | None:
    """Régimen de IVA de la empresa del pedido por su país + NIF-IVA (Tarea
    C), o None si la empresa no tiene país en el CRM (entonces manda la ficha
    F_CLI del cliente)."""
    if not company_id:
        return None
    from app.erp.language import normalize_country  # noqa: PLC0415
    from app.integrations.factusol.vat_regime import regime_for  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415
    from app.services.vies import company_vies_valid  # noqa: PLC0415

    company = session.get(Company, company_id)
    if company is None or not company.country:
        return None
    return regime_for(
        normalize_country(company.country), vat=company.vat, nif=company.tax_id,
        vies_valid=company_vies_valid(company),
    )


#: Código del 409 cuando el pedido manual no tiene empresa vinculada a F_CLI.
COMPANY_NOT_LINKED = "company_not_linked"


def albaran_blocker(
    order: Order, session: Session | None = None,
) -> tuple[str, str] | None:
    """`(code, detail)` si el pedido NO puede tener albarán de BoHub.

    - MUESTRA / envío no facturable → nunca (no pasa por FACTUSOL);
    - web → nunca (lo crea WooCommerce);
    - con documento de origen (Fase 1) → se convierte ese documento;
    - MANUAL (sin documento en FACTUSOL) → el albarán se crea desde las
      LÍNEAS del pedido (Tarea A): hace falta al menos una línea y, con
      `session`, una empresa vinculada a un cliente de F_CLI."""
    from app.erp.sample_orders import (  # noqa: PLC0415
        NOT_BILLABLE_CODE,
        NOT_BILLABLE_DETAIL,
        is_sample_order,
    )

    if is_sample_order(order):
        return (NOT_BILLABLE_CODE, NOT_BILLABLE_DETAIL)
    if is_web_order(order):
        return (
            WebOrderNoAlbaran.code,
            "Los pedidos web no generan albarán en BoHub: lo crea WooCommerce.",
        )
    if order_source(order) is not None:
        return None
    if not order.lines:
        return (
            AlbaranNotApplicable.code,
            f"El pedido {order.order_number} no tiene líneas: no hay nada que "
            "poner en el albarán.",
        )
    if session is not None and not manual_albaran_codcli(session, order):
        return (
            COMPANY_NOT_LINKED,
            "El pedido no tiene una empresa vinculada a un cliente de FACTUSOL "
            "(F_CLI): vincúlala o créala en FACTUSOL antes de crear el albarán.",
        )
    return None


# --- guardarraíl web sobre un pedido de cliente F_PCL -------------------------


def store_ref_prefixes(session: Session) -> set[str]:
    """Prefijos de referencia (`BOP`, `ART`…) configurados en las tiendas
    WooCommerce (`IntegrationAccount.metadata_json.factusol_ref_prefix`)."""
    from app.integrations.factusol.service import (  # noqa: PLC0415
        configured_ref_prefixes,
    )
    from app.models.crm import ExternalSystem  # noqa: PLC0415
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    # Los configurados por tienda en Ajustes ERP (misma fuente que
    # `_store_ref_prefix`) cuentan igual que los del metadata_json.
    out: set[str] = set(configured_ref_prefixes(session).values())
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
    """Valida y resuelve el paso de pago contra los catálogos: la contrapartida
    (código o nombre del Excel → código real) SI se indica —400 si se da una que
    no casa, pero es OPCIONAL— y la fecha (ISO o dd/mm/yyyy → `YYYY-MM-DD`).

    Marcar «Pagado» solo apunta el pago en el pedido (forma, y cuenta/fecha si
    se conocen); no escribe ningún cobro en FACTUSOL. Por eso la cuenta no es
    obligatoria: se pedirá al «Registrar cobro» a mano, cuando exista factura."""
    from app.erp.contrapartidas import (  # noqa: PLC0415
        resolve_contrapartida,
        resolve_contrapartida_code,
    )
    from app.integrations.factusol.collections_write import (  # noqa: PLC0415
        factusol_datetime,
    )

    data: dict[str, Any] = {
        "paid": bool(payment.paid),
        "no_charge": bool(payment.no_charge),
        "forma_pago": (payment.forma_pago or "").strip() or None,
        "forma_pago_nombre": (payment.forma_pago_nombre or "").strip() or None,
        "contrapartida": None,
        "contrapartida_nombre": None,
        "fecha": None,
    }
    if not payment.paid:
        return data
    # Cuenta OPCIONAL: solo se valida (y se guarda) si se ha indicado; una
    # cuenta escrita que no casa sí es un error (no se apunta contra una cuenta
    # adivinada), pero no darla es válido.
    if (payment.contrapartida or "").strip():
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
      intención de cobro (contrapartida, fecha). El cobro F-4-B NO se
      registra solo: lo hace el usuario con «Registrar cobro» cuando exista
      la factura.
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
                "pendiente de registrar a mano con «Registrar cobro» cuando "
                "exista la factura"
            ),
            actor_user_id=actor_user_id,
            metadata={"event": "factusol_payment_intent", **resolved},
        )
        # Lote 4: confirmado el pago (alta manual con cobro o conversión de
        # proforma / pedido de cliente), el pedido entra solo en la Cola SAT si
        # sigue en pre-cola, sin aprobación. Idempotente vía el guard
        # `pending_review`. Se atribuye al usuario que confirmó, si se conoce.
        from app.erp.sat_autoenqueue import enqueue_paid_order  # noqa: PLC0415
        from app.models.crm import User  # noqa: PLC0415

        actor = session.get(User, actor_user_id) if actor_user_id else None
        enqueue_paid_order(session, order, actor=actor)
    elif resolved.get("no_charge"):
        # «Sin cobro» (cortesía): decisión explícita de no facturar ni cobrar.
        # No cambia el «Pagado» del CRM (sigue pendiente), pero queda apuntado
        # y el workflow lo saca de «Por facturar» / «Por cobrar».
        _history(
            session, order, domain=StatusDomain.PAYMENT, from_status=prev,
            to_status=prev,
            reason="Sin cobro (cortesía): este pedido no se factura ni se cobra",
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


def is_no_charge(order: Order) -> bool:
    """¿El pedido está marcado «sin cobro» (cortesía)? (Bloque C · C1.)"""
    intent = payment_intent(order)
    return bool(intent and intent.get("no_charge"))


def payment_decided(order: Order) -> bool:
    """¿Se ha decidido ya el pago del pedido? (Bloque C · C1: antes de generar
    el albarán hay que haber elegido —pagado, sin cobro, o el paso de pago del
    alta—; no se genera dejando el pago «en el aire».)

    Cuenta como decidido: hay un apunte de pago (`factusol_payment`, sea
    pagado / sin pago / sin cobro) o el pedido ya consta pagado en el CRM."""
    from app.erp.models import PaymentStatus  # noqa: PLC0415

    if payment_intent(order) is not None:
        return True
    status = getattr(order.payment_status, "value", order.payment_status)
    return status == PaymentStatus.PAID.value


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

    blocker = albaran_blocker(order, session)
    if blocker is not None:
        code, detail = blocker
        exc_cls = WebOrderNoAlbaran if code == WebOrderNoAlbaran.code else AlbaranNotApplicable
        raise exc_cls(detail)
    if order.factusol_albaran_number:
        return {
            "status": "already", "numero": order.factusol_albaran_number,
            "order_id": order.id, "order_number": order.order_number,
        }
    src = order_source(order)
    if src is None:
        return _create_albaran_from_lines(
            session, client, order, ejercicio=ejercicio, actor_user_id=actor_user_id,
        )
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


def _create_albaran_from_lines(
    session: Session, client: FactusolClient, order: Order,
    *, ejercicio: str, actor_user_id: str | None,
) -> dict[str, Any]:
    """Tarea A — pedido MANUAL (sin documento en FACTUSOL): albarán autónomo
    desde las líneas del pedido, con los builders de «Nueva proforma» y la
    maquinaria de la Fase 2 (`COD*` entero, `ESTALB=0`, tipos como la fila
    real, guard de esquema estricto, registro exacto en el log). Serie como
    en la emisión (`resolve_serie`); la forma de pago apuntada pisa `FOPALB`.
    Con el nº guardado en el pedido, el «PDF del albarán» y «Emitir factura»
    (cadena albarán → factura) funcionan como en la Fase 2."""
    from app.integrations.factusol.albaran_manual import (  # noqa: PLC0415
        create_standalone_albaran,
    )
    from app.integrations.factusol.service import resolve_serie  # noqa: PLC0415

    codcli = manual_albaran_codcli(session, order)
    if not codcli:
        raise AlbaranNotApplicable(
            "El pedido no tiene una empresa vinculada a un cliente de FACTUSOL "
            "(F_CLI): vincúlala o créala en FACTUSOL antes de crear el albarán."
        )
    intent = payment_intent(order) or {}
    result = create_standalone_albaran(
        session, client, order=order, codcli=codcli,
        serie=resolve_serie(session, order), ejercicio=ejercicio,
        fopalb=intent.get("forma_pago") or None, actor_user_id=actor_user_id,
        regime=company_regime(session, order.company_id),
    )
    _attach_albaran(
        session, order, result["numero"],
        how="creado por BoHub desde las líneas del pedido",
        actor_user_id=actor_user_id,
        extra={
            "serie": result["serie"], "codigo": result["codigo"],
            "lines": result["lines"], "source": None, "standalone": True,
            "codcli": codcli, "free_text_lines": result["free_text_lines"],
            "regime": result["regime"], "regime_warning": result["regime_warning"],
            "portes": result["portes"],
        },
    )
    session.commit()
    logger.info(
        "albarán manual: pedido %s → albarán %s (%d líneas desde BoHub)",
        order.order_number, result["numero"], result["lines"],
    )
    return {
        "status": "created", **result,
        "order_id": order.id, "order_number": order.order_number,
    }


# --- Lote 7 · P1: cambiar la SERIE (empresa emisora) de un pedido manual ------

#: Series válidas para la elección MANUAL (empresas emisoras reales):
#: 1 Bomedia · 2 MQ Europe · 4 Lambert · 5 Streamtec. Es un subconjunto de las
#: `VALID_SERIES` de `service` (1-9): las que Bart usa a mano.
MANUAL_SERIES: tuple[int, ...] = (1, 2, 4, 5)
MANUAL_SERIE_NAMES: dict[int, str] = {
    1: "Bomedia", 2: "MQ Europe", 4: "Lambert", 5: "Streamtec",
}
#: Evento de auditoría del cambio de serie (serie/nº viejo → nuevo).
SERIE_CHANGED_EVENT = "erp.order_serie_changed"


def change_order_serie(
    session: Session, order: Order, new_serie: int, *,
    client: FactusolClient | None, ejercicio: str, actor: Any = None,
) -> dict[str, Any]:
    """Fija la serie (empresa emisora) elegida a mano para un pedido y, si el
    pedido ya tiene albarán en FACTUSOL, lo BORRA y lo RE-CREA en la serie
    nueva. Es el orquestador ÚNICO del cambio de serie (lo llama el endpoint
    para el caso sin albarán y el worker serial `factusol:writes` para el caso
    con albarán — la re-creación es una escritura MAX+1 que hay que serializar).

    - `factusol_manual_serie = new_serie` manda desde ya en `resolve_serie`, así
      que el albarán recreado (y luego proforma / factura) sale en esa serie.
    - Sin albarán: solo se registra la serie (nada que borrar / recrear).
    - Con albarán: se borra el viejo (`order_cancel.delete_factusol_document`,
      la misma primitiva de «anular pedido», por clave compuesta serie/código),
      se desvincula en BoHub y se recrea con `create_albaran_for_order`
      (idempotente; toma la serie nueva de `resolve_serie`).
    - ROBUSTEZ: si el borrado sale pero la re-creación falla, el pedido queda
      SIN albarán (estado conocido, ya persistido) y el fallo se anota — nunca
      se deja a BoHub reclamando un albarán que no está en FACTUSOL.

    Devuelve el resumen (serie/nº viejo y nuevo, borrado, recreado, error) que
    el job publica y el frontend lee. La FACTURA nunca se toca aquí: un pedido
    con factura no cambia de serie (el guard vive en el endpoint)."""
    from app.erp.factusol_cobro import parse_invoice_number  # noqa: PLC0415
    from app.erp.order_cancel import delete_factusol_document  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    serie = coerce_serie(new_serie)
    if serie is None or serie not in MANUAL_SERIES:
        raise ValueError(f"Serie no válida: {new_serie!r} (usa una de {MANUAL_SERIES}).")
    actor_user_id = getattr(actor, "id", None)
    old_serie = coerce_serie(order.factusol_manual_serie)
    old_albaran = order.factusol_albaran_number

    # La serie elegida manda en `resolve_serie` desde ya (también para la
    # re-creación de más abajo y para la futura proforma / factura).
    order.factusol_manual_serie = serie

    result: dict[str, Any] = {
        "order_id": order.id, "order_number": order.order_number,
        "new_serie": serie, "old_serie": old_serie,
        "old_albaran_number": old_albaran, "albaran_number": None,
        "deleted": False, "recreated": False, "error": None,
    }

    if not old_albaran:
        # Nada en FACTUSOL: solo se registra la serie elegida.
        _record_serie_changed(
            session, order, result, actor=actor, actor_user_id=actor_user_id,
            ejercicio=ejercicio,
        )
        session.commit()
        return result

    if client is None:  # defensivo: el caso con albarán SIEMPRE trae cliente.
        raise FactusolError(
            "Cambiar la serie con albarán necesita conexión con FACTUSOL "
            "(borrar y recrear el albarán)."
        )

    serie_alb, codigo_alb = parse_invoice_number(old_albaran)
    if serie_alb is None or codigo_alb is None:
        raise FactusolError(
            f"El nº de albarán del pedido no es válido: {old_albaran!r}"
        )

    # 1) Borra el albarán viejo en FACTUSOL (líneas → cabecera, por clave
    #    compuesta serie/código). Si falla, propaga: no se ha tocado nada en
    #    BoHub todavía (la serie sin persistir se descarta).
    delete_factusol_document(
        client, "albaranes", serie=serie_alb, codigo=codigo_alb, ejercicio=ejercicio,
    )
    result["deleted"] = True
    # 2) Desvincula en BoHub y PERSISTE (serie nueva + sin albarán): estado
    #    conocido aunque la re-creación falle.
    order.factusol_albaran_number = None
    packing = packing_of(order)
    packing.pop(ALBARAN_KEY, None)
    save_packing(order, packing)
    session.commit()

    # 3) Recrea el albarán en la serie nueva (resolve_serie ya devuelve `serie`).
    try:
        created = create_albaran_for_order(
            session, client, order, ejercicio=ejercicio, actor_user_id=actor_user_id,
        )
    except (FactusolError, AlbaranNotApplicable) as exc:
        # Borrado OK, re-creación KO: el pedido queda SIN albarán (ya
        # persistido en el paso 2), en un estado conocido y reintentable.
        session.rollback()
        order = session.get(Order, order.id)
        record_albaran_failure(session, order, str(exc), actor_user_id=actor_user_id)
        result["error"] = str(exc)[:400]
        _record_serie_changed(
            session, order, result, actor=actor, actor_user_id=actor_user_id,
            ejercicio=ejercicio,
        )
        session.commit()
        logger.warning(
            "lote7 serie: pedido %s borró el albarán %s pero no pudo recrearlo "
            "en la serie %d: %s", order.order_number, old_albaran, serie, exc,
        )
        return result

    result["recreated"] = True
    result["albaran_number"] = created.get("numero") or order.factusol_albaran_number
    _record_serie_changed(
        session, order, result, actor=actor, actor_user_id=actor_user_id,
        ejercicio=ejercicio,
    )
    session.commit()
    logger.info(
        "lote7 serie: pedido %s cambió a la serie %d (albarán %s → %s)",
        order.order_number, serie, old_albaran, result["albaran_number"],
    )
    return result


def _record_serie_changed(
    session: Session, order: Order, result: dict[str, Any], *,
    actor: Any, actor_user_id: str | None, ejercicio: str,
) -> None:
    """Evento de auditoría del cambio de serie + traza en el historial del
    pedido (dominio preparación, sin mover el estado)."""
    from app.core.audit import record_event  # noqa: PLC0415

    old_serie, new_serie = result["old_serie"], result["new_serie"]
    old_name = MANUAL_SERIE_NAMES.get(old_serie or -1)
    new_name = MANUAL_SERIE_NAMES.get(new_serie, str(new_serie))
    detail = (
        f"Serie del pedido → {new_serie}"
        + (f" ({new_name})" if new_name else "")
        + (f", antes {old_serie}" + (f" ({old_name})" if old_name else "")
           if old_serie is not None else "")
    )
    if result["old_albaran_number"] and result["error"] is None:
        detail += (
            f"; albarán recreado {result['old_albaran_number']} → "
            f"{result['albaran_number']}"
        )
    elif result["old_albaran_number"] and result["error"] is not None:
        detail += (
            f"; albarán {result['old_albaran_number']} borrado pero NO recreado "
            "(reintenta desde la ficha)"
        )
    record_event(
        session, action=SERIE_CHANGED_EVENT, target_type="order",
        target_id=order.id, actor=actor,
        message=detail,
        metadata={
            "order_number": order.order_number,
            "old_serie": old_serie, "new_serie": new_serie,
            "old_albaran_number": result["old_albaran_number"],
            "new_albaran_number": result["albaran_number"],
            "deleted": result["deleted"], "recreated": result["recreated"],
            "error": result["error"], "ejercicio": ejercicio,
            "actor_user_id": actor_user_id,
        },
    )
    prep = _status_value(order.preparation_status)
    _history(
        session, order, domain=StatusDomain.PREPARATION, from_status=prep,
        to_status=prep, reason=detail[:255], actor_user_id=actor_user_id,
        metadata={"event": "factusol_serie_changed", **result},
    )


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


# --- al existir la factura: vincular el pedido (el cobro es manual) ----------------


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
    order.factusol_invoice_serie = int(serie)  # Lote 2 · B: serie + número
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
    session: Session, *, source_type: str,
    source_serie: int, source_codigo: int, serie: int, codigo: int,
    ejercicio: str, actor_user_id: str | None = None,
) -> dict[str, Any] | None:
    """Enganche de la cadena: acaba de crearse la factura `serie-codigo` desde
    `source_type source_serie-source_codigo`. Si ese origen es el albarán (o el
    documento) de un pedido de BoHub, vincula la factura al pedido — y nada
    más: el cobro es siempre manual. `None` si no hay pedido detrás. No hace
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
    # El cobro apuntado NO se registra aquí: es siempre manual.
    return {
        "order_id": order.id, "order_number": order.order_number,
        "linked": linked,
    }
