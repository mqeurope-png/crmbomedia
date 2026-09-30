# ERP · «Registrar cobro en FACTUSOL» (manual) desde la ficha y la bandeja

Disparador manual del motor de cobros **F-4-B**, que ya existía y NO se
reimplementa: `register_invoice_collection` (solo `F_LCO` + `ESTFAC=2`,
idempotente) a través de `POST /api/erp/factusol/documents/facturas/{serie}/{codigo}/collection`
(`{confirm, cuenta, fecha, forma, observaciones, importe?}`, cola
`factusol:writes`, log exacto del registro en el worker). Lo nuevo es UI +
resolver la factura del pedido + el estado de cobro persistido para la bandeja.

## Parte 1 — lo que ya había (respuestas)

1. **Enlace pedido → factura.** El pedido guarda el CODFAC desnudo en
   `orders.factusol_invoice_number` (emisión E2, auto-vínculo por REFFAC,
   `attach_invoice` de la Fase 2). La clave de F_FAC es COMPUESTA (TIPFAC,
   CODFAC), así que hace falta la SERIE, que se guarda en
   `orders.factusol_invoice_serie` al vincular (emisión, auto-vínculo, Fase 2,
   pedido creado desde factura). **Rev. 30/09/2026 (factura vinculada):** la
   factura del pedido es UNA sola definición para todo BoHub
   (`app/erp/linked_invoice.py`): estado facturado + nº + serie guardados, sin
   depender del origen del pedido ni de `factusol_manual_serie`. Sin la serie
   NO se deduce (ni por el historial, ni por REFFAC, ni porque el CODFAC sea
   único en F_FAC): `unresolved` con `reason = "sin_serie"` y sin consultar
   FACTUSOL. `/orders/{id}/factusol-invoice-ref` (PDF y «Enviar factura» de la
   ficha y de Seguimiento) sale del mismo vínculo, sin buscar por REFFAC (antes
   solo encontraba las de los pedidos web).
   Estado de cobro = `collection_status` (F3-fix1): `TOTFAC`, cobros en F_LCO
   por (TFALCO, CFALCO), saldo, `ESTFAC` (2 = cobrada, 1 = parcial, 0 =
   pendiente). **Rev. 30/09/2026:** `ya_cobrada = saldo ≈ 0` (las líneas de
   F_LCO mandan; un `ESTFAC=2` sin líneas que lleguen al total cuenta como
   pendiente — ver «Anular / corregir cobro»). Salvo si F_LCO viene ENTERA
   vacía (lectura rota / ejercicio recién abierto): entonces no se puede
   comprobar y se respeta `ESTFAC=2` (ni se da por pendiente ni se re-cobra).
2. **Catálogo de cuentas.** `app/erp/contrapartidas.py` (las 14 de Bart,
   editables en `/erp/settings`, endpoint `GET /api/erp/catalogs/contrapartidas`):
   6 Bomedia Sabadell · 8 Streamtec Sabadell · 2 MQ Europe Belfius · 14 Paypal
   Streamtec · 12 Paypal MQ Europe · 11 Paypal Bomedia · 1 Bomedia Open Bank ·
   7 Streamtec Open Banc · 9 Caja Bomedia · 10 Cash · 13 Abono · 3/4/5 Lambert.
   El endpoint F-4-B resuelve código o nombre con `resolve_contrapartida_code`
   (400 `unknown_account` si no casa) — igual que el paso de pago de la Fase 2
   (`resolve_payment`). El modal usa el mismo catálogo y sugiere por defecto
   (`suggest_contrapartida`): PayPal → la contrapartida PayPal de la tienda;
   si no, la cuenta bancaria de la empresa emisora de la serie (5 → Streamtec
   Sabadell, 1 → Bomedia Sabadell, 2 → MQ Europe Belfius).
