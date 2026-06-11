# Gmail Push Notifications via Cloud Pub/Sub

Sprint Email v1. Recibimos los replies a los hilos iniciados
desde el CRM en tiempo real usando el mecanismo oficial de
Google: Gmail llama a Cloud Pub/Sub cada vez que llega un
mensaje nuevo, Pub/Sub re-emite a nuestro webhook, y un job RQ
procesa la *history* para importar los mensajes nuevos.

## Setup en Google Cloud Console

Una sola vez (admin).

1. En el mismo proyecto donde está el OAuth de Fase 2 ("CRMBO
   Media CRM"):
2. Habilitar **Cloud Pub/Sub API**.
3. **Topic** → crear `crmbo-gmail-events`.
4. Dar permiso `pubsub.publisher` al service account de Gmail
   (`gmail-api-push@system.gserviceaccount.com`) sobre ese topic.
   Sin este paso Gmail no podrá publicar.
5. **Suscripción** push:
   - Tipo: **Push**.
   - URL: `https://crm.tudominio.com/api/webhooks/gmail`.
   - Verificación: o JWT firmado (default) o un token
     compartido. Si eliges JWT (recomendado), deja
     `GMAIL_PUBSUB_VERIFICATION_TOKEN` vacío y la API hace
     verify de firma con `google-auth`. Si eliges el token
     compartido, generar con `openssl rand -hex 32` y pegarlo
     en el header `Authorization: Bearer <token>` de la
     suscripción.
6. Copia los nombres completos del topic y la suscripción a
   `.env.production`:

   ```
   GMAIL_PUBSUB_PROJECT_ID=<project-id>
   GMAIL_PUBSUB_TOPIC=projects/<project-id>/topics/crmbo-gmail-events
   GMAIL_PUBSUB_SUBSCRIPTION=projects/<project-id>/subscriptions/crmbo-gmail-push
   GMAIL_PUBSUB_VERIFICATION_TOKEN=<vacío o token compartido>
   ```

7. Rebuild + restart de `api` y `worker`.

## Flujo runtime

1. Cuando un usuario autoriza Gmail desde `/account`, el sistema
   llama a `client.watch_mailbox(topic_name)`. Esto:
   - Registra una "watch" en Gmail que vive 7 días.
   - Devuelve `historyId` + expiración.
   - Persistimos la fila en `gmail_pubsub_watches`.
2. Cuando llega un email a la cuenta del usuario, Gmail publica
   un mensaje al topic con `{emailAddress, historyId}`.
3. Pub/Sub lo entrega a `/api/webhooks/gmail` via push HTTP POST.
4. La API valida el JWT (o el token compartido), encola el job
   `gmail:process_history` y responde 200 rápido.
5. El worker:
   - Llama a `client.list_history(last_processed_history_id)`.
   - Itera por `messagesAdded`.
   - Para cada message, si el `threadId` corresponde a un
     `email_threads` que el CRM inició, importa el message
     completo, crea fila `email_messages` inbound y marca
     `has_unread_replies=true`.
   - Actualiza el `history_id` del watch al nuevo.

## Renovación del watch

Las watches expiran cada 7 días. El job `gmail:renew_watches`
corre cada 5 días (con SETNX para coordinar entre instancias) e
itera todos los usuarios con scopes Gmail, llamando a
`watch_mailbox` para topar la expiración. Fallos individuales se
loggean y el batch continúa.

## Troubleshooting

- **No llegan webhooks**: comprobar permiso publisher del SA de
  Gmail sobre el topic.
- **401 en el webhook**: el JWT viene mal firmado o el token
  compartido no coincide. Mirar logs.
- **Replies no aparecen**: verificar que el `threadId` de Gmail
  coincide con el de `email_threads`. Si el usuario respondió
  desde Gmail antes de que el CRM enviara, no se importa (el
  thread no existe en CRM).
