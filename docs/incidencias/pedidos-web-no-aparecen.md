# Incidencia — los pedidos web dejan de aparecer en la bandeja (~13-sep)

**Estado:** diagnóstico (SOLO LECTURA). Este documento explica la causa; **no
se ha tocado la lógica de ingesta**. El arreglo va en un paso aparte.

## Resumen

Desde el despliegue de **PR #387** («Woo: SOLO se crean pedidos en estado
`processing`», commit `0c05c6b`, **2026-09-10**, en producción ~13-sep), un
pedido web solo se **crea** en BoHub si su estado de WooCommerce es
**exactamente `processing`** en el momento en que se procesa su webhook.
Cualquier otro estado (`on-hold`, `pending`, `completed`, `cancelled`,
`refunded`, `failed`, `checkout-draft`) se **descarta sin crear el pedido** —
no se crea oculto ni excluido: **no se crea en absoluto**.

Como el descarte se registra como un webhook procesado con **éxito** (no como
error), el panel de Integraciones sigue diciendo «recibidos, 0 errores» y la
cola RQ se vacía con normalidad, mientras los pedidos desaparecen en silencio.

## Causa raíz (fichero:línea)

- **El filtro:** `backend/app/integrations/woocommerce/mapper.py`
  - `CREATE_ON_STATUSES = frozenset({"processing"})` (≈ línea 72).
  - `should_create_order()` = `(_woo_status(woo_order) or "") in CREATE_ON_STATUSES`
    (≈ línea 75-76).
  - La compuerta en `import_woo_order`: si el pedido es desconocido y no está en
    `processing`, devuelve `ImportOutcome(order_id=None, skipped_status=…)` sin
    crear pedido/contacto/empresa (≈ línea 90-106). `import_woo_order` es el
    **punto único** de ingesta que usan el webhook y el sync.
  - `_woo_status()` normaliza a minúsculas (insensible a mayúsculas), pero **no
    quita el prefijo `wc-`** (≈ línea 234-238). Latente: el webhook refetchea
    por REST (`WooHTTPClient.get_order`, `client.py:154`), que devuelve el
    estado **sin** `wc-`, así que hoy no se dispara; sería un fallo si algún
    día un payload trajera `wc-processing`.
- **Por qué es invisible:** `backend/app/integrations/woocommerce/jobs.py`
  - `process_webhook_event` refetchea el pedido y llama a `import_woo_order`
    (≈ línea 324-326); un descarte deja el evento en `PROCESSED`.
  - `_log_webhook_sync` escribe el descarte como `webhook_process` **SUCCESS**
    con el mensaje `"order ignored (status=X, not processing)"` (≈ línea
    433-437). El panel solo cuenta `FAILED` como error.
- **Sin red de seguridad:** no hay sync periódico de pedidos Woo (solo webhooks
  en tiempo real); y el backfill/sync manual **también** pide
  `status="processing"` a Woo (`jobs.py:208-210`, `client.py:140-152`), así que
  ni re-ejecutándolo se recuperan los `on-hold`/`pending`.

## Por qué un pedido «pagado» no pasa el filtro

