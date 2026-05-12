# Seguridad — almacén cifrado de API keys

Este documento describe cómo se cifran las API keys de las integraciones externas (AgileCRM, Brevo, Freshdesk, FactuSOL) y cómo operar la clave maestra en producción. Complementa `docs/security-rgpd-baseline.md`, que cubre RGPD y políticas generales, y `docs/integrations.md`, que documenta el modelo conceptual de cuentas múltiples.

## 1. Modelo

- Las claves de proveedores externos viven en la columna `integration_accounts.api_key_encrypted` (TEXT, nullable). Cada fila representa una **cuenta** identificada por la pareja natural `(system, account_id)`; el operador puede tener varias cuentas por sistema (p. ej. una por mercado en AgileCRM).
- El cifrado es **simétrico** con [Fernet](https://cryptography.io/en/latest/fernet/) (`cryptography` ≥ 44.0). Fernet aporta AES-128-CBC + HMAC-SHA256 + nonce aleatorio + caducidad opcional.
- La clave maestra es una sola para todo el sistema: **`INTEGRATION_SECRETS_KEY`** (44 chars urlsafe base64). Se lee desde el entorno y **no** se guarda en BBDD. La misma clave cifra **todas** las cuentas.
- La app **no arranca** si `INTEGRATION_SECRETS_KEY` falta o no es una clave Fernet válida (`pydantic.ValidationError` durante el bootstrap).
- La API **nunca** devuelve ni el plaintext ni el `api_key_encrypted`. El único campo derivado expuesto es `has_api_key: bool` y la fecha `api_key_set_at`.
- Los logs de auditoría registran `integration_account.api_key_set` y `integration_account.api_key_deleted` con actor, `system`, `account_id` y timestamp. **Nunca** se loggea el secreto ni el ciphertext.
- Algunos proveedores (AgileCRM) necesitan también un identificador de usuario (email) para autenticar. Se guarda en `integration_accounts.auth_identifier` (VARCHAR, **plaintext**, igual que `account_label` o `notes`) — **no es secreto**. Solo la API key se cifra; el email aparece tal cual en `audit_logs.metadata`, `sync_logs.metadata` y en el JSON de la API.

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

curl -s -X PUT $API/api/integration-accounts/brevo/default/api-key \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"api_key":"xkeysib-...."}'
```

Si tienes varias cuentas (p. ej. AgileCRM ES y AgileCRM UK), sustituye `default` por el `account_id` correspondiente:

```bash
curl -s -X PUT $API/api/integration-accounts/agilecrm/agilecrm-es/api-key ...
curl -s -X PUT $API/api/integration-accounts/agilecrm/agilecrm-uk/api-key ...
```

Respuesta esperada: la cuenta con `has_api_key: true`, `api_key_set_at` actualizado y `credential_status: "configured"`. **Sin** plaintext.

## 4. Borrar una API key

```bash
curl -s -X DELETE $API/api/integration-accounts/brevo/default/api-key \
  -H "Authorization: Bearer $TOKEN"
```

Esto pone `api_key_encrypted=NULL`, `api_key_set_at=NULL`, `api_key_last_used_at=NULL` y `credential_status="not_configured"`.

## 5. Uso desde un conector

Los conectores nunca leen la columna directamente. El helper público está en `app/integrations/credentials.py`:

```python
from app.integrations.credentials import get_decrypted_api_key
from app.models.crm import ExternalSystem

# Single-account install (sin cambios): se usa el row `account_id='default'`
# que la migración 20260515_0007 deja preservado.
api_key = get_decrypted_api_key(ExternalSystem.BREVO)

