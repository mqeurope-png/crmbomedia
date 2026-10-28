# Pedidos web en espera (`on-hold`): traerlos a mano

**Caso (Bart, 28/10/2026).** Los pedidos de WooCommerce `on-hold` son casi
todos transferencias bancarias aún sin cobrar (`payment_method: "bacs"`), de
clientes reales con empresa. El importador solo crea en BoHub los pedidos
`processing` / `completed` / `refunded`, así que un `on-hold` no entra hasta que
la tienda lo cobra, y Bart quiere prepararlo y facturarlo ya. Desde ahora se
puede **traer a mano**, uno a uno, desde ERP · Pedidos.

La idea en una frase: **traerlo es la autorización**. Una vez dentro es un
pedido normal (se aprueba, se prepara, se embala, se envía y se factura). No
hay ningún estado nuevo de «sin pagar» ni bloqueos nuevos: el pago se queda en
su valor real («pendiente») hasta que la tienda lo cobre, y lo único que
distingue al pedido es quién y cuándo lo trajo.

## Cómo se usa

- **Dónde.** ERP · Pedidos → botón para traer un pedido web en espera. Abre la
  lista de los `on-hold` de todas las tiendas Woo activas, **leída en vivo** de
  la REST de cada tienda: tienda, número, fecha, cliente o empresa, importe,
  forma de pago y enlace al pedido en la tienda. Buscador por número, cliente,
  empresa o email (sin tildes ni mayúsculas). Los que ya están en BoHub salen
  marcados.
- **Traer.** Se elige uno, se trae y se abre el pedido creado. En la ficha
  queda a la vista que se trajo a mano (quién, cuándo), qué dijo FACTUSOL y,
  si la web cambió el pedido después, qué cambió.
- **Quién.** Quien crea pedidos **y** ve los pedidos web:
  `Cap.ORDERS_CREATE` + `Cap.ORDERS_VIEW_WEB` (roles pedidos y admin). El
  comercial no ve pedidos web; el taller (sat) no crea pedidos.
- **Un número que no sale en la lista.** Si se busca un número que no está
  entre los `on-hold`, la respuesta dice qué es ese número en cada tienda donde
  exista: «cancelado en la web: no se puede traer», «pendiente de pago
  (carrito sin pagar): no se trae», «ya está en BoHub», «procesando: entra
  solo»…

## API

Router `backend/app/erp/api/pedidos_web.py`, prefijo `/api/erp/pedidos-web`;
la lógica vive en `backend/app/erp/pedidos_web.py`.

### `GET /api/erp/pedidos-web/en-espera?q=<texto opcional>` → 200

```json
{
  "items": [{"store_id": 3, "tienda": "fluxlasers", "tienda_nombre": "fluxlasers.es",
             "woo_id": 5873, "numero": "5873", "fecha": "2026-10-23T09:18:59+00:00",
             "cliente": "Nombre Apellidos", "empresa": "Escola de Ponent", "email": "a@b.es",
             "total": 44.77, "moneda": "EUR", "forma_pago": "Transferencia bancaria directa",
             "enlace_tienda": "https://…/wp-admin/post.php?post=5873&action=edit",
             "en_bohub": null}],
  "tiendas": [{"store_id": 3, "tienda": "fluxlasers", "tienda_nombre": "fluxlasers.es",
               "ok": true, "error": null, "en_espera": 14}],
  "consulta": []
}
```

- Solo `on-hold` de las tiendas Woo **activas**. Las tiendas se leen en
  paralelo, con tiempo acotado por tienda y en total (unos 25 s como mucho): una
  que no responde sale en `tiendas` con `ok: false` y su `error`, sin tumbar a
  las demás.
- `en_bohub` = `{order_id, order_number}` si el pedido ya está en BoHub.
- `q` filtra por número, cliente, empresa o email. Si `q` es un número que no
  está entre los `on-hold`, `consulta` dice, por tienda donde exista, su
  `estado` de Woo y un `motivo` legible.
- Solo lee: ni escribe en BoHub ni en las tiendas.

### `POST /api/erp/pedidos-web/traer` → 200

