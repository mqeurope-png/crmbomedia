# Compositor de email — editor rico, sanitización y envío

> **Sprint CRM-COMPOSITOR-V2.2.** El compositor ya era rico (TinyMCE
> self-hosted desde el sprint Email v2.5); este sprint cierra los huecos de
> seguridad y de entrega. **Decisión: se mantiene TinyMCE** — ya cubre toda
> la lista de features pedida (negrita/cursiva/subrayado/tachado, listas,
> encabezados, alineación, colores, links con dialog, tablas, imágenes por
> drag&drop/paste/botón, deshacer/rehacer, paste desde Word con formato);
> migrar a Tiptap habría sido riesgo de regresión sin ganancia.

## Qué soporta el editor

- Toolbar completa: `undo redo | blocks | bold italic underline
  strikethrough | forecolor backcolor | align | bullist numlist | link
  imagen imagen-por-URL vídeo tabla emoticons`.
- **Paste desde Word/Excel** conservando formato (`paste_merge_formats`).
- **Imágenes**: arrastrar, pegar desde portapapeles o botón subir. Se suben
  a `POST /api/email-templates/assets` (disco, content-addressed por
  sha256) y se insertan como URL en el editor.
- **Imagen por URL** (27/10/2026): una imagen por su enlace, sin subirla (abajo).
- **Firma**: la firma HTML por defecto del operador se inyecta al abrir
  (bloque delimitado `<!--crmbo:signature-->`); selector para cambiarla.
- Autosave de borradores + adjuntos clásicos (límite 25 MB total).
- Cinturón cliente: `invalid_elements` rechaza `script/iframe/object/
  embed/form` al pegar (ver `editorConfig.ts`).

## Imagen por URL y las fuentes del asistente (27/10/2026)

Para quien escribe. Detalle técnico en
[fuentes-externas.md](fuentes-externas.md).

**«Imagen por URL»** (junto a «Imagen»; también en Plantillas y Firmas): pega
el enlace de la imagen y pulsa **«Comprobar»**.

- Solo **https://**: una imagen en http:// la bloquean los clientes de correo,
  así que no se inserta y te dice por qué. Direcciones de la red interna,
  tampoco.
- Tiene que ser **una imagen**: si pegas la dirección de la página en vez de la
  de la imagen, te lo dice.
- Ves la **vista previa**, el tipo y el peso. Si pesa **más de 1 MB** se
  inserta igual, con un aviso: el correo tardará en cargar.
- **«Insertar»** solo se activa después de comprobar ese mismo enlace.
- **La imagen no se copia: se enlaza.** El correo la carga de su web cada vez
  que alguien lo abre (no va incrustada como las que subes desde el PC). Si la
  web la quita o la cambia de sitio, desaparece del correo, también de los ya
  enviados.

**En la franja del asistente** («Ayúdame a contestar»), debajo de la caja y
delante de «Auto · Texto · Con fichas», dos casillas: **«Webs»** y
**«Soporte»** (28/10/2026; antes eran dos botones para buscar a mano). Vienen
**marcadas** y se recuerdan para ti en este navegador. No hay que buscar ni
elegir nada: el asistente consulta por su cuenta mientras redacta.

- **«Soporte»**: si el correo es técnico (una avería, un error, un ajuste,
  cómo se hace algo), mira en el centro de ayuda del fabricante (hoy, solo
  FLUX). Si reconoce en el correo una máquina de una marca que no tiene centro
  de ayuda (una artisJet), no busca en el de otra; si no sabe qué máquina es,
  mira en todos. Si hay un artículo que viene a cuento, el borrador lo
  **cita**: pasos traducidos, referencias y números de pieza tal cual y **el
  enlace** al artículo (si la IA no lo pone, se añade al final). Si no hay
  procedimiento, el correo lo dice; si no hay centro de ayuda para esa
  máquina, dice que lo compruebas. En ningún caso **se inventa pasos**.
- **«Webs»**: mira en nuestras webs las páginas y productos por los que
  pregunta el cliente; la IA los tiene delante con su enlace. El precio que
  sale es **el de la web**: el borrador puede decir «en la web está a…», nunca
  ofertarlo. Desde el 28/10/2026 busca también los productos que **pides tú**
  («incluye la MBO 6090 desktop, mírala en mboprinters.com», con o sin
  enlace), y en un correo «Con fichas» un producto que el catálogo no tiene va
  con una **ficha montada desde la web**: su foto principal, su enlace y el
  precio de la web con su IVA, «Precio a consultar» si la web no lo da, o el
  que le digas tú. Si está en el catálogo, va la del catálogo. Ver
  [docs/guia-erp-usuario.md](guia-erp-usuario.md) («Un producto que solo está
  en la web»).
