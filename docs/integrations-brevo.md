# Integración Brevo

Sprint B+D fusionado: sincronización bidireccional de contactos,
webhooks de eventos de email, plantillas y campañas gestionadas desde
el CRM. El objetivo: el operador vive dentro del CRM para el marketing
habitual; Brevo nativo queda para edición visual de HTML y
configuración de senders.

Documentación relacionada: `integrations-architecture.md` (pasillo
común), `marketing-campaigns.md` (guía del operador).

## Arquitectura del sync

### Read (Brevo → CRM)

`brevo:sync_contacts` (worker RQ, disparable desde el SyncPanel):

- Pagina `GET /contacts` con `modifiedSince = último run OK − 5 min`
  (delta). El payload `{"full_sync": true}` fuerza el recorrido
  completo — botón "Resincronizar todo".
- Upsert por `(system='brevo', account_id, external_id)`. Si el email
  ya existe en el CRM (p. ej. importado de AgileCRM), se añade una
  `external_reference` adicional al contacto existente — nunca se
  duplica. Un contacto puede tener referencias a varios sistemas.
- Membresía de listas → auto-tags `brevo-list:<nombre>` con
  `source=brevo:<account>`; el delta de cada sync solo toca las
  asignaciones de esa fuente (tags manuales y de otros conectores
  sobreviven).
- Lock Redis por cuenta (TTL 1h) contra ejecuciones concurrentes.
- Contactos sin email usable se saltan con warning (no se crean
  cascarones).

### Write (CRM → Brevo): sync targets

Un `BrevoSyncTarget` define el subconjunto del CRM que viaja a una
audiencia Brevo: `segmento (Sprint P.3) → lista Brevo`. El motor de
segmentos se reutiliza sin tocar.

Ejecución (`brevo:push_target`):

1. Evalúa el segmento → contactos actuales (sin email se excluyen).
2. `POST /contacts` por contacto; el 400 `duplicate_parameter` cae a
   `PUT /contacts/{email}` (update).
3. Si hay `brevo_list_id`: alta en la lista en lotes de 100.
4. **Delta de salida**: `brevo_target_memberships` guarda quién se
   empujó en el run anterior; quien ya no cumple el segmento se
   **quita de la lista** (`/contacts/lists/{id}/contacts/remove`) —
   nunca se borra el contacto en Brevo.
5. Stats en `last_run_stats_json` (pushed_new, pushed_updated,
   added_to_list, removed_from_list, errors).

Dry-run (`POST /api/brevo/sync-targets/{id}/run?dry_run=true`):
evalúa y diffea sin tocar Brevo; devuelve would_push /
would_remove_from_list inline. Lo usa el botón "Probar" del modal.

### Scheduler

`brevo:auto_sync_check` corre cada 5 minutos: encola los targets con
`auto_sync_enabled` cuyo `last_run_at + sync_interval_minutes` venció,
y se re-programa a sí mismo vía `enqueue_in` de RQ. Requiere el worker
con `--with-scheduler` (ya en ambos compose). El heartbeat se arma
solo al crear un target (SETNX idempotente), así un deploy nuevo no
necesita intervención. Mismo patrón para `brevo:refresh_campaigns`
(cada 15 min).

## Webhooks

`POST /api/webhooks/brevo` (público — Brevo no puede autenticarse como
user). Flujo: firma → parse (objeto o array) → sync_log + audit →
encolar a `brevo:webhook_process` → 200 inmediato. Si Redis está caído
se procesa inline antes que perder el evento.

### Firma

Con `BREVO_WEBHOOK_SECRET` configurado (ver `.env.production.example`),
el header de auth (acepta `brevo-signature-token`, `x-brevo-signature`
o `x-sib-signature`) se compara en tiempo constante; mismatch → 401.
Sin secret: se acepta con WARNING de seguridad en los logs.

### Idempotencia

