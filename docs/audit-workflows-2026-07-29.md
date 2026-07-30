# Auditoría del sistema de Workflows — 2026-07-29

Auditoría completa previa al Sprint Workflows (post PRs #259/#261/#262). Cuatro pasadas
independientes: (1) ciclo de vida de triggers y dispatch, (2) engine + acciones,
(3) estimator + wizard, (4) motor de segmentos. Todo verificado en código con archivo:línea.

## Resumen ejecutivo

- **12 de los 19 triggers del catálogo están MUERTOS** — seleccionables en la UI pero sin
  ningún productor que los despache. Solo funcionan: `contact.created`, los 3 Brevo del
  webhook, `cron.recurring` y (a medias) `engagement.brevo.composed`.
- `opportunity.*` (4 triggers) son **fantasma**: no existe modelo ni tabla de oportunidades.
  `contact.date_field` no tiene evaluador **ni columnas de fecha** (`birthday`/`anniversary`
  no existen en `contacts`). Dos de las tres plantillas seed crean workflows imposibles de
  disparar (`templates.py:130`, `:185`).
- El runtime **ignora todos los filtros de config del trigger** salvo `filter` y `field`
  (`dispatcher.py:239-268`): campaña, link, plantilla, prioridad, from/to status… La UI
  promete filtros que no se aplican.
- **Bug crítico de activación**: `validate_tree` solo entiende el vocabulario legacy `op`,
  pero el builder de la UI persiste `operator`/`comparator` → **cualquier workflow con un
  paso condicional real da 400 al activar** con "leaf without op (field=None)"
  (`conditions.py:471-494`, verificado por ejecución).
- **Bug crítico de waits en producción**: MySQL no persiste tz; `wake_at` vuelve naive y
  `run.wake_at > datetime.now(UTC)` lanza `TypeError`, que el scheduler traga por-run
  (`scheduler.py:78-81`) → **ningún `wait_time`/`wait_until` se reanuda jamás en prod**
  (`engine.py:404-408`).
- **Multi-tenancy inexistente en dispatch**: un workflow privado de un user dispara por
  eventos de contactos de cualquier otro, y `action_send_email` envía **como el owner del
  contacto** (identidad Gmail + cap diario de otro user) (`dispatcher.py:200-207`,
  `steps.py:808-813`).
- **Cero audit** en dispatch/engine (ni un `record_event` en `app/workflows/`); las
  mutaciones de contacto hechas por workflows son invisibles para /admin/audit y GDPR.
- Dos motores de condiciones divergentes (workflows `conditions.py` vs segmentos
  `build_filter`): 13 campos que la UI ofrece evalúan **False silencioso**, y las
  condiciones de tags son **siempre falsas** (UUIDs vs nombres CSV).
- El estimator diverge del runtime por diseño y tiene 15 bugs propios (carga toda la tabla
  de contactos en memoria por click, `max()` que enmascara filtros, cron ×30 erróneo…).

**Decisión: Opción B (refactor mayor de la capa de triggers + fixes críticos del engine).**
El grafo de runs/steps/history es salvable; la capa de triggers y la doble implementación
de condiciones no. Racional al final del doc.

---

## 1. Inventario de triggers (veredictos)

Productores reales de `dispatch_event` en todo el backend: **5 call sites** —
`routes.py:1454` (replay admin), `routes.py:1567` (POST /contacts),
`agilecrm/jobs.py:834`, `brevo/jobs.py:695` (ambos solo `contact.created`, y con gate
anti-bulk que silencia imports grandes), `brevo/webhooks.py:238` (mapa
`WORKFLOW_TRIGGER_MAP`, `webhooks.py:63-68`).

| Trigger | Veredicto | Detalle |
|---|---|---|
| `contact.created` | ✅ OK | 4 productores. Gate bulk: imports > umbral no despachan nada (`agilecrm/jobs.py:807`, `brevo/jobs.py:670`) |
| `contact.updated` | ❌ MUERTO | `update_contact` (`routes.py:2439-2731`) calcula `changed_fields` y NO despacha. Doble-muerto: el wizard escribe `field`/`new_value` y ningún payload trae `field` |
| `contact.lifecycle_changed` | ❌ MUERTO | `commercial_status` se escribe por el setattr genérico sin despacho. `from_status`/`to_status` del wizard no los lee nadie |
| `contact.unsubscribed` | ⚠️ PARCIAL | Solo vía webhook Brevo. El unsubscribe del propio CRM (`email_tracking/router.py:268-360`) NO despacha → tampoco cancela runs (es el cancellation event por defecto de todo workflow) |
| `email.crm.opened` | ❌ MUERTO | El tracking escribe `email_message_events` (`email_tracking/router.py:119-155`) y no despacha. Rompe también los `wait_for_event` de este tipo (solo salen por timeout) |
| `email.crm.clicked` | ❌ MUERTO | Ídem (`router.py:158-195`). `link_url` del wizard muerto |
| `email.crm.replied` | ❌ MUERTO | El inbound escribe `activity_events(email.reply_received)` (`gmail/service.py:1358-1393`) y no despacha |
| `email.brevo.opened/clicked` | ⚠️ FILTROS IGNORADOS | Despachan, pero `account_id`/`campaign_id`/`link_url` no se aplican en runtime (`dispatcher.py:239-268`); el estimator sí (post #262) → estimado ≠ realidad |
| `engagement.brevo.composed` | ⚠️ SIN DEDUP + SIN FILTER | Se evalúa en cada open/click (`dispatcher.py:276-347`) pero salta `_trigger_matches` (ignora el `filter` del operador) y no tiene marcador → re-dispara sin límite con reentry |
| `task.created` / `task.completed` | ❌ MUERTOS | Los repos escriben `activity_events` del timeline, jamás el bus de workflows (`repositories/tasks.py:119-122`, `:181`) |
| `task.overdue` | ❌ MUERTO (sin detector) | No existe nada que detecte el paso a vencida; el scheduler no mira `tasks` |
| `opportunity.*` (4) | ❌ FANTASMA | **No hay tabla/modelo Opportunity.** El concepto real es `contact_pipeline_stages`. Config del wizard (pipeline/stage/min_value) totalmente inerte |
| `contact.date_field` | ❌ MUERTO (sin evaluador NI columnas) | Nada lo evalúa y `contacts` no tiene `birthday`/`anniversary`. La plantilla "Felicitar cumpleaños" es activable y no corre jamás |
| `cron.recurring` | ⚠️ FUNCIONA CON BUGS | Tick 30s vs ventana `minute<1` → **doble disparo**; `limit(200)` sin orden **antes** del filtro → contactos 201+ excluidos para siempre (`scheduler.py:124-190`) |

## 2. Config del wizard que el runtime ignora

`_trigger_matches` (`dispatcher.py:239-268`) honra exactamente **2 claves**: `filter`
(árbol) y `field` (igualdad contra `payload["field"]` — que ningún productor emite, así que
un workflow con `field` configurado no dispara nunca). Todo lo demás que escribe
`TriggerConfigPanel.tsx` se descarta en silencio: `account_id`, `campaign_id`, `link_url`,
`template_id`, `owner_user_id`, `priority`, `from_status`, `to_status`, `new_value`,
`pipeline_id`, `stage_id`, `min_value`, `field`/`match` de date_field. El humanizador
(`workflowsHumanize.ts`) tampoco muestra nunca la config del trigger — el operador no puede
ver que su filtro no existe.

## 3. Engine y acciones — bugs por severidad

### Críticos (comportamiento incorrecto en producción hoy)

| # | Bug | Dónde |
|---|---|---|
| E1 | **Waits nunca se reanudan en MySQL** (naive vs aware `TypeError` tragado por-run) | `engine.py:404-408`, `scheduler.py:78-81` |
| E2 | **`validate_tree` rechaza el IR del builder** → 400 al activar cualquier workflow con condición | `conditions.py:471-494` |
| E3 | **`status="failed"` de un handler NO falla el run** → completa limpio sin `error_summary` ni `total_failed` | `engine.py:519-526→589-604` |
| E4 | **Runs varados para siempre**: cap `max_steps=30` sale sin estado; `CANCELLING` nunca se finaliza (el botón Cancelar deja el run en `cancelling` reteniendo el dedup key → el contacto no puede reentrar) | `engine.py:388`, `:629-635`; scheduler solo mira WAITING (`scheduler.py:66-71`) |
| E5 | **Editar un workflow pausado mata sus runs en vuelo**: steps se borran y recrean → `current_step_id` SET NULL → run se "completa"; `workflow_event_waits` CASCADE → run `WAITING_FOR_EVENT` **eternamente** | `api/workflows.py:780-850`, `models/workflows.py:247-249`, `:376-377` |
| E6 | **Render fallback al texto crudo**: un placeholder desconocido (p.ej. `{{trigger.value}}` en contact.created) → el cliente recibe `Hola {{ contact.first_name }}` literal, solo log.warning | `variables.py:166-174` + StrictUndefined |
| E7 | **`rollback()` en colisión de dedup borra toda la transacción del caller** (cancelaciones del mismo evento, runs previos del mismo tick cron) — necesita SAVEPOINT | `engine.py:362-372` |
| E8 | **Una excepción en `run_tick` mata el scheduler permanentemente** (re-arm es la última línea, sin try/finally) | `scheduler.py:102-107` |
| E9 | **Tags de workflow escriben solo el CSV legacy**, invisible para filtros/segmentos/Brevo (que leen `contact_tags` M:N) — y para las propias condiciones de tags del workflow | `steps.py:244-280` vs `segments/engine.py:323-368` |
| E10 | **Sin scoping de tenancy** + envío como el owner del contacto (identidad/cap de otro user) | `dispatcher.py:200-207`, `steps.py:808-813` |

### Altos

- Emails vacíos enviables (modo template sin template → subject/body "") — solo guarda el
  frontend (`steps.py:835-852`).
- `from_alias_mode="fixed"` con alias vacío pasa sin validación; las 3 plantillas seed lo
  traen así (`steps.py:700-701`, `templates.py:41-51`).
- `action_notify_owner`/`_manager` son **solo `log.info`** — la UI promete notificación
  in-app (`steps.py:953-1024`); manager = primer MANAGER global, no el del owner.
- `action_move_opportunity_stage` ignora `pipeline_id` y mueve la fila más reciente —
  puede poner un stage de otro pipeline (`steps.py:646-666`).
- `switch` no resuelve custom fields (usa `_FIELD_RESOLVERS` privado) y compara `==` crudo
  (int vs str nunca matchea) (`steps.py:214`).
- Fan-out de edges silenciosamente truncado a un sucesor arbitrario (`engine.py:259-266`).
- Sin `with_for_update` en todo el código (el docstring del scheduler miente) → doble
  ejecución de steps posible con 2 workers (`scheduler.py:5-6`).
- Cero `record_event` en `app/workflows/` — mutaciones sin audit; y las Action de workflow
  del API son strings literales, no constantes (`api/workflows.py:438` etc.).
- Sin dispatch tras acciones (`action_create_task` no emite `task.created`, etc.) → los
  workflows no pueden encadenarse.
- `is_entry` doble si el trigger no es el primer nodo del array React Flow
  (`page.tsx:503` + `api/workflows.py:822-830`).
- El "Probar" (dry-run) es **una implementación paralela** que ya diverge del engine
  (multi-tag, template mode, due_mode, wait branches) (`dry_run.py:92-339`).
- `action_set_custom_field` permite escribir `owner_user_id`/`email`/`company_id` por
  setattr sin validación, dejando `contact_assignments` desincronizado (`steps.py:322,381`).

### Estimator (los 5 peores de 15)

| Bug | Dónde |
|---|---|
| STATE+filter carga TODOS los contactos como ORM en memoria por click | `api/workflows.py:1284-1293` |
| `max(runs, events)` enmascara los filtros según el historial | `:1397` |
| Estima filtros que el runtime no aplica (estimado < realidad sistemático) | `:1373-1389` vs dispatcher |
| Cron ×30 erróneo para 4/5 presets + ignora el cap 200 del runtime | `:1316-1318` vs `scheduler.py:124-172` |
| Sin canal null: "no estimable" y "0" indistinguibles en schema y UI | `schemas/workflows.py:231-236` |

Fuentes de conteo naturales identificadas (para el mapping completo):
`contact.created`→`contacts.created_at`; `contact.updated`/`lifecycle`→`audit_logs`
(action `contact.updated|bulk_updated`, lifecycle vía `metadata LIKE '%commercial_status%'`);
`email.crm.opened|clicked`→`email_message_events(event_type='open'|'click')`;
`email.crm.replied`→`activity_events('email.reply_received')`;
`task.created|completed`→`tasks.created_at|completed_at`;
`task.overdue`, `opportunity.*`, `contact.date_field` → **sin fuente honesta → "—"**.

## 4. Dos motores de condiciones (workflows vs segmentos)

| | `workflows/conditions.py` | `segments/build_filter` |
|---|---|---|
| Ejecución | Python sobre 1 ORM | SQL compilado |
| Campos | 25 nativos + custom fields + `trigger.*` | 33 (sin custom fields, sin `trigger.*`) |
| Tags | nombres del CSV legacy | UUIDs de `contact_tags` |
| Error | False silencioso | raise → 400 |
| Enum `commercial_status` | libre | **hard enum de 4 valores** — rechaza `lead_cualificado` real de prod (`fields.py:364`) |

La UI usa el **mismo builder de segmentos** para el filtro del trigger → 13 campos que
ofrece son irresolubles para el evaluador de workflows (False silencioso), las condiciones
de tags son **siempre falsas**, y `validate_tree` rechaza el árbol entero (E2). El
evaluador in-memory de segmentos (`evaluate_contact_against_rules`) también está roto para
leaves relacionales (tags→None→False, `is_null`→True falso) — **bug vivo en las reglas de
asignación hoy** (`segments/engine.py:822-959`, usado por `assignment_rules.py:145`).

## 5. Motor de segmentos: reusable para el trigger custom

**Veredicto: reusar el compilador SQL, NO el evaluador in-memory.** `build_filter` es
sólido (whitelist, determinista, cycle-safe, 33 campos). Falta y es barato:

1. Predicado single-contact: `build_filter(tree)` + `WHERE Contact.id = :x` (~10 líneas).
2. Tabla de membresía para detectar la transición no-cumple→cumple: clonar
   `BrevoTargetMembership` + su diff (`sync_targets.py:104-204` — el algoritmo exacto ya
   existe en producción, incluso calcula el set "entraron" y lo descarta).
3. 4ª fase en `workflows/scheduler.run_tick` (poll 30s como columna vertebral; fast-path
   por hook después, escribiendo la misma fila para converger).
4. Ensanchar/quitar el enum de `commercial_status` (bloqueo: rechaza valores reales).

Restricciones de diseño ya decididas en el código: NO listeners ORM
(`dispatcher.py:3-6`), NO on-update para reglas (riesgo de bucle,
`assignment_rules.py:23-28`) — el marcador de membresía es lo que rompe el bucle.

## 6. Decisión: Opción B — refactor de la capa de triggers + fixes críticos del engine

- **No A (incremental)**: 12 triggers muertos, 2 motores de condiciones divergentes, 0
  tenancy y 0 audit no se arreglan con parches sin dejar el mismo campo de minas.
- **No C (rebuild)**: el core del grafo (registry de steps, edges, history, dedup) carga
  producción y es correcto en diseño; reescribirlo solo añade riesgo de regresión sobre
  los workflows existentes de Bart.
- **B**: módulo canónico `trigger_definitions.py` (por trigger: productor real, schema de
  config, **matcher de runtime**, **fuente del estimator**, disponibilidad) consumido por
  dispatcher + estimator + catálogo del wizard → no pueden divergir; cablear los triggers
  con productor barato; ocultar los sin fuente como "no disponible"; matcher compartido
  runtime↔estimator; trigger custom por membresía; y los fixes E1-E10.

Alcance detallado, preguntas de producto y qué queda fuera: ver el PR.
