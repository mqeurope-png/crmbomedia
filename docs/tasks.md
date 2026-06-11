# Tareas — productividad básica

Mini-PR C Fase 1. Las tareas son el primer artefacto productivo del CRM:
qué tengo que hacer, para qué contacto/empresa, cuándo, y con qué
prioridad. Se reusarán como base para el sync con Google Calendar y el
widget del dashboard rediseñado (fases siguientes).

## Modelo

Tabla `tasks` (migración `20260612_0027`, ALTER-in-place sobre la
versión Sprint A):

| Columna | Notas |
|---|---|
| `id` | UUID. |
| `title` | Obligatorio, máx. 255. |
| `description` | Texto libre, opcional. |
| `due_at` | Fecha+hora de vencimiento. NULL = sin fecha. |
| `status` | `pending` (default) → `in_progress` → `done` (o `cancelled`). El alias `open` queda para filas históricas de Sprint A. |
| `priority` | `low` / `medium` (default) / `high` / `urgent`. |
| `assigned_user_id` | NOT NULL. Quién la tiene que hacer. |
| `contact_id` | Opcional. Si está, todas las mutaciones generan `activity_event` en el timeline del contacto. |
| `company_id`, `pipeline_stage_id` | Opcional. Otros anclajes CRM. |
| `created_by_user_id` | NOT NULL. Quién la creó. |
| `google_event_id`, `google_calendar_id` | NULL hasta que el sync con Google Calendar las rellene (PR posterior). |
| `reminder_minutes_before` | Opcional, 0–10080. La notificación queda para Sprint E. |
| `completed_at` | Se rellena cuando `status` pasa a `done`. Cero overhead manual: lo aplica el repositorio. |
| `external_*` | Trio de provenance heredado de Sprint A; los importadores (AgileCRM hoy) deduplican por `(external_system, external_account_id, external_id)`. |

Tres índices calientes: `(assigned_user_id, due_at)`, `(status,)`,
`(due_at,)`.

## API

Montado en `/api/tasks` (`app/api/tasks.py`):

| Método + path | Quién | Para qué |
|---|---|---|
| `GET /api/tasks` | viewer+ | Lista con filtros (`assigned_user_id`, `contact_id`, `status`, `from`/`to`). |
| `GET /api/tasks/my-buckets` | viewer+ | Mis tareas abiertas agrupadas por urgencia (overdue / today / tomorrow / later / no_date). Cheap; usado por el sidebar badge y el widget del dashboard. |
| `GET /api/tasks/calendar?from=&to=` | viewer+ | Slice por rango de fechas para la vista calendario futura. |
| `GET /api/tasks/{id}` | viewer+ | Detalle. |
| `POST /api/tasks` | user+ | Crear. Si se omite `assigned_user_id`, el caller queda como asignado y creador. Si se pasa `contact_id` inválido → 400. |
| `PATCH /api/tasks/{id}` | user+ (asignado/creador/admin/manager) | Actualizar. Reasignación a otro usuario requiere admin/manager. Si cambia el status, `completed_at` se ajusta solo. |
| `POST /api/tasks/{id}/complete` | user+ (asignado/creador/admin/manager) | Atajo para status=done. |
| `DELETE /api/tasks/{id}` | user+ (asignado/creador/admin/manager) | Borra; emite `task.deleted` en el timeline del contacto si tenía. |
| `GET /api/contacts/{id}/tasks` | viewer+ | Tareas del contacto, drives el tab "Tareas" de la ficha. |

## Activity events

Cada mutación con `contact_id` no NULL emite una fila en
`activity_events` (sistema `crm`, account `tasks`):

- `task.created`
- `task.due_changed` (cambio de fecha)
- `task.assigned_changed`
- `task.completed`
- `task.reopened`
- `task.deleted`

El timeline de la ficha de contacto las pinta junto a emails y notas.

## UI

- **`/tasks`** — bucket grid (Vencidas / Hoy / Mañana / Más adelante /
  Sin fecha). Cada fila: complete (✓) y borrar (🗑). Modal de creación
  con título + due (datetime-local, defaults a mañana 09:00) + prioridad
  + recordatorio.
- **Sidebar** — entrada "Tareas" con badge rojo mostrando
  `overdue + today` (poll de `my-buckets` cada 90 s).
- **Ficha de contacto** — el card "Tareas pendientes" ahora es
  interactivo: lista las tareas del contacto vía
  `/api/contacts/{id}/tasks`, complete + delete inline, botón "Crear"
  que abre el TaskModal con el contacto pre-seleccionado.

## Próximos pasos

- **Google Calendar sync** (Fase siguiente): OAuth, cifrado de tokens,
  espejo de eventos al crear/modificar/borrar tarea, calendario
  configurable por usuario.
- **Dashboard rediseñado** (Fase siguiente): widget "Mis tareas
  pendientes" usando `/api/tasks/my-buckets` como aquí.
- **Vista calendario en /tasks** (no MVP): pintar las tareas en un
  calendario semanal/mensual; el endpoint `/api/tasks/calendar` ya
  está preparado.
- **Recordatorios y notificaciones** (Sprint E).