Cuerpo `{"store_id": 3, "woo_id": 5873}`.

```json
{"order_id": "uuid", "order_number": "FLUXLA-5873", "creado": true,
 "factusol": {"codigo": "enlazado", "mensaje": "…", "referencia": "FLE-005873"}}
```

- Relee el pedido **en vivo** (`get_order`); la lista puede estar desfasada.
- Idempotente: si ya está en BoHub devuelve el que hay con `creado: false`.

Errores, siempre `{"detail": {"code": "...", "detail": "mensaje legible", "order_id": "opcional"}}`:

| HTTP | `code` | Cuándo |
|---|---|---|
| 409 | `cancelado_en_la_web` | El pedido está `cancelled` en la tienda. |
| 409 | `no_es_un_pedido` | `pending` (carrito sin pagar) o `failed` (pago fallido). |
| 409 | `estado_no_admitido` | `refunded`, `trash` o cualquier otro estado. |
| 409 | `ya_hay_pedido_en_bohub` | Ya hay en BoHub otro pedido para este pedido web (ver «Sin duplicados»); lleva su `order_id`. |
| 404 | `tienda_desconocida` | El `store_id` no es una tienda Woo activa. |
| 404 | `pedido_no_encontrado` | La tienda no tiene ese pedido. |
| 422 | `pedido_no_importable` | El importador no puede con él (p. ej. sin email válido de facturación). |
| 502 | `tienda_no_responde` | La tienda no contesta o da un error. |
| 403 | — | Sin `ORDERS_CREATE` + `ORDERS_VIEW_WEB`. |

### Detalle del pedido

`GET /api/erp/orders/{id}` (el esquema que ya usa la ficha) gana:

```json
"traido_a_mano": {"at": "2026-10-28T12:00:00+00:00", "por": "Nombre" ,
                  "factusol": {"codigo": "…", "mensaje": "…", "referencia": "…"},
                  "diferencias": ["…"]}
```

`null` si el pedido entró solo; `por`, `factusol` y `diferencias` pueden ser
`null`.

## Traer = el mismo camino que el importador

- **Un solo punto de entrada.** Traer llama a `mapper.import_woo_order` —el
  mismo del webhook, el backfill, «Reimportar pedido» y «pagados que faltan»—
  con la autorización `TraidoAMano(user_id)`. Es la **única** excepción a
  `CREATE_ON_STATUSES`, y solo para `on-hold`. El webhook y el backfill siguen
  ignorando los `on-hold` que nadie ha traído.
- **El mismo pedido.** Líneas (con su IVA real; una línea sin SKU entra como
  `woo-pid-<product_id>`), portes con su IVA, cliente, empresa (por CIF de
  `meta_data` `_billing_nif` y por el cliente de FACTUSOL, solo lectura),
  direcciones, totales, método de pago y número `SLUG[:6]-número`
  (`FLUXLA-5873`, con `_store_slug` inyectado como en el webhook). Un pedido
  traído a mano y el mismo importado al pasar a `processing` dan **exactamente
  el mismo pedido**, salvo la marca de traído.
- **La marca.** `Order.manual_web_import_at` (cuándo) y
  `Order.manual_web_import_by_user_id` (quién), migración 0150. Solo la lleva un
  pedido traído a mano; ninguno más. En el código: `es_traido_a_mano(order)`
  (`backend/app/erp/pedidos_web.py`) o, en los módulos que no la importan,
  `order.manual_web_import_at is not None`.
- **Estados al entrar.** `payment_status` = `pending` (honesto: `date_paid` es
  null), `preparation_status` = `pending_review` (como un pedido manual: Bart lo
  aprueba cuando quiera), `woo_status` = `on-hold`.
- **Rastro.** Evento de auditoría `erp.pedido_web_traido` (tienda, id de Woo,
  estado, si fue con autorización y el veredicto de FACTUSOL).

### Qué se hace según el estado de la tienda al traer

