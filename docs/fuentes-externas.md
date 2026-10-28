# Fuentes externas del redactor: soporte de terceros, índice de las webs e imagen por URL

El redactor («Ayúdame a contestar») solo podía citar lo que había en la base
de conocimiento y el catálogo. Ahora puede tener delante tres cosas de fuera,
y en las tres la regla es la de siempre: **el modelo redacta, no aporta
hechos**. Lo que dice sale de una página concreta (la imagen que pega quien
escribe, o el artículo y las páginas que el asistente consulta al redactar) y
va con su enlace.

Desde el 28/10/2026 **nadie busca ni elige**: con las casillas «Webs» y
«Soporte» de la franja, el asistente consulta por su cuenta nuestras webs y
el soporte del fabricante mientras redacta, igual que consulta el catálogo
(§ Cómo consulta el asistente al redactar). Con #564 eran dos botones,
«Buscar en soporte» y «Buscar en las webs», con los que la persona buscaba y
elegía antes de redactar; se quitaron junto con sus rutas de búsqueda del
soporte.

| Qué | Dónde se usa | De dónde lee | Código |
|---|---|---|---|
| **Imagen por URL** | Botón del editor de correo (también en Plantillas y Firmas, mismo editor) | La URL que pega la persona: solo se comprueba | `api/imagen_url.py`, `frontend/…/email/imagenPorUrl.ts` |
| **Soporte de terceros** | Casilla «Soporte» de la franja del asistente: lo consulta por su cuenta al redactar un correo técnico | Los centros de soporte de la **lista blanca** (`soporte_fuentes`; de partida, FLUX por la API pública de Zendesk) | `services/soporte/`, `services/asistente/consulta_fuentes.py`, `erp/api/soporte.py` (la lista blanca) |
| **Índice de las webs propias** | Casilla «Webs» de la franja: lo consulta por su cuenta al redactar. «Probar» del panel del índice | Las webs WordPress del grupo, por su REST pública y la Store API de WooCommerce | `services/webs/`, `services/asistente/consulta_fuentes.py`, `erp/api/webs.py` |

**Nada de esto escribe en FACTUSOL ni en las webs.** Solo se lee, por
endpoints públicos y sin credenciales. Lo que se descarga es **dato, nunca
instrucción** (§ Lo de fuera es dato). Diseño y contratos:
`backend/app/services/externo/` (la base común), `models/fuentes_externas.py`
y las migraciones 0148 y 0149.

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
- **Se comprueba el nombre al que va httpx**, no otro: la URL se lee con
  `httpx.URL` y se resuelve su host ASCII (`straße.example.com` →
  `xn--strae-oqa.example.com`, IDNA2008), no el que `getaddrinfo` sacaría del
  nombre con tildes (IDNA2003, `strasse…`). Lo que httpx no acepta (un
  tabulador o un salto de línea pegados en medio) es `url_invalida`, nunca un
  500.
- **Las redirecciones se siguen a mano** (`follow_redirects=False`), como
  mucho 4, y **cada salto se vuelve a comprobar** entero: una URL pública que
  redirige a `http://127.0.0.1/` se para ahí.
- **Lista de hosts** (`hosts_permitidos`): si quien llama la pasa, cada salto
  tiene que ir a uno de esos hosts. El soporte la usa con el dominio de la
  fuente; el índice, con los de la web.
- **Tiempo y tamaño máximos.** El tiempo cuenta para **toda** la petición,
  redirecciones incluidas (la conexión, 10 s como mucho); el cuerpo se lee a
  trozos y se corta al pasar del tope: error, o truncado si quien llama lo
  acepta (la imagen, para medirla).

| Quién | Tiempo por petición | Tamaño máximo | Hosts |
|---|---|---|---|
| Imagen por URL | 8 s (HEAD y, si hace falta, GET) | HEAD sin cuerpo; GET cortado a 15 MB | cualquiera público |
| Soporte | al redactar (`con_plazo`), **4 s por petición y 6 s para todo el soporte** (búsquedas y artículo), sin reintentos; «Comprobar», búsqueda 8 s y artículo 10 s. El servicio corta además la búsqueda a los 25 s, que al redactar no llegan a contar | 2 MB | el dominio de la fuente |
| Índice de las webs | 45 s, con un reintento si la red falla o da 5xx | JSON 16 MB (solo la REST: el HTML público no se lee) | los de la web, con y sin `www.`, y sus dominios extra |

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
  con `REGLA_SOPORTE`, `REGLA_WEBS`, la de los enlaces y la de
  `material_usado` según lo que haya; `REGLA_SIN_RESULTADOS` o
  `REGLA_SOPORTE_NO_CONSULTADO` si se buscó y no hay procedimiento, o si no se
  pudo consultar.
- **Cada pieza lleva su referencia**: `[S1]` el artículo, `[S2]` y `[S3]` los
  otros artículos, `[W1]`… las páginas de nuestras webs. Con material, el
  modelo añade a su JSON `material_usado` con las que ha usado
  (`material_fuera.material_usado`): es el «usado» de «Lo que ha usado» y
  decide la garantía del enlace.
