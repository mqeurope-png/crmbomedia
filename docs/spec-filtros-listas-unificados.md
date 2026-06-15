# SPEC — Sprint Filtros & Listas Unificados

> **Estado: PROPUESTA / NO IMPLEMENTAR.** Este documento es el resultado de la
> fase de investigación. Bart lo revisa y aprueba (o ajusta) antes de abrir
> ningún PR de implementación. No se ha tocado código de producción ni se ha
> añadido ninguna dependencia.
>
> Autor: Claude Code · Fecha: 2026-06-15 · Rama: `claude/spec-filtros-listas`

---

## 0. TL;DR ejecutivo

El CRM **ya tiene** el 60% de la infraestructura que este sprint necesita, pero
escondida detrás de la pantalla de contactos y duplicada de forma incoherente en
las demás:

1. **Motor de filtros real, recursivo, AND/OR/NOT, con whitelist anti-inyección**
   ya existe en `app/services/segments/engine.py` (Sprint P.3). Compila un árbol
   JSON → `WHERE` de SQLAlchemy. **Está cableado solo a `Contact`.**
2. **Esquema declarativo de campos** ya existe (`app/services/segments/fields.py`,
   `list_fields_for_ui()`), servido en `GET /api/segments/available-fields` como
   `{key, label, type, comparators[], enum_values[]}`. Solo Contact.
3. **Vistas guardadas** (`contact_views`) ya guardan `filters_json` + `columns_json`
   + `sort_json` con owner/compartido/default. Cableado solo a Contact.
4. La pantalla `/contacts` **ya usa un query builder de árbol** (`ContactFiltersBuilder`)
   + selección "todos los filtrados" + columnas configurables + sort por cabecera.
   Es el patrón maduro; las otras 4 pantallas no llegan ahí.
5. Existe ya un `lib/segmentTranslator.ts` que traduce **react-querybuilder ⇄ IR
   del backend** — señal de que RQB ya estaba contemplado.

**Conclusión arquitectónica:** el sprint NO es "construir desde cero", es
**(a) generalizar el motor + el esquema + las vistas de `Contact` a un registro
multi-entidad, (b) sustituir el query builder casero de 2 niveles (que pierde NOT
y anidamiento >2) por react-querybuilder, (c) sustituir las tablas a mano por un
`<EntityTable>` con TanStack Table, y (d) migrar las 5 pantallas a esa base,
limpiando deuda por el camino.**

Esto **preserva** la inversión del Sprint P.3 (segmentos siguen funcionando) y de
Reglas-Assign (reusa el mismo motor IR + schema).

---

## 1. Inventario del estado actual

> ⚠️ **Dos correcciones a las premisas del brief:**
> 1. **No existe la ruta `/empresas`.** La lista de empresas vive en **`/companies`**.
> 2. **`/companies` SÍ pagina** (componente `<Pagination>` compartido, `offset/limit/total`
>    de servidor). La deuda real de empresas es **ausencia de bulk actions**, filtros
>    no persistidos en URL, y sin ordenación. La premisa "sin paginación funcional"
>    es incorrecta.

### 1.1 Tabla comparativa

