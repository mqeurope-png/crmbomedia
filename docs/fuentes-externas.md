# Fuentes externas del redactor: soporte de terceros, índice de las webs e imagen por URL

El redactor («Ayúdame a contestar») solo podía citar lo que había en la base
de conocimiento y el catálogo. Ahora puede tener delante tres cosas de fuera,
y en las tres la regla es la de siempre: **el modelo redacta, no aporta
hechos**. Lo que dice sale de una página que alguien ha elegido y va con su
enlace.

| Qué | Dónde se usa | De dónde lee | Código |
|---|---|---|---|
| **Imagen por URL** | Botón del editor de correo (también en Plantillas y Firmas, mismo editor) | La URL que pega la persona: solo se comprueba | `api/imagen_url.py`, `frontend/…/email/imagenPorUrl.ts` |
| **Soporte de terceros** | «Buscar en soporte» de la franja del asistente y «Consulta de soporte» de la Cola SAT | Los centros de soporte de la **lista blanca** (`soporte_fuentes`; de partida, FLUX por la API pública de Zendesk) | `services/soporte/`, `erp/api/soporte.py` |
| **Índice de las webs propias** | «Buscar en las webs» de la franja | Las webs WordPress del grupo, por su REST pública y la Store API de WooCommerce | `services/webs/`, `erp/api/webs.py` |

**Nada de esto escribe en FACTUSOL ni en las webs.** Solo se lee, por
endpoints públicos y sin credenciales. Lo que se descarga es **dato, nunca
instrucción** (§ Lo de fuera es dato). Diseño y contratos:
`backend/app/services/externo/` (la base común), `models/fuentes_externas.py`
y la migración 0148.

## La red con cerrojos (`services/externo/red.py`)

**Toda petición a fuera pasa por `red.obtener`**, nunca por httpx a pelo: la
imagen que pega Bart, el soporte y las webs. Una URL que llega del navegador
no puede usarse para que el servidor pregunte a la red de dentro (SSRF):

- **Solo HTTPS** y solo el puerto 443 (el 80 solo si quien llama permite HTTP
  a propósito; hoy nadie lo hace). Sin usuario ni contraseña en la URL.
- **Nunca la red de dentro.** El nombre se resuelve y **todas** sus
  direcciones tienen que ser públicas: fuera `localhost`, `127.0.0.0/8`, los
  privados (10/8, 172.16/12 —la red de Docker—, 192.168/16), enlace local
  (169.254/16, donde vive el metadato de las nubes), CGNAT (100.64/10),
  multidifusión, reservadas, sus equivalentes IPv6 y las IPv4 metidas en IPv6
  (mapeadas, 6to4; Teredo, fuera siempre). También los nombres sin punto (los
  servicios de Docker: `api`, `redis`, `db`) y los `.local`, `.localhost`,
  `.internal`, `.lan`, `.home`, `.corp`, `.localdomain`.
- **Las redirecciones se siguen a mano** (`follow_redirects=False`), como
  mucho 4, y **cada salto se vuelve a comprobar** entero: una URL pública que
  redirige a `http://127.0.0.1/` se para ahí.
- **Lista de hosts** (`hosts_permitidos`): si quien llama la pasa, cada salto
  tiene que ir a uno de esos hosts. El soporte la usa con el dominio de la
  fuente; el índice, con los de la web.
- **Tiempo y tamaño máximos.** El tiempo cuenta para **toda** la petición,
  redirecciones incluidas (la conexión, 10 s como mucho); el cuerpo se lee a
  trozos y se corta al pasar del tope: error, o truncado si quien llama lo
  acepta (la imagen, para medirla; el HTML de una web, cuyo contenido va antes
  que los scripts del pie).

| Quién | Tiempo por petición | Tamaño máximo | Hosts |
|---|---|---|---|
| Imagen por URL | 8 s (HEAD y, si hace falta, GET) | HEAD sin cuerpo; GET cortado a 15 MB | cualquiera público |
| Soporte | búsqueda 8 s, artículo 10 s; **25 s para toda la búsqueda** | 2 MB | el dominio de la fuente |
| Índice de las webs | 45 s, con un reintento si la red falla o da 5xx | JSON 16 MB; HTML 4 MB (se acepta cortado) | los de la web, con y sin `www.`, y sus dominios extra |

**Queda una ventana**, dicha en el código: el nombre se resuelve en
`comprobar_url` y httpx lo vuelve a resolver al conectar; un DNS hostil
podría cambiar de respuesta entre las dos (*DNS rebinding*). Cerrarla del
todo pide conectar a la IP comprobada; con lo que se descarga (imágenes,
artículos y webs públicas, nunca con credenciales) no compensa.

El agente de usuario (`USER_AGENT`) es **solo ASCII**: httpx codifica las
cabeceras en ASCII y una tilde («públicas») rompía `red._cliente()`, es decir,
**toda petición de producción**. Los tests no lo veían porque siempre pasan su
propio cliente.

Tests: `cliente=httpx.Client(transport=httpx.MockTransport(...))` y un
`resolver` falso; el soporte y la imagen tienen `fijar_red_para_tests`.
`tests/test_externo_base.py` prueba los cerrojos.

### Del HTML al texto (`services/externo/html_texto.py`)