Brevo entrega best-effort (duplicados posibles). Cada evento se
registra en `webhook_events_seen` con clave
`message-id:evento:email` (+timestamp para opens/clicks repetibles);
la segunda entrega del mismo id se descarta. TTL 30 días con limpieza
oportunista tras cada lote.

### Mapeo de eventos

| Evento Brevo | activity_events.event_type | Acción reactiva |
|---|---|---|
| `request` | `email.queued` | — |
| `sent` | `email.sent` | — |
| `delivered` | `email.delivered` | — |
| `opened` / `unique_opened` | `email.opened` | — |
| `click` | `email.clicked` | URL guardada en `body` |
| `soft_bounce` | `email.bounced_soft` | — |
| `hard_bounce` | `email.bounced_hard` | `is_email_valid=false` |
| `unsubscribe` | `email.unsubscribed` | `marketing_consent='unsubscribed'` |
| `spam` | `email.spam_complaint` | ambas |

Las mutaciones reactivas se auditan
(`contact.consent_changed_by_webhook`,
`contact.email_invalidated_by_webhook`).

> Nota de diseño: el sprint pedía consent `withdrawn`; el enum
> `ConsentStatus` del CRM define `unsubscribed` para esa semántica
> (modelo, filtros, segmentos y UI), así que se usa `unsubscribed`.

**Los webhooks nunca crean contactos.** Un email sin contacto en el
CRM se loggea (email + cuenta + evento) y se descarta — los envíos
transaccionales a desconocidos no deben contaminar la base.

### Configurar el webhook en Brevo (paso a paso)

1. `/admin/integrations` → expandir la card Brevo → sección
   "Webhooks" → **Copiar URL** (`https://<tu-dominio>/api/webhooks/brevo`).
2. (Recomendado) Genera el secret: `openssl rand -hex 32`; añádelo a
   `.env.production` como `BREVO_WEBHOOK_SECRET` y recrea el
   contenedor `api`.
3. En Brevo: **Settings → Webhooks → Add a new webhook**.
   - URL: la copiada en el paso 1.
   - Eventos: marca todos (sent, delivered, opened, clicked,
     soft/hard bounce, unsubscribed, marked as spam).
   - Si configuraste el secret: añade el header
     `brevo-signature-token: <secret>` en la configuración del
     webhook.
4. Verifica: envía un test desde una campaña; los contadores de la
   sección "Webhooks" (últimas 24h) deben moverse y la ficha del
   contacto mostrar el evento en "Actividad email".

## Plantillas y campañas

Ambas usan cache local (`brevo_templates_cache`,
`brevo_campaigns_cache`, unique por `(account, id remoto)`) para que
la UI renderice sin esperar al API:

- Plantillas: la lista nunca trae el HTML; se lazy-loadea al abrir el
  detalle y queda persistido. `?refresh=true` re-espeja el catálogo
  (lo que desapareció en Brevo se borra localmente).
- Campañas: el detalle se auto-refresca si `cached_at > 5 min` (si
  Brevo está caído se sirve la copia stale en vez de fallar). El
  cron de 15 min mantiene la lista fresca.

Reglas de estado (validadas en API, reflejadas en UI):
- editar/borrar: solo `draft` / `suspended` (409 si no).
- send-now: `draft` / `queued` / `suspended`.
- programar: mínimo +1h (cliente y servidor).
- cancelar programación: `queued` → status Brevo `draft`, se limpia
  `scheduled_at`.

Campaña desde segmento: el backend crea una lista
`crm-campaign-{timestamp}`, vuelca los contactos del segmento en
lotes de 100 y crea la campaña apuntando a esa lista.

Los tabs "Destinatarios por evento" y el gráfico del detalle se
alimentan de los `activity_events` del webhook — sin round-trips a
Brevo y con enlace directo a la ficha de cada contacto.

## Rate limits y errores

