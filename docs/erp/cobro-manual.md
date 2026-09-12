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
   CODFAC), así que la SERIE hay que resolverla: el historial de la Fase 2
   guarda `factusol_serie`; el pedido web se localiza por REFFAC = referencia
   común; y si el CODFAC es único en F_FAC, es esa. Ahora se persiste en
   `orders.factusol_invoice_serie`. Con homónimos en varias series y sin pista
   NO se adivina (`unresolved`).
   Estado de cobro = `collection_status` (F3-fix1): `TOTFAC`, cobros en F_LCO
   por (TFALCO, CFALCO), saldo, `ESTFAC` (2 = cobrada, 1 = parcial, 0 =
   pendiente); `ya_cobrada = saldo ≈ 0 or ESTFAC == "2"`.
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
  (`mark_orders_after_collection`), aunque el modal se cierre antes; la
  Fase 2 (opción B) hace lo mismo al registrar su cobro.
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