# Multi-account: pasa el account_id explícitamente.
key_es = get_decrypted_api_key(ExternalSystem.AGILECRM, "agilecrm-es")
key_uk = get_decrypted_api_key(ExternalSystem.AGILECRM, "agilecrm-uk")

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
   from sqlalchemy import select
   from sqlalchemy.orm import Session
   from app.db.session import get_engine
   from app.integrations.credentials import get_decrypted_api_key
   from app.models.integration_settings import IntegrationAccount
   with Session(get_engine()) as s:
       rows = s.scalars(select(IntegrationAccount)).all()
       for r in rows:
           k = get_decrypted_api_key(r.system, r.account_id)
           print(r.system.value, r.account_id, k or '<unset>')
   " | tee /tmp/secrets.txt   # protegido fuera del repo, borrar tras rotación
   ```

3. **Sustituir `INTEGRATION_SECRETS_KEY`** en el entorno (env var o `.env.production`) y reiniciar la app.

4. **Reintroducir cada API key** vía `PUT /api/integration-accounts/{system}/{account_id}/api-key` con el plaintext capturado en el paso 2 (una llamada por cuenta).

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
- `test_get_integration_account_never_returns_plaintext` — el endpoint nunca devuelve la key en claro.
- `test_put_api_key_persists_ciphertext_not_plaintext` — lo que se guarda en la BBDD es el ciphertext.
- `test_settings_fail_fast_when_integration_secrets_key_missing` — sin la clave, la app no arranca.
- `test_audit_log_records_set_and_delete_without_secret` — la auditoría no registra el secreto.

Para una validación end-to-end manual contra una BBDD real:

```bash
# arranca la app, autentícate como admin, guarda una key
# luego, desde una shell con acceso a la BBDD:
mysql -u crm -p crm -e "SELECT system, account_id, LEFT(api_key_encrypted, 32), api_key_set_at FROM integration_accounts;"
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
5. Provoca un error (`curl https://crm.tudominio.com/api/integration-accounts/foo/bar` con `foo` invalido) y comprueba que aparece en Sentry.

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

---

# Two-Factor Authentication (TOTP)

> **2FA es opcional para todos los roles, incluido `admin`.** Cualquier usuario puede activarlo voluntariamente desde *Mi cuenta → Seguridad / 2FA*. No hay enforcement por rol: la app no obliga a activarlo ni bloquea endpoints por falta de 2FA. (Hubo una fase previa con enforcement obligatorio para admin; se retiró por fricción operativa.)

Implementación basada en TOTP (RFC 6238) — funciona con Google Authenticator, Authy, 1Password, Bitwarden, etc. Sin SMS, sin WebAuthn (lo segundo se considera en una fase posterior).

## Modelo de datos

Cuatro columnas nuevas en `users`:

| Columna | Tipo | Descripción |
|---|---|---|
| `totp_secret_encrypted` | `TEXT?` | Secreto base32 cifrado con la misma `INTEGRATION_SECRETS_KEY` que protege las API keys de integraciones. La clave nunca sale del servidor. |
| `totp_enabled` | `BOOL` | `true` solo cuando el usuario ha confirmado el primer código con la app. |
| `totp_confirmed_at` | `DATETIME?` | Timestamp de activación. |
| `backup_codes_hash` | `TEXT?` | JSON array con los hashes pbkdf2 de los 8 backup codes vigentes. Cada uso elimina el hash matched (consumo single-use). |

Migración: `20260512_0004_user_totp.py`.

## Flujo de login en dos pasos

```
POST /api/auth/login                                 (paso 1)
  → si user.totp_enabled == true:
      {access_token: <pre_2fa token, 5 min>, requires_2fa: true, limited: false}
  → si user.totp_enabled == false (cualquier rol, incluido admin):
      {access_token: <full token>, requires_2fa: false, limited: false}

POST /api/auth/2fa/verify                            (paso 2, solo si requires_2fa)
  body: {temp_token, code}    // code = 6-digit TOTP o backup code de 10 chars
  → {access_token: <full token>, requires_2fa: false, limited: false}
```

El `pre_2fa` JWT lleva el claim `pre_2fa: true`, dura 5 minutos y solo es aceptado por `/api/auth/2fa/verify`. Cualquier otro endpoint con ese token responde `401 Complete 2FA verification first`.

## Claim `limited` — heredado, sin enforcement

El JWT soporta un claim `limited: true` que se usaba para gatear los endpoints sensibles cuando un admin no tenía 2FA. **Hoy no se setea nunca** y `require_admin` no lo lee. Los campos quedan en el código (response del login, `create_access_token` los acepta, etc.) por compatibilidad de tokens viejos en circulación durante el TTL de 8 horas y para no romper consumidores que ya leen `limited` del JSON. Si en el futuro se reintroduce enforcement, basta con volver a setear el flag en `login` y reactivar el check en `require_admin`.

## Backup codes

8 códigos hex de 10 caracteres (≈40 bits de entropía cada uno), generados por `secrets.token_hex(5)`. Se hashean con pbkdf2 (mismo helper que las contraseñas) y se almacenan como `JSON list` en `backup_codes_hash`. Cuando un código se consume con éxito en `/api/auth/2fa/verify`, se elimina del array. Cuando el array queda vacío, la columna vuelve a `NULL` y el usuario debe regenerar 2FA si pierde el dispositivo.

