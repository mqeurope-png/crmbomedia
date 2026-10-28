# Base de conocimiento para la respuesta a leads

Lo que el asistente «Ayúdame a contestar» puede **enlazar** (Fase 1) y los
**precios que puede escribir** (Fase 2, el catálogo). La regla es la del
diseño: **el modelo redacta, no aporta hechos**. Precios, plazos y enlaces
salen de los datos de BoHub y de estas tablas, o no salen. Sin embeddings, sin
cuerpos de artículos; títulos, enlaces, precios tal cual y reglas en datos.

Pantalla: **Configuración ERP → «Base de conocimiento»** (capacidad
`erp.config`). Las tablas se guardan al momento por su API; el botón «Guardar
cambios» de la sección lleva solo la configuración (sincronizaciones, palabras
de FLUX, meses hasta «orientativo», IVA por fuente; blob `conocimiento` de
`ErpSettings`).

## Tablas (migraciones 0135 y 0136)

| Tabla | Qué guarda | Siembra |
|---|---|---|
| `sitios_web` | Las webs y dominios de correo del grupo: clave, dominio, host, dominios extra, marca, empresa, tipo (`web` / `correo`), idioma por defecto, idiomas publicados, plugin (`qtranslate` / `wpml`), prefijo del idioma por defecto, slugs traducidos, soporte propio, remitente por defecto, orden, activo | 8 webs + `bomedia.net` y `streamtec.es` |
| `kb_rutas` | Tipo `soporte`: web × idioma → URL. Tipo `producto`: interés → URL (en la web que la tiene). Nota, activo, orden y la última comprobación (cuándo, código HTTP, error) | 29 rutas de soporte + 7 páginas de producto |
| `kb_flux_categorias` | Las categorías de support.flux3dp.com, una por modelo, con las palabras que lo reconocen | 13 categorías |
| `kb_flux_articulos` | El **índice** de la KB de FLUX por locale: título, enlace, sección, categoría, etiquetas, fecha, `outdated`, `outdated_locales`, `source_locale`, `visto_en`, `ido_en`. **Nunca el cuerpo** | vacío; lo llena la primera sincronización |
| `kb_flux_sincronizaciones` | El histórico de pasadas: origen, estado (`ok` / `parcial` / `error`), recuentos y el resumen por locale | — |

La lista de partida de cada tabla sigue en el código
(`services/web_forms/sitios.py`, `services/conocimiento/rutas.py`,
`services/conocimiento/flux.py`): siembra la migración y vale cuando la tabla
está vacía, así que todo funciona igual con o sin filas. El primer cambio desde
la pantalla la escribe en la base.

## Rutas de soporte: la regla en datos

`services/conocimiento/rutas.py`, `Rutas.soporte(web, idioma)`:

1. La fila exacta **web × idioma** (origen `ruta`).
2. Si el idioma no está en ninguna fila de esa web: la fila del **idioma por
   defecto de la web** (origen `idioma_defecto`). mbolasers en italiano →
   `/contacto-mbo-lasers/`; mqeurope en alemán → inglés.
3. Si la web no tiene filas (una web nueva): la **regla de reparto** (origen
   `regla`): castellano → Streamtec (`boprint.net/soporte-tecnico/`); otros →
   MQ Europe (`artisjet-printers.eu/[de|en|pt/]technical-support/`, francés sin
   prefijo; un idioma que MQ Europe no tiene → su por defecto, el francés).
4. Sin web ni idioma, nada: no se inventa una URL.

Las marcas con **soporte propio** (FLUX, MBO Lasers, PimPam) tienen sus filas
en todos sus idiomas, así que la regla no les llega.

Páginas de producto (`Rutas.producto(interes, idioma)`): la fila por interés;
en otro idioma, la misma página con el **prefijo de idioma** si la web lo
publica (qTranslate, o WPML sin slugs traducidos): `dtf` en francés →
`https://www.mqeurope.com/fr/dtf-textile-printing/`; en alemán (no publicado
en mqeurope.com) → la inglesa. Con slugs traducidos (mbolasers) no se adivina.

