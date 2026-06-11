# Dashboard

Mini-PR C Fase 3 redesign. The home page (`/`) now shows six
focused widgets in a responsive grid instead of the previous
placeholder card.

## Widgets

| Widget | Endpoint | Notes |
|---|---|---|
| Mis tareas pendientes | `GET /api/dashboard/tasks-pending?limit=8` | Reuses the productivity-layer task ordering: `due_at IS NULL` last, others ascending. |
| Próximos eventos GCal | `GET /api/dashboard/google-calendar-events?limit=5` | No-op when the user hasn't connected Google. Refresh-token revocation = graceful disconnect. |
| Mi pipeline | `GET /api/dashboard/pipeline-summary` | One bar list per active pipeline, contacts owned by the current user. |
| Leads sin atender | `GET /api/dashboard/unattended-leads?limit=10` | Contacts `commercial_status=new`, last 14 days, no owner OR no open task. Inline "Asignarme" button. |
| Estadísticas de leads | `GET /api/dashboard/leads-stats?range=30d&bucket=day` | Recharts bar chart + KPI strip. Range and bucket toggle. |
| Actividad email | `GET /api/dashboard/recent-email-activity?limit=15&scope=all` | Last 15 email-related activity_events. Scope toggle. |

Each widget owns its own fetch — a slow endpoint never blocks the
rest of the dashboard.

## Stack note

Adds `recharts` (~30 kB tree-shaken) for the stats widget chart.
No new server-side dependency. The aggregation is intentionally
Python-side (one query + in-memory bucket) so the same code works
on SQLite (CI) and MySQL (prod) without dialect-specific
`date_trunc`.
