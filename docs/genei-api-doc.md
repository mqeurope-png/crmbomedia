# Genei API v2 — referencia de esquemas (extraído de la doc oficial, rev 2025-11-10)

Base: `https://apiv2.genei.es/api/v2`. Swagger (interactivo, requiere login): `https://apiv2.genei.es/api-doc/v2`.

## Autenticación
- `POST /login` con `{ "username": "<email>", "password": "<password>" }` → **token Bearer**, validez **15 días**.
- Todas las llamadas: cabecera `Authorization: Bearer <token>`.
- Renovar el token al caducar o ante 401 (re-login). Password/token **cifrados**, nunca en logs.
- **Re-autenticación automática (BoHub, rev 2026-09-25).** El token se guarda en una
  **caché del proceso** (`TokenCache`, por credenciales) y se reutiliza entre
  peticiones; se renueva solo antes de caducar (`exp` del JWT − 1 h) y, como
  mucho, cada 12 h. Si Genei rechaza el token — HTTP `401`/`403`/`419`/`440`, o
  su envoltorio HTTP 200 `status:0` con un mensaje de token/sesión/autenticación —
  se hace login con las credenciales guardadas y se **reintenta la llamada una
  vez**. Un error de DATOS («Invalid bultos…», «agencia no factible») no dispara
  re-login. El pago (`/payments/pay/transactions`) mantiene su regla: reintento
  solo ante un `401` HTTP (un `status:0` ahí se refiere al token DE PAGO).
- **Credenciales rechazadas** en el login (HTTP 400/401/403/422 o `status:0`) →
  `GeneiAuthError` («revisa las credenciales de Genei en Ajustes → Envíos») y
  bloqueo de 5 min para esas credenciales (sin bucle ni martilleo). Un 5xx,
  timeout o fallo de red en el login es pasajero y no bloquea. «Guardar» en
  Ajustes y «Probar conexión» (`POST /api/erp/genei/test-connection`, login
  forzado) desbloquean al momento. `GET /api/erp/genei/config` devuelve `auth`
  (`state` ok/error/unknown, hasta cuándo vale el token, último error), nunca el
  token.
- **Secreto del webhook**: guardar credenciales lo **conserva** (antes se perdía
  en cada «Guardar» y se generaba otro, con lo que Genei recibía 401 al avisar
  de los envíos ya creados).

## ⚠️ Verificado EN VIVO (rev 2026-09-24) — cosas que la doc no dejaba claras

**Envoltorio de respuesta.** Genei responde **HTTP 200 SIEMPRE**, tanto en éxito como en error:
- Éxito: `{ "status": 1, "message": "", "data": [ ... ], "errors": [] }` — los datos van en `data`.
- Error: `{ "status": 0, "message": "Error validacion", "data": { "details": { ... } }, "errors": [ "..." ] }`.
El cliente DEBE tratar `status:0` (o `errors` no vacío) como error; si no, un fallo se cuela como lista vacía (fue el bug «0 agencias siempre»).

**`GET /agencies/prices` — formato REAL de `packages`** (esquema del Swagger + verificado):
- Query, nombre `packages[]`, array de objetos; se envía en notación bracket: `packages[0][height]=15&packages[0][width]=15&packages[0][length]=20&packages[0][weight]=1&packages[0][isBox]=false`.
- Cada bulto: `{ height, width, length, weight (números), isBox (bool) }`. **`isBox` es OBLIGATORIO** (sin él → «Invalid bultos array format» y cero agencias). `isBox=false` = bulto normal (el caso de BoHub).
- `isWarehouse` puede ir `true` o `false` (ambos devuelven agencias); Bart usa su propio origen → `false`.
- **Respuesta** (`data[]`): cada agencia trae `id_agencia`, `importe` (precio), `nombre_agencia` / `nombre_completo_agencia`, `nombre_integracion_cliente` (p. ej. «Dom-Dom»), `domicilio_domicilio` (**1 = entrega a domicilio**, 0 = punto/oficina), `servicio` (1=24h, 2=48h), `estrellas`/`valoracion_agencia`, `maximo_*_bulto`, `descripcion_agencia`.