## FLUX: dos destinos, como datos

- **Cómo se hace algo, software (Beam Studio), calibración, materiales** → la
  **KB de FLUX** en su idioma y en la categoría de su modelo:
  `https://support.flux3dp.com/hc/{locale}/categories/{id}`.
- **Averiada, repuesto, garantía, que vaya alguien** → **nuestro soporte**
  (`fluxlasers.es/soporte/` o `fluxlasers.eu/{idioma}/support/`, por la tabla).
- **No está claro** → el nuestro.

Las palabras que separan un caso del otro están en la configuración
(`conocimiento.flux_palabras_kb` / `flux_palabras_soporte`, con defaults en
`services/conocimiento/config.py`); si hay señales de las dos, gana el soporte.
El modelo se reconoce en el texto del cliente o en la descripción de las líneas
de sus pedidos (`flux.modelo_en_texto`; la palabra más larga gana: «beambox ii»
antes que «beambox»).

Locales: es → `es-419` (no hay `es-es`), en → `en-us`, de → `de-de`,
fr → `fr-fr`, nl → `nl`, pt → `pt-br`; otro idioma → inglés.

Las **FAQ de `fluxlasers.es/preguntas-frecuentes/` están desactualizadas y no
se enlazan nunca**: no están en ninguna tabla.

### Sincronización del índice

`services/conocimiento/zendesk.py`: API pública del Help Center
(`/api/v2/help_center/{locale}/articles.json` y `sections.json`, paginando
`next_page`, sin credenciales). Una pasada completa por locale; primero se
descarga todo el locale y después se escribe, así que **si FLUX no responde el
índice anterior queda intacto** y la pasada acaba `parcial` o `error` con el
motivo. Lo que ya no está en FLUX se marca `ido_en`, no se borra; si vuelve,
deja de estar ido. Los borradores (`draft`) no se indexan. Idempotente.

- **Diaria**: job RQ self-rescheduling `kb:flux_sync` (la escucha
  `worker-sync`), a la hora de la configuración (`flux_sync_hora`, 04:30 de
  Madrid por defecto), interruptor `flux_sync_activa` (encendido). Si ya hubo
  pasada programada hace menos de 12 h, no repite.
- **A mano**: «Sincronizar ahora» en la pantalla (corre en la petición), o
  `python -m app.services.conocimiento.flux_job` en el contenedor `api`.
  **Tras el primer deploy hace falta una pasada a mano**: la diaria no llega
  hasta la noche.

## API

`/api/erp/conocimiento` (capacidad `erp.config`): `GET` (todo lo de la
pantalla), `POST /sitios`, `PATCH /sitios/{clave}`, `POST /rutas`,
`PATCH /rutas/{id}` (URL, nota, activo, orden; la clave no cambia),
`POST /rutas/comprobar` (HEAD/GET a cada URL activa, guarda el código),
`POST /flux/categorias`, `PATCH /flux/categorias/{id}`, `POST /flux/sincronizar`
y `POST /resolver` (`{sitio, idioma, interes, texto, modelo, maquina}` → los
enlaces que saldrían y el catálogo que entraría: es lo mismo que mira el
asistente, `services/conocimiento/enlaces.py` y `catalogo.para_asistente`; el
`texto` —«Lo que escribe el cliente» en «Probar»— cuenta también para el
catálogo: las máquinas que nombra y los intereses que tocan sus palabras).
Cada cambio queda en la auditoría (`kb.changed`).

## Fase 2: el catálogo y los precios verificados (migración 0139)

El asistente no podía escribir «estas son las máquinas que te encajan y esto
cuestan» porque **no tenía los datos**, no porque la regla fallara. La regla
sigue igual: ningún precio que no esté en los DATOS. Lo que entra ahora es el
catálogo, con dos fuentes y nada más:

