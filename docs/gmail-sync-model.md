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

## Backlog (fuera de este PR)

- Convertir el remitente de un email huérfano en lead desde la propia UI.
- Backfill retroactivo de `contact_id` en emails huérfanos cuando se crea un
  contacto con ese email.
- Notificación push in-app al llegar un mail.
