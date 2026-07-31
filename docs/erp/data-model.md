# Contratos de datos — entidades del dominio ERP

Convenciones heredadas del CRM: PK `String(36)` uuid4, timestamps
`TimestampMixin`, JSON como `Text` + json.dumps, "enums" como String +
constante validada en capa API (no ENUM MySQL). **Ninguna tabla se crea en
Sprint 0** — la primera migración real es del MVP.

Fuente de verdad (FdV) por entidad — regla general: el ERP es dueño de la
ORQUESTACIÓN (estados, colas, excepciones); los sistemas externos son
dueños de SUS datos (Woo: pedido original y pago; FACTUSOL: numeración
fiscal; carriers: tracking).

## Order

| Columna | Tipo | Notas |
|---|---|---|
| `id` | String(36) PK | |
| `source` | String(16) | `woocommerce` / `manual` |
| `woo_store_id` | String(64) NULL | slug tienda (NULL en manual) |
| `woo_order_id` | Integer NULL | UNIQUE con store; id externo |
| `order_number` | String(32) | visible; el de Woo o serie manual |
| `company_id` | FK companies NULL | B2B; reusa companies del CRM (mapa validado) |
| `contact_id` | FK contacts NULL | comprador; reusa contacts |
| `payment_status` | String(24) | dominio 1 (state-machines.md) |
| `preparation_status` | String(24) | dominio 2 |
| `shipping_status` | String(24) | dominio 3 |
| `invoicing_status` | String(24) | dominio 4 |
| `currency` | String(3) | |
| `total_amount` | Numeric(12,2) | |
| `billing_json` / `shipping_json` | Text | snapshot dirección al momento del pedido |
| `raw_source_json` | Text | payload Woo original (auditoría/debug) |
| `placed_at` | DateTime | fecha real del pedido en origen |

FdV: Woo para datos del pedido original; ERP para los 4 estados.
Sync: webhook + polling de red de seguridad; upsert idempotente por
(`woo_store_id`,`woo_order_id`).

- Cardinalidad: Order 1—N OrderLine · 1—N Shipment · 1—N Invoice · 1—N
  ExceptionRecord · 1—N OrderStateHistory.

## OrderLine

| Columna | Tipo | Notas |
|---|---|---|
| `id` / `order_id` FK | | CASCADE |
| `woo_line_id` | Integer NULL | id de línea Woo |
| `sku` | String(128) | tal cual del origen |
| `product_id` | FK products NULL | resuelto vía product_sku_mapping |
| `description` | String(255) | |
| `quantity` | Numeric(10,2) | |
| `unit_price` / `total` | Numeric(12,2) | |
| `tax_rate` | Numeric(5,2) | |

## Product

| Columna | Tipo | Notas |
|---|---|---|
| `id` | String(36) PK | |
| `factusol_codart` | String(13) UNIQUE NULL | FdV catálogo: FACTUSOL |
| `name` | String(255) | |
| `family` | String(64) NULL | F_FAM |
| `price` / `cost` | Numeric(12,2) NULL | |
| `tax_rate` | Numeric(5,2) NULL | IVAART |
| `stock_cached` | Numeric(10,2) NULL | caché de F_STO, refresco programado |
| `is_active` | Boolean | |

FdV: FACTUSOL (el ERP no edita catálogo; lo lee). Woo mantiene su propio
catálogo — el puente es `product_sku_mapping` (ver sku-conciliation.md).

## Company (REUSA `companies` del CRM — mapa validado)

Sin tabla nueva. Extensiones vía `external_references_json` (clave
`factusol` → CODCLI) — patrón ya existente para Agile/Brevo. El botón
«Crear en FACTUSOL» (prototipo) escribe F_CLI vía worker-factusol y guarda
el CODCLI en esa referencia. FdV: CRM para datos comerciales; FACTUSOL
para su propio maestro de clientes (no se pisan mutuamente; sync dirigido
CRM→FACTUSOL bajo demanda).

## Shipment