| Fuente | Qué trae | De dónde | Módulo |
|---|---|---|---|
| `compositor` | Las **máquinas** (el 11/10/2026, contra los datos reales: 42 entradas = 202 filas, 10 marcas; **seis con producto**: flux 14, mbo_dtf 9, artisjet 7, mbo 7, smartjet 3, pimpam 2; las cuatro sin producto —bomedia y las muestras— no cuentan como marcas de catálogo) en cinco idiomas (es raíz + fr, de, en, nl): nombre, descripción, área, dos características, imagen, **precio tal cual**, enlace, visible; y las marcas (logo, color, URL por idioma). **«Consultar» no es un precio**: es la marca de que no hay precio público (`precio_publico=False`, sin texto ni número; el correo dice que se prepara a medida y nunca escribe «Consultar»); «desde 21.500 €» se guarda tal cual. Una entrada **sin precio y sin área de impresión** (los premios de artisJet) no es una máquina: queda `descartada` con su motivo, visible en la pantalla con «Ver descartados», fuera del catálogo del asistente | El JSON del compositor de correos de Bomedia (bomedia-v4), fila `main` de `composer_data` en Supabase. **BoHub solo lee**; la clave publicable va en `.env.production` (`SUPABASE_COMPOSER_KEY`, la misma que ya usa la pestaña «Composer» del selector de plantillas), nunca en el código | `services/conocimiento/catalogo_compositor.py` |
| `woocommerce` | Los **consumibles y repuestos** (y las máquinas de tienda) de cada web con tienda: nombre, precio formateado como lo enseña la tienda, moneda, enlace, categorías, marca, web, idioma. El tipo (máquina / consumible / repuesto) sale de las categorías: es una heurística y se ve en la pantalla | La API pública de la tienda (`/wp-json/wc/store/products?per_page=100`, sin credenciales) de cada web activa de la tabla de sitios, en cada idioma publicado (prefijo de idioma, `lang=`). Una web sin tienda no es un error | `services/conocimiento/catalogo_tiendas.py` |

Todo lo demás **no tiene precio**: un precio que aparece en un correo o pedido
antiguo es histórico y el prompt lo dice (si se cita, con su fecha; nunca como
vigente).

### Tablas

| Tabla | Qué guarda |
|---|---|
| `kb_catalogo` | Una fila por **producto × idioma**: fuente, tipo, marca, `origen_id` (el id del compositor o `{web}:{id de Woo}`), nombre, descripción, características (JSON), **categoría principal** (la primera de la tienda; la lista entera en `datos_json.categorias_nombres`: una de 290 caracteres tiró la primera pasada) o área, imagen, **`precio_texto`** (tal cual: «9.300 €», «9 300 €», «12,50 €», «desde 21.500 €»), `precio_num` (solo para ordenar; vacío sin importe, nunca cero), moneda, `precio_publico` (False = «Consultar»), `descartado` + `motivo_descarte` (0142), enlace, idioma, web, `actualizado_origen` (el `updated_at` de la fila del compositor; en una tienda, la pasada que confirmó el precio), visible, `visto_en`, `ido_en`. **Ningún valor del origen desborda su columna**: se recorta antes de escribir (`catalogo.recortar_a_columna`) |
| `kb_catalogo_marcas` | Las marcas del compositor y su **idioma por defecto** (editable): el precio que se usa cuando el producto no lo tiene en el idioma del cliente. La sincronización casa las filas que ya estaban por su clave (minúsculas, solo letras y cifras) contra el id o el nombre de la marca: «artisJet» y «artisjet» son la misma fila y conserva su idioma |
| `kb_catalogo_sincronizaciones` | El histórico de pasadas por fuente: estado (`ok` / `parcial` / `error`), recuentos, `origen_actualizado_en`, resumen por idioma o por tienda e idioma |
| `kb_interes_productos` | **Interés → máquinas**: interés del clasificador, marca del compositor y patrón del nombre (vacío = toda la marca). Se resuelve contra `kb_catalogo` al montar cada borrador: una fila nueva o un producto nuevo del compositor se aplican sin desplegar |

