# Campos de Brevo / Agile no importados todavía

Sprint Empresas. Inventario de campos que las plantillas Brevo o
AgileCRM exponen pero que el CRM aún no persiste en columnas
propias. Categorizado para priorizar.

Última actualización: 2026-06-15 (sub-PR 2/4 de Sprint Empresas).

## A — Lifted into first-class columns (sub-PR 2/4)

Campos que **ya están** en columnas dedicadas tras este sub-PR.
Aquí solo como ancla para futuras refactorizaciones.

| Campo Brevo                            | Campo Agile        | Columna CRM            |
| -------------------------------------- | ------------------ | ---------------------- |
| `JOB_TITLE` / `PUESTO` / `CARGO`       | `Title`            | `contacts.job_title`   |
| `LINKEDIN` / `LINKEDIN_URL`            | `LinkedIn`         | `contacts.linkedin_url` |
| `WEB` / `WEBSITE`                      | `Website`          | `contacts.personal_website` |
| `ADDRESS` / `DIRECCION` / `DIRECCIO`   | `address.address`  | `contacts.address_line` |
| `CIUDAD` / `CITY`                      | `address.city`     | `contacts.address_city` |
| `PROVINCIA` / `STATE`                  | `address.state`    | `contacts.address_state` |
| `CODIGO_POSTAL` / `ZIP` / `POSTCODE`   | `address.zip`      | `contacts.address_postal_code` |
| `PAIS_REGION` / `REGION`               | —                  | `contacts.address_region` |

## B — Custom fields del negocio (en JSON, render dinámico)

Brevo expone estos campos del flujo comercial. La ficha los lista
en la sección "Datos adicionales" leyendo del JSON sin hardcodear
cada uno. Si en algún momento el negocio quiere filtrarlos en
listas, hay que promocionarlos a columnas.

- `GRADO_DE_INTERES`
- `TIPO_DE_CENTRO`
- `INTERES`
- `PRODUCTOS_DE_INTERES`
- `EQUIPO_INTERESADO`
- `INTERESADO_EN_DEMO`
- `TITULARITAT_CENTRE`
- `ESTUDIS_ETIQUETES`
- `FAIG_PPTO_ENVIADO`
- `HORARIO`

## C — Multi-canal (sub-PR 3 + revert ✓)

Datos de comunicación adicional. **Cubierto en sub-PR 3** sólo para
teléfonos. La parte de emails secundarios + redes sociales se
revertió porque el negocio en realidad usa un único email por
contacto y nunca ha registrado handles sociales.

| Sistema | Campo origen                                    | Destino                       |
| ------- | ----------------------------------------------- | ----------------------------- |
| Brevo   | `TELEFONO_1..6`, `LANDLINE_NUMBER`, `TEL`       | `contact_phones` (source=brevo) |
| Brevo   | `EMAIL_SECUNDARIO`, `EMAIL2`, `EMAIL_2`         | `custom_fields` JSON (whitelisted, informativo) |
| Agile   | `phone(work/home/mobile/main/home-fax/work-fax/other)` | `contact_phones` (label=subtype) |
| Agile   | `email(personal/work)`                          | descartado en import (CRM usa `contacts.email` UNIQUE) |
| Agile   | `twitter`, `facebook`, `skype`, `xing`, `blog`, `googleplus`, `flickr`, `github`, `youtube`, `instagram` | descartados en import |

Backfill mirroring de `Contact.phone` a `contact_phones` via `scripts/backfill_contact_channels.py`. Backend enforce de "1 primary por contacto" en `/api/contacts/{id}/phones/{id}/primary`.

## D — Estado de suscripción (sub-PR 2/4 ✓)

| Campo Brevo                  | Destino CRM                                     |
| ---------------------------- | ----------------------------------------------- |
| `emailBlacklisted` (bool)    | `email_unsubscribes` con `source='brevo'`       |
| `EMAILABLE_UNSUBSCRIBED`     | mismo destino, vía `reconcile_brevo_unsubscribe`|

Reconciliado en cada upsert + en el backfill. Idempotente por
contacto.

## E — Notas + actividad (sub-PR 4, no incluido)

- `Notes` libres de AgileCRM (tabla aparte ya existe; falta map).
- Histórico de campañas Brevo recibidas / abiertas / clicadas
  (probablemente debería vivir en `email_message_events` con un
  `system='brevo'`).

## F — Subscriber state granular (sub-PR 2/4 ✓ parcial)

Brevo expone tres flags binarios además del blacklist:

- `emailBlacklisted` → ya materializado.
- `smsBlacklisted` → no usado (CRM no envía SMS todavía).
- `unsubscribed` legacy en algunos accounts → no observado en los
  exports actuales, pendiente de comprobar.

## G — IDs externos / sync metadata

- `Source` de Brevo (campaña / lista de captura). Hoy se ignora
  más allá de la lista; podría enriquecer `external_references.metadata`
  con la campaña de origen para attribución.
- `Opt-in date` de Brevo. Útil para GDPR; hoy se infiere del
  `created_at_external`.

## H — Datos fiscales / facturación (Sprint FactuSOL)

- Régimen de IVA, fecha de alta, condiciones de pago. Fuera de
  Sprint Empresas — vive en su propio sprint con la integración
  FactuSOL.