- **Va en la petición del turno, no en el bloque de contexto**: ese se congela
  con el turno 0 y las correcciones lo reenvían byte a byte; lo que se consulta
  para cada turno no cabría.
- **Sin casillas, el turno es exactamente el de antes del #564**: con «Webs» y
  «Soporte» desmarcadas (o sin `fuentes`) no se consulta nada, ni siquiera el
  plan, y el prompt de sistema, el contexto y la petición son los mismos byte a
  byte (`tests/test_asistente_fuentes.py`). Con alguna marcada pero nada que
  meter (ni artículo, ni páginas, ni «no hay procedimiento», ni «no se pudo
  consultar el soporte»), el prompt tampoco cambia: solo queda apuntado lo que
  se buscó.
- Las guardias de la salida (la del cuerpo y la de Bomedia) no cambian: se
  aplican igual al borrador escrito con material.
- Lo consultado se **lee antes** de llamar al modelo, siempre por la lista
  blanca y por el índice: el artículo por fuente + id (el del resultado de su
  búsqueda), las páginas por su id; **nunca por una URL libre**. El navegador
  solo manda las dos casillas.
- **Nada de la consulta tira el borrador.** Una fuente que falla o no llega a
  tiempo se deja fuera, el borrador se escribe igual y su aviso lo dice («No
  se ha podido consultar el soporte (…): el borrador se ha escrito sin él.»).
  Ya no hay `409`/`502` con `soporte_<código>`, `soporte_vacio` ni
  `webs_no_encontradas` (eran del material elegido a mano, que se quitó el
  28/10/2026).
- Topes: del artículo entran **6.000 caracteres** (`MAX_TEXTO_SOPORTE`; un
  procedimiento de FLUX ronda los 2.000-4.000), de los otros dos artículos solo
  título y extracto (**300**, `MAX_EXTRACTO_OTRO`) y de cada página **900**
  (`MAX_CHARS_WEB`), hasta **3 páginas** (`consulta_fuentes.MAX_WEBS`).

Lo que ve la persona y lo que hace el borrador con todo esto:
[ai-features.md](ai-features.md) § Lo que no está en el catálogo.

## Cómo consulta el asistente al redactar (`services/asistente/consulta_fuentes.py`)

Desde el 28/10/2026. Lo deciden las dos casillas de la franja, **«Webs»** y
**«Soporte»**, que viajan en las tres peticiones que redactan como `fuentes:
{webs, soporte}`. Vienen **marcadas de partida** y se recuerdan por usuario en
el navegador (`lib/asistenteFuentes.ts`), como el alcance. Solo se consulta si
alguna está marcada y hay algo que redactar: con «nada que contestar» (el
cliente no ha escrito nada nuevo) no se llama al modelo ni se consulta.

1. **El plan: qué buscar.** Una llamada corta (`planificar`, `PROMPT_PLAN`) al
   **modelo de las correcciones** (`modelo_correccion` de Configuración ERP →
   «Asistente de respuesta»; sin pensamiento extendido, 300 tokens de salida
   como mucho) lee el último mensaje del cliente (hasta 3.000 caracteres; o su
   consulta del lead si el último del hilo es nuestro), el asunto, la
   instrucción y las máquinas que ha reconocido el catálogo, y devuelve un
   JSON: si el correo es **técnico** (avería, error, ajuste, calibración,
   instalación, actualización, mantenimiento, cómo se hace algo), la
   **marca**, la búsqueda para el centro de ayuda **en inglés** y en el idioma
   del correo, y la búsqueda para nuestras webs. Va con **5 s como mucho y sin
   reintentos** (`TIEMPO_PLAN`; `llm._invoke_claude` con `timeout` y
   `max_retries=0`). Si falla, tarda o devuelve otra cosa, la arma
   `plan_por_palabras`: las máquinas que nombra el cliente (o la FLUX que tiene
   por sus pedidos), los códigos de error y los mensajes entre comillas, y las
   palabras del asunto; técnico si el correo trae palabras de avería, error,
   ajuste o «cómo» en los idiomas de los correos (o las de la KB de FLUX). La
   llamada se anota en `ia_llamadas` con propósito **`asistente_consulta`**
   (también la fallida) y su coste **se suma al `coste_usd` del borrador**: lo
   que cuesta un borrador es todo lo que se pidió a la IA para escribirlo.
2. **Soporte** (casilla «Soporte»), **solo si el correo es técnico**. Fuentes:
   las activas con búsqueda de **esa marca**; si la marca es de una máquina que
   el catálogo ha reconocido en el correo y no tiene centro de ayuda en la
   lista (artisJet, hoy), no se consulta y se apunta (`sin_centro`): no se
   busca lo de artisJet en el soporte de FLUX. Sin marca, o una que no se
   reconoce, en todas las activas con búsqueda. Primero la búsqueda **en
   inglés** (la base de los fabricantes) y, si no sale nada, la del idioma del
   correo; 3 resultados por fuente. El **primer artículo se lee entero**, en el
   idioma del correo si la fuente lo tiene (Zendesk prueba ese locale y cae al
   inglés); los **dos siguientes, solo título y extracto** (se pueden enlazar,
   nunca sacar pasos de ellos).