Lo que desaparece del origen se marca `ido_en`, no se borra. Las dos
sincronizaciones descargan todo antes de escribir: **si Supabase o una tienda
no responden, el catálogo anterior queda intacto** y la pasada lo dice; una
tienda caída no para las demás. La escritura va **por lotes dentro de un
SAVEPOINT** (`catalogo.escribir_filas`): un lote que la base rechaza se
reintenta fila a fila, la que no entra **se salta** con su motivo (`saltadas`,
`errores` en el resumen; estado `parcial`) y la pasada termina siempre,
informando de cuántas filas entraron y cuántas no.

### Las reglas del precio

- **Tal cual**: el correo copia `precio_texto` con su moneda. **Nunca se
  convierte una divisa.** Sin precio en el idioma del cliente, el del idioma
  por defecto de su marca, diciendo de qué idioma es y en qué moneda.
- **Antigüedad**: un precio cuya fuente no se ha confirmado en más de
  `precio_meses_orientativo` meses (6 por defecto) sale como «precio
  orientativo, lo confirmamos al preparar la oferta» (en el idioma del correo).
- **IVA, declarado por fuente**, nunca deducido del número
  (`conocimiento.iva_por_fuente`: `con`, `sin` o sin declarar). El correo
  siempre dice si el precio lleva IVA (o que está pendiente de confirmar),
  avisa de la validación **VIES** para empresas de la UE y de que **un correo
  no es una factura**. Los textos están en cinco idiomas (`catalogo.AVISOS`:
  es, en, de, fr, nl; otro idioma → inglés) y son constantes del código.

### Interés → máquinas (propuesta a revisar)

La propuesta (`catalogo.MAPA_DE_PARTIDA`, lo que se enseña mientras la tabla
está vacía; la tabla ya sembrada **no se re-siembra**: se edita en
Configuración ERP), con el reparto corregido el 11/10/2026 contra los datos
reales y las marcas como ids del compositor: `uv_pequeno` → artisjet Young,
Proud · mbo 1015; `uv_mediano` → artisjet 3000Pro, 5000U · mbo 3050, 4060;
`uv_grande` → artisjet 6090 Trust · mbo 6090, UV1612G, UV1812, UV2513; `dtf` →
**mbo_dtf** (Orion, Lyra, Astra, Vega y sus hornos); `corte_laser` y
`grabado_laser` → **flux**; `packaging` → **smartjet** (overprinters single-pass
sobre cajas y bolsas); `vending` → pimpam. **Hallazgo**: el único interés
comercial sin máquina en el compositor es el **CNC**; para él el asistente
enlaza la página de producto del interés (`kb_rutas`) y no cita ningún precio
ni inventa ninguna máquina. La pantalla enseña con qué casa cada fila, los
huecos y, mientras no se marque «revisada» en los ajustes, el aviso de
propuesta sin revisar; editar una fila que hoy es un hueco hasta que tenga
productos funciona (test).

### Lo que entra en un borrador (`catalogo.para_asistente`)

Nunca el catálogo entero: hasta **6 máquinas** (dos más por cada interés de
más cuando hay varios a la vez, hasta 10) y hasta **8 consumibles o
repuestos**, en su idioma, con precio tal cual, enlace (en la lista de enlaces
permitidos) y los avisos. Las máquinas se eligen **por este orden** (cada una
lleva de dónde salió, `eleccion`, y el bloque del prompt las agrupa así).

