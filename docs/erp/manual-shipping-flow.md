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

---

## Actualización D-1-fix1 (2026-08-04) — Cola SAT en 2 secciones + descarga real del albarán

### Cola SAT táctil con 2 secciones

Un pedido embalado (`packed`) ya no desaparece del táctil: sigue teniendo
trabajo de taller (imprimir, empaquetar, entregar al transportista).

```
┌─ Cola SAT (/erp/sat) ──────────────────────────────────────────────┐
│ Por embalar: N · Listos: M                                          │
│                                                                     │
│  📦 Por embalar                     🚚 Listos para envío            │
│  ───────────────                    ──────────────────             │
│  in_queue / preparing / blocked     packed  &  transporte NO en    │
│  (card → modo trabajo SAT →         (in_transit/delivered/external)│
│   Empezar / Embalado)               · 📄 Imprimir/Falta albarán    │
│                                     · 🏷️ Imprimir/Falta etiqueta   │
│                                     · 📤 Marcar recogido (confirmа) │
│                                     · Reabrir preparación           │
└─────────────────────────────────────────────────────────────────────┘
```

En móvil/tablet las 2 secciones se apilan; en escritorio pueden ir en columnas.

**Guía para el operativo del taller:**
- **📦 Por embalar** — pedidos que aún hay que preparar/embalar. Abre la card →
  «Empezar preparación» → «Embalado» (modal multi-bulto). Al embalar, el pedido
  salta a «Listos para envío».
- **🚚 Listos para envío** — ya embalados. Aquí:
  - `📄 Imprimir albarán` / `🏷️ Imprimir etiqueta` → abren el PDF en pestaña
    nueva (imprime desde el navegador). Si dice `Falta …`, lleva a la ficha para
    subirlo/descargarlo.
  - `📤 Marcar recogido` → confirma («¿el paquete ha salido?») → el pedido pasa a
    `in_transit` y **sale de la Cola SAT** (ya no es trabajo pendiente).
  - `Reabrir preparación` → si se detecta un error de picking tarde, vuelve a
    `in_queue`.

`POST /api/erp/orders/{id}/mark-picked-up`: exige el pedido `packed`; lleva
transporte a `in_transit` (auto-creando el registro de envío). Es una **recogida
manual** (transportista sin tracking en el sistema): se marca `manual_pickup` en
la evidencia, así que respeta el guard de tracking sin exigir número. Acepta un
`tracking_number` opcional si el operativo lo tiene. SAT puede disparar estas
transiciones (arcos ampliados a SAT en D-1-fix1: son acciones físicas del taller).

### Descarga real del albarán de Woo — cascada elegida (B.2 + B.3)

El 502 anterior se debía a que el plugin **free** no expone la REST Pro y el
admin-ajax exige nonce de sesión. Nueva lógica de `fetch-from-woo`, en cascada:

1. **REST del plugin** (Pro): `GET /wp-json/wcpdf/v1/documents/packing-slip/{id}`.
2. **B.2 — acceso público por `order_key`**: se lee el `order_key` del pedido
   (`GET /wp-json/wc/v3/orders/{id}`, que sí funciona) y se prueba la URL pública
   `/?wpo_wcpdf_document=packing-slip&order_ids={id}&access_key={order_key}` (y la
   variante `order_key=`). Funciona si en el plugin está activado **Document
   access → invitados** (WooCommerce → PDF Invoices → Advanced). Confirmar por
   tienda en el deploy.
3. **B.3 — albarán generado por el CRM** (`app/erp/albaran_pdf.py`, `reportlab`):
   si el plugin no entrega nada, se **genera un albarán propio** con los datos del
   pedido Woo (destinatario, líneas, código de barras del nº de pedido) y se
   guarda con `source=crm_generated_pdf`. **Es el backstop garantizado: el
   endpoint ya nunca devuelve 502 por el plugin.** El operativo puede seguir
   imprimiendo el «bonito» desde WP admin si lo prefiere.

Solo devuelve 502 si el propio `GET /orders/{id}` de WooCommerce falla (tienda
caída) — ahí sí toca subir el albarán a mano.

> Dependencia nueva: `reportlab` en `requirements.txt` (pure-Python, sin libs de
> sistema; arrastra `pillow`). Sin migración ni env nuevos.

---

## Actualización D-1-fix2 (2026-08-04) — mu-plugin WP para el albarán oficial + chip en «Por embalar»

### Descarga del albarán — método definitivo: **mu-plugin → reportlab**

Bart no compra la versión Pro del plugin PDF Invoices, así que ni la REST Pro ni
el acceso público por `order_key` (B.1/B.2) sirven — **se retiran**. En su lugar,
un **mu-plugin WordPress propio** (`bohub-albaran`) expone un endpoint público
autenticado por **token compartido** que genera internamente el PDF *oficial* del
plugin free (con logo, IVA y datos fiscales):

