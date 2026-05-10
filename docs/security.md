# Seguridad — almacén cifrado de API keys

Este documento describe cómo se cifran las API keys de las integraciones externas (AgileCRM, Brevo, Freshdesk, FactuSOL) y cómo operar la clave maestra en producción. Complementa `docs/security-rgpd-baseline.md`, que cubre RGPD y políticas generales.

## 1. Modelo

- Las claves de proveedores externos viven en la columna `integration_settings.api_key_encrypted` (TEXT, nullable).
- El cifrado es **simétrico** con [Fernet](https://cryptography.io/en/latest/fernet/) (`cryptography` ≥ 44.0). Fernet aporta AES-128-CBC + HMAC-SHA256 + nonce aleatorio + caducidad opcional.
- La clave maestra es una sola para todo el sistema: **`INTEGRATION_SECRETS_KEY`** (44 chars urlsafe base64). Se lee desde el entorno y **no** se guarda en BBDD.
- La app **no arranca** si `INTEGRATION_SECRETS_KEY` falta o no es una clave Fernet válida (`pydantic.ValidationError` durante el bootstrap).
- La API **nunca** devuelve ni el plaintext ni el `api_key_encrypted`. El único campo derivado expuesto es `has_api_key: bool` y la fecha `api_key_set_at`.
- Los logs de auditoría registran `set_integration_api_key` y `delete_integration_api_key` con actor, sistema y timestamp. **Nunca** se loggea el secreto ni el ciphertext.

## 2. Generar la clave

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Guárdala en el gestor de secretos del entorno (variables de entorno del runtime, secret manager del PaaS, o `.env.production` con `chmod 600` fuera del repo). **Nunca** la subas al control de versiones.

## 3. Almacenar una API key

Solo el rol `admin` puede operar.

```bash
TOKEN=$(curl -s -X POST $API/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@...","password":"..."}' | jq -r .access_token)

curl -s -X PUT $API/api/integration-settings/brevo/api-key \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"api_key":"xkeysib-...."}'
```

Respuesta esperada: el setting con `has_api_key: true`, `api_key_set_at` actualizado y `credential_status: "configured"`. **Sin** plaintext.

## 4. Borrar una API key

```bash
curl -s -X DELETE $API/api/integration-settings/brevo/api-key \
  -H "Authorization: Bearer $TOKEN"
```

Esto pone `api_key_encrypted=NULL`, `api_key_set_at=NULL`, `api_key_last_used_at=NULL` y `credential_status="not_configured"`.

## 5. Uso desde un conector

Los conectores nunca leen la columna directamente. El helper público está en `app/integrations/credentials.py`:

```python
from app.integrations.credentials import get_decrypted_api_key
from app.models.crm import ExternalSystem

api_key = get_decrypted_api_key(ExternalSystem.BREVO)
if api_key is None:
    raise RuntimeError("Brevo no configurado todavía")
# usar api_key únicamente para construir la cabecera/headers de la petición
```

El helper actualiza `api_key_last_used_at` como efecto secundario en cada llamada. El plaintext devuelto vive en memoria solo durante la llamada al conector — **no loggear, no persistir, no pasar a templates**.

## 6. Rotar `INTEGRATION_SECRETS_KEY`

Rotar es necesario cuando la clave actual pueda haberse comprometido (acceso al `.env`, dump de variables del sistema, ex-empleado con shell). Procedimiento sin downtime:

1. **Generar la nueva clave** (paso 2).
2. **Re-cifrar los secretos**: aún no hay script automatizado. Por cada integración configurada:

   ```bash
   # Con la app aún corriendo con la clave antigua, exporta el plaintext
   # llamando al helper en una shell privilegiada (NO desde un endpoint público):
   docker compose exec api python -c "
   from app.integrations.credentials import get_decrypted_api_key
   from app.models.crm import ExternalSystem
   for s in ExternalSystem:
       k = get_decrypted_api_key(s)
       print(s.value, k or '<unset>')
   " | tee /tmp/secrets.txt   # protegido fuera del repo, borrar tras rotación
   ```

3. **Sustituir `INTEGRATION_SECRETS_KEY`** en el entorno (env var o `.env.production`) y reiniciar la app.

4. **Reintroducir cada API key** vía `PUT /api/integration-settings/{system}/api-key` con el plaintext capturado en el paso 2.

5. **Borrar `/tmp/secrets.txt`** (`shred -u`) y purgar el historial del shell.

> Nota: el modelo actual no soporta dos claves simultáneas. Si se prevé rotación frecuente, plantear un esquema de keyring con `kid` antes de Fase 5.

## 7. Pérdida de la clave Fernet

Si `INTEGRATION_SECRETS_KEY` se pierde y no hay copia:

- **Los `api_key_encrypted` existentes son irrecuperables**. La app seguirá funcionando para todo lo demás, pero los conectores no podrán autenticar contra los proveedores.
- **Hay que reintroducir cada API key** desde el panel admin (paso 3) tras desplegar una clave nueva.
- Considera la clave maestra **al mismo nivel** que las contraseñas root del VPS o las claves SSH: backup en un gestor de secretos cifrado off-site (1Password, Bitwarden Vaultwarden, AWS Secrets Manager, IONOS HiDrive cifrado).

## 8. Verificación local

Después de un cambio en este código o tras una rotación, comprueba que el ciclo completo funciona:

```bash
cd backend
python -m pytest tests/test_integration_api_keys.py -q
```

Tests relevantes:

- `test_encrypt_decrypt_roundtrips_arbitrary_secret` — cifrado/descifrado sin pérdida.
- `test_decrypt_with_wrong_key_raises_decryption_error` — un ciphertext de otra clave no se descifra silenciosamente.
- `test_get_integration_setting_never_returns_plaintext` — el endpoint nunca devuelve la key en claro.
- `test_put_api_key_persists_ciphertext_not_plaintext` — lo que se guarda en la BBDD es el ciphertext.
- `test_settings_fail_fast_when_integration_secrets_key_missing` — sin la clave, la app no arranca.
- `test_audit_log_records_set_and_delete_without_secret` — la auditoría no registra el secreto.

Para una validación end-to-end manual contra una BBDD real:

```bash
# arranca la app, autentícate como admin, guarda una key
# luego, desde una shell con acceso a la BBDD:
mysql -u crm -p crm -e "SELECT system, LEFT(api_key_encrypted, 32), api_key_set_at FROM integration_settings;"
```

El campo debe verse como `gAAAAAB...` (prefijo Fernet versión 0x80) y nunca como el plaintext que introdujiste.

---

# Password policy

Reglas mínimas para contraseñas de usuario, aplicadas de forma consistente en creación, cambio, reset por admin y reset auto-servicio. Centralizadas en `backend/app/core/passwords.py`.

## Reglas

| Regla | Valor |
|---|---|
| Longitud mínima | **12** caracteres |
| Longitud máxima | 128 caracteres |
| Mayúscula | al menos una |
| Minúscula | al menos una |
| Dígito | al menos uno |
| Símbolos | recomendados (suman a la fortaleza visual), no obligatorios |
| Lista de bloqueo | `backend/app/core/common_passwords.txt` (~50 entradas comunes / leaked) |

La comparación con la blocklist es **case-insensitive**: `Password`, `PASSWORD` y `password` se rechazan por igual.

## Justificación

- 12 caracteres es el mínimo NIST recomendado actualmente (SP 800-63B Rev. 4 borrador) y supera el `8` que estaba implícito antes.
- Variedad (mayúscula + minúscula + dígito) frena ataques con diccionarios pequeños sin obligar a símbolos no-ASCII que rompen teclados internacionales.
- La blocklist es un sanity check sobre las contraseñas más reutilizadas (RockYou, NCSC bad list); evita que se acepten passwords que ya forman parte de wordlists públicas, sin pretender ser exhaustiva.
- No se exige rotación periódica obligatoria: NIST desaconseja forzar cambios sin causa, porque empuja a los usuarios a patrones predecibles.

## Aplicación

Validador `validate_password_policy(password)` (lanza `PasswordPolicyError(ValueError)`), enchufado a los schemas de:

- `POST /api/users` (crear usuario).
- `PATCH /api/users/{id}/password` (admin cambia password ajeno).
- `POST /api/auth/change-password` (usuario cambia su password).
- `POST /api/auth/password-reset/confirm` (recuperación de contraseña).

La violación devuelve **`422 Unprocessable Entity`** con el campo `detail[].msg` indicando qué regla falla en español.

## UI

`frontend/src/app/components/PasswordRequirements.tsx` muestra en tiempo real una checklist (✓/✗) y una barra de fortaleza (Débil / Media / Fuerte) en los formularios de `/admin/users`, `/account/password` y `/password-reset`. La regla autoritativa es la del backend; el componente solo sirve de hint y deshabilita el botón hasta que se cumple la política mínima.

## Verificación

```bash
cd backend
python -m pytest tests/test_password_policy.py -q
```

Cubre:

- Cada regla individual (longitud, mayúscula, minúscula, dígito, blocklist).
- Rechazo de `Password1234` (cumple las reglas estructurales pero está en la blocklist).
- Rechazo en cada uno de los 4 endpoints.
- Caso negativo: una contraseña conforme se acepta.

---

# Password-reset flow: producción vs desarrollo

`POST /api/auth/password-reset/request` cambia su contrato según `ENVIRONMENT`:

| Comportamiento | `production` | `development` / `test` |
|---|---|---|
| Status code | **`202 Accepted`** | `200 OK` |
| Cuerpo | `{"message": "If the email exists, a reset link has been sent."}` | `{"message": "...", "reset_token": "..."}` (si el email existe) |
| Email existe? | No revela. La respuesta es idéntica en ambos casos. | Mensaje distinto entre existe / no existe. |
| Entrega del token | Por email (TODO: pendiente conector transaccional). Por ahora se loggea un `warning` con `user_id`. | Devuelto en el cuerpo y loggeado en `INFO` para que Codespaces y los tests puedan completar el flujo. |

## Por qué

- En producción no se debe revelar la existencia de cuentas (account enumeration).
- En producción el token **nunca** sale por la respuesta HTTP: solo por el canal autenticado (email del titular).
- En desarrollo / test mantenemos la respuesta antigua para que los tests (`test_password_reset_request_and_confirm`) no necesiten un servicio SMTP, y para que se pueda probar el flujo end-to-end en Codespaces.

## UX de la pantalla `/password-reset`

La pantalla del frontend renderiza **uno de dos estados mutuamente excluyentes** según haya o no un token en el query string:

| Estado | URL | Qué se ve | Acción |
|---|---|---|---|
| **Solicitar enlace** | `/password-reset` (sin `?token=...`) | Solo el formulario de email + botón "Solicitar enlace de recuperación" | Tras enviar, el formulario se oculta y aparece un mensaje neutro "Si la cuenta existe, hemos enviado un enlace…" + link de vuelta al login. |
| **Restablecer contraseña** | `/password-reset?token=ABC123` (link del email) | Solo el formulario de "Nueva contraseña" + "Confirmar contraseña" + checklist + barra de fortaleza | El token va en `useState`, **nunca** se renderiza como input. Al hacer submit, se envía silenciosamente con `{token, new_password}`. |

Reglas:

- El input "Token" del flujo anterior **no existe**. El usuario nunca ve ni copia el token; se mueve solo del email a la URL a state de React.
- Si el usuario refresca la pantalla en modo "confirm", el token se vuelve a leer del query string. No se persiste en `localStorage` ni en cookies.
- Si el backend rechaza el token (`401` con "Invalid reset token", típicamente por caducidad o uso previo), la pantalla muestra el error **y** un enlace `Solicitar un nuevo enlace` que devuelve al modo "request".
- Tras un reset exitoso se redirige a `/login?flash=password-reset-success`. El login lee `flash`, muestra un banner verde "Contraseña actualizada. Inicia sesión con la nueva contraseña." y limpia el query string con `history.replaceState` para que un refresh no re-muestre el banner.

## TODO pendiente

Conectar un proveedor SMTP / transactional (probable: Brevo cuando Fase 5 lo integre, o un proveedor independiente antes). Hasta entonces, en producción la solicitud queda registrada (`audit_logs` + `password_reset_token_hash` en `users`) pero el token solo es accesible por consulta directa a BBDD por un operador. Documentado como riesgo en el README sección "Pendiente para hardening de producción".

## Verificación

```bash
cd backend
python -m pytest tests/test_password_policy.py::test_password_reset_request_in_production_returns_202_without_token -q
python -m pytest tests/test_password_policy.py::test_password_reset_request_in_production_neutral_for_unknown_email -q
python -m pytest tests/test_password_policy.py::test_password_reset_request_in_development_returns_token -q
```

Las tres pasan; las dos primeras prueban el comportamiento neutro de producción, la tercera garantiza la compatibilidad de desarrollo.

---

# Email service (Phase A — env-var config)

`POST /api/auth/password-reset/request` ahora **envía un email real** con el enlace de recuperación cuando hay SMTP configurado. La selección entre proveedor real y stub se hace por entorno; ver `app/services/email.py`.

## Selección de implementación

| `ENVIRONMENT` | `SMTP_HOST` | Servicio | Comportamiento |
|---|---|---|---|
| `production` | definido | `SMTPEmailService` | envía vía `aiosmtplib` |
| `production` | vacío | `ConsoleEmailService` + WARNING | imprime el email a stdout (no entrega) |
| `development` / `test` | cualquier valor | `ConsoleEmailService` | captura el email en `service.sent` para tests |

La factory `get_email_service()` está cacheada con `@lru_cache(maxsize=1)`. En producción la pila se inicializa una vez al arranque.

## Variables (Fase A)

```env
SMTP_HOST=smtp.ionos.es        # IONOS recomendado para deploys IONOS
SMTP_PORT=587                  # 587 STARTTLS, 465 SSL implícito
SMTP_USER=noreply@tudominio.com
SMTP_PASSWORD=<password del buzón>
SMTP_FROM=noreply@tudominio.com
SMTP_FROM_NAME=CRMBO Media CRM
SMTP_USE_TLS=true              # STARTTLS — usar con puerto 587
SMTP_USE_SSL=false             # SSL implícito — usar con puerto 465 (mutuamente exclusivos)
FRONTEND_BASE_URL=https://crm.tudominio.com
```

## Proveedores SMTP soportados (cualquiera vale)

| Proveedor | Host | Puerto | Notas |
|---|---|---|---|
| **IONOS** | `smtp.ionos.es` (ES) o `smtp.ionos.com` (intl) | 587 STARTTLS | Gratis si tienes mailbox IONOS contratado. SMTP_USER = email completo del buzón. |
| **SendGrid** | `smtp.sendgrid.net` | 587 STARTTLS | SMTP_USER = `apikey`, SMTP_PASSWORD = la API key. |
| **Postmark** | `smtp.postmarkapp.com` | 587 STARTTLS | SMTP_USER = SMTP_PASSWORD = Server Token. |
| **Brevo (transactional)** | `smtp-relay.brevo.com` | 587 STARTTLS | SMTP_USER = email login, SMTP_PASSWORD = SMTP key (no la API key). |
| **AWS SES** | `email-smtp.<region>.amazonaws.com` | 587 STARTTLS | Credenciales SES SMTP, no IAM. |

## Validar la conexión SMTP desde el VPS

```bash
docker run --rm -i \
  -e HOST="$SMTP_HOST" -e PORT="$SMTP_PORT" \
  -e USER="$SMTP_USER" -e PASSWORD="$SMTP_PASSWORD" \
  python:3.12-slim bash -lc 'pip install --quiet aiosmtplib && \
  python - <<PY
import asyncio, os
from email.message import EmailMessage
from aiosmtplib import SMTP

async def main():
    msg = EmailMessage()
    msg["From"] = os.environ["USER"]
    msg["To"] = os.environ["USER"]
    msg["Subject"] = "CRMBO smoke test"
    msg.set_content("ok")
    smtp = SMTP(hostname=os.environ["HOST"], port=int(os.environ["PORT"]), start_tls=True)
    await smtp.connect()
    await smtp.login(os.environ["USER"], os.environ["PASSWORD"])
    await smtp.send_message(msg)
    await smtp.quit()
    print("OK")

asyncio.run(main())
PY'
```

Si `OK` aparece, las credenciales son buenas y el firewall del VPS deja salir 587. Si falla, revisa `nc -zv $SMTP_HOST $SMTP_PORT` y los logs del proveedor.

## Tolerancia a fallos

- En **producción**, si el envío SMTP falla (red caída, credenciales mal, rate limit) la app **no rompe** la respuesta: sigue devolviendo `202 Accepted` con el mensaje neutro `"If the email exists, a reset link has been sent."` y registra un `WARNING` con `user_id` y la causa. Nunca se revela al cliente si el email existe ni qué falló.
- En **desarrollo**, el fallo de envío se loggea como `ERROR` con stack completo y la respuesta sigue trayendo el `reset_token` para que la prueba pueda completarse.

## Plantillas

`backend/app/templates/email/password_reset.{html,txt}` (Jinja2). Variables disponibles: `app_name`, `user_name`, `reset_url`, `expires_in_minutes`. La versión texto es el fallback que ven los clientes que rechazan HTML; la HTML lleva estilos inline para que sobreviva en clientes como Outlook que tiran CSS externo.

## Próxima iteración (Phase B)

El siguiente PR moverá la configuración SMTP detrás del panel admin de integraciones, con `SMTP_PASSWORD` cifrada en BBDD usando la `INTEGRATION_SECRETS_KEY` ya existente (mismo patrón que las API keys de Brevo/AgileCRM/etc.). Las env vars seguirán funcionando como **fallback** cuando no haya valor en BBDD, para no romper deploys actuales.

## Tests

```bash
cd backend
python -m pytest tests/test_email_service.py -q
```

- `test_console_service_captures_password_reset` — el render de las plantillas tiene token, URL bien formada y subject en español.
- `test_password_reset_request_sends_email` — flujo end-to-end: `POST /api/auth/password-reset/request` añade un email a `email_capture.sent` con el token correcto.
- `test_production_returns_202_when_smtp_fails` — fallback resiliente en producción.
- `test_factory_uses_smtp_when_production_and_host_set` / `_falls_back_to_console_when_production_missing_host` / `_uses_console_in_development_even_when_host_set` — selección de implementación por entorno.
- `test_smtp_service_maps_starttls_for_port_587` / `_implicit_ssl_for_port_465` — mapeo `SMTP_USE_TLS`/`SMTP_USE_SSL` a `aiosmtplib`.

---

# Error tracking (Sentry)

Backend y frontend reportan automáticamente excepciones no capturadas a Sentry **cuando hay DSN configurado**. Sin DSN, ambos SDKs son no-op: no se inicializan, no se envía nada. Esto cubre dev / Codespaces / despliegues self-hosted sin cuenta Sentry.

## Contrato de privacidad

| Configuración | Valor |
|---|---|
| `send_default_pii` | **`false`** (backend y frontend). Sentry no añade IP del cliente, user-agent, request body completo. |
| `before_send` | Hook propio que recorre el evento y redacta sensibles antes de salir del host. |
| Claves redactadas | `password`, `passwd`, `token`, `secret`, `api_key`, `apikey`, `authorization`, `cookie`, `session` (substring match, case-insensitive). Su valor se reemplaza por `[REDACTED]`. |
| Emails en strings | Cualquier email dentro de cualquier valor string se reemplaza por `[REDACTED EMAIL]` (regex `[A-Za-z0-9._%+\-]+@…`). |
| Profundidad | Recursivo: dicts, listas, tuplas. Cubre `request.data`, `breadcrumbs`, `extra`, `tags`, `exception.values`. |

Backend: `app/core/observability.py::scrub_pii` + `before_send_filter`.
Frontend: `frontend/src/app/lib/sentry-scrub.ts::scrubSentryEvent`. Misma lógica, mismo conjunto de needles, mismo formato de literales — los eventos se ven idénticos vengan de FastAPI o del browser.

## Variables

```env
# Backend (server-side)
SENTRY_DSN=https://<key>@sentry.io/<project>
SENTRY_TRACES_SAMPLE_RATE=0.1
GIT_SHA=<commit hash>          # CI lo exporta desde $GITHUB_SHA

# Frontend (browser bundle)
NEXT_PUBLIC_SENTRY_DSN=https://<key>@sentry.io/<project>
NEXT_PUBLIC_GIT_SHA=<commit hash>
```

`NEXT_PUBLIC_*` se inyecta al bundle en build time. **No** uses la misma DSN para backend y frontend en proyectos sensibles: separa los dos proyectos en Sentry para que el granted-access difiera.

## Crear el proyecto en Sentry (procedimiento)

1. https://sentry.io → *Projects → Create Project*.
2. **Backend**: platform *FastAPI*. Copia la DSN al `SENTRY_DSN` de `.env.production`.
3. **Frontend**: platform *Next.js*. Copia esa DSN al `NEXT_PUBLIC_SENTRY_DSN` de `.env.production`.
4. Reinicia la pila: `docker compose -f docker-compose.prod.yml up -d --force-recreate api frontend`.
5. Provoca un error (`curl https://crm.tudominio.com/api/integration-settings/foo` con `foo` invalido) y comprueba que aparece en Sentry.

## Alertas (recomendado)

En Sentry → *Alerts → Create Alert Rule*:

- **Backend — error rate spike**: "Number of errors > 10 in 5 minutes" → notify Slack/email.
- **Backend — new issue**: "When a new issue is created" → notify (especialmente útil para descubrir excepciones nuevas tras un deploy).
- **Frontend — page error**: "An issue is seen by 50 users in 1 hour" → notify.
- **Performance**: "p95 transaction duration > 2s" → notify (con `traces_sample_rate=0.1` el muestreo es útil pero no exhaustivo; subir a 0.5 si hace falta detalle).

## Releases y source maps

- `release` se setea desde `GIT_SHA` en CI y producción. Los eventos de cada deploy quedan agrupados por commit.
- `next.config.ts` envuelve la configuración con `withSentryConfig` cuando hay DSN. Esto **prepara** la subida de source maps (genera mappings, hide sources en cliente) pero **no** los sube todavía.
- Para activar la subida en CI hace falta `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` como secrets de GitHub. Es un follow-up explícitamente fuera del alcance de este PR.

## `tunnel` opcional para evitar adblockers

Sentry recomienda servir `/monitoring` (o cualquier ruta) como proxy hacia `sentry.io` para que los uBlock-likes no bloqueen el reporting. Para activarlo en el frontend:

```ts
// sentry.client.config.ts
Sentry.init({
  // ...
  tunnel: "/monitoring",
});
```

Y configurar Next.js para hacer rewrite de `/monitoring/*` a `https://sentry.io/api/...`. Documentado en https://docs.sentry.io/platforms/javascript/guides/nextjs/manual-setup/#tunnel pero **no activado** por defecto en este repo.

## Verificación local

```bash
cd backend
python -m pytest tests/test_observability.py -q
```

Cubre:

- `scrub_pii` redacta keys sensibles, emails dentro de strings y recurre por listas/tuplas.
- `before_send_filter` devuelve el evento ya scrubeado.
- `setup_sentry()` no llama a `sentry_sdk.init` cuando falta `SENTRY_DSN`.
- `setup_sentry()` con DSN llama a `init` con `send_default_pii=False`, `before_send=before_send_filter`, `release=git_sha or "unknown"`.
- Test e2e con FastAPI + transport mock: una excepción no capturada llega al transport con el email redactado.

## Lo que **no** se reporta

- Logs `INFO` / `WARNING` (Sentry captura solo errores y trazas con muestreo).
- Audit logs CRM (esos viven en BBDD vía `audit_logs`, no en Sentry).
- Body de respuestas exitosas. Solo errores y métricas de transacción.
- Plaintext de API keys, passwords, tokens — el `before_send` los borra antes de salir.
