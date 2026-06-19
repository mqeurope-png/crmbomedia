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
