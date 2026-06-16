# Reglas de asignación + multi-comercial

> Estado: **producción** tras PR-F (sprint Reglas-Assign cerrado 2026-06-16).
> Sub-PRs A→F + hotfixes Ca / Da / Db / Ea mergeados a `main`.

Doc operativa para administradores, managers y comerciales del CRM.
Cubre el modelo de datos, los triggers del motor, los permisos y las
operaciones cotidianas (asignación manual, gestión de reglas, manual
runs).

---

## 1. Modelo: Primary + Secundarios

Cada contacto tiene **una asignación primary** (el responsable
comercial) y **cero o más secundarios** (watchers — leen, comentan,
participan pero no son el dueño). La fuente de verdad es la tabla
`contact_assignments`; la columna `contacts.owner_user_id` se mantiene
como **caché desnormalizado** del primary para queries baratas.

```
┌──────────────────┐     1   ┌────────────────────────┐   N    ┌──────┐
│ contacts         │ ────────│ contact_assignments    │────────│ users│
│  · owner_user_id │ (cache) │  · user_id             │        └──────┘
└──────────────────┘         │  · is_primary          │
                             │  · source              │
                             │  · rule_id (nullable)  │
                             │  · assigned_by_user_id │
                             │  · assigned_at         │
                             │  · notes               │
                             └────────────────────────┘
```

Invariantes (forzados en app-logic vía `app/repositories/assignments.py`):

1. **Máximo 1 primary por contacto**. `set_primary` baja al anterior
   antes de subir al nuevo, en transacción única.
2. **`contacts.owner_user_id` siempre refleja el primary** (o `NULL` si
   no hay primary). Se recalcula via `recompute_primary_cache(session,
   contact_id)` después de cada mutación del set.
3. **UNIQUE `(contact_id, user_id)`** a nivel DB — `add_assignment` es
   idempotente sobre esa pareja.

### Procedencia (`source`)

Toda fila guarda **de dónde viene** la asignación:

| `source` literal              | Significado                                 |
|-------------------------------|---------------------------------------------|
| `manual`                      | Operador la creó desde la ficha o bulk      |
| `rule:<uuid>`                 | Aplicada por una regla — `rule_id` apunta a la regla |
| `backfill`                    | Migración 0047 desde `owner_user_id` legacy |
| `brevo:auto`, `agile:auto`    | Reservado para integraciones futuras        |

---

## 2. Reglas: cuándo se disparan, cuándo no

El motor (`app/services/assignment_rules.py:evaluate_for_contact`) **se
ejecuta solo en estos 3 puntos**:

| Trigger                                    | Cuándo                                       |
|--------------------------------------------|----------------------------------------------|
| **Manual: `POST /api/contacts`**           | Al crear un contacto desde la UI o API       |
| **Brevo sync, rama `created`**             | Al importar un contacto nuevo desde Brevo    |
| **Agile sync, rama `created`**             | Al importar un contacto nuevo desde AgileCRM |
| **Manual run: `POST /api/assignment-rules/{id}/run`** | El admin/manager lo lanza desde la UI |

### NO se dispara nunca en:

- **Update** de un contacto (PATCH `/api/contacts/{id}`) — incluso si
  ese update cambiase el `address_country` y matchease una regla
  nueva. La razón es **prevenir loops**: una regla que escribe en
  `contact_assignments` ya cambia el contacto; activarse on-update lo
  pondría en bucle. Si quieres re-asignar tras un update, usa el
  manual run.
- **Bulk action** masivo (`/api/contacts/bulk-action assign_owner`) —
  ese es asignación directa, no pasa por el motor.
- **Brevo / Agile sync rama `updated`** — solo `created`.

### Resumen ejecutivo

> **"Fire on create, manual run otherwise."**

---

## 3. Permisos (decisión §1 del spec)

| Acción                                                   | Rol mínimo |
|----------------------------------------------------------|------------|
| **Asignación manual** (ficha contacto / bulk owner)       | `user`     |
| Auto-asignarse desde la lista                            | `user`     |
| Promover / quitar / añadir secundarios                   | `user`     |
| **Crear/editar/borrar reglas**                           | `manager`  |
| Lanzar Preview o Run de una regla                        | `manager`  |
| Toggle activa de una regla                               | `manager`  |
| Ver reglas (read-only)                                   | `user`     |

