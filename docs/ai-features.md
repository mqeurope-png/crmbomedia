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
a contestar»** abre el compositor con la **franja del asistente** desplegada
(`components/email/AsistenteFranja.tsx`), entre la cabecera del correo y el
cuerpo: empuja el contenido, no tapa nada, y se pliega a una línea. Lo primero
es la caja **«¿Qué quieres decirle?»** y, en la fila de debajo, las casillas
**«Webs»** y **«Soporte»** (lo que consulta por su cuenta al redactar, § Lo
que no está en el catálogo), el interruptor **Auto · Texto · Con fichas**, el
selector de histórico y **«Redactar»**; el borrador llega al editor con la
firma del compositor detrás y el asunto propuesto. El envío es el de siempre
(`POST /api/emails/send`), con `asistente_borrador_id` solo para anotar si
salió **tal cual** o **editado**;
cerrar sin enviar deja la conversación como descartada. **No existe ningún
camino en el código que envíe un borrador solo**
(`tests/test_asistente.py::test_ningun_camino_del_asistente_envia_correo` lo
comprueba, también escaneando el código del paquete, turnos incluidos).

### Decirle qué quieres decir (remates del 11/10/2026)

El asistente ya no tiene que adivinar la intención a partir del hilo. Si la
persona escribe en la caja («pregúntale si recibió el presupuesto y dile que el
plato PT5 ya está disponible»), esa **instrucción manda** sobre lo que se
deduzca del hilo (`INSTRUCCIÓN DE QUIEN ENVÍA` en el prompt). Si la deja vacía,
se comporta como antes: deduce del último mensaje del cliente. Y si el cliente
**no ha escrito nada nuevo** (el último mensaje del hilo es nuestro, o de un
compañero) y no hay instrucción, **no se llama al modelo**: el cuerpo queda
vacío y el aviso lo explica («dime qué quieres decirle y lo redacto»). El
contexto queda guardado igual, para la instrucción que venga después.

**Regla primera, verificable en el código y en los tests**: el cuerpo del
borrador es **siempre un correo que se puede enviar tal cual al destinatario,
o está vacío**. Nunca contiene nada dirigido a la persona de BoHub («he
revisado el hilo…», «no tengo datos para…»). Todo eso va en el campo `aviso`
del JSON, que se enseña en la franja. El prompt lo exige y, por si el modelo se
despista, la guardia `texto.evaluar_cuerpo` (`redactor._regla_primera`) mira el
texto plano del borrador y decide en **tres niveles**: **informe** (se vacía el
cuerpo y el texto pasa al aviso) cuando está claro que habla a quien pide —el
hilo revisado, «no hay nada que contestar», no poder hacer algo por falta de
datos, preguntarle qué quiere decirle, hablar del cliente en tercera persona
cuando se sabe cómo se llama («te paso el presupuesto que pidió Marny»), contar
lo que quien pide ya hizo («Manel ya le envió») o saludarle por su nombre—;
**dudoso** (se queda en el editor y la franja pide revisarlo) con «el cliente» o
«el hilo» como objeto, el nombre de quien pide fuera de la despedida o el del
destinatario fuera del saludo sin que esté claro que se habla de él; y
**correo** si nada lo delata. **Una oferta de hacer algo («dímelo y te lo
preparo», «dime si te va bien el jueves») no delata nada**: el primer día de
uso real (11/10/2026) la guardia vació un correo válido por eso. Ante la duda,
avisa; borra solo cuando está claro. El nombre del destinatario solo cuenta
escrito con mayúscula («Will» no es «we will send»; «Rosa» no es «la tinta
rosa»), un cierre con el nombre («Gracias, Marny.», «…con calma, Marny.») es
un vocativo, y a principio de frase la mayúscula no distingue el nombre de la
palabra: ahí, como mucho, dudoso. Con cuerpo, la franja titula el aviso
«Revisa antes de enviar»; sin él, «La IA no ha redactado el correo».

### La conversación, y por qué no cuesta otro borrador

Tras el primer borrador, la caja sigue ahí y acepta correcciones en lenguaje
normal («más corto», «en inglés», «no menciones el precio»). Cada una es un
**turno** más de la misma conversación (`POST /api/emails/asistente/{id}/turno`):
el borrador que se corrige queda como `regenerado` y el nuevo lleva
`conversacion_id`, `turno`, `padre_id` e `instruccion` (migración 0138). La
lista de lo dicho se ve en la franja, con vuelta al borrador anterior; se puede
enviar un turno anterior (los posteriores quedan regenerados) y **«Empezar de
nuevo»** vacía la conversación. **Cambiar el alcance** sí rearma el contexto:
es un borrador nuevo y la conversación anterior queda regenerada.

El contexto (hilo, expediente, base de conocimiento) se monta **una vez**, en el
turno 0, y se guarda tal cual en `contexto_texto`; los turnos siguientes mandan
ese mismo bloque **byte a byte**, el borrador actual y la instrucción nueva, sin
volver a montarlo (`redactor.corregir`; el test lo comprueba contando lo que se
manda). El bloque va como primer bloque del mensaje de usuario con
`cache_control: ephemeral` (la **caché de prompts** de la API de Anthropic) y
la petición, que cambia, fuera: la API cobra lo leído de caché a una décima
parte del precio de entrada (una vigésima en Opus 5.5), y `ia_llamadas` e
`ia_borradores` guardan `tokens_cache_lectura` y `tokens_cache_escritura`, que
`coste.py` cobra a su precio. La caché es **por modelo** y dura 5 minutos: con
el modelo de las correcciones distinto del primer borrador (Haiku por defecto),
la primera corrección escribe su propia caché (12.000 tokens × 1,25 × 0,10 $/M
≈ 0,0015 $) y las siguientes la leen si llegan antes de 5 minutos; con el mismo
modelo, ya la primera lee. Comprobado el 12/10/2026 a raíz de un «0 leídos,
6.446 escritos» en la primera corrección: era eso, y las correcciones seguidas
mandan el mismo prefijo byte a byte (sistema, contexto, modelo y parámetros;
lo prueba un test). Lo que sí se tiraba era la caché que escribía el primer
borrador con su modelo, que no lee nadie: ahora el contexto solo se marca como
cacheable cuando el turno va con el modelo de las correcciones
(`redactor._llamar`). «Lo que ha usado» enseña los tokens leídos y escritos de
verdad y, cuando un turno escribe sin leer, por qué. El SDK fijado
(`anthropic==0.40.0`) recibe los bloques tal cual y los parámetros nuevos
(`thinking`, `output_config`) viajan en `extra_body`.

### El modelo, configurable y por tarea

