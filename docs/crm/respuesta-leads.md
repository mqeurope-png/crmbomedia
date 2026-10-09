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

## El proveedor de IA

`app/services/leads/proveedor_anthropic.py`: Anthropic, con el cliente que ya
tiene BoHub (`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`). Pide un JSON (idioma,
interés, spam, confianza, motivo) y lo normaliza al catálogo (`es_spam` como
booleano, número o texto «false»/«no»: en caso de duda, no es spam); si la IA
no está disponible (sin clave, cuota, caída, respuesta ilegible) cae al
proveedor de palabras clave y el motivo lo dice. La consulta del cliente sí
viaja al proveedor; ni el prompt ni la respuesta se guardan en la base de
datos (en el log, el `llm` apunta tamaños y, si la respuesta no es JSON, sus
primeros 200 caracteres). Sin clave, el proveedor por defecto es el de
palabras clave. Los tests nunca llaman a Anthropic: `tests/conftest.py` vacía
la clave y los del proveedor parchean `_invoke_claude`.

## Configuración (Configuración ERP → «Respuesta a leads»)

Blob `lead_response` de `ErpSettings` (`app/services/leads/config.py`), por
`GET/PATCH /api/erp/settings`:

| Clave | Qué es | Defecto |
|---|---|---|
| `activo` | Interruptor general. Apagado, el paso «Clasificar lead» sale por `omitido`. | apagado |
| `tope_diario` | Leads clasificados al día; al llegar, los siguientes salen por `omitido` y se avisa **una vez al día** a `WEB_FORMS_NOTIFY_TO`. | 20 |
| `umbral_confianza` | Por debajo, la Fase 2 no enviará; en la Fase 1 la lista lo marca. | 0,7 |
| `antiguedad_horas` | Solo leads más recientes, por su fecha real. El paso «Clasificar lead» la lee en cada lead (el workflow de serie no fija la suya, así que cambiarla aquí vale al momento). El trigger tiene su propio `max_age_hours`: al crear el workflow se siembra con este valor y después se cambia en el editor. | 72 |
| `ventana` | De 9 a 18, laborables: con lo que se siembra la espera del workflow. | 09:00–18:00, laborables |
| `mapa` | `interes:idioma → template_id`. Vacío = sin plantilla a propósito; sin entrada se busca por nombre (`Lead · <contenido> (<IDIOMA>)`). | por nombre |
| `remitentes.por_web` | Web → dirección; sin entrada, `sitios.REMITENTES`. | — |
| `remitentes.por_cuenta_agile` | Cuenta de Agile → web (los leads de Agile no traen web). | — |

El GET lleva además `lead_response_catalogo`: intereses, idiomas, plantillas
candidatas, el mapa resuelto por nombre, las webs con su remitente por
defecto y las cuentas de Agile. En el PATCH, `mapa`, `remitentes.por_web` y
`remitentes.por_cuenta_agile` se sustituyen enteros cuando vienen (la
pantalla manda siempre el bloque completo; así se puede quitar una entrada).

## El modo en seco y la lista corregible (`/api/erp/leads`)

- `POST /en-seco {dias, limite}`: clasifica los leads de los últimos N días
  (envíos no spam + notas «form note» por su fecha real) con el mismo
  proveedor y la misma configuración que el workflow, y dice por lead qué
  habría hecho: etapa (Nuevo lead / Descartado · spam), plantilla, remitente,
  tarea y avisos. **No escribe nada.** Es lo que se usa para medir el acierto
  antes de encender el interruptor (y, después, la Fase 2). Cada lead es una
  llamada al proveedor dentro de la petición: 100 leads por defecto, 200 como
  máximo (y 90 días).
- `GET /clasificaciones?dias=15`: los leads clasificados de verdad (hasta 90
  días, 500 filas), con lo que salió, de dónde salió cada dato, lo que se
  preparó y la corrección.
- `POST /clasificaciones/{id}/corregir {idioma?, interes?, es_spam?, nota?}`:
  la corrección a mano. Se guarda aparte (la original se conserva), queda en
  la auditoría (`lead.classification_corrected`) y pasa a la ficha del
  contacto solo si es su último lead (uno de julio corregido no pisa el de
  octubre).
