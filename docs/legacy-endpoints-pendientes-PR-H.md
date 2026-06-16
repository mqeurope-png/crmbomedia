# Endpoints legacy pendientes de retirar en PR-H

Sprint Filtros & Listas — auditoría post-PR-E.

Tras la migración de `/contacts` al stack nuevo (PR-E), los siguientes
endpoints viejos del backend ya **no los usa el frontend de la lista
de contactos**, pero se mantienen vivos porque otras pantallas /
integraciones aún los consumen. Limpieza programada para **PR-H**
(último PR del sprint).

## Endpoints

### `POST /api/contacts/search` y `POST /api/contacts/search/ids`

- **Reemplazo genérico:** `POST /api/entities/contact/search` /
  `/search/ids` (envelope normalizado `{items, total, limit, offset}`,
  motor `build_entity_filter`, segment_resolver inyectado).
- **Consumidores legacy todavía activos:**
  - `app/integrations/brevo/sync_targets.py` — usa `build_filter`
    directamente, no via API. **NO bloquea la retirada del endpoint
    HTTP.**
  - Scripts de admin / herramientas internas pueden tener bookmarks.
    Verificar logs antes de retirar.
- **Diferencia funcional:** el legacy aceptaba `q` (free-text sobre
  name/email/phone) y `assigned_to_me` como params top-level. El
  genérico no — el frontend ahora traduce ambos a rules del motor en
  `lib/contactsRules.ts::buildContactQuery`.

### `GET/POST/PATCH/DELETE /api/contact-views/*`

- **Reemplazo:** `/api/entity-views/contact` (mismo backing table
  `contact_views` con `entity_type='contact'` por defecto desde la
  migración 0046).
- **Consumidores legacy todavía activos:**
  - `POST /api/contact-views/{view_id}/save-as-segment` — usado por
    la pantalla **NUEVA** de `/contacts` también (PR-E). Comparte
    tabla con entity_views, así que opera sobre cualquier view_id
    contact-typed. Cuando se retire, hay que mover esta acción a
    `/api/entity-views/contact/{id}/save-as-segment`.
  - `POST /api/contact-views/{view_id}/push-to-brevo-list` — idem.
- **Plan PR-H:** mover las dos acciones bridge (save-as-segment +
  push-to-brevo-list) al namespace `/api/entity-views/contact/...`,
  luego retirar todos los endpoints CRUD legacy.

### `POST /api/contacts/bulk-action`

- **Sigue vivo** y lo usa la pantalla nueva (`<ContactsBulkBar>`).
- Bart explícitamente diferió el bulk **set-based** (`UPDATE … WHERE
  build_entity_filter(tree)` en una sentencia, sin enumerar ids) a
  un PR posterior. Cuando llegue, este endpoint queda obsoleto.
- **Plan:** dejar como está hasta el PR de bulk set-based.

### `POST /api/contacts/bulk-tag`

- Usado por `/admin/tags`. **No tocar** — fuera de scope del sprint
  de filtros.

## Limpieza del frontend (ya hecha en PR-E)

- `BulkAction` TS union: removidos `add_tag` / `remove_tag` (botones
  muertos en `<ContactsBulkBar>` desde Sprint A). Los tag bulk ops
  reales se hacen vía `/admin/tags` → `POST /api/contacts/bulk-tag`,
  no por `<ContactsBulkBar>`.

## Componentes legacy pendientes de borrar en PR-H

Tras PR-E, los siguientes componentes ya no tienen consumidores
activos:

- `frontend/src/app/components/ContactFiltersBuilder.tsx`
- `frontend/src/app/components/ContactsBulkBar.tsx` — **EXCEPCIÓN:**
  PR-E aún lo monta porque el bulk set-based queda para otro PR.
  Retirar cuando el bulk se migre.
- `frontend/src/app/components/ContactViewsTabs.tsx`
- `frontend/src/app/components/ColumnConfigurator.tsx`

Mantener vivos hasta PR-H. Sandbox `/sandbox/entity-table` permanente
como herramienta de debug.
