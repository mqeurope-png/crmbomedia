# Integración Google Calendar

Mini-PR C Fase 2. Cada usuario del CRM conecta su propia cuenta
Google, elige uno de sus calendarios y las tareas que crea desde
`/tasks` (o el modal del contacto) se espejan como eventos en ese
calendario.

La sincronización es unidireccional: **tarea CRM → evento Google**.
Eventos creados directamente en Google Calendar no aparecen como
tareas. Eso queda para una fase posterior.

## Setup en Google Cloud (admin, una vez)

1. https://console.cloud.google.com — crea un proyecto (o reutiliza
   uno) llamado `CRMBO Media CRM`.
2. **APIs y servicios → Habilitar** → busca **Google Calendar API**.
3. **Credenciales → Crear credencial → ID de cliente de OAuth 2.0**.
   - Tipo: **Aplicación web**.
   - **Orígenes JavaScript autorizados**:
     `https://crm.tudominio.com`
   - **URIs de redireccionamiento autorizadas**:
     `https://crm.tudominio.com/api/integrations/google/callback`
4. Copia el **Client ID** y **Client Secret**. Pégalos en
   `/opt/crmbo/.env.production`:

   ```
   GOOGLE_OAUTH_CLIENT_ID=…apps.googleusercontent.com
   GOOGLE_OAUTH_CLIENT_SECRET=…
   GOOGLE_OAUTH_REDIRECT_URI=https://crm.tudominio.com/api/integrations/google/callback
   ```

5. Rebuild + restart del contenedor `api`:

   ```
   docker compose --env-file .env.production -f docker-compose.prod.yml \
     -f docker-compose.plesk.yml up -d --build --force-recreate api
   ```

6. Aplica la migración (la 0028 añade `user_google_integrations`):

   ```
   docker compose --env-file .env.production exec api alembic upgrade head
   ```

Si una de las tres variables (`CLIENT_ID`, `CLIENT_SECRET`,
`REDIRECT_URI`) está vacía, la API responde 503 con mensaje
explícito y la UI muestra "Pídele al admin que configure las
credenciales OAuth". No se cae, no devuelve 500.

## Flujo de conexión (operador)

1. `/account` → sección "Google Calendar" → **Conectar cuenta Google**.
2. La SPA llama `GET /api/integrations/google/authorize`; el backend
   genera un `state` CSRF-safe, lo cachea en Redis 10 min asociado
   al `user_id`, y devuelve la URL de consentimiento.
3. La SPA hace `window.location.href = url`. Google pide consentimiento
   con los scopes `calendar.readonly`, `calendar.events`,
   `openid email`.
4. Google redirige a `/api/integrations/google/callback?code=…&state=…`.
   El backend valida el `state`, intercambia el `code` por
   `access_token` + `refresh_token` (ambos cifrados con la Fernet
   key de `INTEGRATION_SECRETS_KEY`), persiste la fila
   `user_google_integrations` y redirige a `/account/google-setup`.
5. `/account/google-setup` lista los calendarios del usuario y los
   muestra como una lista de radios. El usuario elige uno, hace
   `PATCH /api/integrations/google/calendar` y vuelve a `/account`.

## Sincronización de tareas

- **Crear tarea con checkbox marcado** → tras `POST /api/tasks` el
  backend invoca `sync_task_to_calendar(task)`. Si el assignee tiene
  Google conectado + calendario seleccionado, se crea un evento con
  título = `task.title`, descripción = `task.description` + link al
  CRM, start/end basados en `task.due_at` (slot de 30 min, sin
  `end_at` por ahora), recordatorio = `reminder_minutes_before` si
  está. Timezone: `Europe/Madrid` (hardcoded; multi-timezone es
  futuro PR).
- **PATCH tarea** → si la tarea tiene `google_event_id`, el backend
  hace `events.patch` con los campos nuevos.
- **DELETE tarea** → `events.delete` previo al borrado de la fila.
- **Cualquier fallo** (Google caído, refresh token revocado, scope
  insuficiente) se loggea como warning y la operación local (crear,
  modificar, borrar la tarea) sigue su curso. La integración nunca
  bloquea el flujo principal.

### Multi-user

- Cada user del CRM tiene su PROPIA conexión Google independiente.
  Aunque dos users conecten la misma cuenta Google (un sysadmin
  que opera con varias identidades en el CRM, p. ej.), cada uno
  tiene su propia fila en `user_google_integrations` con sus
  tokens, sus scopes, su selección de calendario y su watch
  Gmail. No se comparte nada entre users.
- Una tarea creada por el user A pero asignada al user B se
  sincroniza en el calendario **del user B**, no del A.
- Si B no tiene Google conectado, la tarea se crea normalmente y
  la sync se omite en silencio (warning en los logs).
- En `/account` el `google_email` aparece claramente para que el
  user vea con qué cuenta Google está conectado — útil cuando
  alguien gestiona varias.
- Si el operador detecta dos filas en `user_google_integrations`
  para el mismo `google_email` y cree que es por error
  (conexiones duplicadas accidentales), la consolidación es
  manual: revisar qué `user_id` corresponde a qué identidad del
  CRM y borrar la sobrante. No hay UNIQUE en `google_email` a
  propósito.

## Cambio de calendario

`/account/google-setup` permite cambiar el calendario en cualquier
momento. **Importante**: los eventos ya creados siguen en el
calendario antiguo (no se migran). La UI muestra el warning
explícitamente antes de confirmar el cambio.

## Refresh y desconexión

- El cliente hace refresh automático del `access_token` cuando le
  queda menos de 60 s de vida.
- Si Google rechaza el `refresh_token` (`invalid_grant` — el user lo
  revocó desde su cuenta Google), la fila
  `user_google_integrations` se elimina y la próxima visita a
  `/account` muestra "Conectar cuenta Google" de nuevo.
- Desde `/account` → "Desconectar": se hace POST a
  `https://oauth2.googleapis.com/revoke` (best-effort) y se borra
  la fila local.

## Lo que NO incluye este PR (Fase 2)

- Sync de eventos Google → tareas CRM (lectura, no inversa).
- Widget de calendario en el dashboard (Fase 3).
- Notificaciones push o por email (Sprint E).
- Crear calendarios nuevos desde el CRM (el user los crea en
  Google directamente).
- Outlook / iCloud / otros proveedores.
- Multi-timezone (siempre `Europe/Madrid`).