`viewer` no puede mutar ninguna asignación ni ver reglas; sí puede ver
los widgets del dashboard con sus propias asignaciones.

---

## 4. Cuando el primary queda inactivo

Si una regla activa apunta a un `primary_user_id` que **deja de ser
`is_active=True`** (admin lo desactiva, abandona la empresa, etc.), el
motor reacciona así:

1. La próxima vez que `evaluate_for_contact` o `run_rule_over_universe`
   ven esa regla, comprueban `_is_user_active(primary)`.
2. Si está inactivo → la regla se **auto-desactiva** (`rule.is_active =
   False`) y queda registrada en `RuleEvalResult.auto_disabled[]`.
3. Se emite audit `assignment_rule.auto_disabled` con `reason=
   primary_user_inactive`.
4. La UI `/admin/assignment-rules` pinta esa fila con un badge **rojo
   "Inactiva"** + tooltip "La regla apuntaba a un usuario inactivo o el
   operador la desactivó. Edita el primary para reactivar."

El admin re-asigna el primary y vuelve a activar la regla manualmente.

**Note:** las `contact_assignments` ya escritas por esa regla mantienen
su `rule_id`; no se borran al desactivar la regla.

---

## 5. Prioridad y `stop_on_match`

Las reglas se evalúan ordenadas **`priority ASC, created_at ASC`**:
**menor priority = mayor prioridad**. Por defecto `priority = 100`.

Si una regla matchea con `stop_on_match=True` (default), el motor
**corta la cadena** — las reglas de menor prioridad no se evalúan para
ese contacto. Útil para escribir reglas "específicas → generales":

| Priority | Regla                                       | stop_on_match |
|----------|---------------------------------------------|---------------|
| 10       | "VIP Cataluña → Manel"                      | true          |
| 50       | "ES sin asignar → Norma"                    | true          |
| 100      | "Resto sin asignar → Round robin Eduard"    | true          |

Un lead VIP catalán cae en la 10 y para. Un lead ES no-VIP cae en la
50 y para. El resto cae en la 100.

Si `stop_on_match=False`, la regla aplica pero el motor sigue
evaluando — útil para asignar **secundarios cumulativos** (regla 1
añade primary, regla 2 añade un watcher).

---

## 6. `apply_to`: alcance de cada regla

| Valor               | Descripción                                                  |
|---------------------|--------------------------------------------------------------|
| `unassigned_only`   | Default. Solo aplica si el contacto **no tiene asignaciones**. Útil para "round robin de leads sin dueño". |
| `new_only`          | Solo aplica a contactos creados **después de la creación de la regla**. Útil cuando introduces una regla nueva sin querer reasignar la cartera existente. |
| `all_matching`      | Aplica a cualquier match — equivalente a "force". Combinado con `override_existing=True`, reasigna incluso a contactos ya con primary. |
| `all`               | Alias backwards-compat de `all_matching`.                    |

### `override_existing`

- `False` (default): la regla no toca contactos con asignaciones
  previas, ni siquiera con `apply_to=all_matching` (la regla matchea
  pero no escribe).
- `True`: la regla **promociona su primary** al contacto, **degrada al
  primary anterior** a secundario y mantiene los secundarios manuales.

> En `apply_to=new_only` el `override_existing` rara vez tiene sentido
> (nuevos leads no suelen tener asignaciones previas). El backend lo
> respeta por consistencia.

---

## 7. Flujo end-to-end de un lead nuevo

