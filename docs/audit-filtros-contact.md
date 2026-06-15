# Auditoría · Filtros del entity-schema de Contact

> **Estado:** documento de revisión, sin tocar código. La normalización va
> aparte una vez Bart valide la tabla.
>
> Autor: Claude Code · Fecha: 2026-06-15 · Rama: `claude/audit-filtros-contact`

## 0. TL;DR

- El schema de Contact tiene **33 campos** (24 de PR-A + 9 de PR-Cc).
  El motor + el editor cubren bien los tipos comunes (string, enum, int,
  date, bool, tag-multi), pero hay **5 mismatches estructurales** y **3
  bugs activos** — uno de ellos el 500 en lista Brevo + "fespa" que
  reportó Bart.
- Causa del 500: `_compile_brevo_list_leaf` hace `int(item)` sin try/except
  y el editor del campo `in_brevo_list` cae al CsvEditor de texto libre
  (no hay picker), así que el operador escribe "fespa" → `ValueError`
  uncaught → 500.
- Patrón global a corregir: **3 campos de tipo `uuid-multi`** (`in_segment`,
  `in_brevo_list`, `pipeline_id`/`pipeline_stage_id`) sólo dos tienen
  picker. Los otros dos caen a CsvEditor — el operador no tiene forma
  de saber qué teclear.
- También faltan en algunos enum/numeric los nullable matchers
  (`is_not_null` en `lead_score`, `is_null/is_not_null` en los 3 enums).
- `older_than_n_days` está en el motor pero el editor no lo trata como
  duración numérica → cae a date picker (UX rota).

## 1. Tabla: rules normativas (input de Bart)

| Tipo            | Comparators válidos                                      | Editor UX                                                |
| --------------- | -------------------------------------------------------- | -------------------------------------------------------- |
| string libre    | `eq, neq, contains, starts_with, ends_with, is_null, is_not_null` | input texto                                              |
| enum            | `eq, neq, in, not_in, is_null, is_not_null`              | dropdown / multi-select según comparator                 |
| number          | `eq, neq, gt, gte, lt, lte, between, is_null, is_not_null` | input numérico                                           |
| date / datetime | `eq, before, after, between, in_last_n_days, is_null, is_not_null` | date picker + relative shortcuts                         |
| boolean         | `eq` (true/false)                                        | toggle                                                   |
| reference       | `eq, neq, in, not_in, is_null, is_not_null`              | picker con autocompletado contra la tabla referenciada   |
| tag-multi       | `contains_any, contains_all, contains_none, tag_name_contains` | chips picker + input texto cuando `tag_name_contains`    |
| json            | `eq, contains, is_null`                                  | display-only en v1 (diferido)                            |

## 2. Inventario de los 33 campos

Leyenda **status**:
- ✅ **OK**: tipo, comparators y editor casan con la tabla normativa
- ⚠️ **Mismatch parcial**: faltan comparators o el editor no es óptimo
- ❌ **Roto**: 500 o UX inutilizable

### Datos básicos (6)

| Field         | Tipo declarado | Comparators registrados                                    | Editor que dispara                       | Status | Notas                                                                 |
|---------------|----------------|------------------------------------------------------------|------------------------------------------|--------|-----------------------------------------------------------------------|
| `name`        | string (computed concat) | `contains, not_contains, starts_with, ends_with, eq, neq, is_null, is_not_null` | text input fallback                      | ⚠️     | `is_null` sobre concat no tiene semántica clara — drop o documentar    |
| `first_name`  | string         | idem                                                       | text input                                | ✅      |                                                                       |
| `last_name`   | string         | idem                                                       | text input                                | ✅      |                                                                       |
| `email`       | string         | idem                                                       | text input                                | ✅      |                                                                       |
| `phone`       | string         | idem                                                       | text input                                | ✅      |                                                                       |
| `tags`        | tag-multi      | `contains_any, contains_all, contains_none, tag_name_contains` | `TagMultiSelectFilter` (chips picker) / text para `tag_name_contains` | ✅      | PR-Cd hizo el override global; PR-Cc añadió `tag_name_contains`        |

