"""Sprint Workflows Bloque 1 — motor de automatización.

Punto de entrada principal: `app.workflows.dispatcher.dispatch_event`.
Los endpoints existentes lo invocan con `event_type` + `contact_id` +
payload cuando ocurre algo relevante (POST /contacts, PATCH /contacts,
email events, etc.).

Arquitectura:

- `engine` — state machine que avanza un run de step en step.
- `scheduler` — heartbeat RQ que despierta runs con `wake_at <= now`.
- `dispatcher` — eventos entrantes → workflows matching → start runs.
- `triggers` — registro de tipos de trigger + filtros.
- `steps` — registro de tipos de step + handlers.
- `conditions` — evaluador del árbol JSON tipado (anti-injection).
- `variables` — interpolación Jinja2 sandboxed para plantillas.
"""
