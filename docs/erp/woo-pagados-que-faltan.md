# WooCommerce — pedidos pagados que no llegan a BoHub

**Caso (07/10/2026).** El pedido 99976 de boprint se creó `on-hold`. Sus
webhooks llegaron y se ignoraron (aún no estaba pagado: BoHub solo crea los
pagados). A las 09:02 el plugin de TSM lo marcó pagado **sin que la tienda
disparara ningún webhook**, así que nunca entró. Hubo otro igual en
fluxlasers. «Poner al día estados Woo…» solo miraba los pedidos que BoHub ya
conocía. Se desatascó a mano con `POST /stores/{store_id}/reimport-order`.

## Qué hay ahora

Tres piezas, las tres sobre el mismo núcleo (`backend/app/integrations/woocommerce/missing.py`):

| Pieza | Dónde | Qué hace |
|---|---|---|
| Puesta al día | Seguimiento → «Poner al día estados Woo…» (`jobs.run_woo_reconcile`, cola `erp:interactive`) | Además de refrescar los conocidos, lista (vista previa) e importa (aplicar) los pagados que faltan de los últimos N días. |
| Repaso periódico | `missing_job.py`, cola `woocommerce:backfill` (`worker-web`, `--with-scheduler`) | Cada hora (configurable) importa los pagados que falten, mirando solo lo modificado desde el repaso anterior. |
| Aviso | Cuadre, `pedido_woo_pagado_sin_bohub` (fuente `woocommerce`, severidad alta) | Lista por tienda los pagados que faltan, aunque la importación falle. Solo lee. |

## El núcleo

- **Qué se pide.** Una consulta por tienda (paginada de 100 en 100, hasta
  10.000): `status=completed,processing,refunded` (los estados de
  `CREATE_ON_STATUSES`, con los que el importador crea un pedido), `after` =
  hace N días (creación), `dates_are_gmt=true`, `_fields` con solo los campos
  que hacen falta (el listado pesa poco) y, en el repaso, `modified_after` =
  repaso anterior menos 15 min. Un `on-hold`/`pending` no se pide, y si la tienda lo
  devolviera igual, se descarta.
- **Qué falta.** Lo que BoHub no tiene por (`external_source` WooCommerce,
  `external_id`, tienda —o sin tienda, por prudencia—) ni por el número que
  le pondría el importador (`BOPRIN-99976`; por si se dio de alta a mano).
  Los modificados hace menos de 5 min se dejan para la pasada siguiente: su
  webhook puede estar en camino.
- **Cómo se importa.** Igual que «Reimportar pedido»: el pedido **fresco**
  (`get_order`, solo de los que faltan), `jobs.upsert_backfill_event` (evento
  `backfill:{id}`) +
  `import_order_from_event` → `import_woo_order`, el punto único de ingesta
  del webhook. Empresa, líneas, método de pago, corte externo y Cola SAT
  quedan como si hubiera llegado el webhook. Un pedido que ya está en BoHub
  **no se pasa al importador**: ni se refresca ni se pisa (salvo un traído a
  mano aún sin cobrar, abajo). Justo antes de importar cada uno se vuelve a
  mirar (por si entró un webhook mientras tanto: queda `ya_estaba`).
- **Una importación por tienda a la vez.** «Poner al día…» (worker-factusol) y
  el repaso (worker-web) cogen un cerrojo en Redis por tienda
  (`woocommerce:missing:lock:{tienda}`); si está cogido, esa tienda se salta
  con un aviso. La vista previa no lo necesita.
- **Idempotente.** Una segunda pasada no encuentra nada que importar.
- **Tope.** 50 importaciones por pasada y 20 páginas por tienda; el resto, en
  la siguiente (se marca `con_tope`).