| Estado en Woo | Resultado |
|---|---|
| `on-hold` | Se trae con autorización (marca de traído a mano). |
| `processing` / `completed`, no está en BoHub | Se importa por el camino normal, **sin** marca: ya está pagado. |
| `cancelled` | 409 `cancelado_en_la_web`. |
| `pending` / `failed` | 409 `no_es_un_pedido`. Tampoco salen en la lista. |
| `refunded`, `trash`, otros | 409 `estado_no_admitido`. |

`processing` / `completed` / `refunded` se siguen importando solos, como siempre.

## Sin duplicados

- **Cerrojo por tienda.** `import_woo_order` coge, antes de buscar o crear, un
  `SELECT … FOR UPDATE` de la fila `integration_accounts` de la tienda (en
  SQLite no hace nada) y vuelve a mirar si el pedido existe con una lectura que
  ve lo último confirmado. Así traer a mano y el webhook (o dos clics) no crean
  dos pedidos: no hay restricción única en `orders`.
- **Traer dos veces** devuelve el que hay (`creado: false`).
- **Antes de traer**, si ya hay en BoHub un pedido con el mismo `order_number`
  (de cualquier origen, como `missing._ya_en_bohub`) o uno creado «desde
  FACTUSOL» cuyo `packing_json.factusol_source.referencia` es el REFPCL de este
  pedido web → 409 `ya_hay_pedido_en_bohub` con su id. No se crea otro: Bart
  decide.
- **Cuando la tienda lo cobra** llega el webhook de siempre
  (`order.updated` / `order.payment_complete`): empareja por
  `(woocommerce, id de Woo, tienda)` y **actualiza** el que hay
  (`_refresh_existing`): pago `pending → paid` con `date_paid`, `woo_status`
  al día y, si sigue sin aprobar, entrada en la Cola SAT
  (`enqueue_paid_order`), igual que un pedido que entra pagado.