**Lo que escribe el cliente** son sus correos del hilo y la **consulta de su
último lead**: el texto del último envío de formulario (o de la nota «form
note» de Agile) con los productos que marcó, o, si no hay ninguno, el que se
clasificó (`contexto.consulta_del_lead`). En la respuesta a un lead el hilo
suele ser solo nuestro acuse de recibo, que cita la consulta pero es nuestro:
hasta el 12/10/2026 solo se miraban los correos del cliente, y el contacto de
prueba «acuserecibo test» (Metallplatten y T-Shirts) se quedaba sin las DTF.

- Cuenta si el lead es de los **últimos 30 días** (`CONSULTA_LEAD_DIAS`) o si el
  hilo que se contesta **empezó con él** (su acuse, o una respuesta a él, aunque
  sea meses después). Un lead de hace un año no entra en un hilo nuevo de otro
  asunto.
- No cuenta si ese lead se clasificó (o se corrigió) como spam, ni con «solo el
  hilo» (no entra la ficha).
- Va también a la FICHA del prompt («Consulta de su último lead (formulario de
  la web, fecha; la escribió el cliente, es lo que pide): «…»», hasta 1.500
  caracteres): las palabras que se le citan a la IA, las puede leer.
- «Lo que ha usado» lo dice: «la consulta de su formulario (fecha)», con el día
  de Madrid.

1. **Lo que nombra el cliente** (`catalogo.menciones_en`): por el
   nombre del producto (entero, dos de sus palabras con peso, un código como
   «5000U», o una palabra escrita como nombre propio a media frase: «la
   Beambox», «el horno Vega»; lo más completo primero). Una sola palabra
   corriente de un nombre sin nada más («I trust you» y la «6090 Trust») y
   los códigos de modelo (DM3H, PT5) que solo están en la descripción, las
   características, el texto alternativo o el id del producto son menciones
   **posibles**: van detrás de las seguras y de la instrucción, delante del
   interés, con su propio rótulo en el prompt («ofrécelas solo si el hilo lo
   confirma»; `eleccion` = `hilo_posible` / `instruccion_posible`). Lo que no
   esté en ningún sitio no se encuentra: los nombres que usa el cliente de
   talentosdiversos (DM3H, SM3, SwapPress PT5) no están en el compositor y
   solo entrarían con una lista de nombres alternativos por producto, que
   sería cosa del compositor.
2. **Lo que pide la instrucción de quien envía**: una máquina concreta, o un
   interés por sus palabras («las máquinas de DTF» → `dtf`; «una láser para
   cortar» → `corte_laser`, `grabado_laser`; «UV grande» → `uv_grande`; «UV» a
   secas → los tres tamaños; `catalogo.intereses_de_texto`). Eso manda.
3. **Los consumibles de la máquina que ya tiene** (lo que dicen sus pedidos,
   casado por palabras contra nombre, categoría y marca de los productos de las
   tiendas), en su lista aparte.
4. **Los intereses que tocan las palabras del cliente** (12/10/2026) y **el
   interés clasificado**. Una consulta en alemán pedía
   imprimir sobre placas de metal y camisetas; el contacto estaba clasificado
   solo como UV y al borrador solo llegaron las máquinas UV, y el aviso dijo que
   no había DTF. Ahora «T-Shirts» o «Textilien» meten las de DTF aunque el
   contacto esté clasificado de otra forma (`eleccion` = `consulta`, con las
   palabras que casaron en el prompt), y si la consulta toca varios intereses
   entran las de todos, por turnos (una de cada, el interés clasificado primero)
   y con el tope subido. «UV» a secas en la consulta de un contacto ya
   clasificado en una talla es esa talla. En lo que escribe el cliente las
   palabras de talla no cuentan: «un gran saludo» no es una UV grande. El prompt
   presenta esas máquinas como lo que *puede* que pida también la consulta: si
   las palabras no hablan de eso, la IA las ignora.

«Sin máquina en el catálogo para X» solo sale cuando X —pedido por la
instrucción o clasificado— no tiene ninguna máquina por el mapa interés →
máquinas, no porque las plazas ya estén llenas con lo que nombra el hilo. El
bloque CATÁLOGO dice además que es una preselección y no el catálogo entero: si
el cliente pide algo que no está, la IA no dice que no lo tenemos.