**`POST /shipments` — campos OBLIGATORIOS** (verificado contra Swagger + validación real): `agencyId`, `origin`, `destination`, `packagesArray`, **`paymentMethodShipping`**.
- **`origin`** es un **bloque de dirección COMPLETO** (misma forma que `destination`: `name, contact, dni, email, phone, address, postalCode, town, isoCountry, observations`), NO un `originAddressId`. Todos los campos del ejemplo son obligatorios, **incluido el prefijo internacional del teléfono** (E.164, p. ej. `+34…`). En BoHub se resuelve desde la dirección registrada de la cuenta con `GET /addresses/{originAddressId}` (el remitente por defecto, `predeterminada:1`) y se mapea (`nombre→name`, `direccion→address`, `codigo_postal→postalCode`, `poblacion→town`, `country_code→isoCountry`, `telefono_e164→phone`, `mail→email`, `vat_number→dni`).
- **`paymentMethodShipping`**: entero; **`4` = pago con saldo** (la doc: «Normally 4»). Solo declara la modalidad; el pago en sí lo dispara una persona (PR-2).
- **`shippingFromWarehouse`**: `0` para BoHub (origen propio, no el centro logístico de Genei); `1` solo si se envía desde el almacén de Genei.
- `originAddressId` NO existe en el esquema de creación (Genei lo ignora); por eso el payload anterior fallaba con «"origin" is mandatory».

**Respuesta de `POST /shipments` (creación OK)** — `{ "status":1, "message":"Shipment created", "data": { "reference": "<código>", "transactionId": <num>, "paymentUrl": "...", "paymentUrlRest": "..." } }`. **El código del envío va en `data.reference`** (NO `codigo_envio`/`shipmentCode`), y la respuesta **no trae el estado** (nace en **7, pendiente de pago**). Verificado en vivo: crear devolvió `reference` + `paymentUrl` y `DELETE /shipments/{reference}` lo eliminó sin pagar («El envio se elimino correctamente»). El listado `GET /shipments` pagina en `data.rows[]` + `data.count`; ahí el código es `codigo_envio` y el enlace con el pedido, `codigo_envio_externo`.

## Endpoints principales
- **Precios / agencias factibles:** `GET /agencies/prices` — lista de agencias posibles para (origen, destino, bultos). Se elige un `id_agencia` factible de aquí (ver arriba el formato REAL de `packages`).
  - Query (del encargo): `isWarehouse` (bool, req), `isoCountryOrigin` (req), `isoCountryDestination` (req), `postalCodeOrigin`, `postalCodeDestination`, `townOrigin`, `townDestination`, `packages[]`.
- **Crear envío:** `POST /shipments` — se pasa un `agencyId` factible. Devuelve `shipmentCode` y **`paymentUrl`**. Nace en estado **7 (pendiente de pago)**.
  - Body (del encargo): `agencyId`, **`paymentMethodShipping` (=4, pago con saldo — OBLIGATORIO)**, **`origin`** y `destination` `{ name, contact, dni, email, phone (con prefijo +34…), address, postalCode, town, isoCountry, observations }` (ambos OBLIGATORIOS y completos), `packagesArray`, `shippingFromWarehouse` (=0 en BoHub), `externalShippingCode` (= nº pedido BoHub), `clientReference`, `notificationUrl` (webhook BoHub, PR-2), opcionales `goodsValue`, `insurance`/`insuranceAmount`, `cashOnDelivery`/`cashOnDeliveryAmount`, `note`, `priority`, `pickupDate`/`pickupTimeFrom`/`pickupTimeTo`, `destinationOffice`/`originOffice`, `contentsArray` (aduanas). **Ver arriba** el detalle de `origin`/`paymentMethodShipping` verificado en vivo.
- **Pagar (PR-2, verificado en Swagger):** el pago se ejecuta por API contra el **SALDO** de la cuenta, sin popup, y **SIEMPRE** lo dispara una persona (botón «Pagar y tramitar»); nunca automático.
  - Al crear, se guarda `data.transactionId` (y `paymentUrl`) del envío.
  - Al pagar: `GET /payments/token?pg=4` → **JWT de pago fresco** (el de la creación caduca ~2 h, por eso se pide uno nuevo cada vez) → `GET /payments/pay/transactions/{transactionId}?payment_token=<jwt>` **ejecuta el pago** (RESTful solo para saldo/crédito, pg=4). Un rechazo (sin saldo, ya pagado…) llega como envoltorio `status:0` → `GeneiError` con el mensaje, visible en la UI.
  - Tras pagar, el envío pasa **7 → 6 → 1** (tramitado) y la etiqueta queda disponible; BoHub refresca el estado leyendo `GET /shipments/{code}`.
  - `GET /transactions?search=<shipmentCode>` localiza la transacción de un envío (respaldo si no se guardó el `transactionId`).
