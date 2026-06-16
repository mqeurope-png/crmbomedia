# SPEC — Sprint Reglas-Assign + Multi-asignación de comerciales

> **Estado: PROPUESTA / NO IMPLEMENTAR.** Documento de la fase de
> investigación. Bart lo revisa y aprueba (o ajusta) antes de abrir
> ningún PR. No se ha tocado código de producción ni añadido
> dependencias.
>
> Autor: Claude Code · Fecha: 2026-06-16 · Rama: `claude/spec-reglas-assign`

---

## 0. TL;DR ejecutivo

Hoy la asignación es **1-a-1 vía una sola columna** `contacts.owner_user_id`
(`String(36)`, **nullable, sin FK**). Solo se escribe en **un sitio**
(`bulk.py:144`, la bulk-action `assign_owner`, gated manager+). Los contactos
se crean **sin owner** (NULL) sin importar quién los cree, y `owner_user_id`
**ni siquiera está en el schema Pydantic de Contact** → la ficha y el botón
"Asignarme" del dashboard **no pueden asignar** hoy (el PATCH se descarta en
silencio). El motor de auto-asignación llena un hueco real.

**Cuatro hallazgos que condicionan el diseño:**

1. **`owner_user_id` está sobrecargado.** En `contacts` = asignación (lo que
   migramos). En `ContactView` / `Pipeline` / `Segment` = quién creó ese
   recurso (resource-ownership). **Los segundos NO se tocan** — son ~30
   referencias en `routes.py`/`segments.py`/`pipelines.py`/`contact_views.py`.
2. **3 widgets del dashboard asumen single-owner** con `Contact.owner_user_id
   == current_user.id` (filtro, no agregado): `pipeline_summary`,
   `unattended_leads`, `recent_email_activity`. Bajo M:N **sub-cuentan** (se
   pierden los contactos donde el user es secundario). Hay que cambiarlos a
   `EXISTS(assignment WHERE user_id = me)`.
3. **NO existe ningún reporte "por comercial"** hoy (grep de `GROUP BY owner`
   → cero). Así que no hay riesgo de doble-conteo en reportes existentes; el
   riesgo es que cualquier reporte NUEVO agregue sobre la join table, no
   `COUNT GROUP BY contacts.owner_user_id`.
4. **El motor de filtros ya es reutilizable** (`build_filter(tree)` +
   `evaluate_contact_against_rules(contact, tree)`), y los upserts de
   Brevo/Agile ya tienen el patrón de hooks `reconcile_*(session, *,
   contact_id, payload) -> int` en sus 3 ramas. El motor de reglas copia
   ambos patrones tal cual.

**Decisión arquitectónica central:** tabla `contact_assignments` (M:N
primary+secundarios) + `contacts.owner_user_id` se **mantiene como caché
desnormalizado del primary**, recalculado en código. Así los 3 widgets,
GDPR export, la búsqueda legacy y el FieldSpec siguen leyendo el primary sin
romperse, y solo migramos a `EXISTS` los sitios que deben contar secundarios.

---

## 1. Inventario del estado actual

> Leyenda: 🔴 asignación (migramos) · 🟢 resource-ownership (NO tocar).

