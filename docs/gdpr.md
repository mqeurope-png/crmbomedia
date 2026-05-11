# RGPD · Derechos del titular

Este documento describe **cómo el CRM gestiona técnicamente** las
solicitudes RGPD (Reglamento (UE) 2016/679) que recibe la empresa: acceso,
rectificación, supresión, portabilidad y oposición.

> **Importante:** este flujo cubre el plano técnico. **No sustituye** la
> evaluación jurídica de cada solicitud (identidad del titular,
> legitimación, plazos, base jurídica, conservación legal obligatoria).
> El equipo legal o la persona DPO debe validar cada solicitud **antes**
> de marcarla como `completed`.

## Endpoints

Todos los endpoints están bajo `/api/gdpr/*` y exigen rol `admin`.

| Método | Ruta | Descripción |
| --- | --- | --- |
| `POST` | `/api/gdpr/requests` | Registra una solicitud (no la procesa). |
| `GET` | `/api/gdpr/requests` | Listado con filtros `status`, `request_type`, `subject_email`. |
| `GET` | `/api/gdpr/requests/{id}` | Detalle de una solicitud. |
| `PATCH` | `/api/gdpr/requests/{id}` | Cambia estado y/o notas internas. |
| `POST` | `/api/gdpr/requests/{id}/process` | Ejecuta el procesamiento técnico según `request_type`. |

Cada acción se registra en `audit_logs` con un evento `gdpr.*`:

- `gdpr.request_created`
- `gdpr.request_updated`
- `gdpr.request_processed`
- `gdpr.export_generated` (acceso y portabilidad)
- `gdpr.contact_erased` (supresión)
- `gdpr.audit_anonymized` (supresión)
- `gdpr.objection_applied` (oposición)
- `gdpr.rectification_guidance` (rectificación)

## Comportamiento por tipo de solicitud

### Acceso (`access`)

Genera un fichero **JSON** con todos los datos del titular:

- Datos de contacto (`contacts`).
- Notas y tareas vinculadas.
- Referencias externas (Brevo, AgileCRM…).
- Eventos de auditoría en los que figura como **actor** (`actor_email`).

Ruta del fichero: configurable con `GDPR_EXPORT_ROOT` (por defecto
`var/gdpr_exports/`). El nombre incluye el email saneado y un timestamp.

### Rectificación (`rectification`)

**No modifica datos automáticamente.** Devuelve la lista de endpoints
PATCH que el operador debe usar para aplicar la corrección:

- `PATCH /api/contacts/{contact_id}`
- `PATCH /api/companies/{company_id}`
- `POST /api/contacts/{contact_id}/notes` (documentar la base jurídica).

Tras aplicar el cambio, marcar la solicitud como `completed` con
`PATCH /api/gdpr/requests/{id}`.

### Supresión (`erasure`)

Procedimiento **irreversible**:

1. Borrado físico del `Contact` y sus relaciones en cascada
   (`notes`, `tasks`, `external_references`).
2. Las filas `sync_logs` con `contact_id` apuntando al contacto se
   actualizan a `contact_id = NULL` para no romper integridad referencial.
3. En `audit_logs`, las filas con `actor_email = titular` se anonimizan:
   `actor_email` se reescribe a `[ERASED-{hash12}]`, donde el hash es
   `sha256(email)[0:12]`. Esto permite seguir auditando el historial sin
   conservar el dato personal.

**Antes de procesar** la supresión, verificar si existe **obligación
legal de conservación** (facturación, fiscal). En ese caso, **no
procesar**: marcar la solicitud como `rejected` y documentar la base
jurídica de la conservación en `notes`.

### Portabilidad (`portability`)

Genera **dos** ficheros con los mismos datos que `access`:

- JSON estructurado.
- CSV "ancho" denormalizado con columna `section` para que el titular o
  un tercero pueda importarlo en una hoja de cálculo.

### Oposición (`objection`)

Cambia el `marketing_consent` del contacto a `denied` y lo marca como
`is_active = false`. No borra datos: la oposición no obliga a la
supresión, solo a cesar el tratamiento que motiva la solicitud.

## Política de no-self-service

No existe un portal público para que el titular ejecute solicitudes
directamente. Las solicitudes llegan por canales formales (email,
formulario externo, papel) y se introducen manualmente por un
administrador tras verificar la identidad.

## Política de notificación al titular

El CRM **no envía automáticamente** notificación al titular. Las
respuestas a solicitudes RGPD deben enviarse fuera de banda usando las
plantillas en español de `docs/gdpr-templates/`.

## Política de retención de evidencias

Los ficheros generados en `gdpr_export_root` deben tratarse como
**evidencia legal**: copiar al almacén seguro (HiDrive cifrado mediante
restic) y conservar el tiempo mínimo requerido por la legislación local
(habitualmente 5 años). Una vez archivados, los ficheros locales pueden
borrarse del servidor.

## Configuración

| Variable | Por defecto | Descripción |
| --- | --- | --- |
| `GDPR_EXPORT_ROOT` | `var/gdpr_exports` | Directorio donde se escriben los ficheros JSON/CSV. |

El directorio se crea automáticamente. Asegurarse de que sea **legible
solo por el proceso del API** y no esté servido por Nginx.