Sin `bs4` ni `lxml` (no están en el backend): un árbol mínimo con el
`html.parser` de la biblioteca estándar. `extraer` se queda con el bloque
principal (`article`, `main`, el cuerpo de un artículo de Zendesk, la página
de Elementor) y quita scripts, menús, cabecera, pie, barras laterales,
formularios, avisos de cookies, selectores de idioma y la cabecera, el pie y
los emergentes de Elementor. `limpiar_texto_wp` quita lo que deja WordPress en
la REST (shortcodes de WPBakery, CSS suelto de un carrusel, restos de
comentarios). `es_cascara` reconoce la página que **se pinta con JavaScript**:
menos de 200 caracteres de texto y, además, señales de aplicación de una sola
página (`id="root"`, `__next`, `ng-app`…), cinco scripts o más, o menos de 40
caracteres. `fragmentos` saca los párrafos que tienen que ver con la búsqueda,
recortados.

## Lo de fuera es dato, no instrucción

`services/externo/material.py` (el sobre) y
`services/asistente/material_fuera.py` (el turno). Una página puede traer texto
dirigido al modelo («ignora las instrucciones anteriores y…»):

- Va **encapsulado** entre `<<<MATERIAL DE CONSULTA · …>>>` y
  `<<<FIN DEL MATERIAL>>>`, con su título, su enlace y sus datos (fuente,
  idioma, web, precio web). La página **no puede cerrar el sobre**: `<<<` y
  `>>>` se cambian por `‹‹‹` / `›››`. Si el texto parece una orden
  (`parece_orden`, en los idiomas de la casa), el sobre lo dice.
- **`REGLA_MATERIAL` se añade al prompt de sistema solo cuando el turno lleva
  material** (nada de dentro manda; mandan las instrucciones y la petición),
  con `REGLA_SOPORTE`, `REGLA_WEBS` y la de los enlaces según lo que haya.
- **Va en la petición del turno, no en el bloque de contexto**: ese se congela
  con el turno 0 y las correcciones lo reenvían byte a byte; lo que se elige en
  una corrección no cabría.
- **Sin material, el turno es exactamente el de antes**: sin `soporte`,
  `soporte_sin_resultados` ni `webs` (o vacíos), `Pedido.de` devuelve `None` y
  el prompt de sistema, el contexto y la petición son los mismos byte a byte
  (`tests/test_asistente_fuentes.py`).
- Las guardias de la salida (la del cuerpo y la de Bomedia) no cambian: se
  aplican igual al borrador escrito con material.
- Lo elegido se **lee antes** de llamar al modelo, por fuente + id (el
  artículo) y por id del índice (las webs), **nunca por una URL libre** que
  mande el navegador. Si no se puede leer, no hay borrador: `409` (lo elegido
  ya no vale: el artículo no existe, la fuente se desactivó, las páginas ya no
  están) o `502` (la red), con `code` `soporte_<código>`, `soporte_vacio` o
  `webs_no_encontradas`. Si faltan solo algunas páginas, se escribe con las que
  hay y se avisa.
- Topes: del artículo entran **6.000 caracteres** (`MAX_TEXTO_SOPORTE`; un
  procedimiento de FLUX ronda los 2.000-4.000) y de cada página **900**
  (`MAX_CHARS_WEB`), hasta **4 páginas**.

Lo que ve la persona y lo que hace el borrador con todo esto:
[ai-features.md](ai-features.md) § Lo que no está en el catálogo.

## Soporte de terceros (`services/soporte/`)

### La lista blanca (`soporte_fuentes`)

Es **lo único de Internet** de lo que el soporte descarga. Cada fuente:
clave, nombre, **dominio** (único), tipo (`zendesk`, `wordpress`, `html`),
`base_url`, marca, idiomas → locales, activa, orden, notas y lo que dio
«Comprobar» (`comprobado_en`, `comprobado_estado`, `comprobado_detalle`).

- **Arranca solo con FLUX** (la siembra la migración): «FLUX · centro de
  ayuda», `support.flux3dp.com`, Zendesk, marca `flux`, locales es → `es-419`,
  en → `en-us`, de → `de-de`, fr → `fr-fr`, nl → `nl`, pt → `pt-br`. artisJet y
  MBO se añadirán cuando tengan URL.
- Se edita en **Configuración ERP → Base de conocimiento → «Soporte de
  terceros (lista blanca)»** (`components/erp/SoporteFuentesBloque.tsx`). Una
  fuente nueva **arranca desactivada**: se comprueba antes de activarla.
- **Sin respaldo en el código**: con la tabla vacía no hay fuentes. Una lista
  blanca vacía no se llena sola.
- Al guardar se valida lo que no necesita red: dominio con punto, ni IP ni
  nombres internos, y `base_url` en HTTPS sobre ese mismo dominio. Que no
  resuelva a la red de dentro lo comprueba `red.obtener` en **cada** petición
  (el DNS puede cambiar después de guardar).
- Cambiar el dominio, el tipo, la `base_url` o los locales **borra lo
  comprobado y su caché**; el nombre, las notas, el orden y «activa», no.
- Alta y cambios quedan en la auditoría (`kb.changed`, `target_type`
  `kb_soporte_fuente`); «Comprobar» no se audita, igual que la comprobación de
  rutas.