3. **Pendiente vs cobrada en la ficha.** No lo sabía: `factusol-status` solo
   dice si hay factura; el estado de cobro vivía en el explorador de
   documentos (F3). Ahora `GET /api/erp/orders/{id}/factusol-cobro` lo
   consulta EN VIVO y lo persiste (`factusol_cobro_status`).
4. **TOTAL de la bandeja.** `orders.total_amount`. Los pedidos web traen el
   total de WooCommerce (con IVA); los creados desde FACTUSOL (Fase 1) y los
   manuales guardaban la SUMA DE LÍNEAS sin impuestos (`build_order` /
   `create_order` en `api/orders.py`): la base imponible.

## Parte 2 — construido

- **Modal compartido** `RegistrarCobroModal` (ficha + bandeja): al abrir
  llama a `GET /orders/{id}/factusol-cobro` → factura (serie-número, cliente,
  total, cobrado, saldo, ESTFAC, nº de líneas de cobro), cuenta sugerida,
  forma de pago (FOPFAC → F_FPA; si no, la del documento de origen), fecha =
  hoy, observaciones; resumen de lo que se escribe («1 línea en F_LCO … y
  ESTFAC=2») + confirmación explícita. Registra con el endpoint F-4-B, hace
  polling de `collection-status/{job_id}` y **re-comprueba** la factura
  (saldo ≈ 0) antes de dar el éxito.
- **Guardas.** Ya cobrada → estado «Cobrado», sin botón (y el endpoint
  responde `already` sin encolar). Líneas de cobro previas sin llegar al total
  (anticipo / posible doble cobro) o ESTFAC=1 → AVISO en el modal, pero se
  permite registrar el saldo (nunca se sobrepaga: `importe` = saldo). Sin
  factura → botón deshabilitado con tooltip «emite la factura primero», nunca
  error rojo. Solo con permiso de edición ERP (`require_erp_edit` en el POST).
- **Ficha**: fila «Cobro FACTUSOL» junto a FACTURACIÓN con el badge + botón.
  Estado en vivo best-effort al cargar (con factura); el persistido llega en
  el pedido.
- **Bandeja**: badge por fila en la columna FACTURACIÓN (`Cobrado FACTUSOL` /
  `Pendiente de cobro FACTUSOL` / `Cobro FACTUSOL sin comprobar`; nada sin
  factura) — estado CONTABLE, separado del «Pagado» de PAGO (CRM). Filtro
  «Cobro FACTUSOL» (`?cobro=cobrada|pendiente|sin_comprobar`). Botón
  «Registrar cobro» por fila → mismo modal; al terminar se repinta solo esa
  fila. «Actualizar cobros FACTUSOL» (`POST /orders/factusol-cobros/refresh`,
  solo lectura: F_FAC y F_LCO se leen UNA vez) refresca todas las filas con
  factura. El job de cobro también deja el pedido «cobrada»
  (`mark_orders_after_collection`), aunque el modal se cierre antes.
- **El cobro es SIEMPRE manual** (decisión de Bart, 2026-09-14): emitir la
  factura —desde la ficha o al facturar el albarán / presupuesto desde el
  explorador— ya no registra el cobro apuntado al convertir (Fase 2, opción
  B). «Registrar cobro» (ficha, bandeja, Documentos) es la única vía; el
  `workflow` del pedido lo ofrece como siguiente paso y nunca lo da por hecho.
  La única excepción que sigue viva es el auto-marcado ERP-F3 (`ESTFAC=2` sin
  `F_LCO`) de la factura de un pedido WEB ya pagado al comprar: opt-in por
  ajuste, desactivado por defecto, y solo en la emisión por F_PCL (nunca en
  la de un pedido con albarán de BoHub).
- **Persistencia**: `orders.factusol_invoice_serie`, `factusol_cobro_status`
  (indexado), `factusol_cobro_checked_at` + detalle en
  `packing_json.factusol_cobro` (migración `20260915_0105`).

## FIX aparte — TOTAL = importe final con IVA