- **Si el cobro llega sin webhook** (el plugin de TSM): «Pedidos web pagados
  que no llegan» lo refresca por ese mismo camino; ver
  [woo-pagados-que-faltan.md](woo-pagados-que-faltan.md#pedidos-traídos-a-mano).
- **Si la web cambió el pedido** entre `on-hold` y el cobro (total o líneas),
  las líneas **no** se cambian en silencio (el refresco nunca lo ha hecho): se
  deja constancia en `packing_json["traido_a_mano"]["diferencias"]` (lista de
  textos, la ficha la enseña) para que Bart lo revise. No bloquea nada.

## Una vez dentro: un pedido normal

| Puerta | Con un pedido traído a mano |
|---|---|
| Aprobar (`pending_review → in_queue`, `/approve`, en bloque, «Añadir a mano a la Cola SAT», enviar al SAT) | Pasa: nunca miró el pago. |
| «Empezar preparación» (`in_queue → preparing`, `GUARD_PAYMENT_OK`) | **Pasa.** `sample_orders.payment_not_required(order)` = muestra **o** traído a mano. |
| Embalar, crear envío / etiqueta, recogida | Pasan: solo dependían de la preparación. |
| Entrada automática en la Cola SAT (`enqueue_paid_order`) | No al traerlo (queda en «Por revisar»). Sí cuando llega el cobro, si sigue sin aprobar. |
| Emitir factura FACTUSOL | Pasa: nunca miró el pago. Necesita el pedido de cliente de FesteWeb (por REFPCL); «cobrada al emitir» solo si el pedido ya está `paid`. |
| Aviso «⚠ SIN COBRAR» (Cola SAT, ficha) | **Se queda**: es información cierta, no un bloqueo. |
| Bandeja (orden «pagados primero») | Va con los que pueden avanzar (mismo criterio que la puerta). |
| Seguimiento y hoja de Drive | **Se ve** aunque la tienda lo tenga `on-hold` / `pending` (como un forzado, sin forzarlo): `seguimiento.woo_status_visibility`, `woo_status.WEB_UNPAID_STATUSES`. Cancelado, fallido o en la papelera de la tienda, o anulado en BoHub: oculto como siempre. |
| Cuadre «Pedido esperando aprobación» | **Lo cuenta** (antes se saltaba los web ocultos por estado). |
| Cuadre «Pedido pagado en WooCommerce que no está en BoHub» | No lo avisa: está en BoHub. |
| `purge.py` (borrado de carritos) y `cleanup.py` (exclusión de no-processing) | **Nunca** lo tocan: filtran `manual_web_import_at IS NULL`. |
| Albarán FACTUSOL | Como cualquier web: no lleva (409 `web_order_no_albaran`). |

Nada de lo propio de las muestras le aplica (no facturable, sin cobro, Cola SAT
directa): eso sigue mirando `is_sample_order`.

## FACTUSOL: solo lectura

- **Cómo llega un pedido web a FACTUSOL.** Lo crea FesteWeb (la app de la web,
  no BoHub) como pedido de cliente (F_PCL) con
  `REFPCL = <prefijo de la tienda>-<nº a 6 cifras>` (`FLE-005873`). BoHub
  **nunca** crea un F_PCL; el enlace es en vivo por esa referencia
  (`_store_ref_prefix` + `_compose_ref` + `find_pcl_by_order`) y no se guarda.
  El prefijo sale de la tienda (`metadata_json.factusol_ref_prefix`) o de
  Configuración ERP → «Tiendas y referencias»; si no hay, se deriva de las tres
  primeras letras del número (`FLU`), que para fluxlasers es **incorrecto**
  (FesteWeb usa `FLE`).
- **Al traer se busca y se dice, sin crear nada** (`veredicto_factusol`), en
  el ejercicio de la fecha del pedido:

| `codigo` | Cuándo | Qué hacer |
|---|---|---|
| `enlazado` | Exactamente un F_PCL con ese REFPCL, su cliente (CLIPCL → F_CLI.NIFCLI normalizado) es el CIF de la web (`_billing_nif`) y su total (TOTPCL) es el de la web (±0,01). | Nada: BoHub lo enlaza por la referencia, como a cualquier web. |
| `posible` | Ninguno con ese REFPCL, pero uno (y solo uno) de un cliente con ese CIF y ese importe. | La referencia no casa: revisar el prefijo de la tienda en Configuración ERP. |
| `no_encontrado` | Ni por referencia ni por CIF + importe (o la web no trae CIF). | Esperar a que FesteWeb lo pase; BoHub lo enlazará solo por la referencia. |
| `dudoso` | Varios F_PCL con esa referencia, o el de la referencia no casa en cliente o importe, o varios candidatos por CIF + importe. | Revisarlo en FACTUSOL. |
| `sin_factusol` | FACTUSOL no configurado o no responde. | Nada que hacer en el momento; no impide traer. |

- El veredicto va en la respuesta y se guarda en
  `packing_json["traido_a_mano"]["factusol"] = {codigo, mensaje, referencia}`
  para enseñarlo en la ficha. **En ningún caso** se crea o cambia nada en
  FACTUSOL al traer.
- Lo que escribe en FACTUSOL sigue siendo solo lo que ya escribía: emitir la
  factura (botón), y la cancelación (abajo).

## Cancelaciones

- **Cancelado en Woo y nunca traído:** no sale en la lista; pedido por número,
  se dice que está cancelado en la web (409 `cancelado_en_la_web`).
- **Cancelado en Woo después de traerlo:** el webhook de siempre lo empareja y
  `_refresh_existing` llama a `autocancel_web_order`: `cancelled_at` con el
  motivo automático, auditoría `erp.order_cancelled` y el trabajo
  `autocancel_order_documents_job` (`factusol:writes`), que borra el F_PCL de
  FesteWeb si no tiene factura, ni albarán, y sigue pendiente (ESTPCL). Es el
  camino que ya existía; ahora también lo recorren los traídos. Un pedido **ya
  facturado** no se anula solo (regla de siempre): Bart decide.
- **Desde BoHub:** «Anular pedido» como cualquier otro (`cancel-preview` /
  `cancel`, con la opción de borrar el pedido de cliente de FACTUSOL si se puede).
  Nunca escribe en la tienda.
- **Lo que no se cancela se queda esperando:** sin aviso de «sigue sin cobrar»
  ni caducidad. (El Cuadre «Pedido esperando aprobación» lo cuenta solo mientras
  nadie lo apruebe, como a cualquier pedido.)
- Anulado (por cualquiera de las vías) sale del Seguimiento y de la hoja de
  Drive como siempre.

## Lo que no se hace

- **Nada escribe en las tiendas.** Ni cambia su estado, ni añade notas.
- **Nada se crea en FACTUSOL al traer**, ni se guarda un enlace con el F_PCL.
- **Las líneas no se refrescan** al llegar el cobro; solo se anotan las
  diferencias.
- **No hay estado «sin pagar»**, ni recordatorios, ni caducidad.
- **No se traen** `pending` ni `failed` (carritos), ni `refunded` / `trash`.
- **«Pagados que no llegan» solo refresca** los traídos que la tienda tiene
  `processing` / `completed`; un `refunded` sin webhook no se recoge por ahí.
- **«Poner al día estados Woo…» (`reconcile.py`) no mira los traídos que siguen
  `on-hold`**: los da por «ocultos por estado» con la regla solo-de-estado
  (`visibility_for_status`), así que un traído cancelado en la tienda **sin**
  webhook no se detecta por esa vía (el camino es el webhook, o anularlo a
  mano). Sus contadores también cuentan como «sale» a un traído que la tienda
  devuelve a `on-hold`, aunque siga a la vista.
- Si FesteWeb no crea el F_PCL mientras el pedido está `on-hold` (no está
  comprobado), el veredicto será `no_encontrado` y la factura no se podrá emitir
  hasta que exista («aún no está en FACTUSOL»).

## Datos

- `orders.manual_web_import_at` (DateTime tz) y
  `orders.manual_web_import_by_user_id` (FK `users`, `ON DELETE SET NULL`):
  migración `20261028_0150_pedido_web_traido_a_mano.py`, aditiva, sin backfill.
- `orders.packing_json["traido_a_mano"] = {"factusol": {codigo, mensaje,
  referencia} | null, "diferencias": [str] | null}`.
- Auditoría: `erp.pedido_web_traido`.

## Código

| Pieza | Dónde |
|---|---|
| Lista, traer, veredicto FACTUSOL, bloque de la ficha | `backend/app/erp/pedidos_web.py`, `backend/app/erp/api/pedidos_web.py` |
| Autorización, cerrojo por tienda, diferencias | `backend/app/integrations/woocommerce/mapper.py` (`TraidoAMano`, `import_woo_order`, `_refresh_existing`) |
| Puerta de preparación | `backend/app/erp/sample_orders.py` (`payment_not_required`), `backend/app/erp/state_machine/engine.py` |
| Visibilidad (Seguimiento, Drive, Cuadre) | `backend/app/erp/seguimiento.py` (`woo_status_visibility`), `backend/app/erp/woo_status.py` (`WEB_UNPAID_STATUSES`), `backend/app/erp/cuadre/checks_mysql.py` |
| Limpiezas | `backend/app/integrations/woocommerce/purge.py`, `cleanup.py` |
| Cobro sin webhook | `backend/app/integrations/woocommerce/missing.py` (`_traidos_sin_cobro`, `_refrescar_traidos`) |

## Tests

- `backend/tests/test_erp_pedido_traido_a_mano_normal.py`: aprobar, preparar,
  embalar y etiqueta sin esperar el cobro (y el mismo pedido sin la marca
  parado en la puerta de pago); emitir la factura con el pago pendiente;
  visible en Seguimiento y en la hoja de Drive estando `on-hold` (anulado,
  cancelado o fallido, oculto); el Cuadre lo cuenta; `purge` / `cleanup` no lo
  tocan; «pagados que faltan» lo refresca (pago → pagado, un solo pedido) y
  sigue saltándose los normales.
- Los del bloque de traer (lista, traer, estados, duplicados, veredicto de
  FACTUSOL, webhook posterior) van con `backend/app/erp/pedidos_web.py`.

El figurante de todos es el pedido 5873 de fluxlasers.es (Escola de Ponent,
44,77 €, tres líneas —una sin SKU— más portes, CIF en `meta_data`,
`customer_id` 0, provincia «T»), con los datos personales sustituidos.