```
                  ┌─────────────────────────┐
                  │ Lead nuevo via:         │
                  │  · POST /api/contacts   │
                  │  · Brevo sync (created) │
                  │  · Agile sync (created) │
                  └────────────┬────────────┘
                               │
                               ▼
              ┌────────────────────────────────────┐
              │ evaluate_for_contact(session, c)   │
              │                                    │
              │  for rule in active rules:         │
              │    if primary inactive:            │
              │       auto-disable + skip          │
              │    if apply_to filter blocks:      │
              │       skip                         │
              │    if conditions NOT match:        │
              │       skip                         │
              │    --- apply ---                   │
              │    add_assignment(primary)         │
              │    add_assignment(secundarios)     │
              │    recompute_primary_cache         │
              │    audit assignment_rule.applied   │
              │    if stop_on_match:               │
              │       break                        │
              └────────────────┬───────────────────┘
                               │
                               ▼
              ┌────────────────────────────────────┐
              │ Persistencia en MISMA transacción: │
              │  · contact_assignments rows        │
              │  · contacts.owner_user_id cache    │
              │  · audit_logs (created + applied)  │
              │                                    │
              │  session.commit() en el caller     │
              └────────────────────────────────────┘
```

El motor **no commitea** — el caller (route handler, sync job) es dueño
de la transacción. Si el commit falla, se rollback completo: ni
contacto ni assignments ni audit quedan a medias.

---

## 8. Operaciones cotidianas

### Asignar manualmente desde la ficha

UI: `/contacts/<id>` → sección **"Comerciales asignados"**.

- **"Asignarme"** — si no estás en la lista, te añade. Si no hay primary,
  entras como primary; si ya hay, entras como secundario.
- **"Asignar a otro"** — picker de usuarios con búsqueda server-side
  (300ms debounce); siempre añade como secundario.
- **⭐ Estrella** — promociona la fila a primary (degrada al anterior).
- **🗑 Trash** — quita la fila. Si era primary, el primer secundario
  pasa a primary; si no hay nadie, el contacto queda sin owner.

### Asignar en masa (bulk)

UI: `/contacts` → selecciona N contactos (sin límite hasta 50000) →
bulk-bar **"Asignar comercial"**.

Visible para `user`+. El backend chunkea internamente en lotes de 500
con commit por chunk para no atascar una transacción gigante.

### Configurar una regla desde la UI

UI: `/admin/assignment-rules` → **"Nueva regla"** (visible para
`manager`+).

1. Rellena nombre, prioridad, primary, secundarios opcionales.
2. Construye **conditions** con `<EntityFilterBuilder entity="contact">`
   (mismo whitelist de campos que el filtro de `/contacts`).
3. Selecciona `apply_to` y los toggles.
4. **Preview** → llama a `/api/assignment-rules/preview` (regla NO
   guardada todavía) y muestra "X contactos matchean ahora mismo".
5. **Guardar**.

Para aplicar a la cartera existente: botón **"Run"** en la lista →
confirma → motor itera todos los contactos que matchean según
`apply_to` y aplica.

### Lanzar una regla via curl

```bash
# Preview de regla guardada
curl -X POST https://crm.bomedia.net/api/assignment-rules/<rule_id>/dry-run \
     -H "Authorization: Bearer $TOKEN"

# Aplicar la regla en producción
curl -X POST https://crm.bomedia.net/api/assignment-rules/<rule_id>/run \
     -H "Authorization: Bearer $TOKEN"

# Preview de regla SIN GUARDAR
curl -X POST https://crm.bomedia.net/api/assignment-rules/preview \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Test",
       "conditions": {"operator": "AND", "children": [
         {"type": "rule", "field": "address_country", "comparator": "eq",
          "value": "ES"}
       ]},
       "primary_user_id": "<user_uuid>",
       "secondary_user_ids": [],
       "priority": 100,
       "apply_to": "unassigned_only",
       "override_existing": false,
       "stop_on_match": true
     }'
```

---

## 9. Audit log

Cada operación deja rastro en `audit_logs`:

| Action                              | Cuándo se emite                                  |
|-------------------------------------|--------------------------------------------------|
| `contact.assignment_added`          | Añadir secundario o primary desde ficha / bulk  |
| `contact.assignment_removed`        | Borrar asignación desde ficha                   |
| `contact.primary_changed`           | Promover otro a primary o borrar el actual      |
| `assignment_rule.created/updated/deleted` | CRUD de reglas desde `/admin/assignment-rules` |
| `assignment_rule.applied`           | Regla aplicada a un contacto concreto — `target_id=contact_id`, metadata `{rule_id, rule_name, primary_user_id, source}` |
| `assignment_rule.run`               | Manual run (`POST /run`)                        |
| `assignment_rule.auto_disabled`     | Auto-desactivación por primary inactivo         |