| Columna | Tipo | Notas |
|---|---|---|
| `id` / `order_id` FK | | |
| `carrier` | String(16) | `genei` / `dsv` / `manual` |
| `carrier_shipment_id` | String(64) NULL | id externo |
| `tracking_number` | String(64) NULL | obligatorio para in_transit (guard) |
| `label_url` | String(512) NULL | PDF/PNG etiqueta |
| `status` | String(24) | normalizado (dominio transporte) |
| `raw_status` | String(64) NULL | literal del carrier |
| `events_json` | Text | histórico tracking |
| `packages_json` | Text | bultos: peso/dimensiones |

FdV: carrier (via API/webhook cuando exista; manual mientras tanto).

## Invoice

| Columna | Tipo | Notas |
|---|---|---|
| `id` / `order_id` FK | | |
| `factusol_series` | String(8) | serie |
| `factusol_number` | String(16) | número asignado POR FACTUSOL |
| `factusol_exercise` | String(4) | ejercicio |
| `type` | String(16) | `invoice` / `credit_note` |
| `total_amount` | Numeric(12,2) | |
| `issued_at` | DateTime | |
| `pdf_url` | String(512) NULL | si la API lo expone (confirmar) |

FdV: FACTUSOL — el ERP solo referencia. UNIQUE (serie, número, ejercicio).

## PurchaseNeed (necesidades de compra)

| Columna | Tipo | Notas |
|---|---|---|
| `id` | | |
| `product_id` FK | | |
| `order_id` FK NULL | pedido que la dispara (NULL = stock mínimo) |
| `quantity_needed` | Numeric(10,2) | |
| `status` | String(16) | `open` / `ordered` / `received` / `cancelled` |
| `supplier_note` | Text NULL | |

FdV: ERP. Se genera cuando una línea de pedido supera `stock_cached` (⚠️
regla exacta a cerrar con Bart en el MVP; en Sprint 1 solo la tabla + alta
manual).

## IntegrationEvent

Bitácora unificada de todos los eventos de integración (webhook recibido,
job de escritura FACTUSOL, polling tracking):

| Columna | Tipo | Notas |
|---|---|---|
| `id` | | |
| `system` | String(24) | `woocommerce` / `factusol` / `genei` / `dsv` |
| `direction` | String(8) | `in` / `out` |
| `event_type` | String(64) | `order.updated`, `factusol.escribir_registro`… |
| `target_type` / `target_id` | String | entidad ERP afectada |
| `status` | String(16) | `ok` / `error` / `retrying` |
| `payload_json` | Text | recortado; sin secretos |
| `error` | Text NULL | |
| `occurred_at` | DateTime index | |

Equivale al patrón `ActivityEvent`+auditoría del CRM aplicado a máquinas.

## ExceptionRecord (bandeja de excepciones)

| Columna | Tipo | Notas |
|---|---|---|
| `id` | | |
| `order_id` FK NULL | |
| `kind` | String(32) | `sku_unmapped`, `factusol_write_failed`, `webhook_signature_invalid`, `shipping_incident`, `payment_mismatch` |
| `dedup_key` | String(128) UNIQUE NULL | anti-repetición (p.ej. store+sku) |
| `status` | String(16) | `open` / `ack` / `resolved` |
| `detail_json` | Text | contexto para resolver |
| `resolved_by_user_id` / `resolved_at` | | |

FdV: ERP. Es LA bandeja del prototipo — todo lo que rompe el flujo feliz
aterriza aquí con acción de resolución.

## OrderStateHistory

| Columna | Tipo | Notas |
|---|---|---|
| `id` / `order_id` FK | | |
| `domain` | String(16) | pago/preparación/transporte/facturación |
| `from_status` / `to_status` | String(24) | |
| `actor_user_id` | FK NULL | NULL = system |
| `via` | String(16) | `ui` / `webhook` / `sync` / `api` |
| `evidence_json` | Text NULL | foto, tracking, nº factura, motivo |
| `occurred_at` | DateTime index | |

Alimenta el historial de la ficha de pedido y la auditoría de la matriz de
permisos.
