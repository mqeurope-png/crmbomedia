# Módulo /marketing — guía del operador

Campañas y plantillas de email de Brevo gestionadas desde el CRM. El
flujo completo (crear plantilla → segmentar → programar → medir) vive
en el CRM; Brevo nativo queda para dos cosas: **editar HTML
visualmente** y **verificar senders**.

Requisito: una cuenta Brevo configurada y habilitada en
`/admin/integrations` con su API key.

## Flujo típico

### 1. Prepara la plantilla (`/marketing/templates`)

- "+ Nueva plantilla": nombre, asunto, sender (dropdown de senders
  verificados en Brevo), etiqueta opcional, y el HTML en el textarea
  con vista previa en vivo a la derecha.
- El editor es texto plano **a propósito** (sin WYSIWYG): pega aquí
  el HTML generado en tu herramienta favorita. Para edición visual,
  botón "Abrir plantillas en Brevo".
- "Enviar test a…" manda la plantilla a 1-3 emails.
- "Refrescar" re-espeja el catálogo si alguien tocó plantillas en
  Brevo directamente.

### 2. Crea la campaña (`/marketing/campaigns` → "+ Nueva campaña")

Wizard de 5 pasos (todos revisitables hasta confirmar):

1. **Básico** — nombre interno, asunto, sender, reply-to.
2. **Contenido** — desde plantilla (carga HTML/asunto/sender por
   defecto, editables) o desde cero (textarea + preview).
3. **Destinatarios** — desde **segmento del CRM** (muestra cuántos
   contactos cumplen; al confirmar se crea una lista Brevo
   `crm-campaign-{timestamp}` con ellos) o desde una **lista Brevo
   existente**.
4. **Programación** — enviar ahora / programar (mínimo +1h) /
   borrador.
5. **Revisión** — resumen y Confirmar. "Enviar prueba a…" crea el
   borrador, manda el test y te lleva al detalle.

### 3. Mide (`/marketing/campaigns/[id]`)

- Cards: enviados, entregados, abiertos (OR%), clicks (CTR%),
  rebotes, bajas, spam — del cache de stats de Brevo (se refresca
  solo cada 15 min, o al abrir el detalle si lleva >5 min).
- Gráfico de aperturas/clicks por día y tabla de URLs más
  clickeadas — alimentados por los **webhooks** (configúralos:
  ver `integrations-brevo.md` § "Configurar el webhook").
- Tabs de destinatarios por evento (Entregados / Abiertos / Clicks /
  Rebotes / Bajas) con enlace a la ficha de cada contacto.
- Acciones según estado: borrador → editar/test/programar/enviar/
  borrar; programada → enviar ya / cancelar programación; enviada →
  solo lectura + "Abrir en Brevo".

## Sync continuo con sync targets

Para audiencias permanentes (no por campaña), crea un **sync target**
en `/admin/integrations` → card Brevo → "Sync targets": segmento del
CRM → lista Brevo, con auto-sync cada N minutos. Quien entra al
segmento se empuja; quien sale, se quita de la lista (nunca se borra
de Brevo). El botón "Probar (dry-run)" enseña qué haría sin escribir.

## Listas Brevo desde el CRM (`/marketing/listas`)

Las listas de Brevo se gestionan **completas** desde el CRM sin tener
que entrar a Brevo: ver, crear, renombrar, borrar, y ver/quitar
contactos. La cuenta Brevo es la activa (`resolvePrimaryBrevoAccount`);
los endpoints son proxy puro al `/contacts/lists` de Brevo, sin cache
local.

- **Lista de listas** — buscador por nombre + counters
  (suscriptores totales / únicos / blacklist / folder). Click en una
  fila abre el detalle. "+ Nueva lista" (modal con campo nombre)
  llama `POST /api/brevo/lists`. Roles: admin/manager.
