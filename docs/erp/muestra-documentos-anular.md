# ERP · Muestras con documento FACTUSOL, anular con factura y desvincular (30/09/2026)

Caso real: `MUESTRA-000003` (muestra · 0,00 €) con la factura `2-526110` de
PREMO B.V. (5.059,00 €) vinculada desde ERP · Documentos («Vincular»), que solo
apuntaba el número. La ficha quedaba incoherente (sin cliente, 0 €, pasos «no
aplica · no facturable»… y «Pendiente de cobro 5.059,00 €») y no se podía
anular (un pedido no web con factura estaba bloqueado).

Nada de esto escribe en FACTUSOL. Código: `backend/app/erp/order_documents.py`.

## Documentos vinculados a un pedido

| Clase | Campos |
|---|---|
| factura | `invoice_status` + `factusol_invoice_number` + `factusol_invoice_serie` (regla única de #493) |
| albarán | `factusol_albaran_number` + `packing_json.factusol_albaran` |
| origen (proforma / pedido de cliente) | `external_source` `factusol_proforma`/`factusol_pedido` + `external_id` + `packing_json.factusol_source` |

`linked_documents(order)` los lista; el detalle del pedido los expone
(`linked_documents`, `sample_pending_link`).

## A. Vincular un documento a una muestra

- `GET /api/erp/orders/{id}/link-document/preview?doc_type&serie&codigo` y
  `POST /api/erp/orders/{id}/link-document` (`{doc_type: albaranes|presupuestos|facturas, serie, codigo, confirm, company_id?}`).
- Solo muestras puras (`order_kind='sample'`); 409 `not_a_sample` si ya está
  convertida. 409 `document_linked_elsewhere` si otro pedido vivo lo tiene.
  409 `factusol_customer_unlinked` (`codcli`, `cliente_nombre`) si el cliente
  no tiene empresa CRM → la ventana abre crear / vincular empresa
  (`customers/create-crm-and-link` / `customers/link`) y reintenta.
- Carga con el mismo lector que «pedido desde FACTUSOL»
  (`preview_factusol_document` + `add_document_lines`): empresa, líneas,
  `total_amount` (total del documento), `factusol_manual_serie`,
  `factusol_source` (forma de pago), el vínculo del tipo y
  `external_source/external_id` como un pedido creado desde ese documento.
- `order_kind = 'sample_converted'`: deja de ser muestra para todo lo fiscal
  (`is_sample_order` = solo `sample`), conserva el nº y `born_as_sample` (badge
  en ficha y bandeja; el filtro «Muestra» lo incluye). Se guarda una foto de la
  muestra original en `packing_json.sample_original`.
- Re-vincular el MISMO documento a una muestra que ya lo apuntaba sin datos =
  «Reprocesar vínculo» (caso MUESTRA-000003).
- ERP · Documentos `link-order` rechaza muestras puras (409 `order_is_sample`):
  se vincula desde la ficha.

## B. Anular con factura

- `cancel_blockers` ya no bloquea por factura. Al anular se DESVINCULAN la
  factura, el albarán y el origen (`unlink_for_cancel`); siguen en FACTUSOL.
  Historial: «Anulado; factura 2-526110 desvinculada (sigue en FACTUSOL)».
- `cancel-preview` → `documents_to_unlink` (con el aviso de cada uno). El
  borrado opcional en FACTUSOL solo se ofrece SIN factura; los documentos que
  se van a borrar se dejan vinculados para el job, que al terminar desvincula
  lo que quede.
- «Restaurar» (`uncancel`) re-vincula lo desvinculado al anular
  (`packing_json.cancel_unlinked`) salvo lo que ya tenga otro pedido o se haya
  borrado en FACTUSOL.
- La auto-anulación de pedidos web (reembolso/cancelación en Woo) no cambia.

## C. Desvincular sin anular

- `POST /api/erp/orders/{id}/unlink-document` (`{kind: factura|albaran|origen, confirm}`).
- Pedido normal → queda «sin factura» (`not_invoiced`, cobro cacheado limpio),
  facturable de nuevo. Muestra convertida sin más documentos → vuelve a modo
  muestra (líneas originales, 0 €, sin empresa ni serie, origen `manual`).
- Rastro en el historial y en la auditoría (`erp.order_document_unlinked`).

Tests: `backend/tests/test_erp_muestra_documentos.py`, `test_erp_order_cancel.py`;
frontend `VincularDocumentoModal.test.tsx`, `DesvincularDocumentoModal.test.tsx`,
`CancelOrderModal.test.tsx`, `orders/[id]/muestra-documento.test.tsx`,
`orders/bandeja-flujo.test.tsx`.