Configuración ERP → «Asistente de respuesta» tiene dos ajustes separados:
**modelo del primer borrador** (vacío = `ANTHROPIC_MODEL`) y **modelo de las
correcciones** (`claude-haiku-5-5` por defecto: trabajan sobre un texto ya
escrito con el contexto leído de caché). La **lista de modelos** del desplegable
también sale de la configuración, para probar uno nuevo sin desplegar. El
**pensamiento extendido** va apagado por defecto (se factura como salida):
`services/ia/modelos.py` sabe cómo apagarlo en cada familia (`disabled` en
Haiku 5.5, Opus 5, Sonnet 5 y 4.6; `between_tools` en Sonnet 5.5, donde
`disabled` da 400) y dónde no se puede apagar baja el esfuerzo (`effort: low`
en Opus 5.5 y Fable); una familia desconocida se queda con su defecto. La
extracción del texto (los bloques de razonamiento de #537) sigue igual con o
sin pensamiento (test con el cliente simulado).

**Lo pensado cuenta dentro del tope de salida** (`max_tokens`). Hasta el
12/10/2026 el tope era el de la respuesta (2.500) y, con el pensamiento
encendido y «Con fichas», el modelo se lo comía entero pensando la estructura:
la API devolvía solo el bloque de pensamiento y salía «Provider returned
non-text content (blocks: thinking)». El lector no tenía culpa (ya se saltaba
ese bloque). Ahora `llm._invoke_claude` recibe en `max_tokens` lo que ocupa la
respuesta y le suma el sitio para pensar según lo que se manda de verdad
(`modelos.tope_de_salida`), para todas las llamadas (asistente, clasificador
de leads, segmentos, pipeline):

- pensamiento apagado (`disabled`, `between_tools`) o familia que no piensa
  sola (Haiku 4.5, Sonnet 4.5 y anteriores): sin margen;
- esfuerzo bajo (Opus 5.5 y Fable, que no se pueden apagar): **+3.000**;
- el resto (pensamiento encendido, o el defecto de los modelos actuales):
  **+8.000**.

Es un tope: se cobra lo que se piensa de verdad. Una respuesta que llega al
tope es un error que lo dice («se acabó el tope de salida pensando, antes de
escribir nada» o «la respuesta se cortó en el tope de salida»), nunca media
respuesta, y el log anota el `stop_reason`. Con Haiku 5.5 la salida cuesta
0,50 $ por millón: pensar es casi gratis y ayuda justo donde Haiku flojea
(decidir la estructura, agrupar fichas, las tiras de marca). Pensar alarga la
petición: en los ejemplos de nginx (`deploy/nginx`, y las directivas de Plesk de
`deployment-ionos.md`) `/api/` espera hasta 180 s; en un servidor ya montado hay
que cambiarlo a mano. La consulta a nuestras webs y al soporte (las casillas de
la franja) va dentro de la misma petición y suma, en lo peor, unos 11 s (5 del
plan y 6 del soporte; es lo esperable, no una garantía: § Lo que no está en el
catálogo).

En **«Uso de la IA»** (ERP · Leads) el primer borrador y las correcciones se
anotan con propósitos distintos (`asistente_borrador` /
`asistente_correccion`; el plan de la consulta a webs y soporte, con el suyo,
`asistente_consulta`, «Asistente · preparar la consulta a webs y soporte», y
su coste se suma también al del borrador), hay una tabla de **coste por
modelo** para comparar y el número que importa: **lo que cuesta un borrador
terminado** (una conversación con algún turno enviado: el primero más sus
retoques, coste medio y turnos de media).

### Un correo nuevo, sin hilo, con el pasado del cliente

La franja está también plegada en **Redactar** y en el botón de escribir de la
ficha. Sin hilo, el Nivel 1 desaparece y los otros tres funcionan igual
(`contexto.construir(thread=None, destinatario=…, instruccion=…)`):

- **El contexto lo dispara el destinatario**: en cuanto hay una dirección en
  «Para» que corresponde a un contacto, se monta su expediente; si no es nadie
  conocido, funciona solo con la instrucción.
- **La instrucción es obligatoria** (`POST /api/emails/asistente`): sin ella no
  se llama al modelo y la franja lo dice.
- **Los correos viejos del Nivel 3 se eligen comparando con la instrucción**
  (asunto, referencia, máquina, solape de palabras), porque no hay mensaje del
  cliente con el que compararlos; sin esto el nivel saldría siempre vacío.
- El selector pierde «Solo este hilo»: quedan «todo lo de este contacto» (por
  defecto) y «todo lo de la empresa».
- **El idioma sale de la ficha**; si no lo tiene, del último correo que
  escribió esa persona; y si no, castellano.
- **El asunto lo propone la IA** (editable, como todo lo demás).

Es el caso del seguimiento: «han pasado tres semanas desde el presupuesto,
escríbele un seguimiento» y la IA sabe, por el expediente, qué presupuesto, de
cuándo y de qué.

### El destinatario por defecto es el cliente

En un hilo de cliente con un compañero en copia, «Responder» (y el asistente)
proponían al último que escribió, aunque fuera `manel@bomedia.net`. Ahora
`_reply_to_suggestion` prefiere el **último remitente de fuera del grupo**; solo
si no hay nadie de fuera (un hilo interno) cae al último que no es el propio
operador. El detalle del hilo trae `reply_to_interno` y la franja avisa
(«vas a escribir a un compañero») antes de redactar; el prompt lo sabe también
(`DESTINATARIO … COMPAÑERO`, tono interno). La persona puede cambiar «Para» en
el compositor sin que el asistente se reinicie: el destinatario viaja con la
petición.

### La firma

El borrador llega sin firma y el compositor pone la activa detrás
(`buildBodyWithSignature`), también en cada turno. Si el usuario no tiene
ninguna firma marcada como predeterminada pero sí una sola, se usa esa.

### Tras el primer día de uso real (11/10/2026)

El hilo `administracion@talentosdiversos.org` enseñó cuatro cosas, y de él
salen los tests de cada bloque (`tests/test_asistente_conversacion.py`,
`tests/test_catalogo.py`, `EmailComposerModal.test.tsx`,
`AsistenteFranja.test.tsx`).

**El compositor no destruye trabajo.** En el hilo es un **diálogo de verdad**
(la capa y la tarjeta comunes de los modales, con velo; `useModalBehaviour` con
`closeOnOverlayClick: false`): pulsar en el fondo **no cierra**, ni con
confirmación. Escape, la X y «Cancelar» preguntan si hay algo escrito —cuerpo
sin la firma, asunto cambiado, instrucción a medias o conversación con la IA—:
**«Guardar en Borradores»** (por defecto), **«Seguir editando»** o
**«Descartar»**; en blanco cierran sin más. Al guardar, el borrador se lleva el
turno del asistente que estaba en el editor (`email_drafts.asistente_borrador_id`,
migración 0140) y al reabrirlo desde Borradores la franja vuelve con sus turnos
y su coste (`GET /api/emails/asistente/{id}/conversacion`); «Descartar» borra el
borrador y descarta la conversación. En el panel derecho (Redactar, ficha,
Borradores) sigue siendo la tarjeta de siempre (`presentacion="panel"`). La
franja **se pliega sola** en cuanto hay borrador («Ayúdame a contestar · 1
borrador · 0,0049 $») y antes del primero va desplegada; el **cuerpo tiene alto
mínimo** (unas doce líneas, también a 768 px de alto) y el diálogo usa el alto de
la ventana: si algo encoge, es la franja. Cc y Cco solo ocupan fila si se usan
(enlaces junto a Para; abiertos si traen contenido); plantillas, adjuntos y
opciones van a iconos en la línea del asunto; el aviso en ámbar va a dos líneas
con un «más»; el aviso de compañero se recalcula cada vez que cambia «Para»
(`GET /api/emails/asistente/interno`).

**La IA corrige lo que hay en el editor.** Siempre el texto que hay ahora
mismo: el borrador tal cual lo dejó la persona después de retocarlo a mano, un
correo escrito enteramente a mano («ponlo más formal»), un borrador guardado
reabierto sin conversación o lo que quede tras «Empezar de nuevo». El compositor
manda `texto_actual_html` (sin la firma) con cada petición
(`redactor.texto_del_editor`); con texto y sin conversación, el turno 0 ya es
una **corrección** con el modelo de las correcciones (el contexto se monta y se
guarda igual, para que los turnos siguientes lo lean de caché). Con texto
seleccionado al pedirlo (`seleccion_html`, `RichEditorHandle.getSelectionHtml`),
se reescribe **solo ese fragmento** y el resto no se toca ni un carácter: el
fragmento se empalma en el texto si aparece tal cual (`texto.empalmar`) y, si
no, el compositor lo pone donde está la selección (`replaceSelection`); la
franja lo dice antes de aplicar («Se aplicará solo a la selección: …»). El estado
de la caja lo decide si hay texto en el cuerpo: con cuerpo va en modo corrección
y «Redactar» pasa a «Aplicar»; con texto nunca pregunta «¿Qué quieres decirle?».
Una selección dentro de un párrafo vuelve como texto corrido, sin `<p>`
alrededor (`texto.encajar_fragmento`), para no anidar párrafos. El turno guarda
la selección tal cual (`seleccion.original_html`) y el compositor solo
sustituye **esa**: si la que hay entonces es otra (otro turno elegido después,
la persona seleccionó otra cosa), ofrece el cuerpo entero y pregunta antes de
pisar lo escrito a mano. Una corrección que la guardia deja vacía no toca el
editor (el aviso lo explica), y si la persona sigue escribiendo mientras la IA
corrige, el compositor pregunta antes de pisarlo. Un cuerpo empalmado de más de
30.000 caracteres se guarda solo como fragmento (`MAX_HTML_EMPALMADO`; la
columna `texto` es TEXT en MySQL; `html` es LONGTEXT desde 0141). Descartar es
descartar la conversación entera: tampoco vuelve, ni admite un turno más, por
un turno anterior que quedó como regenerado.

**Formato sencillo.** El borrador sale en **HTML con las etiquetas que admite
el editor** (párrafos, negrita, cursiva, subrayado, encabezados, listas,
enlaces, tabla simple), limpiado con bleach (`texto.limpiar_html`): lo que no
está en la lista se quita conservando el contenido, los enlaces solo de la
lista permitida (más los que la persona ya tenía) y nunca se pierde texto. Una
lista de máquinas va **como lista**, con el nombre en negrita y el enlace sobre
el nombre; sobrio, sin estilos ni imágenes; un correo con fichas e imágenes
es un **correo compuesto** (abajo), no un cuerpo maquetado; las correcciones
conservan el formato.
`ia_borradores.html` (migración 0141) guarda el HTML; `texto` sigue siendo el
texto plano (la guardia y la comparación de lo enviado, que pasa los dos lados
por el mismo conversor).

**El catálogo contra los datos reales** está en
[docs/base-conocimiento.md](base-conocimiento.md): de dónde se eligen las
máquinas (lo que nombra el cliente, lo que pide la instrucción, la máquina que
tiene, el interés), «Consultar», los premios descartados y la escritura por
lotes.

### Correos compuestos: fichas, marca, hero, muestras, vídeo y botones (12/10/2026)

Además del correo de texto de siempre, el asistente puede preparar un **correo
compuesto**: la entrada, la tira de la marca, un **hero** con una imagen de las
webs, las fichas de producto (una sola, o dos o tres en fila), una **galería de
muestras** de trabajos impresos, el vídeo, los botones, un destacado,
separadores y el cierre. **La IA elige la estructura y escribe los textos; el HTML lo genera
BoHub** con código determinista (`services/correo_compuesto/`). Es un modo más:
la respuesta en texto sigue siendo la de siempre y la opción por defecto.

- **Cuándo.** El interruptor de la franja: **Auto** (por defecto: texto, salvo
  que la instrucción pida presentar productos —«mándale la gama DTF»,
  «prepárale un correo con las máquinas»—; «dile que el pedido sale el martes»
  es texto), **Texto** o **Con fichas** (`formato`; solo viaja si no es Auto).
  Una conversación compuesta sigue compuesta salvo que se pida texto
  (`correo_compuesto/ia.modo_del_turno`). Delante del interruptor, en la misma
  fila, van las casillas **«Webs»** y **«Soporte»**: no cambian el formato,
  sino lo que el asistente consulta al redactar (§ Lo que no está en el
  catálogo).
- **Sin inventar nada.** La IA nombra productos, enlaces, vídeos, imágenes y
  marcas por **referencia**: `P1`/`C1` (líneas del CATÁLOGO), `L1` (ENLACES),
  `V1` (VÍDEOS: los de YouTube o Vimeo del hilo o de la instrucción y los de la
  tabla de vídeos que van con las máquinas del correo o con lo que pide la
  instrucción, en el idioma del cliente), `I1` (IMÁGENES: las del índice de las
  webs que casan con la instrucción, las máquinas y el interés del lead; las
  favoritas delante, nunca las ocultas), `M:mbo_dtf` (la web de la marca) y,
  desde el 28/10/2026, `W1` (un producto de nuestras webs que el catálogo no
  tiene; abajo). Una
  referencia que no existe no entra y se avisa: ni productos, ni enlaces, ni
  vídeos, ni imágenes inventados. Las fichas toman del **catálogo vivo** el
  nombre, la imagen, el área, dos características, el **precio tal cual** (con
  «+ IVA» si la fuente va sin IVA) y el enlace en el idioma del cliente; un
  producto sin precio público dice «Precio a medida» y **no lleva cifra**. Un
  importe en los textos que no es un precio de las fichas se avisa
  (`ia.importes_ajenos`).
- **Fichas de nuestras webs** (28/10/2026). El catálogo llega del compositor en
  un solo sentido: lo que no tiene y está publicado en una de nuestras webs se
  presenta con una ficha **montada desde su página del índice** (con la
  casilla «Webs» marcada; clave `{"fuente": "web", "origen_id":
  "<sitio>:<wp_id>"}`, referencia `W#`, la de su pieza de «Nuestras webs»).
  Mismo bloque, mismas filas y mismo sitio en el correo que una del catálogo;
  lleva título, descripción, imagen principal (enlazada), enlace y **el precio
  de la web con su IVA** tal como está guardado («489,30 € (+ IVA)»), nunca
  como precio de oferta, o **«Precio a consultar»** si la web no lo da. Si el
  producto está en el catálogo, va la del catálogo, se mire contra todo el
  catálogo del compositor y no solo contra las máquinas del correo (un `W#`
  que es ese producto saca la ficha del catálogo, con aviso); si quien envía
  pide una variante que solo está en la web («la versión desktop»), la de la
  web. Un **precio que dice quien envía** en sus instrucciones puede ir en la
  ficha (`{"ref": "W2", "precio": "…"}`); uno que no sale de sus
  instrucciones, o que les añade otra cifra o un IVA que no dijo, se quita
  con aviso, y el de una ficha del catálogo no se toca. Un texto que repite el
  precio de la web de una ficha se avisa aparte (no es de oferta). En una
  corrección siguiente, una ficha de la web ya puesta se ve como
  `?<origen_id>` y se puede mover sin perderla.
  Otra imagen de su galería: `{"ref": "W2", "imagen": "…"}`. La ficha va en el
  idioma del correo si la web tiene esa página; si no, en el que haya, y el
  aviso lo dice. **Al cliente una ficha es una ficha**; a quien revisa, «Lo
  que ha usado» y el aviso le dicen cuáles salen del catálogo y cuáles del
  índice, con su enlace y de dónde sale el precio. Detalle:
  [fuentes-externas.md](fuentes-externas.md) § Fichas montadas desde el
  índice.
- **Nunca HTML de la IA.** Sus textos van en markdown sencillo (negrita,
  cursiva, listas, enlaces por referencia). Si un texto trae etiquetas, se
  quitan, se cuenta (`contexto.html_descartado`) y se registra; con «Con
  fichas» forzado, un cuerpo en HTML se descarta entero y se avisa.
- **Las correcciones son operaciones** sobre la estructura que hay en la vista
  previa, con los retoques de la persona: quitar, editar, añadir, mover,
  duplicar, reemplazar, idioma. «Quita el vídeo» quita ese bloque y lo demás
  queda igual, byte a byte (`ia.aplicar_operaciones`). Lo que hay en el
  compositor manda: un compuesto pasado a texto se corrige como texto.
- **El generador** (`correo_compuesto/generador.py`): tablas con estilos en
  línea, 600 px centrado, una sola hoja de estilo (la consulta de medio que
  apila las columnas por debajo de 600 px), imágenes con `alt`, ancho y URL
  absoluta, sin JavaScript, fuentes externas, SVG ni `<div>` de maquetación;
  el aspecto de los correos que manda hoy el compositor de Bomedia (paleta
  pizarra, insignia, área, ✓, precio en el acento del producto, botón a lo
  ancho), con código propio. Genera a la vez el **texto plano**. Los datos de
  diseño de cada marca y producto salen de la fila viva del compositor
  (`kb_catalogo_marcas.datos_json`, migración 0143).
- **El vídeo no está atado a una URL pegada** (`correo_compuesto/videos.py`).
  El bloque guarda solo **qué vídeo es** (`{"plataforma": "youtube" | "vimeo",
  "id": …}`); su URL, miniatura, título y duración los resuelve BoHub al
  generar desde su **origen**, como los productos salen del catálogo
  (`videos.resolver`, en `Datos.videos`). Hoy el único origen es la URL pegada:
  de YouTube se deriva la miniatura, siempre en 16:9 (`hqdefault` y
  `sddefault` son 4:3 y ponen bandas negras a un vídeo apaisado): la que
  existe siempre, `mqdefault` (320×180), escalada al ancho de la ficha; la de
  1280×720, `maxresdefault`, solo existe si el vídeo se subió en HD, y la
  comprobará la tabla, que guarda cuál usó para volver a probarla si el vídeo
  se resube en HD. Un vídeo de Vimeo suelto sale sin imagen (su miniatura
  pediría su API, que no se consulta), con su título y su botón.
  La **tabla de vídeos** (`kb_videos`, ver
  [base-conocimiento.md](base-conocimiento.md) § Fase 3) es el otro origen y
  manda sobre la URL: su miniatura comprobada (`maxresdefault` si existe), su
  título y su duración; y si el vídeo es de un **tema** con versión en el
  idioma del correo, sale esa (un correo en alemán con el vídeo castellano de
  FREEBIRD lleva el alemán). El título, el texto y el botón que escriba el
  bloque mandan sobre los del origen; la duración, si se sabe, va junto al
  botón («Ver vídeo · 2:31»).
- **El hero y las muestras** (`generador.hero`, `generador.muestras`) llevan
  imágenes del índice de las webs (`kb_imagenes`), por su id: la URL y el
  tamaño salen de la fila viva, y se pide el tamaño de la misma imagen que
  cubre el doble de su ancho en el correo (pantallas de alta densidad), nunca
  la original de varios megas. El hero va con la imagen **arriba** (a todo el
  ancho), **al lado** del texto (dos columnas que se apilan en el móvil) o **de
  fondo** (atributo `background` y estilo para Gmail y Apple Mail; Outlook, que
  no pinta imágenes de fondo, enseña el color oscuro de respaldo y el texto en
  blanco se sigue leyendo); título, subtítulo, hasta cinco puntos y hasta tres
  botones. Las muestras, de 2 a 6 imágenes en filas de dos o tres que se apilan
  en el móvil, cada una con su pie si lo hay. Una imagen que ya no está en la
  web no sale y se avisa. «Pon el hero de DTF» en una corrección añade el
  bloque con una imagen de las que casan con «DTF» y deja lo demás igual.
- **El orden y los botones** (12/10/2026, el hilo de talentosdiversos abría con
  un hero a toda página y el «Hola Marny» venía después): en una **respuesta**
  —hay hilo, o hay correos anteriores con el contacto— el correo empieza por el
  saludo y la entrada, y el hero va detrás; el hero de apertura queda para un
  correo nuevo a alguien con quien no hay conversación. Lo dice el prompt y lo
  asegura el código (`bloques.abrir_con_saludo`, con un aviso de que se ha
  movido); si la persona deja el hero arriba en la vista previa, una corrección
  lo respeta. Y **un mismo enlace va
  en un solo botón** («Ver la gama DTF» salía en el hero y otra vez al final):
  se queda el primero, el repetido se quita y se avisa
  (`bloques.quitar_botones_repetidos`).
- **Fichas en fila** (12/10/2026, con Haiku: «añade más modelos» dio tres
  fichas sueltas seguidas, cada una a 320 px pegada a la izquierda): dos o tres
  productos de la misma marca van en «productos_fila»; «producto» es solo para
  cuando de esa marca va exactamente uno. Lo dice el prompt y, si aun así llegan
  fichas sueltas seguidas de la misma marca, el código las junta en filas
  (`bloques.juntar_fichas_sueltas`, con aviso; la misma ficha dos veces seguidas
  se queda en una). Las filas se reparten parejas: cuatro son dos y dos, cinco
  tres y dos (`bloques.filas_parejas`), nunca una suelta al final; también
  cuando una corrección amplía una fila. La **ficha sola** va centrada (en el
  móvil, a todo el ancho).
- **El aviso de IVA, VIES y «no es una factura» va solo en el pie** (12/10/2026:
  salía también en el texto de cierre). El prompt lo dice y, si la IA copia en
  un texto alguna de las frases que el pie imprime de verdad, se quita de ahí
  (`bloques.quitar_frases`, con aviso; un párrafo que solo trae esas frases se
  va entero aunque lleven negritas). Sin ningún precio en el correo, o con un
  pie puesto a mano, el pie no lleva los avisos y el texto no se toca.
- **Un modelo que el cliente llama de otra forma** (12/10/2026: marcó «Artisjet
  3000U PRO» en el formulario; el catálogo lo llama «artisJet 3000Pro
  Freebird»; el correo enseñaba la ficha y además decía «Den artisJet 3000U PRO
  prüfe ich noch»): el prompt le dice a la IA que es el mismo, con el nombre del
  catálogo, y que no lo dé por pendiente si ya enseña su ficha. Si el texto
  nombra un código de modelo con el número de una ficha del correo pero escrito
  distinto (y no es el de ninguna otra ficha ni una medida como «1200mm»), se
  avisa a la persona (`ia.modelos_con_otro_nombre`); el texto no se toca. La
  solución de fondo son los alias por producto en el compositor.
- Estos arreglos solo tocan lo que ha puesto o cambiado el turno
  (`bloques.bloques_tocados`): lo que la persona dejó así en la vista previa se
  respeta en las correcciones.
- **La vista previa** sustituye al editor: el correo tal cual sale, en
  escritorio o en móvil; los textos se editan pulsando encima (cambia ese
  campo de la estructura y nada más) y el bloque elegido se sube, se baja, se
  duplica o se quita. **«Copiar HTML»** y **«Descargar .html»** dan el
  documento final, autocontenido; **«Pasar a texto»** lo lleva al editor
  normal. `POST /api/emails/compuesto/render` genera el correo de una
  estructura (con `preview`, con los atributos de edición; no envía nada).
- **El envío** es el de siempre (registro, seguimiento, firma, baja, texto
  plano): el compositor manda el HTML y el texto plano que genera BoHub. El
  saneado reconoce el correo compuesto por su marca y le aplica su propia lista
  blanca (ver [emails-compositor.md](emails-compositor.md)). Tal cual o editado
  se compara por el texto plano. Nunca se envía solo.
- **Registro.** `ia_borradores.modo` (`normal` o `compuesto`) y
  `estructura_json`; los borradores guardados, `email_drafts.estructura_json`
  (migración 0144). Las plantillas de autorrespuesta no cambian.

### Lo que se le pone delante (`services/asistente/contexto.py`)

- El **hilo entero**, en orden, con lo que contestamos nosotros (nuestro lado
  = cualquier dirección de un dominio del grupo), sin las citas del mensaje
  anterior y con un tope por mensaje.
- La **ficha entera**: empresa, idioma, propietario, **teléfonos** (el
  principal y los de `contact_phones`, sin repetir número), **etiquetas** (hasta
  40), **campos personalizados** con valor (hasta 40, cada uno hasta 300
  caracteres: los de Brevo, Agile, formularios, workflows y la ficha), las
  **notas internas** del equipo (hasta 10: las fijadas primero, luego de la
  más reciente a la más antigua y al final las Note1..Note10 de la ficha de
  Agile, sin fecha —la que tienen es la de la importación— y solo en su última
  versión; cada una hasta 800 caracteres y sin HTML; las vacías no cuentan),
  etapa del pipeline,
  **pedidos** con fechas y líneas (y de ahí la **máquina** y, si es de FLUX, el
  modelo), **pendiente de cobro** (BoHub no tiene facturas: son los pedidos con
  pago sin completar), la **clasificación de la IA** y los **últimos 15
  asuntos** tratados (títulos, no cuerpos). El prompt dice que las notas, los
  campos y las etiquetas son internos: sirven para entender el caso, pero no
  se citan ni se dice que existen, y un precio, plazo o condición de una nota
  o de un campo no es vigente (la regla de los precios sigue: solo los del
  CATÁLOGO).
- La **base de conocimiento** (`docs/base-conocimiento.md`): ruta de soporte
  por web e idioma, página de producto del interés, categoría de la KB de
  FLUX por modelo (o nuestro soporte si es avería), con los artículos
  indexados del modelo.
- El **catálogo** (bloque CATÁLOGO, la única fuente de precios): hasta seis
  **máquinas** del compositor de correos que encajan con los intereses del
  lead y hasta ocho **consumibles y repuestos** de las tiendas para la
  máquina que tiene, en su idioma, con el **precio tal cual** está escrito en
  el origen (sin convertir moneda; sin precio en su idioma, el del idioma por
  defecto de la marca, dicho), su enlace y la marca «ORIENTATIVO» si la fuente
  lleva más de seis meses sin confirmarse. El bloque trae también la
  declaración del IVA por fuente y las frases que el correo tiene que llevar
  (IVA, VIES, «no es una factura») en el idioma del correo. Sin producto para
  lo que piden, lo dice y el modelo no cita ningún precio.
- El **idioma del mensaje** (detectado en el último mensaje del cliente; la
  ficha solo si el texto no lo delata), **quién pulsa** (nombre y remitente)
  y, como ejemplo de tono, la plantilla del interés × idioma si la hay.
- Un **remitente desconocido** (sin ficha) funciona con menos: solo el hilo.
- Con las casillas **«Webs»** / **«Soporte»**, lo que el asistente consulta
  solo (el artículo del soporte, las páginas de nuestras webs) **no va en este
  bloque** sino en la petición del turno (§ Lo que no está en el catálogo): el
  bloque se congela con el turno 0.

### La oferta adjunta (12/10/2026)

Al enviar una **oferta** (`docs/erp/ofertas.md`) el modal pide el cuerpo al
asistente con `oferta_id` (`POST /api/emails/asistente`, correo nuevo). La
oferta entra en el contexto como entra el catálogo: un bloque «OFERTA
ADJUNTA» con número, fecha, validez, emisora, con/sin IVA, líneas e importes.
Es la única excepción a «los precios salen solo del CATÁLOGO»: esos importes
sí se citan tal cual, porque son los del PDF que recibe el cliente; para esos
productos no se usan precios del catálogo, y no se afirma el régimen de IVA
definitivo (se fija al convertir, según VIES). Sin contacto explícito, el de
la oferta. «Lo que ha usado» enseña «Oferta OF-2026-0001 adjunta (N líneas,
total …)» (`contexto.oferta`).

### Reglas del prompt (`services/asistente/redactor.py`)

Regla primera: el cuerpo es un correo enviable o está vacío, y lo que haya que
decirle a la persona va en `aviso`. Después: redacta, no aporta hechos (precios
de productos **solo del bloque CATÁLOGO**, copiados tal cual con su moneda y
con las frases de IVA, VIES y «no es una factura»; plazos y disponibilidad solo
de los datos; un precio de un correo o pedido viejo es histórico y no se da
como vigente); la instrucción de quien envía manda sobre el hilo; sin promesas;
**enlaces solo de la lista** (los de la base de conocimiento), tal cual; en el
idioma del mensaje; tono y longitud de las plantillas del equipo (60–180
palabras; tono interno si el destinatario es un compañero); al destinatario
**siempre en segunda persona** y con el mismo trato de principio a fin, nunca
en tercera (en castellano, de tú: «el presupuesto que ya **te** envió Manel», no
«que ya le envió»; visto en producción el 12/10); sin firma (la pone
el compositor); si no puede responder algo, lo dice y lo lista en «pendiente»
(se enseña en la franja); una corrección reescribe el borrador conservando lo
demás. Responde en JSON (`asunto`, `cuerpo`, `idioma`, `aviso`, `pendiente`,
`enlaces_usados`); si no, el texto entero es el cuerpo (y la regla primera
decide si es un correo). El mensaje de usuario va en dos bloques: el contexto,
cacheable, y la petición. Reutiliza `llm._invoke_claude` (salta los bloques de
razonamiento, #537) y `llm._parse_segment_json`.

### La empresa, y la marca que ya no existe (13/10/2026)

Bomedia salió del ecosistema: lo que lee un cliente va a nombre de **Streamtec
(castellano) o MQ Europe (resto de idiomas)**, el mismo criterio que las rutas
de soporte (`services/empresas_grupo.py`). Tres capas:

- **Lo que le pone delante.** El prompt de sistema se presenta como el equipo
  de Streamtec y MQ Europe, y la **regla 11** dice que la empresa es la de
  EMPRESA y que nunca escriba «Bomedia», aunque salga en el hilo, en correos
  antiguos, en una plantilla, en una nota o en la ficha (los hilos viejos están
  firmados como Bomedia y el modelo copia el tono y la firma de lo que lee). La
  cabecera lleva la línea **EMPRESA**: la emisora de la oferta adjunta si es
  Streamtec o MQ Europe; si no, la regla del idioma (dicha como regla, para que
  una corrección «en inglés» la cambie sola). Una oferta de la serie 1 (que
  emite «Bomedia S.L.») le llega al modelo como «serie 1», sin el nombre.
- **La guardia de la marca** (`redactor._guardia_bomedia`, también en el
  compuesto): si el borrador nombra «Bomedia» en el cuerpo o el asunto, se
  **sustituye** por la empresa del correo (no se reintenta: la sustitución es
  determinista y no cuesta otra llamada). Las direcciones y dominios de
  bomedia.net **no** cuentan. Cambiar un nombre puede reescribir un hecho («ya
  trabajaste con Bomedia en 2019»), así que la franja enseña un aviso **propio,
  sin plegar y fuera de lo que se pliega**, con **cada frase antes y ahora**,
  para releerla. Queda registrado: `logger.warning`, la marca `bomedia` en el
  turno (`contexto_json`, sale como `bomedia` en la API) y la auditoría
  `asistente.bomedia_sustituido` (cuántas frases, por qué empresa, modelo):
  así se sabe si sigue pasando.
- **El Cuadre** (`bomedia_en_textos_cliente`, ver `docs/erp/cuadre.md`) vigila
  los textos guardados de cara al cliente.

La serie 1 de FACTUSOL sigue siendo «Bomedia S.L.» (decisión de Bart): sus
facturas, proformas y albaranes, y la firma del correo de presupuesto de esa
serie (`quote_email._firma_for_serie`, coherente con el PDF que lleva adjunto).

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

### Qué histórico mira y cuánto (`services/asistente/historico.py`)

Pasarle al modelo los cuarenta correos de un cliente es un error por tres
motivos: la respuesta sale peor (se agarra a un asunto cerrado), el coste y la
espera se multiplican, y hay cosas que no debe repetir (un precio dado a otro
comprador de la misma empresa). La solución es traer poco y que sea lo que
toca: **cuatro niveles por orden de prioridad**, llenados hasta agotar un
**presupuesto de tokens** configurable (Configuración ERP → «Asistente de
respuesta», 12.000 por defecto). Si no cabe todo, se recorta por abajo, nunca
por arriba.

1. **El hilo actual, entero y literal.** Nunca se recorta: si no cabe, no cabe
   nada más.
2. **El expediente: hechos, no mensajes.** La ficha entera (teléfonos,
   etiquetas, campos personalizados, notas internas), los últimos asuntos
   tratados (15, título y fecha), qué compró y cuándo, la máquina, pendiente de
   cobro, etapa del pipeline, clasificación de la IA. Si no cabe, de aquí solo
   se recortan las notas internas, después de los correos viejos y antes que
   el catálogo (la más antigua de las no fijadas primero).
3. **Los correos viejos que vienen a cuento, con su cuerpo.** Como mucho 5, el
   más reciente primero (uno por hilo). Entra uno si el asunto coincide al
   normalizarlo (fuera `Re:`, `RE:`, `Fwd:`, `AW:`, `RV:` y los corchetes), si
   el mensaje de hoy menciona una referencia (pedido, factura, albarán, número
   de serie) que aparece en él, si habla de la misma máquina o producto (por
   las líneas de sus pedidos), o si hay solape fuerte de palabras con la
   pregunta de hoy, descontando las vacías y las genéricas. Si ninguno cumple,
   el nivel queda vacío y no pasa nada.
4. **La empresa, marcada como tal.** Correos de **otras personas de la misma
   empresa**, agrupada por el **registro de empresa de BoHub, nunca por el
   dominio del correo** (los clientes de gmail.com no son una empresa; la
   lista de dominios gratuitos está en la configuración). Si el contacto no
   tiene empresa asignada, el nivel queda vacío. Entran como asunto y fecha,
   etiquetados «lo escribió Fulano, de la misma empresa», y el prompt prohíbe
   repetir precios, descuentos o condiciones dadas a otra persona. Solo con
   «todo lo de la empresa» pueden traer cuerpo (mismo criterio del nivel 3).

**Lo elige quien pulsa**: el selector de la franja del asistente, junto a la
caja: «Solo este hilo» (nivel 1), «Todo lo de este contacto» (1, 2 y 3; el
defecto) y «Todo lo de la empresa» (los cuatro). Cambiarlo con un borrador ya
hecho rearma el contexto: es un borrador nuevo. La elección se recuerda por
usuario en el navegador (`lib/asistenteAlcance.ts`), igual que las casillas
«Webs» y «Soporte» (`lib/asistenteFuentes.ts`, clave
`bohub.asistente.fuentes.<usuario>`; marcadas de partida). Un remitente
desconocido va siempre solo con el hilo; sin hilo (correo nuevo) no existe
«Solo este hilo» y los correos viejos se eligen comparando con la
instrucción.

**Se ve lo que ha usado**: debajo del borrador, plegado, cuántos mensajes del
hilo, qué correos viejos entraron (fecha, asunto y por qué), si hubo contexto
de la empresa, los tokens estimados y lo que se dejó fuera por tamaño. Es la
misma lista que se guarda con el borrador (`contexto_json`), y el registro
guarda además el **alcance** y el **número de mensajes** (`alcance`,
`mensajes_hilo`, `mensajes_historico`) para cruzar después si los borradores
con más histórico salen mejores o solo más caros.

### Lo que no está en el catálogo: soporte, webs e imagen por URL (27/10/2026)

El asistente estaba encerrado en la base de conocimiento y el catálogo: no
podía citar el procedimiento de FLUX para calibrar una cámara ni enlazar la
página de un accesorio. La referencia técnica (red con cerrojos, lista blanca,
índice, reglas, permisos, cómo se consulta, lo comprobado contra las webs
reales) está en [fuentes-externas.md](fuentes-externas.md); aquí, lo que
cambia en el asistente.

Con #564 eran dos botones de la franja, «Buscar en soporte» y «Buscar en las
webs»: la persona buscaba **antes** de redactar y elegía qué entraba («Usar en
el borrador», «Añadir al borrador», «Escribir diciendo que no hay
procedimiento»). **Desde el 28/10/2026 el asistente consulta por su cuenta,
al redactar**, igual que consulta el catálogo: nadie busca ni elige nada.

- **Dos casillas en la franja, «Webs» y «Soporte»**, en la fila de debajo de
  la caja, donde estaban los botones (delante de «Auto · Texto · Con
  fichas»). **Marcadas de partida** y recordadas por usuario en el navegador,
  como el alcance (`lib/asistenteFuentes.ts`). Tocar una no rehace el
  borrador: vale para la petición siguiente.
- **Lo que viaja**: `fuentes: {webs, soporte}` en las tres peticiones que
  redactan (generar con hilo, generar nuevo y turno). **Con las dos
  desmarcadas (o sin el campo), el turno es exactamente el de antes del
  #564**: ni plan, ni material, ni reglas; el mismo prompt de sistema, el
  mismo contexto y la misma petición (`tests/test_asistente_fuentes.py`).
  Tampoco se consulta si no hay nada que contestar.
- **Qué buscar lo prepara un plan**: una llamada corta al **modelo de las
  correcciones** (el de Configuración ERP → «Asistente de respuesta», Haiku
  por defecto) lee el último mensaje del cliente (su consulta del lead solo si
  en el hilo no hay ninguno suyo), el asunto, la instrucción y las máquinas
  que ha reconocido el catálogo, y dice si el correo es técnico, de qué marca,
  qué buscar en el centro de ayuda (en inglés y en el idioma del correo) y qué
  en nuestras webs. **5 s como mucho y sin reintentos**; si
  falla, tarda o devuelve otra cosa, se busca **con las palabras del correo**
  (las máquinas que nombra, los códigos de error, los mensajes entre comillas,
  el asunto). Se anota en `ia_llamadas` como `asistente_consulta` y su coste
  **se suma al del borrador**.
- **Soporte**: solo si el correo es **técnico** (avería, error, ajuste,
  instalación, mantenimiento, cómo se hace algo), en el centro de ayuda de
  **la marca** de la que habla si lo tiene en la lista blanca. Si el catálogo
  ha reconocido en el correo una máquina de una marca **sin** centro (una
  artisJet), no se busca en el de FLUX: «Lo que ha usado» dice que no tiene
  centro. Si no ha reconocido ninguna máquina de esa marca, o no hay marca,
  se busca en **todos** los centros activos. Primero **en inglés**, luego en
  el idioma del correo (una sola vez si es la misma búsqueda). El **primer
  artículo se lee entero** (en el idioma del correo si existe); **dos más,
  solo título y extracto**, para enlazar. Un correo técnico sin centro donde
  buscar (esa marca no lo tiene, o no hay ninguno activo) lleva la orden de
  no escribir pasos de memoria, y el aviso de pasos si los trae.
- **Webs**: los productos y páginas por los que pregunta, en el índice (MySQL
  local) en el idioma del correo (si nada, en cualquiera): **tres
  fragmentos**, con su enlace y su «precio en la web», que no es precio de
  oferta. **Delante**, los productos que **pide quien envía** (28/10/2026),
  por su enlace o por su nombre («incluye la MBO 6090 desktop, mírala en
  mboprinters.com»), también en una corrección (en una corrección, si trae
  el enlace, dice «ficha», «versión» o «variante», o una cifra del modelo:
  `consulta_fuentes.pide_producto`); los que son un producto valen
  como ficha en un correo compuesto (§ Correos compuestos) y, en uno de
  texto, se enlazan y su precio se dice como precio de la web.
- **El tiempo**, porque va dentro de la petición del borrador: el plan **5 s**,
  todo el soporte **6 s** y cada petición al soporte **4 s**, sin reintentos;
  las webs son una consulta a la base local. La resolución del DNS va dentro
  del plazo, pero httpx vuelve a resolver al conectar y esa no tiene tope: en
  lo peor, unos 11 s más es lo esperable, no una garantía, y queda lejos del
  corte del nginx.
- **Lo que falla se deja fuera**: una fuente que no responde o no llega a
  tiempo no tira el borrador; se escribe sin ella y el aviso de la franja lo
  dice («No se ha podido consultar el soporte (…): el borrador se ha escrito
  sin él.»). Sin soporte consultado, el modelo tiene la orden de no escribir
  pasos de memoria; «no he podido mirar» nunca se convierte en «no hay
  procedimiento».
- **Lo que recibe el prompt**: lo consultado va **en la petición del turno**,
  delante de lo demás (también delante de la cabecera de FORMATO del
  compuesto), **encapsulado como dato** y con su referencia (`[S1]` el
  artículo, `[S2]`, `[S3]` los otros, `[W1]`… las páginas); el bloque de
  contexto no se toca. Al sistema se le añaden, solo entonces, la regla del
  material, las de soporte y webs y la de **`material_usado`**: el modelo dice
  en su JSON qué piezas ha usado. Las URL del material entran en los enlaces
  permitidos y en `enlaces_json`, para que las correcciones las conserven. Si
  se buscó en el soporte y no hay nada (todas las fuentes respondieron), va la
  regla de no escribir pasos y una frase con la búsqueda exacta; si no se pudo
  consultar, o no hay centro de ayuda para esa marca, la de no escribir pasos
  de memoria.
- **Garantías**, que no dependen de que el modelo obedezca: el borrador que
  **usó** el artículo (`S1` en `material_usado`; si el modelo no lo dice, si
  su enlace está en el cuerpo o el cuerpo trae pasos) **lleva su enlace** (si
  no lo puso, se añade al final en el idioma del correo, con aviso; un
  artículo que no viene a cuento no se usa ni se enlaza a la fuerza);
  referencias, modelos y números de pieza **literales**; si el borrador trae
  **pasos** que no salen de ningún artículo (no había procedimiento, no se
  pudo consultar, no hay centro de ayuda para esa marca o no usó el que
  salió), la franja avisa «revísalo»; un precio de la web **nunca se
  oferta**. Las guardias del cuerpo y de Bomedia siguen igual.
- **Las correcciones reutilizan lo que ya se trajo**: lo que trajo el primer
  turno (el artículo, de la caché y en el idioma en que se pidió; las
  páginas, por su id) con las casillas de **ahora** (desmarcar «Webs» deja la
  corrección sin las páginas), sin plan ni coste de consulta. **Salvo** una
  fuente que el primer turno no miró (su casilla iba desmarcada) o que
  falló: esa se consulta en la corrección, con el plan guardado. Si la
  conversación aún no había consultado (el primer turno iba sin casillas o no
  había nada que contestar), la primera corrección con alguna casilla
  consulta entera, con la instrucción del primer turno y la suya.
- **«Lo que ha usado»** enseña lo consultado (`contexto_json["material"]`, por
  turno; una corrección con las dos casillas desmarcadas no hereda el del
  turno anterior): el artículo y los otros con su enlace, su fuente y si el
  borrador lo usó («usado en el borrador», «no lo usó», «solo enlazable»), las
  páginas con web, tipo y precio de la web, lo que no se encontró, lo que no
  hacía falta («el correo no es técnico», «no pregunta por ningún producto») o
  no se pudo consultar, y qué se buscó y cómo («la preparó la IA», «con las
  palabras del correo», «la del primer borrador», «en esta corrección, con la
  búsqueda del primer borrador»), con el tiempo. «Buscó …» sale solo de lo que
  se buscó de verdad (`buscado_en_soporte`, `buscado_en_webs`), nunca del plan
  ni de las casillas; un correo técnico con «Soporte» marcada en el que no se
  buscó nada dice «Soporte: no se ha consultado». El título
  plegado lo resume («consultó soporte y webs»). Un borrador de antes del
  28/10/2026 enseña lo que se eligió con los botones. Con un correo
  compuesto con «Webs» marcada (o con alguna ficha del índice), enseña además
  **las fichas del correo en dos listas**, «Ficha del
  catálogo» y «Ficha del índice de <web>», cada una con su enlace, y en las
  del índice de dónde sale el precio (de la web, el que has indicado tú o a
  consultar), el idioma si no es el del correo y cuántas imágenes tiene su
  galería; el título plegado suma «· N fichas del índice». Con «Webs»
  marcada en ese turno, un producto del catálogo cuyo enlace no lleva a una
  página de producto de nuestras webs sale marcado, con el porqué (una
  página como la de contacto, un producto que ya no está, o uno que el índice
  aún no tiene), y si va en el correo, el aviso lo dice («revísalo en el
  compositor»; no se corrige).
- **Coste**: el plan es una llamada corta (300 tokens de salida como mucho)
  con el modelo barato, una vez por borrador con alguna casilla marcada. Un
  turno con material lleva sus reglas detrás del prompt de sistema, y la caché
  del contexto incluye el sistema: no lee la que dejó un turno sin material y
  escribe la suya. Como las correcciones reutilizan lo del primer turno, su
  sistema no cambia de una a otra y leen la caché como siempre (si se toca una
  casilla, cambian las reglas y ese turno la vuelve a escribir). El material va
  en la petición, fuera de la caché: se paga entero en cada turno (como mucho,
  6.000 caracteres del artículo, 600 de extractos y 2.700 de páginas).
- **Las imágenes del editor sobreviven a una corrección.** El editor tiene
  ahora **«Imagen por URL»** (comprobada antes de insertarla y enlazada tal
  cual). Antes, «más corto» sobre un cuerpo con imágenes las perdía en
  silencio (el borrador no admite `<img>`). Ahora cada imagen **de fuera**
  (la de «Imagen por URL») va al modelo como «[IMAGEN 1]» y vuelve a su sitio
  con su etiqueta saneada, con el `max-width:100%;height:auto` que le pone el
  diálogo y ningún otro estilo (`texto.apartar_imagenes` /
  `restaurar_imagenes`; se acepta el marcador traducido). Si el modelo lo
  quitó, la imagen vuelve al final con aviso; las que no son https no se
  conservan y se dice cuántas (en una corrección sobre una selección, solo las
  de la selección). **Las subidas desde el PC** (`/assets/email-templates/…`
  y los adjuntos de plantilla `…/attachments/by-cid/…`) siguen **exactamente
  como antes**: un editor que solo tiene de esas da la misma petición que
  antes de este cambio. Un compuesto rehecho desde el texto va sin imágenes, y
  se avisa.