> Para reconstruir **"¿por qué este contacto acabó asignado a X?"**:
> filtra `audit_logs WHERE target_id=<contact_id> AND action LIKE 'assignment%'`
> ordenado por `created_at`.

---

## 10. Deudas y limitaciones conocidas

### Deuda #5 — Webhooks Agile real-time

Hoy el motor dispara via:
- POST manual (instantáneo).
- Brevo sync periódico (`BREVO_SYNC_INTERVAL_HOURS`, default 12h).
- Agile sync periódico (`AGILECRM_SYNC_INTERVAL_HOURS`, default 12h —
  scheduler añadido en PR-Db).

Para latencia <1min al recibir un lead Agile haría falta wire-up de
webhooks Agile + endpoint receiver. **Sprint propio, ~6-10h CC.**

### Notificaciones a comerciales

Decisión §4 del spec: **diferidas** a cuando exista la infra de
in-app notifications. Hoy un comercial descubre sus nuevos contactos
por el widget del dashboard o el toggle "Solo asignados a mí" en
`/contacts`.

### apply_to=`new_only` y leads viejos pre-regla

Como `new_only` se basa en `Contact.created_at >= rule.created_at`,
si necesitas aplicar la regla a leads **anteriores** a la creación de
la regla, usa `apply_to=all_matching` + un Run manual + opcionalmente
`override_existing=True` si quieres reasignar incluso a contactos con
primary.

### Round-robin "de verdad"

El motor actual NO hace round-robin (1 primary fijo por regla). Para
"distribuir cada N leads a un comercial distinto" habría que extender
el modelo con un `next_user_index` por regla. **No planificado.**

---

## 11. Estado al cierre del sprint (réplica §8 del spec)

| Sub-PR  | Mergeado | Resumen                                                          |
|---------|----------|------------------------------------------------------------------|
| **PR-A** | #145     | Schema `contact_assignments` + `assignment_rules` + migration + backfill (285 rows). |
| **PR-B** | #146     | Endpoints CRUD + bulk via repo + dashboard EXISTS + filter fields `assigned_users` / `primary_user`. |
| **PR-Ca** | #148    | Hotfix data-too-long source 40→80 + `audit_logs.target_id` 36→120 + perms POST contactos / bulk a `require_user` + dashboard "Asignarme" usa endpoint nuevo. |
| **PR-C** | #147     | Motor de reglas (`evaluate_for_contact` + `run_rule_over_universe`) + CRUD `/api/assignment-rules` + hooks fire-on-create en POST contactos + Brevo + Agile. |
| **PR-D** | #149     | UI ficha "Comerciales asignados" + bulk unlimited (cap 50k + chunk 500) + bulk bar visible para `user` + fix widget "Asignarme" (OR→AND). |
| **PR-Da** | #150    | Hotfix `origin_account_id` con compound keys `(system, account_id)` + scheduler `IntegrationSkipped` para cuentas disabled. |
| **PR-Db** | #151    | Hotfix origin picker frontend emits compound key + scheduler Agile + botón "sync todas las cuentas". |
| **PR-E** | #152     | UI `/admin/assignment-rules` (lista + drawer editor + preview en vivo) + endpoint `/preview` + `apply_to=new_only`. |
| **PR-Ea** | #153    | Hotfix `getUsers` limit 200→100 + cap server-side subido a 500. |
| **PR-F** | (este)   | Cierre: tests E2E (10 nuevos) + audit `assignment_rule.applied` + esta doc. |

**Backfill prod**: 285 contactos migrados de `owner_user_id` a
`contact_assignments` con `source="backfill"` y `is_primary=True`.
Zero pérdida de datos.

**Cobertura tests local**: 953/953 verde tras PR-F (940 + 13 nuevos
entre `test_assignment_rules_e2e.py` + audit en `test_assignment_rules.py`).

**Sprint Reglas-Assign — CERRADO ✅**