3. **Webs** (casilla «Webs»), si el plan trae qué buscar (los productos por
   los que pregunta). La búsqueda del índice (§ La búsqueda: MySQL local) en el
   idioma del correo y, si no da nada, en cualquiera: los **tres mejores
   fragmentos**.
4. **El tiempo**, porque va **dentro** de la petición del borrador: el plan,
   **5 s**; todo el soporte (búsquedas, respaldo y artículo), **6 s**, y cada
   petición, **4 s**, sin reintentos (`soporte.con_plazo`, `TIEMPO_SOPORTE`,
   `TIEMPO_PETICION_SOPORTE`): pasado el plazo, la siguiente petición ni sale.
   Las webs son una consulta a la base local. En lo peor, unos 11 s más que un
   borrador sin casillas, lejos del corte del nginx (`/api/` espera 180 s en
   los ejemplos de `deploy/nginx`; un servidor montado a mano antes puede
   seguir con 60 s).
5. **Lo que falla se deja fuera**, nunca tira el borrador: se escribe sin ello
   y el aviso de la franja lo dice. Con el soporte sin consultar, el modelo
   recibe además la orden de no escribir pasos de memoria
   (`REGLA_SOPORTE_NO_CONSULTADO`). **«No he podido mirar» nunca es «no hay
   procedimiento»**: el «no hay» solo sale si todas las fuentes consultadas
   respondieron y ninguna trajo nada.
6. **Las correcciones no vuelven a buscar** (`reutilizar`): leen otra vez lo
   que trajo el primer turno (el artículo por fuente + id, de la caché; los
   otros artículos tal cual; las páginas por su id) con las casillas de
   **ahora**: desmarcar «Webs» en una corrección la deja sin las páginas. Ni
   plan ni coste de consulta. Si el turno 0 no llegó a redactar (no había nada
   que contestar), la primera corrección que redacta sí consulta, con su
   instrucción.

**Lo que queda apuntado** (`contexto_json["material"]`, por turno; es lo que
enseña «Lo que ha usado» y lo que leen las correcciones): `automatico: true`,
las casillas (`fuentes`), la consulta (`consulta`: técnica, marca, las
búsquedas, si salió de la IA o de las palabras —`origen` `ia` / `palabras`—, el
modelo, el error, `plan_ms`, `coste_usd` y `reutilizado` en una corrección),
el artículo (`soporte`: `ref` `S1`, fuente, título, enlace, idioma, si se
recortó, `usado`), los otros (`otros_soporte`), las páginas (`webs`: `ref`
`W1`…, web, tipo, idioma, precio de la web, `usado`), lo que no se encontró
(`sin_resultados`, `webs_sin_resultados`), lo que no se consultó y por qué
(`no_consultado`, `sin_centro`, `sin_fuentes`), lo que se buscó en el soporte
(`buscado_en_soporte`), los tiempos (`ms`, `soporte_ms`, `webs_ms`),
`material_usado` tal como lo dijo el modelo (`null` si no lo dijo) y, si hizo
falta, `enlace_anadido` y `pasos_sin_procedimiento`.

**Permisos: ninguno aparte.** La consulta la hace el servidor dentro de la
petición del borrador, para quien ya puede usar el asistente (§ Permisos).
Los avisos van al aviso del borrador; la auditoría del borrador
(`ia.borrador_generado`) apunta las casillas con las que se pidió.

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

El artículo trae también su HTML, saneado con la lista de
`html_sanitizer.py` pero con una llamada propia a bleach (fuera comentarios,
`data:` y `cid:`; antes se quitan enteros `script`, `style`, `iframe`,
`object`, `embed`, `noscript`, `template` y `svg`; las rutas relativas pasan a
absolutas). Lo pintaba en un `iframe sandbox=""` el «Leer» del buscador
manual; desde el 28/10/2026 ninguna pantalla lo enseña: al prompt va solo el
texto.

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
  locales no se manda ninguno (el que dé la web). Al redactar, el asistente
  busca **primero en inglés** y, si no sale nada, en el idioma del correo, y
  lee el artículo en el idioma del correo si la fuente lo tiene (si no, en
  inglés; § Cómo consulta el asistente al redactar).
- **Caché** (`soporte_cache`, `cache.py`): una búsqueda vale **1 día** (un
  artículo nuevo tiene que salir pronto) y un artículo **7 días** (cambian poco
  y el borrador lleva el enlace, que abre siempre el vigente). Clave: sha256 de
  `tipo|fuente|idioma|consulta-o-id`. Lo que falla no se guarda. Los caducados
  se borran al escribir (sin tarea programada). Se escribe en un SAVEPOINT: una
  carrera no tumba la petición.
- **Topes**: consulta de 2 a 300 caracteres, 6 resultados por defecto (20 como
  mucho), extracto de 300 caracteres, texto del artículo de 60.000 como mucho.
  Varias fuentes salen por turnos (que la primera no tape a las demás); si la
  búsqueda pasa de 25 s, las que faltan se saltan con un aviso. El asistente
  pide **3 resultados**, fuente a fuente, con búsquedas de 120 caracteres como
  mucho, y su plazo de 6 s corta antes.