El `IntegrationHTTPClient` compartido gestiona: 429 + `Retry-After`
(3 reintentos, backoff exponencial), 401 → `IntegrationAuthError` +
`credential_status='error'`, timeouts 30s, audit por llamada. El
cliente Brevo añade `IntegrationDuplicateError` para el 400
`duplicate_parameter` de `POST /contacts`.

## Operaciones del worker

| Cola | Handler | Disparo |
|---|---|---|
| `brevo:sync_contacts` | read sync | manual (SyncPanel) + cron `periodic_read` |
| `brevo:push_target` | push de un target | manual / heartbeat |
| `brevo:auto_sync_check` | heartbeat targets | cada 5 min (self) |
| `brevo:webhook_process` | materializar eventos | cada delivery |
| `brevo:refresh_campaigns` | refrescar cache campañas | cada 15 min (self) |
| `brevo:refresh_segments` | importar/refrescar mirrors de segmentos | manual / cron `periodic_segments` |
| `brevo:refresh_segment` | refresco de un mirror concreto | botón "Refrescar ahora" |
| `brevo:periodic_read` | heartbeat read sync por cuenta live | cada `BREVO_SYNC_INTERVAL_HOURS` (default 12) |
| `brevo:periodic_segments` | heartbeat refresh segments por cuenta | cada `BREVO_SEGMENTS_REFRESH_INTERVAL_HOURS` (default 6) |
| `brevo:historical_backfill` | importar destinatarios de cada evento de campañas pasadas | manual (panel `/admin/integrations` o CLI), **nunca** automático |

## Backfill histórico de eventos

El webhook en vivo solo dispara desde el día que se configura.
Cualquier campaña enviada ANTES no tiene historial granular en el
CRM — la ficha de cada contacto muestra entregas/aperturas/clicks
solo de lo recibido después. Brevo expone los destinatarios por
evento de cada campaña pasada vía API; este backfill los lee y
materializa las filas faltantes en `activity_events`.

Cuándo lanzarlo:

- Una vez, tras configurar el webhook por primera vez en una
  cuenta nueva — recupera todo el historial accesible.
- Ocasionalmente, si sospechas que se han perdido eventos
  (corte de red, webhook desactivado por accidente).
- **Nunca** como cron — es una operación pesada (10-30 min en
  cuentas con cientos de campañas) que consume cuota del API.

Cómo lanzarlo:

- **UI**: `/admin/integrations` → expandir Brevo → sección
  "Historial de eventos (backfill)" → "Lanzar backfill histórico"
  (admin only). Confirmación inline. Tras lanzarlo, el panel
  refresca solo cada 8 s mientras corre.
- **CLI** (paralelo): `scripts/backfill_brevo_email_history.py`
  con `--account-id`, opcional `--max-campaigns`, opcional
  `--dry-run`.

Idempotencia: la dedup va por el UNIQUE
`activity_events(system, account_id, external_id)` ya existente.
El `external_id` sintetizado encaja `backfill:{brevo_campaign_id}:
{email}:{event_type}` — una re-ejecución hits la misma clave, la
inserción cae con `IntegrityError` dentro de su SAVEPOINT y la
fila cuenta como `events_skipped_existing`. Sin SELECT-por-fila
de coste.

Restricciones (heredadas de la política de webhooks):

- No crea contactos. Un email Brevo sin contraparte en `contacts`
  se cuenta como `contacts_unknown` y se descarta. Sincroniza
  primero `brevo:sync_contacts` si quieres que aparezcan.
- Solo procesa campañas con `status ∈ {sent, archive}` del
  cache local `brevo_campaigns_cache`. Lo que no esté cacheado no
  se trae — refresca antes con el botón "Refrescar" de la lista
  de campañas si hace falta.
- Limita la concurrencia a 2 llamadas + 200 ms entre páginas para
  no quemar la cuota Brevo (400 req/min).

## Periodic scheduling

Tres heartbeats independientes y un cron por separado para campañas
viven en `app/integrations/brevo/scheduler.py`. Todos usan SETNX en
Redis como guard, así que dos procesos de API arrancando a la vez
no pueden doble-armarlos. `app.main` los arma en el startup
handler; la API NO cae si Redis está caído al boot — solo deja de
re-armar hasta el siguiente reinicio.