### Las palabras por interés (`catalogo.PALABRAS_DE_PARTIDA`)

Las palabras que señalan cada interés en la consulta o en la instrucción, en
varios idiomas: `dtf` ← textil*, camiseta*, t-shirt*, shirt*, sudadera*,
hoodie*, ropa, prenda*, kleidung, transfer…; `corte_laser` ← laser*, cortar,
grabar, metacrilato, madera, holz…; `uv` (la UV de cualquier tamaño) ← uv,
uv-drucker, impresora* uv, metallplatte*, placa* metal*…; y cada talla de UV sus
palabras de tamaño, que solo cuentan en la instrucción y si dice también UV (el
código de la talla, «uv_grande», vale solo). Se casan
como las palabras clave del clasificador de leads: palabra o frase entera, sin
acentos ni mayúsculas, `*` al final = prefijo. **Se editan** en Configuración
ERP → Base de conocimiento → interés → máquinas («Palabras que señalan cada
interés»), al momento y sin desplegar; lo cambiado va en
`conocimiento.interes_palabras` (solo los intereses cambiados) y «↺ las de
partida» lo devuelve. Solo se aceptan intereses que existen (los de partida,
los del catálogo de intereses o los del mapa). El «Guardar cambios» de
Configuración ERP no las toca.
API: `PUT /api/erp/conocimiento/catalogo/intereses/palabras/{interes}`
(`{"palabras": [...]}`; `null` = las de partida), con auditoría.

El bloque CATÁLOGO del prompt es la única fuente de precios; un producto sin
precio público lleva la orden de decir que se prepara a medida y no escribir
«Consultar»; si no hay producto, lo dice y el modelo no cita ninguno. Si el
presupuesto de tokens no llega, el catálogo se recorta lo último (consumibles
primero, luego máquinas hasta dejar dos) y queda anotado.

### Sincronización y API

- **Diaria**: job RQ `kb:catalogo_sync` (worker-sync), hora
  `conocimiento.catalogo_sync_hora` (04:45 de Madrid), interruptor
  `catalogo_sync_activa` (encendido). Compositor y tiendas, una tras otra.
- **A mano**: «Sincronizar compositor» / «Sincronizar tiendas» en la pantalla
  (en la petición), o `python -m app.services.conocimiento.catalogo_job` en el
  contenedor `api`. **Tras el primer deploy hace falta una pasada a mano.**
- API (`/api/erp/conocimiento`, capacidad `erp.config`): `GET` trae también
  `catalogo` (estado por fuente, marcas con producto, IVA, `descartados`,
  `sin_precio_publico`) e `interes_productos` (filas con sus coincidencias,
  huecos, hallazgo); `GET /catalogo/productos` (filtros `fuente, tipo, idioma,
  marca, sitio, q, con_idos, con_descartados, limite`);
  `POST /catalogo/sincronizar` (`{fuente}` o las dos);
  `PATCH /catalogo/marcas/{nombre}` (idioma por defecto);
  `POST|PATCH|DELETE /catalogo/intereses[/{id}]`.

## Fase 3: las imágenes de las webs y la tabla de vídeos (migración 0145)

Para los correos compuestos (hero, muestras y vídeo; ver
[ai-features.md](ai-features.md) § Correos compuestos). Pantalla: Configuración
ERP → Base de conocimiento → «Imágenes de las webs» y «Vídeos».

### El índice de imágenes (`kb_imagenes`, `services/conocimiento/imagenes.py`)

La biblioteca de medios de cada web activa del grupo, por la API REST pública
de WordPress (`/wp-json/wp/v2/media?media_type=image`), paginada por
`X-WP-TotalPages` (WordPress devuelve páginas cortas a mitad de la lista: una
página de menos de 100 **no** es la última). Solo entra lo que vale para un
hero o una galería de muestras; medido el 12/10/2026 sobre las ocho webs, de
4.697 imágenes pasan **346**:

