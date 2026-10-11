# AI features

CRMBO usa modelos LLM (Anthropic Claude) para acelerar tareas
mecánicas — hoy sólo el wizard de creación de pipelines (Sprint
P.2.5); a futuro la cualificación automática de contactos (Sprint AI
post-Brevo).

Toda integración con IA sigue un patrón único: **propose, never
apply**. El modelo propone un cambio (un pipeline, una etiqueta, un
estado…) y el operador lo revisa y confirma antes de que persista en
BD. Nunca hay aplicación automática.

## Privacidad y costes

- API key vive sólo en backend (`ANTHROPIC_API_KEY`). Nunca se expone
  al frontend; el frontend consulta `GET /api/health` que devuelve un
  flag boolean `ai_features_enabled` para decidir si pintar el CTA.
- Los prompts del operador **no se persisten**. El audit log carga
  metadata-only (longitud + recuento de elementos propuestos) para
  que un compliance officer pueda medir uso sin ver descripciones.
- Las respuestas del modelo se descartan en cuanto el operador
  decide qué hacer; sólo el pipeline final llega a `pipelines`.
- Rate-limit per-user: 5 generaciones/hora por defecto (cambiable
  vía constantes en `app/services/llm.py`). Sliding window en
  memoria por proceso — multi-worker en prod requerirá migrarlo a
  Redis cuando dé problemas.

## Errores

`POST /api/pipelines/generate-ai` mapea cada fallo a un código limpio
para que la UI muestre un mensaje accionable sin exponer detalles del
proveedor:

| Backend                  | HTTP | Mensaje UI                                          |
|--------------------------|------|-----------------------------------------------------|
| `LLMNotConfiguredError`  | 503  | "AI features are not configured on this deployment." |
| `LLMRateLimitError`      | 429  | "Has agotado las 5 generaciones de esta hora."       |
| `LLMUpstreamError`       | 502  | "Error al generar. Intenta describir tu caso de otra forma, o usa una plantilla." |

## Pruebas

Los tests monkeypatchan `llm_service._invoke_claude` para devolver
respuestas predeterminadas sin hacer llamadas reales. La cobertura
incluye: happy path, fence markdown ```json``` strip, JSON inválido,
demasiado pocas etapas, rate-limit local, 503 cuando la key falta, y
que el audit log no contenga la descripción cruda.

## Próximas extensiones

- **Sprint AI** (después de Brevo): cualificación automática de
  contactos. La IA puntúa el `lead_score` y propone un pipeline /
  etapa basándose en los datos enriquecidos. Mismo patrón:
  propose → operator reviews → save.
- Modelos plug-in: el cliente `_invoke_claude` aísla Anthropic; un
  futuro `LLMProvider` permitirá conmutar Anthropic / OpenAI /
  Google según política del cliente sin tocar las rutas.

## AI en segmentación (Sprint P.3)

Dos endpoints nuevos extienden el patrón **propose, never apply** a los segmentos:

- `POST /api/segments/ai-generate` traduce una descripción en lenguaje natural a un árbol de reglas. La whitelist completa de campos se inyecta dinámicamente en el system prompt, así un campo nuevo añadido al engine se propaga automáticamente a la IA. La propuesta vuelve con `count` + `sample` reales evaluados contra la BD del operador para que vea impacto antes de guardar.
- `POST /api/segments/ai-explain` recibe un árbol (o `segment_id`) y devuelve un párrafo en español describiendo a quién incluye sin jerga técnica.

Rate-limits específicos:
- generación: 10 / hora / user (segmentos son más exploratorios que pipelines).
- explicación: 30 / hora / user (operación más barata).

Coste estimado por llamada (Sonnet 4.6, ~1500 tokens input + 500 output): **~$0.001**.

Garantías de privacidad: idénticas al resto del patrón AI:
- API key sólo en backend.
- Audit metadata-only (descripción nunca persistida; explanation_length y rules_size sí).
- Las reglas que la IA propone se validan con el mismo engine que las del builder visual antes de ofrecerse al operador, así una sugerencia con un campo fuera del whitelist se filtra y se muestra como "La IA propuso reglas inválidas: ...".

## Diagnóstico

`app/services/llm.py` emite tres entradas de log por cada llamada,
todas metadata-only (nunca contenido):

- `llm.request model=… system_chars=… user_chars=…` antes de invocar
  Anthropic.
- `llm.response model=… chars=…` al recibir la respuesta.
- `llm.parse_failure chars=… preview=…` cuando el JSON devuelto no es
  parseable. El `preview` se limita a 200 caracteres del texto crudo
  (no del prompt del operador) para que un compliance officer pueda
  ver si el modelo devolvió prosa, una negativa, o un snippet roto
  sin habilitar los logs del proveedor.

Además, cuando `_parse_segment_json` lanza `LLMUpstreamError`, la
ruta `POST /api/segments/ai-generate` traduce ese caso concreto a un
mensaje de UI accionable: **"La IA no pudo generar reglas para esta
descripción. Intenta reformular usando los nombres de los campos
disponibles."** El resto de fallos del proveedor mantienen el 502
genérico.

### Contexto del CRM real

Antes de invocar a Claude, `generate_segment_rules` construye un
bloque de **contexto del CRM** (`app/services/segments/ai_context.py`)
y lo splicea en el system prompt entre las reglas estructurales y la
whitelist de campos. El bloque incluye:

- Top 100 tags (id + nombre, ordenadas por uso descendente).
- Cuentas de integración habilitadas (`system`, `account_id`,
  `display_name`).
