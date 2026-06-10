# Pipelines

Pipelines son flujos de gestión de contactos al estilo Pipedrive /
HubSpot. Un pipeline tiene **etapas ordenadas**; un contacto puede
estar en varios pipelines a la vez y, dentro de cada uno, ocupa
exactamente una etapa.

## Modelo conceptual

Tablas (Sprint P.2 PR-A, migración `20260525_0017`):

| Tabla | Propósito |
|---|---|
| `pipelines` | El flujo nombrado (Ventas, Reactivación, Onboarding). `is_active=false` es soft-delete. `is_shared=true` por defecto — los pipelines son por defecto a nivel organización. |
| `pipeline_stages` | Pasos ordenados de un pipeline. `position` se mantiene contiguo 0..N-1 vía repository (insert con shift, reorder con full-set, delete con renormalize). `is_won` / `is_lost` marcan etapas terminales para reportes. `target_days` es el SLA visual usado para flag de "estancado". |
| `contact_pipeline_stages` | "Contacto C está en etapa S de pipeline P". UNIQUE `(contact_id, pipeline_id)` garantiza una sola etapa por contacto por pipeline. `is_archived` saca del kanban sin perder histórico. |
| `contact_stage_history` | Una fila por transición. Alta inicial = `from_stage_id=NULL`. Sirve para reportes (avg time per stage, conversion). |

## Casos de uso

- **Ventas**: Nuevo lead → Contactado → Cualificado → Propuesta → Negociación → Ganado / Perdido.
- **Reactivación clientes inactivos**: Identificado → Email enviado → Contestó → Reactivado / No reactivado.
- **Onboarding**: Bienvenida → Demo → Setup → Activo.

El mismo contacto puede vivir en los 3 simultáneamente; cada
ContactPipelineStage es independiente.

## API

### Pipelines

- `GET /api/pipelines` — lista (default sólo activos; `include_inactive=true` los muestra todos).
- `GET /api/pipelines/{id}` — detalle con sus etapas + `contact_count`.
- `POST /api/pipelines` — crear. Body opcional `stages: [...]` para crear pipeline + etapas iniciales en una llamada.
- `PATCH /api/pipelines/{id}` — editar metadatos.
- `DELETE /api/pipelines/{id}` — soft delete.
- `POST /api/pipelines/{id}/duplicate` — clona pipeline + etapas. Body opcional `{name, include_contacts}` (los contactos se traen con su etapa actual; la historia NO se copia).

### Etapas

- `POST /api/pipelines/{id}/stages` — añadir. `position` opcional inserta y desplaza el resto.
- `PATCH /api/pipeline-stages/{id}` — editar.
- `DELETE /api/pipeline-stages/{id}` — borrar. Si tiene contactos, requiere `?move_to_stage_id=...` para reubicarlos (sino 400).
- `POST /api/pipelines/{id}/stages/reorder` — body `{stage_ids: [...]}` con el conjunto completo permutado. Si falta alguno: 400.

### Contactos en pipelines

- `POST /api/contacts/{id}/pipelines` — añadir contacto a un pipeline. `stage_id` opcional (default = position 0).
- `PATCH /api/contact-pipeline-stages/{id}` — mover de etapa. Escribe historia automáticamente. Move-to-same-stage es no-op (no genera fila para que un doble-click no contamine reports).
- `DELETE /api/contact-pipeline-stages/{id}` — soft delete (`is_archived=true`).
- `GET /api/pipelines/{id}/contacts` — agrupado por etapa, paginado por etapa con `per_stage_limit`. Devuelve cards compactas con email, lead_score, tags y `days_in_stage`.
- `GET /api/pipelines/{id}/report` — métricas básicas por etapa (count, avg time, conversion to next, stalled).

### Audit events

`pipeline.created`, `pipeline.updated`, `pipeline.deleted`,
`pipeline.duplicated`, `pipeline_stage.created`,
`pipeline_stage.updated`, `pipeline_stage.deleted`,
`pipeline_stage.reordered`, `contact_pipeline_stage.added`,
`contact_pipeline_stage.stage_changed`,
`contact_pipeline_stage.archived`.

## Próximas extensiones

- **PR-B (próximo)**: Vista kanban en `/pipelines/{id}` con drag-and-drop entre columnas + integración en `/contacts/{id}`.
- **PR-C**: Pantalla de reportes `/pipelines/{id}/report` con gráfico de barras + lista de estancados.
- **Sprint E**: Automatizaciones al cambiar de etapa (enviar email, asignar tag, crear tarea).
- **Sprint P.3**: Segmentación dinámica desde un pipeline.

## Plantillas y generación IA (Sprint P.2.5)

El wizard `/pipelines → + Nuevo pipeline` ofrece tres caminos.

### Plantillas pre-hechas

`app/services/pipeline_templates.py` mantiene una biblioteca **hardcoded**
de 7 plantillas (ventas B2B/B2C, onboarding, reactivación, soporte,
renovaciones, RRHH). Son contenido de producto que viaja con el
release — no datos de usuario — por eso se versionan en código.

- `GET /api/pipeline-templates` lista todas.
- `POST /api/pipelines/from-template` con `{template_id, name?}`
  instancia el pipeline con sus etapas + colores + target_days en una
  sola llamada. Audit `pipeline.created` con `source="template"`.

### Generación con IA

`POST /api/pipelines/generate-ai` con `{description: "..."}` pasa la
descripción a Claude (Anthropic) y devuelve una propuesta en JSON
**sin persistir nada**. El operador la revisa en el wizard y decide:

- "Crear directamente" → POST normal a `/pipelines`.
- "Regenerar" → vuelve a llamar.
- "Editar antes de crear" → carga la propuesta en el form.

Garantías de privacidad y coste:

- API key vive solo en backend (`ANTHROPIC_API_KEY`). El frontend lee
  el flag `ai_features_enabled` desde `/api/health` para decidir si
  pinta el CTA. Cuando la key no está, el endpoint 503 y el flag es
  false.
- **El prompt nunca se persiste**. El audit log
  `pipeline.ai_generated` carga `description_length` +
  `stages_proposed` — nada más. La descripción puede incluir nombres
  de clientes, productos, mercados… nada de eso entra en BD ni en
  logs.
- **Rate limit** local: 5 generaciones/hora/user. Sliding window en
  memoria por proceso. Para deploys multi-worker hay que subirlo a
  Redis cuando dé problemas.
- **El operador siempre revisa**. La IA propone, el operador decide;
  no hay "generate-and-save" en un paso. Patrón replicable cuando
  llegue el Sprint AI (cualificación automática).

Modelo: `claude-sonnet-4-6` (override con `ANTHROPIC_MODEL`). Max
2000 tokens output. La respuesta se normaliza (entre 4 y 8 etapas, la
penúltima debe ser `is_won`, la última `is_lost`).
