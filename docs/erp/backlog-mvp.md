# Backlog MVP — Sprint 1 (Pedidos + SAT)

Priorizado tras el descubrimiento. Estimaciones en PRs "tamaño CRM" (el
ritmo real de este repo: PR mediano ≈ 1 sesión larga con tests+CI+merge).
Total estimado MVP: **10-13 PRs** en 3 fases. Los ítems 🔓 dependen de
credenciales/acciones de Bart — pueden bloquear la fase si no llegan antes.

## Fase A — cimientos (sin dependencias externas)

| # | Ítem | Tamaño | Notas |
|---|---|---|---|
| A1 | Migración 0080: `orders`, `order_lines`, `order_state_history`, `exception_records`, `integration_events` | M | data-model.md; SIN products/shipments/invoices aún |
| A2 | Máquina de estados declarativa (tabla de transiciones + service + tests) | M | patrón trigger_definitions; matriz de permisos state-machines.md |
| A3 | API admin pedidos: lista con filtros por los 4 estados + detalle + transición manual con evidencia | M | roles admin/office/sat sobre el sistema de permisos per-user existente |
| A4 | UI: bandeja de pedidos + ficha de pedido (4 chips de estado + historial + acciones según rol) | L | el prototipo es la spec visual |
| A5 | UI: cola SAT táctil (queued→preparing→packed, botones grandes, nota de bloqueo) | M | pantalla más usada del taller |
| A6 | Bandeja de excepciones (lista + ack/resolve) | S | sobre exception_records |

## Fase B — WooCommerce en vivo 🔓 (keys de mbolasers.com)

| # | Ítem | Tamaño | Notas |
|---|---|---|---|
| B1 | `integration_accounts` para tiendas Woo + migración de los scripts S0 al `IntegrationHTTPClient` | M | multi-tienda desde el día 1 |
| B2 | Webhook receiver productivo (registrar router + encolar RQ + firma + red de seguridad por polling) | M | base: webhooks_prototype.py |
| B3 | Sync de pedidos: upsert idempotente + disparo de máquina de pago (`date_paid`) | M | orders/order_lines |
| B4 | Migración 0081 `products` + `product_sku_mapping` + pantalla admin de conciliación + excepción `sku_unmapped` | M | script S0 como seed |

## Fase C — FACTUSOL en vivo 🔓 (credenciales + validación esquema)

| # | Ítem | Tamaño | Notas |
|---|---|---|---|
| C1 | Ejecutar descubrimiento (schema DISCOVERED + write flows reales) y fusionar docs | S | desde máquina con acceso; cierra preguntas abiertas |
| C2 | `worker-factusol` (cola RQ concurrencia 1) + cliente sobre integration_accounts | M | serialización de escrituras |
| C3 | Botón «Crear en FACTUSOL» en ficha de empresa (F_CLI + chip + CODCLI en external_references) | M | primera escritura real end-to-end |
| C4 | Migración 0082 `invoices` + flujo request_invoice→invoiced con guards (paid + SKUs confirmados) | L | numeración SIEMPRE de FACTUSOL |

## Fuera del MVP (Sprint 2+)

- Shipments con API real Genei/DSV (mientras: transporte manual con
  tracking obligatorio — ya soportado por la máquina de estados).
- PurchaseNeed automático por stock mínimo (Sprint 1 solo alta manual si da tiempo).
- Presupuestos/pedidos FACTUSOL (F_PRE/F_PED) desde el CRM.
- Portal B2B de clientes.

## Riesgos que mueven la estimación

1. **Esquema FACTUSOL distinto de lo esperado** (nombres de tablas de
   líneas, política de numeración) → C4 puede crecer +1 PR.
2. **Sin bulk en API DELSOL** → sync inicial de catálogo lento pero
   asumible (worker nocturno); no bloquea MVP.
3. **Woo sin SKUs poblados** en tiendas reales → la conciliación pasa de
   automática a mayormente manual (+ trabajo de Bart, no de código).
4. **Credenciales tardías** → Fase A avanza igual (cero dependencias).
