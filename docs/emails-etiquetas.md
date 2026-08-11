# Etiquetas de Gmail en el CRM (CRM-ETIQUETAS-GMAIL-V2.3)

## Modelo

Una sola tabla `email_labels` aloja los dos tipos de etiqueta:

| Tipo | `user_id` | `gmail_label_id` | Nivel | Quién la gestiona |
|---|---|---|---|---|
| **Personal CRM** (v2.4a) | dueño | `NULL` | hilo (`email_thread_labels`) | el operador (CRUD en el sidebar) |
| **Org / Gmail** (v2.3) | `NULL` | `Label_…` (unique) | mensaje (`email_message_labels`) | Gmail + `sync_labels` |

Las labels de Gmail viven a nivel de **mensaje** porque así las modela la
propia API (`labelIds` por mensaje): un hilo puede tener un mensaje
etiquetado «Clientes VIP» y el resto sin etiquetar.

Solo se importan las labels **personalizadas** (`type == 'user'`). Las de
sistema se quedan fuera a propósito:

- `INBOX` / `SPAM` / `SENT` / `TRASH` ya tienen vista nativa en el CRM
  (Bandeja, Spam, Enviados, Papelera).
- `CATEGORY_*` (pestañas Social/Promotions/…) no aportan como etiqueta.
- `UNREAD` / `STARRED` / `IMPORTANT` tienen su propio campo o filtro.

`is_hidden` permite ocultar una etiqueta org del sidebar sin perder el
mapeo (hoy no hay UI para togglearlo; se cambia por SQL si hace falta).

## Sync

**Import inicial + retroactivo** (idempotente, re-ejecutable):

```bash
docker exec crmbo-api-1 python -m app.integrations.gmail_watch sync_labels [--dry-run]
```

1. `users.labels.list` → upsert de las labels `type=user` en
   `email_labels` (org). Nombre y color se actualizan si cambiaron en
   Gmail.
2. Mapeo retroactivo: los mensajes ya importados guardan sus `labelIds`
   en el JSON `email_messages.gmail_labels`; el comando materializa
   `email_message_labels` para las labels conocidas.

**Go-forward (Gmail→CRM)** — `process_history` (push de Pub/Sub):

- `messagesAdded`: al persistir el mensaje se materializan sus labels
  personalizadas ya importadas.
- `labelsAdded` / `labelsRemoved`: se añade/quita la fila de
  `email_message_labels` y se actualiza el JSON `gmail_labels`. Si el add
  referencia una label desconocida (creada en Gmail después del último
  sync), se importa on-the-fly vía `labels.get`.

**CRM→Gmail** — endpoints de mensaje:

- `POST /api/emails/messages/{id}/labels/{label_id}`
- `DELETE /api/emails/messages/{id}/labels/{label_id}`

Ambos llaman `users.messages.modify` (scope `gmail.modify`, ya concedido)
**antes** de persistir: si Gmail falla → 502 y no cambia nada local.
Guard de visibilidad = el del thread detail (privilegiado, iniciador, o
mensaje entregado a un alias propio); 404 para hilos ajenos. Solo aceptan
etiquetas org (`gmail_label_id` no NULL) — las personales siguen operando
a nivel de hilo con sus endpoints de siempre.

## Colores

Gmail define **dos** colores por label: `color.backgroundColor` (fondo
del chip) y `color.textColor` (el que contrasta con ese fondo — blanco
sobre rojo, negro sobre amarillo). En el CRM son `email_labels.color` y
`email_labels.text_color` (migración 0096); se conserva el nombre `color`
para el fondo porque es el que ya usaban el sidebar y los chips desde
v2.4a. Ambos se capturan en `sync_labels`, así que **re-ejecutarlo tras
la 0096 rellena `text_color` en las labels ya importadas**.

## API / UI

- `GET /api/emails/labels` devuelve ahora personales **+ org** (no
  ocultas), cada una con `gmail_label_id`, `is_system` y `thread_count`
  (nº de hilos con la etiqueta, a nivel hilo o mensaje).
- `GET /api/emails/threads?label_id=X` casa etiquetas de hilo **o** de
  mensaje.
- `GET /api/emails/threads` incluye en cada hilo `labels[]` = unión de
  sus etiquetas de hilo + las de Gmail de sus mensajes, en **una** query
  batch por página (sin N+1, con test que lo vigila).
- Bandeja (CRM-ETIQUETAS-EN-BANDEJA): chips a la izquierda del asunto,
  con el color pleno de Gmail, máximo 3 + «+N» con el resto en el
  tooltip. Una fila sin etiquetas se ve exactamente igual que antes.
- Thread detail: cada mensaje expone `labels[]`; la UI pinta chips con
  «×» y un dropdown «+» con las etiquetas org restantes.
- Sidebar: las etiquetas org aparecen en la sección «Etiquetas» con badge
  de conteo y sin botones de editar/borrar (su ciclo de vida vive en
  Gmail).

### Silenciar una etiqueta ruidosa

Una etiqueta «cajón de sastre» (p. ej. «todos los emails», que marca
decenas de miles de mails) no aporta señal como chip y tapa a las que sí.
Se oculta del sidebar **y** de los chips sin perder el mapeo ni el filtro
por `label_id`:

```bash
docker exec crmbo-api-1 python -m app.integrations.gmail_watch hide_label "- Bart - todos los emails"
docker exec crmbo-api-1 python -m app.integrations.gmail_watch hide_label "- Bart - todos los emails" --show  # deshacer
```

## Fuera de alcance (backlog)

- Crear/renombrar/borrar labels de Gmail desde el CRM (la UI de crear
  etiqueta del sidebar sigue creando SOLO personales).
- UI de administración de etiquetas org (ocultar, mapear colores).
- Filtros combinados (varias etiquetas a la vez).

## Deploy

`api` + `frontend` + `worker-gmail` (process_history) + migraciones
`20260811_0095` (etiquetas) y `20260811_0096` (`text_color`). Después del
deploy, ejecutar una vez:

```bash
docker exec crmbo-api-1 python -m app.integrations.gmail_watch sync_labels
```

Tras la 0096 esa misma ejecución rellena el `text_color` de las labels ya
importadas (las contabiliza como «actualizadas» en el report).
