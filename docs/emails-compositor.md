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
  imagen vídeo tabla emoticons`.
- **Paste desde Word/Excel** conservando formato (`paste_merge_formats`).
- **Imágenes**: arrastrar, pegar desde portapapeles o botón subir. Se suben
  a `POST /api/email-templates/assets` (disco, content-addressed por
  sha256) y se insertan como URL en el editor.
- **Firma**: la firma HTML por defecto del operador se inyecta al abrir
  (bloque delimitado `<!--crmbo:signature-->`); selector para cambiarla.
- Autosave de borradores + adjuntos clásicos (límite 25 MB total).
- Cinturón cliente: `invalid_elements` rechaza `script/iframe/object/
  embed/form` al pegar (ver `editorConfig.ts`).

## Sanitización en el envío (backend — la barrera real)

`app/services/html_sanitizer.py` (`bleach` + whitelist estricta) se aplica
**siempre** en `send_email`, antes de enviar por Gmail y de persistir:

- Whitelist de tags (p, encabezados, listas, tablas, a, img, span/div…),
  atributos (`style`, `href`, `src`…) y propiedades CSS (color, fondos,
  alineación, tamaños…).
- Fuera: `<script>`/`<style>`/`<iframe>` **con su contenido**, handlers
  `on*`, URLs `javascript:`.
- Los comentarios se conservan (los delimitadores de la firma los usan).

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
