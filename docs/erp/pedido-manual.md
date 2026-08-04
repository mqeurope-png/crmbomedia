# BoHub ERP — Pedido manual (Fase D · PR D-2)

Alta de pedidos que **no vienen de WooCommerce**: encargos por teléfono,
muestras comerciales y reparaciones sin ticket Woo.

## Cuándo crear un pedido manual

| Caso | Origen correcto |
|---|---|
| Encargo por teléfono / email de un cliente B2B | **Pedido manual** |
| Muestra comercial o material de demostración | **Pedido manual** |
| Reparación / SAT sin ticket Woo previo | **Pedido manual** |
| Compra en cualquiera de las 3 tiendas Woo | *Automático* (webhook Woo) |
| Presupuesto/proforma que nace en FACTUSOL | *No se crea aquí* — FACTUSOL manda |

Un pedido manual entra en el circuito **igual que uno de Woo**: aparece en la
Bandeja Pedidos, pasa por Cola PEDIDOS (aprobación) y Cola SAT (preparación,
embalado multi-bulto, albarán/etiqueta, recogida) y se factura en FACTUSOL.
La única diferencia es el **origen** (`manual`) y que el número lo genera el
ERP en vez de la tienda.

## Cómo crearlo

**Bandeja Pedidos** (`/erp/orders`) → botón **«+ Nuevo pedido manual»** (arriba
a la derecha) → formulario `/erp/orders/new`:

1. **Cliente** (obligatorio: empresa **o** contacto). Ambos son autocompletados
   sobre el CRM. Al elegir empresa se rellenan solos el **NIF/CIF** y la
   **dirección de envío** (si la empresa los tiene). Si el cliente no existe, el
   enlace «¿No existe? Créalo primero en Contactos» abre el alta en otra pestaña.
2. **Fecha del pedido** — por defecto hoy.
3. **Líneas** (mínimo 1): `SKU` (texto libre), `Descripción`, `Cantidad`,
   `Precio unitario`. El **total de línea y el total del pedido se calculan
   solos**. «+ Añadir línea» / «✕» para gestionar filas.
   > El SKU es **texto libre**: no se mapea contra el catálogo de Woo/FACTUSOL
   > (eso llega en B-5, mapping de SKU).
4. **Envío** — dirección completa, o marcar **«Recogida en tienda»** (oculta la
   dirección).
5. **Facturación** — «Usar dirección de envío» marcado por defecto; desmárcalo
   para introducir una distinta.
6. **Notas internas** — opcional.
7. **«Crear pedido»** → redirige a la ficha del pedido recién creado.

### Estados iniciales

`preparation=pending_review` · `payment=pending` · `transport=not_shipped` ·
`invoice=not_invoiced`. Es decir: entra por **Cola PEDIDOS** para aprobación,
como cualquier pedido nuevo.

### Numeración

El número se genera solo con el patrón **`MANUAL-000001`** (secuencial sobre los
pedidos manuales existentes). Los de Woo conservan el suyo (`FLUXLA-5743`,
`BOPRIN-99866`, …).

### Dónde se guardan las direcciones

El pedido no tiene columnas de dirección, así que el alta manual guarda
**dirección de envío, de facturación, NIF y «recogida en tienda» dentro de
`packing_json`** (el mismo blob JSON donde ya viven los datos de embalaje y los
documentos). Sin migración. El historial del pedido registra el alta con el
evento `order_created_manual` y el usuario que lo creó.

## Cliente visible en todo el ERP (D-2)

Todas las vistas del ERP muestran ahora el **nombre del cliente** junto al
número de pedido — antes había que abrir el pedido para saber a quién iba:

| Vista | Dónde aparece |
|---|---|
| Bandeja Pedidos | columna **CLIENTE** entre «Pedido» y «Total» |
| Ficha del pedido | subtítulo de la cabecera: «Cliente: Nombre · Empresa» |
| Cola PEDIDOS | línea bajo el número en cada card |
| Cola SAT (ambas secciones) | línea bajo el número, tipografía menor que el número |
| Bandeja de excepciones | número de pedido + cliente en la columna «Pedido» |

Formato: `Nombre Apellido · Empresa` cuando existen los dos; si solo hay uno, se
muestra ese. Los endpoints de listado devuelven `contact_name` / `company_name`
resueltos en lote (2 consultas, sin N+1).
