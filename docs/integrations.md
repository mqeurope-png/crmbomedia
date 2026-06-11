# Integraciones — modelo multi-cuenta

Este documento describe el **modelo conceptual** del módulo de integraciones
del CRM tras el refactor multi-cuenta (PR `refactor(integrations): multi-account
support per system`, migración `20260515_0007`). Para detalles de **cifrado**
de las API keys ver `docs/security.md` § "almacén cifrado de API keys".

## Modelo: 1 sistema, N cuentas

El CRM se integra con cuatro sistemas externos (definidos en
`ExternalSystem`):

- **AgileCRM** — soporta N cuentas (una por mercado/marca/website).
- **Brevo** — típicamente 1 cuenta.
- **Freshdesk** — soporta N cuentas (una por equipo: soporte, ventas...).
- **FactuSOL** — típicamente 1 cuenta.

Cada cuenta es una fila en la tabla `integration_accounts`. La pareja
`(system, account_id)` identifica de forma única la cuenta dentro del
sistema y aparece en las URLs (`/api/integration-accounts/{system}/{account_id}`)
y en la metadata de cada evento de auditoría (`gdpr.* / integration_account.*`).

> El concepto de **"marca / tenant"** (agrupar varias cuentas en un mismo
> espacio lógico, p. ej. "marca Acme España" con su AgileCRM ES + Brevo
> + Freshdesk-soporte) **no está implementado todavía**. Cuando se diseñe
> añadirá una tabla `tenants` y un FK `integration_accounts.tenant_id`
> nullable hacia atrás, sin romper este modelo.

## Esquema

| Columna | Tipo | Notas |
|---|---|---|
| `id` | `VARCHAR(36)` PK | UUID; identidad surrogate. |
| `system` | `VARCHAR(32)` | `agilecrm` / `brevo` / `freshdesk` / `factusol`. |
| `account_id` | `VARCHAR(64)` | Slug elegido por el operador. Solo `[a-z0-9_-]`, sin separador a la cabeza o cola. |
| `display_name` | `VARCHAR(255)` | Nombre humano: "AgileCRM España". |
| `enabled` | `BOOLEAN` | Si el conector debe procesar esta cuenta. |
| `mode` | `sandbox` / `live` | Modo de operación. |
| `status` | `not_configured` / `configured` / `paused` | Estado de configuración. |
| `api_base_url` | `VARCHAR(255)?` | Endpoint base del proveedor. |
| `account_label` | `VARCHAR(255)?` | Anotación libre ("Producción ES"). |
| `credential_status` | `VARCHAR(80)` | `not_configured`, `configured`, `verified`, `error`. |
| `notes` | `TEXT?` | Notas internas. |
| `api_key_encrypted` | `TEXT?` | Ciphertext Fernet (ver `docs/security.md`). |
| `api_key_set_at` | `DATETIME?` | Cuándo se guardó la API key actual. |
| `api_key_last_used_at` | `DATETIME?` | Bumped por `get_decrypted_api_key()`. |
| `quota_max_contacts` | `INT?` | Límite de contactos a mantener (solo AgileCRM por ahora). |
| `quota_strategy` | `keep_newest` / `keep_oldest` / `none` | Política cuando se alcanza la cuota (nullable). |
| `sync_priority` | `INT` (default 100) | Orden en sincronizaciones masivas. Menor = más prioritario. |

**Restricción de unicidad:** `(system, account_id)`. La columna `system`
ya **no** es UNIQUE por sí sola, lo que era el bloqueo conceptual del modelo
anterior de "una fila por sistema".

## Endpoints

Todos bajo `/api/integration-accounts` (los antiguos
`/api/integration-settings/*` ahora devuelven **HTTP 410 Gone** apuntando
al nuevo prefijo).