1. ancho ≥ 1200 px;
2. apaisada, ancho / alto entre 1,3 y 3,2;
3. el título no es de un logo, icono, favicon, recorte (`cropped`), captura,
   firma, sello o QR (como palabra entera: «silicone», «catálogo» o «confirma»
   no cuentan), ni un código de artículo: de dos a cuatro letras, guion y
   cifras («AB-1234», «MCO-21005_2», «PSTY-007») o una letra y cinco o seis
   cifras («B100617»). Un número de modelo sí vale: suelto («artis 3000U pro»,
   «6090»), con cinco letras o más delante («artis-2100-open»,
   «flex-ultra-1600», que el filtro de antes tiraba: unas quince fotos de
   máquinas) o con las letras de una marca del catálogo («MBO-3050»);
4. no está adjunta a un producto de la tienda (su imagen ya está en el
   catálogo): los productos se leen de la web (`/wp-json/wp/v2/product`) y de
   las tiendas del catálogo;
5. JPEG, PNG o GIF: **Outlook no enseña WebP** (302 imágenes anchas de las
   webs son WebP, casi todas fotos de recambios de fluxlasers.es).

Las webs **no tienen texto alternativo**: el título es lo único por lo que se
busca y el `alt` de la imagen en el correo. De cada imagen se guardan sus
tamaños con las **mismas proporciones** (los recortes cuadrados del tema, fuera)
y el correo pide el que le toca. Favoritas (se le ofrecen a la IA primero) y
ocultas (nunca) se marcan a mano; la sincronización no las toca. Una web caída
deja su índice como estaba; lo que desaparece de la web queda `ido_en`.

### La tabla de vídeos (`kb_videos`, `services/conocimiento/videos.py` y `youtube.py`)

Solo YouTube. Tres orígenes: los **canales** del grupo (configuración
`youtube_canales`, de partida @MQEuropebvba, @artisjeteurope, @fluxinc y
@artisJet, cada uno con la marca que se le supone), los vídeos **enlazados**
en las páginas y entradas de las webs que no son de esos canales y los
**pegados a mano** (una URL suelta; no se van nunca).

- **La API de datos v3** con la clave `YOUTUBE_API_KEY` de `.env.production`,
  en la cabecera `X-Goog-Api-Key` (nunca en la URL ni en un log):
  `channels.list?forHandle=@canal` → la lista de subidas →
  `playlistItems.list` → `videos.list` (título, descripción, duración, idiomas),
  de 50 en 50. Una pasada gasta unas decenas de unidades de las 10.000 diarias.
  Ni RSS ni raspar la página. Sin clave, la pasada queda en error y lo dice.
- **El idioma**, el del título cuando sus palabras lo dicen claro; si no, el
  de los metadatos del vídeo (`defaultLanguage`) y, por último, el del audio
  (`defaultAudioLanguage`). Medido el 12/10/2026: el del audio dice «en» en los
  veinte vídeos de FREEBIRD en neerlandés, alemán, francés, castellano y
  portugués, y «de-DE» en quince vídeos en castellano de mqeurope.
- **El tema** agrupa el mismo vídeo en varios idiomas: se detecta solo cuando
  un canal sube el mismo día varios vídeos de la misma duración, cada uno en un
  idioma (así llegaron los cuatro de FREEBIRD, cada uno en cinco idiomas); el
  resto se etiqueta a mano. Los vídeos de un tema comparten máquinas. Para un
  correo sale la versión del idioma del cliente; si no la hay, la inglesa; si
  tampoco, ninguna.
