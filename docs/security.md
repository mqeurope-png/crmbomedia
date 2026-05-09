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
