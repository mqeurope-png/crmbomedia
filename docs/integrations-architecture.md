# Integraciones — arquitectura

Este documento describe la **infraestructura compartida** que usan los
conectores externos del CRM (AgileCRM, Brevo, Freshdesk, FactuSOL).
Cada conector concreto vive en su propio PR (Sprint A PR-2 = AgileCRM,
PR-3 = Brevo, etc.); este documento explica el "pasillo común" sobre
el que se construyen.

Documentación relacionada:

- `docs/integrations.md`: modelo conceptual multi-cuenta
  (`integration_accounts`) y las URLs CRUD.
- `docs/security.md`: cifrado en reposo de las API keys
  (`integration_accounts.api_key_encrypted` con Fernet).

## Visión general

```
┌──────────────┐        ┌──────────────────┐        ┌────────────┐
│   FastAPI    │───────▶│   Redis (RQ)     │───────▶│  Worker    │
│              │ encolar│                  │ pop    │ run_sync_job│
└──────────────┘        └──────────────────┘        └─────┬──────┘
        │                                                  │
        │ POST /sync         /webhooks                     │  HTTP cliente
        │                                                  ▼
        ▼                                          ┌────────────────┐
   ┌──────────┐                                    │   API externo   │
   │ sync_logs│◀───────── audit log + métricas ────│  (AgileCRM,...) │
   └──────────┘                                    └────────────────┘
```

Tres componentes:

1. **Cliente HTTP base** (`app/integrations/http_client.py`):
   `IntegrationHTTPClient` — wrapper async sobre `httpx.AsyncClient` que
   carga las credenciales de la cuenta, aplica retries con backoff
   exponencial, respeta `Retry-After` en 429 y tiene un catálogo de
   excepciones tipadas (`IntegrationAuthError`,
   `IntegrationRateLimitError`, `IntegrationClientError`,
   `IntegrationServerError`, `IntegrationNetworkError`).
2. **Worker async** (`app/workers/`): RQ (Redis Queue) ejecutándose en
   un contenedor Docker que comparte imagen con `api`. Una cola por
   pareja `(system, operation)` para que un operador pueda pausar
   AgileCRM sin parar Brevo.
3. **`sync_logs`** como tabla de verdad: cada job (manual / cron /
   webhook) tiene una fila con el ciclo de vida completo (PENDING →
   RUNNING → SUCCESS / PARTIAL_SUCCESS / FAILED), contadores y un
   `metadata` JSON.

## Cliente HTTP base

### Uso desde un conector

```python
from sqlalchemy.orm import Session
from app.db.session import get_engine
from app.integrations.http_client import IntegrationHTTPClient

async def fetch_contacts() -> list[dict]:
    with Session(get_engine()) as session:
        async with IntegrationHTTPClient(session, "agilecrm", "agilecrm-es") as client:
            resp = await client.get("/api/v1/contacts", params={"page_size": 100})
            return resp.json["objects"]
```

El cliente:

- Lee la fila `integration_accounts` con `(system='agilecrm', account_id='agilecrm-es')`.
- Desencripta la API key Fernet con la clave maestra del proceso.
- Pone la cabecera `Authorization: Bearer <key>` (cambiar `auth_scheme`
  / `auth_header` si el proveedor usa otro formato).
- Audita cada llamada (`integration.api_call`) con
  `{system, account_id, method, url_path, status_code, duration_ms}`.
- Bumpea `integration_accounts.api_key_last_used_at` tras cada llamada
  exitosa.

### Política de errores

| Caso                | Acción                                                                                  |
| ------------------- | --------------------------------------------------------------------------------------- |
| `2xx`               | Retorno normal vía `IntegrationResponse` (status_code, json, text, headers, raw httpx). |
| `401 / 403`         | Marca `credential_status='error'`, audita `integration.auth_failed`, lanza `IntegrationAuthError` (no reintenta). |
| `429`               | Respeta `Retry-After` (cap 60s), reintenta. Tras agotar reintentos → `IntegrationRateLimitError`. |
| Otros `4xx`         | `IntegrationClientError` sin reintento.                                                  |
| `5xx`               | Reintenta con backoff exponencial. Tras agotar → `IntegrationServerError`.               |
| Network / timeout   | Reintenta. Tras agotar → `IntegrationNetworkError`.                                      |

Variables de entorno opcionales:

- `INTEGRATION_HTTP_TIMEOUT_SECONDS` (default 30).
- `INTEGRATION_HTTP_MAX_RETRIES` (default 3).