- **Detalle de una lista** — header con nombre + acciones
  "Renombrar" / "Borrar" (manager-only). Dos stat cards. Tabla
  paginada de subscribers; los emails conocidos en el CRM linkan a
  `/contacts/[id]` con su nombre, los desconocidos aparecen como "no
  está en el CRM" (consistente con la política de webhooks: el CRM
  nunca crea contactos por su cuenta). Cada fila tiene un botón
  "Quitar" que pide confirmación y llama
  `POST /api/brevo/lists/{id}/contacts/remove`.

Para **añadir contactos en bloque** a una lista (sea existente o
nueva), el flujo recomendado es desde una vista guardada en
`/contacts` → botón "Enviar a lista Brevo": el backend resuelve los
contactos del filtro, crea un segmento auxiliar y un `BrevoSyncTarget`
push-only, y encola el job (`brevo:push_target`). El resultado del
endpoint trae `sync_log_id` para seguir el progreso desde
`/admin/integrations`.

### Endpoints `/api/brevo/lists/*`

| Método | Path | Rol | Qué hace |
|---|---|---|---|
| GET | `/lists?account_id=` | user+ | Listado (proxy a Brevo). |
| GET | `/lists/{id}?account_id=` | user+ | Detalle con counters. 404 si Brevo devuelve vacío. |
| POST | `/lists?account_id=` | manager+ | Crear (re-fetch detalle al final para devolver counters). |
| PATCH | `/lists/{id}?account_id=` | manager+ | Renombrar / re-folder. Body vacío → 400. |
| DELETE | `/lists/{id}?account_id=` | manager+ | Borra la lista en Brevo (los contactos no se borran). |
| GET | `/lists/{id}/contacts?account_id=` | user+ | Paginated (limit≤500). Mapea `email`→`contact_id` del CRM (case-insensitive). |
| POST | `/lists/{id}/contacts/add?account_id=` | manager+ | Body `{emails?, contact_ids?}`. Resuelve, deduplica, lowercases. Batches de 100 contra Brevo. |
| POST | `/lists/{id}/contacts/remove?account_id=` | manager+ | Igual que add. |

Cuando se pasa `contact_ids`, los unknown se cuentan como
`skipped_unknown_contact`; los contactos sin email se cuentan como
`skipped_missing_email`. Ninguno crashea la petición.

## Limitaciones conocidas

- **Sin editor WYSIWYG**: HTML en texto plano + preview. Edición
  visual → Brevo nativo.
- **Sin A/B testing** ni campañas RSS/cadenas — fuera de alcance.
- **Sin emails transaccionales 1-a-1** desde el CRM (Sprint C).
- **Sin automatizaciones** disparadas por eventos (ej. "si abre,
  mover de etapa") — Sprint E.
- **Una cuenta Brevo primaria** en la UI (el modelo de datos ya
  soporta varias; la UI se ampliará si llega el caso).
- Si no hay **senders verificados** en Brevo, el wizard lo avisa y
  enlaza a Brevo Senders — verifica al menos uno antes de crear
  campañas.

## Historial de eventos pre-webhook

El detalle de una campaña enviada antes de que el webhook estuviera
configurado muestra los agregados (OR%, CTR%) pero las pestañas
"Destinatarios por evento" y la sección "Actividad email" de cada
ficha de contacto pueden venir vacías para esa campaña.

Para rellenarlas: `/admin/integrations` → expande Brevo →
sección **"Historial de eventos (backfill)"** → "Lanzar backfill
histórico". La operación tarda **~40-60 minutos** para una cuenta
con ~60 campañas (Brevo expone el historial sólo vía export
asíncrono, un job por campaña). Cada evento lleva su timestamp real
del CSV de Brevo (entrega, apertura, baja, rebote, queja). Ver
`integrations-brevo.md` § "Backfill histórico de eventos" para
detalles del flujo, idempotencia y limitaciones.

Tras el backfill, los eventos `email.*` con `metadata.campaign_name`
en el activity event muestran el nombre de la campaña en la timeline
del contacto, en lugar del asunto de la campaña.
