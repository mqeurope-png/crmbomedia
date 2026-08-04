"""BoHub ERP — definición canónica de las 4 máquinas de estado (Fase A PR 2).

Única fuente de verdad de transiciones/roles/guards/evidencias — datos, no
ifs dispersos (mismo patrón que `app/workflows/trigger_definitions.py`,
aprobado por Bart). El motor (`engine.py`) la consume; la API (PR 3) y la
UI (PR 4) derivan de aquí qué botones mostrar.

Contrato por transición:
  - `domain` + `from_status` → `to_status`: el arco permitido.
  - `allowed_roles`: valores de UserRole que pueden dispararla. `SYSTEM`
    (actor None) representa webhooks/sync automáticos. ADMIN va explícito
    en cada arco — sin bypass implícito, la matriz es literal.
  - `guards`: claves resueltas por el engine contra el pedido (cruces
    entre dominios del state-machines.md del Sprint 0).
  - `required_evidence`: claves que deben venir en `evidence` (p.ej. el
    motivo de un reembolso, el tracking de un envío). El engine las
    persiste en el metadata del historial.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.erp.models.orders import (
    InvoiceStatus,
    PaymentStatus,
    PreparationStatus,
    StatusDomain,
    TransportStatus,
)
from app.models.crm import UserRole

#: Actor sintético para transiciones automáticas (webhook/sync/worker).
SYSTEM = "system"

_ADMIN = UserRole.ADMIN.value
_MANAGER = UserRole.MANAGER.value
_PEDIDOS = UserRole.PEDIDOS.value
_SAT = UserRole.SAT.value

#: "Oficina": quienes gestionan pedidos desde el back-office.
_OFFICE = frozenset({_ADMIN, _MANAGER, _PEDIDOS})
_OFFICE_OR_SYSTEM = _OFFICE | {SYSTEM}


@dataclass(frozen=True)
class TransitionDef:
    domain: StatusDomain
    from_status: str
    to_status: str
    label: str
    allowed_roles: frozenset[str]
    guards: tuple[str, ...] = field(default=())
    required_evidence: tuple[str, ...] = field(default=())


# --- guards (claves; la implementación vive en engine.py) --------------------

#: preparation → preparing exige pago cobrado/aprobado.
GUARD_PAYMENT_OK = "payment_ok_for_preparation"
#: transport → label_created exige preparación embalada.
GUARD_PACKED = "packed_before_label"
#: transport → in_transit exige tracking_number en el pedido o evidencia.
GUARD_TRACKING = "tracking_required"
#: invoice → generated exige pago paid Y todos los CODART mapeados.
GUARD_INVOICEABLE = "invoice_requires_paid_and_mapped"
#: preparation → packed exige ≥1 bulto medido (Fase D, multi-bulto obligatorio).
GUARD_PACKAGES_MEASURED = "packages_measured_before_packed"

#: Estados de pago que desbloquean la preparación (GUARD_PAYMENT_OK).
PAYMENT_OK_FOR_PREPARATION = {
    PaymentStatus.PAID.value,
    PaymentStatus.CREDIT_APPROVED.value,
    PaymentStatus.PARTIAL_PAID.value,
}


TRANSITIONS: tuple[TransitionDef, ...] = (
    # --- 1. Pago (fuente: Woo webhook / manual back-office) ------------------
    TransitionDef(
        StatusDomain.PAYMENT, PaymentStatus.PENDING.value, PaymentStatus.PAID.value,
        "Pago recibido", _OFFICE_OR_SYSTEM,
    ),
    TransitionDef(
        StatusDomain.PAYMENT, PaymentStatus.PENDING.value,
        PaymentStatus.PARTIAL_PAID.value,
        "Pago parcial", _OFFICE,
    ),
    TransitionDef(
        StatusDomain.PAYMENT, PaymentStatus.PARTIAL_PAID.value,
        PaymentStatus.PAID.value,
        "Pago completado", _OFFICE_OR_SYSTEM,
    ),
    TransitionDef(
        StatusDomain.PAYMENT, PaymentStatus.PENDING.value,
        PaymentStatus.CREDIT_APPROVED.value,
        "Crédito B2B aprobado", frozenset({_ADMIN, _MANAGER}),
    ),
    TransitionDef(
        StatusDomain.PAYMENT, PaymentStatus.PENDING.value, PaymentStatus.FAILED.value,
        "Pago fallido", frozenset({SYSTEM}),
    ),
    TransitionDef(
        StatusDomain.PAYMENT, PaymentStatus.FAILED.value, PaymentStatus.PENDING.value,
        "Reintentar cobro", _OFFICE_OR_SYSTEM,
    ),
    TransitionDef(
        StatusDomain.PAYMENT, PaymentStatus.PAID.value, PaymentStatus.REFUNDED.value,
        "Reembolso", frozenset({_ADMIN}),
        required_evidence=("reason",),
    ),
    # --- 2. Preparación (Cola PEDIDOS → Cola SAT) ----------------------------
    TransitionDef(
        StatusDomain.PREPARATION, PreparationStatus.PENDING_REVIEW.value,
        PreparationStatus.IN_QUEUE.value,
        "Aprobar pedido", frozenset({_ADMIN, _PEDIDOS}),
    ),
    TransitionDef(
        StatusDomain.PREPARATION, PreparationStatus.IN_QUEUE.value,
        PreparationStatus.PREPARING.value,
        "Empezar preparación", frozenset({_ADMIN, _SAT}),
        guards=(GUARD_PAYMENT_OK,),
    ),
    TransitionDef(
        StatusDomain.PREPARATION, PreparationStatus.PREPARING.value,
        PreparationStatus.PACKED.value,
        "Embalado", frozenset({_ADMIN, _SAT}),
        guards=(GUARD_PACKAGES_MEASURED,),
    ),
    TransitionDef(
        StatusDomain.PREPARATION, PreparationStatus.PREPARING.value,
        PreparationStatus.BLOCKED.value,
        "Bloquear (problema)", frozenset({_ADMIN, _PEDIDOS, _SAT}),
        required_evidence=("reason",),
    ),
    TransitionDef(
        StatusDomain.PREPARATION, PreparationStatus.IN_QUEUE.value,
        PreparationStatus.BLOCKED.value,
        "Bloquear en cola", frozenset({_ADMIN, _PEDIDOS, _SAT}),
        required_evidence=("reason",),
    ),
    TransitionDef(
        StatusDomain.PREPARATION, PreparationStatus.BLOCKED.value,
        PreparationStatus.PREPARING.value,
        "Desbloquear", frozenset({_ADMIN, _PEDIDOS}),
    ),
    TransitionDef(
        StatusDomain.PREPARATION, PreparationStatus.PACKED.value,
        PreparationStatus.IN_QUEUE.value,
        # Fase D-1-fix1: el operativo SAT que embaló puede reabrir si detecta un
        # error de picking tarde (además de admin).
        "Reabrir (error de picking)", frozenset({_ADMIN, _SAT}),
        required_evidence=("reason",),
    ),
    # --- 3. Transporte -------------------------------------------------------
    # Fase D-1-fix1: SAT (taller) también dispara el envío/recogida — son
    # acciones físicas del almacén, no solo de back-office.
    TransitionDef(
        StatusDomain.TRANSPORT, TransportStatus.NOT_SHIPPED.value,
        TransportStatus.LABEL_CREATED.value,
        "Crear envío", _OFFICE_OR_SYSTEM | {_SAT},
        guards=(GUARD_PACKED,),
    ),
    TransitionDef(
        StatusDomain.TRANSPORT, TransportStatus.LABEL_CREATED.value,
        TransportStatus.IN_TRANSIT.value,
        "Recogido / en tránsito", _OFFICE_OR_SYSTEM | {_SAT},
        guards=(GUARD_TRACKING,),
    ),
    TransitionDef(
        StatusDomain.TRANSPORT, TransportStatus.IN_TRANSIT.value,
        TransportStatus.DELIVERED.value,
        "Entregado", _OFFICE_OR_SYSTEM,
    ),
    TransitionDef(
        StatusDomain.TRANSPORT, TransportStatus.IN_TRANSIT.value,
        TransportStatus.INCIDENT.value,
        "Incidencia transporte", _OFFICE_OR_SYSTEM,
        required_evidence=("description",),
    ),
    TransitionDef(
        StatusDomain.TRANSPORT, TransportStatus.INCIDENT.value,
        TransportStatus.IN_TRANSIT.value,
        "Incidencia resuelta", _OFFICE,
    ),
    TransitionDef(
        StatusDomain.TRANSPORT, TransportStatus.INCIDENT.value,
        TransportStatus.RETURNED.value,
        "Devuelto al remitente", _OFFICE,
    ),
    TransitionDef(
        StatusDomain.TRANSPORT, TransportStatus.RETURNED.value,
        TransportStatus.NOT_SHIPPED.value,
        "Reexpedir", frozenset({_ADMIN}),
    ),
    # --- 4. Facturación (FACTUSOL manda la numeración) -----------------------
    TransitionDef(
        StatusDomain.INVOICE, InvoiceStatus.NOT_INVOICED.value,
        InvoiceStatus.PENDING.value,
        "Solicitar factura", _OFFICE_OR_SYSTEM,
    ),
    TransitionDef(
        StatusDomain.INVOICE, InvoiceStatus.PENDING.value,
        InvoiceStatus.GENERATED.value,
        "Factura generada", _OFFICE_OR_SYSTEM,
        guards=(GUARD_INVOICEABLE,),
    ),
    TransitionDef(
        StatusDomain.INVOICE, InvoiceStatus.PENDING.value, InvoiceStatus.ERROR.value,
        "Error al generar", frozenset({SYSTEM}),
    ),
    TransitionDef(
        StatusDomain.INVOICE, InvoiceStatus.ERROR.value, InvoiceStatus.PENDING.value,
        "Reintentar factura", _OFFICE,
    ),
    TransitionDef(
        StatusDomain.INVOICE, InvoiceStatus.GENERATED.value,
        InvoiceStatus.CREDIT_NOTE.value,
        "Abono emitido", frozenset({_ADMIN}),
        required_evidence=("reason",),
    ),
)


#: Columna del Order por dominio (el engine actualiza la correcta).
DOMAIN_COLUMN: dict[StatusDomain, str] = {
    StatusDomain.PAYMENT: "payment_status",
    StatusDomain.PREPARATION: "preparation_status",
    StatusDomain.TRANSPORT: "transport_status",
    StatusDomain.INVOICE: "invoice_status",
}


def find_transition(
    domain: StatusDomain, from_status: str, to_status: str
) -> TransitionDef | None:
    for t in TRANSITIONS:
        if (
            t.domain == domain
            and t.from_status == from_status
            and t.to_status == to_status
        ):
            return t
    return None


def transitions_from(domain: StatusDomain, from_status: str) -> list[TransitionDef]:
    return [
        t for t in TRANSITIONS
        if t.domain == domain and t.from_status == from_status
    ]
