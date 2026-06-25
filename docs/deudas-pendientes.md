# Deudas pendientes

Sprints futuros para los que el diseño NO está cerrado todavía. Cada
sección es un proyecto en sí mismo — no implementar nada sin pasar
por una ronda de diseño con Bart primero.

## Generador de formularios web

**Estado**: pendiente diseño.

**Origen**: PR-Revert-Webhooks-Agile descartó pagar el plan Enterprise
de AgileCRM (única vía para recibir webhooks salientes). La frescura
del polling se redujo a 1 h pero el lead sigue pasando por Agile antes
de llegar a BoHub. Esta deuda persigue capturar leads sin Agile en
medio.

**Objetivo**: Bart quiere generar formularios embedables para poner en
todas las webs del grupo Bomedia (bomedia.net, mbolasers.com,
artisjet-europe.com, fluxlasers.es, etc.) que capturen leads
directamente en BoHub CRM SIN pasar por AgileCRM.

**Beneficio**: independencia de Agile + leads en CRM al instante
(real-time real, no polling) + dispara motor de reglas + auto-asignación
al comercial correcto.

**Por definir antes de implementar**:

- Builder visual de formularios en `/admin/forms` (drag-drop campos:
  nombre, email, teléfono, custom fields).
- Snippet embed JS para pegar en cualquier web
  (`<script src="https://bo-crm.mbolasers.com/forms/{form_id}.js"></script>`).
- Mapeo campos formulario → campos contacto CRM.
- Asociación formulario × cuenta Agile equivalente para mantener owner
  por web (formulario en mbolasers.com → owner equipo MBO).
- Anti-spam: honeypot field + reCAPTCHA opcional + rate limit por IP.
- Estilos: temas predefinidos + CSS override por usuario.
- Notificaciones: email al comercial + Slack opcional cuando llega un
  lead.
- Tracking: UTM parameters guardados como custom fields del contacto.
- Analytics: conversion rate por formulario en dashboard CRM.

**Sprint estimado**: ~30-50 h. Backend (modelo Form + FormSubmission +
endpoint público de submit + endpoint embed JS) + Frontend builder
(admin) + frontend embed script (deploy a CDN o servir desde mismo
CRM).

## Real-time Gmail ↔ contactos CRM (sucesor del backfill histórico)

**Estado**: pendiente diseño.

**Origen**: Sprint-Backfill-Gmail cargó 3 años de conversaciones a
demanda del admin. La pieza que falta es ingestar conversaciones
NUEVAS en tiempo real cuando llega un email entre un alias de un
comercial y un contacto YA existente en el CRM.

**Diferencia con `gmail:process_history` actual**: el webhook existente
solo procesa el INBOX del comercial cuando llega un email desde el
exterior. Esta deuda extiende la cobertura a TODAS las conversaciones
con contactos del CRM, en cualquier dirección, sin importar la
carpeta Gmail.

**Decisión congelada en el backfill** (Bart): NO auto-crear contactos
nuevos si llega un email de un remitente no conocido. La conversación
solo se importa cuando el otro extremo ya existe como contacto en el
CRM.

**Por definir antes de implementar**:

- Gmail Push Notifications + Cloud Pub/Sub: hay setup parcial vía
  `register_watch()` pero la integración fin-a-fin con un topic Pub/Sub
  del proyecto Google Cloud no está cerrada en prod.
- Filtro: pre-comprobar si from/to coincide con un contacto del CRM
  antes de persistir; si no, ignorar el evento del history.
- Coordinación con el `imported_via='incoming_realtime'` que el
  backfill ya etiqueta como tipo separado en `email_messages`.
- ¿Cómo lidiamos con conversaciones que el operador inicia DESPUÉS del
  backfill pero ANTES de que el webhook esté armado? Periodic small
  catch-up (últimas 24h cada N min) como red de seguridad.

**Sprint estimado**: ~20-30 h. Backend (handler nuevo del Pub/Sub +
filtro contacto-existente + reuso del `_persist_inbound` y
`_persist_outbound` ya factorizados en `service.py`).
