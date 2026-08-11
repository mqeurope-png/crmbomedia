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

## Direcciones — inbound vs outbound (CRM-BACKFILL-SENT)

Cada `email_message` guarda su `direction` propia (el thread no tiene
dirección global). El trigger es el **From contra los alias activos**
(`user_email_aliases`):

- **`From` = alias activo del CRM → `outbound`.** Mail enviado desde Gmail
  directo (no desde el compositor). El propietario es el **dueño del
  alias**: en threads nuevos `initiated_by_user_id` = ese user (así lo ve
  en su bandeja), `created_by_user_id` se rellena en el mensaje,
  `delivered_to` queda NULL (no aplica) y el contacto se casa por los
  **destinatarios** (To y luego Cc). No marca el hilo como no leído ni
  emite `email.reply_received`/workflows. La bandeja lo pinta con el chip
  🟢 «Enviado desde CRM» (mismo `direction=outbound` que los envíos del
  compositor).
- **`From` externo → `inbound`.** Comportamiento de siempre: el gate por
  `delivered_to` (alias al que llegó) decide si se guarda; si no va a
  ningún alias configurado, se descarta.
- **Auto-forward / CC a uno mismo** (From alias Y To alias): outbound gana.
- **SENT cuyo From no es un alias** (forward raro de Gmail): descartado con
  warning `unexpected sent from non-alias` en el log del backfill.

Tanto el **push real-time** (Watch con `INBOX,SPAM,SENT` — re-registrar
tras el deploy) como el **backfill universal** (labels default
`INBOX,SPAM,SENT`) aplican esta misma lógica (`_persist_message`).

### Traer los enviados retroactivos

Re-ejecutar el backfill con la **misma fecha** es idempotente: los mails
INBOX/SPAM ya importados se saltan por dedupe y solo entran los SENT
nuevos. El report los muestra en la línea «Enviados (outbound)».

```bash
nohup docker exec crmbo-api-1 python -m app.integrations.gmail_watch backfill_universal \
  --since 2026-02-07 --yes > /root/backfill-sent-$(date +%Y%m%d).log 2>&1 &
```

## Adjuntos — metadata-only + descarga on-demand (CRM-ADJUNTOS-BACKFILL)

**Decisión Bart 2026-08-10: Opción B.** Los binarios de los adjuntos NO se
almacenan en el VPS. La BD solo guarda la **metadata** (filename, mime,
tamaño, `gmail_attachment_id`) en `email_message_attachments` con
`storage_path = NULL`; cuando el operador pulsa «Descargar» en el chip del
thread detail, el backend hace `messages.attachments.get` a Gmail **en ese
momento** y streamea el binario directo al navegador. **0 storage local.**

- **Latencia esperada**: <1s para adjuntos <10MB (una llamada a Gmail API).
- **Ruta legacy intacta**: los adjuntos del backfill de junio que sí están
  en disco (`storage_path` set) se siguen sirviendo desde disco.
- **attachmentId caducado**: los ids de Gmail no son estables a largo plazo.
  Si Gmail responde 404 con el id guardado, el endpoint re-pide el mensaje,
  localiza la parte por filename+tamaño, refresca el id en BD y reintenta —
  transparente para el usuario.
- **Trade-off aceptado**: si el mail se borra en Gmail (papelera vaciada),
  el adjunto deja de ser recuperable desde el CRM → 410. Coherente con
  «si lo borro, es que no lo quiero».
- Cada descarga se audita (`email.attachment.downloaded`, con
  `metadata.source = local_disk | gmail_on_demand`).
- Idempotencia: UNIQUE `(message_id, gmail_attachment_id)` (migración 0091)
  + el backfill salta mensajes que ya tienen filas de adjuntos.

### Inline vs adjunto real (CRM-ADJUNTOS-UX)

Las **imágenes embebidas en el cuerpo** (firmas con logo, `image001.jpg` de
Outlook) NO son adjuntos: se renderizan dentro del HTML, no deben aparecer
como chip descargable ni disparar el clip 📎 de la bandeja. Se distinguen
con la columna `email_message_attachments.is_inline`:

- **Cómo se decide** (extractor, a partir de los headers MIME de la parte):
  - `Content-Disposition: inline` → inline.
  - Sin `Content-Disposition` pero con `Content-ID` (referenciada por `cid:`
    desde el HTML) → inline.
  - `Content-Disposition: attachment` (aunque tenga `Content-ID`) → real.
- **Por qué**: es la señal fiable del propio correo; el criterio previo
  («cualquier parte con `attachmentId`») incluía los logos de firma.
- **Retroactivo** (migración 0092): las ~23k filas ya guardadas se marcan
  con una heurística **conservadora** por nombre+tamaño (`imageNNN.jpg`
  pequeña < 100 KB). Preferimos dejar como adjunto algún inline dudoso antes
  que ocultar un adjunto legítimo; un re-backfill clasifica exacto por
  headers.
- Las surfaces de usuario filtran `is_inline = false`: los chips del detalle,
  el filtro «Con adjuntos» y el flag `has_attachments` (clip de la fila).

### Render de imágenes inline (CRM-ADJUNTOS-INLINE-FIX)

Las imágenes embebidas se referencian en el HTML con `<img src="cid:…">`; el
iframe del thread detail no resuelve el esquema `cid:`, así que salían como
cuadros rotos. **El backend reescribe** cada `cid:<ref>` a la URL de descarga
del adjunto **antes de servir** el `body_html` (`rewrite_cid_urls` en
`_message_read`):