Los códigos se devuelven en plano una sola vez en la respuesta de `/api/auth/2fa/confirm`. La UI los enseña con un botón "He guardado mis códigos de respaldo".

## Endpoints

| Método | Path | Auth | Comportamiento |
|---|---|---|---|
| POST | `/api/auth/login` | none | Devuelve `requires_2fa` + token (temp si `totp_enabled`, full si no). `limited` siempre `false`. |
| POST | `/api/auth/2fa/verify` | body con temp_token | Intercambia (temp_token, code) por el JWT final. Acepta backup code. |
| POST | `/api/auth/2fa/setup` | usuario logueado | Genera secret + URI otpauth; `totp_enabled=false` hasta confirmar. |
| POST | `/api/auth/2fa/confirm` | usuario logueado | Verifica el primer código y devuelve los 8 backup codes (una sola vez). |
| POST | `/api/auth/2fa/disable` | usuario + password | Limpia las cuatro columnas. Audit log. |
| GET | `/api/auth/me` | usuario logueado | Añade `totp_enabled` y `requires_2fa_setup`. |

## Auditoría

Cada acción 2FA registra una entrada en `audit_logs`:

- `start_2fa_setup` — al generar un secret nuevo.
- `enable_2fa` — al confirmar.
- `disable_2fa` — al desactivar.
- `verify_2fa` — login completado con código TOTP.
- `verify_2fa_backup_code` — login completado con un código de respaldo.
- `reset_2fa_cli` — emergencia, vía `scripts/reset-user-2fa.py`.

## Recuperación de emergencia

Si un admin pierde tanto el dispositivo TOTP **como** los backup codes:

```bash
ssh deploy@<vps>
cd /opt/crmbo

# Desde el contenedor (preferido):
sudo docker compose -f docker-compose.prod.yml exec api \
  python -m scripts.reset_user_2fa --email admin@tudominio.com

# O desde el host con un venv y la BBDD accesible:
python scripts/reset-user-2fa.py --email admin@tudominio.com
```

El script pide `RESET` por consola (o `--yes` para automatizar). Limpia las cuatro columnas 2FA, registra `reset_2fa_cli` en audit, y el siguiente login del admin sale `limited` hasta que vuelva a enrolar TOTP. Solo lo puede ejecutar alguien con SSH al VPS — exactamente el mismo nivel de privilegio que reiniciar la pila.

## Política de rotación / pérdida del secret

- **TOTP secret**: cifrado con la `INTEGRATION_SECRETS_KEY`. Si esa Fernet se rota, los secretos TOTP existentes quedan ilegibles y todos los usuarios con 2FA deben volver a enrolar. Documentado en la sección de rotación de `INTEGRATION_SECRETS_KEY`.
- **Backup codes**: hashes salados; no se pueden recuperar. La única vía es regenerarlos (disable + setup + confirm).

## Verificación

```bash
cd backend
python -m pytest tests/test_2fa.py -q
```

13 tests cubren:

- Login devuelve `requires_2fa` cuando hay TOTP, `limited` cuando admin sin TOTP, normal en otros casos.
- `/auth/2fa/verify` con código correcto devuelve un JWT final no limitado.
- Código incorrecto → 401.
- Un JWT final reusado como temp_token → 401.
- Backup code de un solo uso (segundo intento del mismo código → 401, otro código sí funciona).
- Admin sin 2FA puede `/auth/me` y `/api/contacts` pero **no** `/api/users` ni `/api/audit-logs`.
- Admin sin 2FA puede ejecutar setup + confirm; tras login + verify, ya accede a `/api/users`.
- `/auth/2fa/disable` requiere la password actual; password incorrecta → 401.
- `/auth/2fa/setup` cuando ya está habilitado → 409.
- Pre-2FA token NO puede acceder a endpoints protegidos.

---

# Audit logging

Tabla `audit_logs` actúa como pista forense de toda acción sensible del CRM. Implementación en `app/core/audit.py` (constantes `Action.*` + helper `record_event`) y emisión desde `app/api/routes.py`, `app/api/integration_settings.py` (módulo del refactor multi-cuenta) y `app/api/gdpr.py`.

## Esquema