`total_amount` pasa a ser el importe FINAL: en los pedidos creados desde
FACTUSOL, el total del documento de origen (`TOTPRE` / `TOTPCL`, con IVA; los
exentos quedan igual porque base = total); en los manuales, Σ `line_total ×
(1 + tax_rate/100)`. Migración `20260915_0106` recalcula los existentes (no
web): primero `packing_json.factusol_source.total`, si no las líneas con su
IVA. Corrige a la vez la cabecera de la ficha, la Cola SAT y la limpieza Woo,
que leen el mismo campo.

Tests: `backend/tests/test_erp_cobro_manual.py`,
`frontend/.../RegistrarCobroModal.test.tsx`, `orders/[id]/cobro.test.tsx`,
`orders/cobro.test.tsx`.

## Contrapartida sugerida por tienda × método de pago (rev. 30/09/2026)

- El pedido web guarda el método de pago de WooCommerce: `orders.payment_method`
  (id del gateway, p. ej. `mollie_wc_gateway_creditcard`) y
  `orders.payment_method_title` («Carte», «PayPal»…), migración `20260930_0121`.
  Se rellenan al importar / actualizar (webhook) y, para los antiguos, en la
  puesta al día de estados Woo (`to_payment_method`). **Rev. 30/09 (fix):** ver
  «Clave de tienda y relleno del método de pago» abajo.
- Reglas `contrapartida_rules` en `factusol_series_json`
  (`[{tienda, metodo, coincidencia: exacta|contiene, contrapartida}]`, en orden,
  primera que casa; tienda «» = todas). Casan contra el título, el gateway y la
  forma de pago de FACTUSOL, sin mayúsculas. Si nunca se guardaron, son las
  iniciales: PayPal por tienda (`paypal_contrapartidas_by_store`, migrado) +
  `artisjet-europe` «Carte» / `mollie_wc_gateway_creditcard` → 15. Una regla con una
  contrapartida fuera del catálogo se salta. Sin regla → PayPal de la empresa
  emisora (si el método es PayPal) → cuenta bancaria de la serie.
- Se aplica en `GET /orders/{id}/factusol-cobro` (modal de la ficha y de «Por
  cobrar», con `suggested_reason`), en `GET /documents/facturas/{s}/{c}/cobro`
  (pedido vinculado vía `find_order_for_invoice`) y en
  `POST …/collection` cuando no llega `cuenta` (lote CSV: `contrapartida_sugerida_por`;
  sin sugerencia → 400 `missing_account`). Lo apuntado a mano en el pedido
  (pago al convertir) sigue mandando. Ningún cobro se registra solo: el pago
  al convertir solo apunta la intención.
- Conciliación bancaria sin cambios: solo propone contra facturas con saldo en
  F_LCO, así que una factura cobrada por la 15 no se vuelve a casar contra
  Belfius; y F-4-B responde `already` si se intenta.


## Clave de tienda y relleno del método de pago (fix de #495, 30/09/2026)

**Bug 1 — la clave de artisJet no era la misma en todas partes.** La cuenta
Woo es `artisjet-europe` (`integration_accounts.account_id`, la que ya usaba
`by_source`), pero las reglas de contrapartida, la PayPal por tienda y los
remitentes la guardaban como `artisjet`: la regla «Carte» → 15 (y la PayPal →
12) no casaban nunca y ARTISJ-9638 sugería «2 · MQ Europe Belfius · cuenta de
la serie 2»; `store_email_from` de artisJet tampoco se aplicaba.

- **Una sola clave**: el `account_id` de la cuenta Woo (`app/erp/woo_stores.py`).
  La tienda del pedido sale de `orders.store_id → integration_accounts.account_id`
  (sugerencia de contrapartida, remitente de factura y del aviso de envío). Sin
  lista fija ni alias (`_STORE_ALIASES` / `STORES` / `STORE_BY_PREFIX`
  eliminados): el pedido sin cuenta se resuelve por el prefijo del nº de pedido
  de las cuentas reales (`account_id.upper()[:6]`). El motivo de la sugerencia
  usa el `display_name` («tienda Artisjet Europe · método Carte»).