- **La marca y las máquinas**, por el título: el nombre de la máquina del
  catálogo o su número de modelo dentro de la marca del vídeo («artis 3000U
  Pro» es la «artisJet 3000Pro»; sin marca, un número solo no es ninguna
  máquina), y la marca del canal si el título no la dice. La marca del canal
  se guarda con cada vídeo: volver a lo automático uno no se la quita a los
  demás.
- **La miniatura**: `maxresdefault` (1280×720) si existe, comprobada en
  img.youtube.com; si no, `mqdefault` (320×180), y se guarda cuál se usó: la
  segura se vuelve a probar en cada pasada por si el vídeo se resube en HD.
- **Los «shorts»** (#shorts) se guardan pero no se le ofrecen a la IA.
- Lo corregido a mano (idioma, tema, marca, máquinas) queda fijado
  (`manual_json`) y la sincronización no lo pisa; «↺ automático» lo devuelve.
  Lo que desaparece del canal (si se leyó) o ya no está enlazado en ninguna web
  (si se leyeron todas; con una caída se queda) queda `ido_en`.

### Sincronización y API

- **Diaria**, detrás del catálogo en el mismo job (`kb:catalogo_sync`,
  worker-sync), con su propio interruptor `conocimiento.medios_sync_activa`
  (encendido: solo lectura): apagar el del catálogo no la apaga, y cada grupo
  mira sus propias pasadas para no repetir en 12 h. Una pasada que falla (el
  compositor caído, por ejemplo) no corta las demás. Las pasadas van al
  histórico del catálogo con fuente `imagenes` o `videos`.
- **A mano**: «Sincronizar imágenes» / «Sincronizar vídeos» en la pantalla (en
  la petición, medio minuto cada una).
- API (`/api/erp/conocimiento`, capacidad `erp.config`): el `GET` trae
  `medios` (recuentos, última pasada, canales, si hay clave de YouTube);
  `GET /imagenes` (`sitio, q, favoritas, con_ocultas, limite`),
  `PATCH /imagenes/{id}` (`favorita`, `oculta`), `POST /imagenes/sincronizar`;
  `GET /videos` (`q, idioma, canal, origen, tema, con_ocultos, con_cortos,
  limite`), `POST /videos` (`{url}`), `PATCH /videos/{id}` (`idioma, tema,
  marca, productos, visible, automatico`), `POST /videos/sincronizar`.

## Soporte de terceros e índice de las webs (migración 0148, 27/10/2026)

Dos paneles más en **Configuración ERP → Base de conocimiento**, antes de
«Probar», cada uno con su API y sus tablas (aparte de `/api/erp/conocimiento`):

- **«Soporte de terceros (lista blanca)»** (`soporte_fuentes`): los centros de
  soporte de los fabricantes en los que el redactor busca por su cuenta
  un procedimiento al redactar un correo técnico, con la casilla «Soporte» de la
  franja marcada (desde el 28/10/2026; antes, el botón «Buscar en soporte». La
  Cola SAT ya no tiene consulta propia). Arranca con
  FLUX (la API pública de Zendesk de `support.flux3dp.com`). Solo se descarga
  de estos dominios; «Comprobar» dice
  si una fuente sirve contenido o es una cáscara que se pinta con JavaScript.
  No sustituye a la KB de FLUX de arriba: aquella enlaza la categoría del
  modelo; esta trae el artículo que sale al buscar lo que pregunta el cliente.
- **«Índice de las webs»** (`web_indice`): páginas, entradas y productos (sin
  repuestos ni consumibles) de nuestras webs, una fila por idioma, con
  `visto_en` / `ido_en` como `kb_catalogo`, para que el redactor cite un trozo
  con su enlace (lo consulta por su cuenta al redactar, con la casilla
  «Webs»; «Probar» del panel enseña lo que encontraría). Recorrido mensual en
  worker-web y «Reindexar ahora»; reglas editables de qué entra. Los precios
  son **precio de la web**: se pueden citar, nunca ofertar.

Todo en [fuentes-externas.md](fuentes-externas.md).