- Países distintos presentes en `contacts.address_country`.
- Pipelines activos y sus etapas (id + nombre).
- Rango actual de `lead_score` (min, max).

Caching: 5 minutos por proceso, con `reset_cache()` expuesto para
tests. Topkill: si el bloque sobrepasa los 16k caracteres
(~4000 tokens) se trunca preservando las tags primero (las más
útiles para resolver descripciones tipo "tag MBO" → ids reales).

Test manual documentado: con el contexto inyectado, una descripción
como "contactos con tag MBO" debe generar un árbol cuyo
`comparator: "contains_any"` recibe los ids de **todas** las tags
cuyo nombre contiene "mbo" (case-insensitive), no un string libre.

### Pruebas manuales

Para verificar que el endpoint AI está activo:

1. `GET /api/health` → debe devolver `ai_features_enabled: true` (el
   wizard usa ese flag para mostrar el CTA "✨ Generar con IA").
2. En el wizard, modo IA, prueba descripciones de ejemplo:
   - **OK**: "Leads con score mayor a 70 y consentimiento concedido".
   - **OK**: "Contactos creados en los últimos 7 días en España".
   - **Ambiguo**: "los buenos" → debe devolver `{error: "..."}` con
     mensaje del propio modelo.
   - **No parseable**: cuando ocurra (raro con prompts cortos), la
     UI muestra el mensaje accionable y el log graba
     `llm.parse_failure` con el preview.

## «Ayúdame a contestar» (asistente de respuesta, 11/10/2026)

**Lo pide una persona, lo revisa una persona, lo envía una persona.** En
cualquier hilo de la Bandeja (y desde la pestaña de emails de la ficha, enlace
«Ayúdame a contestar» que abre el hilo con `?asistente=1`) el botón **«Ayúdame
a contestar»** pide a la IA un **borrador** de respuesta y abre el compositor
con el texto, el destinatario y el asunto rellenos. En el compositor hay
**«Regenerar»** y **«Descartar el borrador de la IA»**; cerrar sin enviar lo
deja como descartado. El envío es el de siempre (`POST /api/emails/send`), con
`asistente_borrador_id` solo para anotar si salió **tal cual** o **editado**.
**No existe ningún camino en el código que envíe un borrador solo**
(`tests/test_asistente.py::test_ningun_camino_del_asistente_envia_correo` lo
comprueba, también escaneando el código del paquete).

### Lo que se le pone delante (`services/asistente/contexto.py`)

- El **hilo entero**, en orden, con lo que contestamos nosotros (nuestro lado
  = cualquier dirección de un dominio del grupo), sin las citas del mensaje
  anterior y con un tope por mensaje.
- La **ficha**: empresa, idioma, propietario, etapa del pipeline, **pedidos**
  con fechas y líneas (y de ahí la **máquina** y, si es de FLUX, el modelo),
  **pendiente de cobro** (BoHub no tiene facturas: son los pedidos con pago sin
  completar), la **clasificación de la IA** y los **últimos 15 asuntos**
  tratados (títulos, no cuerpos).
- La **base de conocimiento** (`docs/base-conocimiento.md`): ruta de soporte
  por web e idioma, página de producto del interés, categoría de la KB de
  FLUX por modelo (o nuestro soporte si es avería), con los artículos
  indexados del modelo.
- El **idioma del mensaje** (detectado en el último mensaje del cliente; la
  ficha solo si el texto no lo delata), **quién pulsa** (nombre y remitente)
  y, como ejemplo de tono, la plantilla del interés × idioma si la hay.
- Un **remitente desconocido** (sin ficha) funciona con menos: solo el hilo.

### Reglas del prompt (`services/asistente/redactor.py`)

Redacta, no aporta hechos: precios, plazos y disponibilidad solo de los datos,
y un precio viejo no se da como actual; sin promesas; **enlaces solo de la
lista** (los de la base de conocimiento), tal cual; en el idioma del mensaje;
tono y longitud de las plantillas del equipo (60–180 palabras); sin firma (la
pone el compositor); si no puede responder algo, lo dice y lo lista en
«pendiente» (se enseña encima del editor). Responde en JSON
(`asunto`, `cuerpo`, `idioma`, `pendiente`, `enlaces_usados`); si no, el texto
entero es el cuerpo. Reutiliza `llm._invoke_claude` (salta los bloques de
razonamiento, #537) y `llm._parse_segment_json`.

### Registro (`ia_llamadas`, `ia_borradores`; migración 0137)

Cada llamada a Claude, del clasificador o del asistente, deja una fila en
`ia_llamadas` con **modelo, tokens de entrada y salida, coste en USD**
(`services/ia/coste.py`, precios por millón de tokens), quién y a qué se
refiere. Cada borrador queda en `ia_borradores` con su contexto, enlaces,
idioma, coste y **resultado** (`generado`, `regenerado`, `descartado`,
`enviado_tal_cual`, `enviado_editado`). Auditoría: `ia.borrador_generado` y
`ia.borrador_resuelto`. La pantalla **ERP · Leads → «Uso de la IA»**
(`GET /api/erp/ia/uso`) enseña los números reales: nada de estimaciones.

Errores: sin `ANTHROPIC_API_KEY` → 503; FLUX/Anthropic saturados → 429; otro
fallo del proveedor → 502 con el motivo, y la llamada fallida queda anotada.
Puede pedirlo cualquier usuario que pueda ver el hilo (misma regla que el
detalle del hilo).
