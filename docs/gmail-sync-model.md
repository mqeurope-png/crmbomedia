# Modelo de sincronización Gmail (CRM-GMAIL)

Este documento describe cómo el CRM captura, marca y muestra el correo tras el
sprint CRM-GMAIL (captura universal + real-time + spam + filtro por alias).

## Qué se captura ahora

**Todo el correo dirigido a un alias ACTIVO del CRM**, sea quien sea el
remitente. Antes el sync incremental sólo guardaba mensajes que caían en un
*thread que el CRM ya conocía*; ahora la decisión de guardar es:

> guardar si algún destinatario (`Delivered-To` / `X-Original-To`, y en su
> defecto `To`/`Cc`/`Bcc`) coincide con un alias activo en `user_email_aliases`.

- Si el remitente **no** es un contacto conocido, el email se guarda igual con
  `contact_id = NULL` (huérfano). El comercial decide luego si lo convierte en
  lead. El remitente siempre queda visible en `from_email`.
- La cuenta Gmail es **una sola** (org, `mqeurope@…`) que expone todos los
  alias; un único token OAuth cubre todos.

### Tabla de alias entrante — `user_email_aliases`

`(id, user_id, alias_email UNIQUE global, active, created_at, updated_at)`.
Cada `alias_email` pertenece a **un** usuario. Es el registro de *propiedad del
correo entrante*, distinto de `user_email_alias_prefs` (preferencias Send-As,
outbound, no únicas). Se gestiona desde `/admin/users` (admin). Se sembró en la
migración 0090 desde `users.email` de los usuarios activos.

## Cómo se marca el spam

El Watch escucha las labels `INBOX` **y** `SPAM`. En cada `history.list`:

- Un mensaje nuevo cuyas labels incluyen `SPAM` se guarda con `is_spam = true`.
- Si Gmail **añade** la label `SPAM` a un mensaje ya guardado → `is_spam = true`.
- Si Gmail **quita** la label `SPAM` (marcar «No es spam») → `is_spam = false`.

El spam **no se oculta**: se muestra con un chip rojo «🔴 Spam» en la bandeja,
la ficha y el hilo. `email_messages.gmail_labels` guarda el array de labelIds
para debug. A nivel de thread, `has_spam` (derivado: ≥1 mensaje spam) alimenta
el chip de la lista sin tocar `state`, así el thread sigue en la bandeja.
Existe un flag opcional de backend `?exclude_spam=true` por si en el futuro se
quiere ocultar; la UI no lo usa.

## Cómo se filtra por comercial

Visibilidad (`app/services/email_aliases.py`):

- **Admin**: ve todo (ficha de contacto, feeds agregados). Su bandeja personal
  (`scope=mine`) sigue siendo la suya; para verlo todo usa «Todo el equipo».
- **No-admin**: ve un thread si (a) lo inició él (para no ocultar su propio
  correo enviado, que no tiene `delivered_to`) o (b) tiene un mensaje
  `delivered_to` ∈ sus alias activos.

Se aplica en la bandeja general, la pestaña Emails de la **ficha de contacto**
(decisión de Bart: filtrarla también), el detalle de hilo y el timeline. La
vista «Todo el equipo» (manager/admin) es la escotilla privilegiada explícita y
no aplica el filtro por alias. El comercial con >1 alias tiene un dropdown
«Ver: [Todos mis alias ▾]» que acota por un alias concreto (`?delivered_to`).

## Real-time: Watch + Pub/Sub + poller de respaldo

- **Push**: Gmail → topic Pub/Sub → suscripción push → `POST
  /api/webhooks/gmail`. El webhook valida el JWT (firma + `aud` + service
  account), decodifica `{emailAddress, historyId}` y **encola**
  `process_history` (cola `gmail:process_history`); responde rápido. Setup en
  `docs/gmail-watch-setup.md`.
- **Cursor**: `gmail_pubsub_watches.history_id` (una fila para la cuenta org).
  `process_history` arranca desde ese cursor, procesa `messagesAdded` +
  `labelsAdded/Removed`, y avanza el cursor al final (siempre, incluso si algún
  mensaje falla, para no quedar atrapado). Es **idempotente** (dedup por
  `(gmail_account_user_id, gmail_message_id)`).
- **Renovación del Watch**: cron **diario** que re-registra si quedan <24 h (el
  Watch expira a 7 días → se renueva ~cada 6). Antes el job existía pero **nadie
  lo programaba**; ahora se arma al arrancar la API.
- **Poller de respaldo**: cron cada **15 min** (`gmail:poll_fallback`) que hace
  `history.list` desde el cursor por si el push falla o el Watch caduca. Si
  recupera >0 mensajes, emite un warning (señal de que el push no funciona).

## Arquitectura de workers (RQ)

