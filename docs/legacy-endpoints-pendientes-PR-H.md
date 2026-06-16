# Endpoints legacy pendientes — estado post Sprint Filtros & Listas

> **Estado:** cierre de sprint en PR-H. Este doc refleja el estado
> **real** de los endpoints legacy tras la limpieza, no el plan inicial.

## Frontend ya migrado

Tras PR-H:

- ❌ Borrados (frontend, código muerto):
  - `lib/api.ts::searchContacts` + `searchContactIds` + types `ContactSearchPayload` / `ContactSearchIdsResult` / `ContactListPage` (este último se mantiene porque `getContacts` lo usa para el dashboard widget).
  - `lib/api.ts::listSavedViews`, `createSavedView`, `updateSavedView`, `deleteSavedView`, `duplicateSavedView`, `setDefaultSavedView` y los types `SavedView*`.
  - Componentes: `ContactFiltersBuilder.tsx`, `ContactViewsTabs.tsx`, `ColumnConfigurator.tsx`, `ContactViewEditorModal.tsx`.
  - Libs: `contactColumns.ts`, `contactColumnsStorage.ts`, `contactRulesMigration.ts`, `contactsUrlState.ts`.
  - Sandbox: `/sandbox/entity-table/` directory.

- ✅ Mantenidos vivos (siguen usándose):
  - `lib/api.ts::saveViewAsSegment` + `pushViewToBrevoList` — la pantalla nueva `/contacts` las usa para los flujos "Guardar como segmento" y "Enviar a lista Brevo". Los endpoints backend correspondientes están en `/api/contact-views/{id}/save-as-segment` y `/push-to-brevo-list` y comparten tabla con `entity_views`.
  - `<ContactsBulkBar>` — la pantalla nueva `/contacts` lo monta. Cuando llegue el bulk set-based, se generaliza a `<EntityBulkBar>` y este se retira.

## Backend legacy aún vivo

Los siguientes endpoints **no los usa ninguna pantalla del frontend** tras PR-H, pero **siguen vivos** porque tienen tests asociados (~29 referencias en `tests/test_api.py`, `tests/test_bulk_contacts.py`, `tests/test_contact_views.py`, `tests/test_entity_views_and_search.py`). Migrar esos tests al stack nuevo es trabajo aparte (Deuda menor).

| Endpoint | Reemplazo nuevo | Estado |
|---|---|---|
| `POST /api/contacts/search` | `POST /api/entities/contact/search` | Inactivo en frontend; vivo en tests. |
| `POST /api/contacts/search/ids` | `POST /api/entities/contact/search/ids` | Inactivo en frontend; vivo en tests. |
| `GET/POST/PATCH/DELETE /api/contact-views/*` | `/api/entity-views/contact/*` | CRUD inactivo en frontend; vivo en tests. |
| `POST /api/contact-views/{id}/save-as-segment` | (mismo) | **ACTIVO** en frontend (`/contacts`). |
| `POST /api/contact-views/{id}/push-to-brevo-list` | (mismo) | **ACTIVO** en frontend (`/contacts`). |
| `POST /api/contacts/bulk-action` | (sin reemplazo aún) | **ACTIVO** en frontend (`/contacts`). Migrar cuando aparezca bulk set-based. |
| `POST /api/contacts/bulk-tag` | (sin reemplazo) | **ACTIVO** en `/admin/tags`. Out of scope. |

## Plan de retirada futuro (Deuda menor, fuera del sprint)

1. Migrar `tests/test_api.py` + `tests/test_bulk_contacts.py` + `tests/test_contact_views.py` para apuntar al stack nuevo `/api/entities/contact/*` + `/api/entity-views/contact/*`.
2. Mover `save-as-segment` + `push-to-brevo-list` al namespace `/api/entity-views/contact/{id}/…` (cambia la URL pero la pantalla solo necesita un find-replace).
3. Borrar las funciones legacy de `routes.py` (≈ 400 líneas de los endpoints `/api/contact-views/*` + `/api/contacts/search*`).
4. Borrar el repositorio `app/repositories/contact_views.py` y sus schemas.

Estimación: 1 PR aparte, ~3-4h. Programado cuando alguien tenga ganas (no bloquea ningún flujo).