- Defaults con la clave real (`artisjet-europe`) y `GET
  /api/erp/catalogs/contrapartidas` → `stores` = las cuentas Woo reales; el
  desplegable «Tienda» de las reglas de Ajustes usa `woocommerce_stores` (como
  remitentes, prefijo y serie). El PATCH rechaza (400) una regla cuya tienda no
  es ninguna cuenta Woo.
- **Migración `20260930_0122`** (se aplica sola al arrancar `api`): en
  `factusol_series_json` renombra `artisjet` → `artisjet-europe` (y `flux` →
  `fluxlasers`) en `contrapartida_rules[].tienda`,
  `paypal_contrapartidas_by_store`, `store_email_from`, `ref_prefix_by_store` y
  `by_source`. Solo si la clave vieja no es una cuenta Woo real y la nueva sí;
  si la nueva ya existe, se fusiona sin pisarla; idempotente.

**Bug 2 — la puesta al día no rellenaba el método de pago.** La puesta al día
(«Poner al día estados Woo…», job en `erp:interactive` → `worker-factusol`; el
`seguimiento:reconcile` de `worker-sync` es otro job, el de la hoja de Drive)
sí tenía un relleno, pero listaba la tienda entera (`status=any`) desde el pedido
sin método más antiguo con tope de páginas, iba al final de la pasada y la
pantalla no lo enseñaba: con 0 cambios de estado el botón decía «Aplicar (0
cambios)».

- `app/integrations/woocommerce/payment_methods.py`: pide a cada tienda los
  pedidos POR ID (`GET /orders?include=…&status=any`, 100 por llamada, los más
  recientes primero, tope 2.000 por tienda y pasada). Todos los pedidos web sin
  método, en curso o no; solo rellena lo VACÍO (lo guardado no se pisa).
- En la puesta al día es el PRIMER paso y, al aplicar, se confirma ya (no
  depende de que el resto de la pasada termine). El resumen trae
  `to_payment_method`, `payment_method_by_store`, `payment_method_pending`; la
  pantalla lo enseña y el botón lo cuenta.
- **Relleno inicial al desplegar**: al arrancar `api` se encola una vez
  (`run_backfill_job`, cola interactiva; SETNX en Redis) y deja en el log
  `woo.payment_method backfill: N pedidos rellenados (artisjet-europe X,
  boprint Y, fluxlasers Z)`. Marca `payment_method_backfill.done_at` en
  `factusol_series_json` si no hubo errores (si los hubo, se reintenta en el
  siguiente arranque).

Tests: `test_migration_store_keys.py`, `test_erp_contrapartida_reglas.py`,
`test_erp_reconcile_woo.py`, `test_shipment_email.py`, `test_erp_invoice_email.py`,
`test_erp_f5_catalogs.py`, `frontend/.../settings/page.test.tsx`,
`seguimiento/metodo-pago.test.tsx`.

## Anular / corregir cobro, cobros parciales y descuadre (rev. 30/09/2026)

Deshacer un F-4-B equivocado (caso 5-260108: cobro de 333,96 € del
23/09/2026 por la contrapartida 8 que no correspondía). Zona sensible:
escritura en FACTUSOL, en serie por `worker-factusol` (cola `factusol:writes`).

- **Qué se puede anular.** SOLO los cobros que registró BoHub: los eventos
  `erp.invoice_collection_registered` de la auditoría (`bohub_collections` en
  `app/erp/factusol_cobro.py`, con número, línea `LINLCO`, fecha, importe y
  contrapartida). Un cobro hecho a mano en FACTUSOL (no está en la auditoría,
  o traspasado a tesorería `TRALCO=1` / `F_COB`) no aparece y no se toca.
