# Workers y colas RQ

Reparto de colas (RQ) por worker. Todos los workers comparten la imagen
`crmbomedia-api:latest` (mismo Python que el `api`, así que el serializer pickle
de RQ es válido) y viven en `docker-compose.prod.yml` (dev: `docker-compose.yml`).

## Por qué hay varios workers

Un `rq worker` procesa **sus colas en el orden en que se le pasan** y **de una
en una** (concurrencia 1 por proceso): drena por completo la primera cola con
trabajo antes de mirar la siguiente. Por eso una cola con jobs largos y
frecuentes al principio de la lista **mata de hambre** (starvation) a las de más
abajo. La solución recurrente ha sido **aislar** esas colas en su propio worker.

Incidencias que lo motivaron:

- `worker-gmail` — AgileCRM tenía `gmail:process_history` esperando.
- `worker-workflows` — `workflows:dispatch` quedaba en posición 22 de 24.
- **`worker-web` + `worker-agilecrm`** (este PR) — los `agilecrm:sync_contacts`
  paginan miles de contactos por cada una de las ~9 cuentas y tenían el
  `worker-sync` ocupado durante horas; los webhooks de WooCommerce
  (`woocommerce:webhooks`, cola #35 de la lista) no se procesaban hasta que
  AgileCRM soltaba el worker → **los pedidos web se atascaban** aunque el
  webhook respondía 200 y el job estaba encolado. Un `rq worker --burst` manual
  sobre `woocommerce:*` los procesaba al instante: era starvation, no un fallo
  de import.

## Reparto actual

| Worker            | Colas | Notas |
|-------------------|-------|-------|
| **worker-web**    | `woocommerce:webhooks`, `woocommerce:import`, `woocommerce:backfill` | **Ingesta web. Aislado**: nada de AgileCRM ni otros syncs lo bloquean, así que un pedido web entra en **segundos siempre**. `webhooks` va primero (máxima prioridad). `--with-scheduler` (07/10/2026): mueve el tic del repaso de pagados que faltan (`woocommerce:backfill`, ver `docs/erp/woo-pagados-que-faltan.md`) y los reintentos con espera de los webhooks. |
| **worker-agilecrm** | `agilecrm:periodic_read`, `agilecrm:sync_contacts`, `agilecrm:purge_quota` | **Baja prioridad, aislado** (AgileCRM en retirada). `--with-scheduler` para el heartbeat `periodic_read`. `periodic_read` primero para que el tick horario no se retrase tras un sync largo. |
| **worker-sync**   | `brevo:*`, `freshdesk:sync_tickets`, `factusol:sync_invoices`, `gmail:*`, `emails:snooze_sweep`, `email_templates:import_gmail`, `backups:create` (solo prod), `genei:shipments`, `genei:webhooks`, `seguimiento:reconcile`, `cuadre:run` | Sync externo + housekeeping. Ya **no** lleva AgileCRM ni WooCommerce. |
| **worker-workflows** | `workflows:dispatch`, `workflows:execute`, `workflows:scheduler`, `vies:sweep` | Motor de workflows. `--with-scheduler`. |
| **worker-factusol** | `erp:interactive`, `factusol:writes` | Escrituras FACTUSOL, **concurrencia 1** (numeración CODFAC secuencial: NO escalar réplicas). `erp:interactive` primero (trabajos que un usuario espera mirando). |
| **worker-gmail**  | `gmail:process_history`, `gmail:renew_watches`, `gmail:backfill_historic`, `gmail:backfill_per_contact`, `gmail:token_expiry_check`, `gmail:admin_digest`, `gmail:sync_aliases`, `gmail:poll_fallback` | Gmail en tiempo real. `--with-scheduler`. Comparte las colas Gmail con `worker-sync` (RQ reparte entre workers que escuchan la misma cola). |

**Invariante** (fijado en tests): ningún worker escucha a la vez una cola de
ingesta web (`woocommerce:*`) y una de AgileCRM (`agilecrm:*`). Aunque AgileCRM
sature su worker, la ingesta web sigue corriendo.

## AgileCRM: periódico, no continuo

El scheduler `agilecrm:periodic_read` encola un `sync_contacts` por cuenta
habilitada cada `AGILECRM_SYNC_INTERVAL_HOURS` (default **1 h**; override fino
con `AGILECRM_SYNC_INTERVAL_MINUTES`). Es periódico por diseño, pero como cada
`sync_contacts` puede durar más que el intervalo, antes se **apilaban**: cada
hora sumaba 9 jobs más sobre los que aún corrían, y AgileCRM nunca quedaba
ocioso. Ahora `periodic_read_check` **salta** las cuentas que ya tienen un
`sync_contacts` pendiente o en curso (dedup por `sync_logs`), así que como mucho
hay un sync encolado + uno corriendo por cuenta.

## Deploy

Este cambio solo toca el compose y el scheduler (sin migración). Recrear los
workers afectados:

```
docker compose -f docker-compose.prod.yml up -d --build \
    worker-web worker-agilecrm worker-sync
```

`worker-web` y `worker-agilecrm` son servicios nuevos; `worker-sync` se recrea
porque cambió su lista de colas. El resto de workers no cambian.


## Ejecuciones «en curso» que ya no lo están

Un `sync_logs` se crea en `pending` al encolar y pasa a `running` cuando el
worker lo coge; el propio job lo cierra. Si el worker muere a mitad
(contenedor recreado, caída), la fila se quedaba «en curso» para siempre. El
dedup de `agilecrm:periodic_read` saltaba esa cuenta para siempre: el
05/10/2026 había 8 de 9 cuentas sin sincronizar desde hacía 18 días.

Ahora (`app/workers/huerfanas.py`):

- **El «en curso» caduca.** Para los dedup, una ejecución en
  `pending`/`running` que empezó (o se encoló) hace más de
  `SYNC_INFLIGHT_MAX_MINUTES` (60 por defecto) ya no cuenta, y el siguiente
  tick encola otra.
- **Al arrancar, cada worker cierra las huérfanas de sus colas.** Todos los
  workers arrancan con `--worker-class app.workers.worker.BoHubWorker`. Antes
  de coger trabajo, marca `failed` (con un mensaje claro) las ejecuciones de
  sus colas cuyo job ya no está vivo en RQ. Cuenta como huérfana si:
  - el job no existe;
  - el job terminó sin cerrar la fila;
  - el job está «started» en un worker que ya no late.

  Lo que sigue encolado, o en marcha en otro worker vivo de la misma cola, no
  se toca. Sin Redis no se decide nada. El tick de `agilecrm:periodic_read`
  hace lo mismo con los `sync_contacts`.
- **Brevo, sync targets.** El `RUNNING` del target caduca con su cerrojo
  (2 h). Si no, «Ejecutar ahora» respondía 409 para siempre tras un worker
  caído. Al cerrar un `push_target` huérfano, el target pasa a `error`.
- **Brevo, `periodic_read`.** No deduplica, así que no podía bloquearse.
- **Ya caducaban antes:** las copias de seguridad (1 h), los eventos de
  WooCommerce en `processing` (15 min) y las pasadas del Cuadre (2 h).

El Cuadre vigila además la comprobación «Sincronización colgada»
(`docs/erp/cuadre.md`).