| Pieza | Ubicación (file:line) | Comportamiento actual |
|---|---|---|
| **Modelo Contact** 🔴 | `app/models/crm.py:123` | `owner_user_id: Mapped[str \| None] = mapped_column(String(36))` — nullable, **sin FK**, sin default. |
| **Endpoint asignación (único)** 🔴 | `app/api/bulk.py:42,110-117,129-146` | bulk-action `assign_owner`. Gate route `require_user` (`bulk.py:63`) **pero** check en cuerpo limita a admin/manager (`bulk.py:110-117`). Set directo `c.owner_user_id = owner_id` (`:144`), overwrite (no añade). Audita como `CONTACT_UPDATED` (`:90`). |
| **Per-contact assign** 🔴 | — | **NO existe.** `owner_user_id` no está en `ContactCreate`/`ContactUpdate`/`ContactRead` (`schemas/crm.py:260-282, 290-312, 574-607`). El PATCH `update_contact` (`routes.py:1859-1891`) hace `setattr` loop pero Pydantic descarta owner antes. |
| **Contact create** 🔴 | `app/api/routes.py:1172-1214` | `Contact(**data)` sin owner; `current_user` solo se usa como audit actor (`:1208`). → contacto **unowned por defecto**. |
| **Filtro "asignados a mí" (legacy)** 🔴 | `app/api/routes.py:1467-1470` (`/contacts/search`), `:1539-1542` (`/search/ids`) | `Contact.owner_user_id == current_user.id`. Single-owner. |
| **Filtro "asignados a mí" (genérico)** 🔴 | `frontend/src/app/lib/contactsRules.ts:46-53` | `buildContactQuery` añade rule `{field:"owner_user_id", comparator:"eq", value:currentUserId}`. El endpoint `/api/entities/contact/search` no implementa `assigned_to_me`; se traduce en frontend. |
| **FieldSpec `owner_user_id`** 🔴 | `app/services/segments/fields.py:190-199` | `type="reference"`, `comparators=_REFERENCE` (eq/neq/in/not_in/is_null/is_not_null), `reference_table="users"`. Plain column compare (`relation=None`). `is_null` == "Sin asignar". |
| **UI ficha sección owner** 🔴 | `frontend/src/app/contacts/[id]/page.tsx`, `ContactProfessionalSection.tsx` | **El owner NUNCA se muestra ni edita en la ficha.** No hay sección "Propietario"/"Comercial". |
| **Bulk action assign UI** 🔴 | `frontend/src/app/components/ContactsBulkBar.tsx:44,72-74,98-107,136-159` | `canAssign = role admin\|manager`; "Asignar a…" → user picker (`is_active`) → `run("assign_owner",{owner_user_id})`. |
| **Lista: columna Propietario** 🔴 | `frontend/src/app/contacts/page.tsx:580-585` | `row.owner_user_id` → `userMap.get(id).full_name` o "—". `userMap` de `getUsers({limit:100})`. |
| **Toggle assignedToMe** 🔴 | `frontend/src/app/contacts/page.tsx:119,200,313-318,539-544,670-689` | state `assignedToMe`; auto-on para `role==="user"` (`:200`). |
| **Dashboard: pipeline_summary** 🔴⚠️ | `app/api/dashboard.py:158-171` (clause `:167`) | `Contact.owner_user_id == current_user.id` en el JOIN. **Sub-cuenta secundarios.** |
| **Dashboard: unattended_leads** 🔴⚠️ | `app/api/dashboard.py:213-216` (`:214`), row `:229` | `owner_user_id IS NULL` = "sin atender". Global (ignora current_user). Bajo M:N "sin asignar" = "0 filas en assignments". |
| **Dashboard: recent_email_activity** 🔴⚠️ | `app/api/dashboard.py:358` | `scope=mine` → `owner_user_id == current_user.id`. Sub-cuenta secundarios. |
| **Dashboard widget FE** 🔴 | `frontend/src/app/components/dashboard/UnattendedLeadsWidget.tsx:37-45`, `lib/dashboardApi.ts:39` | botón "Asignarme" → `updateContact(id,{owner_user_id})` → **descartado en silencio** (owner no en schema). |
| **GDPR export** 🔴 | `app/services/gdpr.py:81` | serializa `owner_user_id` en el export del titular. |
| **Audit catalog** 🔴 | `app/core/audit.py:59-61,123,58` | `CONTACT_CREATED/UPDATED/DEACTIVATED`, `CONTACT_TAGS_BULK_ACTION`, `COMPANY_BULK_ACTION`. No hay `CONTACT_ASSIGNED`. |
| **Count contactos (backfill)** 🔴 | `app/repositories/crm.py:337` | `select(func.count()).select_from(Contact)`. Tabla `contacts`, PK `String(36)` uuid. |
| **User model** | `app/models/crm.py:883-911` | `id String(36)` uuid (`:886`), `is_active` (`:895`), `role` enum. Sin back-rel a asignación. |
| **Company `owner_user_id` filtro** | — | `companies` **no tiene** `owner_user_id`. La asignación es solo de contactos. |
| **Filtro owner en company schema** | `app/services/entities/fields_company.py` | No expone owner (company no se asigna). |
| **Reportes/métricas por comercial** | — | **NINGUNO existe** (grep `GROUP BY owner` → 0 matches). |
| **Segments/saved views por owner** | — | Ninguno filtra por `contacts.owner_user_id` (NULL en todos hoy). |
| **`owner_user_id` resource-ownership** 🟢 | `ContactView` `crm.py:369`, `Pipeline` `:411`, `Segment`; `repositories/{segments,pipelines,contact_views}.py`; `integrations/brevo/segments.py:67-113` | **NO TOCAR.** Es "quién creó la vista/pipeline/segmento". |

### Mapa de archivos a tocar (solo asignación)

Backend: `models/crm.py` · `schemas/crm.py` · `api/routes.py` (search owner-filter)
· `api/bulk.py` · `api/dashboard.py` (3 widgets) · `services/segments/fields.py` +
`engine.py` (nuevo leaf) · `services/gdpr.py` · `core/audit.py` ·
`repositories/crm.py`.

Frontend: `lib/contactsRules.ts` · `contacts/page.tsx` · `components/ContactsBulkBar.tsx`
· `lib/bulkApi.ts` · `contacts/[id]/page.tsx` (+ nueva sección) ·
`components/dashboard/UnattendedLeadsWidget.tsx` + `lib/dashboardApi.ts` ·
`lib/api.ts` (Contact type / updateContact).

---

## 2. Roles del sistema (confirmado)

Jerarquía **numérica e inclusiva** — `app/core/auth.py:15-20` `ROLE_LEVELS`:
`VIEWER=0 < USER=1 < MANAGER=2 < ADMIN=3`. `require_role(min)` pasa si
`level[user] >= level[min]` (`auth.py:102-113`). **Admin pasa todos los gates
inferiores.** Fallo → `_audit_forbidden` (`ACCESS_FORBIDDEN`) + 403.

