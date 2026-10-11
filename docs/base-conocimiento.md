# Base de conocimiento para la respuesta a leads (Fase 1)

Lo que el asistente «Ayúdame a contestar» puede **enlazar**. La regla es la del
diseño: **el modelo redacta, no aporta hechos**. Precios, plazos y enlaces
salen de los datos de BoHub y de estas tablas, o no salen. Fase 1: sin
embeddings, sin cuerpos de artículos; títulos, enlaces y reglas en datos.

Pantalla: **Configuración ERP → «Base de conocimiento»** (capacidad
`erp.config`). Las tablas se guardan al momento por su API; el botón «Guardar
cambios» de la sección lleva solo la configuración (sincronización y palabras
de FLUX, blob `conocimiento` de `ErpSettings`).

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
y `POST /resolver` (`{sitio, idioma, interes, texto, modelo}` → los enlaces que
saldrían: es lo mismo que mira el asistente, `services/conocimiento/enlaces.py`).
Cada cambio queda en la auditoría (`kb.changed`).
