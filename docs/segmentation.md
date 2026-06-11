# Segmentación dinámica

Sprint P.3 introduce los segmentos: grupos dinámicos de contactos
definidos por reglas booleanas. Se re-evalúan solos cuando el
operador entra en su pantalla; conviven con tags (estáticos) y vistas
guardadas (configuración de la lista de contactos).

## Modelo

Tabla `segments` (migración `20260526_0018`):

| Columna | Propósito |
|---|---|
| `rules_json` | Árbol AND/OR/NOT con hojas `{type:"rule", field, comparator, value}`. |
| `is_dynamic` | True (default) → se re-evalúa por reglas. False → lista fija de `static_contact_ids`. |
| `cached_count` / `last_evaluated_at` | Resultado de la última evaluación para que la lista renderice sin re-correr SQL. |
| `is_shared` | Lectura compartida con el resto de la org. Edita sólo el `owner_user_id`. |
| `color` | Swatch de la paleta de tags para el sidebar. |

## Estructura del árbol

```jsonc
{
  "operator": "AND",
  "children": [
    { "type": "rule", "field": "lead_score", "comparator": "gte", "value": 70 },
    { "type": "rule", "field": "marketing_consent", "comparator": "eq", "value": "granted" },
    {
      "operator": "OR",
      "children": [
        { "type": "rule", "field": "origin_system", "comparator": "eq", "value": "agilecrm" },
        { "type": "rule", "field": "tags", "comparator": "contains_any", "value": ["uuid-1", "uuid-2"] }
      ]
    }
  ]
}
```

Operadores lógicos: `AND`, `OR`, `NOT` (NOT acepta un único hijo).

## Anti-injection

El whitelist en `app/services/segments/fields.py` es el único punto
de entrada. Cualquier `field` o `comparator` fuera de la lista hace
fallar `engine.build_filter(...)` antes de generar SQL, y la ruta lo
mapea a HTTP 400. La whitelist también limita los tipos de
`value` aceptados (`int` → coerced a int, `enum` → valor permitido,
`date` → ISO 8601…).

## Campos disponibles

| Campo | Tipo | Comparadores |
|---|---|---|
| name | string | contains, not_contains, starts_with, eq, neq |
| email | string | contains, eq, neq, is_null, is_not_null |
| phone | string | contains, eq, is_null |
| tags | tag-multi | contains_any, contains_all, contains_none |
| origin_system | enum | eq, neq, in, not_in |
| origin_account_id | string | eq, neq, in |
| commercial_status | enum | eq, neq, in |
| marketing_consent | enum | eq, neq, in |
| is_active | bool | eq |
| lead_score | int | eq, neq, gt, gte, lt, lte, between, is_null |
| address_country | string | eq, neq, in, is_null |
| created_at | date | before, after, between, in_last_n_days, not_in_last_n_days |
| updated_at | date | idem |
| external_data_refreshed_at | date | idem |
| pipeline_id | uuid-multi | in, not_in |
| pipeline_stage_id | uuid-multi | in, not_in |
| created_at_external | date | before, after, between, in_last_n_days, not_in_last_n_days |
| updated_at_external | date | idem |
| in_segment | uuid-multi (segment ids) | in, not_in (resuelto recursivamente) |
| in_brevo_list | uuid-multi (Brevo list ids) | in, not_in |

## API

### CRUD

- `GET /api/segments` — propios + compartidos.
- `GET /api/segments/{id}` — detalle. Privados de otros owners → 404.
- `POST /api/segments` — crea + evalúa + audit `segment.created` + `segment.evaluated`.
- `PATCH /api/segments/{id}` — owner-only.
- `DELETE /api/segments/{id}` — owner-only.
- `POST /api/segments/{id}/duplicate` — shared rows duplicables; el duplicado no hereda `is_shared`.

### Lectura

- `GET /api/segments/{id}/contacts` — lista paginada (`skip`, `limit`, `sort_by`, `sort_dir`).
- `GET /api/segments/{id}/count?force_refresh=true` — recomputa el cache si el operador pulsa el botón.

