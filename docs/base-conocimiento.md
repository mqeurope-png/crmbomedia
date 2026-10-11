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
asistente, `services/conocimiento/enlaces.py` y `catalogo.para_asistente`).
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
| `kb_catalogo_marcas` | Las marcas del compositor y su **idioma por defecto** (editable): el precio que se usa cuando el producto no lo tiene en el idioma del cliente |
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

Nunca el catálogo entero: hasta **6 máquinas** y hasta **8 consumibles o
repuestos**, en su idioma, con precio tal cual, enlace (en la lista de enlaces
permitidos) y los avisos. Las máquinas se eligen **por este orden** (cada una
lleva de dónde salió, `eleccion`, y el bloque del prompt las agrupa así):

1. **Lo que el cliente nombra en el hilo** (`catalogo.menciones_en`): por el
   nombre del producto (palabras con peso: «Orion», «Beambox», códigos como
   «5000U»; lo más completo primero) y, más flojo, por códigos de modelo
   (DM3H, PT5) que estén en la descripción, las características, el texto
   alternativo o el id del producto. Lo que no esté en ningún sitio no se
   encuentra: los nombres que usa el cliente de talentosdiversos (DM3H, SM3,
   SwapPress PT5) no están en el compositor y solo entrarían con una lista de
   nombres alternativos por producto, que sería cosa del compositor.
2. **Lo que pide la instrucción de quien envía**: una máquina concreta, o un
   interés por sus palabras («las máquinas de DTF» → `dtf`; «una láser para
   cortar» → `corte_laser`, `grabado_laser`; «UV grande» → `uv_grande`; «UV» a
   secas → los tres tamaños; `catalogo.intereses_de_texto`). Eso manda.
3. **Los consumibles de la máquina que ya tiene** (lo que dicen sus pedidos,
   casado por palabras contra nombre, categoría y marca de los productos de las
   tiendas), en su lista aparte.
4. **El interés clasificado**, como hasta ahora.

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