- **Rastro.** Al importar (no en vista previa) deja una fila en `sync_logs`
  por tienda, operación `import_missing` (`manual` o `cron`), con los números
  importados. La última **completa** (`success`: la tienda respondió, sin tope
  y sin importaciones fallidas) marca desde dónde mira el repaso siguiente; si
  alguna falló, el siguiente vuelve a mirar desde la misma marca y la
  reintenta. La primera vez de la tienda mira el último día (los 90, con
  vista previa, son de «Poner al día…»); si hubo pasadas pero ninguna
  completa, los N días.

## Pedidos traídos a mano

Un pedido `on-hold` traído a mano desde ERP · Pedidos
([pedidos-web-en-espera.md](pedidos-web-en-espera.md)) ya está en BoHub con el
pago «pendiente». Si la tienda lo cobra **sin** disparar el webhook (el caso
TSM de arriba), el repaso lo ve en el listado de pagados y, en vez de
saltárselo como «ya está», lo **refresca** por el mismo camino del webhook
(`get_order` fresco + `import_order_from_event` → `import_woo_order` →
`_refresh_existing`): pago `pending → paid`, `woo_status` al día y, si sigue sin
aprobar, a la Cola SAT. Sin copiar lógica del importador y sin crear otro.

- **Cuáles.** Los que BoHub tiene por (WooCommerce, id de Woo, esta tienda) con
  `manual_web_import_at` puesto y `payment_status = pending`, y que la tienda
  tiene `processing` o `completed` (`TRAIDOS_COBRADOS`; un `refunded` no: lo
  trae el webhook). Los demás que ya están se siguen saltando.
- **No «faltan».** Van aparte, en `traidos`: no cuentan en `faltan` ni los avisa
  el Cuadre.
- **Las mismas reglas.** Gracia de 5 min, cerrojo por tienda, tope por pasada
  (comparten las 50) y comprobación justo antes (`ya_estaba` si el webhook ya lo
  puso al día). Uno que falla cuenta como fallido: la marca del repaso no
  avanza y el siguiente lo reintenta.
- **Rastro.** La fila de `sync_logs` suma los refrescados en
  `records_processed`, los nombra en el mensaje («traídos a mano con el cobro
  al día N») y guarda `refrescados` / `numeros_refrescados` en sus metadatos.

## Configuración ERP → «Pedidos web pagados que no llegan»

Blob `factusol_series_json`:

| Clave | Defecto | Qué es |
|---|---|---|
| `woo_missing_check_enabled` | `true` | Interruptor del repaso periódico. Apagado, el tic sigue armado y no hace nada. |
| `woo_missing_check_interval_minutes` | `60` (mín. 15) | Cada cuánto corre. Si ya hubo uno hace menos de medio intervalo, no repite. |
| `woo_missing_days` | `90` | Días hacia atrás del repaso y de «Poner al día…». El Cuadre tiene su propio umbral (90). |

## Resumen de «Poner al día estados Woo…»

`run_woo_reconcile` devuelve el resumen de siempre más:

- `refreshed_total`: pedidos conocidos puestos al día;
- `missing`: `{dias, faltan, importados, items, traidos, refrescados, errores,
  con_tope, woo_calls}`. Cada item lleva tienda, número, cliente, importe,
  moneda, fecha de pago, enlace a la tienda y `resultado` (`a_importar` en
  vista previa; `importado`, `error` o `sin_importar` al aplicar), y si se
  importó, `order_number`. Los de `traidos` tienen la misma forma con
  `resultado` `a_refrescar` (vista previa), `refrescado` (con `order_id`,
  `order_number` y `pagado`), `error`, `sin_refrescar` (tope) o `ya_estaba`.

Si la búsqueda de lo que falta revienta, la puesta al día de estados se
conserva y `missing` trae `error`.

## Tests

`backend/tests/test_erp_woo_pagados_que_faltan.py` (la tienda es un doble;
nunca se llama a una real). El refresco de los traídos a mano, en
`backend/tests/test_erp_pedido_traido_a_mano_normal.py`.