| Capacidad | Endpoint | Gate (file:line) | Rol mínimo efectivo |
|---|---|---|---|
| Crear/editar/borrar contacto | `POST/PATCH /api/contacts*` | `routes.py:1176,1864,1904` `require_manager` | **manager** |
| Asignar owner (bulk) | `POST /api/contacts/bulk-action` | route `require_user` `bulk.py:63` + check cuerpo `:110-117` | **manager** (user bloqueado en cuerpo) |
| Bulk acciones contacto | idem | `bulk.py:63` `require_user` (por-acción: deactivate→admin) | user / manager / admin |
| Crear/editar segmento | `POST/PATCH /api/segments` | `routes.py:3815,3870` `require_user` (+owner) | **user** |
| Crear/editar entity-view | `/api/entity-views/*` | `entity_views.py:152,192` `require_viewer` (+owner) | **viewer** |
| Listar usuarios | `GET /api/users` | `routes.py:708` `require_viewer` | **viewer** (mutaciones admin) |
| Configurar integraciones | `/api/integration-accounts` | read `require_manager`, write `require_admin` | manager/admin |
| Ver dashboards | `/api/dashboard/*` | `dashboard.py` `require_viewer` | viewer |
| Company bulk | `POST /api/companies/bulk-action` | `companies.py:405` `require_user` | user |

**Inconsistencias relevantes para este sprint:**

- **Asignar owner hoy es manager+**, pero el gate vive en el **cuerpo**
  (`bulk.py:110`), no en la ruta. Bart quiere "cualquier user reasigna
  manualmente" → hay que **bajar la asignación manual a `require_user`** (ver
  §7) y replicar/eliminar ese check de cuerpo. ⚠️ Esto **diverge del
  comportamiento actual** (hoy un user no puede asignar); es un cambio
  deliberado de política, documentarlo.
- Crear contacto es **manager** pero crear segmento es **user** — asimetría
  preexistente, no la tocamos.
- Crear reglas de auto-asignación afecta el book of business de todo el
  equipo → debe ser **manager+** (más restrictivo que asignar manual). Ver §7.

---

## 3. Modelo BD propuesto

### 3.1 Tablas

Migración `20260616_0047_contact_assignments_and_rules.py` (siguiente tras
`0046`). `contact_assignments` copia el patrón de `ContactTag`
(`crm.py:330-349`), que ya tiene `assigned_at` / `assigned_by_user_id` /
`source`.

```sql
CREATE TABLE contact_assignments (
  id VARCHAR(36) PRIMARY KEY,
  contact_id VARCHAR(36) NOT NULL,
  user_id VARCHAR(36) NOT NULL,
  is_primary BOOLEAN NOT NULL DEFAULT FALSE,   -- sa.false() para MySQL 8
  assigned_by_user_id VARCHAR(36),
  assigned_at DATETIME NOT NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'manual', -- manual|rule:<id>|backfill|brevo:auto|agile:auto
  rule_id VARCHAR(36),
  notes TEXT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  CONSTRAINT fk_ca_contact  FOREIGN KEY (contact_id)         REFERENCES contacts(id) ON DELETE CASCADE,
  CONSTRAINT fk_ca_user     FOREIGN KEY (user_id)            REFERENCES users(id)    ON DELETE CASCADE,
  CONSTRAINT fk_ca_assigner FOREIGN KEY (assigned_by_user_id) REFERENCES users(id)   ON DELETE SET NULL,
  CONSTRAINT fk_ca_rule     FOREIGN KEY (rule_id)            REFERENCES assignment_rules(id) ON DELETE SET NULL,
  UNIQUE KEY uniq_contact_user (contact_id, user_id),
  INDEX idx_ca_contact (contact_id, is_primary),
  INDEX idx_ca_user    (user_id, is_primary)
);

CREATE TABLE assignment_rules (
  id VARCHAR(36) PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  description TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,      -- sa.true()
  priority INT NOT NULL DEFAULT 100,            -- menor = mayor prioridad
  conditions_json TEXT NOT NULL,                -- árbol AND/OR/NOT (IR del motor) — TEXT no JSON (ver nota)
  primary_user_id VARCHAR(36),
  secondary_user_ids_json TEXT,                 -- array JSON de user_ids (TEXT)
  apply_to VARCHAR(20) NOT NULL DEFAULT 'unassigned_only', -- new_only|all_matching|unassigned_only
  override_existing BOOLEAN NOT NULL DEFAULT FALSE,
  stop_on_match BOOLEAN NOT NULL DEFAULT TRUE,  -- si matchea, no evaluar reglas de menor prioridad
  created_by_user_id VARCHAR(36) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  CONSTRAINT fk_ar_primary FOREIGN KEY (primary_user_id)    REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_ar_creator FOREIGN KEY (created_by_user_id) REFERENCES users(id),
  INDEX idx_ar_active_priority (is_active, priority)
);
```

**Notas de tipos (consistencia con el codebase):**
- `conditions_json` y `secondary_user_ids_json` → **`Text`**, no `JSON`
  nativo. Todo el codebase guarda árboles IR como TEXT
  (`segments.rules_json`, `contact_views.filters_json`,
  `companies.custom_fields_json`) y decodifica en la capa de repo. Mantener
  la convención evita un dialecto JSON divergente entre SQLite (tests) y
  MySQL 8 (prod).