Los jobs corren en workers RQ; **cada worker procesa sus colas de forma
secuencial** en el orden declarado. Por eso el reparto de colas entre workers
importa: si una cola con trabajo constante va delante de otra en el MISMO
worker, la de atrás se queda esperando.

| Worker | Colas | Por qué separado |
|---|---|---|
| `worker-sync` | general: `agilecrm:*`, `brevo:*`, `freshdesk:*`, `factusol:sync_invoices`, `woocommerce:*`, `genei:*`, `gmail:*`, `emails:snooze_sweep`, … | cola compartida de integraciones |
| **`worker-gmail`** | **exclusivo `gmail:*`** (`process_history`, `renew_watches`, `backfill_historic`, `backfill_per_contact`, `token_expiry_check`, `admin_digest`, `sync_aliases`, `poll_fallback`) | **PR-CRM-GMAIL-fix1** — evita que el push en tiempo real quede bloqueado |
| `worker-workflows` | `workflows:dispatch` / `execute` / `scheduler` | aislado por historia previa (throughput de automatizaciones) |
| `worker-factusol` | `factusol:writes` | **concurrencia 1** obligatoria (numeración CODFAC secuencial) |

**Por qué `worker-gmail` (PR-CRM-GMAIL-fix1).** Tras el deploy de CRM-GMAIL, el
push llegaba (200 OK, JWT válido) y `gmail:process_history` se encolaba, pero
`worker-sync` estaba saturado con el bucle de `agilecrm:sync_contacts`
(paginación de miles de contactos, siempre con trabajo pendiente y declarada
antes que las colas Gmail). Como RQ vacía las colas en orden dentro de un
worker, `gmail:process_history` acumulaba jobs sin ejecutarse (`LLEN
rq:queue:gmail:process_history` crecía) y el mail nunca aparecía en tiempo real.

`worker-gmail` es un worker dedicado **solo** a `gmail:*`. **No se quitan** esas
colas de `worker-sync`: RQ **reparte** los jobs de una cola entre todos los
workers que la escuchan, así que tener ambos escuchando `gmail:*` suma capacidad
y da resiliencia (si un worker cae, el otro sigue) sin tocar la config anterior.
Reutiliza la imagen `crmbomedia-api:latest` (no necesita build propio) y monta el
volumen `crmbo_email_attachments` para los adjuntos del backfill.

## Backfill histórico universal (CLI)

El push en tiempo real solo captura correo **desde que se activó** (#329). El
histórico anterior lo trajo el backfill de PR #246, pero **con el filtro viejo**
(solo remitentes que ya eran contacto), así que le faltan los mails huérfanos.
Para recuperarlos hay un comando CLI que reprocesa un rango de fechas con la
regla universal (guarda TODO mail a un alias activo), reutilizando la misma
`_persist_inbound` que el push (con `emit_activity=False`: **no** re-dispara
workflows ni ensucia timelines con correo viejo, y `imported_via =
'historic_backfill_universal'`).

```bash
python -m app.integrations.gmail_watch backfill_universal \
  --since 2026-02-07 [--until 2026-08-07] [--dry-run] [--dry-run-limit 500] \
  [--labels INBOX,SPAM] [--yes] [--batch-size 100]
```

- **Prerequisito**: los alias en `/admin/users` deben estar **completos**. Un
  mail a un alias que no esté en la BD se descarta silenciosamente; el comando
  los cuenta y los lista al final («Descartados por alias»). El comando pide
  confirmación al arrancar (salta con `--yes`).
- **`--dry-run`**: no escribe nada; solo cuenta cuántos mails caerían por rama
  (importable con contacto / huérfano / spam / dedupe / sin alias). `--dry-run-limit`
  (default 500) acota los mensajes examinados por rendimiento.
- **Idempotente**: dedupe por `(gmail_account_user_id, gmail_message_id)`.
  Re-ejecutar con la misma fecha solo importa lo que faltaba (el resto se salta).
- **Spam**: los mails con label `SPAM` entran con `is_spam=true` (igual que el
  push); no se ocultan (chip «Spam», o `?exclude_spam=true` en la lista).
- Corre en **foreground** en el proceso invocado (sin cola RQ). Para runs largos:
  `nohup docker exec crmbo-api-1 python -m app.integrations.gmail_watch \
  backfill_universal --since 2026-02-07 --yes > /root/backfill-$(date +%F).log 2>&1 &`.

El report final resume totales + duración + los alias que descartaron mails
(ordenados por número), con la sugerencia de añadirlos y re-ejecutar.

## Backlog (fuera de este PR)

- Convertir el remitente de un email huérfano en lead desde la propia UI.
- Backfill retroactivo de `contact_id` en emails huérfanos cuando se crea un
  contacto con ese email.
- Notificación push in-app al llegar un mail.