- Se hace en **backend**, no en frontend, porque el mapeo `cid → adjunto`
  vive en la BD (`content_id` / `filename`) y el iframe es «tonto» (sirve el
  HTML tal cual).
- **Mapeo** (en orden): `content_id` exacto → `filename` → `filename` sin
  extensión. El `<ref>` de Outlook suele ser `image001.jpg@01D9…`; nos
  quedamos con la parte antes de `@`.
- **`content_id`**: columna nueva (migración 0093). El backfill metadata-only
  **no** la guardaba, así que en el histórico es NULL y el mapeo cae al
  **fallback por filename** (el patrón `image001.jpg` de Outlook casa bien).
  Go-forward el extractor la rellena desde el header `Content-ID`.
- La URL es **relativa** `…/download?inline=1`: en prod nginx sirve frontend
  y API bajo el mismo origen, y el iframe (`sandbox="allow-same-origin"`)
  manda la cookie de sesión → la imagen carga autenticada. `inline=1` evita
  que la carga de cada imagen al abrir el hilo cuente como «descarga» en el
  audit log.

### Heurística retroactiva de inline — por qué se relajó (0093)

La 0092 solo marcaba `image%` de **< 100 KB**, pero las firmas corporativas
incrustadas (`imageNNN.jpg` de Outlook con logo grande) pesan típicamente
200 KB – 2 MB y seguían apareciendo como adjunto descargable. La **0093**
amplía a `imageNNN.<ext>` de **cualquier tamaño** (`image` + 3 caracteres +
extensión de imagen, con `LIKE` portable). Sigue siendo conservadora: un
adjunto legítimo debe llamarse exactamente `imageNNN.jpg` (patrón Outlook)
para marcarse — `factura.pdf`, `producto-final.jpg`, etc. no se tocan.

### Estado del mensaje en Gmail — `gmail_status` (CRM-ADJUNTOS-PURGE)

Cada `email_message` lleva `gmail_status`:

- **`active`** (default): existe en Gmail; todo funciona normal.
- **`deleted_gmail`**: el mensaje ya no existe en Gmail (papelera vaciada /
  borrado permanente). El CRM lo conserva (historia) pero: los hilos cuyos
  mensajes son **todos** `deleted_gmail` se ocultan de las vistas generales
  (Bandeja/Enviados/etc.); la vista **`state=deleted`** («Papelera Gmail»
  del sidebar) lista los hilos con ≥1 mensaje borrado; la **ficha del
  contacto** los mantiene visibles (no se corta el histórico); el detail
  muestra banner «ya no existe en Gmail» y las descargas quedan
  deshabilitadas. NO confundir con `EmailThread.state=TRASHED` (mover a
  papelera manual desde el CRM).

**`--purge-not-found`** (en `backfill_universal` y `backfill_attachments`):
ante un 404 de Gmail marca el mensaje `deleted_gmail` en vez de contarlo
como error. Matiz importante: `backfill_universal` lista mensajes DESDE
Gmail, así que allí el flag solo cubre la carrera list→get; el **purge
efectivo del histórico** lo hace `backfill_attachments`, que itera los
mensajes de NUESTRA BD y toca cada `gmail_message_id`:

```bash
docker exec crmbo-api-1 python -m app.integrations.gmail_watch backfill_attachments \
  --since 2026-02-07 --purge-not-found --yes
```

Cuándo usarlo: limpieza tras cambios masivos en Gmail (vaciar papelera) o
housekeeping de admin. El report añade «Marcados como borrados en Gmail: N».
Sin cron automático — solo ejecución manual.

### Permisos de descarga — visibilidad del hilo

La descarga de un adjunto **hereda la visibilidad del thread**
(`thread_is_visible`), NO el owner del contacto. Si el operador ve el email
en su bandeja (mensaje entregado a uno de sus alias activos, o hilo iniciado
por él; admin ve todo) puede descargar el adjunto. Antes el endpoint miraba
`contact.owner_user_id`, que negaba a un comercial que SÍ veía el mail por
alias pero cuyo contacto pertenecía a otro (o era NULL). Si no es visible →
403 `{code: attachment_not_visible}` con un mensaje sobre el **email** (no el
contacto).

### Backfill de metadata para mensajes ya importados

Los ~15k mensajes de los backfills de junio/agosto y del go-forward se
importaron **sin** adjuntos. Para registrarles la metadata:

```bash
# 1. Dry-run (opcional, 2-3 min): cuenta adjuntos + tamaño agregado.
docker exec crmbo-api-1 python -m app.integrations.gmail_watch backfill_attachments \
  --since 2026-02-07 --dry-run --yes

# 2. Ejecución real (10-30 min según volumen):
nohup docker exec crmbo-api-1 python -m app.integrations.gmail_watch backfill_attachments \
  --since 2026-02-07 --yes > /root/adjuntos-$(date +%Y%m%d).log 2>&1 &
tail -f /root/adjuntos-$(date +%Y%m%d).log
```

Flags: `--since` (requerido en la práctica; default hoy-6meses), `--until`
(default hoy, inclusivo), `--dry-run`, `--yes`, `--batch-size` (default 100).
No descarga ningún binario en ningún modo; el report final muestra el tamaño
agregado solo como dato informativo. Re-ejecutable sin duplicar (los
mensajes con adjuntos ya registrados se saltan).

## Backlog (fuera de este PR)

- Convertir el remitente de un email huérfano en lead desde la propia UI.
- Backfill retroactivo de `contact_id` en emails huérfanos cuando se crea un
  contacto con ese email.
- Notificación push in-app al llegar un mail.
