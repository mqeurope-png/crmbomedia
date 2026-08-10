# Bandeja de emails (`/emails`) — comportamiento de la UI

> **Sprint CRM-BANDEJA.** Rediseño del thread detail estilo Gmail tras el
> feedback de uso post-backfill (PR #331). Complementa a
> [`gmail-sync-model.md`](./gmail-sync-model.md) (modelo de datos/sync) —
> este doc cubre solo la experiencia de la bandeja.

## Layout de 3 paneles

`/emails` es un grid de 3 columnas: sidebar (carpetas + etiquetas) ·
lista de hilos · detalle del hilo. Los dos divisores son **arrastrables**
(barra vertical visible de 4px, cursor `col-resize`; se ilumina al pasar
el ratón). El ancho elegido persiste per-usuario en `localStorage`:

| Panel | Clave localStorage | Min | Default | Max |
|---|---|---|---|---|
| Sidebar | `crmbomedia_ui:emails:sidebar_width` | 180px | 240px | 400px |
| Lista (medio) | `crmbomedia_ui:emails:middle_width` | 280px | 380px | **800px** |

El tope del panel medio subió de 600→800px (CRM-BANDEJA): con 600 no se
podían leer asuntos largos.

## Thread detail estilo Gmail

- **Cada mensaje es una card apilada**; el contenedor que scrollea es el
  panel derecho entero — **ningún mensaje tiene scroll interno**. El HTML
  del email se renderiza en un iframe sandboxed (sin JS) que se
  auto-redimensiona a la altura natural del contenido.
- **Estado inicial:** solo el último mensaje expandido; los anteriores
  plegados como fila fina con snippet de 1 línea (como Gmail).
- **Click en el header** de un mensaje → toggle expand/collapse individual.
- **«Expandir todo» / «Colapsar todo»** encima del hilo (solo con 2+
  mensajes).
- **Header siempre visible** (plegado y expandido): avatar circular con la
  inicial del remitente, nombre + email, fecha relativa a la derecha
  (`hace 2 h`, `hace 3 d`, `03 ago 2026`), y chip de estado:
  - 🟢 **Enviado desde CRM** (verde) — mensajes `outbound`.
  - 📧 **Respuesta entrante** (azul suave) — mensajes `inbound`.
  - 📅 **Programado** (ámbar) — scheduled sends pendientes.
  - **Spam** (rojo) — mensajes que Gmail marcó como spam.
- Expandido, bajo el remitente aparece el resumen de destinatarios
  («para mí» / «para ana, luis» / «para a, b +2 más»).
- **▼ Detalles** en cada mensaje expandido → bloque con De / Para / Cc /
  Fecha completa / Entregado a (alias) / Asunto.
- **Adjuntos** como chips al final del mensaje: icono + nombre + tamaño +
  botón de descarga (cuando el binario está en disco; cada descarga se
  audita). Si solo existe el sumario inline (`attachments_json`, binario
  no descargado por tamaño), el chip se muestra sin botón.

## Toolbar del hilo (3 grupos con divisores)

| Grupo | Acciones |
|---|---|
| **Estado** | ⭐ Estrella · 📥 Archivar/Restaurar · 🗑 Papelera · ✉ Marcar no leído |
| **Clasificar** | 🚫 Spam · 🏷 Etiquetar (dropdown) · 📁 Mover a carpeta |
| **Acciones** | **Responder** (primario) · Responder a todos · Reenviar |

- Todos los iconos llevan tooltip descriptivo.
- **Marcar no leído** vuelve a la bandeja tras aplicarse (abrir el hilo lo
  re-marcaría como leído — mismo patrón que Gmail).
- **Responder a todos** pre-rellena Cc con el resto de destinatarios del
  mensaje (excluyendo los alias del propio operador).
- **Reenviar** abre el compositor con `Fwd:` + el mensaje original citado
  (mensaje nuevo, sin threading).

## Filtros rápidos del listado

Chips encima de la lista central:

| Chip | Query param | Acumulable |
|---|---|---|
| No leídos | `has_unread=true` | ✓ |
| **Con adjuntos** | `has_attachments=true` | ✓ |
| **Con contacto CRM** | `has_contact=true` | ✓ |
| Hoy / Última semana / Último mes / Todas / Personalizado… | `since`/`until` | excluyentes entre sí, acumulables con el resto |

- «Con adjuntos» = al menos un mensaje del hilo con adjunto (binario
  descargado o sumario inline).
- «Con contacto CRM» = hilo vinculado a un contacto (en el thread o en
  cualquiera de sus mensajes).
- «Estrellados» vive en el sidebar (`starred=true`) y también se acumula.
- Fix incluido en CRM-BANDEJA: los filtros `No leídos` y los rangos de
  fecha se escribían en la URL pero **no llegaban al backend** — ahora la
  lista los pasa todos a `GET /api/emails/threads`.

## Breadcrumb

Encima del asunto del hilo:

- Por defecto: `← Bandeja › [carpeta] › [asunto]` — click en «Bandeja»
  deselecciona y vuelve a la lista.
- Abierto desde la ficha del contacto (link con `?from=ficha`):
  `← Ficha › Historial › [asunto]` — vuelve a la ficha.

## Fuera de alcance (PRs futuros)

Compositor rico v2.2 · etiquetas Gmail bidireccionales v2.3 · backfill de
label `SENT` · atajos de teclado · hover-preview de fila ·
multi-selección con checkboxes · snooze (el backend ya lo soporta).
