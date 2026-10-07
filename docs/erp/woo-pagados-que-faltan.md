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

- **Qué se pide.** Una consulta por tienda (paginada de 100 en 100):
  `status=completed,processing,refunded` (los estados de `CREATE_ON_STATUSES`,
  con los que el importador crea un pedido), `after` = hace N días (creación),
  `dates_are_gmt=true` y, en el repaso, `modified_after` = repaso anterior
  menos 15 min. Un `on-hold`/`pending` no se pide, y si la tienda lo
  devolviera igual, se descarta.
- **Qué falta.** Lo que BoHub no tiene por (`external_source` WooCommerce,
  `external_id`, tienda —o sin tienda, por prudencia—) ni por el número que
  le pondría el importador (`BOPRIN-99976`; por si se dio de alta a mano).
  Los modificados hace menos de 5 min se dejan para la pasada siguiente: su
  webhook puede estar en camino.
- **Cómo se importa.** Igual que «Reimportar pedido»:
  `jobs.upsert_backfill_event` (evento `backfill:{id}`) +
  `import_order_from_event` → `import_woo_order`, el punto único de ingesta
  del webhook. Empresa, líneas, método de pago, corte externo y Cola SAT
  quedan como si hubiera llegado el webhook. Un pedido que ya está en BoHub
  **no se pasa al importador**: ni se refresca ni se pisa.
- **Idempotente.** Una segunda pasada no encuentra nada que importar.
- **Tope.** 50 importaciones por pasada y 20 páginas por tienda; el resto, en
  la siguiente (se marca `con_tope`).
- **Rastro.** Al importar (no en vista previa) deja una fila en `sync_logs`
  por tienda, operación `import_missing` (`manual` o `cron`), con los números
  importados. La última sin tope marca desde dónde mira el repaso siguiente;
  la primera vez, el último día (los 90, con vista previa, son de «Poner al
  día…»).

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
- `missing`: `{dias, faltan, importados, items, errores, con_tope, woo_calls}`.
  Cada item lleva tienda, número, cliente, importe, moneda, fecha de pago,
  enlace a la tienda y `resultado` (`a_importar` en vista previa; `importado`,
  `error` o `sin_importar` al aplicar), y si se importó, `order_number`.

Si la búsqueda de lo que falta revienta, la puesta al día de estados se
conserva y `missing` trae `error`.

## Tests

`backend/tests/test_erp_woo_pagados_que_faltan.py` (la tienda es un doble;
nunca se llama a una real).
