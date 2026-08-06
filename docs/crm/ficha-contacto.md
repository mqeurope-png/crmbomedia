# Ficha del contacto — pestañas

`/contacts/[id]`. La ficha tiene una barra de pestañas; la primera («Resumen»)
es la que abre por defecto.

| Pestaña | Qué muestra |
|---|---|
| **Resumen** | Actividad reciente, engagement por email, tareas/notas/tags recientes |
| **Emails** | Hilos de correo del contacto |
| **Tareas** | Tareas asociadas |
| **Notas** | Notas del contacto (incluye las que nacen de una llamada, CRM-1) |
| **Historial** | Timeline unificado: emails, notas, tareas, **llamadas**, cambios de estado, workflow, brevo, lifecycle |
| **Tags** | Etiquetas |
| **Pipelines** | Pipelines/oportunidades del contacto |
| **Workflows** | Workflows en los que está |
| **Soporte** | Incidencias (Freshdesk, pendiente) |

---

## CRM-2 — cambios

**Se retiró la pestaña «Actividad».** Era redundante con **«Historial»**: leía
`activity_events`, mientras que el Historial es el timeline unificado
(`GET /contacts/{id}/timeline`) que ya cubre **todos** los tipos de evento
—emails, notas, tareas, llamadas, cambios de estado, workflow, brevo,
lifecycle— y más. El enlace «Ver toda la actividad» del card de Resumen ahora
lleva a **Historial**.

**«Oportunidades» → «Pipelines».** La pestaña se llamaba «Oportunidades» pero el
menú lateral y el resto del CRM dicen «Pipelines». Se renombra la **etiqueta
visible** (pestaña + el card placeholder «Pipelines vinculados» del Resumen). El
`id` interno de la pestaña sigue siendo `opportunities` y la entidad
`Opportunity` (modelo, tabla `opportunities`, endpoints `/api/opportunities/…`)
**no cambia** — es solo cosmética.

> **Qué NO se renombró.** Las métricas de negocio del dashboard y del onboarding
> que hablan de «Oportunidades» como *deals* —«Oportunidades activas»,
> «Oportunidades calientes», «Oportunidades ganadas»— y las etiquetas de eventos
> de workflow (`opportunity.won` → «Oportunidad ganada») se mantienen: ahí
> «Pipelines» sería semánticamente incorrecto (no se «gana un pipeline»). El
> rename es coherencia de **navegación de la ficha**, no del vocabulario de
> deals.