- **Anular** (`annul_invoice_collection` en `collections_write.py`): relee
  F_LCO y borra EXACTAMENTE `TFALCO + CFALCO + LINLCO` —nunca por número de
  factura— y solo si fecha, importe y `CPALCO` siguen siendo los que registró
  BoHub y la línea no está traspasada. Si no coincide → `mismatch`, no se borra
  nada y se explica qué cambió. Después recalcula ESTFAC con las líneas que
  quedan (0 pendiente / 1 parcial / 2 cobrada) y el saldo vuelve solo (sale de
  F_LCO). El worker deja en el log la línea y el ESTFAC antes y después, sin
  credenciales. Si la línea ya no existe (`line_missing`, p. ej. borrada a mano)
  no se borra nada, pero se corrige ESTFAC según las líneas reales.
- **En BoHub:** evento `erp.invoice_collection_annulled`, el pedido pasa a
  `factusol_cobro_status = pendiente` (o sigue cobrada si otras líneas cubren
  el total) y el historial dice «Cobro de 333,96 € del 23/09/2026
  (contrapartida 8) anulado».
- **Corregir** = anular + registrar el correcto (fecha / cuenta / importe) en
  UN solo job: si la anulación no se hace (mismatch…), no se registra nada; al
  final queda una sola línea, la nueva.
- **Endpoints** (permiso de registrar cobros, `confirm` obligatorio, 202 +
  `job_id`, polling con `collection-status/{job_id}`):
  `POST /api/erp/orders/{id}/factusol-cobros/{event_id}/anular` y
  `…/corregir` (`{confirm, cuenta, fecha, importe?, forma?}`; 400
  `unknown_account` / `invalid_date`). 404 `cobro_not_found` si el cobro no es
  de la factura vinculada al pedido; 409 `cobro_already_annulled`.
  `GET /orders/{id}/factusol-cobro` añade `bohub_cobros` (con `anulable`) y
  `total_mismatch`.
- **UI:** modal `AnularCobroModal` (ficha: botón «Anular / corregir cobro»
  cuando hay un cobro de BoHub anulable; «Por cobrar»: opción del menú de la
  fila). Enseña el cobro, avisa de que se BORRA en FACTUSOL y pide
  confirmación.
- **Importe editable y cobros parciales.** El modal «Registrar cobro» propone
  lo pendiente EN VIVO en FACTUSOL; un importe menor registra un parcial
  (ESTFAC=1) y varios parciales suman (325,49 + 8,47 sobre 333,96 → cobrada).
  Nunca más de lo pendiente: el modal no deja y el endpoint responde 400
  `amount_exceeds_pending` (y el motor tampoco escribe).
- **Estado en los dos sentidos.** «Actualizar cobros» / la consulta en vivo
  dejan el pedido `pendiente` si la suma de F_LCO no llega al total (aunque
  ESTFAC diga 2); «Anular» lo deja pendiente directamente.
- **Descuadre pedido / factura.** Si `orders.total_amount` ≠ TOTFAC, la ficha y
  el modal avisan: «Pedido 325,49 · Factura 333,96 · diferencia 8,47». El
  cobro va siempre por lo pendiente en FACTUSOL.
- **MULLCO / TRALCO.** La plantilla de F_LCO que copia BoHub traía
  `MULLCO=51` (heredado de la línea de ejemplo: cobro múltiple / movimiento de
  tesorería de otra operación) y podía traer `TRALCO=1`. BoHub escribe ahora
  `MULLCO=0` y `TRALCO=0` (cobro simple, sin traspasar). Para comprobarlo en
  producción (solo lectura):
  `docker exec crmbo-api-1 python -m scripts.factusol_discover_invoice_payment --mullco`
  (reparto de F_LCO por MULLCO/TRALCO y si los MULLCO casan con `F_COB.CODCOB`).

Tests: `backend/tests/test_erp_anular_cobro.py`,
`frontend/.../AnularCobroModal.test.tsx`, `RegistrarCobroModal.test.tsx`,
`orders/[id]/cobro.test.tsx`.