- Booleans con `server_default=sa.false()` / `sa.true()` (patrón de las
  migraciones existentes para MySQL 8).
- `stop_on_match` añadido al schema original de Bart: necesario para que la
  prioridad tenga semántica de "primera regla que matchea gana" (ver §5.3).

### 3.2 Decisiones justificadas

**(a) `contacts.owner_user_id` se MANTIENE como caché desnormalizado del
primary.** Recomendado. Razones:
- Lo leen **6 sitios** que seguirían funcionando sin cambio (los 3 widgets en
  su forma "mi primary", GDPR export, búsqueda legacy, FieldSpec, columna de
  la lista).
- Query rápida "quién es el responsable" sin JOIN.
- Se recalcula **en el código** del endpoint/servicio que toca asignaciones
  (no trigger DB — más testeable, portable SQLite↔MySQL, y el codebase no usa
  triggers en ningún sitio). Helper `_recompute_primary_cache(session,
  contact_id)`: setea `owner_user_id` = el `user_id` del assignment con
  `is_primary=True`, o `NULL` si no hay primary.
- Los sitios que deben contar **secundarios** (assigned_to_me "cualquiera",
  los 3 widgets si queremos incluir watchers) migran a `EXISTS` sobre la join
  — decisión explícita por sitio, no automática.

**(b) Backfill de los ~19.968 contactos** (en realidad solo los que tienen
owner no-NULL; hoy son **pocos** porque casi ningún contacto está asignado).
Script idempotente paginado:
```sql
INSERT INTO contact_assignments (id, contact_id, user_id, is_primary, assigned_at, source, created_at, updated_at)
SELECT UUID(), id, owner_user_id, TRUE, NOW(), 'backfill', NOW(), NOW()
FROM contacts
WHERE owner_user_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM contact_assignments ca WHERE ca.contact_id = contacts.id AND ca.user_id = contacts.owner_user_id);
```
En la migración Alembic se hace en Python paginado (batches de 500) para no
bloquear la tabla, igual que los backfills previos del sprint anterior
(`scripts/backfill_contact_*`). Como casi nadie está asignado hoy, el backfill
real será trivial; el código pagina por seguridad.

**(c) Recompute del caché**: en código, no trigger. Cada endpoint que
inserta/borra/cambia primary llama `_recompute_primary_cache` dentro de la
misma transacción. Idéntico patrón al "1 primary por contacto" que
`contact_phones` ya enforce en app-layer (`/api/contacts/{id}/phones/{id}/primary`).

**(d) Máximo 1 primary por contacto**: **lógica de aplicación en transacción**,
no índice único parcial (MySQL 8 no los soporta de forma portable; SQLite
tampoco). `set_primary(contact_id, user_id)`:
1. `UPDATE contact_assignments SET is_primary=FALSE WHERE contact_id=:cid AND id != :target`
2. `UPDATE contact_assignments SET is_primary=TRUE WHERE id=:target`
3. `_recompute_primary_cache`
Mismo patrón que `_set_primary` de `contact_phones` (ya en prod).

**(e) Soft vs hard delete de assignments**: **hard delete** del row de
asignación, **pero** el historial vive en el **audit log** (`CONTACT_ASSIGNED`
/ `CONTACT_UNASSIGNED` con metadata `{user_id, by, source, rule_id}`). No
añadimos un `deleted_at` a la join (complicaría el UNIQUE y los EXISTS). Si en
el futuro se quiere "historial de asignaciones en la ficha", se lee del audit
log filtrado por `target_id=contact_id`.

---

## 4. Endpoints backend propuestos

```
# --- Asignaciones (manual) — require_user (Bart: cualquier user) ---
GET    /api/contacts/{cid}/assignments            → lista [{id,user_id,is_primary,source,assigned_by,assigned_at,notes}]
POST   /api/contacts/{cid}/assignments            → añadir user {user_id, is_primary?, notes?}
PATCH  /api/contacts/{cid}/assignments/{id}       → cambiar is_primary / notes
DELETE /api/contacts/{cid}/assignments/{id}       → desasignar
PUT    /api/contacts/{cid}/primary                → {user_id} set primary (transacción §3.2d)
POST   /api/contacts/bulk-assign                  → {contact_ids[], user_id, as_primary?, mode: add|replace}
POST   /api/contacts/bulk-unassign                → {contact_ids[], user_id}

# --- Reglas — require_manager (afectan a todo el equipo) ---
GET    /api/assignment-rules                      → lista (ordenada por priority asc)
POST   /api/assignment-rules                      → crear
GET    /api/assignment-rules/{id}
PUT    /api/assignment-rules/{id}
DELETE /api/assignment-rules/{id}
POST   /api/assignment-rules/{id}/preview         → {match_count, sample[]} SIN aplicar — require_user (read-only)
POST   /api/assignment-rules/{id}/run             → aplica a contactos que matcheen (según apply_to) — require_manager
POST   /api/assignment-rules/run-all              → aplica todas las activas en orden priority — require_manager
```