| Columna | Tipo | Notas |
|---|---|---|
| `id` | `VARCHAR(36)` PK | UUID. |
| `actor_user_id` | `VARCHAR(36)?` | FK → `users.id`. Nullable para acciones del sistema (CLI, eventos pre-login). |
| `actor_email` | `VARCHAR(255)?` | Email del actor en el momento del evento, incluso si después se desactiva o cambia. Para login_failed lleva el email intentado. |
| `action` | `VARCHAR(120)`, indexed | Cadena normalizada dotted: `auth.login_success`, `user.password_set_by_admin`, etc. |
| `target_type` | `VARCHAR(120)` | Categoría del recurso afectado: `user`, `contact`, `company`, `note`, `task`, `integration_account`, `audit_log`, `endpoint`. |
| `target_id` | `VARCHAR(36)?` | id del recurso afectado (o la `path` del endpoint en accesos denegados). |
| `metadata` | `TEXT?` (JSON) | Detalles estructurados (campos modificados, antes/después de un cambio de rol, filtros del export, etc.). **Nunca** contiene secretos. |
| `message` | `TEXT?` | Texto libre heredado de la primera versión. Para entradas nuevas suele estar vacío en favor de `metadata`. |
| `ip_address` | `VARCHAR(45)?` | Respeta `X-Forwarded-For` / `X-Real-IP` (Nginx, Plesk). IPv6 cabe. |
| `user_agent` | `TEXT?` | Cabecera User-Agent completa. |
| `created_at` | `DATETIME` | Cuándo ocurrió. |
| `updated_at` | `DATETIME` | (Heredado de `TimestampMixin`; las filas no se mutan, así que coincide con `created_at`.) |

Migración: `20260513_0005_audit_log_fields.py` renombra `entity_type → target_type`, `entity_id → target_id`, y añade las cuatro columnas nuevas. Los rows previos a esta migración conservan su contenido pero usan acciones antiguas (`login`, `create_contact`, ...); convivirán hasta que envejezcan fuera del retain.

## Eventos capturados

### Autenticación (`auth.*`)

| Acción | Disparada por | Metadata |
|---|---|---|
| `auth.login_success` | `POST /api/auth/login` con credenciales válidas. | — |
| `auth.login_failed` | Mismo endpoint con email inexistente, usuario desactivado o password mal. | `reason` ∈ {`user_not_found`, `user_inactive`, `invalid_password`}; `actor_email` = email intentado; `ip_address`/`user_agent` capturados. |
| `auth.password_changed` | `POST /api/auth/change-password`. | — |
| `auth.password_reset_requested` | `POST /api/auth/password-reset/request` (usuario existe). | — |
| `auth.password_reset_confirmed` | `POST /api/auth/password-reset/confirm`. | — |
| `auth.2fa_setup_started` | `POST /api/auth/2fa/setup`. | — |
| `auth.2fa_enabled` | `POST /api/auth/2fa/confirm`. | — |
| `auth.2fa_disabled` | `POST /api/auth/2fa/disable`. | — |
| `auth.2fa_verified` | `POST /api/auth/2fa/verify` con TOTP. | — |
| `auth.2fa_verified_backup_code` | Mismo endpoint pero consumiendo un backup code. | — |
| `auth.2fa_reset_cli` | `scripts/reset-user-2fa.py`. | — |

### Usuarios (`user.*`)

| Acción | Endpoint | Metadata |
|---|---|---|
| `user.created` | `POST /api/users`. | `target_email`, `target_role` |
| `user.updated` | `PATCH /api/users/{id}`. | `target_email`, `changed_fields` |
| `user.role_changed` | Como anterior, escrito **además** cuando `role` cambia de valor. | `target_email`, `from_role`, `to_role` |
| `user.deactivated` | `PATCH /api/users/{id}/deactivate`. | `target_email` |
| `user.reactivated` | `PATCH /api/users/{id}/reactivate`. | `target_email` |
| `user.password_set_by_admin` | `PATCH /api/users/{id}/password`. | `target_email` |

### CRM (`company.*`, `contact.*`, `note.*`, `task.*`)

| Acción | Endpoint | Metadata |
|---|---|---|
| `company.created` / `company.updated` / `company.deactivated` | `POST/PATCH/PATCH ../companies/*`. | `name`, `changed_fields` (update). |
| `contact.created` / `contact.updated` / `contact.deactivated` | `POST/PATCH/PATCH ../contacts/*`. | `email`, `changed_fields` (update). |
| `note.created` | `POST /api/contacts/{id}/notes`. | `contact_id` |
| `task.created` | `POST /api/contacts/{id}/tasks`. | `contact_id`, `title` |