- **«No he podido mirar» no es «no hay nada».** Si **todas** las fuentes
  consultadas fallan, la búsqueda da error (la de la fuente, o
  `sin_respuesta` 502 con varias), no una lista vacía: el redactor no dirá
  nunca «no hay procedimiento» sin haber mirado. Con varias, `fuentes` lleva
  solo las que **han respondido** y `fallidas` las que no (fallaron o no dio
  tiempo). El asistente busca fuente a fuente: si alguna falla y ninguna trae
  un artículo, el soporte cuenta como **no consultado** (aviso y la orden de no
  escribir pasos de memoria), nunca como «no hay procedimiento», aunque las
  demás no encuentren nada. Un 404 en una búsqueda es
  `respuesta_fuente` 502 («revisa el tipo y la dirección base»), no «no
  encontrado». Errores como `{"detail": {"code", "detail"}}`: 400 datos, 403
  dominio o red interna, 404 fuente desconocida o inactiva, 409 ya existe, 422
  cáscara o sin contenido, 502 red o fuente, 504 tiempo agotado.

**Al redactar, la búsqueda va dentro de la generación del borrador, con su
plazo.** Con #564 era una acción aparte, antes de redactar, para no acercarse
al corte del nginx; desde el 28/10/2026 la hace el asistente con un tope
corto, 4 s por petición y 6 s para todo el soporte (§ Cómo consulta el
asistente al redactar), y lo que no llega a tiempo se deja fuera.

### El procedimiento se cita, no se improvisa

Lo que exige el prompt (`REGLA_SOPORTE`) y lo que asegura el código, sin
depender de que el modelo obedezca:

- El artículo lo ha encontrado una búsqueda, no una persona: el modelo lo usa
  **solo si trata lo que se pregunta**. Si lo usa, los pasos se traducen al
  idioma del correo; **referencias, modelos, códigos de error, números de
  pieza, nombres de menú y valores van literales**. No se añaden pasos, no se
  cambia su orden ni se rellenan huecos de memoria. Si el artículo no cubre lo
  que se pregunta, no lo usa y el correo dice que lo comprobamos. De los otros
  dos artículos (solo título y extracto) puede poner el enlace, nunca pasos.
- **El modelo dice qué ha usado** (`material_usado`). Si no lo dice, el
  artículo cuenta como usado si su enlace está en el cuerpo o el cuerpo trae
  pasos.
- **El enlace va siempre que se usa el artículo.** Si el modelo lo usó y no
  puso el enlace, se añade al final (`redactor._aplicar_material`):
  «Procedimiento completo: URL» en el idioma del correo (es, en, de, fr, nl,
  pt, it, ca; otro → inglés), con la URL como texto del enlace y no el título
  (texto de un tercero sin pasar por las guardias). En un correo compuesto,
  un bloque de texto al final; en una corrección sobre una selección no se
  añade nada (partiría el párrafo) y el aviso lleva la URL para ponerla a
  mano.
- **Si no hay procedimiento**: con «Soporte» marcada y un correo técnico, si
  todas las fuentes consultadas responden y ninguna trae nada, el turno lleva
  `REGLA_SIN_RESULTADOS` y una frase con la búsqueda exacta: el correo dice
  que no hay un procedimiento publicado (que lo estamos revisando o lo
  consultamos con el fabricante) y **no escribe pasos**. Ya no hace falta
  pedirlo («Escribir diciendo que no hay procedimiento» era del buscador
  manual). Si el soporte no se pudo consultar, tampoco escribe pasos de
  memoria.
- **Los pasos que no salen de un artículo se avisan.** Si el borrador trae una
  lista de dos elementos o más, o dos líneas que empiezan por «1.», un guion o
  «Paso 1:» (en los idiomas de los correos), y no hay artículo que los
  respalde (no había procedimiento, no se pudo consultar el soporte o el
  modelo no usó el que salió), la franja avisa: «revísalo»
  (`pasos_sin_procedimiento`; el cuerpo no se toca). Es lo más importante de
  todo el bloque.

### Solo en el redactor

