# Respuesta a leads — Fase 1 (clasificar, preparar, colocar, avisar)

Bomedia se quedó sin comerciales activos y los leads entraban sin que nadie los
contestara. La Fase 1 **no envía ni un correo al cliente**: clasifica el lead,
deja un borrador preparado, lo coloca en el pipeline y avisa a una persona. El
envío automático es la Fase 2 y se enciende cuando la clasificación haya
demostrado acertar sobre leads reales (modo en seco + lista corregible).

> El cliente ya recibe un **acuse de recibo** inmediato al rellenar un
> formulario (desde la dirección de su web). El correo que prepara esto es el
> **segundo** contacto: va al grano, con la gama y los precios.

## Arquitectura

- **El clasificador es un servicio** (`backend/app/services/leads/`): entra la
  consulta con su contexto y sale idioma, interés, spam y confianza. Aislado
  detrás de una interfaz, auditable (`lead_classifications`) y con el
  proveedor intercambiable (IA o palabras clave).
- **Todo lo demás es un workflow** del motor que ya existía
  (`backend/app/workflows/`): la espera con ventana horaria, elegir plantilla,
  dejar el borrador, colocar en el pipeline y crear la tarea. Bart ve y cambia
  el flujo desde la pantalla de Workflows, sin un PR para mover un plazo.

## El trigger `lead.received`

Lo producen dos sitios, **después** de comitear el lead y best-effort (un fallo
del despacho no tumba la captura):

| Productor | Cuándo | Payload |
|---|---|---|
| Formulario web (`services/web_forms/submit.py`) | envío que NO es spam | `source=web_form`, `source_ref` (id del envío), `lead_at`, `form_slug`, `site` (clave del slug), `form_language`, `products` (etiquetas marcadas), `text` (la consulta), `country`, `email` |
| Nota «form note» de AgileCRM (`integrations/agilecrm/refresh.py`) | nota NUEVA que empieza por `form note` al refrescar el contacto | `source=agilecrm`, `source_ref` (id de la nota), `lead_at` = **`external_created_at`** (la fecha real, no la de sincronización), `agile_account_id`, `text` |

Config del trigger (`trigger_definitions.py`): `source` (web_form / agilecrm),
`site` y `max_age_hours` (72 por defecto; 0 = sin límite). El matcher descarta
por la fecha real del lead: una nota de julio sincronizada en septiembre no
dispara nada. El estimador cuenta envíos no spam + notas «form note» de 30 días.

## Los pasos nuevos del motor

| Paso | Qué hace | Config |
|---|---|---|
| `action_classify_lead` | Llama al clasificador y guarda el resultado en `lead_classifications` y en el contacto (`lead_interest`, `lead_is_spam`, `lead_confidence`, `lead_classified_at`). Ramas: **`ok`**, **`spam`**, **`omitido`** (lead sin consulta, demasiado antiguo o ya procesado); sin ramas conectadas sigue por `default`. Un lead se clasifica UNA vez: el segundo paso reutiliza la fila y, si ya se le preparó algo, sale por `omitido`. | `max_age_hours` (72) |
| `action_prepare_email_draft` | Compone el correo con su plantilla y su remitente y lo deja **guardado como borrador** (`email_drafts`, Bandeja → Borradores), sin mandarlo. Plantilla por interés × idioma (o fija); remitente de la web del lead (o fijo); dueño del borrador: el comercial del lead, el usuario fijo del paso o el primer administrador. Sin plantilla (otro, consumibles, servicio técnico, repuestos o un interés sin contenido): borrador vacío y aviso. Una vez por lead: con borrador ya preparado, o con un correo saliente posterior al lead que no sea el acuse, se salta. | `template_mode` (`por_interes` / `fija`), `template_id`, `from_mode` (`web_del_lead` / `fijo`), `from_alias`, `from_name`, `owner_mode` (`propietario` / `usuario`), `user_id`, `subject_override` |
| `action_add_to_pipeline` | Coloca el contacto en un pipeline y una etapa (Ventas B2B → Nuevo lead). Escribe `contact_stage_history` por el repositorio, como la ficha. Si ya está en ese pipeline no lo mueve. | `pipeline_id`, `stage_id` |
| `action_move_opportunity_stage` | **Arreglado**: «Mover contacto de etapa». El modelo real son contactos en pipelines (`contact_pipeline_stages`), no oportunidades. Pipeline y etapa explícitos; escribe `contact_stage_history` con origen, destino y fecha. Antes movía la fila más reciente del contacto, de cualquier pipeline, sin rastro. Si el contacto no está en ese pipeline se salta. | `pipeline_id`, `stage_id` |
| `wait_time` | Admite una **ventana horaria**: el despertar se mueve al siguiente hueco (hora de Madrid). | `window: {enabled, start, end, weekdays_only}` |

Las condiciones y el `switch` ven los campos nuevos del contacto:
`contact.language`, `contact.lead_interest`, `contact.lead_is_spam`,
`contact.lead_confidence`, `contact.lead_classified_at`. Las plantillas de tarea
y de correo tienen el namespace `lead.*`: `interes`, `interes_texto`, `idioma`,
`confianza` («95%»), `motivo`, `web`, `plantilla`, `remitente`, `borrador_url`,
`productos`.

## El clasificador

Entrada: la consulta, el país del contacto, la web y el idioma del formulario,
los productos marcados, la cuenta de Agile y el dominio del email. Salida:
idioma (es, en, fr, de, nl, pt; ca e it si aparecen), interés
(`uv_pequeno_mediano`, `uv_gran_formato`, `laser_cnc`, `vending`,
`distribucion`, `consumibles`, `servicio_tecnico`, `repuestos`, `otro`),
`es_spam`, confianza 0–1 y el motivo en una frase.

Dos reglas mandan sobre cualquier proveedor:

- **Productos marcados en el formulario → el interés sale de las etiquetas**,
  no de la IA (no se la llama para el interés).
- **El idioma del formulario manda**; el texto solo gana si está claramente en
  otro idioma (un alemán que rellena el formulario francés), y la discrepancia
  queda anotada (`language_mismatch`).

El spam claro por palabras clave (venta de bases de datos, generación de leads,
SEO, agencias ofreciendo servicios, dominios tipo `leadgeneration`) tampoco
gasta IA. `ClasificadorPalabrasClave` es el proveedor sin IA; el de Anthropic
entra por la misma interfaz cuando `ANTHROPIC_API_KEY` está configurada
(Fase 1 · PR B).

## Tablas

- `lead_classifications` (migración `20261010_0132`): una fila por lead
  clasificado — lo que entró, lo que salió, de dónde salió cada dato, lo que se
  preparó (`draft_id`, `task_id`, `template_name`, `sender_email`, `status`) y
  la corrección a mano. `(source, source_ref)` única.
- `contacts.lead_interest / lead_is_spam / lead_confidence /
  lead_classified_at`: copia de lo esencial para bifurcar sin JOIN.

## Lo que no hace la Fase 1

Enviar. Mover el pipeline por hechos (Fase 3). Segundo y tercer toque
(Fase 3).
