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
| `tags` (tag-multi)          | Multi-select con checkboxes y swatch de color, lista cargada desde `/api/tags`. |
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

El bug que motivó este cambio era que el editor por defecto de
`react-querybuilder` aceptaba cualquier string, así que un operador
podía teclear `formmbo` en un campo `tags` con comparador
`contains_any` y el backend recibía un string donde esperaba una
lista de UUIDs. El engine ahora también convierte ese
`ValueError` a `SegmentRuleError`, así que aunque alguien envíe
`rules_json` malformado por API directa, la respuesta es 400 con
mensaje claro (`Campo 'tags': Comparator 'contains_any' requires a
non-empty list`) en lugar de 500.