**Permisos (recomendación, ver §7):**
- **Asignación manual** (`/assignments*`, `/primary`, `/bulk-assign`,
  `/bulk-unassign`): **`require_user`**. Cumple "cualquier user reasigna".
  Cambia la política actual (hoy manager+). El gate va en la **ruta** (no en
  el cuerpo como hoy) — más limpio.
- **CRUD de reglas**: **`require_manager`**. Una regla reasigna contactos de
  otros comerciales → no es decisión de un user individual.
- **`preview`**: `require_user` (solo cuenta, no escribe).
- **`run` / `run-all`**: **`require_manager`** (mutación masiva).

**Reuso del bulk genérico**: `bulk-assign`/`bulk-unassign` pueden ser acciones
nuevas del `POST /api/contacts/bulk-action` existente (`add_assignment` /
`remove_assignment`) en vez de endpoints nuevos, para reusar el cap de 1000 +
audit. Recomiendo **endpoints dedicados** porque la semántica primary/secondary
+ mode add/replace no encaja en el dispatch plano actual; pero es decisión
menor.

---

## 5. Auto-asignación: cuándo dispara

### 5.1 Triggers — recomendación: (a) creación + (d) manual

| Opción | ¿Entra? | Razón |
|---|---|---|
| **(a) En creación de contacto** | ✅ **SÍ** | Manual (`create_contact`) + sync Brevo/Agile. Es el caso de uso principal: lead nuevo entra → se asigna solo. |
| **(b) En actualización** | ❌ NO | Riesgo de **loop de reasignación** (cambiar tags/origen re-dispara → cambia owner → audit → …). Complejidad alta, valor bajo. Si un contacto cambia de país, el comercial lo reasigna a mano. |
| **(c) Job periódico** | 🟡 OPCIONAL | Solo como mantenimiento (`apply_to=all_matching`) lanzado a mano o cron lento. NO en v1. |
| **(d) Manual "Aplicar regla ahora"** | ✅ **SÍ** | Botón en `/admin/assignment-rules` → `run` / `run-all`. Para aplicar una regla nueva al backlog existente. |

### 5.2 Integración con sync Brevo/Agile

El hook copia el patrón `reconcile_*` ya existente. **Solo dispara para
contactos nuevos/consolidados, no para cada fila de un re-sync completo:**

- **Brevo** (`upsert_brevo_contact`, `brevo/jobs.py:300-434`): nuevo
  `reconcile_assignment_rules(session, *, contact_id, payload) -> int` añadido
  tras los reconcilers existentes, **solo en la rama fresh-create** (`action
  == "created"`, jobs.py:405-434). La rama consolidate devuelve `"updated"` →
  no dispara (correcto: el contacto ya existía).
- **Agile** (`_upsert_contact_for_payload`, `agilecrm/jobs.py:132-254`):
  mismo helper, en la rama **fresh-create** (`"created"`, jobs.py:228-254) y
  opcionalmente en **consolidate-by-email** (`was_consolidated == True`,
  jobs.py:193-226) si Bart quiere que un lead recién vinculado también se
  evalúe. Recomiendo **solo fresh-create** en v1 (consistente con Brevo).
- El `stats` dict de Brevo (`jobs.py:312-323`) recibe un contador
  `rules_assigned` para observabilidad en el `SyncLog`.

### 5.3 Estrategia de evaluación — recomendación: SQL scoped, no in-memory

El motor tiene dos primitivas:
- `build_filter(tree)` → `WHERE` SQLAlchemy (resuelve TODO, incl. relaciones
  tags/origin/segment).
- `evaluate_contact_against_rules(contact, tree)` → bool en memoria, **pero
  solo resuelve columnas + concat**; las relaciones (tags, external_refs,
  pipelines) devuelven `None` → el leaf evalúa falsy en silencio
  (`engine.py:642-664`).

**Recomendación: para el hook de creación, usar SQL scoped a 1 fila**, NO el
evaluador en memoria:
```python
matched = session.scalar(
    select(Contact.id).where(Contact.id == new_cid, build_filter(rule.conditions))
) is not None
```
Razón: en el momento del hook el contacto ya está flushed con sus tags +
external_refs (los reconcilers de tags/channels corren antes), así que el SQL
los ve. Una sola fila → query trivial. Evita el agujero de relaciones del
evaluador en memoria. Las reglas suelen condicionar por origen/país/tags →
**todas necesitan relaciones**, así que el evaluador en memoria no sirve aquí.

Para `run` / `run-all` (aplicar a N contactos): `build_filter(rule.conditions)`
directo como WHERE de un `UPDATE`/`SELECT` masivo — set-based, sin enumerar.

**Orden de evaluación (`run-all` y hook de creación):** reglas activas
ordenadas por `priority ASC`. Para cada contacto, la primera regla que matchea
con `stop_on_match=True` gana y corta; si `stop_on_match=False`, sigue
acumulando secundarios de reglas posteriores. El `primary` lo fija la primera
regla matcheada con `primary_user_id` no nulo (respetando `override_existing`).

### 5.4 apply_to — semántica

- `unassigned_only` (**default**): solo contactos **sin ningún assignment**.
  El más seguro para `run-all` sobre el backlog (no pisa asignaciones
  manuales).