| Método | Ruta | Rol | Descripción |
|---|---|---|---|
| `GET` | `/api/integration-accounts` | `manager+` | Listado con `system`, `enabled`, paginación. Cabecera `X-Total-Count`. |
| `GET` | `/api/integration-accounts/{system}/{account_id}` | `manager+` | Detalle. |
| `POST` | `/api/integration-accounts/{system}` | `admin` | Crear cuenta. Body: `account_id` (slug), `display_name`, `mode`, `api_base_url?`, `account_label?`, `quota_max_contacts?`, `quota_strategy?`, `sync_priority?`, `notes?`. |
| `PATCH` | `/api/integration-accounts/{system}/{account_id}` | `admin` | Editar campos. **No** permite mutar `system` ni `account_id`. |
| `DELETE` | `/api/integration-accounts/{system}/{account_id}` | `admin` | Borrar. Si hay `external_references` para el sistema, devuelve **409**; pasar `?force=true` para borrar de todos modos. |
| `PUT` | `/api/integration-accounts/{system}/{account_id}/api-key` | `admin` | Guardar API key cifrada. |
| `DELETE` | `/api/integration-accounts/{system}/{account_id}/api-key` | `admin` | Borrar API key. |

## Añadir cuentas desde la UI

1. Loguéate como `admin`.
2. Ve a **Administración → Integraciones**.
3. Cada sistema tiene su propia sección con el número de cuentas activas
   y un botón **"+ Añadir cuenta"**.
4. Rellena al menos `account_id` (slug) y `display_name`. Para AgileCRM
   también aparecen los campos de cuota (`quota_max_contacts` y
   `quota_strategy`).
5. Tras crear, expande la cuenta y guarda la **API key** (cifrada con la
   `INTEGRATION_SECRETS_KEY`; el plaintext nunca se vuelve a mostrar).

## Convenciones de naming para `account_id`

El `account_id` aparece en URLs, scripts y metadata de auditoría — debe
ser corto, legible y estable. Reglas:

- **Carácter set:** `[a-z0-9_-]+`, sin separador a la cabeza o cola.
- **Longitud:** 1-64 caracteres.
- **Patrón recomendado:** `<sistema>-<discriminante>`.
  - AgileCRM: `agilecrm-es`, `agilecrm-uk`, `agilecrm-fr`, `agilecrm-acme`.
  - Freshdesk: `freshdesk-soporte`, `freshdesk-ventas`.
  - Brevo y FactuSOL: `default` (heredado de la migración) o `produccion`.

No reutilizar un `account_id` borrado para una cuenta distinta: el
audit log tiene historial de eventos con ese par `(system, account_id)`
y leer eventos cruzados confunde la investigación.

## Cuotas (AgileCRM)

AgileCRM cobra por número de contactos almacenados, por lo que cada
cuenta tiene típicamente un techo (p. ej. 1000 contactos en el plan
"Starter", 50 000 en "Regular"). El CRM modela esto con dos columnas:

- `quota_max_contacts` (`INT?`): umbral declarado por el operador.
- `quota_strategy` (`enum?`): qué hacer cuando el conector lo alcanza.
  - `keep_newest`: borrar los más antiguos del lado de AgileCRM para
    abrir hueco. Útil si AgileCRM es el "warm storage" pero la fuente de
    verdad es el CRM.
  - `keep_oldest`: rechazar el push del contacto nuevo. Útil si AgileCRM
    es la fuente de verdad histórica y no queremos perderla.
  - `none`: loggear un warning y dejar pasar (cuota informativa).

> **Estado:** las columnas se persisten y validan en la API; el
> **conector real de AgileCRM** que aplique la política llegará en un PR
> aparte. Mientras tanto, las cuotas sirven como documentación operativa.

## Migración desde el modelo single-account

La migración `20260515_0007` renombra `integration_settings` a
`integration_accounts` y stamp-a `account_id='default'` en cada fila
existente. **No requiere intervención del operador**:

- Las API keys cifradas siguen siendo válidas (la `INTEGRATION_SECRETS_KEY`
  no cambia).
- Los conectores que llaman a `get_decrypted_api_key(system)` siguen
  funcionando porque el segundo argumento `account_id` por defecto es
  `"default"`.
- El namespace legacy `/api/integration-settings/*` devuelve **410 Gone**
  con un mensaje que apunta al nuevo prefijo, por lo que cualquier
  cliente externo o script fallará ruidosamente en lugar de silenciosamente.

## Helper para conectores

