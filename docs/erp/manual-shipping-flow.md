# BoHub ERP — Flujo de expedición manual (Fase D · PR D-1)

Flujo completo para preparar y expedir un pedido **sin depender aún de la API
de Genei/DSV**. Genei (Fase B-4) llegará después como capa de automatización
encima de este flujo manual, que seguirá funcionando como fallback.

## Estados y transiciones relevantes

**Preparación** (Cola SAT):

```
in_queue → preparing → packed
              ↓  ↑
           blocked (excepción)   packed → in_queue (reabrir)
```

- `in_queue → preparing` («Empezar preparación»): exige pago cobrado/aprobado.
- **`preparing → packed` («Embalado»)**: exige **≥1 bulto medido** (guard
  `packages_measured_before_packed`). Se dispara desde el modal multi-bulto, no
  como transición directa.

**Transporte**: `not_shipped → label_created → in_transit → delivered`
(`label_created` exige `packed`; `in_transit` exige `tracking_number`).

## Multi-bulto obligatorio (paso a `packed`)

Al pulsar «Embalado» (ficha del pedido o modo trabajo SAT) se abre
`EmbalarModal`:

- Empieza con **1 bulto**; «+ Añadir bulto» añade más; «Eliminar» quita (excepto
  el primero).
- Cada bulto: **peso (kg) + alto + ancho + fondo (cm)**, todos **> 0**.
- «Guardar y embalar» hace `POST /orders/{id}/packages` (reemplaza la lista
  entera, idempotente) y luego `POST /orders/{id}/transition/preparation/packed`.
- El backend rechaza (400) bultos incompletos o con valores ≤ 0, y rechaza pasar
  a `packed` sin ningún bulto (`no_packages`). El guard del engine protege
  además la transición genérica (`guard_failed`).

Tabla `shipment_packages` (migración `0085`): `order_id` (CASCADE), `position`,
`weight_kg`, `height_cm`, `width_cm`, `depth_cm`.

## Albarán y etiqueta (`shipment_files`)

Un pedido puede acumular varios ficheros del mismo `kind`; solo el último con
`replaced_at IS NULL` es el **vigente** (los previos se conservan). Los bytes
viven en el storage; en BD solo la `storage_path` relativa.

### Albarán — origen según el pedido

- **Pedido Woo** (BOP/ART/FLE): botón «Descargar albarán de Woo»
  (`POST /orders/{id}/albaran/fetch-from-woo`) → descarga el PDF del plugin
  *PDF Invoices & Packing Slips* y lo guarda con `source=woo_pdf_plugin`.
  Idempotente (no re-descarga si ya hay uno vigente). Si la descarga falla
  (502), el operativo sube el PDF a mano.
- **Pedido sin Woo** (o si el fetch falla): «Subir albarán» (upload manual,
  `source=manual_upload`).

El cliente Woo (`get_packing_slip_pdf`) intenta **primero la REST del plugin**
(`GET /wp-json/wcpdf/v1/documents/packing-slip/{order_id}`) y, si no está
(404 / versión free), el **admin-ajax** (`generate_wpo_wcpdf`, que suele exigir
nonce de sesión → probable fallo vía API → upload manual). Qué endpoint funciona
en cada tienda **lo confirma el discovery de Bart** con la Consumer Key/Secret
de `.env.production`.

### Etiqueta

**Siempre subida manual** en Fase D (`POST /orders/{id}/shipping-files`,
`kind=etiqueta`). La automatización Genei llega en B-4.

### Ver / imprimir

`GET /orders/{id}/shipping-files/{file_id}/download` devuelve el PDF con
`Content-Disposition: inline` → se abre en una **pestaña nueva** del navegador y
el operativo imprime desde el diálogo del navegador (no hay impresora térmica en
el taller). En la Cola SAT táctil, cada card en `preparing`/`packed` muestra
chips `Albarán ✓/✗` y `Etiqueta ✓/✗`; el chip ✓ abre el PDF, el ✗ lleva a la
ficha para subirlo.

## Storage (`app/storage`)

Interfaz `ShippingStorage` (`save` / `read` / `delete`). Backend por env:

| `STORAGE_BACKEND` | Implementación | Estado |
|---|---|---|
| `local` (default) | `LocalShippingStorage` | disco del VPS |
| `hidrive` | `HiDriveShippingStorage` | **stub** (`NotImplementedError`) |

**Local** guarda en `{LOCAL_SHIPPING_STORAGE_DIR}/{order_id}/{kind}/{uuid}_{fichero}`
y persiste la ruta **relativa** (portable). Default del directorio:
`/opt/crmbo/uploads/erp-shipping`.

### Migrar a HiDrive cuando haya espacio

1. Implementar `HiDriveShippingStorage.save/read/delete` (WebDAV, reusando el
   skeleton HiDrive de Fase A: `HIDRIVE_WEBDAV_URL` + `HIDRIVE_USERNAME` +
   `HIDRIVE_PASSWORD_ENCRYPTED` con Fernet).
2. Poner `STORAGE_BACKEND=hidrive` y reiniciar el `api`.
3. (Opcional) migrar los ficheros ya existentes de disco a HiDrive.

## Variables de entorno nuevas

- `STORAGE_BACKEND=local` (default).
- `LOCAL_SHIPPING_STORAGE_DIR=/opt/crmbo/uploads/erp-shipping` (default).

## Deploy

```bash
mkdir -p /opt/crmbo/uploads/erp-shipping
cd /opt/crmbo && git pull origin main && \
docker compose -f docker-compose.prod.yml build api frontend && \
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head && \
docker compose -f docker-compose.prod.yml up -d --force-recreate api frontend
```

El directorio `/opt/crmbo/uploads/erp-shipping` se monta como volumen bind en el
servicio `api` (patrón espejo de `email-templates`).
