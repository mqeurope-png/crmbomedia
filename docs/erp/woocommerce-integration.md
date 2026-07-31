# WooCommerce — Integración de pedidos online (lectura + webhooks + multi-tienda)

**Estado:** cliente de lectura + receiver de webhooks + test manual listos.
La prueba live contra mbolasers.com está **pendiente de Consumer Key/Secret**
(Bart los genera en WP admin → WooCommerce → Ajustes → Avanzado → REST API,
permiso *Read* para el sync; el webhook usa su propio secret).

## Lectura (REST API v3)

- Base: `https://{tienda}/wp-json/wc/v3/` — auth HTTP Basic (CK/CS) sobre HTTPS.
- `GET /orders?status=&after=&per_page=&page=` — estados nativos:
  `pending`, `processing`, `on-hold`, `completed`, `cancelled`, `refunded`,
  `failed`. Paginación por headers `X-WP-Total(Pages)`, máx 100/página.
- `GET /orders/{id}` — pedido completo: `line_items[]` (con `sku`,
  `product_id`, `quantity`, `total`), `billing`, `shipping`,
  `customer_id`, `payment_method`, `date_paid`, `meta_data`.
- `GET /customers/{id}` y `GET /products` (este último alimenta la
  conciliación SKU, ver sku-conciliation.md).

Código: `backend/app/integrations/woocommerce/client.py` (retry 429/5xx con
backoff; multi-tienda por prefijo de entorno). Test manual:
`python -m scripts.woocommerce_read_test` → últimos 20 pedidos.

## Webhooks

Configurar por tienda en WP admin (Ajustes → Avanzado → Webhooks):

| Campo | Valor |
|---|---|
| Topic | `order.created` y `order.updated` |
| URL de entrega | `https://crm.bomedia.es/webhooks/woocommerce/mbolasers` |
| Secreto | aleatorio fuerte → `WOO_MBOLASERS_WEBHOOK_SECRET` |
| Versión API | WP REST API v3 |

- **Firma:** `X-WC-Webhook-Signature` = base64(HMAC-SHA256(secret, body
  crudo)). Validación con `hmac.compare_digest` en
  `app/integrations/woocommerce/webhooks_prototype.py` (**no registrado en
  app.main** — restricción Sprint 0; el MVP lo registra + encola job RQ).
- **Ping de activación:** al activar el webhook WP manda body
  `webhook_id=N` sin JSON — responder 200 sin validar firma o el webhook
  queda en "pausado".
- **No existe topic `order.payment_complete` nativo**: el pago se detecta
  en `order.updated` cuando `date_paid` pasa de `null` a fecha (o status →
  `processing`). Documentado así en la máquina de estados de pago.
- Reintentos: WP reintenta entregas fallidas con backoff y desactiva el
  webhook tras fallos persistentes → el receiver SIEMPRE responde rápido
  (encolar y 200) y el sync por polling (lecturas `after=`) actúa de red de
  seguridad ante webhooks perdidos.

## Multi-tienda

Mismo patrón que las 6+ cuentas AgileCRM del CRM: una fila por tienda en
`integration_accounts` con `system='woocommerce'`, `account_id` = slug de
tienda (`mbolasers`, `artisjet-europe`, `fluxlasers`), credenciales
cifradas (CK/CS + webhook secret) y `base_url` en el config JSON. El
receiver enruta por el path (`/webhooks/woocommerce/{store}`) y contrasta
además `X-WC-Webhook-Source` con la `base_url` de la cuenta (defensa ante
cruces de configuración).

Sprint 0 usa prefijos de entorno (`WOO_MBOLASERS_*`) para los scripts; la
migración a `integration_accounts` es tarea del MVP (backlog-mvp.md).

## Validación pendiente (con credenciales)

1. `python -m scripts.woocommerce_read_test` → 20 pedidos reales de
   mbolasers.com (verificar SKUs poblados en line_items).
2. Crear webhook de prueba apuntando a un entorno accesible y capturar 1
   payload real de `order.created` + 1 de `order.updated` con pago →
   pegar shapes en este doc.
3. Confirmar si el hosting (LiteSpeed/WAF de IONOS) limita el ritmo de la
   REST API con catálogos grandes (paginación completa de /products).
