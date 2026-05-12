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

## Health checks

- El servicio `worker` no expone HTTP; `docker compose ps` muestra el
  estado del contenedor RQ. `rq info --url redis://redis:6379/0` lista
  las colas, workers activos y jobs encolados.
- El servicio `api` mantiene `/api/health`. La salud del worker no
  está expuesta como endpoint público para evitar information disclosure.