### Builder en vivo

- `POST /api/segments/preview` con `{rules}` → `{count, sample[10]}`. Usado por el live preview a la derecha del builder visual.
- `GET /api/segments/available-fields` → whitelist en formato consumible por la UI.

### Plantillas

- `GET /api/segments/templates` lista 7 plantillas hardcoded (Hot leads, Inactivos 90 días, …). Sin endpoint para instanciar — la wizard hace POST normal con las reglas del template.

### IA

- `POST /api/segments/ai-generate` con `{description}` → propuesta `{rules, count, sample, error?}`. Sin persistencia. Audit metadata-only.
- `POST /api/segments/ai-explain` con `{rules}` o `{segment_id}` → párrafo en español. Patrón **propose, never apply**.

Rate limit por user: 10 generaciones/hora, 30 explicaciones/hora.

### Audit

`segment.created`, `segment.updated`, `segment.deleted`, `segment.duplicated`, `segment.evaluated` (count + duration_ms), `segment.ai_generated`, `segment.ai_explained`.

### Búsqueda de contactos con `rules_json`

El mismo árbol que aliments `/api/segments/preview` también filtra la
lista de contactos vía `POST /api/contacts/search`:

```json
{
  "rules_json": { "operator": "AND", "children": [ {"type":"rule", "field":"email", "comparator":"contains", "value":"@empresa.com"} ] },
  "sort_by": "updated_at_external",
  "sort_dir": "desc",
  "limit": 25,
  "offset": 0,
  "q": "ana"
}
```

- Body vacío (`{}`) devuelve todos los contactos activos — mismo
  comportamiento que `GET /api/contacts` sin filtros.
- `q` es un free-text que se suma al árbol con AND (matching contra
  first_name/last_name/email/phone). Útil para narrow-down rápido
  encima de una vista guardada.
- El endpoint pasa por el engine, así que un campo o comparador
  fuera de la whitelist → **400** con el mensaje del primer fallo.
