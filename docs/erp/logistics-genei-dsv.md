# Logística — Genei (paquetería) + DSV (palés)

**Estado:** investigación desde fuentes públicas (los portales están fuera
del allowlist de red del entorno de dev — validación final con credenciales
desde máquina de Bart). Conclusión anticipada: **ambos proveedores tienen
API real**; ninguno obliga a plan alternativo manual, aunque el de DSV
requiere onboarding formal.

## Genei — paquetes pequeños

Qué es: comparador/agregador de envíos español (300+ servicios: Correos
Express, SEUR, GLS, UPS, Zeleris…). Integraciones **gratuitas** para
usuarios registrados (sin cuotas ni volumen mínimo).

Lo confirmado en fuentes públicas:
- **Tiene API** (la usan sus plugins oficiales de WooCommerce/PrestaShop/
  Shopify) — el plugin WooCommerce muestra tracking, códigos de envío y
  etiquetas desde el backend de la tienda, y su web ofrece "Integra Genei
  en tu sistema" (https://www.genei.es/soluciones/integraciones).
- Los plugins transforman pedidos → envíos Genei automáticamente, con
  gestión centralizada en el área de usuario.

Alcance mínimo a validar con la cuenta de Bomedia (soporte Genei entrega la
doc API al solicitarla desde el área de cliente):
1. Crear expedición (origen/destino/bultos/peso/servicio elegido).
2. Descargar etiqueta (PDF/PNG).
3. Tracking (¿webhook o polling? — los plugins sugieren polling).
4. Incidencias.

**Plan B (si la API directa se retrasa):** instalar su plugin WooCommerce
oficial en las tiendas → los envíos se crean desde WP y el ERP lee el
tracking desde los meta_data del pedido Woo (ya sincronizados). Menos
control, cero desarrollo.

## DSV — palés

Lo confirmado en el portal oficial (developer.dsv.com):
- **Developer Portal real** con catálogo de APIs (https://developer.dsv.com/apicatalogue)
  y guías por producto: myDSV (`/guide-mydsv`), XPress (`/guide-xpress`),
  Solutions (`/guide-solutions`).
- **Booking API**: "Submit new booking" — booking real de transporte, con
  opción draft o final.
- **Tracking API**: estado + últimos eventos de los envíos.
- **Webhooks**: "reverse API" que notifica al endpoint que registres; para
  el detalle del envío se combina con la Tracking API.
- Posicionan la API como alternativa moderna a EDI: bookings, **etiquetas**,
  tracking.

Pasos de onboarding (acción Bart):
1. Cuenta myDSV de Bomedia → solicitar acceso API en developer.dsv.com
   (alta de aplicación + API key; suele pasar por el account manager DSV).
2. Sandbox si lo ofrecen; si no, bookings draft como entorno seguro.
3. Validar: crear booking de palé draft → etiqueta/documentación →
   registrar webhook de tracking → consultar Tracking API.

**Plan B:** si el onboarding API se alarga, arrancar el MVP con estado de
transporte manual en la cola SAT (el operario pega el tracking de myDSV al
marcar "enviado") + email de notificación. El dominio de transporte de la
máquina de estados no cambia — solo la fuente del evento (manual vs API).

## Diseño común (independiente del proveedor)

- Tabla `shipments` (ver data-model.md): `carrier` (`genei`/`dsv`/otros),
  `carrier_shipment_id`, `tracking_number`, `label_url`, `status` propio
  normalizado + `raw_status` del proveedor, `events_json`.
- Adaptador por proveedor con la MISMA interfaz (`create_shipment`,
  `get_label`, `get_tracking`, `cancel`): el motor del ERP no sabe de
  Genei/DSV, solo de `shipments`.
- Credenciales en `integration_accounts` (`system='genei'` / `'dsv'`).
- Tracking por polling programado (RQ scheduler) + webhook cuando exista
  (DSV lo tiene; Genei a confirmar).

## Bloqueos

| Qué | Quién |
|---|---|
| Cuenta Genei + solicitar doc API a soporte | Bart |
| Alta developer.dsv.com + API key (vía account manager) | Bart |
| Allowlist de red del entorno dev (genei.es, developer.dsv.com) o ejecutar validación desde otra máquina | Bart / infra |