Un pedido genuinamente en `processing` **sí** pasa hoy (lo prueban los tests de
#387: webhook `order.updated` con `processing` → se crea). El problema es que
**los pedidos legítimos de Bomedia no siempre están en `processing`** cuando se
procesa su (único) webhook:

- **Transferencia bancaria (BACS) → `on-hold`**: el pedido queda «a la espera»
  hasta que se confirma el pago a mano; muchos B2B de Bomedia pagan así y
  **nunca pasan por `processing` automáticamente** → siempre descartados.
- **Contrareembolso / cheque → `pending`** → descartados.
- **Pedidos marcados `completed` directamente** (algunos gateways/plugins) →
  descartados (la premisa «no se llega a completed sin pasar por processing» es
  falsa en esos casos).
- **Solo `order.created`**: si una tienda solo tiene activo `order.created`
  (que dispara cuando el pedido aún puede estar `pending`) y no
  `order.updated`/`order.payment_complete`, la transición posterior a
  `processing` no llega y el pedido nunca se importa. Antes de #387,
  `order.created` creaba el pedido en cualquier estado.

## Descartadas (no son la causa)

- **Cola/worker RQ:** `worker-sync` consume `woocommerce:webhooks`,
  `woocommerce:import` y `woocommerce:backfill` en ambos compose
  (`docker-compose.yml:105-107`, `docker-compose.prod.yml:187-189`). Sin
  desajuste de nombres.
- **La bandeja:** `GET /api/erp/orders` (`orders.py:661-751`) muestra por
  defecto los pedidos `pending_review` no externalizados y no excluidos, así
  que un pedido `processing` recién creado **sí** aparecería. No hay filtro
  nuevo por estado/cola que oculte pedidos nuevos.
- **Fecha de corte mal parseada (DD/MM):** el formulario usa `type="date"`
  (`YYYY-MM-DD`) y `_parse_dt` usa `datetime.fromisoformat`, que ante un
  `DD/MM/YYYY` **lanza y devuelve None** (falla abierto, sin corte). El único
  riesgo real sería una fecha **futura** bien formada, que se descarta con el
  diagnóstico (punto 4 de abajo).

## Cuántos pedidos afectados desde el 13 (SOLO LECTURA)

No puedo darlos desde el sandbox (no hay BD de producción). El script de
diagnóstico los cuenta en el VPS, sin escribir nada:

```
docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
    python -m scripts.diag_pedidos_web_no_aparecen
```

Devuelve, desde 2026-09-13 (o `--since`):

1. **Webhooks descartados por «solo processing», por estado de Woo** (leídos de
   `sync_logs`, mensaje `"order ignored (status=X, not processing)"`). El
   reparto por estado es la señal para el arreglo: si domina `on-hold`, el
   problema son las transferencias; si `completed`, los auto-completados, etc.
2. **Pedidos DISTINTOS** que llegaron por webhook y **nunca se crearon** (ids de
   Woo de `integration_events` que no tienen `Order`) — el «cuántos afectados»
   más fiel (los logs cuentan eventos, no pedidos).
3. **Creados-pero-ocultos** desde la fecha: pedidos web con
   `externally_processed_at` (fecha de corte) o `seguimiento_excluded_at`
   (limpieza reversible) — el otro modo de «no aparecer». Nota: la limpieza de
   #387 (`cleanup_non_processing_orders`) pudo excluir retroactivamente los
   `pending`/`on-hold` ya importados; aquí se ven.
4. **`external_cutoff_date` de cada tienda**, para descartar una fecha futura.

## Propuesta de arreglo (NO ejecutada aquí)

1. **Ampliar los estados que crean pedido** en `CREATE_ON_STATUSES` a los que
   representan un pedido real/pagado para Bomedia. Según lo que muestre el punto
   1 del diagnóstico, lo más probable: `{"processing", "on-hold", "completed"}`.
   Alternativa más robusta: **invertir** el filtro a una lista de EXCLUSIÓN de
   estados no accionables (`cancelled`, `refunded`, `failed`, `checkout-draft`,
   `trash`, y `pending` si se decide no importar los aún sin pagar), de modo que
   cualquier estado nuevo entre por defecto. Idealmente, configurable por tienda.
2. **Recuperar los perdidos desde el 13:** un backfill que NO fije
   `status="processing"` (o que recorra los estados ampliados) sobre la ventana
   afectada, re-importando los pedidos descartados. Idempotente por
   `(external_source, external_id, store_id)`.
3. **Endurecer `_woo_status`** para quitar también el prefijo `wc-` (defensivo).
4. **Hacer visible el descarte** en las métricas del panel (hoy un descarte es
   un webhook «con éxito»), para que esta clase de pérdida silenciosa se detecte.
5. **Revisar en cada tienda de WordPress** qué topics de webhook están activos
   (que estén `order.updated`/`order.payment_complete`, no solo `order.created`)
   y la `external_cutoff_date` de cada tienda (que no sea futura).

Cada punto se validará con tests antes de tocar producción.