### Comercial (4)

| Field               | Tipo       | Comparators registrados                            | Editor                                                                                          | Status | Notas                                                                                                    |
|---------------------|------------|----------------------------------------------------|-------------------------------------------------------------------------------------------------|--------|----------------------------------------------------------------------------------------------------------|
| `commercial_status` | enum       | `eq, neq, in, not_in`                              | `EnumEditor`/`EnumMultiEditor` por `enum_values.length > 0`                                     | ⚠️     | Falta `is_null, is_not_null`. Free-string subyacente; valores advisory new/qualified/won/lost            |
| `owner_user_id`     | reference  | `eq, neq, in, not_in, is_null, is_not_null`         | **TEXT input fallback** (no hay picker de usuarios)                                              | ❌     | Bart explícito: reference → picker autocomplete. Hoy un texto libre que el operador no sabe qué teclear |
| `lead_score`        | int        | `eq, neq, gt, gte, lt, lte, between, is_null`       | `NumberEditor` (single), `CsvEditor` (multi en list comparators no registrados aquí)             | ⚠️     | Falta `is_not_null`. El motor lo soporta; sólo es un olvido del whitelist                                |
| `pipeline_id`       | uuid-multi | `in, not_in`                                       | `PipelineEditor("pipeline")` (picker dropdown)                                                  | ✅      |                                                                                                          |
| `pipeline_stage_id` | uuid-multi | `in, not_in`                                       | `PipelineEditor("stage")` (picker)                                                              | ✅      |                                                                                                          |

> ℹ️ `pipeline_id`/`pipeline_stage_id` los conté en Comercial porque pertenecen al grupo, pero
> técnicamente son `uuid-multi`. Tabla siguiente los re-lista bajo "Referencias / pertenencias".

### GDPR (1)

| Field                | Tipo | Comparators                  | Editor                            | Status | Notas                                                          |
|----------------------|------|------------------------------|------------------------------------|--------|----------------------------------------------------------------|
| `marketing_consent`  | enum | `eq, neq, in, not_in`        | `EnumEditor`/`EnumMultiEditor`     | ⚠️     | Falta `is_null, is_not_null`. Valores: granted/denied/unknown/unsubscribed |

### Sistema (4)

| Field            | Tipo    | Comparators              | Editor                | Status | Notas                                                  |
|------------------|---------|--------------------------|------------------------|--------|--------------------------------------------------------|
| `is_active`      | bool    | `eq`                    | `BoolEditor` (toggle) | ✅      |                                                        |
| `is_email_valid` | bool    | `eq`                    | `BoolEditor`           | ✅      |                                                        |
| `created_at`     | date    | `before, after, between, in_last_n_days, not_in_last_n_days, older_than_n_days, is_null, is_not_null` | `DateEditor` o `NumberEditor` (para in_last/not_in_last) | ⚠️ | `older_than_n_days` cae a `DateEditor` por bug (ver §3.3). Falta `eq` si lo quisiéramos por paridad con Bart |
| `updated_at`     | date    | idem                    | idem                  | ⚠️     | Mismo bug `older_than_n_days`                          |

### Profesional (3)

| Field              | Tipo   | Comparators                                  | Editor      | Status | Notas                                                                          |
|--------------------|--------|----------------------------------------------|-------------|--------|--------------------------------------------------------------------------------|
| `job_title`        | string | `_COMMON_STRING`                             | text input  | ✅      |                                                                                |
| `linkedin_url`     | string | `_COMMON_STRING`                             | text input  | ✅      |                                                                                |
| `personal_website` | string | `_COMMON_STRING`                             | text input  | ✅      |                                                                                |
| `company_id`       | reference | `eq, neq, in, not_in, is_null, is_not_null` | **TEXT input fallback** (no hay picker de empresas) | ❌ | Igual que `owner_user_id`. Bart lo quiere como picker autocomplete contra `companies` |