- Si una de las dos no responde a tiempo, el borrador sale igual, sin ella, y
  la franja te lo dice. Desmarcadas, el asistente escribe como antes, solo con
  el catálogo. Cambiar una casilla vale para el siguiente «Redactar» o la
  siguiente corrección. Las correcciones usan lo que trajo el primer borrador,
  sin volver a buscar; lo que ese no miró (la casilla iba desmarcada) o no
  pudo mirar (no respondió), lo consultan entonces.
- En **«Lo que ha usado»** ves qué ha consultado, con sus enlaces, y si el
  borrador lo ha usado; «Buscó …» solo sale si buscó de verdad. Si el
  borrador trae pasos que no salen de ningún artículo, la franja te avisa:
  revísalo. En un correo con fichas, ves también cuáles salen **del
  catálogo** y cuáles **del índice de las webs**, con su enlace y de dónde
  sale su precio; el aviso del borrador lo repite (al cliente le llegan
  todas iguales).
- Las imágenes del cuerpo **se conservan** cuando le pides a la IA que corrija
  («más corto», «en inglés»); si alguna no vuelve a su sitio, va al final y la
  franja te avisa.

## Sanitización en el envío (backend — la barrera real)

`app/services/html_sanitizer.py` (`bleach` + whitelist estricta) se aplica
**siempre** en `send_email`, antes de enviar por Gmail y de persistir:

- Whitelist de tags (p, encabezados, listas, tablas, a, img, span/div…),
  atributos (`style`, `href`, `src`…) y propiedades CSS (color, fondos,
  alineación, tamaños…).
- Fuera: `<script>`/`<style>`/`<iframe>` **con su contenido**, handlers
  `on*`, URLs `javascript:`.
- Los comentarios se conservan (los delimitadores de la firma los usan).

**El correo compuesto** (fichas, marca, hero, muestras, vídeo y botones; ver
[ai-features.md](ai-features.md)) es un documento entero que con esa lista
saldría deshecho. Se reconoce por su marca (`<meta name="generator"
content="BoHub correo compuesto">`) y pasa por `sanitize_correo_compuesto`: la
misma lista más lo que usa el generador (`role`, `bgcolor`, anchos de celda,
`display`, bordes redondeados, y la imagen de fondo del hero: el atributo
`background` y `background-image`/`-size`/`-position`/`-repeat`, solo con URL
`http(s)`; una `javascript:` se quita). La cabecera y la **única hoja de estilo** (la
de las columnas en el móvil) las vuelve a poner el generador, nunca las del
HTML que llega: forjar la marca no da más que esa lista, que tampoco deja
scripts, manejadores, `javascript:` ni comentarios condicionales de Outlook
(solo los delimitadores de la firma). Aplicarlo dos veces da lo mismo. La baja
(«Anular suscripción») va dentro del `<body>` y el píxel de apertura antes de
`</body>`.

## Fallback text/plain

Si el caller solo manda `body_html`, el backend genera el `text/plain` con
`html2text` (bodywidth=0) y el MIME sale como `multipart/alternative`
(text + html) — los clientes de texto ven algo legible. Si el caller manda
su propio `body_text`, se respeta.

## Imágenes incrustadas al enviar (CID)

Las imágenes pegadas viven como assets en disco y se insertan como URL. Al
enviar, `_swap_asset_urls_to_cid` las **incrusta como CID inline** en el
MIME (patrón `multipart/related`, igual que las plantillas Gmail): el
destinatario ve la imagen embebida aunque su cliente bloquee imágenes
remotas. Si el fichero ya no está en disco, la URL remota se deja intacta
(sigue funcionando para clientes que cargan remotas).

## Límites y decisiones

- Assets **sin TTL de borrado**: son content-addressed (sha256, dedupe) y
  compartidos con las plantillas — borrarlos por edad rompería plantillas.
  Crecimiento acotado por dedupe. (Decisión documentada; revisable.)
- Máx 25 MB de adjuntos por mensaje (límite Gmail); assets de imagen con
  su propio límite (`email_assets_max_bytes`).
- El «quote del mensaje anterior» al responder no se añadió en este sprint
  (el hilo ya muestra el contexto; backlog si se quiere).
