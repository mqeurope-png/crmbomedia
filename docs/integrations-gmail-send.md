# Gmail — envío de emails desde el CRM

Sprint Email v1. Una vez el usuario autoriza los scopes Gmail
(`gmail.send`, `gmail.modify`, `gmail.settings.basic`), el CRM
puede:

- Enviar emails desde cualquiera de los aliases "Send mail as"
  verificados (`info@bomedia.net`, `ventas@bomedia.net`, etc.).
- Recibir replies a hilos iniciados desde el CRM en tiempo real
  via Cloud Pub/Sub (ver `docs/integrations-gmail-pubsub.md`).

## Flujo del usuario

1. `/account` → sección Google Calendar. Si el usuario ya
   estaba conectado con Fase 2 pero le faltan los scopes Gmail,
   verá un banner amarillo: *"Necesitamos permisos adicionales
   para enviar emails desde el CRM"*. Click → reinicia el OAuth
   con la lista de scopes ampliada.
2. Tras autorizar, el sistema llama internamente a
   `watch_mailbox` para empezar a recibir notificaciones de
   replies.
3. Desde la ficha de un contacto, el botón **📧 Email** en la
   sidebar de "Acciones rápidas" abre el composer.
4. El composer carga los aliases verificados de la cuenta Gmail
   del usuario (no se pueden usar aliases sin verificar).
5. El operador escribe asunto + cuerpo y pulsa **Enviar**. El
   email se entrega vía Gmail API, aparece en *Enviados* de la
   cuenta Gmail del usuario y se persiste en `email_threads` +
   `email_messages` del CRM.
6. Cuando el destinatario responde, el webhook Pub/Sub trae el
   reply al CRM en segundos. El thread queda marcado con
   "Nuevo" y aparece en `/emails`.

## Listados y vistas

- `/emails` — todos los hilos iniciados por el usuario actual.
- `/emails/[thread_id]` — detalle de un hilo, con responder
  inline.
- Tab "Emails" en la ficha de contacto — hilos donde participa
  ese contacto.
- `/admin/emails` — vista admin con todos los hilos del CRM.

## Lo que NO incluye Sprint Email v1

- No es una bandeja de entrada completa: solo se importan
  replies a hilos iniciados desde el CRM. Si alguien escribe a
  `info@bomedia.net` desde cero, ese email se queda en Gmail.
- Sin attachments todavía.
- Sin drafts persistentes.
- Sin sync de la carpeta *Enviados* — solo lo enviado desde el
  CRM se ve aquí.
- Sin WYSIWYG editor: textarea para texto plano + textarea
  opcional para HTML con preview iframe.

## Límites

Gmail API: ~500 emails/día por cuenta personal, 2000 por
Workspace. Si necesitas envíos masivos, sigue usando Brevo.