- `new_only`: solo en el trigger de creación (no aplica a `run` retroactivo).
- `all_matching`: todos los que matcheen, respetando `override_existing` para
  decidir si pisa el primary actual. Para limpiezas dirigidas.

---

## 6. UI propuesta

### 6.1 Ficha contacto — nueva sección "Comerciales asignados"

Componente nuevo `ContactAssignmentsSection.tsx` (patrón de
`ContactPhonesSection` / `ContactNotesSection` ya existentes). Entre "Estado"
y "Origen" en la sidebar de `contacts/[id]/page.tsx`.

```
┌─ Comerciales asignados ────────────────────────────┐
│ 👤 Bart Simpson (Primary) ⭐            [Cambiar]   │
│ 👤 Anna García (Watcher)                  [Quitar]  │
│ 👤 Marc Roig (Watcher)                    [Quitar]  │
│ [+ Añadir comercial ▾] (UserPicker)                 │
└─────────────────────────────────────────────────────┘
```
- "Cambiar" en el primary → UserPicker → `PUT /primary`.
- "Quitar" → `DELETE /assignments/{id}`.
- "+ Añadir" → UserPicker (reusa el de Sprint Filtros, server-side) → `POST
  /assignments {user_id, is_primary:false}`.
- Badge de origen: chip "regla: <nombre>" si `source` empieza por `rule:`.

### 6.2 Lista /contacts

- **Toggle "Asignados a mí"**: semántica nueva = **al menos un assignment es
  mi user_id** (primary O secundario). En `buildContactQuery` cambia de
  `{owner_user_id eq me}` a `{assigned_users contains_any [me]}` (campo nuevo,
  §7). Toggle adicional en el builder avanzado: "Solo donde soy Primary" →
  `{primary_user eq me}`.
- **Campos nuevos en el filter builder** (schema declarativo de Contact):
  - `assigned_users` (uuid-multi, relation→contact_assignments,
    reference_table=users): `contains_any` / `contains_all` / `contains_none`
    / `is_empty` (= "sin asignar").
  - `primary_user` (reference→users, lee el caché `owner_user_id`):
    `eq`/`neq`/`in`/`not_in`/`is_null`. Mantiene el FieldSpec actual
    renombrado de cara al usuario a "Comercial primary".

### 6.3 `/admin/assignment-rules` (nuevo)

Lista con priority + activa + match-count + acciones. Editor reusa
**`<EntityFilterBuilder>`** de Sprint Filtros (mismo schema declarativo de
Contact) para "Condiciones", + UserPicker primary + multi-select secundarios +
`apply_to` + `priority` + `override_existing` + `stop_on_match`.

```
┌─ Reglas de asignación automática ──────────────────────────────────┐
│ Prio │ Nombre                │ Condiciones      │ Asigna │ Match │ ✓ │
│ 10   │ Brevo Spain → Bart    │ origen=brevo:*   │ Bart   │ 1.204 │ ✅│
│      │                       │ ∧ país=España    │        │       │   │
│ 20   │ MBO Lasers → Anna     │ tag~mbo          │ Anna   │   312 │ ✅│
│ 100  │ Calientes → Marc      │ lead_score>80    │ Marc   │    47 │ ⏸ │
└────────────────────────────────────────────────────────────────────┘
[+ Nueva regla]   [▶ Aplicar todas ahora]
```
- "Match" = `POST /{id}/preview` (cuenta sin aplicar).
- "▶ Aplicar todas" → `run-all` con confirm + resultado (N asignados).
- Gate de la página: `require_manager` (en el frontend: ocultar si role <
  manager).

### 6.4 Bulk en /contacts

Acción nueva "Asignar comerciales" en `ContactsBulkBar` (o el futuro
`EntityBulkBar`): modal con UserPicker primary + multi-select secundarios +
radio "Reemplazar / Añadir" → `POST /bulk-assign {contact_ids, user_id,
as_primary, mode}`. `canAssign` baja de `admin|manager` a cualquier user
(§7).

---

## 7. Decisiones técnicas (con recomendación)