### Dirección (6)

| Field                  | Tipo   | Comparators                                                                   | Editor                          | Status | Notas                                          |
|------------------------|--------|-------------------------------------------------------------------------------|---------------------------------|--------|------------------------------------------------|
| `address_country`      | string | `eq, neq, in, not_in, contains, not_contains, is_null, is_not_null`           | `CountryEditor` (dropdown de países servido por API segments) | ✅ |                                                |
| `address_state`        | string | `_COMMON_STRING`                                                              | text input                      | ✅      |                                                |
| `address_city`         | string | `_COMMON_STRING`                                                              | text input                      | ✅      |                                                |
| `address_line`         | string | `_COMMON_STRING`                                                              | text input                      | ✅      |                                                |
| `address_postal_code`  | string | `_COMMON_STRING`                                                              | text input                      | ✅      |                                                |
| `address_region`       | string | `_COMMON_STRING`                                                              | text input                      | ✅      |                                                |

### Origen (5)

| Field                          | Tipo    | Comparators                                  | Editor                                                                       | Status | Notas                                              |
|--------------------------------|---------|----------------------------------------------|-------------------------------------------------------------------------------|--------|----------------------------------------------------|
| `origin_system`                | enum    | `eq, neq, in, not_in`                        | `EnumEditor`/`EnumMultiEditor`                                                | ⚠️     | Falta `is_null/is_not_null`                        |
| `origin_account_id`            | string  | `eq, neq, in`                                | `OriginAccountEditor` (picker servido por API segments)                       | ✅      | tipo "string" engañoso — el editor por `key` lo corrige |
| `external_data_refreshed_at`   | date    | `_DATE`                                      | `DateEditor` / `NumberEditor` (in_last/not_in_last)                           | ⚠️     | `older_than_n_days` mismo bug                      |
| `created_at_external`          | date    | `_DATE`                                      | idem                                                                          | ⚠️     | Idem                                               |
| `updated_at_external`          | date    | `_DATE`                                      | idem                                                                          | ⚠️     | Idem                                               |

### Referencias / pertenencias (3 — relations a otras tablas)

| Field            | Tipo       | Comparators   | Editor                                                                       | Status | Notas                                                                                 |
|------------------|------------|---------------|-------------------------------------------------------------------------------|--------|---------------------------------------------------------------------------------------|
| `in_segment`     | uuid-multi | `in, not_in`  | **CsvEditor** (separa UUIDs por coma)                                         | ❌     | No hay picker de segmentos. Operador escribe UUIDs a mano                              |
| `in_brevo_list`  | uuid-multi | `in, not_in`  | **CsvEditor**                                                                 | ❌     | **EL 500 DE BART**. Escribir "fespa" → `int("fespa")` en `_compile_brevo_list_leaf` → 500. Necesita picker contra `/api/brevo/lists` + manejo seguro en el motor |

> *(pipeline_id / pipeline_stage_id ya listados arriba bajo Comercial.)*

## 3. Bugs activos detectados

### 3.1. El **500 del filtro Brevo** (reportado por Bart) ❌ alta prioridad

**Reproducción exacta**: `in_brevo_list / es uno de / fespa`
**Cadena:**
1. Tree: `{type:"rule", field:"in_brevo_list", comparator:"in", value:["fespa"]}`
2. `validate_value(spec, "in", ["fespa"])` → `["fespa"]` (string passthrough porque type=`uuid-multi` no entra en bool/int/enum/date/reference)
3. Engine despacha a `_compile_brevo_list_leaf("in", ["fespa"])`
4. Línea `list_ids = [str(int(item)) for item in value]` → `int("fespa")` → `ValueError` **uncaught**
5. FastAPI devuelve 500

**Fix mínimo (motor):** wrap del `int(...)` en try/except → `SegmentRuleError` que el route layer transforma en 400 con un mensaje claro ("`in_brevo_list` espera ids numéricos de lista Brevo").