- **Eliminar / cancelar:** `DELETE /shipments/{shipmentCode}` — elimina si es prueba; cancela un envío tramitado mientras no haya pasado a tránsito.
- **Etiqueta:** `GET /shipments/{shipmentCode}/label` — PDF o ZPL, base64 o binario.
- **Datos completos** (tracking/estado/detalles): `GET /shipments/{shipmentCode}`.
- **Tracking DETALLADO del transportista** (Swagger oficial v2.0.0): `GET /shipments/{shipmentCode}/tracking` →
  `{ status:1, message:"<URL de seguimiento web de la agencia>", data:{ estadosAgencia:[{fecha, codigo_estado, descripcion}], estadosInternos:[{fecha, estado_anterior, id_estado_anterior, estado_actual, id_estado_actual}], reintentosRetramitacionAutomaticos:{} } }`.
  `estadosAgencia` son los **eventos del propio transportista** (CTT, UPS, GLS…) tal cual («Pendiente de entrada en red», «En reparto», «Entregado»…) y pueden venir **desordenados** (se ordenan por `fecha`). `estadosInternos` = los estados de Genei (7→6→1→5→80→3).
  Variantes: `/tracking-full` (lo mismo + `tipo_envio` y datos de retramitación), `/tracking/url` (`data.webSeguimiento`, `webSeguimientoBultos[]`), `/state` (solo `data.state` numérico).
  ⚠️ El ejemplo del Swagger usa códigos/descripciones de relleno («AAA0000X», «Ejemplo: …»): el texto real depende de cada agencia; BoHub lo clasifica por palabras clave (`tracking.classify_carrier_text`) y enseña el literal.
- Direcciones habituales: `GET/POST /addresses`, `GET/PATCH/DELETE /addresses/{addressId}`. [dirección origen por defecto de la cuenta Genei]

## packagesArray (bultos)
Origen ≠ centro logístico de Genei (caso BoHub) → NO `box` ni `references`:
```json
"packagesArray": [ { "weight": 0.5, "height": 2, "width": 10, "length": 10 } ]
```
(Solo si el origen es el almacén de Genei se usan `box: { idBox }` y `references: [{ idReference, quantity }]`. Para BoHub NO aplica.)