Hasta el 28/10/2026 la Cola SAT tenía un botón «Consulta de soporte» con el
mismo buscador en modo consulta. **Se quitó** con #566: buscar y leer en el
soporte es del redactor, y las rutas `/buscar` y `/articulo` pasaron al ámbito
del asistente. El mismo día, con la consulta automática, se quitaron también
los botones de la franja y **esas dos rutas**: el asistente usa el servicio
directamente, en el servidor (§ Permisos). El taller (rol `sat`) ya no busca ni
lee en el soporte, y sigue sin poder enviar correos (#562).

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
  - **WPGlobus** (`qtranslate`: artisjet-printers.eu, mboprinters.com,
    www.mqeurope.com): prefijo `/xx/` y los mismos ids en todos los idiomas,
    **también los de las categorías** (sus nombres sí se traducen). Una
    página sin traducir sale
    con el texto del idioma por defecto y la URL del otro; solo se indexa el
    idioma xx si `translation.languages.xx.content` lo dice (pedido con título
    y contenido en `_fields`: sin ellos WPGlobus dice que nada está traducido).
    En productos (la Store API no trae `translation`) es respaldo si el texto
    es idéntico al del idioma por defecto. Si el idioma por defecto falla en un
    tipo, los demás de ese tipo quedan en error: sin él no se distingue
    traducción de respaldo.
  - **WPML** (mbolasers.com, fluxlasers.eu, pimpam-vending.com): `&lang=xx`,
    cada idioma con **sus propios ids y sus propios slugs**: páginas,
    productos **y categorías** («Accesorios» de mbolasers es la 39 en es, la
    763 «Accessories» en en, la 765 «Zubehör» en de). Un idioma sin
    traducción vuelve **en silencio** al de por defecto:
    se descarta lo que repite un id del idioma por defecto o no lleva el idioma
    en el enlace. Si **todo** lo de un idioma es respaldo, no está publicado y
    se salta en todos los tipos. Si `pt` solo da respaldo se prueba `pt-pt` y
    `pt-br` (pimpam publica `pt-pt`); la fila se guarda como `pt`.
- **Páginas y entradas** fuera: por regla de slug o de id, con **menos de 150
  caracteres** de texto limpio (portadas que pinta el tema, «contacto»,
  «gracias»: las útiles tienen de mediana más de 2.000), con «lorem ipsum» de
  plantilla y los textos idénticos en el mismo idioma (se queda el id más
  bajo).
- **Todo por la REST, nunca por el HTML público.** Un 5xx (tras el
  reintento) deja ese grupo (tipo × idioma) en error, como cualquier otro
  fallo: no se escribe ni se marca nada ido, y sale un aviso en el resumen.
  mbolasers.com tuvo un camino por el HTML de cada página mientras su REST
  daba 500 con el contenido; era el límite de memoria de WordPress, arreglado
  en la web el 28/10, y ese camino se quitó.
- **Productos por la Store API** (`/wc/store/v1/products/categories` y
  `/products`): las categorías de fuera se filtran ya en el servidor
  (`category_operator=not_in`) y se revisan otra vez aquí, **solo por id**.
  **Cualquier categoría de fuera manda** (también una madre: un repuesto
  colgado de «Accessoires et pièces de rechange» y de «Pièces de rechange
  A5000» queda fuera; una Open Box en «Cortadoras Láser» y «Reacondicionadas»,
  también), **sin categoría → fuera**, y encima de todo las **excepciones por
  id** (si un id está en las dos, gana excluir: no ofrecer un repuesto como
  máquina). Los «incluir» se piden aparte con `include=`, porque el `not_in`
  del servidor los quitaría. Una web **sin tienda** (`rest_no_route`) no es un
  error: hay webs **solo de páginas** (pimpam). Pero solo cuenta como «sin
  tienda» si en esa misma lectura la REST de la web **sí** respondió (páginas o
  entradas del idioma por defecto): una web que da 404 a todo es un error y no
  marca ningún producto como ido.
- **Qué productos entran se decide UNA vez por web, en su idioma por defecto,
  y se lleva a los demás idiomas por id.** Los nombres traducidos no deciden
  nada. El 27/10 fluxlasers.eu metió **1.219 productos en vez de ~166**: cada
  idioma se decidía con los nombres de su propio árbol, y «Spare Parts»,
  «Ersatzteile», «Reserveonderdelen»… no empiezan por «Repuestos».
  - **Idioma por defecto**: fuera las categorías cuyo nombre empieza por una
    regla `categoria_fuera` y **todas sus hijas** (la jerarquía real, por
    `parent`). El árbol (y las traducciones con `include=`) se piden con
    `hide_empty=false`: la Store API esconde de partida las categorías
    vacías, y una de fuera vacía en el idioma por defecto pero con productos
    en otro no estaría ni entre las de fuera ni traducida.
  - **Red de seguridad por nombre, solo en el idioma por defecto** (y en las
    webs de un idioma), donde los nombres están en el idioma de las reglas:
    fuera también el producto con una categoría **propia** cuyo nombre empieza
    por una regla, aunque esa categoría falte del árbol (`count` desfasado,
    árbol cortado en `MAX_PAGINAS`). En los demás idiomas deciden solo los ids.
  - **WPGlobus y webs de un idioma**: los mismos ids en todos los idiomas →
    las mismas categorías de fuera y las mismas excepciones.
  - **WPML, otro idioma**: se lee el árbol del idioma (ids y madres) y se le
    pide a WPML la traducción de las categorías de fuera y de todas:
    `categories&lang=en&include=<ids del idioma por defecto>` devuelve las
    del inglés (WPML «ajusta los ids»; ningún campo de la REST enlaza las
    traducciones). Fuera = esas traducciones, las del idioma por defecto que
    aparezcan (los ids de término son únicos en toda la instalación) y
    **todas sus hijas en el árbol del idioma** («Beambox Spare Parts» por
    colgar de «Spare Parts», aunque no empiece igual).
  - **Cerrado ante la duda**: si el idioma tiene categorías **propias** (ids
    que no son del idioma por defecto) y **ninguna** resulta ser traducción
    de una del idioma por defecto (WPML no ha ajustado los ids), sus
    productos quedan **en error**: no se escribe ni se marca nada ido. Una
    categoría sin traducir que WPML sirve con su id del idioma por defecto no
    cuenta como enlace (con ella sola, un repuesto en «Spare Parts» y en
    «Láser de fibra» entraba); un idioma cuyas categorías son todas del
    idioma por defecto sí pasa (decide el mismo id).
  - Una categoría que **solo existe en ese idioma** («sin original») y no
    cuelga de una de fuera no deja entrar a un producto que no tenga otra
    (motivo `categoria_sin_original`); el resumen lo avisa con sus nombres.
  - Las **excepciones por id** se escriben con el id del idioma por defecto y
    se traducen igual (`products&lang=xx&include=…`; los «incluir», con la
    petición que ya se hacía). Solo se pide si la web tiene excepciones.
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
  pasada programada si la última programada `ok` o `parcial` empezó hace
  `conocimiento.webs_indice_dias` días o más (**30**, de 1 a 365; con un
  margen de 2 horas, porque la pasada empieza unos instantes después del tic y
  sin él caería el día N+1) y no hay otra en marcha. Por qué un tic diario y no un `enqueue_in` de 30 días: un trabajo
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
todas (web vacía), salvo las **de id, que siempre son de una web** (los ids de
WordPress son de cada instalación: la API rechaza una sin web, y una metida a
mano sin web se salta con aviso). Se aplican en el siguiente recorrido.

| Tipo | Patrón |
|---|---|
| `pagina_excluir_slug` | Expresión regular, sin distinguir mayúsculas, sobre el slug decodificado de páginas y entradas |
| `pagina_excluir_id` | Id de WordPress de una página o entrada (en WPML, el de cada idioma) |
| `categoria_fuera` | Un producto queda fuera si alguna de sus categorías, o una madre, **empieza** por este nombre (sin tildes ni mayúsculas). **En el idioma por defecto de la web**: vale para todos sus idiomas |
| `producto_excluir_id` | Id de producto (del idioma por defecto) que no entra aunque su categoría sea de dentro |
| `producto_incluir_id` | Id (del idioma por defecto) que entra aunque una regla lo saque (excluir gana) |

**Las reglas de categoría se escriben una vez, en el idioma por defecto de
cada web** (español; francés en artisjet-printers.eu; inglés en
www.mqeurope.com), y valen para todos sus idiomas: la decisión pasa a los
demás por id (§ Cómo se lee una web). Un nombre traducido («Spare Parts»,
«Ersatzteile») ni hace falta ni sirve. Lo mismo las excepciones por id: el
del idioma por defecto (en WPML el lector pide el de cada idioma).

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
  (`^test(?!imon|emun)|-test$|test-form|testagile|sample-page|pagina-ejemplo`:
  las de testimonios —testimonios, testimonials, testemunhos— no son de
  prueba); páginas
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
  | artisjet-eu | 493 | incluir | WhiteRIP: software en «Logiciels», hija de «Autres pièces de rechange» (migración 0149) |

## Permisos

| Qué | Capacidad | Quién |
|---|---|---|
| Soporte e índice al redactar (casillas «Soporte» y «Webs») | ninguna aparte: lo consulta el servidor dentro de `/api/emails/asistente` (`require_user` + `require_crm_access`) | quien usa el asistente: rol principal user, manager o admin, también con roles de ERP añadidos; sat, pedidos y comercial como rol principal, y viewer, no (403) |
| Índice: buscar (`/api/erp/webs/buscar`, el «Probar» del panel) | `erp.access` | cualquiera con ERP (comercial, pedidos, sat, user/manager, admin); viewer no |
| Lista blanca y «Comprobar»; estado del índice, «Reindexar ahora» y reglas | `erp.config` | admin |
| Imagen por URL (`/api/emails/imagen-url/comprobar`) | usuario con sesión | cualquiera que use el editor |

**No hay ningún permiso nuevo**: la consulta la hace el servidor mientras
redacta, para quien ya puede pedir un borrador. Las rutas para buscar y leer
en el soporte (`/api/erp/soporte/buscar` y `/articulo`) **se quitaron el
28/10/2026** con los botones de la franja: el buscador manual era su único
usuario. `/api/erp/webs/buscar` se queda para el «Probar» del panel del índice,
con `erp.access` como antes. Los roles principales solo ERP (sat, pedidos,
comercial) siguen sin llegar al asistente (403 en todo el CRM, desde antes de
esto); una persona del CRM (rol `user`) que además es comercial o de pedidos en
el ERP sí lo usa, con las casillas. Detalle por rol en
[perfiles-y-permisos.md](perfiles-y-permisos.md).

## Migración 0148 (`20261027_0148_fuentes_externas.py`)

Solo **crea** tablas (no toca datos que ya existan):

1. `soporte_fuentes`, sembrada con FLUX;
2. `soporte_cache` (índice por `expira_en`);
3. `web_indice` (índices por web e idioma y por `ido_en`; en MySQL, el
   FULLTEXT sobre título y texto);
4. `web_indice_reglas`, sembrada con las reglas de partida (12 de slug, 12 de
   categoría, 8 excluir y 1 incluir, el 477: las de arriba salvo el 493, que
   lo añade la 0149; copiadas en la migración porque una migración no importa
   la aplicación);
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

## Migración 0149 (`20261028_0149_reglas_indice_por_idioma.py`)

Acompaña al lector que decide en el idioma por defecto y lleva la decisión
por id:

1. **Borra las reglas `categoria_fuera` de la web `fluxlasers`**: el parche
   a mano del 27/10 (29 nombres traducidos exactos) que paró los 1.219
   productos. Ya no hacen falta, y como los nombres traducidos no deciden
   nada, tampoco harían nada. Solo esas (ninguna otra regla de categoría es
   de una web concreta). Apunta en el registro **cada regla borrada**
   (`0149: borrada la regla categoria_fuera de fluxlasers: 'Spare Parts'
   (activo=…, posicion=…, nota=…)`) y cuántas: el parche se puede reponer a
   mano desde el registro del despliegue.
2. **Añade `producto_incluir_id` 493 de `artisjet-eu`** si no está: el
   «WhiteRIP LOGICIEL D'IMPRESSION» es software, pero su categoría 19
   «Logiciels» es hija de la 18 «Autres pièces de rechange» y, con la
   herencia de #564, quedaba fuera en todos los idiomas. También en
   `reglas_partida.PRODUCTOS_DENTRO` (la 0148, ya aplicada, no se toca). La
   fila lleva una nota propia («… (migración 0149)»).

**No toca `web_indice`**: las 1.053 filas de FLUX marcadas idas el 27/10
siguen idas; el siguiente recorrido decide lo que vuelve. Bajar quita el 493
**solo si es el que puso la 0149** (su nota propia); si ya estaba, se queda.
El parche de FLUX **no** se repone: si se vuelve atrás el código, el lector de
antes decide cada idioma con los nombres de su árbol y, sin el parche, los
repuestos de FLUX en en/de/fr/nl vuelven a entrar; hay que reponer esas
reglas a mano desde el registro del despliegue. Test:
`tests/test_migration_0149_reglas_indice.py`.

### Tras desplegar la 0149

**«Reindexar ahora»** y mirar en el estado fluxlasers.eu: si la web no ha
cambiado, unos **166 productos** (34 en español y unos 33 en cada uno de
en/de/fr/nl), ninguno de «Spare Parts», «Ersatzteile»,
«Reserveonderdelen»… Si un idioma sale en error con «WPML no ha traducido
los ids», el enlace entre idiomas falló: se queda lo anterior y hay que
mirarlo antes de tocar nada. El WhiteRIP (493) tiene que estar en los cuatro
idiomas de artisjet-printers.eu.

## API

- `GET|POST /api/erp/soporte/fuentes`, `PATCH /api/erp/soporte/fuentes/{clave}`
  (la clave no cambia), `POST /api/erp/soporte/fuentes/{clave}/comprobar`. Es
  toda la API del soporte: `GET /api/erp/soporte/buscar` y `/articulo` se
  quitaron el 28/10/2026 (el asistente usa el servicio directamente).
- `GET /api/erp/webs/buscar?q=&idioma=&sitio=&tipo=` — `{items}` (fragmentos
  con URL, imagen, galería, categorías y precio web). Lo usa el «Probar: qué
  encontraría el redactor» del panel del índice; el asistente no pasa por
  aquí.
- `GET /api/erp/webs/estado` — por web y tipo lo vivo, los idos, los idiomas y
  la última lectura; la última pasada y la que está en curso.
- `POST /api/erp/webs/reindexar` — encola (o devuelve la que hay).
- `GET|POST /api/erp/webs/reglas`, `PATCH|DELETE /api/erp/webs/reglas/{id}`.
- `POST /api/emails/imagen-url/comprobar` `{url}` — responde **siempre 200**:
  `{ok: true, url, content_type, bytes, pesada, aviso}` o `{ok: false, url,
  codigo, motivo}` (`no_https`, `red_interna`, `no_resuelve`, `no_responde`,
  `no_es_imagen`, `url_invalida`, `puerto_no_permitido`, `tiempo_agotado`). No
  guarda nada: la URL que entra es la que sale.
- Las tres peticiones del asistente que redactan aceptan `fuentes: {webs,
  soporte}` (las casillas; sin el campo, o con las dos a `false`, no se
  consulta nada) ([ai-features.md](ai-features.md)). Los campos del material
  elegido a mano (`soporte`, `soporte_sin_resultados` y `webs`) ya no existen.

Las formas de respuesta son las de `frontend/src/app/lib/soporteApi.ts` (la
lista blanca) y `websApi.ts`; los errores, `{"detail": {"code", "detail"}}`
como el resto del ERP.

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
  como respaldo (no mezcla ni duplica, pero tampoco los indexa). En
  producción (27/10) `.eu` sí publicó sus cinco idiomas por directorio; lo que
  falló fue otra cosa: las categorías decididas con los nombres traducidos
  (§ Migración 0149).

Las dos cosas **hay que mirarlas tras el despliegue** (§ Tras desplegar).

## Lo que se comprobó el 28/10

Contra las webs reales, solo lectura y por los endpoints públicos:

- **WPML da a cada traducción su propio id y su propio slug**, también a las
  categorías (y a los productos): «Accesorios» de mbolasers es la 39
  `accesorios` en es, la 763 «Accessories» `accessories` en en y la 765
  «Zubehör» `zubehor` en de. Ni el id ni el slug valen para enlazar idiomas, y
  ningún campo de la REST lo hace (no hay `wpml_translations`).
- **WPML «ajusta los ids» al idioma pedido**:
  `/wc/store/v1/products/categories&lang=en&include=39` devuelve la 763;
  `/wp/v2/product_cat&lang=en&include=39,36`, la 763 y la 775;
  `/wc/store/v1/products&lang=en&include=965,30` (ids españoles), sus
  traducciones 2839 y 2418; `…/products&lang=en&category=39&category_operator=not_in`,
  los 9 productos en inglés que no están en «Accessories». La Store API
  respeta `_fields` (`id`, `id,name,parent`). En esto se apoya el lector.
- **WPGlobus/qTranslate** (artisjet-printers.eu, fr por defecto;
  mboprinters.com, es; www.mqeurope.com, en): los **mismos ids de
  categoría** en todos los idiomas, con los **nombres traducidos** (en
  artisjet-printers.eu cambian los 19: la 46 «Accessoires et pièces de
  rechange» es «Zubehör und Ersatzteile» en de y «Accessories and spare
  parts» en en). Ya se decidían con los ids del idioma por defecto: no les
  afectó.
- **fluxlasers.es y fluxlasers.eu son el mismo WordPress**: por `.es` WPML no
  hace caso de `lang` y solo sirve el español. Por `.eu` (producción, 27/10)
  salen los cinco idiomas con las categorías traducidas: en «Spare Parts»,
  «Beambox Spare Parts», «Hexa Spare Parts», «Ador Spare Parts», «Beamo Spare
  Parts»; de «Ersatzteile», «Beambox-Ersatzteile», «Hexa Ersatzteile», «Ador
  Ersatzteile», «Beamo-Ersatzteile»; fr «Pièces de rechange»… «Pièces
  détachées Beamo»; nl «Reserveonderdelen», «Beambox-reserveonderdelen»,
  «Ador Onderdelen», «Beamo reserveonderdelen». Con el parche a mano: **166
  productos = 34 en español + 33 en cada uno de en/de/fr/nl**. El árbol real
  del español (19 categorías) y sus productos reales (386: 34 dentro, 6 en una
  de fuera y otra de dentro, como las Open Box y los cartuchos de Ador) son la
  base del WordPress simulado de los tests (`web_flux_wpml`), que reproduce
  ese 166 sin ninguna regla de FLUX.
- **mbolasers.com responde 200 con contenido en los cinco idiomas** (las
  páginas en unos 3,7 s, las entradas en unos 2,5 s): el 500 era el límite de
  memoria de WordPress, arreglado en la web. Con el lector nuevo, todo por la
  REST: 29 peticiones, **48 páginas, 25 entradas y 107 productos** (22 en es y
  en, 21 en de, fr y nl), ni una petición al HTML, sin avisos: lo mismo que el
  recorrido del 27/10.
- **artisjet-printers.eu**: desde #564 el 493 «WhiteRIP LOGICIEL
  D'IMPRESSION» quedaba fuera en todos los idiomas (su categoría 19
  «Logiciels» es hija de la 18 «Autres pièces de rechange»). Con la excepción
  de la 0149 entra en los cuatro (fr, de, en, pt): 49 productos.
