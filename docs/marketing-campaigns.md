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
histórico". Ver `integrations-brevo.md` § "Backfill histórico de
eventos" para detalles de cuándo lanzarlo, idempotencia y
limitaciones.

Tras el backfill, los eventos `email.*` con `metadata.campaign_name`
en el activity event muestran el nombre de la campaña en la timeline
del contacto, en lugar del asunto de la campaña.