**Fix UX completo (frontend):** añadir un `BrevoListPicker` (igual que `OriginAccountEditor`) que abra un dropdown contra `/api/brevo/lists?account_id=…` y emita los `id` reales. Mientras tanto, ningún operador racional debería poder llegar a este 500 — el picker es la solución correcta.

### 3.2. **`owner_user_id` / `company_id` sin picker** ❌ alta prioridad

**Reproducción**: `owner_user_id / es igual a / <vacío>`
- El editor por defecto es un text input, así que el operador escribe… ¿qué? ¿Un email? ¿Un UUID?
- Si escribe algo no-UUID, `validate_value` reference → `_coerce_scalar` → `str(value)` → pasa el motor → `column == "raul@bomedia.net"` → 0 matches (NO 500, sólo confusión).

**Fix UX**: 2 pickers nuevos análogos a `OriginAccountEditor`:
- `UserPicker` contra `/api/users` (probablemente ya existe — verificar) → emite `user.id` (UUID).
- `CompanyPicker` contra `/api/companies?q=...` → emite `company.id`.

### 3.3. **`older_than_n_days` cae a DateEditor** ⚠️ media

**Reproducción**: `created_at / hace más de N días`
- El editor lista los 3 comparadores temporales relativos en `_DATE`.
- `NUMERIC_DURATION_COMPARATORS = {"in_last_n_days", "not_in_last_n_days"}` — **`older_than_n_days` falta**.
- Por orden de checks, el editor cae a `type === "date"` → `DateEditor` (date picker).
- Operador necesita meter "30 días", obtiene un calendario. No se entiende.

**Fix**: añadir `"older_than_n_days"` al set `NUMERIC_DURATION_COMPARATORS` en `SegmentValueEditor.tsx`.

### 3.4. Faltantes en whitelists (rules table de Bart vs. real) ⚠️ baja

| Field type      | Falta                          | Campos afectados                                                  | Plan                                                                 |
|-----------------|-------------------------------|-------------------------------------------------------------------|----------------------------------------------------------------------|
| `lead_score`    | `is_not_null`                 | `lead_score`                                                      | Añadir a `_NUMERIC`                                                  |
| Todos los enum  | `is_null, is_not_null`        | `origin_system`, `commercial_status`, `marketing_consent`         | Reemplazar `("eq", "neq", "in", "not_in")` con `_ENUM_NULLABLE` que extienda `_ENUM` |
| `name` concat   | `is_null` semánticamente raro | `name`                                                            | Documentar que matchea cuando ambos NULL                              |

## 4. Plan de normalización (mini-PR siguiente)

Ordenado por prioridad/impacto. Cada item es un cambio puntual; agrupados en un solo PR queda manejable.

### Backend (`fields.py` + `engine.py`)

1. `_compile_brevo_list_leaf` y `_compile_segment_membership` envuelven los parses en try/except → `SegmentRuleError` ("`in_brevo_list` espera ids numéricos") → 400. Tests pin.
2. `_NUMERIC` adquiere `is_not_null`.
3. Define `_ENUM_NULLABLE = _ENUM + ("is_null", "is_not_null")` y rebincha los 3 enums a este set.
4. `validate_value` para `type="reference"` valida que el valor sea un UUID (regex simple, 36 chars o hex 32) — previene basura silenciosa.

### Frontend (`SegmentValueEditor.tsx`)

5. `NUMERIC_DURATION_COMPARATORS` añade `"older_than_n_days"`.
6. Editor nuevo `BrevoListPicker` (componente análogo a `OriginAccountEditor`) — dispatch en `spec.key === "in_brevo_list"`. Servir contra `/api/brevo/lists` resolviendo `accountId` primario (mismo patrón que `/marketing/templates`).
7. Editor nuevo `SegmentPicker` para `spec.key === "in_segment"` — dropdown contra `/api/segments` (ya existe).
8. Editor nuevo `UserPicker` para `spec.key === "owner_user_id"` — `/api/users` (verificar endpoint y forma).
9. Editor nuevo `CompanyPicker` para `spec.key === "company_id"` — `/api/companies?q=...` con debounce.