Variables de entorno (con defaults):

| Variable | Default | Qué controla |
|---|---|---|
| `BREVO_SYNC_INTERVAL_HOURS` | 12 | Cada cuánto encola `sync_contacts` para cada cuenta `enabled=true, mode=live` |
| `BREVO_SEGMENTS_REFRESH_INTERVAL_HOURS` | 6 | Cada cuánto encola `refresh_segments` para cada cuenta `enabled=true` (live y sandbox) |

El RQ worker debe correr con `--with-scheduler` (ya está en ambos
`docker-compose.yml`) para que `enqueue_in` funcione.

## ConsentStatus deviation

El sprint pedía `marketing_consent='withdrawn'` para los contactos
con `emailBlacklisted=true`. El enum `ConsentStatus` del CRM define
`granted | denied | unknown | unsubscribed` — esta última cubre la
misma semántica que Brevo entiende como "no contactable".
Introducir `withdrawn` como valor paralelo exigiría migrar el enum
y tocar filtros, motor de segmentos, contexto de IA y pickers del
frontend para un comportamiento idéntico, así que se usa
`unsubscribed`. Mismo trade-off que se documentó al cerrar PR #51.

## Segments mirror

Los segmentos Brevo viven como "mirrors" en el CRM: una fila normal
de `segments` con `is_dynamic=False`, `static_contact_ids`
refrescado periódicamente, y `external_source =
"brevo:<account>:<brevo_id>"`. El motor de segmentos no cambia —
sirve la membresía desde `static_contact_ids` exactamente como en
los segmentos congelados nativos.

La API de Brevo NO expone el árbol de filtros, así que las reglas
NO se importan: la UI esconde el editor y muestra "Espejo Brevo" +
"Refrescar ahora desde Brevo" + deeplink. Editar las reglas se hace
en Brevo nativo; el siguiente refresco trae la nueva membresía.

Resolución de membresía: por email contra `contacts`. Emails Brevo
sin contraparte CRM se ignoran silenciosamente (los webhooks
tampoco crean contactos — esa restricción se mantiene aquí). El
sync read tradicional (`brevo:sync_contacts`) trae los contactos;
este mirror solo asigna membresía.

Ruta de membresía (corregida en el debt-closure PR): Brevo v3 NO
tiene `/contacts/segments/{id}/contacts` (404 `Invalid route`); la
lectura va por el listado genérico filtrado:
`GET /contacts?segmentId={id}`. Límites por endpoint:
`/contacts/segments` capea `limit` en 50 (`out_of_range` por
encima); `/contacts` admite hasta 1000. El cliente clampa ambos.

Si una cuenta Brevo rechaza el filtro `segmentId` (es un parámetro
relativamente reciente), el refresco degrada con elegancia: conserva
la membresía del refresco anterior (nunca vacía un mirror que
funcionaba por una limitación de API) y escribe la nota en la
descripción del segmento — "Brevo no expone la membresía de este
segmento vía API… ábrelo en Brevo o expórtalo como lista Brevo para
sincronizarla como tag".

## Scripts operativos

| Script | Qué hace | Idempotente |
|---|---|---|
| `scripts/backfill_brevo_consent.py` | `unknown → granted` para contactos sourced de Brevo. Pasa una vez tras el deploy del PR follow-up. | ✅ |
| `scripts/cleanup_stale_sync_logs.py` | `pending → failed` para SyncLogs ≥ 2h sin que el worker los cogiera. Schedulable como cron. | ✅ |
| `scripts/backfill_brevo_email_history.py` | Pulla los destinatarios por evento de cada campaña pasada del cache local y los inserta como `activity_events`. `--account-id`, `--max-campaigns`, `--dry-run`. Misma operación que el botón del panel `/admin/integrations`. | ✅ |