### Cómo se lee cada tipo: la API primero (`adaptadores.py`)

Cada dominio se lee por su **API pública si la tiene**: el HTML de un centro
de ayuda puede llegar vacío (se pinta con JavaScript) y la API trae el cuerpo
del artículo.

- **`zendesk`** (FLUX): búsqueda en `/api/v2/help_center/articles/search.json`
  (título, enlace, extracto y cuerpo) y artículo en
  `/api/v2/help_center/{locale}/articles/{id}.json`. Sin credenciales.
- **`wordpress`**: `/wp-json/wp/v2/search` y `/wp-json/wp/v2/{tipo}/{id}`, con
  la limpieza de WordPress.
- **`html`**: una web **sin búsqueda**: solo se leen sus artículos por enlace,
  y el enlace solo vale si es de su dominio (si no, `dominio_no_permitido` sin
  hacer ninguna petición). Del HTML se queda el bloque principal; si es una
  cáscara, es un error (`cascara_vacia`), **nunca un texto vacío que parezca un
  artículo**.

El HTML para leer en pantalla se sanea con la lista de `html_sanitizer.py`
pero con una llamada propia a bleach (fuera comentarios, `data:` y `cid:`;
antes se quitan enteros `script`, `style`, `iframe`, `object`, `embed`,
`noscript`, `template` y `svg`; las rutas relativas pasan a absolutas) y se
pinta en un `iframe sandbox=""`.

### «Comprobar»

Por fuente, desde la lista blanca (también con la fuente desactivada, para
probarla antes de activarla). Zendesk: una búsqueda con la marca (o la primera
palabra del nombre) y, si no sale nada, el listado de artículos; WordPress:
una búsqueda sin palabra y el primer contenido; `html`: la `base_url`.
Resultado guardado: **`ok`** (sirve contenido: en Zendesk y WordPress, un
artículo con 200 caracteres de texto o más; en `html`, una página que no es
cáscara), **`vacio`** (cáscara que se pinta con JavaScript, o artículos sin
texto) o **`error`** (con el detalle). La pantalla avisa si una fuente **activa** dio
`vacio` o `error`.

### Idiomas, caché y errores

- **Idioma**: sin idioma se busca en castellano. El locale sale de los locales
  de la fuente; si no hay resultados, se busca en inglés y se dice en los
  avisos; si la fuente no tiene ese idioma, se va directo al inglés. Sin
  locales no se manda ninguno (el que dé la web). El buscador deja elegir el
  idioma (el taller puede leer en inglés un artículo no traducido).
- **Caché** (`soporte_cache`, `cache.py`): una búsqueda vale **1 día** (un
  artículo nuevo tiene que salir pronto) y un artículo **7 días** (cambian poco
  y el borrador lleva el enlace, que abre siempre el vigente). Clave: sha256 de
  `tipo|fuente|idioma|consulta-o-id`. Lo que falla no se guarda. Los caducados
  se borran al escribir (sin tarea programada). Se escribe en un SAVEPOINT: una
  carrera no tumba la petición.
- **Topes**: consulta de 2 a 300 caracteres, 6 resultados por defecto (20 como
  mucho), extracto de 300 caracteres, texto del artículo de 60.000 como mucho.
  Varias fuentes salen por turnos (que la primera no tape a las demás); si la
  búsqueda pasa de 25 s, las que faltan se saltan con un aviso.
- **«No he podido mirar» no es «no hay nada».** Si **todas** las fuentes
  consultadas fallan, la búsqueda da error (la de la fuente, o
  `sin_respuesta` 502 con varias), no una lista vacía: el redactor no dirá
  nunca «no hay procedimiento» sin haber mirado. Un 404 en una búsqueda es
  `respuesta_fuente` 502 («revisa el tipo y la dirección base»), no «no
  encontrado». Errores como `{"detail": {"code", "detail"}}`: 400 datos, 403
  dominio o red interna, 404 fuente desconocida o inactiva, 409 ya existe, 422
  cáscara o sin contenido, 502 red o fuente, 504 tiempo agotado.

**Buscar va aparte, antes de redactar.** El nginx de Plesk corta las
peticiones a los 60 s: una búsqueda dentro de la generación del borrador se lo
comería. Por eso es una acción explícita con su propio tope.

### El procedimiento se cita, no se improvisa

Lo que exige el prompt (`REGLA_SOPORTE`) y lo que asegura el código, sin
depender de que el modelo obedezca:

- Los pasos se traducen al idioma del correo; **referencias, modelos, códigos
  de error, números de pieza, nombres de menú y valores van literales**. No se
  añaden pasos, no se cambia su orden ni se rellenan huecos de memoria. Si el
  artículo no cubre lo que se pregunta, el correo lo dice.
- **El enlace va siempre.** Si el modelo no lo puso, se añade al final
  (`redactor._aplicar_material`): «Procedimiento completo: URL» en el idioma
  del correo (es, en, de, fr, nl, pt, it, ca; otro → inglés), con la URL como
  texto del enlace y no el título (texto de un tercero sin pasar por las
  guardias). En un correo compuesto, un bloque de texto al final; en una
  corrección sobre una selección no se añade nada (partiría el párrafo) y el
  aviso lleva la URL para ponerla a mano.