### Integraciones (`integration_account.*`)

Tras el refactor multi-cuenta (migración `20260515_0007`), todos los eventos incluyen siempre `system` **y** `account_id` en `metadata` para que el audit log pueda pivotarse por cualquiera de las dos dimensiones.

| Acción | Endpoint | Metadata |
|---|---|---|
| `integration_account.created` | `POST /api/integration-accounts/{system}`. | `system`, `account_id`, `display_name` |
| `integration_account.updated` | `PATCH /api/integration-accounts/{system}/{account_id}`. | `system`, `account_id`, `changed_fields` |
| `integration_account.deleted` | `DELETE /api/integration-accounts/{system}/{account_id}` (con `?force=true` si hay referencias). | `system`, `account_id`, `display_name`, `force` |
| `integration_account.api_key_set` | `PUT /api/integration-accounts/{system}/{account_id}/api-key`. | `system`, `account_id` (**nunca** la API key ni el ciphertext). |
| `integration_account.api_key_deleted` | `DELETE /api/integration-accounts/{system}/{account_id}/api-key`. | `system`, `account_id` |

### Audit log + acceso

| Acción | Origen | Metadata |
|---|---|---|
| `audit.exported` | `GET /api/audit-logs/export`. | `format`, `rows`, `filters` (action, action_prefix, actor_user_id, target_type, from, to) |
| `access.forbidden` | Cualquier dep de rol (`require_admin`/`require_manager`/`require_user`/`require_viewer`) cuando el rol actual no llega. | `method`, `path`, `required_role`, `actual_role`. `target_type` = `endpoint`, `target_id` = path. |

> **Nota sobre logout**: la sesión es stateless (JWT con TTL 8h). El logout en el frontend solo limpia `localStorage`; no hay endpoint para auditar.

## Listado y filtros

`GET /api/audit-logs` (solo `admin`) acepta:

| Param | Tipo | Descripción |
|---|---|---|
| `skip` | int | Paginación. |
| `limit` | int (1-100) | Tamaño de página, default 50. |
| `action` | string | Filtro exacto (`auth.login_failed`). |
| `action_prefix` | string | Filtro por prefijo (`auth.`, `user.`). |
| `actor_user_id` | uuid | Restringir a un usuario. |
| `target_type` | string | `user` / `contact` / `endpoint` / … |
| `from` | ISO datetime | Inicio de rango (`created_at >= from`). |
| `to` | ISO datetime | Fin de rango. |

La respuesta incluye `X-Total-Count: <int>` para que la UI pinte paginación.

## Export `GET /api/audit-logs/export`

- Solo `admin`. **El propio export escribe un row `audit.exported`** con `format`, `rows` y los filtros utilizados.
- Mismos filtros que el listado.
- Sin `from`/`to` → últimos **365 días** por defecto.
- `format` ∈ `{csv, json}`.
- Tope: **50 000 rows**. Si la consulta excede, `400 Bad Request` sugiriendo paginación / rangos más estrechos.
- CSV incluye: `id, actor_user_id, actor_email, action, target_type, target_id, metadata, message, ip_address, user_agent, created_at`. Comas y newlines dentro de campos se sustituyen por espacios para no romper el parsing.

## Política de privacidad de los audits

- Las API keys (plaintext o ciphertext) **nunca** aparecen en `metadata`, `message`, ni en ningún campo serializado.
- Las passwords **nunca** se loggean en ninguna forma.
- Para `auth.password_reset_requested` y `auth.password_reset_confirmed`, el token de reset **no** aparece en metadata (solo el id del usuario).
- Los logs `WARNING`/`ERROR` que `record_event` puede generar (por ejemplo si el commit falla) no contienen datos personales más allá del `target_id`.

## Tests

```bash
cd backend
python -m pytest tests/test_audit.py -q
```

Cubre cada categoría de evento, el header `X-Total-Count`, los filtros (`action`, `action_prefix`, `target_type`, rango de fechas), la auditoría del propio export, el límite de 50 000 rows (vía `monkeypatch.setattr(routes, "EXPORT_MAX_ROWS", 5)`) y el corte por defecto a los últimos 365 días.
