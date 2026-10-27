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

## Imagen por URL y los buscadores del asistente (27/10/2026)

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

**En la franja del asistente** («Ayúdame a contestar»), junto a la caja, dos
botones para buscar **antes de redactar**:

- **«Buscar en soporte»**: el procedimiento en el soporte del fabricante (hoy,
  FLUX). **«Leer»** abre el artículo; **«Usar en el borrador»** hace que el
  borrador lo cite: pasos traducidos, referencias y números de pieza tal cual
  y **siempre el enlace** al artículo. Si no hay nada, **«Escribir diciendo que
  no hay procedimiento»**: el correo lo dice y no se inventa pasos.
- **«Buscar en las webs»**: páginas, entradas y productos de nuestras webs.
  **«Añadir al borrador»** (hasta 4): la IA los tiene delante con su enlace. El
  precio que sale es **el de la web**: el borrador puede decir «en la web está
  a…», nunca ofertarlo.
- Lo elegido queda en fichas debajo de la caja y **va con cada petición hasta
  que lo quitas** con su ✕. Cada botón lleva además **la cuenta** de lo
  elegido (un número azul): si la ventana es baja y la franja encoge, se ve
  igual que hay material aunque las fichas queden más abajo.
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
