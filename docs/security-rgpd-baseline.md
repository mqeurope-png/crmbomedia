# Base de seguridad y RGPD

## Medidas obligatorias antes de producción

- Sustituir `SECRET_KEY` por un secreto robusto y gestionado fuera del repositorio.
- Usar HTTPS en el dominio público mediante proxy Nginx y Let's Encrypt.
- Mantener PostgreSQL y Redis sin exposición pública.
- Cifrar API keys de conectores antes de persistirlas.
- Registrar accesos, cambios relevantes, exportaciones y errores de sincronización.
- Verificar backups diarios y restauración periódica.
- Revisar usuarios, roles y permisos antes de operar con datos reales.
- Añadir 2FA para administradores antes de producción.
- Auditar cualquier exportación de contactos o empresas.

## Consentimiento marketing

El campo `marketing_consent` acepta estos estados:

- `unknown`: no consta base jurídica o consentimiento.
- `granted`: puede recibir campañas si no hay rebote duro ni baja.
- `denied`: no debe recibir campañas.
- `unsubscribed`: baja prioritaria; no se debe reactivar desde sincronizaciones externas.

Antes de integrar Brevo, cualquier sincronización debe comprobar `marketing_consent`, `is_email_valid` y el historial de bajas.

## Minimización de datos

El modelo inicial guarda datos comerciales mínimos y deja los payloads completos de conectores para fases posteriores, cuando exista una política de retención y auditoría definida.

## Integraciones externas

No se deben guardar API keys reales en el repositorio ni en texto plano. Los ajustes de integraciones solo guardan metadatos no secretos (`enabled`, `mode`, `status`, URL base futura, etiqueta, estado textual de credenciales y notas). Los conectores deben usar variables de entorno o un almacén de secretos y cifrado en reposo antes de producción.

## Auditoría mínima

El MVP registra logins y acciones CRM relevantes en `audit_logs`: creación/edición/desactivación de contactos y empresas, creación de notas, creación de tareas y cambios en ajustes de integraciones. Las exportaciones y cambios de permisos deberán auditarse en un sprint posterior.

## Recuperación de contraseña

El envío de email está stubbeado en el MVP. Antes de producción debe sustituirse por un proveedor transaccional, tokens con caducidad estricta y no devolver nunca el token en la respuesta HTTP.