```
GET {store}/?bohub_albaran=packing-slip&order_id={id}&token={TOKEN}
→ 200 application/pdf   (PDF oficial del plugin)
→ 401 text/plain        (token incorrecto)
→ 404 text/plain        (mu-plugin no instalado o PDF Invoices inactivo)
```

Nuevo flujo de `get_packing_slip_pdf()` / `fetch-from-woo`:

1. **mu-plugin** (preferente): `GET …?bohub_albaran=packing-slip&order_id=…&token=…`
   (timeout 20 s). Si devuelve `application/pdf` → ese es el albarán oficial.
2. **reportlab** (backstop): si el token no está configurado, el mu-plugin no
   está instalado, hay 401/404/timeout o la respuesta no es un PDF → el CRM
   genera su albarán propio (`source=crm_generated_pdf`). **Nunca 502 por el
   plugin** (solo si `GET /orders/{id}` de Woo falla, tienda caída).

**Env nueva:** `WOOCOMMERCE_ALBARAN_TOKEN` (mismo token en las 3 tiendas). El
valor real vive **solo en `.env.production`**, nunca en el repo. Vacío → el CRM
usa siempre reportlab.

### Instalación del mu-plugin en una tienda nueva

1. **Copiar el fichero** `wp-content/mu-plugins/bohub-albaran.php` a la tienda
   (los mu-plugins se cargan solos, sin activar). Contrato de referencia:

   ```php
   <?php
   /* Plugin Name: BoHub Albarán (mu-plugin) */
   // Token compartido — el MISMO en las 3 tiendas. Reemplazar por el real.
   if ( ! defined( 'BOHUB_ALBARAN_TOKEN' ) ) {
       define( 'BOHUB_ALBARAN_TOKEN', 'REEMPLAZAR_POR_EL_TOKEN_REAL' );
   }
   add_action( 'wp_loaded', function () {          // wp_loaded: Woo + PDF Invoices ya cargados
       if ( empty( $_GET['bohub_albaran'] ) ) return;
       $token = isset( $_GET['token'] ) ? (string) $_GET['token'] : '';
       if ( ! hash_equals( BOHUB_ALBARAN_TOKEN, $token ) ) {
           status_header( 401 ); header( 'Content-Type: text/plain' );
           echo 'token invalido'; exit;
       }
       $order_id = isset( $_GET['order_id'] ) ? absint( $_GET['order_id'] ) : 0;
       $type     = sanitize_text_field( $_GET['bohub_albaran'] ); // packing-slip
       if ( ! function_exists( 'wcpdf_get_document' ) || ! $order_id ) {
           status_header( 404 ); header( 'Content-Type: text/plain' );
           echo 'PDF Invoices no activo o pedido invalido'; exit;
       }
       $doc = wcpdf_get_document( $type, wc_get_order( $order_id ), true );
       if ( ! $doc ) { status_header( 404 ); header( 'Content-Type: text/plain' );
           echo 'documento no disponible'; exit; }
       header( 'Content-Type: application/pdf' );
       echo $doc->get_pdf(); exit;
   } );
   ```

2. **Poner el token real** en la constante `BOHUB_ALBARAN_TOKEN` (mismo valor que
   `WOOCOMMERCE_ALBARAN_TOKEN` del CRM).
3. **Probar con curl** (con un `order_id` real):
   ```bash
   curl -sS "https://<dominio>/?bohub_albaran=packing-slip&order_id=<ID>&token=<TOKEN>" \
     -o /tmp/test.pdf -w "HTTP %{http_code} · %{content_type}\n"
   # esperado: HTTP 200 · application/pdf
   ```
4. **Añadir la env al CRM**: `WOOCOMMERCE_ALBARAN_TOKEN=<TOKEN>` en
   `.env.production` y redeploy `api`.

### Cola SAT táctil — chip de albarán en AMBAS secciones

```
┌─ Cola SAT ──────────────────────────────────────────────────────────┐
│  📦 Por embalar                       🚚 Listos para envío           │
│  ───────────────                      ──────────────────            │
│  card → modo trabajo                  card:                         │
│  · 📄 Descargar/Imprimir albarán      · 📄 Imprimir/Falta albarán    │
│    (cotejar líneas antes de embalar)  · 🏷️ Imprimir/Falta etiqueta   │
│                                       · 📤 Marcar recogido           │
│                                       · Reabrir preparación          │
└──────────────────────────────────────────────────────────────────────┘
```

En «📦 Por embalar» cada card lleva el chip de albarán (sin salir del táctil):
- `has_albaran` → `📄 Imprimir albarán` → abre el PDF en pestaña nueva.
- si no → `📄 Descargar albarán` → un click dispara `fetch-from-woo`
  (mu-plugin → reportlab), refresca y **auto-abre** el PDF. Si falla, aviso con
  enlace a la ficha para subirlo a mano.
La **etiqueta** sigue solo en «Listos para envío» (requiere multi-bulto).