```python
from app.integrations.credentials import get_decrypted_api_key
from app.models.crm import ExternalSystem

# Single-account install (transparente tras la migración):
api_key = get_decrypted_api_key(ExternalSystem.BREVO)

# Multi-account: pasa el account_id explícitamente.
key_es = get_decrypted_api_key(ExternalSystem.AGILECRM, "agilecrm-es")
key_uk = get_decrypted_api_key(ExternalSystem.AGILECRM, "agilecrm-uk")
```

El helper actualiza `api_key_last_used_at` como efecto secundario, lo
que permite ver en la UI cuándo se usó cada cuenta por última vez.

## Auditoría

Cada acción se registra en `audit_logs` con `target_type='integration_account'`
y metadata `{system, account_id, ...}`. Eventos disponibles:

- `integration_account.created`
- `integration_account.updated`
- `integration_account.deleted`
- `integration_account.api_key_set`
- `integration_account.api_key_deleted`

Para filtrar todo el tráfico de integraciones:

```bash
curl -G $API/api/audit-logs \
  --data-urlencode "action_prefix=integration_account." \
  -H "Authorization: Bearer $TOKEN"
```

## Orígenes múltiples por contacto + fechas reales

Un contacto puede vivir en varios sistemas a la vez (típico: AgileCRM
**y** Brevo, consolidados por email). El CRM lo modela así:

- **Fuente de verdad de los orígenes**: la tabla `external_references`,
  con una fila por `(system, account_id, external_id)`. Un contacto en
  dos sistemas tiene dos filas. Los mappers de cada conector las
  pueblan siempre en el upsert.
- **`contacts.origin` (legacy)**: string del **primer** sistema que
  importó el contacto. **No se sobrescribe** en syncs posteriores
  (antes el último sync ganaba, dando la falsa impresión de un único
  origen). Se mantiene por compatibilidad; la UI consume
  `external_references`.

La ficha de contacto (`/contacts/[id]`) y la lista (`/contacts`)
muestran **todos** los orígenes como chips. El endpoint los expone así:

- `GET /api/contacts/{id}` → `external_refs[]` enriquecido con
  `system_label` ("AgileCRM"/"Brevo"), `account_label` (el
  `display_name` de la `integration_account`) y `external_url` (deep
  link al registro en el sistema de origen, cuando se puede construir:
  Brevo siempre; AgileCRM si la cuenta tiene `api_base_url` con el
  subdominio del tenant).
- `GET /api/contacts` → cada item lleva `external_references_summary`,
  un array compacto `[{system, account_id}]` para pintar los chips por
  fila sin inflar la respuesta.

### Fechas reales del sistema de origen

Dos columnas en `contacts` guardan la fecha real en el sistema fuente,
NO la fecha de sincronización al CRM:

- `created_at_external` — la creación **más antigua** entre todos los
  sistemas (el sistema más viejo es el origen real del contacto).
- `updated_at_external` — la modificación **más reciente**.

Los mappers las extraen del payload (`created_time`/`updated_time` en
AgileCRM, `createdAt`/`modifiedAt` en Brevo). Si el payload no las
trae, quedan `NULL` — nunca se inventa una fecha. La política de
merge (más antigua para creación, más reciente para modificación) vive
en `app/integrations/contact_merge.py`; ver
`integrations-architecture.md` § "Merge de campos multi-sistema".

La migración `0026` rellena estas columnas en contactos ya importados
agregando las fechas que ya viven en `external_references`
(`MIN(external_created_at)`, `MAX(external_updated_at)`).

> Para rellenar contactos importados ANTES de este cambio con la mejor
> precisión, relanza un sync de cada cuenta (botón "Sincronizar ahora"
> en `/admin/integrations`): el upsert es idempotente y completa las
> fechas que estuvieran a NULL.

## Roadmap

- **Verificación de conexión** (`POST /api/integration-accounts/{system}/{account_id}/test`):
  pendiente; cada conector necesita una implementación específica.
- **Marca / tenant**: agrupar cuentas; pendiente para iteración posterior.
- **Rotación selectiva de API keys** (con `kid`): solo necesario si se
  prevé rotación frecuente; ver `docs/security.md` § rotación.
