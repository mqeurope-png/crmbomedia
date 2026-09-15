import { redirect } from "next/navigation";

/** ERP · Lote 2 D — la Cola PEDIDOS ya no es una pantalla aparte: es la cola
 *  «Por revisar» de la bandeja, donde se aprueba en el sitio (uno a uno o
 *  «Aprobar seleccionados»). La ruta se conserva para los enlaces guardados
 *  (marcadores, correos, la ficha) y manda a la bandeja ya filtrada. */
export default function PendingApprovalRedirect(): never {
  redirect("/erp/orders?queue=por_revisar");
}
