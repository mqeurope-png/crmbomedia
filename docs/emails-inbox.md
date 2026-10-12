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
| **Acciones** | **Responder** (primario) · Responder a todos · Reenviar · **Ayúdame a contestar** (abre el compositor con la franja del asistente: dices qué quieres decir, la IA redacta, lo corriges en lenguaje normal y lo envías tú; ver [docs/ai-features.md](ai-features.md)) |

- Todos los iconos llevan tooltip descriptivo.
- **Ayúdame a contestar** abre el compositor con la **franja del asistente**
  desplegada, entre la cabecera (De, Para, Asunto con los iconos de plantillas,
  adjuntos y opciones; Cc y Cco solo si se usan, con un enlace junto a Para) y
  el cuerpo: empuja el contenido, no tapa nada, y **se pliega sola a una línea
  en cuanto hay borrador** («Ayúdame a contestar · 1 borrador · 0,0049 $»).
  Lo primero es la caja **«¿Qué quieres decirle?»**: si escribes, eso manda; si
  la dejas vacía, la IA contesta al último mensaje del cliente, y si el cliente
  no ha escrito nada nuevo lo dice en la franja y el cuerpo queda vacío (nunca
  mete en el correo nada dirigido a ti; si algo es dudoso, lo deja y te pide
  que lo revises). **Con texto en el cuerpo la caja va en modo corrección**
  («Aplicar»): la IA corrige siempre lo que hay ahora mismo en el editor —el
  borrador que has retocado a mano, un correo escrito a mano, un borrador
  reabierto— y, si tienes texto seleccionado, solo ese trozo (la franja lo
  dice). El borrador llega con formato sencillo (listas, negritas, enlaces sobre
  el nombre de la máquina); cada corrección se apila en la lista de lo dicho y
  se puede volver al anterior; el envío anota qué turno salió. La franja está
  también plegada en **Redactar** y en la ficha del contacto: un correo nuevo,
  sin hilo, con el pasado de esa persona (ahí decir qué quieres es obligatorio
  y el asunto lo propone la IA). Si el destinatario es un compañero (una
  dirección del grupo), la franja lo avisa en cuanto cambias «Para»; en un hilo
  de cliente con un compañero en copia, **Responder** propone al cliente, no al
  último que escribió.
- **Correos con fichas** («mándale la gama DTF», o el interruptor **Con
  fichas** de la franja; **Auto** por defecto, **Texto** para forzar el de
  siempre): la IA prepara un correo compuesto —entrada, tira de la marca,
  fichas de producto con su imagen y su precio del catálogo («Precio a medida»
  si no tiene precio público), vídeo, botones y cierre— y en lugar del editor
  sale la **vista previa**, tal cual llegará, en **Escritorio** o **Móvil**
  (las fichas se apilan). Pulsa un texto para cambiarlo; pulsa un bloque para
  subirlo, bajarlo, duplicarlo o quitarlo; o pídeselo a la IA («quita el
  vídeo», «deja solo dos máquinas»). **Copiar HTML** y **Descargar .html** dan
  el correo entero; **Pasar a texto** lo convierte en un correo normal. Se
  envía como siempre, con tu firma y su versión en texto plano.
- **El compositor del hilo es un diálogo con velo que no destruye trabajo**:
  pulsar en el fondo no lo cierra. Escape, la X y «Cancelar» preguntan si hay
  algo escrito (cuerpo, asunto, instrucción o conversación con la IA): **Guardar
  en Borradores** (por defecto), **Seguir editando** o **Descartar**; en blanco
  cierran sin más. Un borrador guardado se lleva la conversación con la IA y
  vuelve con ella al reabrirlo desde **Borradores**.
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