## Worker async

### RQ por encima de Redis

Usamos [RQ](https://python-rq.org/) en vez de Celery por simplicidad:
una sola dependencia (`rq`), Redis ya está en el stack, y los jobs son
funciones Python normales.

Nombres de cola: `{system}:{operation}`. Ejemplos:

- `agilecrm:sync_contacts`
- `agilecrm:purge_quota`
- `brevo:push_contact`
- `freshdesk:sync_tickets`
- `factusol:sync_invoices`

Una sola lista de colas se declara en `docker-compose.prod.yml` (servicio
`worker`). Para añadir una cola nueva, ampliar esa lista + redeploy del
worker (`docker compose -f docker-compose.prod.yml up -d worker`).

### Registrar un operation handler

Cada conector hace, en su `__init__.py` o equivalente:

```python
from app.workers.jobs import OPERATIONS, SyncOutcome
from sqlalchemy.orm import Session
from app.models.crm import SyncLog

def sync_contacts_handler(session: Session, sync_log: SyncLog) -> SyncOutcome:
    # Recoger account_id desde sync_log.account_id, ir a AgileCRM,
    # actualizar contactos, devolver counters.
    return SyncOutcome(
        records_processed=120,
        records_skipped=3,
        records_failed=0,
        metadata={"page_count": 4},
    )

OPERATIONS["agilecrm:sync_contacts"] = sync_contacts_handler
```

El handler:

- Recibe la sesión SQLAlchemy que el worker abrió.
- Recibe la `SyncLog` row ya en estado `RUNNING` (no hay que tocarla;
  los counters se rellenan via el `SyncOutcome` que se devuelve).
- Devuelve un `SyncOutcome`; el worker lo persiste y emite el evento
  de auditoría apropiado (`integration.sync_succeeded` /
  `_partial` / `_failed`) en función del resultado.
- Cualquier excepción no controlada queda capturada y aparece en
  `sync_log.error_summary` con stack trace truncado.

### Encolar desde código

```python
from app.workers.jobs import enqueue_sync_job
from app.models.crm import SyncTrigger

sync_log_id, job_id = enqueue_sync_job(
    session,
    system="agilecrm",
    account_id="agilecrm-es",
    operation="sync_contacts",
    triggered_by=SyncTrigger.CRON,
)
```

La API ya hace esto al recibir `POST /api/integration-accounts/{system}/{account_id}/sync`.

### Política de reintentos

RQ tiene su propio sistema de reintentos. El handler no debe asumir
"exactly-once" — debe ser idempotente. Las llamadas HTTP ya tienen
retries en el cliente. Si el handler falla todas las veces, queda en
la cola fallida de RQ; un operador puede reencolar desde `rq` CLI o
borrarla. El `sync_log` queda con `status='failed'` y `error_summary`
con detalle.

## Webhook genérico

### Endpoint

```
POST /api/webhooks/{system}/{account_id}
```

Cualquier sistema externo puede entregar payloads aquí. Hoy (Sprint A):

- Verifica que `(system, account_id)` existe en `integration_accounts`.
- Lee el body raw (cap 64 KiB).
- Lo persiste en `sync_logs` con `operation='webhook_received'`,
  `triggered_by='webhook'`, `status='success'`.
- Audita `integration.webhook_received` con `payload_size_bytes` (sin
  el body completo).

En Sprint A **no validamos firma**. Cada conector añadirá su verificador
específico (Brevo: HMAC; Freshdesk: signature header) en su PR.

### Patrón para el verificador

```python
# Ejemplo: cada conector añade su propia función decorada que valida
# antes de continuar.
def verify_brevo_hmac(request: Request, account: IntegrationAccount) -> None:
    signature = request.headers.get("X-Brevo-Signature")
    secret = decrypt(account.webhook_secret_encrypted)
    expected = hmac.new(secret.encode(), await request.body(), "sha256").hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Bad signature")
```

## sync_logs como source of truth

Esquema (ver migración `20260517_0009_enrich_sync_logs.py`):

| Columna                | Tipo                | Notas |
| ---------------------- | ------------------- | --- |
| `id`                   | `VARCHAR(36)` PK    | UUID. |
| `system`               | enum                | `agilecrm` / `brevo` / `freshdesk` / `factusol`. |
| `account_id`           | `VARCHAR(64)?`      | Espejo de `integration_accounts.account_id`. |
| `operation`            | `VARCHAR(120)?`     | `sync_contacts`, `webhook_received`, etc. |
| `status`               | `VARCHAR(40)`       | `pending` → `running` → `success` / `partial_success` / `failed`. |
| `started_at` / `finished_at` | `DATETIME?`   | Llenos por el worker. |
| `records_processed`    | `INT`               | OK. |
| `records_skipped`      | `INT`               | E.g. duplicados ignorados. |
| `records_failed`       | `INT`               | Si `> 0` y `processed > 0` → `partial_success`. |
| `error_summary`        | `TEXT?`             | Stack trace truncado o mensaje del error. |
| `triggered_by`         | `VARCHAR(32)?`      | `manual` / `cron` / `webhook`. |
| `triggered_by_user_id` | FK users `?`        | Quién lo lanzó manualmente. |
| `job_id`               | `VARCHAR(64)?`      | RQ job id (para `rq info`). |
| `metadata`             | `TEXT?` (JSON)      | Payload del webhook o detalles operativos. |
| `direction` / `message` | columnas legacy    | Quedan nullables; el código nuevo usa `operation` + `metadata`. |
| `contact_id`           | FK contacts `?`     | Sólo para syncs que apuntan a un contacto concreto. |

### Endpoints HTTP

| Método | Ruta                                                            | Rol      | Descripción |
| ------ | --------------------------------------------------------------- | -------- | --- |
| POST   | `/api/integration-accounts/{system}/{account_id}/sync`          | admin    | Encola job. Body: `{operation, payload?}`. 409 si la operación no está registrada. |
| GET    | `/api/integration-accounts/{system}/{account_id}/sync-logs`     | manager+ | Listado paginado con filtros `status`/`operation`/`from`/`to` + `X-Total-Count`. |
| GET    | `/api/integration-accounts/{system}/{account_id}/sync-logs/{id}` | manager+ | Detalle. |

### Audit log

Toda acción de runtime emite `integration.*` en `audit_logs`:

- `integration.api_call` — cliente HTTP llamó a un sistema externo.
- `integration.auth_failed` — 401/403 con la API key actual.
- `integration.sync_triggered` — alguien encoló un job.
- `integration.sync_started` / `_succeeded` / `_partial` / `_failed` —
  ciclo de vida del job.
- `integration.webhook_received` — entró un payload externo.

Toda la metadata respeta la regla: **nunca** secretos en claro.

## Patrón ejemplo (PR-2 AgileCRM)

Pseudo-código mostrando cómo encajará el conector AgileCRM:

```python
# app/integrations/agilecrm/__init__.py
from app.workers.jobs import OPERATIONS, SyncOutcome
from app.integrations.http_client import IntegrationHTTPClient

async def _fetch_all_contacts(session, account_id: str):
    async with IntegrationHTTPClient(session, "agilecrm", account_id) as client:
        page = await client.get("/api/v1/contacts")
        ...

def sync_contacts(session, sync_log) -> SyncOutcome:
    import asyncio
    contacts = asyncio.run(_fetch_all_contacts(session, sync_log.account_id))
    upserted = 0
    skipped = 0
    for c in contacts:
        # ... aplicar reglas de consent, upsert a `contacts`...
        upserted += 1
    return SyncOutcome(records_processed=upserted, records_skipped=skipped)

OPERATIONS["agilecrm:sync_contacts"] = sync_contacts
OPERATIONS["agilecrm:purge_quota"] = purge_quota_handler  # otro handler
```

Tras añadir esto y redeployar:

1. El frontend ya lista la operación porque `SYSTEM_OPERATIONS.agilecrm`
   pasará a `['sync_contacts']` (el PR-2 lo actualiza).
2. El botón "Sincronizar ahora" se habilita.
3. El admin pulsa → `POST /api/integration-accounts/agilecrm/agilecrm-es/sync`
   → `sync_log_id` + `job_id` → el panel polea cada 5s y muestra el progreso.
4. Cada `integration_account.api_call` queda en `audit_logs`.

## Conector AgileCRM (referencia de implementación)

El Sprint A PR-2 implementa el primer conector real (AgileCRM) sobre
la infraestructura común. El código sirve como **plantilla de
referencia** para los conectores siguientes (Brevo, Freshdesk,
FactuSOL).

### Layout

- `app/integrations/agilecrm/client.py` — `AgileCRMClient`, subclase
  de `IntegrationHTTPClient` que conoce los endpoints `/dev/api/contacts`,
  `/dev/api/contacts/{id}` y `/dev/api/contacts/count`.
- `app/integrations/agilecrm/mapper.py` — `map_agilecrm_contact_to_internal`
  que traduce un payload AgileCRM (con su array `properties: [{name, value}]`)
  a un dict plano listo para `Contact(**record)`.
- `app/integrations/agilecrm/jobs.py` — los dos handlers
  (`sync_agilecrm_contacts`, `purge_agilecrm_quota`) registrados en
  `OPERATIONS["agilecrm:sync_contacts"]` y `OPERATIONS["agilecrm:purge_quota"]`.
- `app/integrations/agilecrm/__init__.py` — import side-effect que
  carga `jobs` (y por tanto registra las operaciones) cuando
  `app.workers` se inicializa.

### Credencial

AgileCRM usa **HTTP Basic** con `<email>:<api_key>` como user:password.

- El **email** se guarda en `integration_accounts.auth_identifier`
  (columna nueva, plaintext — no es secreto). En la UI aparece como
  campo "Email de login de AgileCRM" en el modal de creación / edición
  de la cuenta, con validación que lo marca obligatorio.
- La **API key** se guarda en `integration_accounts.api_key_encrypted`
  (cifrada con Fernet, igual que todas las demás claves).

El `AgileCRMClient` compone `Authorization: Basic base64(email:api_key)`
en construcción y lo añade automáticamente a cada llamada.

### Accept: application/json

AgileCRM responde en **XML** por defecto. El `AgileCRMClient` fuerza
`Accept: application/json` (y `Content-Type: application/json` para
POST/PUT) en los headers default del `httpx.AsyncClient` para que cada
llamada pida JSON sin necesidad de pasarlo en cada `request()`.

### Compatibilidad hacia atrás (legacy `email:api_key`)

Versiones anteriores guardaban ambos en el campo cifrado, separados por
`:`. El cliente sigue aceptando esa forma:

- Si `auth_identifier` está vacío Y el campo cifrado contiene `:`, el
  cliente parte por el primer `:`, emite `DeprecationWarning` y sigue
  funcionando. El operador verá el warning en `docker compose logs -f
  worker`; al re-guardar la cuenta desde la UI moderna, el email migra
  a `auth_identifier` y la advertencia desaparece.
- Si ni hay `auth_identifier` ni `:` en el campo cifrado, el cliente
  lanza `IntegrationAuthError` con un mensaje claro pidiendo que se
  configure ambos en `/admin/integrations`.

### Idempotencia y dedup multi-cuenta

`sync_agilecrm_contacts` decide qué hacer en este orden:

1. ¿Existe ya un `external_references (system, account_id, external_id)`
   para esta cuenta? → **update** del `contacts` enlazado.
2. ¿No existe la ref pero ya hay un `contacts` con ese **email**? →
   **consolidación**: se añade un segundo `external_references` apuntando
   al contact existente. Un único contacto puede aparecer en N cuentas
   AgileCRM sin duplicarse.
3. En caso contrario → **insert** del contact + insert del
   external_reference.

El RGPD: `marketing_consent` siempre se importa como `unknown`. AgileCRM
no garantiza una base jurídica equivalente; el flujo dedicado de RGPD
es quien actualiza el consentimiento explícito.

### Cuotas

AgileCRM cobra por contactos almacenados. Cuando una cuenta tiene
`quota_max_contacts` set + `quota_strategy ∈ {keep_newest, keep_oldest}`,
`sync_contacts` encola automáticamente al final de la importación una
ejecución de `purge_quota` (cola `agilecrm:purge_quota`). El job:

- Llama a `/dev/api/contacts/count` para saber cuántos hay.
- Si `count <= quota_max_contacts`, no hace nada.
- Si `count > quota`, listar `to_delete = count - quota` contactos en
  el orden correspondiente (`keep_newest` → `created_time` ASC,
  `keep_oldest` → `-created_time`) y borrarlos **en el remoto** vía
  `DELETE /dev/api/contacts/{id}`.
- Cada borrado emite `integration.quota_deleted` con
  `{system, account_id, external_id, reason='quota', strategy}`.
- En `external_references` la fila se conserva (no se borra) con
  `external_status='deleted_in_origin'`.

**Nunca se borra del CRM local.** Solo se borra de AgileCRM y se marca
la fila de auditoría.

### Endpoints de la API

- `POST /api/integration-accounts/agilecrm/{account_id}/sync` con
  `{"operation": "sync_contacts"}` o `{"operation": "purge_quota"}`.
- `POST /api/integration-accounts/agilecrm/{account_id}/sync/sync_contacts`
  (variante path-based, sin body) — equivalente para automatizaciones
  o el botón "Sincronizar ahora" de la UI.
- `POST /api/integration-accounts/agilecrm/{account_id}/sync/purge_quota`
  — para el botón "Purgar cuota ahora".

### Notas operativas

- **Rate limits**: AgileCRM Free tier ≈ 200 req/h por API key. El base
  `IntegrationHTTPClient` ya respeta `Retry-After` en 429 — un workflow
  saturado se enlentece pero el job no se rompe; la importación es
  idempotente.
- **`company_name`**: el mapper expone el nombre de empresa como hint
  pero NO resuelve `company_id` automáticamente. El operador puede
  asociar empresas desde la UI a posteriori.
- **Errores por contacto**: hasta 100 errores se acumulan en
  `error_summary` de la `sync_log` row; el resto se trunca.
- **Cap por sync**: `MAX_CONTACTS_PER_SYNC = 50_000` por seguridad.
  Las cuentas con más contactos completan la importación en
  ejecuciones sucesivas (la idempotencia garantiza progreso).

## Patrón para los siguientes conectores

Para Brevo / Freshdesk / FactuSOL repetir el layout:

1. `app/integrations/<system>/client.py` — subclase de
   `IntegrationHTTPClient` con los endpoints del proveedor.
2. `app/integrations/<system>/mapper.py` — traducción payload → CRM.
3. `app/integrations/<system>/jobs.py` — handlers + registro en
   `OPERATIONS["<system>:<operation>"]`.
4. `app/integrations/<system>/__init__.py` — `from . import jobs`.
5. En `app/workers/__init__.py`, añadir `from app.integrations import <system>`.
6. Frontend: añadir `<system>: ['<operation>', ...]` a `SYSTEM_OPERATIONS`.

Las colas RQ ya están declaradas en `docker-compose.prod.yml` para los
cuatro sistemas previstos.

## Debugging external API calls

Cuando un conector falla con `IntegrationServerError` o `500 from
<system>/<account>` y un `curl` directo al mismo endpoint con las
mismas credenciales devuelve 200, suele tratarse de una diferencia
sutil en headers / URL / cuerpo. El cliente base soporta un modo de
logging detallado, desactivado por defecto, controlado por la variable
de entorno `INTEGRATION_HTTP_DEBUG`.

### Activación

```bash
# .env.production (o pasado al contenedor por compose)
INTEGRATION_HTTP_DEBUG=true
```

Acepta `1` / `true` / `yes` / `on`. Cualquier otro valor (incluyendo
ausente) lo deja apagado.

Tras editarlo basta con reiniciar el contenedor `api` (para llamadas
desde endpoints síncronos) y `worker` (para jobs de sync) — los logs
se imprimen vía el logger estándar `logging`, así que se ven con
`docker compose logs -f api worker`.

### Qué se loggea

- **INFO `integration.http.request`** por cada llamada:
  - `method` (GET / POST / ...)
  - `url` completa (host + path + query string como httpx la enviará).
  - `headers` finales, **con `Authorization`, `X-Api-Key`, `Apikey` y
    `X-Auth-Token` enmascarados** (`Basic abcdefghijkl...wxyz`); strings
    < 20 caracteres se redactan completos como `***`.
- **ERROR `integration.http.response_error`** por cada respuesta
  `>= 400`:
  - `status` numérico.
  - `headers` de respuesta (mismo enmascarado que la request).
  - `body` truncado a los primeros 2000 caracteres del `response.text`.

### Qué NO se loggea

- El cuerpo de la request (los conectores actuales solo hacen GETs;
  cuando lleguen POST/PUT habrá que decidir si añadir el body o
  filtrar antes de logear PII).
- El API key sin enmascarar, **nunca**. La función `_mask_secret`
  vive en `app/integrations/http_client.py` y se aplica tanto a
  request como a response headers.

### Recordatorio

`INTEGRATION_HTTP_DEBUG` es para diagnóstico puntual. Dejarlo activo
en producción estable inunda los logs y disipa el `audit_logs` real.
Apagarlo en cuanto el bug esté entendido.

## Health checks

- El servicio `worker` no expone HTTP; `docker compose ps` muestra el
  estado del contenedor RQ. `rq info --url redis://redis:6379/0` lista
  las colas, workers activos y jobs encolados.
- El servicio `api` mantiene `/api/health`. La salud del worker no
  está expuesta como endpoint público para evitar information disclosure.