## Enlace con el pedido y webhook (PR-2)
- Al crear: `externalShippingCode` = nº pedido BoHub (aparece como `codigo_envio_externo`); `notificationUrl` = webhook BoHub = `<base pública>/api/webhooks/genei?token=<secreto>`. La base va en la config del carrier; el **secreto va cifrado con las credenciales** (se genera al activar el webhook) y **nunca** en `config_json` en claro.
- **Endpoint:** `POST /api/webhooks/genei?token=<secreto>`. Valida el token (comparación en tiempo constante) contra el secreto guardado; sin token válido → **401** (no se actúa por payloads no verificados). Localiza el pedido por `codigo_envio_externo` (nº de pedido) o por `codigo_envio` (guardado en `packing_json.genei`). **Idempotente**: el mismo estado dos veces no descuadra (un arco ya recorrido no vuelve a aplicarse).
- **Mapeo estado Genei → bucket** (lo que se ENSEÑA; qué mueve el transporte, ver «Qué mueve el pedido SOLO» abajo): `5`/`80`/`85`/`13`/`86` → bucket **in_transit** (`2` «pendiente de depositar en oficina» ya NO: el transportista aún no lo tiene → bucket `ready`, como `1`); `3` → **delivered**; `9`/`10`/`14`/`15`/`78` → **incident** (incidencia de TRANSPORTE, distinta de una de pedido/taller; el texto va a `desc_incidencia`). `7`/`6`/`1`/`77`/`79` no mueven el transporte.
- Genei hace `POST` a esa URL en cada cambio de estado: `{ "status": 1, "message": "Shipment processed", "data": { ... } }`.
- **Granularidad del webhook**: el Swagger lo define como «push notification when shipment is processed successfully… with the same object as `GET /shipments/{shipmentCode}`» → trae **solo el estado de Genei**, NO los eventos del transportista. Por eso BoHub, al recibirlo, **lee además `/tracking`** (si falla, sigue con el estado de Genei) y hay un **sondeo periódico** para el detalle.
- **Estado real del envío (rev 2026-09-25)**: se guarda en `packing_json.genei` el último escaneo del transportista (`carrier_status` literal, `carrier_status_at`, `carrier_step` normalizado: `pre_transit` / `picked_up` / `in_transit` / `out_for_delivery` / `available_pickup` / `delivered` / `incident` / `unknown`), el historial (`carrier_events`, máx. 40) y `tracking_url`. **Qué mueve el pedido SOLO (`tracking.transport_target`, rev 2026-09-25, decisión de Bart):** las pestañas de la Cola SAT solo cambian solas con una **incidencia** (Genei `9`/`10`/`14`/`15`/`78` → `incident` → «Incidencias»). El paso de «Pendiente de recogida» a «Enviados» lo hace una **persona con «📤 Marcar recogido»**; Genei `5`/`80`/`85`/`13`/`86` (recogido, reparto…) y los escaneos del transportista **solo se guardan y se enseñan**. Excepción sin cambio de pestaña: Genei `3` (entregado) pasa a `delivered` si el pedido ya estaba `in_transit` (ya marcado recogido). El escaneo del transportista es solo INFORMATIVO.
- **Sondeo** (`tracking_job.py`): job RQ self-rescheduling en `genei:shipments` (ya la escucha `worker-sync`); revisa los envíos vivos (tramitados, no entregados/cerrados, < 60 días) cuya última consulta supera `tracking_poll_minutes` (30, mín. 10), 40 por pasada, commit por envío, se corta si Genei rechaza las credenciales. Interruptor `tracking_poll_enabled` (encendido por defecto; solo LEE de Genei) en Ajustes → Envíos.
- Campos clave de `data`: `codigo_envio` (=shipmentCode), `codigo_envio_externo` (=nº pedido), `codigo_seguimiento` (tracking), `estado` (num), `nombre_estado`, `nombre_agencia` (courier), `importe`/`importe_total`/`importe_sin_iva`/`valor_impuesto`, `fecha_recogida`, direcciones (`nombre_llegada`, `dir_llegada`, `cp_llegada`, `pob_llegada`, `prov_llegada`, `pais_llegada`, `tel_llegada`, `email_llegada`), `desc_incidencia`, `historico_estados[]` (`{fecha,id_estado,codigo_estado,descripcion}`), `etiqueta` (PDF base64 en la creación; null después), `datos_adicionales[]` (incluye `url_notificacion_api`).

## Códigos de estado
7 Recogida pdte pago → recién creado (pdte pagar)
6 Pendiente de tramitar → pagado, tramitándose
1 Tramitado → listo (etiqueta disponible)
5 Recogida efectuada / en tránsito → recogido/enviado
80 En reparto → tránsito
85 Disponible en oficina → tránsito
2 Pdte depositar en oficina de recogida → tramitado, sin recoger (bucket `ready`; antes «tránsito», incorrecto)
13 Concertado próximo reparto → tránsito
3 Entregado → entregado
9 Recogida fallida → incidencia
10 En tránsito con incidencia → incidencia
14 Devuelto → incidencia
15 Gestionando siniestro → incidencia
78 Incidencia gestionada → incidencia (resuelta)
8 Rectificativo → —
11 Pendiente de abono → —
12 Abonado → —
77 Cerrado → cerrado
79 Destruido/abandonado → cerrado
86 En el centro logístico → tránsito

### Ciclo de vida
7 (pdte pago) → pagar → 6 (pdte tramitar) → llamada a la agencia → 1 (tramitado; reintentos si falla) → recogido 5 (tránsito) → 80 (reparto) → 3 (entregado). Webhook por cada salto; idempotente.

## Seguridad
- Pago solo por persona (botón «Pagar y tramitar»); nunca auto-pagar.
- Webhook validado (secreto/token); no actuar por payloads no verificados.
- Credenciales/token cifrados; 401 → re-login; agencia no factible y timeouts con mensajes claros sin romper la Cola SAT.

## PR-1 scope (offline, este)
GeneiClient (login+JWT 15d, re-login en 401, agencies/prices, POST shipments, GET label, GET shipments/{code}, DELETE) con MockTransport+tests · Carrier «Genei» credenciales cifradas (Carrier.api_credentials_encrypted, ExternalSystem.GENEI, app/core/crypto.py) · Config: couriers preferidos por país, medidas/peso bulto por defecto, origen (default_address_id) · prices/comparador (preferido factible más barato del país destino, door-to-door por defecto) + crear envío (externalShippingCode=nº pedido, notificationUrl=webhook) + etiqueta auto + tracking manual («Actualizar estado»). SIN pago automático y SIN webhook (PR-2).