- **Si la búsqueda no encontró nada**, la persona puede pedir el borrador
  igualmente («Escribir diciendo que no hay procedimiento», solo si se miró en
  algún centro): el correo dice que no hay un procedimiento publicado (que lo
  estamos revisando o lo consultamos con el fabricante) y **no escribe pasos**.
  Si aun así el borrador trae una lista de dos elementos o más, o dos líneas
  numeradas o con guion, la franja avisa (el cuerpo no se toca). Es lo más
  importante de todo el bloque.

### En la Cola SAT, como consulta

Botón **«Consulta de soporte»** en la barra de la Cola SAT
(`components/soporte/SoporteConsultaModal.tsx`): el mismo buscador
(`SoporteBusqueda.tsx`) **sin «Usar en el borrador»**. El taller (rol `sat`)
busca, lee el artículo y abre el enlace en su web; **no envía nada** ni mete
nada en ningún borrador, y sigue sin poder enviar correos (#562).

## Índice de las webs (`services/webs/`)

### La tabla (`web_indice`)

Una fila por **web × tipo (página, entrada, producto) × id de WordPress ×
idioma** (clave única: reindexar dos veces no duplica): dominio del que se
leyó, URL, slug, título, **texto limpio** (sin HTML, shortcodes ni CSS),
extracto (500 caracteres), imagen y galería, categorías, precio web, fecha de
WordPress, huella. El patrón de `kb_catalogo`: lo que se ve lleva `visto_en`;
**lo que desaparece se marca `ido_en`, no se borra**, y deja de salir en la
búsqueda; si vuelve, deja de estar ido. La **huella** (sha256 del contenido,
sin la fecha de WordPress) cuenta los actualizados sin reescribir lo igual.
Cada fila se escribe en su SAVEPOINT: una que la base rechaza se salta con su
motivo.

**No se vacía por un fallo.** Una web, un idioma o un tipo que falla no marca
nada como ido; una lista que la web devuelve vacía (200 con cero elementos)
tampoco. Sí se van: lo que la web devuelve pero las reglas sacan (así una regla
nueva saca lo que ya estaba), los productos de una web sin tienda, los idiomas
quitados de la tabla de sitios y, al cerrar una pasada que no ha fallado
entera, lo de las webs que ya no se indexan.

### Cómo se lee una web (`lector.py`)

Las webs son las **activas de `sitios_web`** (tipo web), con su idioma por
defecto, sus idiomas publicados y su plugin de traducción.

- **Siempre `?rest_route=`** (`https://web/?rest_route=/wp/v2/pages&per_page=100&page=N&_fields=…`),
  con `&` para los parámetros: `mboprinters.com` lo necesita y funciona en
  todas. 100 elementos por petición, 30 páginas de resultados como mucho, una
  pausa de 0,25 s entre peticiones (comparten servidor con la tienda).
- **Una fila por idioma, sin mezclar**:
  - **WPGlobus** (`qtranslate`: artisjet-printers.eu, mboprinters.com): prefijo
    `/xx/` y los mismos ids en todos los idiomas. Una página sin traducir sale
    con el texto del idioma por defecto y la URL del otro; solo se indexa el
    idioma xx si `translation.languages.xx.content` lo dice (pedido con título
    y contenido en `_fields`: sin ellos WPGlobus dice que nada está traducido).
    En productos (la Store API no trae `translation`) es respaldo si el texto
    es idéntico al del idioma por defecto. Si el idioma por defecto falla en un
    tipo, los demás de ese tipo quedan en error: sin él no se distingue
    traducción de respaldo.
  - **WPML** (mbolasers.com, pimpam-vending.com): `&lang=xx`, cada idioma con
    sus ids. Un idioma sin traducción vuelve **en silencio** al de por defecto:
    se descarta lo que repite un id del idioma por defecto o no lleva el idioma
    en el enlace. Si **todo** lo de un idioma es respaldo, no está publicado y
    se salta en todos los tipos. Si `pt` solo da respaldo se prueba `pt-pt` y
    `pt-br` (pimpam publica `pt-pt`); la fila se guarda como `pt`.
- **Páginas y entradas** fuera: por regla de slug o de id, con **menos de 150
  caracteres** de texto limpio (portadas que pinta el tema, «contacto»,
  «gracias»: las útiles tienen de mediana más de 2.000), con «lorem ipsum» de
  plantilla y los textos idénticos en el mismo idioma (se queda el id más
  bajo).
- **mbolasers.com**: si la lista con contenido da 5xx (tras el reintento), se
  pide sin contenido y cada página se lee de su **HTML público** (el bloque
  `wp-page` de Elementor, sin cabecera ni pie) con el idioma del `<html lang>`.
  Una página cuyo HTML falla se conserva como estaba.
- **Productos por la Store API** (`/wc/store/v1/products/categories` y
  `/products`): las categorías de fuera se filtran ya en el servidor
  (`category_operator=not_in`) y se revisan otra vez aquí. **Cualquier
  categoría de fuera manda** (también una madre: un repuesto colgado de
  «Accessoires et pièces de rechange» y de «Pièces de rechange A5000» queda
  fuera), **sin categoría → fuera**, y encima de todo las **excepciones por
  id** (si un id está en las dos, gana excluir: no ofrecer un repuesto como
  máquina). Los «incluir» se piden aparte con `include=`, porque el `not_in`
  del servidor los quitaría. Una web **sin tienda** (`rest_no_route`) no es un
  error: hay webs **solo de páginas** (pimpam).
- **Imágenes**: la principal y la galería del producto, como URL https
  **enlazadas en caliente** (nunca la biblioteca de medios: esa es
  `kb_imagenes`).
- **fluxlasers.es no se indexa**: FLUX se lee de **fluxlasers.eu**
  (`conocimiento.webs_indice_hosts`, de partida `{"fluxlasers":
  "fluxlasers.eu"}`).

### El precio de la web

Decisión de la casa: **«como dice la web»**. Se guarda el precio que enseña
la web (`price_html` sin el precio tachado de una rebaja ni el texto para
lectores de pantalla) y el número (`prices.price` / 10^decimales; en un
producto variable, el mínimo, y el texto enseña el rango), con su IVA **según
lo que pone la web** junto al precio (`+ IVA`, `zzgl. MwSt`, `HT`… → `sin_iva`;
`IVA incl.`, `TTC`… → `con_iva`; si no dice nada, `desconocido`), nunca
deducido del número. **Un precio a 0 («a consultar») no se guarda.**

El redactor puede decir «en la web está a X» citándolo como precio de la web
y con su IVA; **nunca oferta con él** (el de una oferta sale de FACTUSOL y del
CATÁLOGO). En el prompt va como «Precio en la web: … (sin IVA, según la web) —
NO es precio de oferta».

### La búsqueda (`busqueda.py`)

- **MySQL**: `MATCH(titulo, texto) AGAINST(… IN BOOLEAN MODE)` con las mismas
  columnas del índice FULLTEXT `ft_web_indice_titulo_texto` (InnoDB exige que
  coincidan). Fuera los operadores; cada palabra de 3 letras o más con `*`
  (prefijo).
- **Las palabras de 1 o 2 letras** («UV», «A3») no están en el FULLTEXT
  (`innodb_ft_min_token_size` es 3 y no se toca el servidor): van por `LIKE`.
- **Cualquier otra base** (SQLite en los tests): todo por `LIKE`.
- Primero las filas con **todas** las palabras; si no hay, con alguna. Fuera
  las palabras vacías de es/en/fr/de/nl/pt y las de InnoDB (con `+palabra` una
  vacía dejaría la búsqueda sin nada). Como mucho 8 palabras y 200 candidatos.
- Se puntúa en Python: el título pesa ×5, las repeticiones en el texto cuentan
  hasta 5, +15 si el título trae la búsqueda entera, y cuanto más del título
  es la búsqueda, más (la «Beambox II 55W» antes que el «Extension Pocket –
  Beambox II»); la relevancia de MySQL desempata.
- Solo lo vivo (`ido_en IS NULL`). Devuelve **fragmentos con su URL**: 8
  resultados de 700 caracteres como mucho, nunca la página entera.

### Recorridos: una vez al mes, en worker-web

- **Una pasada** (`web_indice_pasadas`, `pasadas.py`) recorre las webs **una a
  una, un trabajo RQ por web** en la cola **`webs:indice`**, la **última** de
  `worker-web`. Por qué: worker-web es un solo proceso y atiende los webhooks de
  los pedidos; un recorrido entero los retrasaría minutos, y así entran entre
  web y web (ver [workers-colas.md](workers-colas.md)). Cada trabajo hace su
  web, apunta su resumen y encola la siguiente; el último cierra la pasada:
  `ok`, `parcial` (alguna web falló) o `error` (todas). Tope por trabajo: 15
  minutos. Si no puede encolar la siguiente, sigue en el mismo trabajo.
- **Nunca se recorre en la petición.** Si ya hay una pasada en cola o en
  marcha, se devuelve esa. Una que lleva **más de 2 h sin moverse** (worker
  reiniciado a media pasada) se da por muerta y no bloquea otra. Sin Redis
  fuera de producción (desarrollo) se recorre en línea; en producción, la
  pasada queda en `error` con el motivo.
- **El tic diario** (`job.py`, el patrón de `catalogo_job` y `flux_job`:
  SETNX + `enqueue_in`, re-armado en `finally`, armado al arrancar el API):
  cada día a `conocimiento.webs_indice_hora` (**05:20 de Madrid**, fuera de
  FLUX a las 04:30 y del catálogo a las 04:45) mira si toca, y solo lanza la
  pasada programada si la última programada `ok` o `parcial` tiene más de
  `conocimiento.webs_indice_dias` días (**30**, de 1 a 365) y no hay otra en
  marcha. Por qué un tic diario y no un `enqueue_in` de 30 días: un trabajo
  programado que se pierde (Redis vaciado, un despliegue) dejaría la cadena
  muerta un mes; así se pierde como mucho un día. Interruptor
  `conocimiento.webs_indice_activo` (encendido: solo lee webs públicas).
  `worker-web` lleva `--with-scheduler`, que mueve el tic.
- **A mano**: **«Reindexar ahora»** en Configuración ERP → Base de
  conocimiento → «Índice de las webs» (encola; la pantalla se refresca cada 5 s
  mientras la pasada está en cola o en marcha), o
  `python -m app.services.webs.job` en el contenedor `api` (ignora el
  interruptor y los días).

Los cuatro ajustes (`webs_indice_activo`, `_hora`, `_dias`, `_hosts`) viven en
el blob `conocimiento` de `ErpSettings`
(`services/conocimiento/config.py`), validados; no tienen control propio en la
pantalla.

### Las reglas (`web_indice_reglas`)

Qué entra y qué no, **editable** en el panel («Reglas: qué entra en el índice y
qué no»): las webs crecen. Cinco tipos; cada regla vale para una web o para
todas (web vacía). Se aplican en el siguiente recorrido.

| Tipo | Patrón |
|---|---|
| `pagina_excluir_slug` | Expresión regular, sin distinguir mayúsculas, sobre el slug decodificado de páginas y entradas |
| `pagina_excluir_id` | Id de WordPress de una página o entrada (en WPML, el de cada idioma) |
| `categoria_fuera` | Un producto queda fuera si alguna de sus categorías, o una madre, **empieza** por este nombre (sin tildes ni mayúsculas) |
| `producto_excluir_id` | Id de producto que no entra aunque su categoría sea de dentro |
| `producto_incluir_id` | Id que entra aunque una regla lo saque (excluir gana) |

Las **de partida** (`services/webs/reglas_partida.py`) las siembra la
migración y valen mientras la tabla esté vacía (un test con `create_all` se
comporta como producción); la pantalla siembra la tabla la primera vez que la
abre. Ojo: **borrar todas** las reglas devuelve las de partida; para quitar
una, se desactiva.

- **Páginas fuera por slug** (medido el 27/10/2026 en las webs a las que se
  llega): cookies; privacidad (`privac|datenschutz|proteccion-datos|confidentialit`);
  aviso legal (`aviso-legal|legal-notice|legal-warning|mentions-legales|impressum|juridisch|rechtliche|avis-juridique`);
  condiciones y términos (`condicion|condition|terms|voorwaarden|^agb$|contratacion|contratacao|clausulas-informativas`);
  devoluciones (`devoluci|^returns?$|retours?|widerruf|refund`); carrito
  (`carrito`, `cart`, `basket`, `panier`, `einkaufswagen`, `winkelkarretje`,
  `winkelwagen`); pago (`checkout`, `finalizar-compra`, `pago`,
  `pasarela-de-pago`, `zur-kasse`, `paiement`, `afronden`, `compra-finalizada`,
  `orders`); mi cuenta (`mi-cuenta`, `my-account`, `mein-account`,
  `mon-compte`, `mijn-account`, `account`); aviso de formulario enviado
  (`enviado`, `sent`, `gesendet`, `envoye`, `verstuurd`); páginas de prueba
  (`^test|-test$|test-form|testagile|sample-page|pagina-ejemplo`); páginas
  internas (`^dpd-|^app-posts|^app-crea|cowork`); declaración de accesibilidad.
- **Categorías fuera**: Repuestos, Recambios, Pièces de rechange, Consumibles,
  Tintas (incluye «Tintas Ador», «Tintas UV Led…»), Encres, Cartuchos, Packs de
  servicio, Services, Servicios, Reacondicionadas y Autres pièces de rechange.
  Dentro quedan las máquinas y los accesorios, también los accesorios por
  modelo.
- **Excepciones por id**, encontradas **repasando producto a producto el
  recorrido real**: artisjet-spain.es mete recambios en «Accesorios» y
  artisjet-printers.eu mezcla consumibles en «Accessoires et pièces de
  rechange»:

  | Web | Id | Regla | Por qué |
  |---|---|---|---|
  | artisjet-es | 470 | excluir | Cabezal Epson DX5: recambio en «Accesorios» |
  | artisjet-es | 474 | excluir | Cabezal Epson L801: recambio en «Accesorios» |
  | artisjet-es | 481 | excluir | Damper Textil y EcoS: recambio en «Accesorios» |
  | artisjet-es | 485 | excluir | Damper UV Led: recambio en «Accesorios» |
  | artisjet-es | 2026051305 | excluir | artisJet DIVE (copia): producto duplicado |
  | artisjet-eu | 1892 | excluir | Polyester cleaning wipes: consumible |
  | artisjet-eu | 1989 | excluir | Syringe Kit: consumible |
  | artisjet-eu | 2979 | excluir | Hojas y films UV DTF: consumible |
  | artisjet-eu | 477 | incluir | Plantillas y moldes a medida: accesorio colgado de «Autres pièces de rechange» |

En WPML las reglas por nombre se aplican a los **nombres traducidos**: si una
web WPML tuviera «Ersatzteile», habría que añadir ese prefijo.

## Permisos

| Qué | Capacidad | Quién |
|---|---|---|
| Soporte: buscar y leer (`/api/erp/soporte/buscar`, `/articulo`) | `erp.sat.view` | comercial, pedidos, **sat**, user/manager (legacy) y admin; viewer no |
| Índice: buscar (`/api/erp/webs/buscar`) | `erp.access` | cualquiera con ERP (los mismos); viewer no |
| Lista blanca y «Comprobar»; estado del índice, «Reindexar ahora» y reglas | `erp.config` | admin |
| Imagen por URL (`/api/emails/imagen-url/comprobar`) | usuario con sesión | cualquiera que use el editor |

Los buscadores de la franja llaman a esas rutas del ERP: quien use el
asistente sin acceso al ERP recibe un 403 al buscar. Detalle del rol `sat` en
[perfiles-y-permisos.md](perfiles-y-permisos.md).

## Migración 0148 (`20261027_0148_fuentes_externas.py`)

Solo **crea** tablas (no toca datos que ya existan):

1. `soporte_fuentes`, sembrada con FLUX;
2. `soporte_cache` (índice por `expira_en`);
3. `web_indice` (índices por web e idioma y por `ido_en`; en MySQL, el
   FULLTEXT sobre título y texto);
4. `web_indice_reglas`, sembrada con las reglas de partida (12 de slug, 12 de
   categoría, 8 excluir y 1 incluir: las de arriba, copiadas en la migración
   porque una migración no importa la aplicación);
5. `web_indice_pasadas`.

Texto largo en `MEDIUMTEXT` en MySQL (`TEXT` se queda en 64 KB). Bajar borra
las cinco tablas. Test: `tests/test_migration_0148_fuentes_externas.py`.

**El índice nace vacío.** El primer recorrido llega en el **siguiente tic de
las 05:20**, que lanza la pasada porque no hay ninguna programada anterior, o
antes con **«Reindexar ahora»**.

### Tras desplegar

1. Recrear `worker-web`: ahora escucha también `webs:indice`
   ([workers-colas.md](workers-colas.md)). Sin eso, el tic y «Reindexar ahora»
   se quedan en cola.
2. **«Comprobar» en FLUX** (lista blanca): desde el entorno de pruebas no se
   llegaba a `support.flux3dp.com` (abajo).
3. **«Reindexar ahora»** (o esperar a las 05:20) y mirar en el estado que
   fluxlasers.eu tiene filas y en qué idiomas.

## API

- `GET /api/erp/soporte/buscar?q=&idioma=&fuente=` — `{consulta, resultados,
  avisos, fuentes}`; cada resultado con fuente, título, extracto, URL, idioma.
- `GET /api/erp/soporte/articulo?fuente=&id=&idioma=` — el artículo limpio
  (texto y HTML saneado), por fuente + id (o enlace del artículo en su dominio).
- `GET|POST /api/erp/soporte/fuentes`, `PATCH /api/erp/soporte/fuentes/{clave}`
  (la clave no cambia), `POST /api/erp/soporte/fuentes/{clave}/comprobar`.
- `GET /api/erp/webs/buscar?q=&idioma=&sitio=&tipo=` — `{items}` (fragmentos
  con URL, imagen, galería, categorías y precio web).
- `GET /api/erp/webs/estado` — por web y tipo lo vivo, los idos, los idiomas y
  la última lectura; la última pasada y la que está en curso.
- `POST /api/erp/webs/reindexar` — encola (o devuelve la que hay).
- `GET|POST /api/erp/webs/reglas`, `PATCH|DELETE /api/erp/webs/reglas/{id}`.
- `POST /api/emails/imagen-url/comprobar` `{url}` — responde **siempre 200**:
  `{ok: true, url, content_type, bytes, pesada, aviso}` o `{ok: false, url,
  codigo, motivo}` (`no_https`, `red_interna`, `no_resuelve`, `no_responde`,
  `no_es_imagen`, `url_invalida`, `puerto_no_permitido`, `tiempo_agotado`). No
  guarda nada: la URL que entra es la que sale.
- Las tres peticiones del asistente que redactan aceptan `soporte`,
  `soporte_sin_resultados` y `webs` ([ai-features.md](ai-features.md)).

Las formas de respuesta son las de `frontend/src/app/lib/soporteApi.ts` y
`websApi.ts`; los errores, `{"detail": {"code", "detail"}}` como el resto del
ERP.

## Imagen por URL

Bart pega el enlace de una imagen y va en el `<img>` **tal cual, enlazada en
caliente**: no se descarga para guardarla, no se rehospeda, no va a ninguna
biblioteca. Por eso, a diferencia de las subidas, **no se incrusta al enviar**
(el cambio a `cid:` solo toca los assets propios): el cliente de correo la
carga de su web, y si la web la quita, desaparece del correo.

Antes de insertarla, el diálogo (`imagenPorUrl.ts`, botón «Imagen por URL»
junto a «Imagen») la comprueba en el backend: **HTTPS** (si no, no se inserta
y se dice por qué), que no apunte a la red interna, que responda **200 con
`Content-Type: image/*`**. Primero un HEAD (8 s): 404/410 → no responde; 200
con otro tipo → no es imagen, sin GET. Si el HEAD no vale (405/501, sin tipo,
sin tamaño), un GET cortado a 15 MB para medir. **Más de 1 MB se inserta igual
con aviso**; un SVG lleva el suyo (Gmail y Outlook no lo enseñan). Una URL de
más de 2.048 caracteres no vale. «Insertar» solo se habilita tras una
comprobación buena **de ese mismo enlace**, con la **vista previa** a la vista;
si la web no deja enseñarla fuera, la vista previa lo dice. Se inserta con
`alt=""` y `max-width:100%`.

## Lo que se comprobó contra las webs reales (27/10/2026)

Un recorrido **de verdad, de punta a punta**: «Reindexar ahora» por la API,
la cola `webs:indice` con su worker, las webs reales (sin dobles), las reglas
de partida **con** las excepciones por id, y un **MySQL 8** con la cadena de
migraciones entera hasta la 0148 (el FULLTEXT incluido). Filas vivas:

| Web | Peticiones | Páginas | Entradas | Productos | Idiomas |
|---|---|---|---|---|---|
| artisjet-printers.eu | 21 | 98 | 8 | 45 | de, en, fr, pt |
| artisjet-spain.es | 5 | 28 | 0 | 16 | es |
| boprint.net | 5 | 10 | 6 | 36 | es |
| fluxlasers.eu | — | — | — | — | no se llega desde el entorno de pruebas |
| mbolasers.com | 25 | 48 | 25 | 107 | de, en, es, fr, nl |
| mboprinters.com | 17 | 42 | 1 | 68 (todos a 0: sin precio) | de, en, es, fr |
| pimpam-vending.com | 14 | 27 | 0 | sin tienda | de, en, es, fr, nl, pt |
| www.mqeurope.com | 13 | 44 | 4 | 122 | en, fr, nl |
| **Total** | | **297** | **44** | **394** | **735 filas** |

La pasada entera tarda unos 4 minutos. **La segunda, seguida: 0 nuevas, 0
actualizadas y 0 idas** en todas las webs (no duplica). FLUX falla limpia
(«No se encuentra el dominio fluxlasers.eu») y la pasada sigue con la
siguiente web.

- **Repaso de los productos, uno a uno:** máquinas y accesorios, también los
  de cada modelo («Accesorios Beambox/Hexa/Beamo», «Complementos Ador», los
  dispositivos giratorios de mbolasers). Lo que se colaba en artisJet
  (cabezales, dampers, toallitas, jeringas, hojas y films, y un duplicado
  «(copia)») son las excepciones por id de arriba; repetida la lectura, ya no
  sale ninguno. Ni un tubo, espejo, lente, tinta, cartucho, servicio ni
  reacondicionada.
- **Repaso de las páginas:** ni legales, ni carrito, ni pago, ni cuenta. Hay
  páginas con título interno y contenido de verdad («fre eng» y «3000 freebird
  aleman» en artisjet-printers.eu, «4060 fran» y «mbo 3020 english» en
  mboprinters, «artisjet spain v2»): si no se quieren, se quitan por id desde
  el panel.
- **Idiomas:** ninguna fila lleva marcas de qTranslate/WPGlobus (`[:en]`,
  `{:en}`, `<!--:-->`): un idioma por fila. mqeurope publica en, fr y nl (no
  es); sus categorías de impresoras no están traducidas y salen en inglés.
- **Búsqueda en MySQL** (`MATCH … AGAINST` en modo booleano): «horno DTF», los
  hornos de boprint y mboprinters; «Trust 6090», la de mqeurope en sus tres
  idiomas y la de artisjet-spain; «Braille», la página de artisjet-printers.eu
  en cada idioma; «Orion 20», «UV DIVE», «CO2 desktop», bien. Los operadores
  que escriba alguien (`+horno -dtf`, comillas, `*`) se limpian antes y no
  rompen la consulta.
- **Precios** (sonda previa, con FLUX leído de `fluxlasers.es`): FLUX, 34 de 34
  con «(+ IVA)» → sin IVA; mbolasers «+ IVA» y «zzgl. MwSt» → sin IVA; boprint
  «(+IVA)» → sin IVA; artisjet-eu «€15,00» → desconocido.
- **Imagen por URL** contra direcciones reales: una imagen https de boprint →
  `ok` (image/jpeg, 141 KB); la misma en http → `no_https`; `127.0.0.1`,
  `10.0.0.1`, `redis` → `red_interna`; `localhost:8000` → `puerto_no_permitido`;
  la portada de boprint → `no_es_imagen`.

**Lo que no se pudo comprobar.** Desde el entorno de pruebas **no se llega a
`fluxlasers.eu` ni a `support.flux3dp.com`** (no resuelven). Por eso:

- **El soporte de FLUX** se probó con la forma de las respuestas de Zendesk
  sacada de su documentación pública, no de una respuesta real; la ruta de
  producción sí se recorrió sin dobles y falla limpia («No se encuentra el
  dominio support.flux3dp.com», 502, y «Comprobar» guarda `error`).
- **La web de FLUX** se probó con `fluxlasers.es` en su lugar, sin cambiar el
  defecto `.eu`. Si `.eu` publica sus idiomas con WPML por dominio en vez de
  por directorio, el enlace no llevaría `/xx/` y esos idiomas se descartarían
  como respaldo (no mezcla ni duplica, pero tampoco los indexa).

Las dos cosas **hay que mirarlas tras el despliegue** (§ Tras desplegar).

## Tests

`test_externo_base.py` (red, HTML, sobre), `test_soporte.py` y
`test_soporte_api.py`, `test_webs_indice.py` y `test_webs_api.py` (con un
WordPress simulado de datos realistas en `test_webs_comun.py`),
`test_imagen_url.py`, `test_asistente_fuentes.py` (el material en el turno y el
turno sin material idéntico), `test_migration_0148_fuentes_externas.py`,
`test_web_ingest_worker_isolation.py` y `test_erp_worker_queues.py` (la cola
`webs:indice`, última y solo en worker-web).
