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
| **worker-web**    | `woocommerce:webhooks`, `woocommerce:import`, `woocommerce:backfill` | **Ingesta web. Aislado**: nada de AgileCRM ni otros syncs lo bloquean, así que un pedido web entra en **segundos siempre**. `webhooks` va primero (máxima prioridad). |
| **worker-agilecrm** | `agilecrm:periodic_read`, `agilecrm:sync_contacts`, `agilecrm:purge_quota` | **Baja prioridad, aislado** (AgileCRM en retirada). `--with-scheduler` para el heartbeat `periodic_read`. `periodic_read` primero para que el tick horario no se retrase tras un sync largo. |
| **worker-sync**   | `brevo:*`, `freshdesk:sync_tickets`, `factusol:sync_invoices`, `gmail:*`, `emails:snooze_sweep`, `email_templates:import_gmail`, `backups:create` (solo prod), `genei:shipments`, `genei:webhooks` | Sync externo + housekeeping. Ya **no** lleva AgileCRM ni WooCommerce. |
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