### Tests

- Backend: pin del 400 (no 500) para `in_brevo_list/value=["fespa"]`.
- Backend: pin de `_ENUM_NULLABLE` aplicado a los 3 enums (filtro `is_null` matchea).
- Backend: pin de `older_than_n_days` sigue verde tras añadirlo al set numerico (no rompe el motor).
- Frontend: sin runner; manual en sandbox.

## 5. Bonus UX para pickers de listas largas

Aplicable a `BrevoListPicker`, `TagMultiSelectFilter` (cuando >100 tags), `CompanyPicker`:

### 5.1 Agrupación por prefijo

Las listas Brevo siguen el patrón `brevo-list:<nombre>` cuando se importan como tags
(ver `brevo/mapper.py:92`). Lo mismo aplica a las listas reales — muchas comparten un
prefijo común (`fespa-…`, `mbo-…`, `artisjet-…`).

Render del dropdown:

```
[brevo-list:fespa-*]   (colapsable, badge "3")
  ─ brevo-list:fespa-2024
  ─ brevo-list:fespa-leads
  ─ brevo-list:fespa-old
[brevo-list:mbo-*]     (colapsable, badge "12")
  ─ …
[Otros]                (todo lo no-agrupado)
```

Implementación: `groupBy = (item) => item.name.split(/[:\-]/)[0]` (primer segmento separado por `:` o `-`). Si hay sólo 1 item por grupo, no colapsar.

### 5.2 Truncado con ellipsis + tooltip

Nombres largos (`brevo-list:fespa-warm-leads-2024-q4`) cortar a ~28 chars con
`text-overflow: ellipsis` + `<button title={fullName}>` para que el hover muestre
el nombre completo. CSS de `.tag-multiselect-name` ya tiene `white-space: nowrap` —
añadir `overflow: hidden; text-overflow: ellipsis; max-width: 240px`.

### 5.3 Paginación interna o virtual scroll

`TagMultiSelectFilter` hoy slicea a 80 resultados (`tags.slice(0, 80)`). Para listas Brevo
+ tags brevo-list:* esto puede recortar. Opciones por orden de complejidad:

| Opción                                | Esfuerzo | Cuándo aplicar                              |
|---------------------------------------|----------|---------------------------------------------|
| Aumentar el slice a 200 o eliminar     | trivial  | Si la API ya devuelve <=200, hecho           |
| Paginación interna con cursor (offset) | bajo     | Si la API soporta `?offset=&limit=`         |
| Virtual scroll (`@tanstack/virtual`)   | medio    | Si necesitamos >1k items renderizados       |

Recomendación: aumentar slice a 300 + `useDeferredValue` en la query de búsqueda
en v1; virtual scroll cuando algún operador reporte lag.

## 6. Qué NO entra en la normalización

- `json` (custom_fields) — diferido por decisión §3 del spec.
- Crear más pickers para campos que no están en el schema hoy (`segment_membership` por
  ejemplo). Se acota a los 4 campos uuid/reference que sí están y caen a CsvEditor o text.
- Cambios al motor de filter compile que no resuelven un bug declarado (no refactors).

## 7. Validación previa al merge de la normalización

Sandbox `/sandbox/entity-table?entity=contact`:

- `in_brevo_list / es uno de / <texto sin sentido>` → **400 con mensaje en pantalla**, no 500.
- `in_brevo_list / es uno de / …` con picker abierto → autocomplete contra Brevo, agrupado por prefijo.
- `owner_user_id / es igual a / …` con picker → dropdown de usuarios.
- `company_id / en / [a, b]` con picker → multi-select de empresas.
- `created_at / hace más de N días / 30` → input numérico (no calendario).
- `commercial_status / está vacío` → 0 matches (lo esperado en una base sin commercial_status NULL).
- `lead_score / no está vacío` → matchea contactos con lead_score sin importar el valor.
