# Máquinas de estado del pedido — 4 dominios independientes

Cada pedido lleva **4 máquinas de estado paralelas** (decisión cerrada):
pago, preparación, transporte y facturación. Ninguna bloquea a las otras
salvo los cruces explícitos marcados como *guard*. Cada transición se
registra en `order_state_history` (quién, cuándo, evidencia, origen
manual/webhook/api).

**PROPUESTA para cerrar con Bart** — las transiciones/roles/evidencias
marcadas ⚠️ son las que necesitan su confirmación explícita.

Roles: `admin` (Bart), `sat` (técnico taller), `office` (administración),
`system` (webhooks/sync automáticos).

## 1. Pago (fuente de verdad: WooCommerce / manual en pedidos B2B)

```mermaid
stateDiagram-v2
    [*] --> pending
    pending --> paid: payment_received (system via Woo date_paid · office manual B2B)
    pending --> partially_paid: partial_payment (office) ⚠️ ¿anticipos B2B?
    partially_paid --> paid: payment_completed (office)
    pending --> payment_failed: payment_failed (system)
    payment_failed --> pending: retry_payment (system/office)
    paid --> refunded: refund (admin SOLO) — evidencia: motivo obligatorio
    [*] --> paid: pedido manual ya cobrado (office)
```

- `payment_received` automático: webhook `order.updated` con `date_paid`
  poblado (no existe topic nativo `payment_complete`, ver woocommerce-integration.md).
- ⚠️ Pregunta a Bart: ¿existen anticipos/pagos parciales en B2B (palés)?
  Si no, se elimina `partially_paid` del MVP.

## 2. Preparación (fuente de verdad: cola SAT del ERP)

```mermaid
stateDiagram-v2
    [*] --> queued: pedido entra en cola SAT (system)
    queued --> preparing: start_preparation (sat) — botón grande en cola táctil
    preparing --> packed: mark_packed (sat) ⚠️ evidencia: foto obligatoria SÍ/NO
    preparing --> blocked: report_issue (sat) — evidencia: nota obligatoria
    blocked --> preparing: unblock (admin/office)
    packed --> queued: reopen (admin SOLO) — reabre por error de picking
```

- ⚠️ Pregunta a Bart: ¿la foto del bulto antes de `packed` es obligatoria
  (evidencia adjunta) u opcional? Impacta la UI táctil del SAT.
- *Guard*: `start_preparation` requiere pago `paid` **salvo** override
  `admin` (⚠️ ¿se prepara sin cobrar en B2B con crédito?).

## 3. Transporte (fuente de verdad: Genei/DSV, entrada manual mientras no haya API)

```mermaid
stateDiagram-v2
    [*] --> not_shipped
    not_shipped --> label_created: create_shipment (office/system) — evidencia: carrier+servicio
    label_created --> in_transit: pickup_confirmed (system webhook/polling · office manual) ⚠️ tracking number obligatorio
    in_transit --> delivered: delivery_confirmed (system/office)
    in_transit --> incident: incident_reported (system/office) — evidencia: descripción
    incident --> in_transit: incident_resolved (office)
    incident --> returned: returned_to_sender (office)
    returned --> not_shipped: reship (admin)
```

- *Guard*: `create_shipment` requiere preparación `packed`.
- ⚠️ Confirmación Bart: `in_transit` exige `tracking_number` no vacío (la
  transición falla sin él) — parece obvio pero fuerza disciplina manual
  mientras DSV/Genei van por entrada manual.

## 4. Facturación (fuente de verdad: FACTUSOL)

```mermaid
stateDiagram-v2
    [*] --> not_invoiced
    not_invoiced --> invoice_pending: request_invoice (system al llegar paid · office manual)
    invoice_pending --> invoiced: invoice_created (system worker-factusol) — evidencia: nº factura FACTUSOL
    invoice_pending --> invoice_error: creation_failed (system) → bandeja excepciones
    invoice_error --> invoice_pending: retry (office/admin)
    invoiced --> credit_note: credit_note_issued (admin SOLO) — evidencia: motivo + nº abono
```

- *Guards*: `request_invoice` requiere pago `paid` Y todos los SKU del
  pedido con mapping confirmado (excepción `sku_unmapped` si no, ver
  sku-conciliation.md). La numeración de factura la asigna FACTUSOL —
  el ERP NUNCA inventa números (pendiente de confirmar política de
  numeración en el descubrimiento de la API).
- ⚠️ Pregunta a Bart: ¿factura automática al cobrar (pedidos web) o
  siempre con revisión manual en `invoice_pending`?

## Matriz de permisos (resumen)

| Transición | admin | office | sat | system |
|---|---|---|---|---|
| payment_received | ✔ | ✔ | ✖ | ✔ |
| refund | ✔ | ✖ | ✖ | ✖ |
| start_preparation / mark_packed | ✔ | ✖ | ✔ | ✖ |
| reopen (packed→queued) | ✔ | ✖ | ✖ | ✖ |
| create_shipment | ✔ | ✔ | ✖ | ✔ |
| incident_* | ✔ | ✔ | ✖ | ✔ |
| request_invoice / retry | ✔ | ✔ | ✖ | ✔ |
| credit_note_issued | ✔ | ✖ | ✖ | ✖ |

Implementación (Sprint 1): tabla de transiciones declarativa (dominio,
from, to, roles[], evidencia_requerida[], guards[]) validada en el service
layer — misma filosofía que `trigger_definitions.py` de workflows: datos,
no ifs dispersos.