- `POST /workflow`: crea el workflow «Respuesta a leads (Fase 1)» en BORRADOR
  resolviendo «Ventas B2B» → «Nuevo lead» / «Descartado / spam» por nombre;
  409 si ya existe, 400 si falta el pipeline. `GET /workflow` dice si existe.

## El workflow de la Fase 1

```
lead.received → clasificar
   [spam]    → añadir a Ventas B2B · Descartado / spam → salida perdida
   [omitido] → salida natural
   [ok]      → esperar 12 h (9–18 laborables) → preparar borrador
             → añadir a Ventas B2B · Nuevo lead → crear tarea → salida natural
```

La tarea sale como «Revisar lead: Vending (95%) · Isabella Cedillo» con la
web, el idioma, el motivo, los productos marcados, la plantilla, el remitente
y el enlace al borrador. Se asigna a quien creó el workflow (cámbialo en el
editor). Ni un paso de enviar.

## El Cuadre: «Lead sin contactar»

Severidad media, fuente BoHub (`checks_mysql.lead_sin_contactar`): lead
entrado hace más de 48 horas, no spam, sin ningún correo saliente — el acuse
de recibo no cuenta. Es la red de seguridad por si falla todo lo demás y mide
el problema de partida, así que lee los leads de sus fuentes y no de lo que
el workflow clasificó: los envíos de formulario que no son spam y las notas
«form note» de Agile por su fecha real (estén clasificados o no), los
contactos web de antes de guardarse los envíos (por su alta) y lo que sí se
clasificó. Ventana de N días por esa fecha real.

## La pantalla (PR C)

- **Configuración ERP → «Respuesta a leads»**: el interruptor, el tope
  diario, el umbral, la antigüedad, la ventana horaria, el mapa interés ×
  idioma → plantilla («Por nombre» enseña la que se resuelve; «Sin plantilla»
  a propósito; o una concreta), el remitente por web y la web de cada cuenta
  de AgileCRM. Se guarda como una sección más (el PATCH lleva solo
  `lead_response`).
- **ERP · Leads** (`/erp/leads`, misma capacidad que la configuración): el
  estado del workflow de la Fase 1 con «Crear el workflow» (en borrador, con
  el enlace al editor), el **modo en seco** (días, «Simular en seco», resumen
  y tabla de qué haría con cada lead) y la **lista de leads procesados** de
  los últimos N días con idioma, interés y spam corregibles en la propia fila
  («Guardar corrección», con nota opcional), la confianza en rojo por debajo
  del umbral, lo que se preparó (estado, plantilla, remitente, enlace al
  borrador) y quién corrigió qué.
- **Editor de workflows**: el trigger «Lead recibido» (origen, web,
  antigüedad), los paneles de «Clasificar lead» (tres salidas: Lead / Spam /
  Omitido), «Preparar borrador de email (sin enviar)» (plantilla por interés
  o fija, remitente de la web o fijo, dueño del borrador) y «Añadir a
  pipeline»; «Mover contacto de etapa» con su texto nuevo; la espera con
  ventana horaria. La categoría «Oportunidades» pasa a llamarse «Pipelines».

## Después del deploy

1. Configuración ERP → Respuesta a leads: comprobar el mapa (las 30
   plantillas se resuelven por nombre) y los remitentes; mapear las cuentas de
   Agile a su web.
2. **Modo en seco sobre los últimos 15 días** y revisar la clasificación con
   Bart.
3. «Crear el workflow», revisarlo en Workflows y activarlo. Encender el
   interruptor.

## Lo que no hace la Fase 1

Enviar. Mover el pipeline por hechos (Fase 3). Segundo y tercer toque
(Fase 3).

## Pendientes (anotados en la revisión, severidad baja)

- El modo en seco clasifica dentro de la petición (hasta 200 leads, una
  llamada a Anthropic por lead con la clave puesta). Si Bart lo lanza con
  cientos de leads y el proxy corta la respuesta, moverlo al worker con un
  informe que se consulta después, como «Comprobar ahora» del Cuadre.
- «Crear el workflow» comprueba que no exista por nombre sin bloqueo: dos
  clics a la vez podrían dejar dos borradores. Se ve en Workflows y se
  archiva uno.
- `lead_classifications` y la lista no paginan (500 filas por consulta).