| Pantalla | Componente raíz | Backend endpoint | Filtros | Bulk actions | Columnas config.? | Vistas guardadas? | Sort? | Paginación |
|---|---|---|---|---|---|---|---|---|
| **`/contacts`** (baseline maduro) | `contacts/page.tsx` (~1050 líneas, monolito) | `POST /api/contacts/search` (+`/search/ids`) | Query builder árbol AND/OR (2 niveles) + búsqueda `q` + toggle "asignados a mí" | `assign_owner`, `change_status`, `deactivate` (`add/remove_tag` definidos pero **sin botón**) | **Sí** (`contactColumns.ts` + `ColumnConfigurator`, show/hide + reorder, persiste en vista/localStorage) | **Sí** (`ContactViewsTabs` → `/api/contact-views`) | **Sí**, 1 columna, cabecera clicable + `<select>` | offset/limit, `PAGE_SIZE=25`, inline |
| **`/companies`** | `companies/page.tsx` (autocontenido) | `GET /api/companies` (`q,country,source,has_contacts,limit,offset`) | 4 selects locales (no URL): búsqueda, país (hardcoded 4 valores), fuente, tiene-contactos | **NINGUNA** ❌ | **No** (`<thead>` fijo: Nombre, Dominio, CIF, País, Fuente, #Contactos, Actualizada) | **No** | **No** (backend fija `ORDER BY name ASC`) | offset/limit, `PAGE_SIZE=100`, `<Pagination>` compartido ✅ |
| **`/emails`** | `emails/layout.tsx` (3 paneles) + `EmailThreadList.tsx` | `GET /api/emails/threads` | Sidebar (state/folder/label/starred) + `EmailFiltersBar` (q, unread, rango fechas) — **en URL** | **Sí**: archivar/papelera/spam/restaurar, star, leído, mover, etiquetar (`POST /api/emails/threads-bulk/{action}`) | **No** (lista `<ul>` fija) | **No** (sidebar = presets URL hardcoded) | **No** (backend fija `last_message_at DESC`) | **NINGUNA** ❌ (API devuelve `total`, se ignora) |
| **`/marketing/templates`** | `marketing/templates/page.tsx` | `GET /api/brevo/templates` (array, sin envelope) | búsqueda + tag + activo — **client-side** `Array.filter` | **Ninguna** | **No** (card grid fijo) | **No** | **No** | **Ninguna** (renderiza todo el array) |
| **`/marketing/campaigns`** | `marketing/campaigns/page.tsx` | `GET /api/brevo/campaigns` (array) | búsqueda + status — **client-side** (¡ignora el `status` de servidor que el cliente API ya soporta!) | **Ninguna** | **No** (`<table>` fija) | **No** | **No** | **Ninguna** |

### 1.2 Bugs / smells concretos detectados

- **`/emails` — filtros silenciosamente ignorados:** `EmailFiltersBar` escribe
  `has_unread`, `since`, `until` en la URL y el backend los soporta, pero
  `EmailThreadList.tsx:96-101` **no los reenvía** a la API → 3 filtros no-op.
- **`/emails` — sin paginación** pese a que la API devuelve `total`. Riesgo de
  escala real.
- **`/marketing/campaigns` — ignora su propio `status` de servidor:** filtra en
  cliente sobre todo el array; el param `status` está cableado en `brevoApi.ts`
  pero la página nunca lo usa.
- **`/contacts` — `add_tag`/`remove_tag` son código muerto en UI** (definidos en
  `BulkAction`, descritos en el comentario del bar, sin botón renderizado).
- **`/contacts` — `assigned_to_me` no está en la URL** → copiar/pegar la URL o
  navegar atrás pierde el toggle (que además re-default a `true` para rol `user`).
- **`/contacts` — "seleccionar todos los filtrados" puede sub-seleccionar en
  silencio:** `searchContactIds` trunca a `max_ids=10.000`; muestra error pero
  `selected` se queda con el set truncado → para 19k matches, footgun real.
- **`/contacts` — round-trip de reglas con pérdida:** `deserializeTree`
  (`ContactFiltersBuilder.tsx:224-257`) aplana cualquier cosa más allá de 2
  niveles AND/OR (NOT, anidamiento ≥3) y avisa que solo preserva el árbol "mientras
  el operador no toque nada". El **backend ya soporta NOT + anidamiento arbitrario**;
  el cuello de botella es el builder de UI.
- **`/contacts` — `column widths` plumbed pero sin usar** (siempre `{}`).
- **Inconsistencias transversales:**
  - **Paginación: 4 respuestas distintas.** Solo `/companies` y `/contacts` paginan,
    y lo hacen con componentes distintos (`<Pagination>` vs inline). Page sizes:
    25 (contacts) / 100 (companies) / 50 (backend default companies). `/emails` y
    marketing no paginan.
  - **Estado de filtro: 3 modelos.** URL (`/emails`, `/contacts`) vs estado local
    efímero (`/companies`, marketing).
  - **Filtrado servidor vs cliente:** marketing filtra en cliente sobre fetch
    completo; el resto en servidor.
  - **Selección `Set<string>`** duplicada entre `/emails` (con shift-click range) y
    `/contacts` (con banner "todos filtrados"), sin hook compartido.
  - **Bulk bars** (`EmailBulkActionsBar`, `ContactsBulkBar`) son componentes
    separados sin base común.
  - **Envelopes de respuesta divergentes:** `{items,total,limit,offset}` (contacts)
    vs `{items,total}` (companies, emails) vs array crudo (marketing).
  - **Debounce de búsqueda** reimplementado 3 veces (250ms / 300ms / sin debounce).

### 1.3 Backend de filtrado — el hallazgo clave

Existen **DOS sistemas de filtrado en paralelo**:

| | Motor de reglas (Sprint P.3) | Params planos legacy |
|---|---|---|
| Dónde | `app/services/segments/engine.py` `build_filter(tree)` | `app/repositories/crm.py` `_apply_contact_filters` |
| Lo usan | `POST /api/contacts/search[/ids]`, `/api/segments/*` | `GET /api/contacts` |
| Formato | Árbol JSON recursivo (ver §3.2) | ~25 query params `if param: where(...)` |
| Operadores | 24 comparadores + AND/OR/NOT, depth 10, whitelist anti-inyección | hardcoded por param |
| Entidad | **Solo Contact** (`Contact.id`, `ContactTag`, …) | Solo Contact |

El motor de reglas es el que hay que **generalizar y convertir en único**; los
params planos hay que **retirarlos** (duplican capacidad con vocabulario divergente).

Comparadores que el motor ya soporta (`engine.py`): `is_null, is_not_null, eq, neq,
contains, not_contains, starts_with, ends_with, in, not_in, gt, gte, lt, lte,
between, before, after, in_last_n_days, not_in_last_n_days, older_than_n_days`
y para tags `contains_any, contains_all, contains_none`. Leaves de relación ya
implementados: `tags`, `external_refs.system/account_id`, `pipeline_id/stage_id`,
`brevo_list_membership`, `segment_membership` (recursivo con detección de ciclos).

---

## 2. Esquema declarativo propuesto por entidad

### 2.1 Tipo del descriptor (TS compartido)

Extiende el `SegmentFieldDescriptor` que el backend ya emite hoy (`{key, label,
type, comparators[], enum_values[]}`) con los atributos de columna/UI:

```ts
type FieldType =
  | 'string' | 'number' | 'date' | 'datetime' | 'boolean'
  | 'enum' | 'reference' | 'tag-multi' | 'uuid-multi' | 'json';

// Operadores = vocabulario EXACTO del motor existente (no renombrar: es el IR
// persistido en segments.rules_json y contact_views.filters_json.rules_json).
type Operator =
  | 'is_null' | 'is_not_null'
  | 'eq' | 'neq' | 'in' | 'not_in'
  | 'contains' | 'not_contains' | 'starts_with' | 'ends_with'
  | 'gt' | 'gte' | 'lt' | 'lte' | 'between'
  | 'before' | 'after' | 'in_last_n_days' | 'not_in_last_n_days' | 'older_than_n_days'
  | 'contains_any' | 'contains_all' | 'contains_none';

interface FieldDescriptor {
  key: string;
  label: string;
  type: FieldType;
  operators: Operator[];          // = comparators del motor para ese tipo
  sortable: boolean;
  filterable: boolean;
  displayable: boolean;
  defaultVisible: boolean;
  groupedUnder: string;           // sección en el ColumnConfigurator / FilterBuilder
  source: 'column' | 'custom_fields_json' | 'computed' | 'related_table';
  enumValues?: string[];
  referenceTable?: 'users' | 'companies' | 'tags' | 'pipelines' | 'pipeline_stages'
                 | 'segments' | 'brevo_lists' | 'email_folders' | 'email_labels';
  ambiguous?: string;             // nota si el campo es problemático (ver §2.7)
}
```

> **Mapeo de operadores RQB↔motor:** react-querybuilder usa nombres propios
> (`=`, `!=`, `contains`, `beginsWith`, `null`, `between`, `in`…). El frontend
> traduce RQB→IR del motor en `segmentTranslator.ts` (ya existe). **La fuente de
> verdad persistida es el IR del motor**, no el formato RQB (ver decisión §3.2).

### 2.2 Contact (`contacts`)

| key | label | type | source | sortable | defaultVisible | groupedUnder | notas |
|---|---|---|---|---|---|---|---|
| `name` | Nombre | string | computed (`first+last`) | ✓ | ✓ | Datos básicos | concat, always-visible |
| `first_name` | Nombre pila | string | column | ✓ | ✗ | Datos básicos | NOT NULL |
| `last_name` | Apellidos | string | column | ✓ | ✗ | Datos básicos | |
| `email` | Email | string | column | ✓ | ✓ | Datos básicos | unique, nullable |
| `phone` | Teléfono | string | column | ✓ | ✓ | Datos básicos | canónico |
| `commercial_status` | Estado comercial | enum | column | ✓ | ✓ | Comercial | **free-string**; valores advisory: new/qualified/won/lost |
| `marketing_consent` | Consentimiento | enum | column | ✓ | ✓ | GDPR | unknown/granted/denied/unsubscribed |
| `lead_score` | Lead score | number | column | ✓ | ✗ | Comercial | int, nullable |
| `is_active` | Activo | boolean | column | ✓ | ✗ | Sistema | soft-delete |
| `is_email_valid` | Email válido | boolean | column | ✗ | ✗ | Sistema | |
| `owner_user_id` | Propietario | reference→users | column | ✓ | ✗ | Comercial | ⚠ informal FK, NULL en importados |
| `job_title` | Cargo | string | column | ✓ | ✗ | Profesional | |
| `linkedin_url` | LinkedIn | string | column | ✗ | ✗ | Profesional | |
| `personal_website` | Web | string | column | ✗ | ✗ | Profesional | |
| `address_country` | País (contacto) | string | column | ✓ | ✗ | Dirección | ISO2 ⚠ dup Company.country |
| `address_country_name` | País nombre | string | column | ✗ | ✗ | Dirección | |
| `address_state` | Provincia | string | column | ✗ | ✗ | Dirección | ⚠ dup Company.state |
| `address_city` | Ciudad | string | column | ✓ | ✗ | Dirección | ⚠ dup Company.city |
| `address_line` | Dirección | string | column | ✗ | ✗ | Dirección | ⚠ dup Company |
| `address_postal_code` | CP | string | column | ✗ | ✗ | Dirección | ⚠ dup Company |
| `address_region` | Región | string | column | ✗ | ✗ | Dirección | ⚠ dup Company |
| `origin` / `origin_system` | Origen | enum | column / related (external_refs) | ✓ | ✓ | Origen | agilecrm/brevo/freshdesk/factusol |
| `origin_account_id` | Cuenta origen | reference | related (external_refs) | ✗ | ✗ | Origen | |
| `tags` (tag_objects) | Tags | tag-multi | related (contact_tags) | ✗ | ✓ | Datos básicos | M:N; `contains_any/all/none` |
| `company` | Empresa | reference→companies | related | ✓* | ✗ | Profesional | *sort por nombre vía join |
| `pipeline_id` | Pipeline | reference→pipelines | related | ✗ | ✗ | Comercial | filtro only |
| `pipeline_stage_id` | Etapa | reference→pipeline_stages | related | ✗ | ✗ | Comercial | filtro only |
| `in_segment` | En segmento | uuid-multi | related (segments) | ✗ | ✗ | Segmentos | recursivo |
| `in_brevo_list` | En lista Brevo | uuid-multi | related | ✗ | ✗ | Marketing | |
| `lead_score` … `created_at` | — | — | — | — | — | — | |
| `created_at` | Creado (CRM) | datetime | column | ✓ | ✗ | Sistema | |
| `updated_at` | Modificado (CRM) | datetime | column | ✓ | ✗ | Sistema | |
| `created_at_external` | Creado (origen) | datetime | column | ✓ | ✓ | Origen | |
| `updated_at_external` | Modificado (origen) | datetime | column | ✓ | ✓ | Origen | |
| `external_data_refreshed_at` | Último refresh | datetime | column | ✓ | ✗ | Origen | |
| `external_data_freshness` | Frescura datos | computed | computed | ✗ | ✗ | Origen | "outdated"/… display-only |
| **custom_fields_json** (whitelist, 13 keys) | — | string | custom_fields_json | ✗ | ✗ | Datos adicionales | ver lista |

`custom_fields_json` (whitelist `CUSTOM_FIELDS_WHITELIST`): `GRADO_DE_INTERES`,
`TIPO_DE_CENTRO`, `INTERES`, `PRODUCTOS_DE_INTERES`, `EQUIPO_INTERESADO`,
`INTERESADO_EN_DEMO`, `TITULARITAT_CENTRE`, `ESTUDIS_ETIQUETES`, `FAIG_PPTO_ENVIADO`,
`HORARIO`, `EMAIL_SECUNDARIO`, `EMAIL2`, `EMAIL_2`. Tipo `string`, operadores
`eq/neq/contains/not_contains/is_null/is_not_null`. Filtrar JSON requiere
`JSON_EXTRACT` (MySQL) — ver riesgo §5.

Enums (valores exactos): `ConsentStatus` = unknown/granted/denied/unsubscribed ·
`ExternalSystem` = agilecrm/brevo/freshdesk/factusol · `commercial_status` = libre
(advisory new/qualified/won/lost).

### 2.3 Company (`companies`)

| key | label | type | source | sortable | defaultVisible | groupedUnder | notas |
|---|---|---|---|---|---|---|---|
| `name` | Nombre | string | column | ✓ | ✓ | Datos básicos | NOT NULL, indexed |
| `domain` | Dominio | string | column | ✓ | ✓ | Datos básicos | unique (dedupe key) |
| `tax_id` | CIF | string | column | ✓ | ✓ | Fiscal | ⚠ solapa con `vat` |
| `vat` | VAT | string | column | ✗ | ✗ | Fiscal | ⚠ solapa con `tax_id` |
| `website` | Web | string | column | ✗ | ✗ | Datos básicos | |
| `country` | País | string | column | ✓ | ✓ | Dirección | free-string ⚠ dup Contact.address_country |
| `region` | Región | string | column | ✗ | ✗ | Dirección | |
| `state` | Provincia | string | column | ✗ | ✗ | Dirección | len 200 (vs Contact 120) |
| `city` | Ciudad | string | column | ✓ | ✗ | Dirección | |
| `address_line` | Dirección | string | column | ✗ | ✗ | Dirección | |
| `postal_code` | CP | string | column | ✗ | ✗ | Dirección | |
| `sector` | Sector | string | column | ✓ | ✗ | Negocio | |
| `size_category` | Tamaño | string | column | ✓ | ✗ | Negocio | |
| `source` | Fuente | enum | column | ✓ | ✓ | Origen | manual/brevo/agilecrm/auto-domain |
| `is_active` | Activo | boolean | column | ✓ | ✗ | Sistema | |
| `contacts_count` | #Contactos | number | computed (COUNT) | ✓* | ✓ | Negocio | *sort vía subquery |
| `notes` | Notas | string | column | ✗ | ✗ | Datos adicionales | Text |
| `created_at` | Creada | datetime | column | ✓ | ✓ | Sistema | |
| `updated_at` | Actualizada | datetime | column | ✓ | ✓ | Sistema | |
| **custom_fields_json** | — | string/json | custom_fields_json | ✗ | ✗ | Datos adicionales | ⚠ **free-form, sin whitelist documentada** |
| **external_references_json** | — | json | custom_fields_json-style | ✗ | ✗ | Origen | |

### 2.4 EmailThread (`email_threads`)

| key | label | type | source | sortable | defaultVisible | groupedUnder | notas |
|---|---|---|---|---|---|---|---|
| `subject` | Asunto | string | column | ✓ | ✓ | Mensaje | |
| `contact_id` | Contacto | reference→contacts | column | ✗ | ✓ | Mensaje | |
| `state` | Estado | enum | column | ✓ | ✓ | Buzón | inbox/archived/trashed/spam |
| `is_starred` | Estrella | boolean | column | ✓ | ✓ | Buzón | |
| `has_unread_replies` | No leído | boolean | column | ✓ | ✓ | Buzón | |
| `folder_id` | Carpeta | reference→email_folders | column | ✗ | ✗ | Buzón | |
| `labels` | Etiquetas | tag-multi | related (email_labels) | ✗ | ✓ | Buzón | M:N |
| `message_count` | #Mensajes | number | column | ✓ | ✓ | Mensaje | |
| `first_message_at` | Primer mensaje | datetime | column | ✓ | ✗ | Fechas | |
| `last_message_at` | Último mensaje | datetime | column | ✓ | ✓ | Fechas | sort default DESC |
| `snooze_until` | Pospuesto hasta | datetime | column | ✓ | ✗ | Buzón | |
| `initiated_by_user_id` | Iniciado por | reference→users | column | ✗ | ✗ | Sistema | |
| `is_archived` | (legacy) | boolean | column | ✗ | ✗ | — | ⚠ shadow de `state==archived`, retirar |
| `created_at`/`updated_at` | — | datetime | column | ✓ | ✗ | Sistema | |

Campos de **EmailMessage** útiles para filtros derivados (vía join/EXISTS):
`direction` (outbound/inbound), `from_email`, `read_at` (NULL=no leído), `sent_at`,
`scheduled_status`. Eventos (`email_message_events.event_type`): sent/delivered/
open/click/bounce/complaint/unsubscribe.

### 2.5 BrevoTemplate (`brevo_templates_cache`) — tabla cache local

| key | label | type | source | sortable | defaultVisible | groupedUnder | notas |
|---|---|---|---|---|---|---|---|
| `name` | Nombre | string | column | ✓ | ✓ | Plantilla | NOT NULL |
| `subject` | Asunto | string | column | ✓ | ✓ | Plantilla | |
| `is_active` | Activa | boolean | column | ✓ | ✓ | Plantilla | |
| `tag` | Tag | string | column | ✓ | ✓ | Plantilla | |
| `sender_name` | Remitente | string | column | ✓ | ✓ | Remitente | |
| `sender_email` | Email remitente | string | column | ✗ | ✗ | Remitente | |
| `brevo_account_id` | Cuenta Brevo | reference | column | ✗ | ✗ | Sistema | |
| `created_at_brevo` | Creada (Brevo) | datetime | column | ✓ | ✓ | Fechas | |
| `modified_at_brevo` | Modificada (Brevo) | datetime | column | ✓ | ✗ | Fechas | |
| `cached_at` | Cacheada | datetime | column | ✓ | ✗ | Sistema | |
| `html_content` | HTML | string | column | ✗ | ✗ | — | LONGTEXT, lazy, no filtrable |

### 2.6 BrevoCampaign (`brevo_campaigns_cache`) — tabla cache local

| key | label | type | source | sortable | defaultVisible | groupedUnder | notas |
|---|---|---|---|---|---|---|---|
| `name` | Nombre | string | column | ✓ | ✓ | Campaña | |
| `subject` | Asunto | string | column | ✓ | ✓ | Campaña | |
| `status` | Estado | enum | column | ✓ | ✓ | Campaña | **free-string**: draft/sent/queued/suspended/archive |
| `type` | Tipo | enum | column | ✓ | ✗ | Campaña | classic/trigger/… |
| `sender_name` | Remitente | string | column | ✓ | ✗ | Remitente | |
| `sender_email` | Email remitente | string | column | ✗ | ✗ | Remitente | |
| `scheduled_at` | Programada | datetime | column | ✓ | ✓ | Fechas | |
| `sent_at` | Enviada | datetime | column | ✓ | ✓ | Fechas | |
| `created_at_brevo` | Creada (Brevo) | datetime | column | ✓ | ✗ | Fechas | |
| `template_id_used` | Plantilla usada | number | column | ✗ | ✗ | Campaña | |
| `recipient_list_ids` | Listas destino | json | json (`recipient_list_ids_json`) | ✗ | ✗ | Campaña | |
| **stats** (open%/click%…) | Métricas | number | json (`stats_json`) | ⚠ | ✓ | Métricas | ver riesgo §5 |

`stats_json` keys (`STAT_KEYS`): sent, delivered, uniqueViews, viewed, uniqueClicks,
clickers, hardBounces, softBounces, unsubscriptions, complaints, mirrorClick,
mobileOpen. **Para ordenar/filtrar por open%/CTR hay que materializar columnas o
usar JSON path — ver riesgo §5.**

### 2.7 Campos AMBIGUOS / problemáticos (decisiones requeridas)

1. **Dirección Contact vs Company** duplicada con longitudes y convenciones
   distintas (Contact `address_country` = ISO2; Company `country` = free-string).
   → **Decisión:** la lista de Contact muestra la dirección **del contacto**; añadir
   opcionalmente columnas `company.country` etc. como campos `reference`-derivados,
   claramente etiquetados "(empresa)". No fusionar.
2. **`tax_id` vs `vat`** en Company — semánticamente solapados. → exponer ambos pero
   marcar `vat` como secundario (no defaultVisible).
3. **Dos `CompanyRead`** (`schemas/companies.py` completo vs `schemas/crm.py` mínimo
   stale). → **PR-A debe eliminar el stale** y dejar uno.
4. **`commercial_status`, campaign `status`/`type`** son free-string, no enum. →
   tratarlos como `enum` con `enumValues` advisory + permitir valor libre en el filtro.
5. **`is_archived` (thread) vs `state==archived`** — bool legacy que sombrea el enum.
   → no exponer `is_archived`; retirar en PR de limpieza.
6. **`owner_user_id`** informal, NULL en importados, no en `ContactRead`. → exponerlo
   pero documentar que estará vacío en contactos importados; resolver poblándolo o
   mostrando "—".
7. **`Contact.tags` CSV (deprecado) vs `contact_tags` M:N.** → el schema usa SOLO la
   M:N (`tag_objects`); el CSV no se expone.
8. **`custom_fields` de Company sin whitelist** (free-form). → o se documenta una
   whitelist como en Contact, o se excluyen del filtro inicialmente (display-only).

---

## 3. Decisiones técnicas (con recomendación)

### 3.1 Persistencia de vistas guardadas

**Opciones:** (a) generalizar `contact_views` → `entity_views(entity_type, …)`;
(b) tabla por entidad.

**Recomendación: (a) tabla única `entity_views` con discriminador `entity_type`.**
- La estructura actual (`filters_json` + `columns_json` + `sort_json` + owner +
  `is_shared` + `is_default`) es ya genérica; solo falta la columna `entity_type`.
- Unicidad de default pasa a ser `(owner_user_id, entity_type)`.
- Migración: `ALTER TABLE contact_views ADD COLUMN entity_type VARCHAR(40) NOT NULL
  DEFAULT 'contact'`, luego renombrar a `entity_views` (o crear view/alias). Los
  endpoints `/api/contact-views` se mantienen como alias de
  `/api/entity-views?entity=contact` para no romper el frontend hasta PR-E.
- Evita N tablas casi idénticas y N repositorios.

### 3.2 Formato de filtro almacenado

**Opciones:** (a) guardar el JSON de react-querybuilder tal cual; (b) traducir a un
IR propio antes de persistir.

**Recomendación: (b) persistir el IR del motor que YA existe** (el árbol
`{operator, children}` / `{type:"rule", field, comparator, value}` de
`segments.rules_json` y `contact_views.filters_json.rules_json`).
- **Retro-compat crítica:** los segmentos del Sprint P.3 y las vistas existentes ya
  guardan este IR. Cambiar el formato rompería datos en producción.
- **Seguridad:** el backend nunca confía en SQL generado en cliente; compila el IR
  con su whitelist. (Ver 3.3.)
- El frontend traduce **RQB ⇄ IR** en el borde (`lib/segmentTranslator.ts` ya
  existe para esto). RQB es solo la capa de edición; el IR es la fuente de verdad.
- `formatQuery` de RQB se usa **solo para preview client-side** (mostrar el WHERE
  legible), nunca para ejecutar.

### 3.3 Traducción filtro → SQL en backend

**Opciones:** (a) builder Python que toma el árbol y emite SQLAlchemy; (b) reusar lo
de Sprint P.3.

**Recomendación: (b)+(a) = generalizar el motor existente.** `engine.py` ya hace
exactamente esto para Contact, con whitelist anti-inyección, 24 comparadores,
AND/OR/NOT, depth 10, y leaves de relación. El trabajo es **parametrizarlo por
entidad**:
- Sacar el `Contact` hardcoded a un `EntityRegistry` (`base_model`, `FIELD_SPECS`,
  dispatchers de join, `_true/_false`).
- Registrar `FIELD_SPECS` por entidad (Company, EmailThread, BrevoTemplate,
  BrevoCampaign) reusando los descriptores de §2.
- **NO** usar `formatQuery('sql')` de RQB en el backend (implicaría confiar en SQL
  de cliente → inyección; además es JS).

### 3.4 Framework de bulk actions

**Recomendación: componente genérico `<BulkActionsBar>`** con API declarativa, +
endpoint backend genérico.

```ts
// Frontend
<BulkActionsBar
  selection={selection}          // {mode:'ids', ids} | {mode:'filter', rules, total}
  actions={[
    { id:'assign_owner', label:'Asignar a…', icon:UserPlus,
      roles:['admin','manager'], render:'user-picker' },
    { id:'change_status', label:'Cambiar estado', icon:Tag, render:'status-picker' },
    { id:'deactivate', label:'Desactivar', icon:Trash, roles:['admin'],
      confirm:'¿Desactivar N contactos?' },
  ]}
  onRun={(actionId, payload) => runBulk(entity, selection, actionId, payload)}
/>
```

```python
# Backend — POST /api/{entity}/bulk-action
class BulkActionRequest(BaseModel):
    selection: IdsSelection | FilterSelection   # union discriminada
    action: str
    payload: dict = {}
# IdsSelection: {mode:'ids', ids:[...]}  (cap 1000, troceado en cliente)
# FilterSelection: {mode:'filter', rules: <IR tree>}  (set-based, SIN cap)
```

- Unifica los 3 surfaces actuales (`contacts/bulk-action`, `contacts/bulk-tag`
  duplicado, `emails/threads-bulk`).
- Mantiene role-gating + audit row + dispatch table por entidad.

### 3.5 Persistencia de columnas configurables

**Opciones:** localStorage vs tabla backend.

**Recomendación: dos niveles (como ya hace `/contacts`), sin tabla nueva.**
- **Por vista** → `entity_views.columns_json` (servidor, compartible, portable).
- **Default del usuario por entidad** → `localStorage` (`{entity}:default-columns`),
  rápido y sin migración.
- **NO** crear una tabla `user_preferences` solo para columnas. (Hoy no existe tal
  tabla — "UserPreferences" es solo 1 bool en `users`. No vale la pena montarla.)

### 3.6 Selección "todos los filtrados" (caso 19.000)

**Recomendación: bulk filter-native (set-based), no enumeración de ids.**
- Hoy: `search/ids` enumera y **trunca a 10.000** → para 19k es un footgun.
- Nuevo: para acciones expresables como UPDATE masivo (`assign_owner`,
  `change_status`, `add/remove_tag`, `deactivate`, archivar, etiquetar), el bulk
  acepta `selection.mode='filter'` con el árbol IR y ejecuta
  `UPDATE … WHERE build_filter(tree)` — **sin límite**, una sola sentencia.
- Para acciones que requieren trabajo por fila (raras), fallback a enumeración con
  paginación de servidor.
- Esto **sí se generaliza** a las 5 pantallas, y además **arregla** la sub-selección
  silenciosa actual.

### 3.7 react-querybuilder vs alternativas

**Recomendación: react-querybuilder.** (MIT, verificado vigente.)
- CSS propio **framework-agnóstico** (`.css`/`.scss`), **sin MUI/Tailwind** →
  encaja con nuestro CSS vanilla.
- Esquema declarativo `fields` mapea 1:1 con nuestro `FieldDescriptor`.
- Soporta **AND/OR/NOT + anidamiento arbitrario** → arregla el builder casero de 2
  niveles con pérdida.
- `formatQuery(query, 'sql'|'parameterized')` para **preview** client-side.
- **Ya existe `lib/segmentTranslator.ts` con traducción RQB↔IR** → intención previa,
  menor riesgo de integración.
- Alternativa **@react-awesome-query-builder/ui**: más potente pero más pesada e
  históricamente orientada a MUI/AntD; sus widgets "vanilla" existen pero es más
  trabajo. **No recomendada** para CSS vanilla.

### 3.8 TanStack Table vs alternativas

**Recomendación: TanStack Table v8.** (MIT confirmado vigente, ~15KB gzip,
headless.)
- **Headless** = sin estilos propios → traemos nuestro CSS vanilla, sin pelear con
  temas.
- Column visibility / ordering / sizing / **multi-sort** / row selection nativos.
- Integra con Next.js como client component sin fricción (~3M descargas/semana).
- **Para 19k filas: NO virtualizar — paginar en servidor.** Ya tenemos offset/limit;
  la tabla solo sostiene 1 página (25–100 filas). Virtualización (`@tanstack/virtual`)
  queda como opción futura si alguna pantalla necesita listas grandes en cliente
  (ninguna lo necesita hoy).
- Multi-sort: el backend hoy solo acepta `sort_by`/`sort_dir` (1 columna). Si
  queremos multi-columna real hay que extender el endpoint a `sort=[{by,dir},…]`
  (decisión menor; se puede diferir — empezar con 1 columna por paridad).

### 3.9 Tabla resumen de decisiones

| # | Decisión | Recomendación |
|---|---|---|
| 3.1 | Vistas | Tabla única `entity_views` + `entity_type` |
| 3.2 | Formato filtro | Persistir **IR del motor** (no RQB crudo); RQB solo edición |
| 3.3 | Filtro→SQL | Generalizar `engine.py` con `EntityRegistry` |
| 3.4 | Bulk | `<BulkActionsBar>` genérico + `POST /api/{entity}/bulk-action` |
| 3.5 | Columnas | Vista→DB (`columns_json`), default→localStorage; sin tabla nueva |
| 3.6 | Todos filtrados | Bulk **filter-native** set-based (sin cap 10k) |
| 3.7 | Query builder | **react-querybuilder** (MIT, CSS agnóstico, NOT+anidamiento) |
| 3.8 | Tabla | **TanStack Table v8** + paginación servidor (sin virtualizar) |

---

## 4. Plan de sub-PRs

> Orden pensado para que cada PR sea mergeable y verde por sí solo, y para que
> `/contacts` (el más complejo) sirva de canary antes de tocar las otras 4.

### PR-A — Fundación backend: esquema declarativo multi-entidad + generalización del motor
- **Alcance:** `EntityRegistry`; sacar `Contact` hardcoded de `engine.py`; registrar
  `FIELD_SPECS` para Company, EmailThread, BrevoTemplate, BrevoCampaign; extender el
  descriptor con `sortable/displayable/defaultVisible/groupedUnder/source`; endpoint
  `GET /api/{entity}/filter-schema`; tipos TS compartidos (`FieldDescriptor`,
  `Operator`). Eliminar el `CompanyRead` stale (§2.7.3).
- **NO entra:** UI nueva, RQB, TanStack, cambios de comportamiento en contacts.
- **Archivos (~8):** `app/services/entities/registry.py`, `…/fields_company.py`,
  `…/fields_email.py`, `…/fields_brevo.py`, refactor `engine.py`, `app/api/entities.py`,
  `frontend/src/app/lib/entitySchema.ts`, borrar `CompanyRead` de `schemas/crm.py`.
- **Migraciones:** ninguna.
- **Tests:** compile del motor por entidad; `filter-schema` por entidad; whitelist
  anti-inyección por entidad.
- **Verificación post-deploy:** `GET /api/companies/filter-schema` devuelve campos;
  `/api/contacts/search` sigue idéntico (regresión).
- **Estimación:** 3–4 h.

### PR-B — Endpoint de lista genérico + tabla `entity_views`
- **Alcance:** generalizar `contact_views`→`entity_views` (+`entity_type`, migrar
  datos); `POST /api/{entity}/search[/ids]` genérico (reusa `build_filter`); CRUD de
  vistas genérico; alias `/api/contact-views`→entity=contact. Normalizar envelope a
  `{items,total,limit,offset}` y param `offset` (deprecar `skip`).
- **NO entra:** UI; migración de pantallas.
- **Archivos (~7):** migración alembic, `app/models/crm.py` (modelo `EntityView`),
  `app/repositories/entity_views.py`, `app/api/entities.py` (search + views),
  `frontend/src/app/lib/entityViewsApi.ts`, ajustes de envelope.
- **Migraciones:** sí (add `entity_type`, rename, default uniqueness).
- **Tests:** views CRUD por entidad; default uniqueness por `(owner,entity)`; search
  paginado/ordenado por entidad; retro-compat de vistas contact existentes.
- **Verificación post-deploy:** vistas de contactos existentes siguen cargando;
  `POST /api/companies/search` con árbol filtra.
- **Estimación:** 3–4 h.

### PR-C — `<EntityTable>` (TanStack) + framework bulk + `<BulkActionsBar>`
- **Alcance:** componente `<EntityTable>` (show/hide/reorder columnas, multi-sort
  básico=1col, selección `Set`, paginación servidor) conectado al endpoint genérico;
  `<BulkActionsBar>` declarativo; backend `POST /api/{entity}/bulk-action` con
  selección por **ids o filtro** (set-based, §3.6). Hook `useSelection` compartido.
- **NO entra:** RQB (filtros aún por props simples); migración de pantallas reales.
- **Archivos (~9):** `components/entity/EntityTable.tsx`, `…/BulkActionsBar.tsx`,
  `…/ColumnConfigurator.tsx` (generalizado), `lib/useSelection.ts`,
  `lib/entityColumnsStorage.ts`, `app/api/entities.py` (bulk), backend bulk
  framework + dispatch por entidad, tests.
- **Migraciones:** ninguna.
- **Tests:** backend bulk por ids y por filtro (incl. caso "todos" set-based);
  role-gating; frontend render/selección (si hay infra de test de componentes).
- **Verificación post-deploy:** bulk assign sobre filtro de 19k no truncado.
- **Estimación:** 4–5 h.

### PR-D — `<EntityFilterBuilder>` (react-querybuilder) + traductor RQB⇄IR + UI de vistas
- **Alcance:** **proponer** dep `react-querybuilder` (no instalar hasta aprobación);
  `<EntityFilterBuilder>` alimentado por `filter-schema`; consolidar/ampliar
  `segmentTranslator.ts` (RQB⇄IR, soporta NOT + anidamiento); `<EntityViewsTabs>`
  generalizado; preview con `formatQuery`.
- **NO entra:** migración de pantallas (solo el componente + storybook/demo aislada).
- **Archivos (~6):** `components/entity/EntityFilterBuilder.tsx`,
  `…/EntityViewsTabs.tsx`, `lib/rqbTranslator.ts` (ex-segmentTranslator),
  `package.json` (propuesta documentada), CSS vanilla del builder, tests del
  traductor.
- **Migraciones:** ninguna.
- **Tests:** round-trip RQB→IR→RQB sin pérdida (incl. NOT, anidamiento ≥3);
  IR→preview SQL.
- **Verificación post-deploy:** n/a (componente aislado hasta PR-E).
- **Estimación:** 3–4 h.

### PR-E — Migrar `/contacts` al nuevo stack (CANARY)
- **Alcance:** sustituir el monolito `contacts/page.tsx` por `<EntityTable>` +
  `<EntityFilterBuilder>` + `<BulkActionsBar>` + `<EntityViewsTabs>`. **Arreglar
  deuda:** `assigned_to_me` en URL, botones `add/remove_tag` (reactivar o borrar),
  round-trip de reglas sin pérdida, selección "todos filtrados" set-based. Paridad
  funcional total.
- **NO entra:** otras pantallas.
- **Archivos (~5, mucho borrado):** reescribir `contacts/page.tsx`, retirar
  `ContactFiltersBuilder.tsx`/`ContactsBulkBar.tsx`/`contactColumns.ts` específicos,
  ajustar `contactsUrlState.ts`.
- **Migraciones:** ninguna.
- **Tests:** e2e/regresión de filtros, vistas, bulk, columnas en contactos.
- **Verificación post-deploy:** comparar resultados de un set de filtros guardados
  pre/post; "seleccionar 19k → cambiar estado" funciona sin truncar.
- **Estimación:** 4–5 h.

### PR-F — Migrar `/companies` (incluye DEUDA: bulk + sort + filtros URL)
- **Alcance:** `/companies` sobre el nuevo stack; **añade** bulk actions (las que
  apliquen: activar/desactivar, asignar sector, exportar), sort por cabecera,
  filtros en URL, query builder. Cierra la deuda de empresas.
- **Archivos (~3):** reescribir `companies/page.tsx`, `companiesApi.ts` (al endpoint
  genérico), backend dispatch bulk de company.
- **Migraciones:** ninguna.
- **Tests:** bulk company; filtros company por árbol.
- **Verificación post-deploy:** seleccionar+desactivar N empresas; ordenar por
  #contactos.
- **Estimación:** 2–3 h.

### PR-G — Migrar `/emails`
- **Alcance:** lista de hilos sobre `<EntityTable>` (mantener sidebar 3-paneles como
  toolbar custom: folders/labels/state siguen siendo nav, pero ejecutados como
  filtros del motor). **Arreglar** los 3 filtros caídos (`has_unread/since/until`),
  **añadir paginación**. Mapear `threads-bulk` al framework genérico.
- **Archivos (~5):** `EmailThreadList.tsx` (sobre EntityTable), `EmailFiltersBar` →
  filtros del motor, `emailsApi.ts`, backend dispatch bulk de email_thread.
- **Migraciones:** ninguna.
- **Tests:** filtros email que antes se caían; paginación.
- **Verificación post-deploy:** filtrar "no leídos del último mes" devuelve subset
  correcto; paginar bandeja grande.
- **Estimación:** 3–4 h.

### PR-H — Migrar `/marketing/templates` + `/marketing/campaigns` + limpieza + docs
- **Alcance:** ambas listas Brevo sobre el nuevo stack con **filtro/sort/paginación
  en servidor** sobre las tablas cache (`brevo_*_cache`); mantener el botón
  "Refrescar" (cache-bust) como acción custom de toolbar. **Limpieza:** retirar
  `_apply_contact_filters` + params planos de `GET /contacts`, código muerto,
  `is_archived`, segundo `CompanyRead`. Docs: actualizar
  `docs/integrations-architecture.md` + README.
- **Migraciones:** ninguna (o `DROP COLUMN is_archived` si se decide).
- **Tests:** filtro server-side de templates/campaigns; (campaigns `status` ahora en
  servidor); ausencia de regresión tras retirar params planos.
- **Verificación post-deploy:** `GET /api/brevo/campaigns/search?status=sent` filtra
  en servidor; templates paginan.
- **Estimación:** 3–4 h.

**Total estimado:** ~25–32 h (el brief estimaba 15–25 h; la generalización del motor
+ bulk filter-native + arreglo de deuda lo empujan al alto del rango).

---

## 5. Riesgos identificados

1. **Filtrado sobre JSON (`custom_fields_json`, `stats_json`).** Filtrar por
   `GRADO_DE_INTERES` (Contact) o por open%/CTR (Campaign) requiere `JSON_EXTRACT`
   en MySQL 8 — el motor actual no toca JSON. **Mitigación:** (a) materializar las
   métricas de campaña más usadas (open%, click%) a columnas reales en el refresh
   del cache; (b) para custom_fields, soportar un leaf `json_path` acotado a la
   whitelist. Empezar **display-only** y habilitar filtro en una iteración posterior.
2. **Multi-sort real** no está en el backend (solo `sort_by`/`sort_dir`). TanStack lo
   soporta en UI pero el endpoint debe extenderse a `sort=[{by,dir},…]`.
   **Mitigación:** arrancar con 1 columna (paridad) y diferir multi-sort.
3. **Pérdida en round-trip de reglas existentes.** El builder casero aplana NOT y
   anidamiento ≥3; algunas vistas/segmentos guardados podrían tener árboles que la
   UI vieja nunca mostró bien. El **motor ya los soporta**, y RQB también, así que la
   migración los **recupera** — pero hay que verificar que `rqbTranslator` importa el
   IR fielmente (test explícito en PR-D).
4. **No romper Sprint P.3 (segmentación).** Reusamos el motor + IR sin cambiar el
   formato; `save-as-segment` y `in_segment` siguen. **Riesgo solo si se altera el
   IR** → regla dura: **extender, nunca renombrar comparadores/estructura**.
5. **Features únicas por pantalla que no encajan en lo genérico:**
   - `/emails`: el shell de 3 paneles (sidebar folders/labels/state como navegación
     primaria) es UX bespoke. **Mitigación:** `<EntityTable>` con slots de toolbar
     custom; folders/labels/state se ejecutan como filtros del motor pero se
     presentan como nav. No forzar el query builder visible ahí por defecto.
   - `/marketing/*`: botón "Refrescar" (cache-bust contra API Brevo) y el patrón
     "resolver cuenta Brevo primaria" no son filtros → acción custom de toolbar.
   - Contactos: push a lista Brevo + save-as-segment son acciones de **vista**, no de
     selección → mantener en menú "Acciones".
6. **Campos free-string tratados como enum** (`commercial_status`, campaign
   `status`): el filtro debe permitir valor libre además de las opciones advisory, o
   se pierden filas con valores no estándar.
7. **`owner_user_id` vacío en importados:** una columna "Propietario" saldría vacía
   para la mayoría del histórico → confusión. Decidir poblar o mostrar "—" + tooltip.
8. **Coste de migración del monolito `/contacts`** (~1050 líneas con varios
   `exhaustive-deps` disabled y refetch por mutación de identidad). Es el PR más
   arriesgado; por eso va de canary (PR-E) tras tener los componentes probados (A–D).
9. **Dependencias nuevas (bundle/seguridad):** TanStack Table (~15KB) +
   react-querybuilder (+CSS). Ambas MIT. **No instalar hasta que Bart apruebe** este
   spec; PR-D documenta la propuesta de `package.json` pero no la mete sin OK.

---

## 6. Apéndice — assets reutilizables ya en el repo

| Asset | Ubicación | Reuso |
|---|---|---|
| Motor de filtros AND/OR/NOT → SQLAlchemy | `app/services/segments/engine.py` | Generalizar (PR-A) |
| Whitelist de campos / `list_fields_for_ui()` | `app/services/segments/fields.py` | Base del schema (PR-A) |
| IR de reglas (formato persistido) | `segments.rules_json`, `contact_views.filters_json` | Mantener tal cual (3.2) |
| Vistas guardadas (3 blobs JSON + owner/share/default) | `contact_views` + `app/repositories/contact_views.py` | Generalizar a `entity_views` (PR-B) |
| "Todos los filtrados" (id enumeration) | `POST /api/contacts/search/ids` | Sustituir por filter-native (PR-C/3.6) |
| Bulk request shape `{ids, action, payload}` + audit | `app/api/bulk.py` | Plantilla del framework (PR-C) |
| Query builder de árbol (2 niveles, con pérdida) | `components/ContactFiltersBuilder.tsx` | Sustituir por RQB (PR-D) |
| Traductor RQB ⇄ IR | `lib/segmentTranslator.ts` | Ampliar (PR-D) |
| Columnas configurables (registry + configurator + storage) | `lib/contactColumns.ts`, `components/ColumnConfigurator.tsx`, `lib/contactColumnsStorage.ts` | Generalizar (PR-C) |
| `<Pagination>` compartido | `components/Pagination.tsx` | Reusar en EntityTable |
| URL state (serialize/deserialize) | `lib/contactsUrlState.ts` | Generalizar (PR-E) |

## 7. Decisiones aprobadas por Bart (2026-06-15)

Resueltas tras la entrega del spec — quedan bloqueadas para la implementación:

1. **Vistas guardadas:** ✅ **tabla única `entity_views`** con discriminador
   `entity_type` (no tabla por entidad). → PR-B.
2. **Filtrar por JSON (métricas de campaña open%/CTR + `custom_fields`):** ✅
   **diferido — display-only en v1.** Se muestran como columnas pero sin filtro;
   `JSON_EXTRACT`/materialización de columnas queda para una iteración posterior.
   → sale del alcance de PR-A…H salvo como columnas display.
3. **Multi-sort:** ✅ **diferido — 1 columna primero** (paridad con
   `sort_by`/`sort_dir`). TanStack lo soporta en UI; el backend se extiende a
   `sort=[{by,dir}]` en otra iteración. → afecta §3.8 y los endpoints de PR-B.
4. **Dependencias (TanStack Table v8 + react-querybuilder, ambas MIT):** ✅
   **aprobadas**; se instalan en su PR (D) sin necesidad de re-confirmar.

**Pendiente de decidir (no bloquea el arranque; se resuelve en PR-A):**
tratamiento de campos ambiguos §2.7 — dirección Contact vs Company, `tax_id`/`vat`,
`owner_user_id` vacío en importados. Recomendaciones ya propuestas en §2.7.