- Campos nuevos (Mini-PR B): `created_at_external`,
  `updated_at_external`, `in_brevo_list` (uuid-multi de list_ids
  Brevo, evaluado vía LIKE anclado en `external_references.metadata.list_ids`),
  `in_segment` (uuid-multi, resuelve cada segmento referenciado por
  un `segment_resolver` y OR'es los sub-árboles; detección de ciclos).

### Acciones de vista (`/api/contact-views/{id}/...`)

Vistas guardables del Sprint P.1 ahora son la fuente para dos
acciones de "promoción":

- `POST /save-as-segment` — materializa el `filters.rules_json` de la
  vista como segmento regular (visible en `/segments`). Body: `{name,
  description?, color?, is_shared?}`. Las vistas privadas de otros
  usuarios devuelven **403**; las legacy (solo dropdowns) devuelven
  un segmento con `rules: {}` (matches everything) en vez de
  crashear.
- `POST /push-to-brevo-list` — manager-only. Body: `{brevo_account_id,
  brevo_list_id?, new_list_name?}` (exactamente uno). Crea un segmento
  auxiliar con el árbol de la vista + un `BrevoSyncTarget` push-only +
  encola `brevo:push_target`. Si pasa `new_list_name`, primero llama
  `BrevoClient.create_list`. Responde con
  `{sync_log_id, job_id, target_id, segment_id, contacts_to_push,
  brevo_list_id}` para que la UI linke al sync log.

## Próximas extensiones

- **Sprint E**: triggers cuando un contacto entra/sale de un segmento (notificación, asignar tag, mover de pipeline). El motor ya expone `evaluate_contact_against_rules(contact, tree)` para usarlo desde un hook.
- **Sprint P.4**: re-evaluación en background con cron — no necesario aún porque el cache se invalida al entrar al detail page.
- **Sprint AI**: cualificación automática del contacto basada en los segmentos donde encaja.

## UX del builder

El `SegmentRuleBuilder` ofrece dos vistas que comparten el mismo árbol JSON:

- **Vista simple** (default). Lista plana inspirada en Brevo/ActiveCampaign:
  cada fila es `Campo | Operador | Valor | ×`, con un toggle global
  `Coincidir todas (AND)` / `Coincidir cualquiera (OR)` arriba y un
  botón `+ Añadir condición` al final. Cubre el 90% de los casos
  reales del CRM y no requiere que el operador entienda grupos
  anidados.
- **Vista avanzada**. La que ya existía sobre `react-querybuilder`,
  con grupos anidados, NOT, etc. Se selecciona vía botón y la
  preferencia persiste en `localStorage`
  (`crmbomedia_segment_builder_mode`).

Detección automática: si las reglas guardadas contienen grupos
anidados o NOT, el builder arranca en avanzada aunque el usuario
hubiera elegido simple, para que no se pierdan estructuras al
re-serializar.

## Editor de valores tipado

Tanto la vista simple como la avanzada delegan en
`SegmentValueEditor`, que pinta el control adecuado según el tipo de
campo + comparador:

| Tipo de campo               | Editor                              |
|-----------------------------|-------------------------------------|
| `tags` (tag-multi)          | Dropdown con buscador (reutiliza `<TagMultiSelectFilter>`) y chips de seleccionadas — mismo control que la lista de contactos. |
| `address_country`           | Dropdown poblado de `/api/segments/available-countries` (códigos presentes en BD + contact_count). Para `in` → multi-checkbox. |
| `origin_account_id`         | Dropdown / multi-checkbox poblado de `/api/segments/available-origin-accounts` (cuentas habilitadas en /admin/integrations). Si pasa de 20 cuentas, autocomplete. |
| `origin_system` (enum)      | `<select>` con etiquetas legibles ("AgileCRM", "Brevo", …) en vez de slugs. |
| `pipeline_id` (uuid-multi)  | Multi-select de pipelines.          |
| `pipeline_stage_id`         | Multi-select agrupado por pipeline. |
| `enum` (in/not_in)          | Multi-checkbox.                     |
| `enum` (eq/neq)             | `<select>` con `enum_values`.       |
| `bool`                      | Toggle Sí/No.                       |
| `int`                       | `<input type="number">`. Para listas → CSV de enteros. |
| `date`                      | `<input type="date">`. Para `between` → par de fechas. |
| `between` (cualquier tipo)  | Dos inputs del tipo del campo.       |
| `in_last_n_days`            | `<input type="number">` con N días. |
| `is_null` / `is_not_null`   | Sin valor (placeholder "sin valor").|

### Endpoints auxiliares del builder

| Endpoint | Devuelve | Auth |
|---|---|---|
| `GET /api/segments/available-fields` | Whitelist + comparadores. | viewer |
| `GET /api/segments/available-countries` | `[{code, contact_count}]` ordenado por uso. | viewer |
| `GET /api/segments/available-origin-accounts` | `[{value, label, system}]` con cuentas `enabled=true`. | viewer |

El bug que motivó este cambio era que el editor por defecto de
`react-querybuilder` aceptaba cualquier string, así que un operador
podía teclear `formmbo` en un campo `tags` con comparador
`contains_any` y el backend recibía un string donde esperaba una
lista de UUIDs. El engine ahora también convierte ese
`ValueError` a `SegmentRuleError`, así que aunque alguien envíe
`rules_json` malformado por API directa, la respuesta es 400 con
mensaje claro (`Campo 'tags': Comparator 'contains_any' requires a
non-empty list`) en lugar de 500.

## Query builder en `/contacts` (Mini-PR B Fase 2)

La lista de contactos usa el **mismo motor de segmentación** que
`/segments`. Lo que cambia es la presentación:

- **Tabs de vistas guardadas** en la cabecera. "Todos los contactos"
  es una tab permanente que limpia el filtro. Cada `contact_view`
  guardada es una tab; la activa marca un punto (·) en cuanto el árbol
  se desvía del estado guardado, y un cogwheel abre el menú de
  acciones por vista (renombrar / compartir / duplicar / marcar por
  defecto / borrar).
- **`ContactQueryBuilder`** — wrapper de `react-querybuilder` con tema
  Brevo: cards blancas anidadas, combinadores Y/O como cápsulas, botones
  "+ Y" / "+ O" / "×". Reusa la whitelist de campos
  (`/api/segments/available-fields`), el traductor de comparadores y
  el `SegmentValueEditor` tipado, así que todos los campos
  (incluidos los 4 nuevos: `created_at_external`, `updated_at_external`,
  `in_brevo_list`, `in_segment`) están disponibles sin duplicar lógica.
- **Acciones**: arriba del builder, fila con "Guardar" (sólo cuando el
  estado difiere de la vista activa), "Revertir" y menú "Acciones"
  con:
  - "Guardar como vista nueva" — modal de `ContactViewEditorModal`.
  - "Guardar como segmento" — llama a
    `POST /api/contact-views/{id}/save-as-segment` y muestra un
    toast con el nombre.
  - "Enviar contactos a lista Brevo" — abre `PushViewToBrevoModal`,
    elige lista existente o crea una nueva, y llama a
    `POST /api/contact-views/{id}/push-to-brevo-list`.

### URL state preservation

El estado completo (tab activa / árbol / búsqueda / sort / columnas)
viaja en la URL para que la navegación de vuelta desde una ficha
(`/contacts/{id}`) recupere exactamente el mismo screen:

- `view_id=<uuid>` — carga una vista guardada.
- `rules=<base64(json)>` — árbol inline (cuando no hay vista activa).
  Usamos `encodeURIComponent` antes del `btoa` para que valores con
  acentos no rompan el round-trip.
- `q`, `sort=field:dir`, `cols=name,email,...`.

El listener de la URL es one-way (re-aplicamos `router.replace` al
cambiar el state); el push state lo gestiona el router de Next, no
hay listener de `popstate` adicional.

### Migración suave de vistas viejas

Las vistas creadas antes de Sprint UX guardan filtros como un dict
plano (`{q, tag_ids, origin_account_keys, commercial_status, ...}`).
`legacyFiltersToRulesTree` (en `lib/contactRulesMigration.ts`) las
convierte on-the-fly a un árbol AND/OR al cargar; cada filtro pasa a
una rule:

| Campo legacy | Regla resultante |
|---|---|
| `q` | grupo OR de `contains` sobre first_name/last_name/email/phone |
| `tag_ids` + `tag_match_mode='all'` | `tags contains_all [...]` |
| `tag_ids` + `tag_match_mode='any'` | `tags contains_any [...]` |
| `origin_account_keys` | `origin_system in [...]` (extrayendo los sistemas únicos) |
| `commercial_status` | `commercial_status eq X` |
| `marketing_consent` | `marketing_consent eq X` |
| `lead_score_min/max` | `lead_score gte/lte/between` |
| `is_active=false` | `is_active eq false` |

Si la vista nueva ya trae `filters.rules_json`, ese gana.

### Bug fix de paso: DELETE 204 No Content

El helper `apiFetch` siempre intentaba `response.json()` después de un
status OK. En DELETE (204 No Content) eso lanzaba "Unexpected end of
JSON input" y mostraba toast de error aunque la operación había
funcionado. Ahora detecta `status === 204` y `content-length: 0` y
devuelve `null` sin parsear.

### Forma canónica del builder de /contacts (cierre Mini-PR B)

Tras los bugs de producción, el builder de `/contacts` impone la
forma de Brevo en vez de exponer la flexibilidad cruda de
react-querybuilder:

- **Sin dropdown de combinator.** La raíz es un OR implícito de
  "tarjetas"; cada tarjeta es un AND implícito de condiciones. El "O"
  entre tarjetas se pinta como cápsula centrada vía CSS. `+ Y` solo
  aparece dentro de tarjetas; `+ O` solo a nivel raíz (un nivel de
  anidación, como Brevo).
- **`+ O` crea la tarjeta ya configurada** (`addRuleToNewGroups`):
  primera condición con el primer campo del catálogo seleccionado.
- **Editor tipado correctamente conectado.** Lección dura:
  react-querybuilder v8 ignora un componente `valueEditor` por campo
  (solo lee el STRING `valueEditorType`); el editor tipado debe ir en
  `controlElements.valueEditor` global, leyendo el spec desde
  `fieldData`. Sin esto, todos los valores viajaban como texto libre.
- **Prune + coerción antes de emitir** (`pruneRulesTree` en
  `segmentTranslator`): las condiciones sin valor se ignoran (el
  operador las ve en pantalla pero no filtran hasta completarlas);
  ints se convierten de string, fechas se normalizan a ISO
  (`<input type="date">` ya emite YYYY-MM-DD), `between` exige ambos
  extremos, listas vacías se descartan. `{}` = sin filtro.

### Push a lista Brevo — timeout largo

`brevo:push_target` está en `LONG_JOB_TIMEOUTS` (2 h). El job upserta
cada contacto en serie ANTES del add-to-list final; con ~100 req/min
de presupuesto Brevo, una vista de cientos de contactos supera los
600 s por defecto de RQ y el SIGKILL dejaba la lista creada pero
vacía. Mismo remedio que el historical_backfill (PR #57).

### Campañas — borrador y programación en dos pasos

El wizard crea SIEMPRE el borrador primero (`POST /emailCampaigns`
sin `scheduledAt`) y programa con la segunda llamada documentada
(`PUT /emailCampaigns/{id}`). Un rechazo en la programación deja un
borrador usable. Un 405 de Brevo en campañas casi siempre es
restricción de cuenta (API key sin permisos de Marketing o plan sin
API de campañas) — el error de la API ahora lo dice en claro.

## Filtros de /contacts — componente propio (cierre Mini-PR B)

Tras 4 PRs intentando hacer que `react-querybuilder` produjera el UX
de Brevo, el componente de `/contacts` quedó reescrito como
`ContactFiltersBuilder.tsx`: HTML + estado en React, sin librería de
query builder externa. El motor de segmentos backend NO cambia — el
componente emite el mismo `rules_json` que el engine consume.

Modelo de datos plano (lo que el operador realmente piensa):

```
tree     = OR of cards
card     = AND of conditions
condition= { field, operator, value }
```

Operaciones:

- `+ Y` dentro de una tarjeta → añade condición con primer campo del
  catálogo + primer operador válido.
- `+ O` debajo de las tarjetas → añade tarjeta nueva con UNA
  condición sembrada.
- `×` en una condición → la elimina; si la tarjeta queda sin
  condiciones, se elimina la tarjeta; si quedan 0 tarjetas, se
  siembra una nueva con una condición vacía (estado mínimo
  garantizado).

Catálogo de operadores por tipo de campo (`PER_TYPE_OPERATORS`) ∩
whitelist por campo del backend (`/api/segments/available-fields`).
Esto garantiza que el dropdown NUNCA muestre un comparador que el
engine rechazaría (no más bugs tipo `lead_score after`).

Serialización (`serializeTree`):
- Una sola card con una sola condición → emite `{type:"rule", ...}`
  bare.
- Una sola card con varias condiciones → `{operator:"AND", children:[...]}`.
- Múltiples cards → `{operator:"OR", children:[<AND group per card>]}`.
- Condiciones sin valor se descartan; ints/fechas se coercen al tipo
  del campo; vistas sin nada útil emiten `{}` (= match all).

Deserialización (`deserializeTree`):
- Acepta cualquier árbol del engine y lo aplana a 2 niveles. NOT y
  3+ niveles de anidación se descartan/aplanan (el operador puede
  editar la versión simplificada; si necesita anidamiento real, el
  builder avanzado de `/segments` sigue disponible).

Inputs nativos (sin overrides): `<input type="date">` (ISO), `<input
type="number">`, `<select>` para enums/bool, texto coma-separado para
multi-select. CSS en `styles.css` (`.filter-builder`, `.filter-card`,
`.filter-pill*`, `.filter-or-separator`).