| # | Decisión | Recomendación |
|---|---|---|
| 1 | **¿`owner_user_id` caché o desaparece?** | **Caché del primary**, recalculado en código. 6 lectores siguen vivos; los que cuentan secundarios migran a EXISTS por sitio. |
| 2 | **¿1 primary respeta UNIQUE?** | El UNIQUE es `(contact_id, user_id)`, no sobre `is_primary`. "1 primary" se garantiza con **transacción app-layer** (clear+set), patrón ya en prod en `contact_phones`. |
| 3 | **¿Campo nuevo en el schema de Contact?** | **Sí, dos**: `assigned_users` (uuid-multi, relation, comparadores `contains_any/all/none` + `is_empty`) y `primary_user` (reference, lee caché). El leaf `assigned_users` copia `_compile_tag_leaf` (EXISTS sobre `contact_assignments`). El FieldSpec `owner_user_id` actual se mantiene como alias de `primary_user`. |
| 4 | **¿Reglas snapshot o always-fresh?** | **Always-fresh** al disparar (estado actual del contacto vía `build_filter`). **Audit guarda el snapshot** (`metadata={rule_id, matched_at, conditions_hash}`) para reproducibilidad. Sin tabla de snapshots. |
| 5 | **¿Auditoría por asignación?** | **Sí**: `CONTACT_ASSIGNED="contact.assigned"`, `CONTACT_UNASSIGNED="contact.unassigned"`, `ASSIGNMENT_RULE_APPLIED="assignment_rule.applied"` (convención `<entity>.<verb>` de `audit.py`). |
| 6 | **¿Notificación al comercial?** | **Diferir (Deuda).** NO existe sistema de notificaciones in-app hoy (solo `services/email.py` para envío). Recomendación futura: **badge in-app** ("N nuevos asignados"), NO email (un bulk de 100 = 100 emails = spam). Fuera de scope. |
| 7 | **¿Regla asigna a user inactivo/borrado?** | FK `primary_user_id ON DELETE SET NULL` (borrado). Para **inactivo**: al disparar, si `primary_user.is_active == False`, **saltar esa asignación** (log warning + skip), seguir a la siguiente regla por prioridad. La validación al **crear/editar** la regla rechaza users inactivos (400). |
| 8 | **¿assigned_to_me default?** | **"Cualquier assignment es mío"** (primary o secundario). Toggle extra "solo primary". |
| 9 | **¿Asignación manual: permiso?** | **`require_user`** (cualquier user, per Bart). Diverge del actual manager+. Documentar. |
| 10 | **¿Reglas: permiso?** | **`require_manager`** crear/editar/run; `require_user` preview. |

---

## 8. Plan de sub-PRs

### PR-A — Schema BD + backfill
- **Alcance:** modelos `ContactAssignment` + `AssignmentRule`; migración
  `0047` con backfill paginado desde `owner_user_id`. `owner_user_id` se
  mantiene (caché). Helper `_recompute_primary_cache`. NO API, NO UI.
- **Archivos (~5):** `models/crm.py`, migración alembic, `repositories/assignments.py`
  (CRUD + recompute + set_primary), `scripts/backfill_assignments_from_owner.py`,
  tests.
- **Migraciones:** sí (0047). **Tests:** modelo, backfill idempotente,
  set_primary transaction (1 primary garantizado), recompute cache.
- **Verificación:** backfill dry-run; `SELECT count(*) FROM contact_assignments`
  == contactos con owner no-NULL.
- **Estimación:** 2-3 h.

### PR-B — Endpoints asignación manual + adaptación filtros
- **Alcance:** `/api/contacts/{cid}/assignments*` CRUD + `/primary` +
  `/bulk-assign` + `/bulk-unassign` (`require_user`). Audit
  `CONTACT_ASSIGNED/UNASSIGNED`. Motor: leaf `assigned_users` + field
  `primary_user` en `fields.py`/`engine.py`. Adaptar los **3 widgets dashboard**
  a EXISTS. Adaptar `assigned_to_me` legacy + `buildContactQuery`. Schema
  Pydantic gana representación de asignaciones en `ContactDetailRead`.
- **NO entra:** reglas, UI ficha.
- **Archivos (~9):** `api/assignments.py` (nuevo), `api/bulk.py`,
  `api/dashboard.py`, `services/segments/{fields,engine}.py`, `schemas/crm.py`,
  `core/audit.py`, `lib/contactsRules.ts`, tests.
- **Tests:** CRUD, set_primary, bulk add/replace, EXISTS en dashboard
  (secundario cuenta), `assigned_users` leaf compila/matchea, audit rows.
- **Verificación:** asignar primary+secundario desde API; dashboard "mis
  pipelines" incluye contactos donde soy watcher.
- **Estimación:** 3-4 h.

### PR-C — Motor de reglas + integración sync
- **Alcance:** `AssignmentRule` CRUD (`require_manager`) + `/preview` + `/run`
  + `/run-all`. `services/assignment_rules.py` (evaluación vía `build_filter`,
  orden por priority, stop_on_match, override_existing, skip inactive).
  `reconcile_assignment_rules` hook en `upsert_brevo_contact` (fresh-create) +
  `_upsert_contact_for_payload` (fresh-create) + `create_contact` manual.
  Audit `ASSIGNMENT_RULE_APPLIED`. `stats` counter en sync.
- **NO entra:** UI admin.
- **Archivos (~8):** `api/assignment_rules.py`, `services/assignment_rules.py`,
  `integrations/brevo/jobs.py`, `integrations/agilecrm/jobs.py`,
  `api/routes.py` (create_contact hook), `core/audit.py`, tests (2 archivos).
- **Tests:** preview count, run sobre unassigned_only, prioridad+stop_on_match,
  override_existing, skip user inactivo, hook fire-on-create (no fire-on-resync),
  idempotencia del reconciler.
- **Verificación:** crear regla "Brevo Spain → Bart", `run`, contar asignados;
  sync Brevo nuevo lead ES → auto-asignado.
- **Estimación:** 3-4 h.

### PR-D — UI ficha + bulk
- **Alcance:** `ContactAssignmentsSection.tsx` en la ficha. Acción "Asignar
  comerciales" en `ContactsBulkBar` (modal primary+secundarios+mode). Toggle
  assigned_to_me nueva semántica + "solo primary". Columna lista usa caché.
  `lib/assignmentsApi.ts`.