- **fluxlasers.eu sigue sin llegarse desde el entorno de pruebas**: FLUX en
  varios idiomas está probado con el simulado (datos reales del español,
  traducciones con otros ids), no contra la web: hay que mirarlo tras
  desplegar (§ Tras desplegar la 0149).

## Tests

`test_externo_base.py` (red, HTML, sobre), `test_soporte.py` y
`test_soporte_api.py` (la lista blanca y «Comprobar»), `test_webs_indice.py` y
`test_webs_api.py` (con un
WordPress simulado de datos realistas en `test_webs_comun.py`: WPML con ids
por idioma y los ids «ajustados» en `include=` y `not_in`, la Store API que
esconde las categorías vacías sin `hide_empty=false`, y FLUX con su árbol
y sus productos reales), `test_imagen_url.py`, `test_asistente_fuentes.py` (la
consulta automática con las casillas: el plan y su respaldo por palabras, el
plazo del soporte, lo que no responde, «no hay procedimiento», el enlace,
`material_usado`, las correcciones que no vuelven a buscar, y el turno con las
casillas desmarcadas idéntico al de siempre),
`test_migration_0148_fuentes_externas.py`,
`test_migration_0149_reglas_indice.py`,
`test_web_ingest_worker_isolation.py` y `test_erp_worker_queues.py` (la cola
`webs:indice`, última y solo en worker-web).