- **Archivos (~6):** sección nueva, `contacts/[id]/page.tsx`, `ContactsBulkBar.tsx`,
  `contacts/page.tsx`, `lib/assignmentsApi.ts`, `lib/bulkApi.ts`.
- **Tests:** tsc/eslint/build (sin runner de componentes).
- **Verificación:** asignar/quitar desde ficha; bulk asignar 50 contactos.
- **Estimación:** 2-3 h.

### PR-E — UI /admin/assignment-rules
- **Alcance:** lista + editor reusando `<EntityFilterBuilder>` + UserPickers.
  `lib/assignmentRulesApi.ts`. Gate `require_manager` (ocultar en FE).
- **Archivos (~4):** `admin/assignment-rules/page.tsx`, editor component,
  `lib/assignmentRulesApi.ts`, nav link.
- **Verificación:** crear regla con condiciones AND/OR, preview, aplicar.
- **Estimación:** 3-4 h.

### PR-F — Tests E2E + docs + cierre
- **Alcance:** test e2e del flujo completo (crear regla → sync lead →
  auto-asignado → reasignar manual → dashboard refleja). Doc
  `docs/asignacion-comerciales.md`. Opcional: reporte "contactos por comercial"
  en dashboard (agregado sobre la join). Actualizar este spec con §cierre.
- **Estimación:** 2 h.

**Total:** ~15-20 h (Bart estimó 8-14; el +30% es por los 3 widgets dashboard
+ el motor de reglas + la doble representación caché/join).

---

## 9. Riesgos identificados

1. **Loop de reasignación si activamos trigger-on-update (opción b).** →
   Mitigado: NO la implementamos. Solo fire-on-create. El reconciler es
   idempotente (UNIQUE `(contact_id, user_id)` + dedupe), así que un re-sync
   que por error llegue a la rama create no duplica.
2. **Backfill timing.** Hoy casi nadie está asignado (owner NULL en la
   inmensa mayoría) → backfill trivial. Aun así paginar batches de 500 en la
   migración para no bloquear `contacts` 19.968 filas.
3. **Los 3 widgets dashboard asumen 1 owner.** Riesgo de sub-conteo de
   secundarios. Mitigado: PR-B los migra a EXISTS explícitamente. `unattended_leads`
   "sin asignar" pasa de `owner IS NULL` a `NOT EXISTS(assignment)`.
4. **No romper resource-ownership.** Los `owner_user_id` de View/Pipeline/Segment
   son otra cosa. Mitigado: el inventario §1 los marca 🟢; ningún PR los toca.
5. **`owner_user_id` no está en el schema Contact** → el botón "Asignarme" del
   dashboard ya está roto hoy (PATCH descartado). PR-B/D lo arreglan apuntando
   al endpoint de asignación nuevo, no al PATCH de contacto.
6. **Política de permisos diverge.** Bajar asignación manual a `require_user`
   es un cambio deliberado (hoy manager+). Riesgo: un viewer NO puede (correcto,
   `require_user` excluye viewer). Documentar en el PR.
7. **Reglas con user inactivo.** Mitigado: validación al crear (400) + skip al
   disparar (no rompe el sync).
8. **Notificaciones por email saturando.** → Diferido; si se implementa,
   batch digest, NO 1 email por asignación.
9. **Concurrencia en set_primary.** Dos requests simultáneos cambiando primary
   del mismo contacto → la transacción clear+set puede dejar 0 o 2 primaries
   en una race. Mitigado: el endpoint hace la transacción con un `SELECT … FOR
   UPDATE` sobre los assignments del contacto (o re-check post-commit). Bajo
   riesgo (raro que dos toquen el mismo contacto a la vez) pero documentado.

### Decisiones aprobadas por Bart (2026-06-16)

1. ✅ **Permiso de asignación manual**: `require_user` (cualquier user).
   Diverge del manager+ actual — cambio deliberado. Un viewer sigue sin poder
   (read-only). → PR-B gate en la ruta.
2. ✅ **Reglas solo fire-on-create + manual.** Verbatim: "SOLO CREACIÓN Y
   MANUAL, Y EN NUEVOS LEADS CUANDO ENTREN O SEAN CREADOS". → dispara en
   `create_contact` (manual) + rama fresh-create de los upserts Brevo/Agile
   (lead nuevo entra por sync) + botón "Aplicar ahora". **NO on-update.**
3. ✅ **apply_to default `unassigned_only`** para `run-all` — no pisa
   asignaciones manuales.
4. ✅ **Notificaciones diferidas** — fuera de scope. Futuro badge in-app, no
   email.

### Pendiente de decidir (no bloquea arranque; se resuelve dentro del PR)

- **Agile consolidate-by-email**: ¿dispara reglas o solo fresh-create?
  Recomendación en §5.2: **solo fresh-create** (consistente con Brevo). Como
  Bart dijo "nuevos leads cuando entren o sean creados", un lead consolidado
  por email NO es nuevo → confirma solo-fresh-create. Se cierra en PR-C.
- **Reporte "por comercial"**: ¿entra en PR-F o queda como deuda? Se decide al
  llegar a PR-F.
