import { ApiError, apiDownloadBlob, apiDownloadConCabeceras, apiFetch, apiUpload } from "./api";
import { downloadName } from "./downloadName";
import type { CustomerEmailStatus, GeneiLabelAuto } from "./geneiApi";
import type { LinkedInvoice } from "./linkedInvoice";
import type { TraidoAMano } from "./pedidosWebApi";

/** BoHub ERP Fase A — cliente de la API de pedidos (PR 3 backend). */

export type PaymentStatus =
  | "pending" | "paid" | "partial_paid" | "credit_approved" | "failed" | "refunded";
export type PreparationStatus =
  | "pending_review" | "in_queue" | "preparing" | "packed" | "blocked"
  | "already_completed_externally";
export type TransportStatus =
  | "not_shipped" | "label_created" | "in_transit" | "delivered" | "incident" | "returned"
  | "already_shipped_externally";
export type InvoiceStatus =
  | "not_invoiced" | "pending" | "generated" | "error" | "credit_note"
  | "already_invoiced_externally" | "invoiced_by_erp";
export type StatusDomain = "payment" | "preparation" | "transport" | "invoice";

/** Rediseño de flujo (Fase 1) — el bloque `workflow` que calcula el backend
 *  (`app/erp/workflow.py`) y consumen IGUAL la bandeja y la ficha: en qué cola
 *  cae el pedido, qué acción toca y qué alertas tiene. El frontend no vuelve a
 *  deducir el estado de flujo por su cuenta. */
export type WorkflowQueue =
  | "por_revisar" | "por_facturar" | "factura_por_enviar" | "por_cobrar"
  | "incidencias" | "listo";

export type WorkflowAction =
  | "aprobar" | "emitir_factura" | "registrar_cobro" | "crear_envio"
  | "enviar_sat" | "marcar_completado" | "vincular_empresa" | "vincular_cliente"
  | "revisar_incidencia" | "crear_albaran" | "revalidar_vies" | "enviar_factura"
  | "mandar_sat" | "ninguna";

export type WorkflowAlert = {
  code: string;
  text: string;
  action: WorkflowAction | null;
  action_label: string | null;
  /** Bloqueante: manda el pedido a la cola «Incidencias». */
  blocking: boolean;
  /** Aviso de la app: manda el pedido a «Por revisar». */
  review?: boolean;
  /** `datos_cliente_incompletos`: qué le falta al pedido web («empresa»,
   *  «nif», «direccion»), para decirlo en la lista. */
  faltan?: FaltaCliente[];
};

export type FaltaCliente = "empresa" | "nif" | "direccion";

/** Lo que se dice en la bandeja de cada cosa que falta. */
export const FALTA_CLIENTE_LABEL: Record<FaltaCliente, string> = {
  empresa: "sin empresa",
  nif: "sin NIF",
  direccion: "sin dirección",
};

/** Una propuesta de empresa para un pedido web que entró sin cliente. Con
 *  `company_id`, una ficha del CRM; sin él, un cliente de FACTUSOL (`codcli`)
 *  cuya ficha se crea al vincular. */
export type PropuestaEmpresaPedido = {
  company_id: string | null;
  codcli: string | null;
  nombre: string;
  nif: string | null;
  motivo: string;
  origen: "pedido_factusol" | "nif_factusol" | "nif";
  sin_nif?: boolean;
};

export type ClienteWebPedido = {
  faltan: FaltaCliente[];
  faltan_texto: string[];
  nif: string | null;
  nombre: string | null;
  email: string | null;
  propuestas: PropuestaEmpresaPedido[];
  factusol: { disponible: boolean; referencia: string; pedido_encontrado: boolean; pedido?: string };
  /** Por qué no hay propuestas (no hay con qué emparejar). */
  mensaje: string | null;
};

/** Qué le falta a un pedido web y con qué empresa se propone vincularlo
 *  (pedido de cliente de FACTUSOL o NIF de la web). Solo lee. */
export async function getClienteWebPedido(orderId: string): Promise<ClienteWebPedido> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/cliente-web`);
}

/** Vincula la empresa del pedido (una del CRM o la del cliente de FACTUSOL).
 *  Queda en la auditoría del pedido. No escribe en FACTUSOL. */
export async function vincularEmpresaPedido(
  orderId: string,
  body: { company_id?: string; codcli?: string; origen?: string },
): Promise<{ id: string; company_id: string; company_name: string; codcli: string | null; creada: boolean }> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/company`, {
    method: "POST", body: JSON.stringify(body),
  });
}

/** Un paso de la «línea de vida» del pedido (7 en la ficha). `skipped` = no
 *  aplica (el albarán de un pedido web lo crea WooCommerce). */
export type WorkflowStep = {
  key: string;
  label: string;
  state: "done" | "now" | "pending" | "skipped";
  detail: string | null;
  /** Hito OPCIONAL de la línea de vida (p. ej. «Factura enviada»): se enseña
   *  pero NO cuenta para el «Paso N de N» ni bloquea el completado. */
  optional?: boolean;
};

/** «Pagado» / «Sin pagar», con importe y forma de pago (31/10/2026: el pago
 *  es un hecho que se enseña, no una etapa de la línea de vida). */
export type WorkflowPago = {
  no_se_cobra: boolean;
  pagado: boolean;
  estado: string | null;
  importe: string;
  forma: string | null;
};

export type WorkflowCompany = {
  id: string;
  name: string;
  country: string | null;
  /** Nº de cliente en FACTUSOL (F_CLI) o null si no está vinculada. */
  factusol_id: string | null;
  regime: string | null;
};

export type OrderWorkflow = {
  queue: WorkflowQueue;
  queue_label: string;
  next_action: WorkflowAction;
  next_action_label: string;
  next_action_hint: string;
  alerts: WorkflowAlert[];
  blocked: boolean;
  steps: WorkflowStep[];
  /** El pago, como distintivo junto a la línea de vida (no es un paso). */
  pago?: WorkflowPago;
  /** Régimen de IVA del cliente: nacional / intracomunitario / exportacion. */
  regime: string | null;
  company: WorkflowCompany | null;
};

export const WORKFLOW_QUEUES: readonly WorkflowQueue[] = [
  "por_revisar", "por_facturar", "factura_por_enviar", "por_cobrar", "incidencias", "listo",
] as const;

export type OrderSummary = {
  id: string;
  order_number: string;
  external_source: string;
  store_id: string | null;
  contact_id: string | null;
  company_id: string | null;
  /** D-2: nombre del cliente para verlo sin abrir el pedido. */
  contact_name: string | null;
  company_name: string | null;
  total_amount: number;
  currency: string;
  payment_status: PaymentStatus;
  preparation_status: PreparationStatus;
  transport_status: TransportStatus;
  invoice_status: InvoiceStatus;
  tracking_number: string | null;
  /** Pedido web: método de pago de WooCommerce — id del gateway
   *  (`mollie_wc_gateway_creditcard`) y título en la tienda («Carte»). */
  payment_method?: string | null;
  payment_method_title?: string | null;
  /** Fase C: nº de factura FACTUSOL (CODFAC) si ya se emitió; null si no. */
  factusol_invoice_number: string | null;
  /** Cobro manual — estado de cobro EN FACTUSOL de la factura del pedido
   *  («cobrada» = ESTFAC=2 / saldo 0; «pendiente» = factura emitida sin
   *  cobro completo; null = sin factura o sin comprobar). Es el estado
   *  CONTABLE, distinto del «Pagado» del CRM (`payment_status`). */
  factusol_cobro_status?: FactusolCobroStatus | null;
  factusol_cobro_checked_at?: string | null;
  /** Serie (TIPFAC) de la factura, resuelta una vez (clave compuesta). */
  factusol_invoice_serie?: number | null;
  /** Factura VINCULADA (estado facturado + nº + serie, sea cual sea el origen
   *  del pedido): `numero` = «2-526107». null si no la tiene o le falta la
   *  serie (ver `factusol_invoice_problem`). */
  factusol_invoice?: LinkedInvoice | null;
  /** Por qué no hay factura utilizable: sin factura, o hay nº pero falta la
   *  serie (no se localiza por el nº solo). null = bien vinculada. */
  factusol_invoice_problem?: "sin_factura" | "sin_serie" | null;
  /** Último detalle comprobado (nº, total, cobrado, saldo, ESTFAC, líneas). */
  factusol_cobro?: FactusolCobroBlock | null;
  /** ERP-F6 — campos del Excel de seguimiento: nº de serie (texto libre,
   *  también notas), licencia WhiteRIP y origen del envío (OFI-TER-SAT). */
  serial_number?: string | null;
  whiterip_license?: string | null;
  shipping_origin?: string | null;
  /** E4-fix1: idioma del pedido (es/en/de/fr/nl) — detectado al importar de
   *  Woo o corregido a mano; null = desconocido. */
  language?: string | null;
  approved_at: string | null;
  placed_at: string | null;
  created_at: string;
  /** B-2-fix4: seteado si el pedido se gestionó fuera del ERP. */
  externally_processed_at: string | null;
  /** Control manual (#388 + bandeja): quitado de las listas de trabajo
   *  (bandeja, Cola PEDIDOS, seguimiento/Drive). Reversible. */
  excluded?: boolean;
  seguimiento_excluded_at?: string | null;
  seguimiento_excluded_reason?: string | null;
  seguimiento_excluded_by_user_id?: string | null;
  seguimiento_excluded_by_name?: string | null;
  /** «Marcar completado» (solo BoHub, reversible): estado final manual. */
  completed?: boolean;
  completed_at?: string | null;
  completed_by_user_id?: string | null;
  completed_by_name?: string | null;
  /** Estado propio «Reembolsado» (WooCommerce `refunded`). Lleva el mismo
   *  sello técnico que un anulado, pero NO es lo mismo: se dice por lo que es
   *  y el Seguimiento lo sigue enseñando. */
  refunded?: boolean;
  /** «Anular pedido» (solo manuales/FACTUSOL, reversible, distinto de
   *  quitar): estado final; fuera de bandeja, colas y seguimiento. */
  cancelled?: boolean;
  cancelled_at?: string | null;
  cancelled_reason?: string | null;
  cancelled_by_user_id?: string | null;
  cancelled_by_name?: string | null;
  /** Nombre de envío (dropshipping) del pedido manual; null = la empresa. */
  shipping_name?: string | null;
  /** «No requiere envío» (pestaña «Sin envío» de la Cola SAT): el pedido NO se
   *  envía. Fuera de los pendientes del taller y de «Por enviar»; el Envío
   *  sale «No aplica». Reversible. */
  shipping_not_required?: boolean;
  /** Tipo de pedido cuando no es el corriente. `"sample"` = MUESTRA / envío
   *  NO FACTURABLE: sin albarán, factura ni cobro (esas casillas salen «No
   *  aplica») y no entra en «Por facturar» ni «Por cobrar».
   *  `"sample_converted"` = muestra con un documento FACTUSOL vinculado: un
   *  pedido normal a todos los efectos (conserva el nº y el badge). null = normal. */
  order_kind?: string | null;
  /** Nació como muestra (pura o convertida): badge «muestra». */
  born_as_sample?: boolean;
  /** Fase 2: nº del albarán FACTUSOL (`5-500008`) creado por BoHub al
   *  convertir la proforma / pedido de cliente. Los pedidos web no lo llevan. */
  factusol_albaran_number?: string | null;
  /** Fecha (ISO) del último envío de la factura por email al cliente, o null si
   *  nunca. Con factura + null = «Factura sin enviar»; sin factura = no aplica. */
  invoice_emailed_at?: string | null;
  /** Rediseño de flujo: cola, siguiente acción y alertas (calculado). */
  workflow?: OrderWorkflow;
};

/** Lote 7 · P1 — series (empresas emisoras) que se eligen a mano en un pedido
 *  manual: manda en `resolve_serie` (albarán y luego proforma / factura). */
export const FACTUSOL_SERIES: readonly { value: number; label: string }[] = [
  { value: 1, label: "Bomedia" },
  { value: 2, label: "MQ Europe" },
  { value: 4, label: "Lambert" },
  { value: 5, label: "Streamtec" },
] as const;

/** Nombre de una serie (empresa emisora), o el número si no se reconoce. */
export function factusolSerieLabel(serie: number | null | undefined): string {
  if (serie == null) return "—";
  return FACTUSOL_SERIES.find((s) => s.value === serie)?.label ?? String(serie);
}

export type Blocker = { code: string; detail: string };
/** B-2-fix4: aviso NO bloqueante (misma forma que Blocker). */
export type Warning = { code: string; detail: string };

export type OrderLine = {
  id: string;
  position: number;
  product_sku: string;
  product_codart: string | null;
  description: string;
  quantity: number;
  unit_price: number;
  tax_rate: number;
  line_total: number;
  notes: string | null;
  /** Línea de PORTES (gastos de envío), no mercancía: al emitir va a los
   *  portes de la cabecera del documento FACTUSOL, como los pedidos web. */
  is_shipping?: boolean;
  /** Tipo de línea de un pedido web: 'shipping' (envío/portes), 'fee'
   *  (comisiones / otros cargos) o null/ausente (mercancía). Separa el
   *  desglose del resumen económico sin derivar los portes como resto. */
  line_kind?: string | null;
};

export type StatusHistoryRow = {
  id: string;
  domain: StatusDomain;
  from_status: string | null;
  to_status: string;
  changed_at: string;
  changed_by_user_id: string | null;
  reason: string | null;
  metadata: Record<string, unknown>;
};

export type AvailableTransition = {
  to_status: string;
  label: string;
  required_evidence: string[];
};

export type OrderException = {
  id: string;
  type: string;
  subtype: string | null;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type OrderDetail = OrderSummary & {
  notes: string | null;
  packing: Record<string, unknown> | null;
  lines: OrderLine[];
  status_history: StatusHistoryRow[];
  exceptions: OrderException[];
  available_transitions: Record<StatusDomain, AvailableTransition[]>;
  blockers: Blocker[];
  warnings: Warning[];
  externally_processed_note: string | null;
  externally_processed_by_user_id: string | null;
  /** Fase 2: paso de pago apuntado al convertir (opción B). Su `cobro` solo
   *  lo llevan los pedidos anteriores al 14/9/2026 (cuando se registraba
   *  solo al emitir); ahora el cobro es siempre manual. */
  factusol_payment?: FactusolPaymentInfo | null;
  /** Documento de ORIGEN imprimible en FACTUSOL («PDF del pedido
   *  (FACTUSOL)»): presupuesto / pedido de cliente del que se creó (Fase 1),
   *  o el F_PCL de un pedido web (`by_ref`: se localiza por REFPCL al
   *  descargar). `null` = sin documento → el botón se deshabilita. */
  factusol_document?: FactusolOriginDocument | null;
  /** Rediseño de flujo: el MISMO bloque que recibe la bandeja (stepper de la
   *  «línea de vida», siguiente paso y alertas). */
  workflow?: OrderWorkflow;
  /** Último envío de la factura al cliente por email DESDE la app (ISO) y a
   *  quién se envió. `null` / vacío = nunca se ha enviado. Alimenta el
   *  indicador «Factura enviada al cliente el DD/MM/AAAA» de la línea de vida. */
  invoice_emailed_at?: string | null;
  invoice_emailed_to?: string[];
  /** «Factura enviada» (paso 6 de la línea de vida): por email desde BoHub,
   *  marcada a mano («ya enviada») o apuntada en la hoja de seguimiento. La
   *  más reciente; `null` = sin enviar. */
  invoice_sent?: InvoiceSent | null;
  /** Lote 7 · P1: serie (empresa emisora) elegida a mano para el pedido
   *  (1 Bomedia / 2 MQ Europe / 4 Lambert / 5 Streamtec), o null. La ficha la
   *  enseña y ofrece «Cambiar serie» en los pedidos manuales. */
  factusol_manual_serie?: number | null;
  /** Documentos FACTUSOL vinculados (origen / albarán / factura): la ficha
   *  ofrece «Desvincular» en cada uno. */
  linked_documents?: LinkedOrderDocument[];
  /** «Mandar a SAT» y el albarán sin duplicar (30/10/2026). */
  mandar_sat?: MandarSatInfo;
  /** Muestra SIN convertir que ya apunta a un documento (vinculado desde
   *  ERP · Documentos sin cargar sus datos): «Reprocesar vínculo». */
  sample_pending_link?: LinkedOrderDocument | null;
  /** Pedido web traído a mano desde ERP · Pedidos («en espera» en la tienda):
   *  cuándo y quién, el veredicto de FACTUSOL y lo que cambió en la web al
   *  cobrarse. `null` / ausente = entró solo. */
  traido_a_mano?: TraidoAMano | null;
};

export type FactusolOriginDocument = {
  doc_type: "presupuestos" | "pedidos";
  serie: number | null;
  codigo: number | null;
  numero: string | null;
  label: string;
  /** Pedido web: el F_PCL se localiza al descargar por su referencia común
   *  `REFPCL` (`ref`, p. ej. `FLE-005789`); la ficha NO comprueba en cada
   *  carga si existe (el botón siempre intenta y un 404 controlado avisa). */
  by_ref: boolean;
  ref?: string | null;
};

/** Fase 2 · paso de confirmación de pago al convertir (opción B). `paid=false`
 *  = «sin pago» (solo se apunta la forma de pago). `paid=true` exige la
 *  contrapartida (código «6» o nombre «Bomedia (Sabadell)»); fecha opcional
 *  (hoy por defecto). Nunca emite factura, y el cobro F-4-B tampoco se
 *  registra solo: lo hace «Registrar cobro» cuando exista la factura. */
export type PaymentIntentInput = {
  paid: boolean;
  /** C1 «sin cobro» (cortesía): ni se factura ni se cobra. Excluyente con `paid`. */
  no_charge?: boolean;
  forma_pago?: string | null;
  forma_pago_nombre?: string | null;
  contrapartida?: string | null;
  fecha?: string | null;
};

export type FactusolCollectionInfo = {
  registered: boolean;
  status: string | null;
  numero: string | null;
  linlco: number | null;
  importe: number | null;
  fecha: string | null;
  motivo: string | null;
  at: string | null;
};

/** Lo apuntado en el pedido (`packing_json.factusol_payment`). */
export type FactusolPaymentInfo = {
  paid: boolean;
  forma_pago: string | null;
  forma_pago_nombre: string | null;
  contrapartida: string | null;
  contrapartida_nombre: string | null;
  fecha: string | null;
  recorded_at?: string | null;
  cobro: FactusolCollectionInfo | null;
};

/** Lo que añade el alta al detalle cuando parte de un documento FACTUSOL:
 *  el job del albarán encolado en `factusol:writes` (o por qué no). */
export type AlbaranJobExtra = {
  albaran_job_id?: string | null;
  albaran_error?: string | null;
  albaran_skipped?: string | null;
};

export type PendingOrder = OrderSummary & {
  blockers: Blocker[];
  warnings: Warning[];
};

export type TimelineEvent = {
  type: "status" | "exception" | "audit";
  at: string;
  title: string;
  detail: Record<string, unknown>;
  actor_user_id: string | null;
};

/** D-2: «Nombre Apellido · Empresa» a partir de lo que haya. Cadena vacía si
 *  el pedido no tiene ni contacto ni empresa (el CRM no siempre los tiene). */
export function customerLabel(
  order: { contact_name?: string | null; company_name?: string | null },
): string {
  return [order.contact_name, order.company_name].filter(Boolean).join(" · ");
}

/** El cliente como se lee de lejos (tarjeta de la bandeja, cabecera de la
 *  ficha): la EMPRESA primero y, si hay, la persona detrás («SAS QUALI-CITE ·
 *  Virginie Marchand»); sin empresa, la persona. */
export function customerHeadline(
  order: { contact_name?: string | null; company_name?: string | null },
): string {
  return [order.company_name, order.contact_name].filter(Boolean).join(" · ");
}

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export type OrderFilters = {
  /** Bandeja: los pagados delante y los pendientes de pago detrás, también al
   *  recortar a `limit` (los demás usos, por fecha). */
  paid_first?: boolean;
  payment?: string;
  preparation?: string;
  transport?: string;
  invoice?: string;
  store?: string;
  /** B-2-fix4: incluir los pedidos procesados externamente (por defecto ocultos). */
  show_external?: boolean;
  /** Control manual — ver SOLO los quitados a mano («Ver ocultados»). */
  show_excluded?: boolean;
  /** «Ver anulados»: SOLO los pedidos anulados. */
  show_cancelled?: boolean;
  /** «Completado»: true = solo completados, false = solo sin completar. */
  completed?: boolean;
  /** Fase 6: facturado (true) / sin facturar (false); ausente = todos. */
  invoiced?: boolean;
  /** Fase 6: tienda por slug (artisjet-europe / boprint / fluxlasers…). */
  store_slug?: string;
  /** Fase 6: rango de fecha del pedido (YYYY-MM-DD, inclusivo). */
  placed_from?: string;
  placed_to?: string;
  /** Cobro FACTUSOL (estado contable): cobrada / pendiente / sin_comprobar. */
  cobro?: "cobrada" | "pendiente" | "sin_comprobar";
  /** «Factura enviada» al cliente por email: enviada / no_enviada (con factura
   *  pero sin enviar). Ausente = todas. */
  invoice_email?: "enviada" | "no_enviada";
  /** Rediseño de flujo: cola de trabajo (la organización primaria de la
   *  bandeja). Los contadores llegan SIEMPRE completos, con filtro o sin él. */
  queue?: WorkflowQueue;
  /** Fase 3 (ficha de empresa): pedidos de UNA empresa. */
  company_id?: string;
  /** Ficha de contacto (30/10/2026): pedidos de UN contacto. */
  contact_id?: string;
  sort?: string;
  limit?: number;
};

/** Lo que devuelve la bandeja: los pedidos (con su `workflow`) y el contador
 *  de cada cola, calculado sobre todo lo filtrado ANTES de elegir cola. */
export type OrdersBandeja = {
  items: OrderSummary[];
  queue_counts: Record<WorkflowQueue, number>;
  queue: WorkflowQueue | null;
};

const EMPTY_QUEUE_COUNTS = Object.fromEntries(
  WORKFLOW_QUEUES.map((q) => [q, 0]),
) as Record<WorkflowQueue, number>;

export async function listOrders(filters: OrderFilters = {}): Promise<OrdersBandeja> {
  const {
    show_external, show_excluded, show_cancelled, completed, invoiced, paid_first, ...rest
  } = filters;
  const query = qs({
    ...rest,
    paid_first: paid_first ? "true" : undefined,
    show_external: show_external ? "true" : undefined,
    show_excluded: show_excluded ? "true" : undefined,
    show_cancelled: show_cancelled ? "true" : undefined,
    completed: completed === undefined ? undefined : String(completed),
    invoiced: invoiced === undefined ? undefined : String(invoiced),
  });
  const r = await apiFetch<Partial<OrdersBandeja>>(`/api/erp/orders${query}`);
  return {
    items: r.items ?? [],
    queue_counts: { ...EMPTY_QUEUE_COUNTS, ...(r.queue_counts ?? {}) },
    queue: r.queue ?? null,
  };
}

export async function getOrder(id: string): Promise<OrderDetail> {
  return apiFetch<OrderDetail>(`/api/erp/orders/${id}`);
}

// --- «Anular pedido» (manual / FACTUSOL; reversible; distinto de quitar) ----

/** Documento FACTUSOL del pedido que se podría BORRAR al anular (albarán o
 *  presupuesto). La factura nunca: se borra desde FACTUSOL. */
/** Documento FACTUSOL vinculado a un pedido. */
export type LinkedOrderDocument = {
  kind: "factura" | "albaran" | "origen";
  doc_type: "facturas" | "albaranes" | "presupuestos" | "pedidos";
  serie: number | null;
  codigo: number | null;
  /** `2-526110`. */
  numero: string;
  /** «factura 2-526110», «albarán 5-000123», «proforma 1-000045». */
  label: string;
  /** Solo en la previsualización de «Anular»: el aviso de ese documento. */
  message?: string;
};

export type LinkableDocType = "albaranes" | "presupuestos" | "facturas";

/** Lo que cargaría una muestra al vincularle un documento (solo lectura). */
export type LinkDocumentPreview = {
  doc_type: LinkableDocType;
  serie: number;
  codigo: number;
  numero: string;
  fecha: string | null;
  total: number | null;
  referencia: string | null;
  forma_pago: string | null;
  forma_pago_nombre: string | null;
  cliente_codigo: string | null;
  cliente_nombre: string | null;
  company_id: string | null;
  company_name: string | null;
  company_linked: boolean;
  lines: {
    codart: string | null; description: string; quantity: number;
    unit_price: number; discount_pct: number; iva_pct: number | null;
  }[];
  is_sample: boolean;
  linked_elsewhere: { order_id: string; order_number: string } | null;
};

export async function previewLinkOrderDocument(
  orderId: string, doc: { doc_type: LinkableDocType; serie: number; codigo: number },
): Promise<LinkDocumentPreview> {
  const q = new URLSearchParams({
    doc_type: doc.doc_type, serie: String(doc.serie), codigo: String(doc.codigo),
  });
  return apiFetch(`/api/erp/orders/${orderId}/link-document/preview?${q.toString()}`);
}

/** «Vincular documento FACTUSOL» a una muestra: carga sus datos. 409
 *  `factusol_customer_unlinked` (con `codcli`) si el cliente no tiene empresa
 *  CRM: hay que vincularla / crearla y reintentar. Nunca escribe en FACTUSOL. */
export async function linkOrderDocument(
  orderId: string,
  doc: { doc_type: LinkableDocType; serie: number; codigo: number; company_id?: string | null },
): Promise<OrderDetail & { linked?: { document: LinkedOrderDocument } }> {
  return apiFetch(`/api/erp/orders/${orderId}/link-document`, {
    method: "POST", body: JSON.stringify({ ...doc, confirm: true }),
  });
}

/** «Desvincular documento» sin anular: sigue en FACTUSOL. Una muestra
 *  convertida sin más documentos vuelve a modo muestra (`back_to_sample`). */
export async function unlinkOrderDocument(
  orderId: string, kind: LinkedOrderDocument["kind"],
): Promise<OrderDetail & { unlinked?: { document: LinkedOrderDocument; back_to_sample: boolean } }> {
  return apiFetch(`/api/erp/orders/${orderId}/unlink-document`, {
    method: "POST", body: JSON.stringify({ kind, confirm: true }),
  });
}

export type CancelOrderDoc = {
  doc_type: "albaranes" | "presupuestos" | "pedidos";
  serie: number;
  codigo: number;
  numero: string;
  /** Estado en FACTUSOL (0 pendiente; albarán 1 = facturado; presupuesto 1 = aceptado). */
  estado: number | null;
  /** Se puede borrar sin romper nada (no facturado / sin hijos). */
  deletable: boolean;
  reason: string | null;
};

export type CancelOrderPreview = {
  can_cancel: boolean;
  /** Motivos por los que NO se puede anular (p. ej. ya tiene factura, es web). */
  blockers: string[];
  /** Avisos no bloqueantes. */
  warnings: string[];
  factusol_docs: CancelOrderDoc[];
  /** Documentos que se DESVINCULARÁN al anular (siguen en FACTUSOL), con el
   *  aviso de cada uno. */
  documents_to_unlink?: LinkedOrderDocument[];
};

export type CancelOrderPayload = {
  /** OBLIGATORIO true: anular es una decisión, no un clic accidental. */
  confirm: boolean;
  reason?: string | null;
  /** Borrar también en FACTUSOL los documentos marcados como borrables
   *  (albarán / presupuesto). Se hace en cola (worker) y se informa. */
  delete_factusol_docs?: boolean;
};

export type CancelOrderResult = OrderDetail & {
  already_cancelled?: boolean;
  /** Avisos no bloqueantes (p. ej. FACTUSOL no respondió: no se borró nada). */
  cancel_warnings?: string[];
  /** Job de borrado en FACTUSOL (si se pidió); se consulta con
   *  `getConvertJobStatus`/`convert-status`. */
  factusol_delete_job_id?: string | null;
  factusol_docs_to_delete?: CancelOrderDoc[];
  /** Documentos desvinculados al anular (siguen en FACTUSOL). */
  unlinked_documents?: LinkedOrderDocument[];
};

export async function previewCancelOrder(orderId: string): Promise<CancelOrderPreview> {
  return apiFetch(`/api/erp/orders/${orderId}/cancel-preview`, { method: "POST" });
}

export async function cancelOrder(
  orderId: string, payload: CancelOrderPayload,
): Promise<CancelOrderResult> {
  return apiFetch(`/api/erp/orders/${orderId}/cancel`, {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function uncancelOrder(orderId: string): Promise<OrderDetail & {
  already_active?: boolean;
  /** Documentos que se vuelven a vincular al restaurar (los que nadie más tiene). */
  relinked?: { restored: string[]; skipped: { numero: string; reason: string }[] };
}> {
  return apiFetch(`/api/erp/orders/${orderId}/uncancel`, { method: "POST" });
}

/** Lote B8: filtros ligeros de la Cola PEDIDOS — tienda por slug y orden por
 *  fecha (ascendente por defecto: lo más antiguo primero). */
export type PendingApprovalFilters = {
  store_slug?: string;
  sort?: "placed_asc" | "placed_desc";
};

export async function listPendingApproval(
  filters: PendingApprovalFilters = {},
): Promise<PendingOrder[]> {
  const r = await apiFetch<{ items: PendingOrder[] }>(
    `/api/erp/orders/pending-approval${qs(filters)}`,
  );
  return r.items;
}

export async function getOrderTimeline(
  id: string,
  opts: { types?: string; limit?: number } = {},
): Promise<{ total: number; items: TimelineEvent[] }> {
  return apiFetch(`/api/erp/orders/${id}/timeline${qs(opts)}`);
}

export async function fireTransition(
  id: string,
  body: { domain: StatusDomain; to_status: string; reason?: string; evidence?: Record<string, unknown> },
): Promise<OrderDetail> {
  return apiFetch<OrderDetail>(`/api/erp/orders/${id}/transitions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** «Aprobar» (pending_review → in_queue). Lote 2 D: se dispara desde la
 *  bandeja («Por revisar»); con bloqueos el backend responde 409 `blocked`
 *  con la lista de motivos. */
export async function approveOrder(id: string): Promise<OrderDetail> {
  return apiFetch<OrderDetail>(`/api/erp/orders/${id}/approve`, { method: "POST" });
}

/** C-bis: apunta el pago de un pedido ya creado (acción rápida «Pagado»):
 *  forma obligatoria; cuenta y fecha opcionales. No escribe cobro en FACTUSOL. */
export async function recordOrderPayment(
  id: string, payment: PaymentIntentInput,
): Promise<OrderDetail> {
  return apiFetch<OrderDetail>(`/api/erp/orders/${id}/payment`, {
    method: "POST", body: JSON.stringify({ payment }),
  });
}

/** «Aprobar seleccionados» (bandeja): la misma lógica que `approveOrder`
 *  aplicada a cada pedido. Los bloqueados / inexistentes van en `failed` con
 *  su motivo; los que ya no estaban pendientes se cuentan aparte; el resto
 *  se aprueba igualmente. */
export type BulkApproveResult = {
  ok: boolean;
  approved: number;
  already_approved: number;
  failed: { order_id: string; error: string; code?: string }[];
  items: OrderSummary[];
};

export async function approveOrdersBulk(orderIds: string[]): Promise<BulkApproveResult> {
  return apiFetch("/api/erp/orders/bulk-approve", {
    method: "POST", body: JSON.stringify({ order_ids: orderIds }),
  });
}

/** «Marcar completado» (solo BoHub, reversible): estado final del pedido.
 *  Devuelve la ficha + avisos NO bloqueantes («aún sin facturar»…). Nunca
 *  toca WooCommerce. */
export async function completeOrder(
  id: string,
): Promise<OrderDetail & { already_completed: boolean; completion_avisos: string[] }> {
  return apiFetch(`/api/erp/orders/${id}/complete`, { method: "POST" });
}

/** «Completar seleccionados» (bandeja): la misma lógica que `completeOrder`
 *  aplicada a cada pedido (solo BoHub, reversible, idempotente). Cada fila
 *  vuelve con sus `completion_avisos`; los fallos van en `failed` y el resto
 *  se completa igualmente. */
export type BulkCompleteResult = {
  ok: boolean;
  completed: number;
  already_completed: number;
  failed: { order_id: string; error: string }[];
  sin_facturar: number;
  items: (OrderSummary & { already_completed: boolean; completion_avisos: string[] })[];
};

export async function completeOrdersBulk(orderIds: string[]): Promise<BulkCompleteResult> {
  return apiFetch("/api/erp/orders/bulk-complete", {
    method: "POST", body: JSON.stringify({ order_ids: orderIds }),
  });
}

/** «Desmarcar completado»: revierte completeOrder. Idempotente. */
export async function uncompleteOrder(
  id: string,
): Promise<OrderDetail & { already_uncompleted: boolean }> {
  return apiFetch(`/api/erp/orders/${id}/uncomplete`, { method: "POST" });
}

/** E4-fix1 — corrige/persiste el idioma del pedido (null = desconocido). */
export async function updateOrderLanguage(
  id: string, language: FactusolPdfLang | null,
): Promise<{ id: string; language: string | null }> {
  return apiFetch(`/api/erp/orders/${id}/language`, {
    method: "PATCH",
    body: JSON.stringify({ language }),
  });
}

// --- ERP-F6 · seguimiento de pedidos -----------------------------------------

/** Fila de la vista de seguimiento (las columnas del Excel de Bart). */
export type SeguimientoRow = {
  id: string;
  order_number: string;
  serie: number | null;
  empresa: string | null;
  empresa_corta: string;
  fecha: string | null;
  cliente: string | null;
  vendedor: string;
  origen: string | null;
  transportista: string | null;
  preparado: string | null;
  recogido: string | null;
  fecha_envio_factura: string | null;
  productos: string;
  proforma: string | null;
  albaran_pedido: string;
  factura: string | null;
  tracking: string | null;
  num_serie: string | null;
  whiterip: string | null;
  orden: string | null;
  estado: "pendiente" | "enviado" | "facturado";
  en_curso: boolean;
  /** ERP-F6-fix7 — excluido del seguimiento (reversible; no toca el pedido). */
  excluido: boolean;
  excluido_en: string | null;
  excluido_por: string | null;
  excluido_motivo: string | null;
  /** Control manual — nombre de quien lo quitó (vista de excluidos). */
  excluido_por_nombre?: string | null;
  /** «Marcar completado» (solo BoHub, reversible): estado final manual. */
  completado?: boolean;
  completado_en?: string | null;
  completado_por_nombre?: string | null;
  /** ERP-F6-fix7 — ya escrito en la hoja de Drive vs pendiente de escribir. */
  escrito_drive: boolean;
  pendiente_escribir: boolean;
  /** ERP-Woo — estado crudo de WooCommerce y su efecto en el seguimiento. */
  woo_status: string | null;
  /** Oculto por estado: anulado, o web que la tienda no llegó a procesar
   *  (sin pagar / en espera / cancelado / fallido / borrador). Distinto de la
   *  exclusión manual. */
  oculto_por_estado: boolean;
  estado_woo_motivo: string | null;
  /** El mismo motivo, en legible («Sin pagar», «En espera»…). */
  estado_woo_motivo_label?: string;
  /** Estado propio «Reembolsado»: se VE en el seguimiento, aunque el pedido
   *  esté anulado en BoHub. */
  reembolsado: boolean;
  /** «Forzar en seguimiento»: decisión explícita de ver un oculto por estado.
   *  Sigue listado en «Ver ocultos por estado» (para poder deshacerlo). */
  forzado?: boolean;
  forzado_en?: string | null;
  forzado_por_nombre?: string | null;
  /** --- rediseño 2026: hoja simplificada, ordenada por Situación --- */
  /** Situación = cola de la línea de vida (reutiliza el workflow), más el
   *  estado propio «Reembolsado». */
  situacion:
    | "incidencias" | "por_revisar" | "por_facturar" | "factura_por_enviar" | "por_cobrar"
    | "por_enviar" | "listo" | "reembolsado";
  situacion_label: string;
  /** Tono de color de la celda Situación (letras del sistema de diseño). */
  situacion_tone: "r" | "a" | "b" | "t" | "g" | "n";
  /** Total del pedido (número) y su moneda. */
  importe: number;
  moneda: string;
  /** «N · Nombre» de la empresa emisora (serie). */
  empresa_serie: string;
  /** Fecha de la factura (emisión registrada en BoHub). */
  fecha_factura: string | null;
  /** Fecha del envío de la factura por email al cliente. */
  factura_enviada: string | null;
  /** Estado de cobro FACTUSOL (contable). */
  cobro: "cobrado" | "pendiente" | "na";
  cobro_label: string;
  /** Preparación (SAT) y Envío; «No aplica» si no requiere envío. Envío es
   *  SOLO el estado, de una lista cerrada (Sin enviar · Pendiente de entrada
   *  en red · Recogido · En tránsito · En reparto · Disponible en oficina ·
   *  Entregado · Incidencia · Enviado · No aplica). */
  preparacion: string;
  envio: string;
  /** Con quién va el envío: la agencia de Genei («Ctt Premium»), el courier
   *  apuntado en la Cola SAT («UPS»; «otro courier» si no se apuntó) o «—» sin
   *  envío. El filtro «Transportista» filtra por aquí. */
  courier: string;
  /** Origen: WEB o el canal/comercial. */
  origen_label: string;
  /** Datos técnicos combinados (Nº serie · WhiteRIP). */
  serie_whiterip: string;
  /** Motivo del bloqueo (columna Nota / Incidencia). */
  nota_incidencia: string;
  /** Detalle de la incidencia (pestaña Incidencias) o null. */
  incidencia: {
    tipo: string;
    motivo: string;
    asignado: string;
    fecha: string | null;
    estado: string;
  } | null;
};

export type SeguimientoFilters = {
  serie?: number;
  vendedor?: string;
  transportista?: string;
  origen?: string;
  desde?: string;
  hasta?: string;
  estado?: "pendiente" | "enviado" | "facturado" | "completado";
  q?: string;
  en_curso?: boolean;
  /** ERP-F6-fix7 — ver SOLO los excluidos; ver SOLO los pendientes de escribir. */
  ver_excluidos?: boolean;
  pendiente_escribir?: boolean;
  /** ERP-Woo — ver SOLO los ocultados por estado (cancelado/reembolsado/fallido). */
  ver_ocultos_estado?: boolean;
  sort?: string;
  dir?: "asc" | "desc";
  limit?: number;
  offset?: number;
};

export type SeguimientoPage = {
  items: SeguimientoRow[];
  total: number;
  columns: string[];
  /** Cabecera de la pestaña Incidencias (rediseño 2026). */
  incidencias_columns?: string[];
  drive: {
    configured: boolean;
    service_account_email: string | null;
    spreadsheet_id: string | null;
  };
};

function seguimientoQs(filters: SeguimientoFilters): string {
  // `qs` solo admite string/number: los booleanos van como "true"/"false".
  const {
    en_curso, ver_excluidos, ver_ocultos_estado, pendiente_escribir, ...rest
  } = filters;
  return qs({
    ...rest,
    ...(en_curso === undefined ? {} : { en_curso: String(en_curso) }),
    ...(ver_excluidos ? { ver_excluidos: "true" } : {}),
    ...(ver_ocultos_estado ? { ver_ocultos_estado: "true" } : {}),
    ...(pendiente_escribir ? { pendiente_escribir: "true" } : {}),
  });
}

export async function listSeguimiento(
  filters: SeguimientoFilters = {},
): Promise<SeguimientoPage> {
  return apiFetch<SeguimientoPage>(`/api/erp/seguimiento${seguimientoQs(filters)}`);
}

export async function exportSeguimientoXlsx(
  filters: SeguimientoFilters = {},
): Promise<Blob> {
  return apiDownloadBlob(`/api/erp/seguimiento/export${seguimientoQs(filters)}`);
}

/** ERP-F6-fix4/fix7 — un item a revisar: contradicción, ambigüedad o
 *  coincidencia probable. (Ya NO hay «celda manual distinta»: fix7 no
 *  actualiza filas existentes, así que no hay nada que contrastar.) */
export type DriveSyncConflict = {
  kind: "contradicted" | "ambiguous" | "probable_match";
  order_number: string | null;
  row?: number;
  rows?: number[];
  column?: string;
  detail?: string;
};

/** ERP-F6-fix7 — factura escrita en la hoja que BoHub no conoce. Es
 *  INFORMACIÓN, no requiere decisión: va en su propia sección. */
export type DriveSyncUnknownInvoice = {
  kind: "sheet_invoice_unknown";
  order_number: string | null;
  rows?: number[];
  detail?: string;
};

/** ERP-F6-fix4 — items a revisar agrupados por pedido (Parte G). */
export type DriveSyncReviewGroup = {
  order_number: string | null;
  items: DriveSyncConflict[];
};

/** Volcado del formato NUEVO a la pestaña gestionada por la app (lo que hace
 *  «Actualizar hoja de Drive» desde el rediseño 2026). La pestaña histórica no
 *  se toca: se nombra solo para decirlo. */
export type DriveManagedSummary = {
  mode: "managed_tab";
  /** Pestaña que la app reescribe entera. */
  tab: string;
  incidencias_tab: string;
  /** La pestaña histórica, que NO se toca. */
  historic_tab: string;
  rows: number;
  incidencias: number;
  por_situacion: Record<string, number>;
  columns: string[];
  dry_run: boolean;
  written: boolean;
  /** Pestañas creadas en esta escritura (vacío si ya existían). */
  created_tabs?: string[];
  /** Filas del histórico que se CONSERVAN bajo el separador (no se reescriben). */
  historico_preservado?: number;
  /** Pendientes heredados que se conservan en la pestaña de incidencias. */
  pendientes_preservados?: number;
  /** Filas tecleadas A MANO (Origen = MANUAL) que se conservan arriba. */
  manuales?: number;
  /** De ellas, cuántas se han cruzado con su pedido de BoHub (mismo Nº). */
  manuales_fusionadas?: number;
  /** Cuántas se entregan a BoHub (sin nada que perder: dejan de ser manuales). */
  manuales_entregadas?: number;
  /** Celdas en las que BoHub dice otra cosa: se conserva lo manual y se marca
   *  en la Nota («⚠ BoHub …»). */
  conflictos?: number;
  /** Espejo bidireccional (Fase 2): lo leído de la hoja y lo que se ha hecho. */
  espejo?: SeguimientoEspejoStats;
  /** Migración de la pestaña a 20 columnas (columna «Courier»): null si ya la
   *  tenía; «pendiente» en la vista previa; «hecha» tras insertar la columna,
   *  con el recuento de celdas no vacías antes/después; «reescrita» si era del
   *  formato de 17 columnas (se reescribe entera, sin insertar). */
  migracion_courier?: DriveCourierMigration | null;
};

/** Migración de «Seguimiento (app)» a 20 columnas: inserta «Courier» entre
 *  «Envío» y «Fecha recogido» en la cabecera y en todas las filas. */
export type DriveCourierMigration = {
  estado: "pendiente" | "hecha" | "reescrita";
  /** Formato con el que estaba escrita la pestaña: «sin_courier» (19
   *  columnas: se inserta la columna) o «sin_recogido» (17: se reescribe). */
  formato?: string;
  filas?: number;
  celdas_antes?: number;
  celdas_despues?: number;
  /** Vista previa: celdas con dato en la columna Z, que no cabrían al
   *  insertar «Courier» (hay que moverlas antes; si no, no se migra). */
  celdas_que_no_caben?: number;
  por_columna_antes?: Record<string, number>;
  por_columna_despues?: Record<string, number>;
};

/** Recuento del espejo BoHub ↔ hoja en una pasada. */
export type SeguimientoEspejoStats = {
  /** Primera pasada (sin foto anterior): no se infiere ninguna edición. */
  bootstrap?: boolean;
  ediciones_leidas?: number;
  overrides_guardados?: number;
  tracking_leidos?: number;
  tracking_genei_ignorados?: number;
  manuales_nuevas?: number;
  manuales_invalidas?: number;
  historico_ids_asignados?: number;
  historico_editadas?: number;
  historico_nuevas?: number;
  historico_no_encontradas?: number;
  historico_duplicados_suprimidos?: number;
  borradas?: number;
  restauradas?: number;
  borrado_masivo?: boolean;
  protecciones?: number;
  proteccion_error?: string;
  /** Filas guardadas por el espejo puestas al formato de 20 columnas (una vez). */
  formato_bd_convertidas?: number;
  /** Pedidos de BoHub cuya fila iba a desaparecer de la hoja sin estar
   *  quitados, anulados, completados ni gestionados fuera: se conservan. */
  filas_rescatadas?: string[];
  /** Filas repetidas (mismo id) que se quitan FUSIONADAS en la que se queda:
   *  Nº de pedido de cada una, valores conservados y en conflicto. */
  filas_fusionadas?: string[];
  valores_rellenados?: number;
  valores_en_conflicto?: number;
};

/** Resumen de la sincronización INCREMENTAL a la hoja histórica (ERP-F6). Solo
 *  se usa con el ajuste `drive_legacy_insert`; ya no es el modo por defecto. */
export type DriveLegacySummary = {
  mode?: undefined;
  ok: boolean;
  preview: boolean;
  orders_considered: number;
  /** ERP-F6-fix7: SOLO se añaden filas; nunca se actualiza una ya escrita. */
  appended_rows: number;
  /** ERP-F6-fix7: pedidos que ya estaban en la hoja (no se tocan). */
  already_present: number;
  /** Compat: la sincronización ya no actualiza nada (siempre 0). */
  updated_rows: number;
  updated_cells: number;
  conflicts: DriveSyncConflict[];
  /** ERP-F6-fix4: coincidencias probables (falta un dato para confirmar). */
  probable_matches: DriveSyncConflict[];
  /** ERP-F6-fix7: facturas de tu hoja que BoHub no conoce (información). */
  unknown_invoices: DriveSyncUnknownInvoice[];
  /** ERP-F6-fix4: a revisar, agrupado por pedido. */
  review_groups: DriveSyncReviewGroup[];
  orders_to_review: number;
  sheet_rows: number;
  omitted_columns: string[];
};

/** Lo que devuelve «Actualizar hoja de Drive»: el volcado a la pestaña
 *  gestionada (por defecto) o la sincronización histórica (con el toggle).
 *  Se distinguen por `mode`. */
export type DriveSyncSummary = DriveManagedSummary | DriveLegacySummary;

export function isManagedSummary(s: DriveSyncSummary): s is DriveManagedSummary {
  return s.mode === "managed_tab";
}

/** Previsualiza (dry_run) o ejecuta la sincronización con la hoja de Drive. */
export async function syncSeguimientoDrive(
  opts: { preview?: boolean } = {},
): Promise<DriveSyncSummary> {
  const q = opts.preview ? "?dry_run=true" : "";
  return apiFetch<DriveSyncSummary>(`/api/erp/seguimiento/drive-sync${q}`, { method: "POST" });
}

/** Control manual — motivos rápidos de «Quitar del seguimiento». */
export const EXCLUSION_REASON_CODES = [
  "cancelado", "duplicado", "prueba", "reembolsado", "otro",
] as const;
export type ExclusionReasonCode = (typeof EXCLUSION_REASON_CODES)[number];

/** Control manual — un pedido en la previsualización de «Quitar»: sus AVISOS
 *  aguas abajo (factura, cobro, albarán, SAT, Drive…). Solo avisa. */
export type ExcludePreviewItem = {
  order_id: string;
  order_number: string;
  cliente: string | null;
  woo_status: string | null;
  excluido: boolean;
  excluido_motivo: string | null;
  avisos: string[];
};

export type ExcludePreview = {
  ok: boolean;
  items: ExcludePreviewItem[];
  con_avisos: number;
  ya_excluidos: number;
};

/** Control manual — qué tiene cada pedido aguas abajo antes de quitarlo. No
 *  escribe nada; no bloquea. */
export async function previewExcludeSeguimiento(
  orderIds: string[],
): Promise<ExcludePreview> {
  return apiFetch("/api/erp/seguimiento/exclude-preview", {
    method: "POST",
    body: JSON.stringify({ order_ids: orderIds }),
  });
}

export type ExcludeResult = {
  ok: boolean;
  excluded: number;
  already_excluded: number;
  reason: string | null;
  avisos: Record<string, string[]>;
  con_avisos: number;
};

/** ERP-F6-fix7 + control manual — quitar pedidos del seguimiento (uno o
 *  varios), en cualquier estado. No borra ni modifica el pedido; solo lo saca
 *  del seguimiento. Reversible con `includeSeguimiento`. */
export async function excludeSeguimiento(
  orderIds: string[], reason?: string, reasonCode?: ExclusionReasonCode,
): Promise<ExcludeResult> {
  return apiFetch("/api/erp/seguimiento/exclude", {
    method: "POST",
    body: JSON.stringify({
      order_ids: orderIds, reason: reason || null, reason_code: reasonCode || null,
    }),
  });
}

/** ERP-F6-fix7 — reincluir pedidos antes excluidos (deshace «Quitar»). */
export async function includeSeguimiento(
  orderIds: string[],
): Promise<{ ok: boolean; included: number; already_included: number }> {
  return apiFetch("/api/erp/seguimiento/include", {
    method: "POST",
    body: JSON.stringify({ order_ids: orderIds }),
  });
}

/** ERP-Woo — forzar (o dejar de forzar) en el seguimiento pedidos ocultos POR
 *  ESTADO. Es otro eje que `includeSeguimiento`, que solo deshace la exclusión
 *  manual: un oculto por estado no se rescata con «Reincluir». No toca el
 *  pedido ni el estado de la tienda; es solo la vista. */
export async function forceSeguimiento(
  orderIds: string[], forced = true,
): Promise<{ ok: boolean; forced: boolean; changed: number; already: number }> {
  return apiFetch("/api/erp/seguimiento/force", {
    method: "POST",
    body: JSON.stringify({ order_ids: orderIds, forced }),
  });
}

/** ERP-Woo — resultado de la puesta al día de estados de WooCommerce. */
export type WooReconcileSummary = {
  ok: boolean;
  preview: boolean;
  scanned: number;
  unchanged: number;
  capped: boolean;
  limit: number;
  /** nº de llamadas a WooCommerce (listados) que hizo la reconciliación. */
  woo_calls?: number;
  to_cancel: number;
  to_fail: number;
  to_trash: number;
  /** Volvieron a «sin pagar» / «en espera»: salen del seguimiento y vuelven
   *  solos si la tienda los pasa otra vez a `processing`. */
  to_unpaid: number;
  removed_total: number;
  /** Reembolsos. NO salen del seguimiento —es el estado propio
   *  «Reembolsado»—: solo se marcan como tales. */
  to_refunded: number;
  /** Web SIN estado (NULL) consultados uno a uno: rellenados con su estado
   *  real, y los que la tienda ya no tiene (quedan `not_found`, ocultos). */
  unknown_total?: number;
  to_filled?: number;
  to_not_found?: number;
  /** Pedidos web a los que se rellena el MÉTODO DE PAGO (estaba vacío), cambien
   *  o no de estado: en total, por tienda (`account_id`) y los que siguen sin
   *  él (la tienda no lo tiene o quedan para la próxima pasada). */
  to_payment_method?: number;
  payment_method_by_store?: Record<string, number>;
  payment_method_pending?: number;
  payment_method_samples?: string[];
  /** Pedidos conocidos cuyo estado se pone al día (salidas, reembolsos,
   *  estados recuperados y los que ya no existen). */
  refreshed_total?: number;
  /** Pedidos PAGADOS en la tienda que no están en BoHub (la tienda no disparó
   *  el webhook): en la vista previa, los que se importarían; al aplicar, los
   *  importados por el mismo camino que el webhook. */
  missing?: WooMissingSummary;
  errors: { order_number?: string | null; store?: string; status?: string; error: string }[];
  samples: Record<string, string[]>;
};

/** Un pedido pagado de la tienda que no estaba en BoHub. */
export type WooMissingItem = {
  tienda: string;
  tienda_nombre: string;
  woo_id: number;
  numero: string;
  estado: string;
  cliente: string;
  importe: string;
  moneda: string;
  pagado_el: string | null;
  /** El pedido en el admin de la tienda. */
  enlace: string;
  /** a_importar (vista previa) · importado · error · sin_importar (tope) ·
   *  ya_estaba (lo creó un webhook entre el listado y la importación). */
  resultado: "a_importar" | "importado" | "error" | "sin_importar" | "ya_estaba";
  order_id?: string;
  order_number?: string | null;
  error?: string;
};

export type WooMissingSummary = {
  /** Si la búsqueda entera falló (la puesta al día de estados sí se hizo). */
  error?: string;
  dias?: number;
  faltan?: number;
  importados?: number;
  items?: WooMissingItem[];
  errores?: { store?: string; order_number?: string; error: string }[];
  con_tope?: boolean;
  woo_calls?: number;
};

/** Por dónde va la puesta al día (lo informa el propio trabajo). */
export type WooReconcileProgress = {
  fase: string;
  hechos?: number | null;
  total?: number | null;
  at?: string;
};

type WooReconcileExtra = {
  progress?: WooReconcileProgress | null;
  preview?: boolean | null;
  ended_at?: string | null;
};

/** Estado del job de reconciliación (polling). `missing`: el trabajo ya no
 *  existe (caducó: RQ guarda el resultado una hora). */
export type WooReconcileStatus =
  | ({ status: "pending" } & WooReconcileExtra)
  | ({ status: "finished"; result: WooReconcileSummary } & WooReconcileExtra)
  | ({ status: "error"; error?: string } & WooReconcileExtra)
  | { status: "missing" };

/** La última puesta al día lanzada y su estado (para recuperarla al volver). */
export type WooReconcileLast =
  | { job_id: null }
  | (WooReconcileStatus & {
    job_id: string; preview: boolean; store?: string | null; enqueued_at?: string;
  });

export async function getLastReconcileWoo(): Promise<WooReconcileLast> {
  return apiFetch("/api/erp/seguimiento/reconcile-woo-last");
}

/** Texto de «por dónde va» para la pantalla. */
export function describeReconcileProgress(p?: WooReconcileProgress | null): string {
  if (!p?.fase) return "En cola…";
  return p.total ? `${p.fase} (${p.hechos ?? 0} de ${p.total})` : p.fase;
}

/** ERP-Woo — ENCOLA la puesta al día (corre en segundo plano en worker-sync)
 *  y devuelve un `job_id`. `preview` (por defecto) no escribe. */
export async function reconcileWooStatuses(
  opts: { preview?: boolean } = {},
): Promise<{ job_id: string; status: string; preview: boolean }> {
  const q = opts.preview === false ? "?dry_run=false" : "?dry_run=true";
  return apiFetch(`/api/erp/seguimiento/reconcile-woo${q}`, { method: "POST" });
}

export async function getReconcileWooStatus(jobId: string): Promise<WooReconcileStatus> {
  return apiFetch(`/api/erp/seguimiento/reconcile-woo-status/${encodeURIComponent(jobId)}`);
}

/** Espera a que el job termine, el tiempo que haga falta (tarda 4-5 minutos;
 *  el trabajo tiene un tope de 20 en el servidor), avisando de por dónde va
 *  con `onProgress`. Solo se rinde si el trabajo deja de existir, si se pide
 *  parar (`signal`) o pasados `maxMs` (por encima del tope del servidor). */
export async function waitForReconcileWoo(
  jobId: string,
  {
    delayMs = 2000,
    maxMs = 30 * 60 * 1000,
    onProgress,
    signal,
  }: {
    delayMs?: number;
    maxMs?: number;
    onProgress?: (p: WooReconcileProgress | null | undefined) => void;
    signal?: { cancelled: boolean };
  } = {},
): Promise<WooReconcileStatus> {
  const inicio = Date.now();
  let last: WooReconcileStatus = { status: "pending" };
  while (!signal?.cancelled && Date.now() - inicio < maxMs) {
    last = await getReconcileWooStatus(jobId);
    if (last.status !== "pending") return last;
    onProgress?.(last.progress);
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return last;
}

// --- ERP · vincular facturas creadas a mano en FACTUSOL ----------------------

/** Una factura de FACTUSOL localizada por REFFAC. */
export type FactusolFacturaRef = {
  codfac: string | null;
  serie: number | null;
  numero: string;
  cliente_codigo: string | null;
  total: number | null;
  fecha: string | null;
};

export type FactusolLinkItem = FactusolFacturaRef & {
  order_id: string;
  order_number: string;
  ref: string;
};

/** Pedido con MÁS de una factura por la misma referencia → no se enlaza solo. */
export type FactusolLinkConflict = {
  order_number: string;
  ref: string;
  facturas: FactusolFacturaRef[];
};

export type FactusolLinkSummary = {
  ok: boolean;
  preview: boolean;
  scanned: number;
  linked: number;
  to_link: FactusolLinkItem[];
  conflicts: FactusolLinkConflict[];
  no_match: number;
};

export type FactusolLinkStatus =
  | { status: "pending" }
  | { status: "finished"; result: FactusolLinkSummary }
  | { status: "error"; error?: string };

/** ENCOLA la vinculación de facturas FACTUSOL→pedidos (segundo plano). */
export async function reconcileFactusolInvoices(
  opts: { preview?: boolean } = {},
): Promise<{ job_id: string; status: string; preview: boolean }> {
  const q = opts.preview === false ? "?dry_run=false" : "?dry_run=true";
  return apiFetch(`/api/erp/seguimiento/reconcile-factusol${q}`, { method: "POST" });
}

export async function getFactusolReconcileStatus(jobId: string): Promise<FactusolLinkStatus> {
  return apiFetch(`/api/erp/seguimiento/reconcile-factusol-status/${encodeURIComponent(jobId)}`);
}

export async function waitForFactusolReconcile(
  jobId: string, { tries = 60, delayMs = 2000 } = {},
): Promise<FactusolLinkStatus> {
  let last: FactusolLinkStatus = { status: "pending" };
  for (let i = 0; i < tries; i++) {
    last = await getFactusolReconcileStatus(jobId);
    if (last.status !== "pending") return last;
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return last;
}

/** ERP-F6 — campos de seguimiento del pedido. `orden` se AÑADE a las
 *  observaciones (no existe como campo). */
export async function updateOrderSeguimiento(
  id: string,
  patch: {
    serial_number?: string;
    whiterip_license?: string;
    shipping_origin?: string;
    orden?: string;
  },
): Promise<{
  id: string;
  serial_number: string | null;
  whiterip_license: string | null;
  shipping_origin: string | null;
  notes: string | null;
}> {
  return apiFetch(`/api/erp/orders/${id}/seguimiento`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

// --- procesado externamente (B-2-fix4) --------------------------------------

export async function markExternallyProcessed(
  id: string, note?: string | null,
): Promise<OrderDetail> {
  return apiFetch<OrderDetail>(`/api/erp/orders/${id}/mark-externally-processed`, {
    method: "POST",
    body: JSON.stringify({ note: note ?? null }),
  });
}

export async function bulkMarkExternallyProcessed(
  body: { order_ids?: string[]; store_id?: string; before_date?: string; note?: string | null },
): Promise<{ ok: boolean; marked: number }> {
  return apiFetch("/api/erp/orders/bulk-mark-externally-processed", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Dirección de envío/facturación de un pedido manual (D-2). */
export type OrderAddress = {
  address_line?: string | null;
  city?: string | null;
  postal_code?: string | null;
  state?: string | null;
  country?: string | null;
};

export type OrderCreatePayload = {
  /** D-2: opcional — el backend genera `MANUAL-000001` si no se envía. */
  order_number?: string | null;
  company_id?: string | null;
  contact_id?: string | null;
  currency?: string;
  notes?: string | null;
  placed_at?: string | null;
  tax_id?: string | null;
  pickup_in_store?: boolean;
  shipping_address?: OrderAddress | null;
  billing_address?: OrderAddress | null;
  /** Nombre de envío (dropshipping): destinatario del albarán cuando NO es la
   *  empresa cliente. null = enviar a la empresa. */
  shipping_name?: string | null;
  /** Fase 1: el alta parte de un presupuesto / pedido de cliente de FACTUSOL. */
  factusol_source?: FactusolSourceInput | null;
  /** Lote 7 · P1: serie (empresa emisora) elegida a mano — solo en el alta
   *  MANUAL (con `factusol_source` la serie la hereda el documento). 1 Bomedia
   *  / 2 MQ Europe / 4 Lambert / 5 Streamtec. */
  factusol_serie?: number | null;
  /** Fase 2 (solo con `factusol_source`): paso de pago y albarán en FACTUSOL. */
  payment?: PaymentIntentInput | null;
  create_albaran?: boolean;
  lines: OrderLinePayload[];
};

/** Línea de un alta de pedido (manual o muestra). Al menos SKU o descripción. */
export type OrderLinePayload = {
  product_sku: string;
  product_codart?: string | null;
  description?: string;
  quantity: number;
  unit_price: number;
  tax_rate?: number;
  /** Portes: línea aparte que el backend manda a los portes del documento. */
  is_shipping?: boolean;
};

export async function createOrder(
  payload: OrderCreatePayload,
): Promise<OrderDetail & AlbaranJobExtra> {
  return apiFetch<OrderDetail & AlbaranJobExtra>("/api/erp/orders", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Alta de una MUESTRA / envío NO FACTURABLE (`MUESTRA-000001`).
 *
 *  No pide empresa, serie ni NIF y no toca FACTUSOL: no habrá albarán, factura
 *  ni cobro. Entra directa a la Cola SAT — solo queda prepararla y enviarla.
 *  La pueden crear Comercial, ERP Pedidos, ERP SAT y Admin. */
export type SampleOrderPayload = {
  /** Persona de contacto: a quién va dirigido (obligatorio). */
  recipient_name: string;
  /** Empresa destinataria; opcional (puede ir a un particular/prospecto). */
  recipient_company?: string | null;
  recipient_email?: string | null;
  recipient_phone?: string | null;
  shipping_address: {
    address_line?: string | null;
    city?: string | null;
    postal_code?: string | null;
    state?: string | null;
    country?: string | null;
  };
  contact_id?: string | null;
  company_id?: string | null;
  reason?: string | null;
  notes?: string | null;
  lines: OrderLinePayload[];
};

export async function createSampleOrder(
  payload: SampleOrderPayload,
): Promise<OrderDetail> {
  return apiFetch<OrderDetail>("/api/erp/orders/sample", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Fase 2 — (re)encola la creación del albarán FACTUSOL del pedido (202 +
 *  job_id; estado con `getQuoteJobStatus`). 409 si ya lo tiene, si es un
 *  pedido web o si no procede de un documento de FACTUSOL. */
export async function createOrderAlbaran(
  orderId: string,
  payment?: PaymentIntentInput | null,
): Promise<{ job_id: string; order_id: string; status: string }> {
  // C1: si el pago no está decidido y no se manda una decisión, el backend
  // responde 409 `payment_undecided` (la ficha pide elegir).
  return apiFetch(`/api/erp/orders/${orderId}/albaran`, {
    method: "POST",
    ...(payment ? { body: JSON.stringify({ payment }) } : {}),
  });
}

// --- Fase 1 · pedido desde un documento de FACTUSOL (solo lectura allí) -------

export type FactusolOrderDocType = "presupuestos" | "pedidos";

/** Origen FACTUSOL de un pedido creado a mano desde un documento. */
export type FactusolSourceInput = {
  doc_type: FactusolOrderDocType;
  serie: number;
  codigo: number;
  referencia?: string | null;
  forma_pago?: string | null;
  forma_pago_nombre?: string | null;
};

/** El resumen de «Crear pedido» que se enseña ANTES de crearlo: se calcula
 *  con el mismo reparto de líneas y el mismo importe que la creación. */
export type ResumenPedido = {
  company_id: string | null;
  company_name: string | null;
  /** ISO (fecha del documento; hoy al convertir una proforma). */
  fecha: string | null;
  lineas: {
    product_sku: string;
    description: string;
    quantity: number;
    unit_price: number;
    discount_pct: number;
    line_total: number;
    tax_rate: number;
  }[];
  /** Importe con IVA. */
  total: number;
  origen: {
    doc_type: string;
    /** «albarán», «factura», «proforma»… */
    label: string;
    serie: number;
    codigo: number;
    numero: string;
    referencia: string | null;
  };
};

export type FactusolOrderPreviewLine = {
  position: number;
  codart: string | null;
  description: string;
  quantity: number;
  unit_price: number;
  line_total: number;
  discount_pct: number;
  iva_pct: number | null;
};

/** Cómo quedaría el pedido: cliente resuelto contra el CRM, líneas, nº. */
export type FactusolOrderPreview = {
  doc_type: FactusolOrderDocType;
  serie: number;
  codigo: number;
  numero: string;
  fecha: string | null;
  total: number | null;
  referencia: string | null;
  estado: string | null;
  estado_label: string | null;
  forma_pago: string | null;
  forma_pago_nombre: string | null;
  cliente_codigo: string | null;
  cliente_nombre: string | null;
  company_id: string | null;
  company_name: string | null;
  company_linked: boolean;
  lines: FactusolOrderPreviewLine[];
  order_number: string;
  external_id: string;
  already_imported: { order_id: string; order_number: string } | null;
  /** Lo que se va a crear (modal de confirmación). */
  resumen: ResumenPedido;
};

/** Vista previa del pedido que saldría del documento. Solo LEE: no crea nada. */
export async function previewOrderFromFactusol(
  docType: FactusolOrderDocType, serie: number, codigo: number,
): Promise<FactusolOrderPreview> {
  return apiFetch(
    `/api/erp/orders/from-factusol/preview?doc_type=${docType}&serie=${serie}&codigo=${codigo}`,
  );
}

/** Lo mismo para un ALBARÁN o una FACTURA (Documentos): el resumen del modal
 *  «Crear pedido», antes de crear nada. */
export type DocumentOrderPreview = Omit<FactusolOrderPreview, "doc_type"> & {
  doc_type: "albaranes" | "facturas";
};

export async function previewOrderFromDocumentType(
  docType: "albaranes" | "facturas", serie: number, codigo: number,
): Promise<DocumentOrderPreview> {
  return apiFetch(
    `/api/erp/orders/from-factusol/preview?doc_type=${docType}&serie=${serie}&codigo=${codigo}`,
  );
}

/** Crea el pedido directamente desde el documento (sin pasar por el formulario). */
export async function createOrderFromFactusol(body: {
  doc_type: FactusolOrderDocType;
  serie: number;
  codigo: number;
  company_id?: string | null;
  contact_id?: string | null;
  /** Fase 2: paso de pago (opción B) y albarán en FACTUSOL. */
  payment?: PaymentIntentInput | null;
  create_albaran?: boolean;
}): Promise<OrderDetail & AlbaranJobExtra> {
  return apiFetch<OrderDetail & AlbaranJobExtra>("/api/erp/orders/from-factusol", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --- etiquetas de estado (labels ES + tono para el badge) -------------------

export const STATUS_LABELS: Record<string, { label: string; tone: string }> = {
  // payment
  pending: { label: "Pendiente", tone: "warn" },
  paid: { label: "Pagado", tone: "ok" },
  partial_paid: { label: "Pago parcial", tone: "warn" },
  credit_approved: { label: "Crédito aprobado", tone: "active" },
  failed: { label: "Pago fallido", tone: "bad" },
  refunded: { label: "Reembolsado", tone: "muted" },
  // preparation
  pending_review: { label: "Pend. revisión", tone: "warn" },
  in_queue: { label: "En cola", tone: "muted" },
  preparing: { label: "Preparando", tone: "active" },
  // D-2: en curso → azul (el verde queda para «cerrado/cobrado/entregado»).
  packed: { label: "Embalado", tone: "active" },
  blocked: { label: "Bloqueado", tone: "bad" },
  already_completed_externally: { label: "Externalizado", tone: "muted" },
  // transport
  not_shipped: { label: "Sin enviar", tone: "muted" },
  label_created: { label: "Etiqueta creada", tone: "active" },
  // D-2: recogido/en tránsito ya salió del taller → verde.
  in_transit: { label: "En tránsito", tone: "ok" },
  delivered: { label: "Entregado", tone: "ok" },
  incident: { label: "Incidencia", tone: "bad" },
  returned: { label: "Devuelto", tone: "bad" },
  already_shipped_externally: { label: "Externalizado", tone: "muted" },
  // invoice
  not_invoiced: { label: "Sin facturar", tone: "muted" },
  generated: { label: "Facturada", tone: "ok" },
  error: { label: "Error factura", tone: "bad" },
  credit_note: { label: "Abono", tone: "muted" },
  already_invoiced_externally: { label: "Externalizado", tone: "muted" },
  invoiced_by_erp: { label: "Facturado FACTUSOL", tone: "ok" },
};

export const DOMAIN_LABELS: Record<StatusDomain, string> = {
  payment: "Pago",
  preparation: "Preparación",
  transport: "Transporte",
  invoice: "Facturación",
};

/** Roles con permiso de edición/aprobación en el ERP (espejo de deps.py). */
export const ERP_EDIT_ROLES = ["admin", "pedidos"] as const;

// --- Cola SAT (PR 5) --------------------------------------------------------

/** Lote 2 A3: de dónde sale el albarán que el taller imprime/descarga desde
 *  la cola (prioridad del backend: `factusol` › `file` › `woo`). */
export type SatAlbaranSource = "factusol" | "file" | "woo";

export type SatQueueItem = {
  id: string;
  order_number: string;
  /** «sample» = muestra / envío no facturable: no se cobra ni lleva albarán. */
  order_kind?: string | null;
  /** D-2: cliente visible en la card del taller. */
  contact_name: string | null;
  company_name: string | null;
  preparation_status: PreparationStatus;
  /** Fase D-1-fix1: estado de transporte (decide «Marcar recogido»). */
  transport_status: TransportStatus;
  payment_status: PaymentStatus;
  total_amount: number;
  currency: string;
  lines: { sku: string; description: string; quantity: number }[];
  /** Lote 2 A3: «hay albarán que imprimir o descargar». True también para un
   *  pedido web que aún no lo ha bajado de Woo (lo genera la tienda; BoHub
   *  nunca lo crea en FACTUSOL). El fichero vigente queda aparte en
   *  `has_albaran_file`. */
  has_albaran: boolean;
  /** Ruta del chip de albarán: `factusol` (PDF del albarán de FACTUSOL) ·
   *  `file` (fichero vigente: subido o ya descargado de Woo) · `woo` (pedido
   *  web: descargar de WooCommerce y abrir) · null (sin albarán). */
  albaran_source: SatAlbaranSource | null;
  /** Fase D: fichero de albarán vigente en shipment_files y su origen
   *  (`manual_upload` · `woo_pdf_plugin` · `crm_generated_pdf`). */
  has_albaran_file: boolean;
  albaran_file_source: string | null;
  /** Pedido web (WooCommerce): su albarán lo genera la tienda, nunca FACTUSOL. */
  is_web_order: boolean;
  /** Pedido web con descarga de Woo posible (tienda, id y conexión en orden);
   *  si no, `woo_albaran_unavailable_reason` dice por qué (la card lo enseña). */
  woo_albaran_available: boolean;
  woo_albaran_unavailable_reason: string | null;
  /** Nº del albarán que BoHub creó en FACTUSOL («serie-código»), o null. Es
   *  la fuente PREFERENTE del PDF en el taller: el mismo documento que el
   *  botón de la ficha y el que adjunta el email al SAT. */
  factusol_albaran_number?: string | null;
  /** Fase D: presencia de etiqueta vigente (para el chip). */
  has_etiqueta: boolean;
  /** Fotos (o documentos) del embalaje, todas, de la más antigua a la última. */
  fotos?: ShipmentFile[];
  /** Lote B6: tienda (slug) y fecha del pedido, para la vista lista. */
  store_slug?: string | null;
  placed_at?: string | null;
  /** Lote 5 · #3 — nº de seguimiento del transportista (se rellena y guarda
   *  desde la card de «Listos» con `setOrderTracking`). */
  tracking_number?: string | null;
  /** Lote 2 · PR-2: lo que el taller lee de pie. Campos de seguimiento de la
   *  ficha (ERP-F6), solo lectura en la cola; el backend ya los recorta y
   *  manda null si están vacíos. `notes` = observaciones del comercial (va
   *  arriba, en ámbar, solo si hay); `serial_number` y `whiterip_license`
   *  van grandes en mono con botón de copiar; `shipping_origin` = OFI-TER-SAT. */
  notes?: string | null;
  serial_number?: string | null;
  whiterip_license?: string | null;
  shipping_origin?: string | null;
  /** Pestaña de la Cola SAT a la que pertenece (la decide el backend). */
  sat_tab?: SatTab | null;
  /** Marcado «No requiere envío» (pestaña «Sin envío»): NO se envía. */
  sin_envio?: boolean;
  /** Envío Genei del pedido, si ya lo hay («Ver envío Genei» vs «Crear»). */
  genei?: SatGeneiSummary | null;
  /** Envío con Genei o con OTRO courier (UPS, MRW… apuntado a mano); null =
   *  aún sin envío. */
  shipment_kind?: "genei" | "externo" | null;
  /** Courier del envío: la agencia de Genei o el apuntado a mano. */
  courier?: string | null;
  /** Web de seguimiento (la de Genei o la del courier), si se conoce. */
  tracking_url?: string | null;
  /** Aviso de envío al cliente de un envío externo (pending/sent/error…). */
  customer_email_status?: string | null;
  /** Aviso de envío al cliente (Genei u otro courier): estado, cuándo y a
   *  quién se mandó el último, y cuántas veces. null = sin aviso registrado. */
  customer_email?: SatCustomerEmail | null;
};

export type SatCustomerEmail = {
  status?: string | null;
  sent_at?: string | null;
  to?: string | null;
  sends?: number;
  automatic?: boolean;
};

/** Pestañas de la Cola SAT. «Todos pendientes» = las cuatro de pendientes;
 *  «Sin envío» = «No requiere envío» (NO es enviado). */
export type SatTab =
  | "por_embalar" | "en_preparacion" | "embalados" | "pendiente_recogida"
  | "sin_envio" | "enviados";

/** Resumen del envío Genei en la card del taller. `label_available` = envío
 *  tramitado (estado 1+): antes no se ofrece la etiqueta. */
export type SatGeneiSummary = {
  shipment_code: string;
  state_code?: number | null;
  state_bucket?: string | null;
  state_label?: string | null;
  courier?: string | null;
  tracking?: string | null;
  label_available: boolean;
  /** Último escaneo REAL del transportista (Genei `/tracking`), tal cual. */
  carrier_status?: string | null;
  carrier_status_at?: string | null;
  carrier_step?: string | null;
  carrier_step_label?: string | null;
  tracking_url?: string | null;
  tracking_checked_at?: string | null;
  /** Descarga automática de la etiqueta (esperando / adjunta / agotada). */
  label_auto?: GeneiLabelAuto | null;
};

/** Contadores de cada pestaña (el backend los calcula con su criterio). */
export type SatQueueCounts = {
  por_embalar: number;
  en_preparacion: number;
  embalados: number;
  pendiente_recogida: number;
  pendientes: number;
  sin_envio: number;
  enviados: number;
};

/** Cola SAT por pestañas. `preparing` / `ready_for_pickup` son las dos
 *  secciones de antes (compatibilidad). */
export type SatQueue = {
  preparing: SatQueueItem[];
  ready_for_pickup: SatQueueItem[];
  por_embalar?: SatQueueItem[];
  en_preparacion?: SatQueueItem[];
  embalados?: SatQueueItem[];
  pendiente_recogida?: SatQueueItem[];
  counts?: SatQueueCounts;
};

/** Filtro «Estado» de la Cola SAT: `por_embalar` = las tres de la sección
 *  (bloqueado/preparando/en cola); `ready` = solo «Listos para envío». */
export type SatQueueEstado = "por_embalar" | "blocked" | "in_queue" | "preparing" | "ready";

/** Lote B6: filtros de la Cola SAT (mismos para cola e historial). Fechas
 *  YYYY-MM-DD sobre la fecha del pedido; `q` busca nº de pedido o cliente. */
export type SatQueueFilters = {
  desde?: string;
  hasta?: string;
  store_slug?: string;
  estado?: SatQueueEstado;
  q?: string;
  /** `true` = enseñar SOLO los pedidos marcados «No requiere envío» (para
   *  revisarlos / desmarcar); por defecto quedan fuera de la cola. */
  no_shipping?: boolean;
  /** C4: orden por fecha del pedido. `fecha_desc` (por defecto) = los más
   *  recientes primero; `fecha_asc` = los más antiguos (FIFO). La prioridad
   *  por estado sigue mandando dentro de cada sección. */
  sort?: "fecha_desc" | "fecha_asc";
};

export async function getSatQueue(filters: SatQueueFilters = {}): Promise<SatQueue> {
  const { no_shipping, ...rest } = filters;
  return apiFetch<SatQueue>(
    `/api/erp/sat/queue${qs({ ...rest, no_shipping: no_shipping ? "true" : undefined })}`,
  );
}

/** «Enviados» (recogido / en tránsito / entregado) o, con `sinEnvio`, los
 *  «No requiere envío» (pestaña «Sin envío», que NO son enviados). `total` =
 *  recuento completo; `items` = los `limit` primeros por fecha del pedido. */
export async function getSatShipped(
  filters: SatQueueFilters = {}, sinEnvio = false,
): Promise<{ items: SatQueueItem[]; total: number; limit: number }> {
  const { no_shipping: _ns, estado: _e, ...rest } = filters;
  return apiFetch(
    `/api/erp/sat/shipped${qs({ ...rest, sin_envio: sinEnvio ? "true" : undefined })}`,
  );
}

/** Marcar/desmarcar «No requiere envío» en lote. `value=true` = el pedido NO
 *  se envía (recogida en tienda, licencia, servicio…) y pasa a «Sin envío»;
 *  `value=false` lo devuelve a los pendientes. Reversible; no toca factura,
 *  cobro ni completado. */
export async function bulkNoShipping(
  orderIds: string[], value: boolean,
): Promise<{ ok: boolean; changed: number; already: number; value: boolean }> {
  return apiFetch(`/api/erp/sat/bulk-no-shipping`, {
    method: "POST",
    body: JSON.stringify({ order_ids: orderIds, value }),
  });
}

/** Fila del historial de «enviados al taller»: un email al SAT
 *  (`email_sat`, con destinatarios) o un paso a in_queue (`aprobado`, con el
 *  motivo: Cola PEDIDOS, enviado por email, reabierto, añadido a mano). */
export type SatHistoryRow = {
  order_id: string;
  order_number: string;
  contact_name: string | null;
  company_name: string | null;
  kind: "email_sat" | "aprobado";
  at: string;
  actor_user_id: string | null;
  actor_name: string | null;
  to: string[];
  cc: string[];
  subject: string | null;
  attachment_kinds: string[];
  reason: string | null;
  from_status: string | null;
  preparation_status: PreparationStatus;
  transport_status: TransportStatus;
  factusol_albaran_number: string | null;
  has_albaran: boolean;
  store_slug: string | null;
  placed_at: string | null;
  cancelled: boolean;
  excluded: boolean;
};

export async function getSatHistory(
  filters: SatQueueFilters & { limit?: number } = {},
): Promise<{ items: SatHistoryRow[]; limit: number }> {
  // El historial no filtra por `estado` (es un registro de lo que pasó).
  const { desde, hasta, store_slug, q, limit } = filters;
  return apiFetch(`/api/erp/sat/history${qs({ desde, hasta, store_slug, q, limit })}`);
}

/** Fila de la pestaña «Incidencias» de la Cola SAT: de ENVÍO (transporte, del
 *  webhook de Genei) o de PEDIDO (excepción abierta: taller / stock / VIES…). */
export type SatIncidenciaRow = {
  order_id: string;
  order_number: string;
  contact_name: string | null;
  company_name: string | null;
  tipo: "envio" | "pedido";
  motivo: string;
  /** Solo en las de PEDIDO: id de la excepción (para resolverla) + su etiqueta. */
  exception_id: string | null;
  exception_type: string | null;
  transport_status: TransportStatus;
  tracking_number: string | null;
  store_slug: string | null;
  placed_at: string | null;
  /** «sample» = muestra: el taller también la resuelve (14/10/2026). */
  order_kind?: string | null;
};

export async function getSatIncidencias(
  filters: SatQueueFilters = {},
): Promise<{ items: SatIncidenciaRow[] }> {
  const { desde, hasta, store_slug, q } = filters;
  return apiFetch(`/api/erp/sat/incidencias${qs({ desde, hasta, store_slug, q })}`);
}

/** Resuelve una incidencia de ENVÍO: el transporte vuelve a «en tránsito» y el
 *  pedido sale de «Incidencias» → «Enviados». */
export function resolveShippingIncidencia(
  orderId: string,
): Promise<{ order_id: string; transport_status: TransportStatus }> {
  return apiFetch(`/api/erp/sat/orders/${orderId}/shipping-incidencia/resolve`, {
    method: "POST",
  });
}

/** «Añadir pedido a la cola»: nº de pedido → id + situación actual. */
export type SatOrderLookup = {
  id: string;
  order_number: string;
  contact_name: string | null;
  company_name: string | null;
  preparation_status: PreparationStatus;
  transport_status: TransportStatus;
  already_queued: boolean;
  cancelled: boolean;
  excluded: boolean;
};

export async function findSatOrderByNumber(number: string): Promise<SatOrderLookup> {
  return apiFetch<SatOrderLookup>(`/api/erp/sat/find-order${qs({ number: number.trim() })}`);
}

export type SatEnqueueResult = {
  order_id: string;
  order_number: string;
  preparation_status: PreparationStatus;
  already_queued: boolean;
  /** true si estaba pendiente de revisión y se aprobó al añadirlo. */
  approved: boolean;
  via: "approve" | "transition" | "direct" | null;
};

/** Añade un pedido a la Cola SAT a mano (pendiente → aprobado; embalado /
 *  externalizado → vuelve a la cola). Idempotente si ya estaba. */
export async function satEnqueueOrder(orderId: string): Promise<SatEnqueueResult> {
  return apiFetch<SatEnqueueResult>(`/api/erp/orders/${orderId}/sat-enqueue`, {
    method: "POST",
  });
}

/** «Marcar recogido»: el paquete salió del taller → transporte in_transit. */
export async function markPickedUp(
  orderId: string, data: { tracking?: string | null; courier?: string | null } = {},
): Promise<{ order_id: string; transport_status: string; already_picked_up: boolean }> {
  const body: Record<string, string> = {};
  if (data.tracking?.trim()) body.tracking_number = data.tracking.trim();
  if (data.courier?.trim()) body.courier = data.courier.trim();
  return apiFetch(`/api/erp/orders/${orderId}/mark-picked-up`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Envío del pedido (Genei u OTRO courier) para la ficha. */
export type OrderShipmentInfo = {
  order_id: string;
  kind: "genei" | "externo" | null;
  courier: string | null;
  tracking: string | null;
  tracking_url: string | null;
  transport_status: string;
  picked_up_at: string | null;
  customer_email: CustomerEmailStatus | null;
  suggested_courier: string | null;
  couriers: string[];
};

export async function getOrderShipment(orderId: string): Promise<OrderShipmentInfo> {
  return apiFetch(`/api/erp/orders/${orderId}/shipment`);
}

/** Catálogo de excepciones que SAT puede reportar (subset del backend con
 *  subtipos donde aplica). Espejo de ExceptionType/EXCEPTION_SUBTYPES. */
export const EXCEPTION_CATALOG: {
  type: string;
  label: string;
  subtypes?: { value: string; label: string }[];
}[] = [
  {
    type: "stock_shortage",
    label: "Falta de stock",
    subtypes: [
      { value: "pending_purchase", label: "Pendiente de compra" },
      { value: "eta_set", label: "Con fecha estimada (ETA)" },
      { value: "eta_unknown", label: "Sin ETA" },
      { value: "not_replenishable", label: "Descatalogado" },
    ],
  },
  { type: "material_defective", label: "Material defectuoso (repedir)" },
  { type: "sat_issue", label: "Problema en preparación" },
  { type: "size_exceeds_carrier", label: "Excede peso/medidas del transportista" },
  { type: "blocked_by_customer_request", label: "El cliente pidió parar" },
];

export async function reportException(
  orderId: string,
  body: { type: string; subtype?: string | null; description?: string; metadata?: Record<string, unknown> },
): Promise<{ id: string; preparation_status: string }> {
  return apiFetch(`/api/erp/orders/${orderId}/report-exception`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function savePackingInfo(
  orderId: string,
  body: { weight_kg?: number | null; dimensions_cm?: string | null; packages?: number | null },
): Promise<{ packing: Record<string, unknown> }> {
  return apiFetch(`/api/erp/orders/${orderId}/packing-info`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Foto (o documento) del embalaje: queda como `shipment_files` (`kind =
 *  foto`) en el almacén de expedición. HEIC/WebP se convierten a JPEG en el
 *  servidor. Errores claros: 400 vacío, 413 más de 15 MB, 415 formato. */
export async function attachDocument(
  orderId: string,
  file: File,
): Promise<{ file: ShipmentFile; document: ShipmentFile }> {
  const form = new FormData();
  form.append("file", file);
  // apiUpload deja que el navegador ponga el boundary multipart (apiFetch
  // forzaría Content-Type JSON y rompería la subida).
  return apiUpload(`/api/erp/orders/${orderId}/attach-document`, form);
}

// --- Bandeja de excepciones + settings (PR 6) -------------------------------

export type ErpExceptionRow = {
  id: string;
  type: string;
  subtype: string | null;
  status: "open" | "in_progress" | "resolved" | "dismissed";
  order_id: string;
  /** D-2: pedido + cliente para identificar la excepción de un vistazo. */
  order_number: string | null;
  contact_name: string | null;
  company_name: string | null;
  metadata: Record<string, unknown>;
  eta_date: string | null;
  eta_overdue: boolean;
  assigned_to_user_id: string | null;
  reported_by_user_id: string | null;
  resolution_note: string | null;
  resolved_at: string | null;
  created_at: string;
};

export const EXCEPTION_TYPE_LABELS: Record<string, string> = {
  stock_shortage: "Falta de stock",
  material_defective: "Material defectuoso",
  sat_issue: "Problema preparación",
  size_exceeds_carrier: "Excede transportista",
  blocked_by_customer_request: "Parada por cliente",
  carrier_incident: "Incidencia transporte",
  returned_by_transport: "Devuelto por transporte",
  factusol_write_failed: "Error escritura FACTUSOL",
  invoice_email_failed: "Email factura fallido",
};

export const EXCEPTION_STATUS_LABELS: Record<string, { label: string; tone: string }> = {
  open: { label: "Abierta", tone: "bad" },
  in_progress: { label: "En curso", tone: "warn" },
  resolved: { label: "Resuelta", tone: "ok" },
  dismissed: { label: "Descartada", tone: "muted" },
};

export async function listExceptions(
  filters: { type?: string; status?: string; assigned?: string } = {},
): Promise<ErpExceptionRow[]> {
  const r = await apiFetch<{ items: ErpExceptionRow[] }>(`/api/erp/exceptions${qs(filters)}`);
  return r.items;
}

export async function assignException(id: string, userId: string | null): Promise<ErpExceptionRow> {
  return apiFetch(`/api/erp/exceptions/${id}/assign`, {
    method: "POST",
    body: JSON.stringify({ assigned_to_user_id: userId }),
  });
}

export async function setExceptionStatus(id: string, status: string): Promise<ErpExceptionRow> {
  return apiFetch(`/api/erp/exceptions/${id}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

export async function resolveException(id: string, note = ""): Promise<ErpExceptionRow> {
  return apiFetch(`/api/erp/exceptions/${id}/resolve`, {
    method: "POST",
    body: JSON.stringify({ resolution_note: note }),
  });
}

export type ErpSettings = {
  default_invoice_mode: "manual" | "auto" | "auto_under_max";
  auto_invoice_max_amount_eur: number | null;
  default_carrier_id: string | null;
  factusol_default_ejercicio: string | null;
  /** Fase C: activa la consulta EN VIVO a FACTUSOL (detección/auto-vinculación
   *  de factura y albarán). Ya no añade bloqueos a la Cola PEDIDOS (C-2-fix3). */
  factusol_live: boolean;
  /** C-2: serie de facturación por defecto (vacío → «A»). */
  factusol_series_default: string;
  /** C-2: override de serie por origen del pedido (o por store_id). */
  factusol_series_by_source: Record<string, string>;
  /** Prefijo de la referencia común (`REFPCL`/`REFALB`/`REFFAC`) que la app
   *  Woo→FACTUSOL pone a los documentos de cada tienda ({"fluxlasers":
   *  "FLE"}). Vacío = se deriva de las 3 primeras letras del nº de pedido
   *  (`FLUXLA-5789` → `FLU`), que puede no coincidir: entonces el F_PCL del
   *  pedido web no se localiza y «PDF del pedido (FACTUSOL)» avisa. */
  factusol_ref_prefix_by_store?: Record<string, string>;
  /** ERP-E2-fix2 — valor de F_PCL.ESTPCL que marca el pedido como facturado
   *  («Enviado» en el escritorio). Confirmado en vivo: "2". */
  factusol_estpcl_invoiced?: string;
  /** E3-B-fix3 — estados con los que se marca el documento de ORIGEN al
   *  convertir (confirmados en el escritorio): ESTPRE 1 = «Aceptado»,
   *  ESTALB 1 = «Facturado». Vacío = no marcar. */
  factusol_estpre_accepted?: string;
  factusol_estalb_invoiced?: string;
  /** ERP-F3 — estado de COBRO de las facturas (F_FAC.ESTFAC). Confirmado:
   *  2 = cobrada, 0 = pendiente. Vacío = no marcar. */
  factusol_estfac_cobrada?: string;
  factusol_estfac_pendiente?: string;
  /** ERP-F3 — marcar la factura como cobrada al emitirla si el pedido ya
   *  constaba pagado (web). Desactivado por defecto (afirmación contable). */
  factusol_auto_mark_paid_when_order_paid?: boolean;
  /** ERP-E4 — identidad fiscal de las empresas emisoras por serie (alimenta
   *  los PDF). Llega ya fusionada con los defaults de los modelos reales. */
  factusol_companies?: Record<string, FactusolCompany>;
  /** ERP-F5 — contrapartidas de cobro (destino del dinero en FACTUSOL;
   *  código → descripción). La tabla no existe en FACTUSOL: vive aquí. */
  contrapartidas?: Contrapartida[];
  /** ERP-F5 — contrapartida PayPal por tienda (heredado: lo sustituyen las
   *  reglas `contrapartida_rules`). */
  paypal_contrapartidas_by_store?: Record<string, string>;
  /** Reglas tienda × método de pago → contrapartida SUGERIDA del cobro, en
   *  orden (la primera que casa). Sin ninguna, la cuenta de la serie. */
  contrapartida_rules?: ContrapartidaRule[];
  /** Menciones legales que se pueden añadir a una factura (observaciones). */
  menciones_legales?: MencionLegal[];
  /** VERI*FACTU activo: no se cambia el cliente de una factura emitida. */
  verifactu_activo?: boolean;
  /** ERP-F6 — orígenes del envío configurables (OFI-TER-SAT del Excel). */
  shipping_origins?: string[];
  /** ERP-F6 — hoja de seguimiento en Drive. El JSON de la cuenta de servicio
   *  es WRITE-ONLY: se envía en el PATCH y nunca vuelve; solo se expone el
   *  client_email para que Bart comparta la hoja con él. */
  drive_spreadsheet_id?: string | null;
  drive_configured?: boolean;
  drive_service_account_email?: string | null;
  drive_service_account_json?: string;
  /** ERP-F6-fix2 — preferir el nº de albarán (si existe) sobre el de pedido
   *  web en la columna de referencia de la hoja. Por defecto true. */
  drive_reference_prefer_albaran?: boolean;
  /** Espejo Seguimiento (Fase 2) — reconcile automático BoHub ↔ hoja en
   *  worker-sync. Apagado por defecto. */
  seguimiento_reconcile_enabled?: boolean;
  /** Cada cuántos minutos corre el reconcile automático (mín. 5; 10 por defecto). */
  seguimiento_reconcile_interval_minutes?: number;
  /** WooCommerce — repaso periódico de pedidos PAGADOS en la tienda que no
   *  están en BoHub (la tienda no siempre dispara el webhook). Encendido por
   *  defecto; cada 60 min (mín. 15); mira 90 días hacia atrás (también
   *  «Poner al día estados Woo…» y el Cuadre). */
  woo_missing_check_enabled?: boolean;
  woo_missing_check_interval_minutes?: number;
  woo_missing_days?: number;
  /** ERP-F6-fix3 — abreviaturas de empresa por serie ({"1":"BO","2":"MQ",
   *  "5":"ST"}) para la columna Empresa del seguimiento. */
  factusol_series_abbreviations?: Record<string, string>;
  /** ERP — remitente (alias de envío) del email de factura por serie = empresa
   *  emisora ({"2":"info@artisjet-printers.eu","5":"pedidos@streamtec.es"}).
   *  Un valor vacío borra el default precargado de esa serie. El envío de la
   *  factura (F-1) usa este alias según la serie; si la serie no lo tiene, cae
   *  al alias por defecto del usuario. */
  factusol_series_email_from?: Record<string, string>;
  /** ERP · remitente del email de factura por TIENDA ({"boprint":
   *  "pedidos@streamtec.es"}). Más fino que la serie: dos tiendas de la misma
   *  empresa emisora pueden enviar desde alias distintos. Manda sobre el de la
   *  serie; un valor vacío borra el default de esa tienda (cae a la serie). */
  factusol_store_email_from?: Record<string, string>;
  /** ERP · plantillas del email de factura por idioma ({"es": {subject, body}}).
   *  Placeholders: {cliente}, {numero} (nº factura), {pedido} (nº de pedido,
   *  con separador; vacío sin pedido) y {referencia} («su ref.»). Vacío = el
   *  default del código para ese idioma. */
  factusol_invoice_email_templates?: Record<string, { subject: string; body: string }>;
  /** Punto A · plantillas del email de PRESUPUESTO / proforma por idioma.
   *  Marcadores: {numero}, {empresa}, {contacto}, {total}, {fecha}, {validez}
   *  y {firma}. Vacío = el default del código para ese idioma. */
  factusol_quote_email_templates?: Record<string, { subject: string; body: string }>;
  /** Aviso de ENVÍO al cliente (nº de seguimiento + enlace) por idioma.
   *  Placeholders: {cliente}, {pedido}, {tracking}, {enlace}, {agencia}. */
  shipment_email_templates?: Record<string, { subject: string; body: string }>;
  /** Remitente del aviso de envío de los pedidos MANUALES (factura, proforma,
   *  albarán, manual, muestra): en español y en cualquier otro idioma. Los
   *  pedidos web salen del remitente de su tienda. */
  shipment_email_from?: { es: string; otros: string };
  /** ERP · email del SAT / taller: destinatario por defecto de «Enviar por
   *  email» desde un pedido. "" = sin destinatario precargado. */
  sat_email?: string;
  /** ERP · Cuadre — job nocturno (interruptor + hora HH:MM de Madrid) y, por
   *  comprobación, si está activa y su umbral en días. */
  cuadre?: CuadreConfig;
  /** ERP · Cuadre — catálogo de comprobaciones (solo lectura; viene en el GET). */
  cuadre_catalogo?: CuadreComprobacion[];
  /** Respuesta a leads (Fase 1) — interruptor, tope diario, umbral, antigüedad,
   *  ventana, mapa interés × idioma → plantilla y remitentes. */
  lead_response?: LeadResponseConfig;
  /** Respuesta a leads — lo que la pantalla necesita para pintar la
   *  configuración (solo lectura; viene en el GET). */
  lead_response_catalogo?: LeadResponseCatalogo;
  /** Base de conocimiento — sincronización diaria del índice de la KB de FLUX
   *  (interruptor + hora) y las palabras que separan «cómo se hace» (KB de
   *  FLUX) de «averiada / repuesto» (nuestro soporte). Las tablas (sitios,
   *  rutas, FLUX) van por su API (`getConocimiento`). */
  conocimiento?: ConocimientoConfig;
  /** Asistente de respuesta («Ayúdame a contestar») — presupuesto de tokens
   *  por generación, cuántos correos viejos y asuntos entran, y los dominios
   *  de correo gratuitos (la empresa se agrupa por su registro, nunca por uno
   *  de estos dominios). */
  asistente?: AsistenteConfig;
  /** Ofertas — prefijo de la numeración (`OF` → `OF-2026-0001`) y validez por
   *  defecto en días (1–365). El PATCH acepta cualquiera de los dos. */
  ofertas?: { prefijo: string; validez_dias: number };
  /** ERP-F6-fix3 — tiendas Woo dadas de alta, para configurar la serie de
   *  cada una (solo lectura; se rellena en el GET). */
  woocommerce_stores?: {
    slug: string;
    label: string;
    /** Prefijo fijado a mano en el metadata_json de la cuenta (manda). */
    ref_prefix_metadata?: string | null;
    /** El que se deriva del nº de pedido si no hay ninguno configurado. */
    derived_ref_prefix?: string;
  }[];
  /** E4-fix1 — almacenes de recogida del albarán de devolución. */
  factusol_pickup_warehouses?: FactusolPickupWarehouse[];
  /** ERP-E2 — nombre de la empresa emisora de cada serie ({"5": "Streamtec"}),
   *  para escribir «serie 5 (Streamtec)» al lado del ajuste. */
  factusol_series_names?: Record<string, string>;
  /** Lote 2 · PR-2 — si el usuario puede guardar (solo ADMIN). La UI desactiva
   *  «Guardar cambios» con el motivo en vez de dejar que el PATCH falle. */
  can_edit?: boolean;
};

/** Lote 2 · PR-2 — plantilla del email de factura rellena con datos de
 *  MUESTRA («Ver ejemplo»): misma sustitución que el envío real. */
export type InvoiceEmailTemplatePreview = {
  lang: string;
  subject: string;
  body_text: string;
  body_html: string;
  /** Desde dónde saldría: el remitente de la serie por defecto, de la primera
   *  tienda con remitente o del usuario. */
  from_alias_example: string;
  from_alias_source: "serie" | "tienda" | "usuario";
  from_alias_scope: string | null;
  /** Datos de muestra con los que se rellenó (factura: cliente/numero/pedido/
   *  referencia; presupuesto: numero/empresa/contacto/total/fecha/validez/firma). */
  sample: Record<string, string>;
};

/** Previsualiza la plantilla de un idioma con datos de muestra. `subject` /
 *  `body` = lo que se está escribiendo (vacío → la guardada / por defecto). */
export async function previewInvoiceEmailTemplate(
  lang: string,
  draft: { subject?: string; body?: string } = {},
): Promise<InvoiceEmailTemplatePreview> {
  return apiFetch<InvoiceEmailTemplatePreview>("/api/erp/settings/invoice-email/preview", {
    method: "POST",
    body: JSON.stringify({ lang, subject: draft.subject ?? null, body: draft.body ?? null }),
  });
}

/** Lote 2 · PR-2 — resultado de «Enviarme una prueba» (sin PDF; el asunto va
 *  precedido de «[Prueba]»). */
export type InvoiceEmailTemplateTestResult = {
  sent: true;
  to: string;
  lang: string;
  subject: string;
  from_alias: string;
  from_alias_source: "serie" | "tienda" | "usuario";
  from_alias_scope: string | null;
  message_id: string;
};

/** Envía la plantilla rellena con datos de muestra al propio usuario (o a
 *  `to`) desde el remitente configurado. Solo ADMIN; 403 con motivo si el
 *  remitente no es un «enviar como» utilizable. */
export async function sendInvoiceEmailTemplateTest(
  lang: string,
  draft: { subject?: string; body?: string; to?: string } = {},
): Promise<InvoiceEmailTemplateTestResult> {
  return apiFetch<InvoiceEmailTemplateTestResult>("/api/erp/settings/invoice-email/test-send", {
    method: "POST",
    body: JSON.stringify({
      lang, subject: draft.subject ?? null, body: draft.body ?? null, to: draft.to ?? null,
    }),
  });
}

/** Aviso de envío al cliente: plantilla de un idioma con datos de muestra. */
export async function previewShipmentEmailTemplate(
  lang: string,
  draft: { subject?: string; body?: string } = {},
): Promise<InvoiceEmailTemplatePreview> {
  return apiFetch<InvoiceEmailTemplatePreview>("/api/erp/settings/shipment-email/preview", {
    method: "POST",
    body: JSON.stringify({ lang, subject: draft.subject ?? null, body: draft.body ?? null }),
  });
}

/** Aviso de envío al cliente: «Enviarme una prueba» (datos de muestra). */
export async function sendShipmentEmailTemplateTest(
  lang: string,
  draft: { subject?: string; body?: string; to?: string } = {},
): Promise<InvoiceEmailTemplateTestResult> {
  return apiFetch<InvoiceEmailTemplateTestResult>("/api/erp/settings/shipment-email/test-send", {
    method: "POST",
    body: JSON.stringify({
      lang, subject: draft.subject ?? null, body: draft.body ?? null, to: draft.to ?? null,
    }),
  });
}

/** Lote 2 · PR-2 — lo que se compondrá con los ajustes actuales: siguiente
 *  nº de pedido manual y, por tienda Woo, el prefijo efectivo con la
 *  siguiente referencia de ejemplo. `next_number` permite recomponer la
 *  referencia en vivo con el prefijo que se esté escribiendo. */
export type ErpNextReferences = {
  manual_next: string;
  stores: {
    slug: string;
    label: string;
    prefix: string;
    prefix_source: "cuenta" | "ajustes" | "derivado";
    next_number: number;
    /** false = nunca hubo pedidos de esa tienda y se enseña 000001. */
    next_number_known: boolean;
    example_ref: string;
  }[];
};

export async function getErpNextReferences(): Promise<ErpNextReferences> {
  return apiFetch<ErpNextReferences>("/api/erp/settings/next-references");
}

/** ERP-E4 — identidad fiscal de una empresa emisora (serie). Los textos
 *  legales van por idioma ({es, en, …}). `logo` es de solo lectura (se sube
 *  aparte). */
/** E4-fix1 — cuenta bancaria de una empresa emisora. `defecto` marca la
 *  preseleccionada al descargar. */
export type FactusolBankAccount = {
  nombre: string;
  domicilio: string;
  iban: string;
  bic: string;
  defecto: boolean;
};

export type FactusolCompany = {
  nombre: string;
  direccion: string;
  cp_poblacion: string;
  pais: string;
  /** País en ISO2 («ES», «BE»): **decide el IVA** de lo que emite esta serie
   *  (la pareja emisor → cliente). Se guarda explícito porque deducirlo del
   *  literal `pais` sería adivinar, y aquí adivinar es facturar mal. */
  pais_iso2: string;
  telefono: string;
  email: string;
  nif: string;
  /** E4-fix1 — idioma por defecto de la empresa emisora (nivel 4 de la
   *  cascada de idioma). */
  idioma_defecto: string;
  /** E4-fix1 — el banco ya no es fijo: lista de cuentas, una por defecto. */
  bancos: FactusolBankAccount[];
  legal: Record<string, string>;
  pie: Record<string, string>;
  intracom: Record<string, string>;
  /** E4-fix1 — título de la variante VALORADA del albarán, por idioma. */
  titulo_albaran_valorado: Record<string, string>;
  logo?: boolean;
  /** ERP-F3 — nombre del fichero de logo actual (para la miniatura); null si
   *  no hay logo. Solo lectura. */
  logo_filename?: string | null;
};

/** E4-fix1 — almacén de recogida del albarán de devolución. */
export type FactusolPickupWarehouse = { nombre: string; direccion: string };

export async function getErpSettings(): Promise<ErpSettings> {
  return apiFetch<ErpSettings>("/api/erp/settings");
}

export async function updateErpSettings(patch: Partial<ErpSettings>): Promise<ErpSettings> {
  return apiFetch<ErpSettings>("/api/erp/settings", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

/** ERP-F5 — contrapartida de cobro de FACTUSOL (destino del dinero). */
export type Contrapartida = { codigo: string; nombre: string };

/** Regla de contrapartida sugerida: tienda («» = todas) + método de pago de
 *  Woo (id del gateway o título; también la forma de pago de FACTUSOL),
 *  coincidencia exacta o «contiene» sin mayúsculas → código de contrapartida. */
export type ContrapartidaRule = {
  tienda: string;
  metodo: string;
  coincidencia: "exacta" | "contiene";
  contrapartida: string;
};

export async function getContrapartidas(): Promise<Contrapartida[]> {
  const r = await apiFetch<{ items: Contrapartida[] }>("/api/erp/catalogs/contrapartidas");
  return r.items;
}

// --- WooCommerce multi-tienda admin (Fase B PR B-2) --------------------------

export type WooWebhookSummary = {
  last_received_at: string | null;
  count_24h: number;
  errors_24h: number;
};

export type WooStore = {
  id: string;
  account_id: string;
  display_name: string;
  base_url: string;
  enabled: boolean;
  credential_status: string;
  /** B-2-fix4: fecha de corte; pedidos anteriores se auto-marcan como
   *  procesados externamente al importar (ISO 8601 o YYYY-MM-DD). */
  external_cutoff_date: string | null;
  /** B-3: resumen de webhooks para la tabla (última recepción + 24h). */
  webhook_summary: WooWebhookSummary;
};

export type WooWebhookStatus = {
  webhook_url: string;
  webhook_secret_last4: string;
  last_received_at: string | null;
  count_24h: number;
  errors_24h: number;
  topics_received_24h: string[];
};

export type WooStoreCreate = {
  account_id: string;
  display_name: string;
  base_url: string;
  consumer_key: string;
  consumer_secret: string;
  enabled?: boolean;
  external_cutoff_date?: string | null;
};

export type WooStoreUpdate = Partial<Omit<WooStoreCreate, "account_id">>;

export async function listWooStores(): Promise<WooStore[]> {
  const r = await apiFetch<{ items: WooStore[] }>("/api/erp/integrations/woocommerce/stores");
  return r.items;
}

export async function createWooStore(payload: WooStoreCreate): Promise<WooStore> {
  return apiFetch("/api/erp/integrations/woocommerce/stores", {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function updateWooStore(id: string, patch: WooStoreUpdate): Promise<WooStore> {
  return apiFetch(`/api/erp/integrations/woocommerce/stores/${id}`, {
    method: "PATCH", body: JSON.stringify(patch),
  });
}

export async function testWooStore(id: string): Promise<{ ok: boolean; status?: number; detail?: string }> {
  return apiFetch(`/api/erp/integrations/woocommerce/stores/${id}/test-connection`, {
    method: "POST",
  });
}

export async function syncWooBackfill(
  id: string, sinceIso?: string,
): Promise<{ ok: boolean; queued?: boolean; job_id?: string; outcome?: Record<string, unknown> }> {
  const q = sinceIso ? `?since_iso=${encodeURIComponent(sinceIso)}` : "";
  return apiFetch(`/api/erp/integrations/woocommerce/stores/${id}/sync-backfill${q}`, {
    method: "POST",
  });
}

// --- webhooks (Fase B PR B-3) -----------------------------------------------

export async function getWooWebhookStatus(id: string): Promise<WooWebhookStatus> {
  return apiFetch(`/api/erp/integrations/woocommerce/stores/${id}/webhook-status`);
}

export async function regenerateWooWebhookSecret(
  id: string,
): Promise<{ webhook_secret: string; webhook_url: string }> {
  return apiFetch(
    `/api/erp/integrations/woocommerce/stores/${id}/regenerate-webhook-secret`,
    { method: "POST" },
  );
}

// --- FACTUSOL emisión de factura (Fase C · C-2 / C-2-fix2) -------------------

export type FactusolInvoiceStatus =
  | { status: "invoiced"; codfac: string }
  | { status: "pending" }
  /** `error`: solo el mensaje (la traza se queda en el log del servidor).
   *  `code` «pedido_no_en_factusol»: el pedido web aún no lo ha importado la
   *  app WooCommerce→FACTUSOL (la ficha ofrece «Volver a comprobar»). */
  | { status: "failed"; error?: string; code?: string };

/** «Volver a comprobar»: ¿la app WooCommerce→FACTUSOL ya importó el pedido? */
export async function checkFactusolPedido(
  orderId: string,
): Promise<{ en_factusol: boolean; detail: string | null }> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/factusol-pedido-check`);
}

/** Estado del pedido frente a FACTUSOL (C-2-fix2): consulta en vivo si ya
 *  existe factura (auto-vinculada) o albarán. `unknown` = no se pudo consultar
 *  (factusol_live off o FACTUSOL no responde) → se cae al botón de emisión. */
export type FactusolStatus =
  | { status: "invoiced"; codfac: string; ref?: string; auto_linked?: boolean }
  | { status: "albaran"; ref?: string; albaran_codigo?: string | null }
  | { status: "pending"; ref?: string }
  | { status: "already_invoiced_externally" }
  | { status: "unknown"; reason?: string };

/** Opciones de emisión del modal (como el diálogo «Nueva factura» del
 *  escritorio FACTUSOL). Todas opcionales. */
export type EmitFactusolOptions = {
  tipfac?: string;
  /** Mención legal (Configuración ERP) o texto libre → primera observación
   *  de la factura. */
  mencion_id?: string | null;
  mencion_texto?: string | null;
  /** ERP-E2 — empresa emisora. NO es una columna de F_FAC: determina el rango
   *  de numeración del CODFAC (serie 5 ⇒ 5xxxxx). Omitir → la resuelve el
   *  backend (override por origen → default de ajustes → 5 Streamtec). */
  serie?: number | null;
  /** Fecha de emisión ISO `YYYY-MM-DD`; omitir → hoy (lo pone el backend). */
  fecfac?: string | null;
  fopfac?: string | null;
  comfac?: string | null;
};

export type FormaPago = { codigo: string | null; nombre: string };

/** Cómo consta enviada la factura al cliente. */
export type InvoiceSent = {
  at: string;
  via: "email" | "manual" | "hoja";
  /** Quién lo hizo (email o marca a mano). */
  by?: string | null;
  to?: string[];
  medio?: FacturaEnviadaMedio | null;
  nota?: string | null;
  marked_at?: string | null;
};

export type FacturaEnviadaMedio = "papel" | "gmail" | "albaran" | "otro";

export const FACTURA_ENVIADA_MEDIO_LABEL: Record<FacturaEnviadaMedio, string> = {
  papel: "En papel",
  gmail: "Desde Gmail",
  albaran: "Con el albarán",
  otro: "Por otro medio",
};

/** «Ya enviada»: marca a mano que la factura llegó al cliente por fuera de
 *  BoHub. Queda en Auditoría. Devuelve la ficha. */
export async function marcarFacturaEnviada(
  orderId: string,
  body: { fecha?: string | null; medio: FacturaEnviadaMedio; nota?: string | null },
): Promise<OrderDetail> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/factura-enviada`, {
    method: "POST", body: JSON.stringify({ ...body, confirm: true }),
  });
}

export async function desmarcarFacturaEnviada(orderId: string): Promise<OrderDetail> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/factura-enviada/deshacer`, {
    method: "POST",
  });
}

/** Serie de facturación = empresa emisora (ERP-E2). */
export type FactusolSerie = {
  serie: number;
  nombre: string;
  is_default: boolean;
  /** `false` = serie sin nombre asignado; la UI las ordena al final. */
  is_known: boolean;
};

export async function emitFactusolInvoice(
  orderId: string, options?: EmitFactusolOptions,
): Promise<{ job_id: string; order_id: string; status: string }> {
  return apiFetch(`/api/erp/orders/${orderId}/emit-factusol-invoice`, {
    method: "POST",
    body: JSON.stringify(options ?? {}),
  });
}

export async function getFactusolInvoiceStatus(
  orderId: string, jobId?: string,
): Promise<FactusolInvoiceStatus> {
  const q = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
  return apiFetch(`/api/erp/orders/${orderId}/factusol-invoice-status${q}`);
}

export async function getFactusolStatus(orderId: string): Promise<FactusolStatus> {
  return apiFetch(`/api/erp/orders/${orderId}/factusol-status`);
}

export async function getFactusolFormasPago(): Promise<FormaPago[]> {
  const r = await apiFetch<{ items: FormaPago[] }>("/api/erp/factusol/formas-pago");
  return r.items;
}

export async function getFactusolSeries(): Promise<{
  items: FactusolSerie[];
  default: number;
}> {
  return apiFetch("/api/erp/factusol/series");
}


// --- explorador de documentos FACTUSOL (ERP-E3-A, solo lectura) --------------

export type FactusolDocType =
  | "pedidos" | "presupuestos" | "albaranes" | "facturas";

/** Cabecera normalizada de un documento FACTUSOL (lectura en vivo). Los
 *  campos pueden venir a null cuando la columna no existe en la tabla
 *  (F_ALB aún sin confirmar) — la UI pinta «—». */
export type FactusolDocument = {
  doc_type: FactusolDocType;
  codigo: number | string | null;
  serie: number | null;
  /** `TIPO-CÓDIGO` como lo muestra el escritorio (`5-000005`). */
  numero: string;
  cliente_codigo: string | null;
  cliente_nombre: string | null;
  fecha: string | null;
  total: number | null;
  estado: string | null;
  estado_label: string;
  referencia: string | null;
  /** Código de forma de pago (F_FOP), p. ej. "002". */
  forma_pago: string | null;
  /** E4 — país del cliente (para deducir el idioma por defecto del PDF). */
  cliente_pais?: string | null;
  /** Nombre del país del cliente (CPA* guarda el código numérico, «276»). */
  cliente_pais_nombre?: string | null;
  /** E3-B — posición en el ciclo PRE→ALB→FAC. `null`/ausente = el backend
   *  no pudo cargar el índice (anotación best-effort). */
  ciclo?: FactusolCycle;
  /** F3-fix1 — solo facturas: saldo pendiente de cobro (total − cobrado),
   *  leído de F_LCO. `undefined` si el backend no pudo anotar los cobros. */
  saldo_pendiente?: number | null;
  total_cobrado?: number | null;
  /** Fase 5 — cruce con el CRM (mismo criterio que proformas/ficha): empresa
   *  vinculada por CODCLI, país (ISO2) y régimen de IVA para la pastilla, y el
   *  pedido de BoHub si el documento ya se importó (presupuestos/pedidos).
   *  Ausentes en respuestas antiguas / mocks. */
  company?: { id: string; name: string; country: string | null; factusol_id: string | null } | null;
  country_iso2?: string | null;
  regime?: "nacional" | "intracomunitario" | "exportacion" | null;
  regime_label?: string | null;
  regime_source?: "empresa" | "cabecera" | null;
  exento?: boolean;
  order?: { id: string; order_number: string } | null;
  /** Tono de la pastilla de estado (`ok`/`warn`/`muted`…) derivado del mismo
   *  criterio del escritorio (ESTFAC/ESTALB/ESTPRE/ESTPCL). */
  estado_tone?: string;
  /** Punto A (solo presupuestos): último envío por email, si lo hubo. */
  emailed_at?: string | null;
  emailed_to?: string[] | null;
};

/** F3-fix1 — un cobro de la factura (F_LCO), solo lectura. */
export type FactusolCobro = {
  linea: number | null;
  fecha: string | null;
  importe: number | null;
  /** ERP-F5 — código de CONTRAPARTIDA de cobro (F_LCO.CPALCO: destino del
   *  dinero, p. ej. 8 = Streamtec Sabadell). No es la forma de pago. */
  contrapartida: string | null;
  /** Nombre de la contrapartida resuelto del catálogo configurable. */
  contrapartida_nombre?: string | null;
  concepto: string | null;
};

/** Referencia a otro documento del ciclo (hijo u origen), con lo justo para
 *  abrir su detalle. */
export type FactusolCycleRef = {
  doc_type: FactusolDocType;
  serie: number;
  codigo: number;
  numero: string;
};

/** E3-B — enlaces del documento en el ciclo PRE→ALB→FAC, leídos de los
 *  campos `DOC/DTP/DCO` de las líneas de F_LAL/F_LFA. `estado` solo aplica a
 *  presupuestos/pedidos (¿facturado?) y albaranes; en facturas es null.
 *  `estado_label` (E3-B-fix1): etiqueta con la semántica del TIPO — un
 *  albarán «pendiente» es «Sin facturar», nunca «sin albarán». */
export type FactusolCycle = {
  albaranes: FactusolCycleRef[];
  facturas: FactusolCycleRef[];
  origen: FactusolCycleRef[];
  estado: "pendiente" | "con_albaran" | "facturado" | null;
  estado_label?: string | null;
} | null;

/** E4-fix1 — idioma sugerido para el PDF (cascada) con su procedencia, para
 *  preseleccionar el selector indicando si es dato real o suposición. */
export type FactusolPdfLangSuggestion = {
  lang: FactusolPdfLang;
  // E4-fix2: `pais_cliente` = idioma deducido del país de la empresa
  // cliente (distinto de `cliente`, que es el idioma puesto a mano).
  // F1-fix2: `pais_documento` = país del cliente EN EL DOCUMENTO (CPA*).
  source:
    | "pedido" | "cliente" | "pais_documento" | "pais_cliente"
    | "empresa" | "defecto";
};

export type FactusolDocumentDetail = FactusolDocument & {
  /** Nombre de la forma de pago resuelto del catálogo F_FOP; null si el
   *  documento no la trae o el catálogo no la conoce. */
  forma_pago_nombre: string | null;
  /** E4-fix1 — idioma propuesto para el PDF con su origen. */
  pdf_lang?: FactusolPdfLangSuggestion;
  /** F3-fix1 — solo facturas: lista de cobros registrados en F_LCO. */
  cobros?: FactusolCobro[];
  lines: {
    position: number;
    codart: string | null;
    description: string;
    quantity: number;
    unit_price: number;
    line_total: number;
  }[];
};

export type FactusolDocumentSort =
  | "numero" | "cliente" | "fecha" | "total" | "saldo";

export type FactusolDocumentFilters = {
  codcli?: string;
  /** E3-A-fix1 — término libre que el backend resuelve contra F_CLI por
   *  nombre, CIF (NIFCLI) o email (EMACLI). */
  cliente_q?: string;
  sort?: FactusolDocumentSort;
  dir?: "asc" | "desc";
  serie?: number;
  estado?: string;
  fecha_desde?: string;
  fecha_hasta?: string;
  q?: string;
  /** E3-B — filtra por el estado del ciclo (antes de paginar). */
  ciclo?: "pendiente" | "con_albaran" | "facturado";
  /** E3-B-fix1 — salta el cache del índice del ciclo (tras crear un
   *  documento, para que la columna CICLO se repinte al momento). */
  fresh_ciclo?: boolean;
  /** Lote 2 · PR-2 — `false` = «solo sin vincular» (sin pedido de BoHub);
   *  `true` = solo los vinculados. Se aplica antes de paginar. */
  linked?: boolean;
  /** El ejercicio de FACTUSOL que se lee (por defecto, el en curso). */
  ejercicio?: string;
  limit?: number;
  offset?: number;
};

/** Lote 2 · PR-2 — respuesta del listado: además de la página, el contador
 *  de «sin vincular» (sobre el resto de filtros, sin aplicar `linked`) y la
 *  marca de tiempo de la lectura en vivo para «sincronizado hace X». */
export type FactusolDocumentList = {
  items: FactusolDocument[];
  total: number;
  /** Documentos SIN pedido de BoHub entre los que casan los filtros. */
  unlinked_total?: number;
  /** Hora del servidor (ISO) en que se leyó FACTUSOL. */
  fetched_at?: string | null;
  /** Antigüedad (s) del índice del ciclo cacheado; `null` si no cargó. */
  cycle_index_age_seconds?: number | null;
  /** El ejercicio leído y el en curso. */
  ejercicio?: string;
  ejercicio_en_curso?: string;
};

/** El ejercicio en curso (el de FACTUSOL, Configuración ERP) y los que ofrece
 *  el selector, del en curso hacia atrás. */
export type Ejercicios = { en_curso: string; ejercicios: string[] };

export async function getEjercicios(): Promise<Ejercicios> {
  return apiFetch("/api/erp/factusol/ejercicios");
}

export async function listFactusolDocuments(
  docType: FactusolDocType, filters: FactusolDocumentFilters = {},
): Promise<FactusolDocumentList> {
  const { fresh_ciclo, linked, ...rest } = filters;
  const query = qs({
    ...rest,
    fresh_ciclo: fresh_ciclo ? "1" : undefined,
    linked: linked === undefined ? undefined : String(linked),
  });
  return apiFetch(`/api/erp/factusol/documents/${docType}${query}`);
}

// --- vincular albarán / factura a un pedido de BoHub (Lote 2 · PR-2) -------

/** Un pedido sugerido para vincular. `match`: `referencia` (su referencia
 *  común es la REFFAC/REFALB del documento, fuerte), `cliente` (misma
 *  empresa, débil) o `busqueda` (por nº de pedido). `current_link` es el
 *  documento de ESE tipo que el pedido ya tiene vinculado (aviso). */
export type FactusolLinkCandidate = {
  id: string;
  order_number: string;
  company_name: string | null;
  total_amount: number;
  placed_at: string | null;
  external_source: string;
  current_link: string | null;
  match: "referencia" | "cliente" | "busqueda";
};

export type FactusolLinkCandidates = {
  doc: {
    doc_type: FactusolDocType; serie: number; codigo: number; numero: string;
    referencia: string | null; cliente_codigo: string | null;
    cliente_nombre: string | null; fecha: string | null; total: number | null;
  };
  /** Pedidos que YA apuntan a este documento. */
  linked_orders: { id: string; order_number: string }[];
  candidates: FactusolLinkCandidate[];
  q: string | null;
};

/** Sugerencias de pedido para vincular (solo lectura). `q` busca por nº. */
export async function getDocumentLinkCandidates(
  docType: FactusolDocType, serie: number, codigo: number | string, q?: string,
): Promise<FactusolLinkCandidates> {
  const query = qs({ q: q?.trim() || undefined });
  return apiFetch(
    `/api/erp/factusol/documents/${docType}/${serie}/${codigo}/link-candidates${query}`,
  );
}

/** Vincula el albarán / la factura al pedido: SOLO escribe los campos del
 *  pedido en BoHub (FACTUSOL no se toca). `force` salta el 409 cuando el
 *  pedido ya tenía otro documento de ese tipo o el documento ya apuntaba a
 *  otro pedido. */
export async function linkDocumentToOrder(
  docType: FactusolDocType, serie: number, codigo: number | string,
  payload: { order_id: string; confirm: boolean; force?: boolean },
): Promise<{
  status: "linked";
  doc: { doc_type: FactusolDocType; serie: number; codigo: number; numero: string };
  order: { id: string; order_number: string };
  previous: string | null;
}> {
  return apiFetch(
    `/api/erp/factusol/documents/${docType}/${serie}/${codigo}/link-order`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export async function getFactusolDocument(
  docType: FactusolDocType, serie: number, codigo: number | string,
  opts?: { fresh?: boolean },
): Promise<FactusolDocumentDetail> {
  const query = opts?.fresh ? "?fresh_ciclo=1" : "";
  return apiFetch(
    `/api/erp/factusol/documents/${docType}/${serie}/${codigo}${query}`,
  );
}

// --- crear la cadena de documentos (ERP-E3-B) --------------------------------

export type FactusolConvertTarget = "albaranes" | "facturas";

/** Encola crear albarán/factura desde el documento (202 + job_id). El 409
 *  `already_converted` llega con `existing` (números de los hijos que ya
 *  apuntan al origen); reintentar con `force: true` crea el segundo. */
export async function convertFactusolDocument(
  docType: FactusolDocType, serie: number, codigo: number | string,
  payload: {
    target: FactusolConvertTarget;
    serie?: number | null;
    fecha?: string | null;
    force?: boolean;
  },
): Promise<{ job_id: string; status: string }> {
  return apiFetch(
    `/api/erp/factusol/documents/${docType}/${serie}/${codigo}/convert`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export type FactusolConvertStatus =
  | { status: "pending" }
  | {
      status: "finished";
      result: {
        target_type: FactusolDocType;
        serie: number;
        codigo: number;
        numero: string;
        lines: number;
        /** E3-B-fix3 — ¿quedó el documento de ORIGEN marcado como
         *  convertido (ESTPRE aceptado / ESTALB facturado)? Si no,
         *  `origin_mark_warning` trae el aviso legible: el hijo existe
         *  igualmente, esto NO es un error de la conversión. */
        origin_marked?: boolean;
        origin_mark_warning?: string | null;
      };
    }
  | { status: "failed"; error?: string; code?: string };

export async function getFactusolConvertStatus(
  jobId: string,
): Promise<FactusolConvertStatus> {
  return apiFetch(
    `/api/erp/factusol/documents/convert-status/${encodeURIComponent(jobId)}`,
  );
}

// --- PDF de documentos (ERP-E4) ---------------------------------------------

export type FactusolPdfLang = "es" | "en" | "de" | "fr" | "nl";

/** E4-fix1 — variantes de impresión del mismo documento (nunca crean nada
 *  en FACTUSOL). */
export type FactusolPdfVariant =
  | "anticipo" | "proforma" | "valorado" | "devolucion";

export type FactusolPdfOptions = {
  variant?: FactusolPdfVariant;
  /** Índice de la cuenta bancaria de la empresa (default: la marcada). */
  bank?: number;
  /** Código ISO de divisa (solo cambia la presentación; no convierte). */
  currency?: string;
  /** Índice del almacén de recogida (albarán de devolución). */
  warehouse?: number;
  /** Otro ejercicio de FACTUSOL (consulta desde un ejercicio anterior). */
  ejercicio?: string;
};

/** PDF del documento, generado por BoHub (la API de DELSOL no imprime). */
export async function downloadFactusolDocumentPdf(
  docType: FactusolDocType, serie: number, codigo: number | string,
  lang: FactusolPdfLang = "es", opts: FactusolPdfOptions = {},
): Promise<Blob> {
  const query = qs({
    lang,
    variant: opts.variant,
    bank: opts.bank,
    currency: opts.currency,
    warehouse: opts.warehouse,
    ejercicio: opts.ejercicio,
  });
  return apiDownloadBlob(
    `/api/erp/factusol/documents/${docType}/${serie}/${codigo}/pdf${query}`,
  );
}

/** ZIP con los PDF de varias facturas (selección múltiple), por serie+número.
 *  Solo lectura: genera y empaqueta, no marca ni envía. Las que no existen se
 *  omiten (van en `_no_encontradas.txt` dentro del ZIP). */
export async function downloadFacturasPdfZip(
  items: { serie: number; codigo: number; lang?: FactusolPdfLang }[],
  lang?: FactusolPdfLang,
): Promise<Blob> {
  return apiDownloadBlob("/api/erp/factusol/documents/facturas/pdf-zip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, lang }),
  });
}

/** PDF del pedido de cliente (F_PCL) vinculado a un pedido del CRM. */
export async function downloadOrderFactusolPedidoPdf(
  orderId: string, lang: FactusolPdfLang = "es",
): Promise<Blob> {
  return apiDownloadBlob(
    `/api/erp/orders/${orderId}/factusol-pedido-pdf?lang=${lang}`,
  );
}

/** Fase 2 — PDF del albarán que BoHub creó en FACTUSOL para el pedido
 *  (`orders.factusol_albaran_number`), con el mismo motor que el PDF del
 *  pedido y de la factura (BoHub lo compone; DELSOL no imprime). 404 con
 *  código propio si el pedido no tiene albarán. */
export async function downloadOrderFactusolAlbaranPdf(
  orderId: string, lang: FactusolPdfLang = "es",
  variant?: "valorado" | "devolucion",
): Promise<Blob> {
  const query = qs({ lang, variant });
  return apiDownloadBlob(`/api/erp/orders/${orderId}/factusol-albaran-pdf${query}`);
}

export async function uploadFactusolCompanyLogo(
  serie: number | string, file: File,
): Promise<{ serie: number; logo: boolean }> {
  const form = new FormData();
  form.append("file", file);
  return apiUpload(`/api/erp/factusol/companies/${serie}/logo`, form);
}

/** ERP-F3 — descarga el logo actual de la empresa (para la miniatura). */
export async function downloadFactusolCompanyLogo(
  serie: number | string,
): Promise<Blob> {
  return apiDownloadBlob(`/api/erp/factusol/companies/${serie}/logo`);
}

/** ERP-F3 — quita el logo de la empresa. */
export async function deleteFactusolCompanyLogo(
  serie: number | string,
): Promise<{ serie: number; logo: boolean }> {
  return apiFetch(`/api/erp/factusol/companies/${serie}/logo`, {
    method: "DELETE",
  });
}

// --- ERP-F3 · estado de cobro de facturas -----------------------------------

/** Respuesta del POST de marcado: `already` (ya estaba en ese estado, sin
 *  encolar) o `queued` (job en marcha). Trae cliente/importe para el aviso. */
export type InvoicePaymentResponse = {
  status: "already" | "queued";
  paid: boolean;
  numero: string;
  cliente: string;
  importe: number | null;
  referencia?: string;
  job_id?: string;
  estfac?: string;
};

export type InvoicePaymentJobStatus =
  | { status: "pending" }
  | {
      status: "finished";
      result: {
        marked: boolean; motivo: string | null; numero: string;
        paid: boolean; serie: number; codigo: number;
      };
    }
  | { status: "failed"; error?: string; code?: string };

/** Marca la factura como cobrada (`paid: true`) o pendiente (`paid: false`).
 *  `confirm` OBLIGATORIO — el backend lo exige (afirmación contable). */
export async function markInvoicePayment(
  serie: number, codigo: number | string,
  body: { confirm: boolean; paid: boolean },
): Promise<InvoicePaymentResponse> {
  return apiFetch(
    `/api/erp/factusol/documents/facturas/${serie}/${codigo}/payment`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function getInvoicePaymentStatus(
  jobId: string,
): Promise<InvoicePaymentJobStatus> {
  return apiFetch(
    `/api/erp/factusol/documents/facturas/payment-status/${encodeURIComponent(jobId)}`,
  );
}

/** Espera a que el job de marcado termine (o `pending` si se agota el margen). */
export async function waitForInvoicePaymentJob(
  jobId: string, { tries = 20, delayMs = 1500 } = {},
): Promise<InvoicePaymentJobStatus> {
  let last: InvoicePaymentJobStatus = { status: "pending" };
  for (let i = 0; i < tries; i++) {
    last = await getInvoicePaymentStatus(jobId);
    if (last.status !== "pending") return last;
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return last;
}

// --- Cobro MANUAL desde la app (ficha + bandeja) — motor F-4-B tal cual -------

export type FactusolCobroStatus = "cobrada" | "pendiente";

/** Último estado de cobro comprobado, persistido en el pedido. */
export type FactusolCobroBlock = {
  numero: string;
  serie: number;
  codigo: number;
  total: number | null;
  total_cobrado: number | null;
  saldo_pendiente: number | null;
  estfac: string | null;
  cobros: number | null;
  fopfac?: string | null;
  cobrada: boolean;
  checked_at: string;
  source: string;
};

/** `GET /orders/{id}/factusol-cobro` — estado EN VIVO de la factura del
 *  pedido para la ficha y el modal. `sin_factura` = el botón se deshabilita
 *  («emite la factura primero»); `unresolved` / `not_found` = hay CODFAC pero
 *  no se localiza su fila (no se escribe nada). */
export type OrderCobroInfo = {
  /** Ausentes cuando el estado se pide por factura (serie+número) desde el
   *  explorador de documentos, sin pedido de BoHub (`getFactusolFacturaCobro`). */
  order_id?: string;
  order_number?: string;
  /** Fase 5 — solo en la variante por factura: nº «serie-código». */
  numero?: string;
  serie?: number;
  codigo?: number;
  status: FactusolCobroStatus | "sin_factura" | "unresolved" | "not_found";
  invoice: { serie: number | null; codigo: number | null; numero: string } | null;
  detail?: string;
  cliente?: string;
  referencia?: string;
  total?: number;
  total_cobrado?: number;
  saldo_pendiente?: number;
  estfac?: string;
  /** Líneas de cobro que YA tiene la factura (aviso de posible doble cobro). */
  cobros?: number;
  fopfac?: string;
  forma_pago_nombre?: string | null;
  /** Cuenta sugerida por defecto (serie / empresa emisora, PayPal por tienda,
   *  o la que se apuntó en el pedido al pagar — Bloque B). */
  suggested_cuenta?: Contrapartida | null;
  /** Por qué se sugiere esa cuenta («tienda Artisjet Europe · método Carte»,
   *  «cuenta de la serie 2», «pago apuntado en el pedido»). */
  suggested_reason?: string | null;
  /** Método de pago del pedido web (título en la tienda). */
  payment_method_title?: string | null;
  /** Fecha sugerida (Bloque B): la del pago apuntado en el pedido, si la hay,
   *  para prellenar «Registrar cobro»; ausente → el modal usa hoy. */
  suggested_fecha?: string | null;
  warnings?: string[];
  checked_at?: string;
  persisted_status?: FactusolCobroStatus | null;
  /** Cobros de la factura que registró BoHub (se pueden anular / corregir). */
  bohub_cobros?: BohubCobro[];
  /** Pedido y factura con totales distintos (portes, líneas cambiadas…). */
  total_mismatch?: { pedido: number; factura: number; diferencia: number } | null;
  /** El diálogo único (29/10/2026): la factura no tiene pedido de BoHub. */
  externa?: boolean;
  /** El movimiento del banco identificado como su cobro (o el pedido). */
  movimiento?: CobroMovimiento | null;
  /** Si al cobrar con ese movimiento también se escribe en la hoja de Drive. */
  hoja?: { cuenta: string; pestana: string | null; factura: string } | null;
  /** Contra qué se cobró: sus líneas de cobro y el movimiento de cada una. */
  cobrado_con?: {
    fecha: string | null; importe: number; cuenta: string | null;
    movimiento: CobroBancoMovimiento | null;
  }[];
};

/** El movimiento con el que se va a cobrar, en el diálogo único. */
export type CobroMovimiento = CobroBancoMovimiento & {
  /** Lo que paga de ESTA factura (su parte si está repartido). */
  importe_factura: number;
  conciliado: boolean;
  /** Su cobro ya está en FACTUSOL: solo se concilia. */
  ya_en_factusol: boolean;
};

/** Lo que pasó en la hoja de Drive al cobrar con un movimiento. */
export type CobroHoja = {
  cuenta: string;
  pestana: string | null;
  estado: "escrita" | "ocupada" | "no_encontrada" | "corrida" | "sin_columna" | "en_cola"
    | "no_se_escribe" | "error" | "pendiente" | string;
  detalle?: string;
  factura?: string | null;
};

/** Un cobro que registró BoHub en FACTUSOL (según su auditoría): su línea de
 *  F_LCO, fecha, importe y contrapartida. `anulable` = se puede anular /
 *  corregir (no se anuló ya). Los cobros hechos a mano en FACTUSOL no salen. */
export type BohubCobro = {
  id: string;
  numero: string;
  serie: number;
  codigo: number;
  linlco: number | null;
  fecha: string | null;
  importe: number | null;
  contrapartida: string | null;
  contrapartida_nombre: string | null;
  registrado_at: string | null;
  registrado_por: string | null;
  anulado: boolean;
  anulado_at: string | null;
  anulable: boolean;
};

/** Resultado del job de anular (y, si es «Corregir», del nuevo cobro). */
export type AnnulCobroJobResult = {
  annulled: boolean;
  status: string;
  motivo?: string | null;
  numero?: string;
  saldo_pendiente?: number;
  estfac?: string;
  correccion?: { registered: boolean; status: string; motivo?: string | null;
                 importe?: number; saldo_pendiente?: number; parcial?: boolean } | null;
};

/** «Anular cobro» (solo los de BoHub): encola borrar SU línea de F_LCO y dejar
 *  la factura con el estado de lo que quede cobrado. */
export async function annulOrderCobro(
  orderId: string, eventId: string,
): Promise<{ status: "queued"; job_id: string; cobro: BohubCobro }> {
  return apiFetch(
    `/api/erp/orders/${encodeURIComponent(orderId)}/factusol-cobros/${encodeURIComponent(eventId)}/anular`,
    { method: "POST", body: JSON.stringify({ confirm: true }) },
  );
}

/** «Corregir cobro» = anular + registrar de nuevo con estos datos. */
export async function correctOrderCobro(
  orderId: string, eventId: string,
  body: { cuenta: string; fecha: string; importe?: number | null; forma?: string | null },
): Promise<{ status: "queued"; job_id: string; cobro: BohubCobro }> {
  return apiFetch(
    `/api/erp/orders/${encodeURIComponent(orderId)}/factusol-cobros/${encodeURIComponent(eventId)}/corregir`,
    { method: "POST", body: JSON.stringify({ confirm: true, ...body }) },
  );
}

/** Espera al job de anular / corregir (misma cola y polling que el cobro). */
export async function waitForAnnulCobroJob(
  jobId: string, opts: { tries?: number; delayMs?: number } = {},
): Promise<{ status: "pending" } | { status: "finished"; result: AnnulCobroJobResult }
  | { status: "failed"; error?: string }> {
  return waitForInvoiceCollectionJob(jobId, opts) as never;
}

export async function getOrderFactusolCobro(
  orderId: string, movimientoId?: string,
): Promise<OrderCobroInfo> {
  const q = movimientoId ? `?movimiento=${encodeURIComponent(movimientoId)}` : "";
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/factusol-cobro${q}`);
}

/** Un movimiento del banco visto desde la ficha del pedido. */
export type CobroBancoMovimiento = {
  id: string;
  fecha_oper: string;
  /** La fecha con la que se registra el cobro. */
  fecha_valor: string;
  importe: number;
  concepto: string;
  payer_name: string | null;
  status: "pending" | "reconciled" | "discarded";
  cuenta: {
    id: string;
    nombre: string | null;
    contrapartida_codigo: string | null;
    contrapartida_nombre: string | null;
  };
};

export type CobroBancoCandidato = CobroBancoMovimiento & {
  confianza: BankConfidence;
  motivo: string;
  /** Menor que lo pendiente: cobro parcial. */
  parcial: boolean;
  /** Mayor que lo pendiente: se reparte en Conciliación. */
  excede: boolean;
  /** Fecha ISO de su línea de cobro si ya está en FACTUSOL: confirmar solo
   *  concilia, no registra otra vez. */
  ya_en_factusol: string | null;
  /** La cuenta (contrapartida) de esa línea de cobro de FACTUSOL. */
  ya_en_factusol_cuenta?: string | null;
  /** Apuntado en OTRA cuenta que la del banco: puede ser otro pago del mismo
   *  importe (efectivo, tarjeta…). */
  ya_en_factusol_otra_cuenta?: boolean;
  /** Hay en FACTUSOL un cobro suelto del mismo importe: puede ser este. */
  posible_doble?: boolean;
};

export type CobroBancoConciliado = CobroBancoMovimiento & {
  importe_conciliado: number;
  origen: string | null;
  /** Fecha ISO de su línea de cobro en FACTUSOL, o null si no la hay. */
  registrado: string | null;
  /** Su cobro, mandado desde la ficha, sigue en la cola de FACTUSOL. */
  en_cola: boolean;
  /** Hay en FACTUSOL un cobro suelto del mismo importe: puede ser este. */
  posible_doble?: boolean;
};

/** El cobro que propone el banco para la factura del pedido. */
export type CobroBanco =
  | { aplica: false; motivo: "sin_permiso" | "muestra" | "sin_factura" }
  | {
      aplica: true;
      estado: "cobrada" | "conciliado" | "candidatos" | "nada";
      cobrada: boolean;
      /** Lo conciliado que ni consta en FACTUSOL ni está en cola. */
      por_registrar: number;
      factura: { serie: number; codigo: number; numero: string; total: number;
                 saldo_pendiente: number };
      conciliados: CobroBancoConciliado[];
      candidatos: CobroBancoCandidato[];
      /** Cobros de FACTUSOL que no son de ningún movimiento del banco
       *  (efectivo, tarjeta, o un pago apuntado a mano con otra fecha). */
      cobros_sueltos?: { fecha: string | null; importe: number; cuenta: string | null }[];
    };

export async function getOrderCobroBanco(orderId: string): Promise<CobroBanco> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/cobro-banco`);
}

/** «Marcar cobrado con esta fecha»: concilia el movimiento con la factura,
 *  registra el cobro con la fecha valor y la contrapartida de la cuenta, y
 *  pone la factura en la hoja de Drive. Si su cobro ya estaba en FACTUSOL,
 *  solo concilia: `status: "already"` con `fecha_factusol`. 409 `en_cola` si
 *  su cobro sigue en la cola. */
/** Lo que devuelve cobrar con un movimiento del banco (ficha o factura). */
export type CobroBancoResultado = InvoiceCollectionResponse & {
  parcial: boolean; movimiento: CobroBancoMovimiento; fecha_factusol?: string | null;
  cuenta_factusol?: string | null; otra_cuenta?: boolean;
  /** La factura en la hoja de Drive (null si la cuenta no escribe en una). */
  hoja?: CobroHoja | null;
};

export async function confirmarCobroBanco(
  orderId: string, movementId: string, { forzar = false }: { forzar?: boolean } = {},
): Promise<CobroBancoResultado> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/cobro-banco`, {
    method: "POST",
    body: JSON.stringify({ movement_id: movementId, confirm: true, forzar }),
  });
}

/** El mismo cobro con movimiento, por factura (Conciliación, Documentos). */
export async function cobrarFacturaConMovimiento(
  serie: number, codigo: number | string, movementId: string,
  { forzar = false }: { forzar?: boolean } = {},
): Promise<CobroBancoResultado> {
  return apiFetch(
    `/api/erp/factusol/documents/facturas/${serie}/${encodeURIComponent(String(codigo))}/cobro-banco`,
    { method: "POST", body: JSON.stringify({ movement_id: movementId, confirm: true, forzar }) },
  );
}

/** Fase 5 — estado de cobro EN VIVO de UNA factura de FACTUSOL por serie+número,
 *  SIN pedido de BoHub: lo que necesita «Registrar cobro» (motor F-4-B) desde el
 *  explorador de documentos. Mismo contrato que `getOrderFactusolCobro` (saldo,
 *  cuenta sugerida, avisos), sin `order_id`/`order_number`. Solo lectura. */
export async function getFactusolFacturaCobro(
  serie: number, codigo: number | string, movimientoId?: string, ejercicio?: string,
): Promise<OrderCobroInfo> {
  const q = qs({ movimiento: movimientoId, ejercicio });
  return apiFetch(
    `/api/erp/factusol/documents/facturas/${serie}/${encodeURIComponent(String(codigo))}/cobro${q}`,
  );
}

/** «Actualizar cobros FACTUSOL» de la bandeja: comprueba de una vez los
 *  pedidos con factura (todos los visibles, o solo `ids`) y devuelve las
 *  filas ya con su estado persistido. Solo lectura en FACTUSOL. */
export async function refreshOrdersFactusolCobro(
  ids: string[] = [],
): Promise<{ checked: number; items: (OrderSummary & { cobro: OrderCobroInfo | null })[] }> {
  return apiFetch("/api/erp/orders/factusol-cobros/refresh", {
    method: "POST", body: JSON.stringify({ ids }),
  });
}

/** Respuesta del POST de registro de cobro (F-4-B): `already` (ya cobrada,
 *  nada encolado) o `queued` (job en `factusol:writes`). */
export type InvoiceCollectionResponse = {
  status: "already" | "queued";
  job_id?: string;
  estfac?: string;
  numero: string;
  cliente: string;
  referencia: string;
  total: number;
  saldo_pendiente: number;
  importe: number;
  contrapartida: Contrapartida;
  fecha: string;
  forma: string | null;
  /** El importe es menor que lo pendiente: cobro PARCIAL. */
  parcial?: boolean;
};

export type InvoiceCollectionJobStatus =
  | { status: "pending" }
  | {
      status: "finished";
      result: {
        registered: boolean;
        status: string;
        motivo?: string | null;
        numero?: string;
        linlco?: number;
        importe?: number;
        contrapartida?: string;
        fecha?: string;
        estfac_marked?: boolean;
        orders_updated?: string[];
        /** Estado tras el cobro: parcial = queda saldo pendiente. */
        parcial?: boolean;
        saldo_pendiente?: number;
      };
    }
  | { status: "failed"; error?: string; code?: string };

/** Registra UN cobro de la factura (motor F-4-B: solo F_LCO + ESTFAC=2,
 *  idempotente). `confirm` OBLIGATORIO (escritura contable). `importe`
 *  omitido = el saldo pendiente. */
export async function registerInvoiceCollection(
  serie: number, codigo: number | string,
  body: {
    confirm: boolean; cuenta: string; fecha: string;
    forma?: string | null; observaciones?: string | null; importe?: number | null;
  },
): Promise<InvoiceCollectionResponse> {
  return apiFetch(
    `/api/erp/factusol/documents/facturas/${serie}/${codigo}/collection`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function getInvoiceCollectionStatus(
  jobId: string,
): Promise<InvoiceCollectionJobStatus> {
  return apiFetch(
    `/api/erp/factusol/documents/facturas/collection-status/${encodeURIComponent(jobId)}`,
  );
}

/** Espera a que el job de cobro termine (o `pending` si se agota el margen). */
export async function waitForInvoiceCollectionJob(
  jobId: string, { tries = 20, delayMs = 1500 } = {},
): Promise<InvoiceCollectionJobStatus> {
  let last: InvoiceCollectionJobStatus = { status: "pending" };
  for (let i = 0; i < tries; i++) {
    last = await getInvoiceCollectionStatus(jobId);
    if (last.status !== "pending") return last;
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return last;
}

/** Abre el diálogo «guardar» del navegador con el blob descargado.
 *
 *  El nombre lo pone el servidor: `apiDownloadBlob` lo trae pegado al binario
 *  (`Content-Disposition`, ver `downloadName.ts`), así que `fallback` solo se
 *  usa si la respuesta no traía ninguno — es el nombre que cada pantalla
 *  construía hasta #531, y se mantiene para que ninguna descarga se quede
 *  sin nombre. */
export function saveBlob(blob: Blob, fallback: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = downloadName(blob, fallback);
  a.click();
  URL.revokeObjectURL(url);
}

// --- ERP-F1 · enviar la factura por email en el idioma del cliente ----------

/** Origen del idioma sugerido para el email (misma cascada que el PDF, E4). */
export type InvoiceEmailLangSource =
  | "pedido" | "cliente" | "pais_documento" | "pais_cliente"
  | "empresa" | "defecto";

/** Un contacto de la empresa del pedido, para el selector de destinatarios de
 *  los envíos de documentos (factura / pedido / proforma). */
export type EmailContact = {
  id: string;
  name: string;
  email: string | null;
  has_email: boolean;
  /** Es el contacto ligado al pedido (se marca por defecto). */
  is_order_contact: boolean;
  /** Punto A (presupuestos): premarcado en «Para» (el contacto vinculado a la
   *  cabecera o, si no, el principal con email). */
  is_primary?: boolean;
};

// --- Punto A · enviar el PRESUPUESTO / proforma por email ---------------------

export type QuoteEmailPreview = {
  serie: number;
  codpre: number;
  numero: string;
  /** Email de la cabecera de la proforma, por si no hay contactos. */
  to: string;
  lang: FactusolPdfLang;
  lang_source: InvoiceEmailLangSource | "selector";
  subject: string;
  body_text: string;
  /** Remitente: el de la EMPRESA EMISORA de la serie; si no, el del usuario. */
  from_alias: string;
  from_alias_source: "serie" | "usuario";
  from_alias_ok?: boolean | null;
  from_alias_problem?: string | null;
  attachment_filename: string;
  variant: "proforma" | null;
  currency: string;
  /** Empresa CRM vinculada al cliente (null = no vinculada). */
  company_id: string | null;
  company_name: string | null;
  customer_name?: string | null;
  order_id: string | null;
  order_number?: string | null;
  company_contacts: EmailContact[];
  /** A quién saluda `{contacto}` en el cuerpo (null = a la empresa). */
  contacto_id?: string | null;
  /** Valores de los marcadores con los que se rellenó la plantilla. */
  markers?: Record<string, string>;
};

export type QuoteEmailSendPayload = {
  confirm: boolean;
  to: string[];
  cc?: string[];
  bcc?: string[];
  subject: string;
  body_text: string;
  lang: FactusolPdfLang;
  from_alias: string;
  variant?: "proforma" | null;
  currency?: string;
  bank?: number | null;
};

export async function getQuoteEmailPreview(
  codpre: string | number, serie: number,
  opts: {
    lang?: FactusolPdfLang; variant?: "proforma" | null; currency?: string | null;
    /** A quién saludar: id de contacto, "none" (direcciones libres → la empresa)
     *  u omitido (el premarcado). */
    contact_id?: string | null;
  } = {},
): Promise<QuoteEmailPreview> {
  return apiFetch(
    `/api/erp/factusol/quotes/${encodeURIComponent(String(codpre))}/email-preview${qs({
      serie, lang: opts.lang, variant: opts.variant ?? undefined,
      currency: opts.currency ?? undefined, contact_id: opts.contact_id ?? undefined,
    })}`,
  );
}

/** Contacto del CRM encontrado con el buscador del modal de email. */
export type CrmContactHit = {
  id: string;
  name: string;
  email: string;
  company_name: string | null;
};

/** Remates · punto 3: busca CUALQUIER contacto del CRM con email (nombre,
 *  email o empresa). Vive en el ERP: los perfiles de pedidos no tienen
 *  acceso a `/api/contacts`. Con `conEmail: false` (elegir el contacto de una
 *  oferta) también los que no tienen email o lo tienen sin validar: un lead
 *  que llegó por teléfono puede recibir una oferta; el email solo hace falta
 *  para enviarla. Sin email, `email` llega como cadena vacía. */
export async function searchCrmContacts(
  q: string, limit = 8, opts: { conEmail?: boolean } = {},
): Promise<CrmContactHit[]> {
  const r = await apiFetch<{ items: (Omit<CrmContactHit, "email"> & { email: string | null })[] }>(
    `/api/erp/contacts/search${qs({
      q, limit, ...(opts.conEmail === false ? { con_email: "false" } : {}),
    })}`,
  );
  return r.items.map((i) => ({ ...i, email: i.email ?? "" }));
}

export async function sendQuoteEmail(
  codpre: string | number, serie: number, payload: QuoteEmailSendPayload,
): Promise<InvoiceEmailSendResult> {
  return apiFetch(
    `/api/erp/factusol/quotes/${encodeURIComponent(String(codpre))}/email${qs({ serie })}`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

/** Plantilla del email de PRESUPUESTO: ejemplo con datos de muestra. */
export async function previewQuoteEmailTemplate(
  lang: string,
  draft: { subject?: string; body?: string } = {},
): Promise<InvoiceEmailTemplatePreview> {
  return apiFetch<InvoiceEmailTemplatePreview>("/api/erp/settings/quote-email/preview", {
    method: "POST",
    body: JSON.stringify({ lang, subject: draft.subject ?? null, body: draft.body ?? null }),
  });
}

/** Plantilla del email de PRESUPUESTO: «Enviarme una prueba». */
export async function sendQuoteEmailTemplateTest(
  lang: string,
  draft: { subject?: string; body?: string; to?: string } = {},
): Promise<InvoiceEmailTemplateTestResult> {
  return apiFetch<InvoiceEmailTemplateTestResult>("/api/erp/settings/quote-email/test-send", {
    method: "POST",
    body: JSON.stringify({
      lang, subject: draft.subject ?? null, body: draft.body ?? null, to: draft.to ?? null,
    }),
  });
}

/** Datos de la PREVISUALIZACIÓN obligatoria antes de enviar la factura. Todo
 *  editable en el modal salvo el nombre del adjunto (se regenera al enviar). */
export type InvoiceEmailPreview = {
  serie: number;
  codigo: number;
  numero: string;
  /** Destinatario propuesto (email del cliente); vacío si no se conoce. */
  to: string;
  lang: FactusolPdfLang;
  lang_source: InvoiceEmailLangSource;
  subject: string;
  body_text: string;
  /** Alias emisor, por orden: el de la TIENDA del pedido si está configurado
   *  (`from_alias_source: "tienda"`); si no, el de la empresa emisora de esta
   *  serie (`"serie"`); si no, el alias por defecto del usuario (`"usuario"`). */
  from_alias: string;
  from_alias_source: "tienda" | "serie" | "usuario";
  /** ¿Se podrá enviar desde ese alias? (preferencia del usuario, o remitente
   *  de Ajustes ERP verificado como send-as en Gmail). `null`/ausente = no se
   *  pudo comprobar. */
  from_alias_ok?: boolean | null;
  /** Por qué no: "alias_not_allowed" | "not_in_gmail" | "gmail_unavailable" | … */
  from_alias_problem?: string | null;
  /** Slug de la tienda Woo del pedido (null si no es de tienda). */
  store?: string | null;
  attachment_filename: string;
  /** Si hay un hilo del pedido al que responder, su message-id; si no null. */
  reply_to_message_id: string | null;
  replies_to_thread: boolean;
  /** Pedido del CRM al que se registrará el envío (null si no se localizó). */
  order_id: string | null;
  /** Nº de pedido de BoHub (va en asunto y cuerpo vía el placeholder {pedido}). */
  order_number?: string | null;
  /** Cliente de la factura en FACTUSOL y si NO coincide con la empresa del
   *  pedido (posible vínculo erróneo: revisar el «Para»). */
  invoice_customer?: string | null;
  customer_mismatch?: boolean;
  /** Contactos de la empresa del pedido para el selector de destinatarios. */
  company_contacts?: EmailContact[];
};

export type InvoiceEmailSendPayload = {
  /** OBLIGATORIO true: enviar es irreversible, nunca un clic accidental. */
  confirm: boolean;
  to: string[];
  /** Copia (CC) y copia oculta (CCO): contactos de la empresa o emails libres. */
  cc?: string[];
  bcc?: string[];
  subject: string;
  body_text: string;
  lang: FactusolPdfLang;
  from_alias: string;
  reply_to_message_id?: string | null;
  /** Índice de cuenta bancaria (mismo criterio que la descarga del PDF). */
  bank?: number | null;
  /** Solo "anticipo" cambia el PDF; el resto de variantes no aplican aquí. */
  variant?: "anticipo" | null;
  /** Pedido desde cuya ficha se envía: el backend verifica que la factura es
   *  suya (409 si no) y registra el envío en SU timeline. */
  order_id?: string | null;
};

export type InvoiceEmailSendResult = {
  sent: boolean;
  message_id: string;
  thread_id: string;
  to: string[];
  cc?: string[];
  bcc?: string[];
  lang: FactusolPdfLang;
  numero: string;
  attachment_filename: string;
};

/** Factura de FACTUSOL vinculada a un pedido del CRM (serie + número). */
export type FactusolInvoiceRef = { serie: number; codigo: number; numero: string };

/** Un remitente disponible para los envíos del ERP: un «enviar como» verificado
 *  de la cuenta de Gmail conectada del CRM. */
export type EmailSender = { email: string; name: string; is_primary: boolean };

export type EmailSendersResult = {
  senders: EmailSender[];
  /** false = Gmail no conectado/accesible: la UI cae al remitente propuesto. */
  available: boolean;
  problem: string | null;
};

/** Remitentes disponibles para el selector «Enviar desde» (sendAs verificados
 *  del Gmail del CRM). */
export async function getEmailSenders(): Promise<EmailSendersResult> {
  return apiFetch(`/api/erp/email-senders`);
}

export async function getInvoiceEmailPreview(
  serie: number, codigo: number | string, lang?: FactusolPdfLang,
  orderId?: string | null,
): Promise<InvoiceEmailPreview> {
  // `order_id` (ficha de pedido): destinatario, tienda, idioma y timeline
  // del pedido indicado, tras verificar que la factura es suya.
  const params = new URLSearchParams();
  if (lang) params.set("lang", lang);
  if (orderId) params.set("order_id", orderId);
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiFetch(
    `/api/erp/factusol/documents/facturas/${serie}/${codigo}/email-preview${query}`,
  );
}

export async function sendInvoiceEmail(
  serie: number, codigo: number | string, payload: InvoiceEmailSendPayload,
): Promise<InvoiceEmailSendResult> {
  return apiFetch(
    `/api/erp/factusol/documents/facturas/${serie}/${codigo}/email`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

// --- ERP · enviar el PEDIDO por email (SAT / taller + otros) ----------------

/** Un PDF que se puede adjuntar al pedido (o por qué no se puede). */
export type OrderEmailAttachmentInfo = {
  available: boolean;
  numero: string | null;
  reason: string | null;
  code: string | null;
  doc_type?: string | null;
};

export type OrderEmailPreview = {
  order_id: string;
  order_number: string;
  cliente: string;
  lang: FactusolPdfLang;
  /** Destinatarios precargados: el SAT de Ajustes ERP (vacío si no hay). */
  to: string[];
  sat_email: string;
  sat_configured: boolean;
  subject: string;
  body_text: string;
  from_alias: string;
  attachments: {
    albaran: OrderEmailAttachmentInfo;
    pedido: OrderEmailAttachmentInfo;
    factura: OrderEmailAttachmentInfo;
  };
  /** Qué viene marcado: el albarán si existe; los otros no. */
  defaults: { albaran: boolean; pedido: boolean; factura: boolean };
  /** Contactos de la empresa del pedido para el selector de destinatarios. */
  company_contacts?: EmailContact[];
  /** Destinatarios fijos (la dirección del taller): quien no escribe a
   *  clientes (el taller, 14/10/2026) solo manda el pedido al taller. */
  recipients_locked?: boolean;
};

export type OrderEmailSendPayload = {
  confirm: true;
  to: string[];
  cc?: string[];
  bcc?: string[];
  subject: string;
  body_text: string;
  lang: FactusolPdfLang;
  from_alias: string;
  include_albaran: boolean;
  include_pedido: boolean;
  include_factura: boolean;
};

/** El correo al taller pedido en «Mandar a SAT»: enviado, fallido (se
 *  reintenta) o esperando a que exista el albarán. */
export type SatCorreoEstado = {
  estado: "enviado" | "fallido" | "pendiente_albaran" | "enviando" | "cancelado";
  detalle: string | null;
  at: string | null;
};

export type MandarSatInfo = {
  /** Creado desde una proforma, un albarán o una factura de FACTUSOL. */
  desde_documento: boolean;
  /** Sin albarán y BoHub se lo crearía (proforma, pedido, manual). */
  albaran_lo_crea_bohub: boolean;
  /** Viene de una factura y aún no tiene albarán: se vincula, nunca se crea. */
  factura_sin_albaran: boolean;
  /** Desde una factura o un albarán y sin albarán: solo se vincula. */
  albaran_solo_vincular?: boolean;
  /** Pedido web: el albarán lo crea WooCommerce. */
  es_web?: boolean;
  /** En la Cola SAT y el taller aún no ha empezado: se puede deshacer. */
  deshacible?: boolean;
  correo: SatCorreoEstado | null;
};

export type MandarSatResult = {
  cola: "entrado" | "ya_estaba" | "mas_adelante";
  albaran: {
    estado: string | null; numero?: string | null; detalle?: string;
    candidatos?: string[]; crear: boolean;
  };
  correo: SatCorreoEstado | null;
  order: OrderDetail;
};

/** «Mandar a SAT»: el pedido entra en la Cola SAT y, con `correo`, sale el
 *  aviso al taller en la misma acción. Si el correo falla, el pedido sigue en
 *  la cola (`correo.estado = fallido`). */
export async function mandarASat(
  orderId: string, correo: Omit<OrderEmailSendPayload, "confirm"> | null,
): Promise<MandarSatResult> {
  return apiFetch(`/api/erp/orders/${orderId}/mandar-sat`, {
    method: "POST", body: JSON.stringify({ confirm: true, correo }),
  });
}

/** «Deshacer envío al SAT»: vuelve a «Por revisar» si el taller no ha
 *  empezado (409 con el motivo si ya prepara o lo ha embalado). */
export async function deshacerEnvioSat(
  orderId: string, motivo?: string,
): Promise<{ aviso_correo: string | null; order: OrderDetail }> {
  return apiFetch(`/api/erp/orders/${orderId}/deshacer-sat`, {
    method: "POST", body: JSON.stringify({ confirm: true, motivo: motivo ?? null }),
  });
}

/** Manda (o reintenta) el correo al taller pendiente de «Mandar a SAT». */
export async function enviarCorreoSatPendiente(
  orderId: string,
): Promise<{ correo: SatCorreoEstado; order: OrderDetail }> {
  return apiFetch(`/api/erp/orders/${orderId}/sat-correo/enviar`, {
    method: "POST", body: JSON.stringify({ confirm: true }),
  });
}

export type AlbaranCandidato = {
  serie: string;
  codigo: number;
  numero: string;
  ejercicio: string;
  fecha: string | null;
  cliente_nombre: string | null;
  codcli: string | null;
  total: number | null;
  /** «Proforma 1-000500»: de qué documento sale. */
  viene_de: string | null;
  /** El que apunta la factura de origen del pedido. */
  de_la_factura: boolean;
};

/** Albaranes para vincular al pedido: los del cliente sin otro pedido, los
 *  más recientes primero; con `q`, por número. */
export async function getAlbaranesCandidatos(
  orderId: string, q?: string,
): Promise<{ codcli: string | null; items: AlbaranCandidato[]; de_la_factura: string[] }> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return apiFetch(`/api/erp/orders/${orderId}/albaranes-candidatos${qs}`);
}

/** Vincula un albarán que ya existe en FACTUSOL (no escribe en FACTUSOL). */
export async function vincularAlbaranPedido(
  orderId: string, serie: string, codigo: number,
): Promise<OrderDetail> {
  return apiFetch(`/api/erp/orders/${orderId}/albaran/vincular`, {
    method: "POST", body: JSON.stringify({ confirm: true, serie, codigo }),
  });
}

export type OrderEmailSendResult = {
  sent: boolean;
  order_id: string;
  message_id: string;
  thread_id: string | null;
  to: string[];
  cc: string[];
  bcc: string[];
  lang: FactusolPdfLang;
  attachments: string[];
  attachment_kinds: string[];
};

/** Datos del modal «Enviar por email» del pedido (no envía ni genera PDF). */
export async function getOrderEmailPreview(
  orderId: string, lang?: FactusolPdfLang,
): Promise<OrderEmailPreview> {
  const query = lang ? `?lang=${lang}` : "";
  return apiFetch(`/api/erp/orders/${orderId}/email-preview${query}`);
}

/** Envía el pedido por email (Gmail integrado) con los PDF marcados. */
export async function sendOrderEmail(
  orderId: string, payload: OrderEmailSendPayload,
): Promise<OrderEmailSendResult> {
  return apiFetch(`/api/erp/orders/${orderId}/email`, {
    method: "POST", body: JSON.stringify(payload),
  });
}

/** ERP-F1 — la factura FACTUSOL VINCULADA al pedido (serie + número) para que
 *  la ficha / Seguimiento reutilicen el mismo flujo de PDF y email que el
 *  detalle de la factura. Sale del vínculo guardado (sea cual sea el origen del
 *  pedido). 404 sin factura; 409 si falta la serie. */
export async function getOrderFactusolInvoiceRef(
  orderId: string,
): Promise<FactusolInvoiceRef> {
  return apiFetch(`/api/erp/orders/${orderId}/factusol-invoice-ref`);
}

// --- Clientes FACTUSOL ↔ CRM (Fase C · C-3) ---------------------------------

/** Columnas REALES de F_CLI (verificadas contra la base de Bomedia, C-3-fix1):
 *  el nombre vive en `nofcli` (fiscal) y `noccli` (comercial), el NIF en
 *  `nifcli`, el domicilio en `domcli` y el país en `paicli` (ISO numérico).
 *  `nombre` y `nif` son alias que calcula el backend para la UI. */
export type FactusolCustomer = {
  codcli: string | null;
  /** Alias: comercial si existe, si no el fiscal. */
  nombre: string | null;
  /** Alias de `nifcli`. */
  nif: string | null;
  nofcli: string | null;
  noccli: string | null;
  nifcli: string | null;
  domcli: string | null;
  pobcli: string | null;
  cpocli: string | null;
  procli: string | null;
  /** Código ISO 3166-1 numérico («724» = España). */
  paicli: string | null;
  emacli: string | null;
  telcli: string | null;
  /** País en ISO2 (PAICLI normalizado por el backend); null si no se reconoce. */
  pais_iso2?: string | null;
  /** Tarea C: tipo de documento / aplicar IVA / tipo impositivo de la ficha y
   *  el régimen que codifican (null si no es ninguno de los confirmados). */
  ificli?: number | null;
  ivacli?: number | null;
  tivcli?: number | null;
  regime?: FactusolRegime | null;
  regime_label?: string | null;
  /** Vínculo CRM existente (null si el cliente aún no está en el CRM). */
  crm_link: { type: "company" | "contact"; id: string; name: string } | null;
  factusol_matches_crm_id: string | null;
};

// --- Corregir lo emitido desde la ficha: mención legal y cliente ------------

/** Una mención legal de factura (Configuración ERP). Solo las revisadas se
 *  pueden aplicar; las de referencia vienen sin revisar. */
export type MencionLegal = {
  id: string;
  nombre: string;
  /** El castellano (lo de antes de haber idiomas). */
  texto: string;
  revisado: boolean;
  /** Un texto por idioma; cada uno se revisa por separado. Al emitir va el
   *  del idioma del pedido si está revisado; si no, el castellano y un aviso. */
  textos?: Partial<Record<IdiomaMencion, TextoMencion>>;
};

export type TextoMencion = { texto: string; revisado: boolean };

/** Los idiomas de una mención (ver `mencionesIdioma.ts`). */
export type IdiomaMencion = "es" | "en" | "de" | "fr" | "nl" | "pt";

export async function getMencionesLegales(): Promise<{
  items: MencionLegal[]; max: number; verifactu_activo: boolean;
}> {
  return apiFetch("/api/erp/catalogs/menciones-legales");
}

export type MencionInput = { mencion_id?: string | null; texto?: string | null };

export type MencionPlan = {
  factura: string;
  texto: string;
  mencion_id: string | null;
  /** La observación donde iría (la primera vacía); null = no hay hueco. */
  campo: "OB1FAC" | "OB2FAC" | null;
  ya_esta: boolean;
  actuales: { OB1FAC: string; OB2FAC: string };
  enviada: InvoiceSent | null;
  avisos: string[];
};

export async function previewMencion(orderId: string, body: MencionInput): Promise<MencionPlan> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/factura/mencion/vista-previa`, {
    method: "POST", body: JSON.stringify(body),
  });
}

export async function aplicarMencion(
  orderId: string, body: MencionInput & { campo: "OB1FAC" | "OB2FAC" },
): Promise<{ ok: boolean; factura: string; campo: string; texto: string; ya_enviada: boolean }> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/factura/mencion`, {
    method: "POST", body: JSON.stringify({ ...body, confirm: true }),
  });
}

export type ClienteResumen = {
  codcli: string | null;
  nombre: string | null;
  nif: string | null;
  direccion: string | null;
  cp: string | null;
  ciudad: string | null;
  provincia: string | null;
  pais: string | null;
  regime: FactusolRegime | null;
};

export type CambioFactura = {
  campo: string;
  label: string;
  antes: string;
  despues: string;
};

export type RevincularPlan = {
  cliente: ClienteResumen;
  actual: { company_id: string; nombre: string; codcli: string | null } | null;
  empresa: { company_id: string | null; nombre: string | null; crear: boolean };
  sin_cambios_crm: boolean;
  /** Nada que cambiar: ni la ficha, ni la factura, ni el cliente con el que
   *  se emitirá (el del documento de origen). */
  sin_cambios: boolean;
  factura: {
    numero: string;
    cambios: CambioFactura[];
    /** Por qué NO se puede cambiar la factura (VERI*FACTU, IVA). */
    bloqueo: string | null;
    /** La factura tiene abono: no se toca. */
    abonada?: boolean;
    avisos: string[];
    enviada?: InvoiceSent | null;
    /** ¿Puede este usuario tocar la factura? (quien emite facturas). */
    puedes?: boolean;
  } | null;
  /** Pedido aún sin facturar cuyo documento de FACTUSOL lleva otro cliente. */
  documento: { tipo: string; numero: string; codcli: string; bloqueo: string | null } | null;
  avisos: string[];
};

export async function previewRevincularCliente(
  orderId: string, codcli: string,
): Promise<RevincularPlan> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/cliente/vista-previa`, {
    method: "POST", body: JSON.stringify({ codcli }),
  });
}

/** `camposFactura`: lo que enseñó la vista previa de la factura, campo →
 *  valor nuevo (`despues`); vacío sin factura o si no cambia. */
export async function revincularCliente(
  orderId: string, codcli: string, camposFactura: Record<string, string>,
): Promise<{
  ok: boolean; company_id: string; company_name: string; codcli: string;
  empresa_creada: boolean; factura: string | null; escrito_en_factura: boolean;
}> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/cliente`, {
    method: "POST",
    body: JSON.stringify({ codcli, campos_factura: camposFactura, confirm: true }),
  });
}

export type ClienteNuevoDatos = {
  nombre: string;
  nif: string;
  direccion: string;
  cp: string;
  ciudad: string;
  provincia: string;
  pais: string;
  email?: string | null;
  telefono?: string | null;
};

export type ClienteNuevoPlan = {
  /** Ya hay un cliente con ese CIF en FACTUSOL: no se ofrece crear. */
  existe: boolean;
  factusol: { codcli: string | null; nombre: string | null; nif: string | null }[];
  crm: { company_id: string; nombre: string; codcli: string | null }[];
  /** Los campos exactos de F_CLI que se crearían. */
  campos: { campo: string; valor: string }[];
  regime?: FactusolRegime;
  regime_label?: string;
};

export async function previewClienteNuevo(
  orderId: string, datos: ClienteNuevoDatos,
): Promise<ClienteNuevoPlan> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/cliente/nuevo/vista-previa`, {
    method: "POST", body: JSON.stringify(datos),
  });
}

export async function crearClienteNuevo(
  orderId: string, datos: ClienteNuevoDatos,
): Promise<{ codcli: string; creado: boolean }> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/cliente/nuevo`, {
    method: "POST", body: JSON.stringify({ ...datos, confirm: true }),
  });
}

export async function searchFactusolCustomers(
  q: string, by: "nif" | "email" | "name" | "codcli" = "nif",
): Promise<FactusolCustomer[]> {
  const r = await apiFetch<{ items: FactusolCustomer[] }>(
    `/api/erp/factusol/customers/search?q=${encodeURIComponent(q)}&by=${by}`,
  );
  return r.items;
}

export async function linkFactusolCustomer(body: {
  crm_type: "company" | "contact";
  crm_id: string;
  factusol_codcli: string;
}): Promise<{ linked: boolean }> {
  return apiFetch("/api/erp/factusol/customers/link", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Lote 8 · B2 — quién ya tiene el CODCLI que se intentaba vincular. Cuando el
 *  registro que lo tiene es una EMPRESA, `company_id`/`company_name` permiten
 *  ofrecer «Fusionar con esa empresa» (la actual se fusiona EN la que ya tiene
 *  el vínculo, sin duplicar). */
export type AlreadyLinkedHolder = {
  type: string | null;
  company_id: string | null;
  company_name: string | null;
};

/** Si el error es el 409 `already_linked` de vincular un CODCLI ya tomado por
 *  OTRA empresa CRM, devuelve su `holder`; si no, `null`. Solo se ofrece
 *  fusionar cuando quien lo tiene es una empresa con id (no un contacto). */
export function alreadyLinkedHolder(err: unknown): AlreadyLinkedHolder | null {
  if (!(err instanceof ApiError) || err.code !== "already_linked") return null;
  const detail = err.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const d = detail as Record<string, unknown>;
  const type = typeof d.holder_type === "string" ? d.holder_type : null;
  const companyId =
    typeof d.holder_company_id === "string" ? d.holder_company_id : null;
  const companyName =
    typeof d.holder_company_name === "string" ? d.holder_company_name : null;
  if (type !== "company" || !companyId) return null;
  return { type, company_id: companyId, company_name: companyName };
}

/** «Traer datos de FACTUSOL» (ficha de empresa): un campo que cambiaría. */
export type FactusolPullChange = {
  field: string;
  label: string;
  crm: string;
  factusol: string;
};

export type FactusolPullPreview = {
  company_id: string;
  codcli: string;
  customer: FactusolCustomer;
  changes: FactusolPullChange[];
};

/** Qué cambiaría «Traer datos de FACTUSOL» (no escribe nada). */
export async function getFactusolPullPreview(companyId: string): Promise<FactusolPullPreview> {
  return apiFetch(
    `/api/erp/factusol/customers/pull-preview?company_id=${encodeURIComponent(companyId)}`,
  );
}

/** Sobrescribe la empresa CRM con los datos del cliente FACTUSOL vinculado
 *  (FACTUSOL = fuente de verdad). Solo CRM; deja auditoría. */
export async function pullFactusolIntoCompany(companyId: string): Promise<{
  ok: boolean; company_id: string; codcli: string;
  changes: FactusolPullChange[]; applied: number;
}> {
  return apiFetch("/api/erp/factusol/customers/pull-into-crm", {
    method: "POST",
    body: JSON.stringify({ company_id: companyId }),
  });
}

/** Tarea C · régimen de IVA / tipo de documento del cliente F_CLI, decidido
 *  por el país + NIF-IVA de la empresa CRM (mapeo confirmado con volcados
 *  reales: `IFICLI` tipo de documento, `IVACLI` aplicar IVA, `TIVCLI` tipo
 *  impositivo, `PAICLI` país ISO numérico). */
export type FactusolRegime = "nacional" | "intracomunitario" | "exportacion";

export type FactusolRegimeChange = {
  column: string;
  label: string;
  current: string | number | null;
  current_label: string;
  proposed: string | number;
  proposed_label: string;
};

export type FactusolRegimePreview = {
  company_id: string;
  codcli: string;
  company_country: string | null;
  company_vat: string | null;
  country_iso2: string | null;
  regime: FactusolRegime;
  regime_label: string;
  /** Por qué sale ese régimen, nombrando las DOS puntas de la pareja
   *  («Bélgica → España (UE) con NIF-IVA … → intracomunitario»). */
  reason: string;
  /** País (ISO2) de la empresa emisora con la que se ha calculado, o null
   *  cuando las empresas discrepan (ver `conflicto`). */
  issuer_iso2?: string | null;
  /** El régimen con CADA empresa que factura: el mismo cliente es nacional
   *  para Streamtec (ES) e intracomunitario para MQ Europe (BE). */
  regimes?: {
    serie: number | null;
    empresa: string | null;
    pais_iso2: string | null;
    regime: FactusolRegime;
    regime_label: string;
  }[];
  /** Texto del conflicto cuando las empresas no coinciden: la ficha F_CLI es
   *  una sola y BoHub NO toca sus columnas de régimen (sí el país). */
  conflicto?: string | null;
  current: {
    IFICLI: number | null;
    IVACLI: number | null;
    TIVCLI: number | null;
    PAICLI: string | null;
    regime: FactusolRegime | null;
    regime_label: string | null;
  };
  proposed: Record<string, string | number>;
  changes: FactusolRegimeChange[];
  /** true = la ficha F_CLI ya coincide: nada que corregir. */
  coherent: boolean;
};

/** Qué régimen le corresponde al cliente y qué cambiaría en F_CLI (no escribe). */
export async function getFactusolRegimePreview(companyId: string): Promise<FactusolRegimePreview> {
  return apiFetch(
    `/api/erp/factusol/customers/regime-preview?company_id=${encodeURIComponent(companyId)}`,
  );
}

/** Corrige en FACTUSOL (ActualizarRegistro de F_CLI, SOLO las columnas que
 *  cambian) el tipo de documento / régimen / país del cliente vinculado.
 *  A demanda, con auditoría. */
export async function fixFactusolCustomerRegime(companyId: string): Promise<
  FactusolRegimePreview & {
    ok: boolean;
    changed: boolean;
    written: Record<string, string | number>;
  }
> {
  return apiFetch("/api/erp/factusol/customers/fix-regime", {
    method: "POST",
    body: JSON.stringify({ company_id: companyId }),
  });
}

export type CreateFactusolCustomerPayload = {
  crm_type: "company" | "contact";
  crm_id: string;
  nombre: string;
  nif?: string;
  direccion?: string;
  ciudad?: string;
  cp?: string;
  provincia?: string;
  /** País (ISO2, nombre o numérico) y NIF-IVA: si no vienen, el backend los
   *  toma de la empresa CRM. Deciden `PAICLI` y el régimen de IVA. */
  pais?: string;
  vat?: string;
  email?: string | null;
  telefono?: string | null;
};

/** C-3-fix3: crea la empresa CRM y la vincula al cliente FACTUSOL en UNA sola
 *  transacción del backend. Sustituye al par createCompany + link, que dejaba
 *  empresas huérfanas si el link fallaba. */
export async function createFactusolCustomerAndLink(payload: {
  factusol_codcli: string;
  factusol_customer_data: {
    nombre: string;
    nif?: string;
    direccion?: string;
    ciudad?: string;
    cp?: string;
    provincia?: string;
    telefono?: string;
    email?: string;
    /** F1-fix2: país del cliente en FACTUSOL (PAICLI, ISO numérico o nombre);
     *  el backend lo normaliza a ISO2. Sin él el país queda vacío. */
    pais?: string;
  };
}): Promise<{ company_id: string; factusol_codcli: string; created: boolean }> {
  return apiFetch("/api/erp/factusol/customers/create-crm-and-link", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function createFactusolCustomer(
  payload: CreateFactusolCustomerPayload,
): Promise<{
  factusol_codcli: string;
  created: boolean;
  /** Régimen con el que se creó la ficha (null si ya existía y solo se vinculó). */
  regime?: FactusolRegime | null;
  regime_label?: string | null;
}> {
  return apiFetch("/api/erp/factusol/customers/create", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// --- Proformas FACTUSOL (Fase C · C-4) --------------------------------------

/** Artículo de F_ART. `precio` es el coste (PCOART): FACTUSOL no expone una
 *  tarifa de venta única, así que sirve solo de sugerencia editable.
 *
 *  C-4-fix1: `equart` es el **SKU comercial** (`CDR80WPT`), distinto del
 *  `codart` interno (`00001`). `sku` es el alias que calcula el backend
 *  (comercial → interno) y es lo que hay que enseñar al operativo. */
export type FactusolArticle = {
  codart: string | null;
  equart: string | null;
  /** Alias: `equart` si existe, si no `codart`. */
  sku: string | null;
  descripcion: string | null;
  desart: string | null;
  deeart: string | null;
  detart: string | null;
  eanart: string | null;
  famart: string | null;
  /** C-4-fix2: precio de VENTA. `null` si esta base de FACTUSOL no tiene
   *  ninguna de las columnas candidatas — entonces se deja en blanco y lo
   *  teclea el operador, nunca se fuerza un 0.00. */
  precio_venta: number | null;
  /** Columna real de la que salió `precio_venta` (`PVPART`, `TAR1ART`…). */
  precio_venta_columna: string | null;
  /** PCOART: precio de COSTE. Nunca es lo que se factura. */
  precio_coste: number;
  /** Venta si la hay, coste como reserva. Compat con C-4/C-4-fix1. */
  precio: number;
  /** Tarifas multinivel si la base las usa. Informativas: no se calculan. */
  tarifas?: Record<string, number>;
  stock: number;
  iva_pct: number;
};

/** Una línea del desglose de la proforma. */
export type FactusolQuoteLine = {
  position: number;
  /** CODART interno (`ARTLPS`); null en las líneas de texto libre. */
  codart: string | null;
  /** Lote B4: SKU comercial (`EQUART`) del artículo — el que enseña y busca
   *  el autocomplete. Null en las de texto libre; ausente en mocks antiguos. */
  sku?: string | null;
  description: string;
  quantity: number;
  unit_price: number;
  discount_pct: number;
  line_total: number;
  iva_pct: number;
  /** Línea de TEXTO (sin artículo, cantidad 0, precio 0): continuación de una
   *  descripción larga —FACTUSOL guarda 255 caracteres por fila de F_LPS— o
   *  una nota del escritorio. No suma. Ausente en respuestas antiguas. */
  text_only?: boolean;
};

/** Proforma (presupuesto F_PRE). Recuerda que F_PRE es MONO-LÍNEA: `lines`
 *  sale de la caché del CRM y está vacío en las proformas hechas en el
 *  FACTUSOL de escritorio (`line_source: "ref_text"`). */
/** Colas de la pantalla Proformas (Fase 4): aceptada → por convertir,
 *  pendiente de respuesta, rechazada, y convertida (ya es pedido de BoHub). */
export type QuoteQueue = "aceptadas" | "pendientes" | "rechazadas" | "convertidas";
export type QuoteEstado = "pendiente" | "aceptada" | "rechazada" | "otro";

export type FactusolQuote = {
  codpre: string | null;
  referencia: string;
  fecha: string | null;
  clipre: string | null;
  cliente_nombre: string | null;
  base: number;
  iva: number;
  total: number;
  /** Lote B3b: portes de la cabecera (`IPOR1PRE`); 0 si no tiene. */
  portes?: number;
  lines?: FactusolQuoteLine[];
  line_source?: "cache" | "ref_text" | "F_LPS";
  /** Fase 4: estado (`ESTPRE`), cola, empresa CRM vinculada, régimen de IVA
   *  (de la empresa; sin empresa, lo que dice la cabecera) y el pedido de
   *  BoHub si ya se convirtió. Ausentes en respuestas antiguas / mocks. */
  estpre?: number | null;
  estado?: QuoteEstado;
  estado_label?: string;
  queue?: QuoteQueue | null;
  queue_label?: string | null;
  company?: { id: string; name: string; country: string | null; factusol_id: string | null } | null;
  order?: { id: string; order_number: string } | null;
  country_iso2?: string | null;
  regime?: "nacional" | "intracomunitario" | "exportacion" | null;
  regime_label?: string | null;
  regime_source?: "empresa" | "cabecera" | null;
  exento?: boolean;
  /** Serie (`TIPPRE` = empresa emisora: 1 Bomedia / 2 MQ Europe / 4 Lambert /
   *  5 Streamtec) y número visible «serie-código» (5-000039). */
  tippre?: string | number | null;
  serie?: number | null;
  serie_label?: string | null;
  numero?: string | null;
  /** Punto E: forma de pago de la cabecera (`FOPPRE`, código F_FPA) y su
   *  nombre. `foppre` es la columna cruda; `forma_pago` la normalizada (null
   *  si no tiene). Solo en el detalle (`getFactusolQuote`). */
  foppre?: string | null;
  forma_pago?: string | null;
  forma_pago_nombre?: string | null;
  /** Punto C: bloque de entrega de la cabecera (a quién y dónde) y si es
   *  distinto de la sede del cliente en FACTUSOL (dropshipping / dirección
   *  alternativa). Solo en el detalle; la copia directa lo hereda. */
  envio?: {
    nombre: string | null; direccion: string | null; poblacion: string | null;
    cp: string | null; provincia: string | null; pais: string | null;
  } | null;
  envio_distinto?: boolean;
  /** Punto A: último envío por email (evento `erp.proforma_emailed`): fecha
   *  ISO y destinatarios; null si nunca se envió. */
  emailed_at?: string | null;
  emailed_to?: string[] | null;
};

export type QuoteJobStatus =
  | { status: "pending" }
  | { status: "finished"; result: Record<string, unknown> }
  /** `code: "quote_not_editable"` → la proforma no está pendiente; se puede
   *  reintentar con `force` (C-4-fix6). */
  | { status: "failed"; error?: string; code?: string };

export async function searchFactusolArticles(q: string): Promise<FactusolArticle[]> {
  const r = await apiFetch<{ items: FactusolArticle[] }>(
    `/api/erp/factusol/articles/search?q=${encodeURIComponent(q)}`,
  );
  return r.items;
}

export type QuotesListing = {
  items: FactusolQuote[];
  unlinked: boolean;
  /** Fase 4: contadores de TODAS las colas (aunque se filtre con `queue`) y
   *  los valores reales de `ESTPRE` que hay. */
  queue_counts?: Record<QuoteQueue, number>;
  estpre_values?: Record<string, number>;
  /** Cuántas casaban con los filtros ANTES del recorte a `limit`, y si la
   *  lista viene recortada (la pantalla avisa «mostrando 500 de 716»). */
  total?: number;
  limit?: number;
  truncated?: boolean;
};

export async function listFactusolQuotes(
  opts: {
    company_id?: string; days_back?: number; queue?: QuoteQueue; limit?: number;
    /** Serie = empresa emisora. Omitir = TODAS (como en Documentos). */
    serie?: number;
    /** Texto del buscador. Se aplica en el SERVIDOR antes del recorte: casa
     *  con nº (con o sin serie), referencia, cliente FACTUSOL, empresa CRM
     *  vinculada y contactos, sin acentos ni mayúsculas. */
    q?: string;
  } = {},
): Promise<QuotesListing> {
  return apiFetch(`/api/erp/factusol/quotes${qs(opts)}`);
}

/** C-4-fix1: busca proformas de CUALQUIER cliente para usarlas de plantilla.
 *  A diferencia de `listFactusolQuotes`, no filtra por empresa. */
export async function searchFactusolQuotes(
  q: string, opts: { days_back?: number; limit?: number } = {},
): Promise<FactusolQuote[]> {
  const r = await apiFetch<{ items: FactusolQuote[] }>(
    `/api/erp/factusol/quotes/search${qs({ q, ...opts })}`,
  );
  return r.items;
}

/** Una proforma por su clave REAL: serie + número. En FACTUSOL la clave de
 *  F_PRE es (`TIPPRE`, `CODPRE`) y cada serie lleva su contador, así que los
 *  números se repiten entre series — sin `serie` se abre la que toque por
 *  descarte, que con un número repetido puede no ser la que se quería. */
export async function getFactusolQuote(
  codpre: string, serie?: number | null,
): Promise<FactusolQuote> {
  return apiFetch(
    `/api/erp/factusol/quotes/${encodeURIComponent(codpre)}${qs({ serie: serie ?? undefined })}`,
  );
}

/** Punto E: forma de pago por defecto del cliente en FACTUSOL (`F_CLI.FPACLI`)
 *  con su nombre. `codigo: null` si no tiene. El modal de proforma la
 *  preselecciona. */
export async function getFactusolCustomerPaymentMethod(
  codcli: string,
): Promise<{ codigo: string | null; nombre: string | null }> {
  return apiFetch(`/api/erp/factusol/customers/${encodeURIComponent(codcli)}/payment-method`);
}

/** Dirección del cliente en FACTUSOL. `codigo: 0` es la sede; 1-4 son las
 *  adicionales del botón «Direcciones» del escritorio (C-4-fix6). */
export type FactusolAddress = {
  codigo: number;
  nombre: string;
  direccion: string;
  ciudad: string;
  cp: string;
  provincia: string;
  pais: string;
};

export async function getFactusolCustomerAddresses(
  codcli: string,
): Promise<FactusolAddress[]> {
  const r = await apiFetch<{ items: FactusolAddress[] }>(
    `/api/erp/factusol/customers/${encodeURIComponent(codcli)}/addresses`,
  );
  return r.items;
}

// --- conciliación masiva CRM ↔ FACTUSOL (Fase C · C-5) ----------------------

export type BulkMatchDifference = {
  field: string;
  crm: string;
  factusol: string;
  differs: boolean;
};

export type BulkMatchCandidate = {
  factusol_codcli: string | null;
  factusol_nifcli: string | null;
  factusol_nofcli: string | null;
  factusol_noccli: string | null;
  factusol_domcli: string | null;
  factusol_pobcli: string | null;
  factusol_cpocli: string | null;
  factusol_procli: string | null;
  differences: BulkMatchDifference[];
  differing_fields: number;
};

export type BulkMatchRow = {
  crm_company_id: string;
  crm_name: string;
  crm_tax_id: string | null;
  /** `nif` es contable; `name` es solo una sugerencia. */
  match_type: "nif" | "email" | "name";
  confidence: "high" | "medium" | "low";
  candidates: BulkMatchCandidate[];
};

export type BulkMatchDryRun = {
  total_crm_companies: number;
  total_factusol_customers: number;
  matches: BulkMatchRow[];
  no_match: { crm_company_id: string; crm_name: string; crm_tax_id: string | null }[];
  ejercicio: string;
};

/** Campos que el sync puede sobrescribir. `companies` no tiene teléfono ni
 *  email (viven en `contacts`), por eso no están. */
export const BULK_MATCH_FIELDS = [
  "name", "tax_id", "address_line", "city", "postal_code", "state",
] as const;

export const BULK_MATCH_FIELD_LABELS: Record<string, string> = {
  name: "Nombre", tax_id: "NIF", address_line: "Dirección",
  city: "Ciudad", postal_code: "CP", state: "Provincia",
};

export async function bulkMatchDryRun(
  body: { filter?: "unlinked_only" | "all"; batch_size?: number } = {},
): Promise<BulkMatchDryRun> {
  return apiFetch("/api/erp/factusol/bulk-match/dry-run", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** C-5-fix1: fila del modo «contactos por email». Lo que se actualiza es la
 *  **empresa del contacto**, no el contacto. */
export type BulkMatchByEmailRow = {
  contact_id: string;
  contact_name: string;
  contact_email: string;
  /** `null` = el contacto no tiene empresa: no hay nada que actualizar. */
  company_id: string | null;
  company_name: string | null;
  company_factusol_id: string | null;
  candidates: BulkMatchCandidate[];
};

export type BulkMatchByEmailDryRun = {
  total_contacts_with_email: number;
  matches: BulkMatchByEmailRow[];
  /** Solo el recuento: son miles y listarlos no aporta.
   *  `matches.length + no_match_count === total_contacts_with_email`. */
  no_match_count: number;
  /** Subconjunto de `matches`, NO una tercera categoría. */
  matches_without_company: number;
  /** `true` si se llegó al tope de seguridad y quedaron contactos sin
   *  evaluar. Normalmente `false`: se procesan todos (C-5-fix2). */
  truncated: boolean;
  ejercicio: string;
};

/** Desenlace de cada operación del modo por email (C-5-fix2). */
export type BulkMatchByEmailResult = {
  contact_id: string;
  result:
    /** Tenía empresa: se le han traído los datos limpios. */
    | "refreshed"
    /** No tenía empresa: se ha creado con los datos de F_CLI. */
    | "created_new_company"
    /** No tenía empresa, pero ya había una vinculada a ese CODCLI: se le
     *  asigna esa. Crear otra duplicaría el vínculo. */
    | "linked_existing_company"
    /** C-5-fix5: estaba mal agrupado. Se mueve a la empresa que ya está
     *  vinculada a su CODCLI. La original no se toca. */
    | "reassigned_to_existing_company"
    /** C-5-fix5: mal agrupado y no había empresa con ese CODCLI: se crea y
     *  se mueve. */
    | "reassigned_to_new_company"
    /** Desenlace de C-5-fix1, hoy prácticamente extinto: ese caso se
     *  reasigna. Se mantiene por el historial ya auditado. */
    | "skipped_already_linked_other";
  company_id?: string;
  /** Solo en las reasignaciones: de dónde venía el contacto. */
  old_company_id?: string;
  detail?: string;
};

/** Sin `batch_size` procesa TODOS los contactos con email. El parámetro existe
 *  solo como red de seguridad. */
export async function bulkMatchByEmailDryRun(
  body: { batch_size?: number } = {},
): Promise<BulkMatchByEmailDryRun> {
  return apiFetch("/api/erp/factusol/bulk-match/by-contact-email/dry-run", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function bulkMatchByEmailApply(
  operations: {
    contact_id: string;
    factusol_codcli: string;
    fields_to_sync: string[];
  }[],
): Promise<{
  applied: number;
  results: BulkMatchByEmailResult[];
  refreshed: number;
  created_new_company: number;
  linked_existing_company: number;
  reassigned_to_existing_company: number;
  reassigned_to_new_company: number;
  /** Suma de las dos anteriores, para el titular del resumen. */
  reassigned: number;
  skipped_already_linked_other: number;
  errors: { contact_id: string; error: string }[];
}> {
  return apiFetch("/api/erp/factusol/bulk-match/by-contact-email/apply", {
    method: "POST",
    body: JSON.stringify({ operations }),
  });
}

// --- C-6: importar F_CLI huérfanas ------------------------------------------

/** Cliente de FACTUSOL que no tiene ninguna empresa en el CRM. */
export type FactusolOrphan = {
  codcli: string | null;
  nofcli: string | null;
  noccli: string | null;
  nifcli: string | null;
  domcli: string | null;
  pobcli: string | null;
  cpocli: string | null;
  procli: string | null;
  paicli: string | null;
  emacli: string | null;
  telcli: string | null;
  /** `true` si trae `EMACLI`: sin email no se crea contacto. */
  will_create_contact: boolean;
};

export type ImportOrphansDryRun = {
  total_factusol_clientes: number;
  linked_already: number;
  orphans_to_import: number;
  with_email: number;
  without_email: number;
  orphans: FactusolOrphan[];
  ejercicio: string;
};

export type ImportOrphansResult = {
  codcli: string;
  result:
    | "imported_company_and_contact"
    | "imported_company_only"
    | "skipped_race";
  company_id: string | null;
  contact_id: string | null;
  /** Por qué no hubo contacto: sin email, email ya usado, o desactivado. */
  contact_skipped?: "no_email" | "email_taken" | "disabled";
  detail?: string;
};

export async function importOrphansDryRun(
  body: { filter?: "all" | "only_with_email" } = {},
): Promise<ImportOrphansDryRun> {
  return apiFetch("/api/erp/factusol/bulk-match/import-orphans/dry-run", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Los datos de F_CLI que viajan con cada operación del apply.
 *
 *  C-6-fix1: el backend releía `F_CLI` entera para esto y DELSOL devolvió `KO`
 *  66 segundos después de un dry-run correcto — el lote entero se fue en un
 *  502. El navegador ya tiene los datos; pedirlos otra vez solo añadía un punto
 *  de fallo. */
export type ImportOrphanOperation = {
  codcli: string;
  factusol_data: Omit<FactusolOrphan, "codcli" | "will_create_contact">;
};

/** Extrae del dry-run lo que el apply necesita.
 *
 *  Campo a campo, no con un spread: así el contrato del payload se ve, y si
 *  algún día `FactusolOrphan` crece no se cuela solo en la petición. */
export function orphanToOperation(row: FactusolOrphan): ImportOrphanOperation {
  return {
    codcli: row.codcli ?? "",
    factusol_data: {
      nofcli: row.nofcli, noccli: row.noccli, nifcli: row.nifcli,
      domcli: row.domcli, pobcli: row.pobcli, cpocli: row.cpocli,
      procli: row.procli, paicli: row.paicli, emacli: row.emacli,
      telcli: row.telcli,
    },
  };
}

export async function importOrphansApply(
  operations: ImportOrphanOperation[],
  createContactsIfEmail = true,
): Promise<{
  imported_company_and_contact: number;
  imported_company_only: number;
  skipped_race: number;
  imported: number;
  results: ImportOrphansResult[];
  errors: { codcli: string; error: string }[];
}> {
  return apiFetch("/api/erp/factusol/bulk-match/import-orphans/apply", {
    method: "POST",
    body: JSON.stringify({
      operations, create_contacts_if_email: createContactsIfEmail,
    }),
  });
}

export async function bulkMatchApply(
  operations: {
    crm_company_id: string;
    factusol_codcli: string;
    fields_to_sync: string[];
  }[],
): Promise<{ applied: number; errors: { crm_company_id: string; error: string }[] }> {
  return apiFetch("/api/erp/factusol/bulk-match/apply", {
    method: "POST",
    body: JSON.stringify({ operations }),
  });
}

export type CreateQuotePayload = {
  company_id: string;
  /** Serie = empresa emisora del documento (`TIPPRE` de F_PRE), como en el
   *  alta de pedido manual. Solo en el ALTA: la serie de una proforma que ya
   *  existe no se cambia al editarla. Omitir → 1 (Bomedia). */
  serie?: number;
  referencia?: string;
  lines?: {
    codart?: string;
    description: string;
    quantity: number;
    unit_price: number;
    discount_pct?: number;
    iva_pct?: number;
  }[];
  fecha?: string | null;
  /** Forma de pago del presupuesto (`F_PRE.FOPPRE`, código de F_FPA). */
  foppre?: string | null;
  /** Dirección de envío elegida; omitir → la de la empresa CRM. */
  address?: {
    direccion?: string;
    ciudad?: string;
    cp?: string;
    provincia?: string;
    pais?: string;
  } | null;
  /** Lote B3b: gastos de envío. Van a la banda de portes de la cabecera
   *  (IPOR1PRE), como en el albarán manual — NO como línea. 0/null → sin. */
  portes?: number | null;
  /** Lote B3b: destinatario libre (dropshipping). Pisa el bloque de envío de
   *  la cabecera (nombre + dirección); el cliente fiscal no cambia. Si viene
   *  junto con `address`, manda este. */
  shipping?: QuoteShippingInput | null;
};

/** Destinatario de envío libre de la proforma (Lote B3b). Todo opcional: sin
 *  nombre se conserva el del cliente; sin país, el del cliente. */
export type QuoteShippingInput = {
  name?: string;
  address_line?: string;
  city?: string;
  postal_code?: string;
  state?: string;
  country?: string;
};

export async function createFactusolQuote(
  payload: CreateQuotePayload,
): Promise<{ job_id: string; status: string }> {
  return apiFetch("/api/erp/factusol/quotes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** C-4-fix6: reescribe una proforma existente (cabecera + líneas).
 *  `force` salta el guard de estado; el job responde
 *  `code: "quote_not_editable"` cuando hace falta. */
export async function updateFactusolQuote(
  codpre: string, payload: CreateQuotePayload & { force?: boolean },
  serie?: number | null,
): Promise<{ job_id: string; status: string; codpre: string }> {
  // `serie` IDENTIFICA cuál se edita; no la cambia (va en la URL, no en el
  // cuerpo: el cuerpo de la edición no lleva serie a propósito).
  return apiFetch(
    `/api/erp/factusol/quotes/${encodeURIComponent(codpre)}${qs({ serie: serie ?? undefined })}`,
    { method: "PATCH", body: JSON.stringify(payload) },
  );
}

export async function duplicateFactusolQuote(
  codpre: string, serie?: number | null,
): Promise<{ job_id: string; status: string; source_codpre: string }> {
  return apiFetch(
    `/api/erp/factusol/quotes/${encodeURIComponent(codpre)}/duplicate${qs({ serie: serie ?? undefined })}`,
    { method: "POST" },
  );
}

/** El resumen del pedido que saldría de la proforma (empresa, fecha, líneas,
 *  importe, origen) y si ya se convirtió. Solo LEE: no crea nada. */
export type QuoteConvertPreview = ResumenPedido & {
  codpre: string;
  serie: number;
  company_linked: boolean;
  cliente_codigo: string | null;
  already_existed: { order_id: string; order_number: string } | null;
};

export async function getQuoteConvertPreview(
  codpre: string, serie?: number | null,
): Promise<QuoteConvertPreview> {
  return apiFetch(
    `/api/erp/factusol/quotes/${encodeURIComponent(codpre)}/convert-preview`
    + qs({ serie: serie ?? undefined }),
  );
}

/** Convierte la proforma en pedido de BoHub y, Fase 2, en el mismo job:
 *  apunta el pago (opción B) y crea el ALBARÁN en FACTUSOL. El resultado del
 *  job trae `albaran` ({numero, status}) o `albaran_error` / `albaran_skipped`. */
export async function convertFactusolQuoteToOrder(
  codpre: string,
  opts?: { payment?: PaymentIntentInput | null; create_albaran?: boolean },
  serie?: number | null,
): Promise<{ job_id: string; status: string; codpre: string }> {
  return apiFetch(
    `/api/erp/factusol/quotes/${encodeURIComponent(codpre)}/convert-to-order`
    + qs({ serie: serie ?? undefined }),
    { method: "POST", body: JSON.stringify(opts ?? {}) },
  );
}

export async function getQuoteJobStatus(jobId: string): Promise<QuoteJobStatus> {
  return apiFetch(`/api/erp/factusol/quotes/status/${encodeURIComponent(jobId)}`);
}

/** Espera a que el job termine. Devuelve su estado final, o `pending` si se
 *  agota el margen (el worker es serie: puede haber cola por delante). */
export async function waitForQuoteJob(
  jobId: string, { tries = 30, delayMs = 2000 } = {},
): Promise<QuoteJobStatus> {
  let last: QuoteJobStatus = { status: "pending" };
  for (let i = 0; i < tries; i++) {
    last = await getQuoteJobStatus(jobId);
    if (last.status !== "pending") return last;
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return last;
}

// --- Expedición manual: bultos + albarán + etiqueta (Fase D · D-1) -----------

export type ShipmentPackage = {
  id: string;
  position: number;
  weight_kg: number;
  height_cm: number;
  width_cm: number;
  depth_cm: number;
};

/** Un bulto tal como lo introduce el operativo en el modal (antes de guardar). */
export type PackageInput = {
  weight_kg: number | null;
  height_cm: number | null;
  width_cm: number | null;
  depth_cm: number | null;
};

/** `foto` = foto (o documento) del embalaje: un pedido puede tener varias. */
export type ShipmentFileKind = "albaran" | "etiqueta" | "foto";
export type ShipmentFileSource = "woo_pdf_plugin" | "manual_upload" | "factusol_pdf";

export type ShipmentFile = {
  id: string;
  kind: ShipmentFileKind;
  source: ShipmentFileSource;
  filename: string;
  mime_type: string;
  size_bytes: number;
  uploaded_by_user_id: string | null;
  uploaded_at: string | null;
  download_url: string;
};

export async function getPackages(orderId: string): Promise<ShipmentPackage[]> {
  const r = await apiFetch<{ items: ShipmentPackage[] }>(
    `/api/erp/orders/${orderId}/packages`,
  );
  return r.items;
}

export async function setPackages(
  orderId: string, packages: PackageInput[],
): Promise<{ packages: ShipmentPackage[] }> {
  return apiFetch(`/api/erp/orders/${orderId}/packages`, {
    method: "POST",
    body: JSON.stringify(packages),
  });
}

/** Marca el pedido `packed` (exige ≥1 bulto medido, si no → 400). */
export async function transitionPacked(
  orderId: string,
): Promise<{ order_id: string; preparation_status: string }> {
  return apiFetch(`/api/erp/orders/${orderId}/transition/preparation/packed`, {
    method: "POST",
  });
}

export async function listShippingFiles(
  orderId: string, kind?: ShipmentFileKind,
): Promise<ShipmentFile[]> {
  const q = kind ? `?kind=${kind}` : "";
  const r = await apiFetch<{ items: ShipmentFile[] }>(
    `/api/erp/orders/${orderId}/shipping-files${q}`,
  );
  return r.items;
}

/** Respuesta de la subida de un fichero de expedición. Lote 2 C: subir la
 *  ETIQUETA con el transporte «Sin enviar» aplica `not_shipped →
 *  label_created` en el backend (es el antiguo «Crear envío»). Si el engine
 *  la rechaza (pedido sin embalar, rol sin permiso), el fichero se guarda
 *  igual, `transition_applied` es false y `transition_reason` dice por qué.
 *  El albarán nunca mueve el transporte (`transition_reason` null). */
export type ShippingFileUploadResult = {
  file: ShipmentFile;
  transition_applied: boolean;
  transport_status: TransportStatus;
  transition_reason: string | null;
};

export async function uploadShippingFile(
  orderId: string, kind: ShipmentFileKind, file: File,
): Promise<ShippingFileUploadResult> {
  const form = new FormData();
  form.append("kind", kind);
  form.append("file", file);
  return apiUpload(`/api/erp/orders/${orderId}/shipping-files`, form);
}

export async function fetchAlbaranFromWoo(
  orderId: string,
): Promise<{ file: ShipmentFile; already_present: boolean }> {
  return apiFetch(`/api/erp/orders/${orderId}/albaran/fetch-from-woo`, {
    method: "POST",
  });
}

/** Descarga el PDF con auth y lo abre en una pestaña nueva (imprimible desde
 *  el diálogo del navegador — no hay impresora térmica en el taller). */
/** Una foto del embalaje que se perdió en un despliegue (antes se guardaban
 *  dentro del contenedor): solo nombre y fecha, para volver a subirla. */
export type FotoPerdida = {
  filename?: string | null;
  uploaded_at?: string | null;
  motivo?: string | null;
};

/** Fotos (y documentos) del embalaje del pedido, y las que se perdieron. */
export async function listFotos(
  orderId: string,
): Promise<{ items: ShipmentFile[]; fotos_perdidas: FotoPerdida[] }> {
  const r = await apiFetch<{ items: ShipmentFile[]; fotos_perdidas?: FotoPerdida[] }>(
    `/api/erp/orders/${orderId}/shipping-files?kind=foto`,
  );
  return { items: r.items, fotos_perdidas: r.fotos_perdidas ?? [] };
}

/** Miniatura JPEG de una foto (pequeña: para las tarjetas y la ficha), a
 *  partir de su `download_url`. */
export async function fetchShippingThumb(downloadUrl: string): Promise<Blob> {
  return apiDownloadBlob(`${downloadUrl}?thumb=1`);
}

export async function openShippingFile(file: ShipmentFile): Promise<void> {
  const blob = await apiDownloadBlob(file.download_url);
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener");
  // El navegador retiene el blob mientras la pestaña lo usa; lo liberamos tras
  // un margen para no cortar la apertura.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Descarga el PDF (etiqueta, albarán) y lanza el diálogo de IMPRIMIR en el
 *  mismo clic: se carga en un iframe oculto y se imprime. Si el navegador no
 *  deja imprimir así el PDF, se abre en una pestaña (como `openShippingFile`). */
export async function printShippingFile(file: ShipmentFile): Promise<void> {
  const blob = await apiDownloadBlob(file.download_url);
  const url = URL.createObjectURL(blob);
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.title = "Impresión";
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
  const limpiar = () => setTimeout(() => {
    frame.remove();
    URL.revokeObjectURL(url);
  }, 60_000);
  frame.onload = () => {
    try {
      frame.contentWindow?.focus();
      frame.contentWindow?.print();
    } catch {
      window.open(url, "_blank", "noopener");
    }
    limpiar();
  };
  frame.src = url;
  document.body.appendChild(frame);
}

// --- ERP-F4-A · conciliación bancaria (sin escribir en FACTUSOL) -------------

export type BankAccount = {
  id: string;
  name: string;
  bank_name: string | null;
  iban: string;
  bic: string | null;
  currency: string;
  serie: number | null;
  /** ERP-F5 — contrapartida de cobro de FACTUSOL enlazada (código del
   *  catálogo configurable) y su nombre resuelto. */
  contrapartida_codigo: string | null;
  contrapartida_nombre?: string | null;
  column_mapping: Record<string, string> | null;
  has_statement_header: boolean;
  /** La hoja de Drive de la cuenta (identificador; al guardar vale el
   *  enlace), la pestaña (vacía: la primera) y si BoHub escribe en ella. */
  drive_file_id?: string | null;
  drive_pestana?: string | null;
  drive_escribir?: boolean;
  /** Qué es el fichero, visto en la última lectura: hoja de Google (se
   *  escribe) o .xlsx subido (solo se lee). */
  drive_formato?: BankDriveFormato | null;
  drive_leida_at?: string | null;
  drive_resultado?: BankDriveResultado | null;
  /** Movimientos conciliados pendientes de escribir en la hoja. */
  drive_pendientes?: number;
};

export type BankDriveFormato = "hoja" | "xlsx";

/** La escritura en la hoja de una pasada: cuántas filas se escribieron y
 *  cuántas no, y por qué. */
export type BankDriveEscritura = {
  pendientes?: number;
  escritas?: number;
  ocupadas?: number;
  no_encontradas?: number;
  corridas?: number;
  sin_columna?: number;
  movidas?: number;
  error?: string | null;
  origen?: string;
  at?: string;
};

/** La última pasada de Drive de una cuenta (botón o tic diario). Solo
 *  recuentos: nada de la hoja. */
export type BankDriveResultado = {
  origen?: "manual" | "programada" | string;
  at?: string;
  ok?: boolean;
  code?: string | null;
  error?: string | null;
  formato?: BankDriveFormato;
  imported?: number;
  duplicates?: number;
  no_entendidas_total?: number;
  recolocadas?: number;
  escritura?: BankDriveEscritura | null;
};

export type BankAccountSuggestion = Omit<
  BankAccount,
  | "id" | "column_mapping" | "has_statement_header" | "serie" | "contrapartida_codigo"
  | "contrapartida_nombre" | "drive_file_id" | "drive_pestana" | "drive_escribir"
  | "drive_formato" | "drive_leida_at" | "drive_resultado" | "drive_pendientes"
>;

export type BankConfidence = "alta" | "media" | "baja";

/** Propuesta (o conciliación confirmada) movimiento → factura. */
export type BankProposal = {
  id: string;
  serie: number;
  codigo: number;
  numero: string;
  cliente_nombre: string | null;
  importe: number;
  confidence: BankConfidence | null;
  reason: string | null;
  status: "proposed" | "confirmed";
  /** «extracto» = venía anotado a mano en la columna `factura` del extracto
   *  (la respuesta de Bart, no una propuesta de la máquina); «pedido» =
   *  confirmado desde la ficha del pedido. */
  origen?: "extracto" | "pedido" | null;
  /** El pedido de BoHub de esa factura, para ir a su ficha. */
  pedido?: { id: string; order_number: string } | null;
};

export type BankMovement = {
  id: string;
  account_id: string;
  fecha_oper: string;
  /** Fecha valor (con la que se registra el cobro); null = la de operación. */
  fecha_valor?: string | null;
  concepto: string;
  importe: number;
  saldo: number | null;
  referencia1: string | null;
  referencia2: string | null;
  payer_name: string | null;
  status: "pending" | "reconciled" | "discarded";
  discard_reason: string | null;
  /** Lo que Bart escribió a mano en las columnas `factura`/`pedido`. */
  anotacion_factura?: string | null;
  anotacion_pedido?: string | null;
  /** La escritura de la factura y el pedido en la hoja de Drive. */
  hoja_estado?: BankHojaEstado | null;
  hoja_estado_at?: string | null;
  /** Lo que escribió BoHub en la hoja. */
  hoja_factura?: string | null;
  hoja_pedido?: string | null;
  confidence: BankConfidence | null;
  proposals: BankProposal[];
  /** Propuestas contra facturas externas que no se ofrecen (casilla apagada). */
  externas_ocultas?: number;
  reconciled: BankProposal[];
};

export type BankHojaEstado =
  | "pendiente" | "escrita" | "ocupada" | "no_encontrada" | "corrida" | "sin_columna";

export type BankCounters = {
  pending: number;
  reconciled: number;
  discarded: number;
  pending_amount: number;
  /** Por conciliar que solo casan con facturas externas (sin pedido): 0 con
   *  la casilla de externas encendida. */
  solo_externas?: number;
};

export type BankMovementsPage = {
  items: BankMovement[];
  total: number;
  counters: BankCounters;
};

export type BankMatchStats = {
  ok: boolean;
  detail?: string;
  candidates?: number;
  excluded?: number;
  proposed?: number;
  no_proposal?: number;
  pending_invoices?: number;
  by_confidence?: Record<BankConfidence, number>;
};

export type BankImportSummary = {
  ok: boolean;
  code: string;
  account_id?: string;
  account_name?: string;
  iban?: string | null;
  detail?: string;
  total_rows: number;
  imported: number;
  duplicates: number;
  /** Formato reconocido («Sabadell», «Belfius»…). */
  formato?: string | null;
  /** Filas sin fecha ni importe que se unieron al movimiento de encima. */
  continuaciones?: number;
  /** Filas corridas (un nombre partido por una coma) que se recolocaron. */
  recolocadas?: number;
  /** Las filas que no se entendieron: se importa el resto igual. */
  no_entendidas_total?: number;
  no_entendidas?: BankNoEntendida[];
  /** Lo que venía anotado en la columna `factura`. */
  anotadas?: {
    confirmadas: number; propuestas: number; sin_factura: number;
    /** Repetidos que no tenían anotación y ahora sí la traen. */
    nuevas_en_reimportados?: number;
  };
  /** «1.240 movimientos importados, 3 filas no entendidas (139, 144, 802)». */
  resumen?: string;
  matching?: BankMatchStats;
};

export type BankNoEntendida = {
  fila: number;
  motivo: string;
  valores: string[];
};

export type BankRule = {
  id: string;
  kind: "exclude_pattern" | "payer_to_client";
  pattern: string;
  client_codcli: string | null;
  client_nombre: string | null;
  note: string | null;
};

export async function listBankAccounts(): Promise<BankAccount[]> {
  const r = await apiFetch<{ items: BankAccount[] }>("/api/erp/bank/accounts");
  return r.items;
}

/** Las cuentas que conoce FACTUSOL (F_BAN) como sugerencia de alta. */
export async function listSuggestedBankAccounts(): Promise<BankAccountSuggestion[]> {
  const r = await apiFetch<{ items: BankAccountSuggestion[] }>("/api/erp/bank/accounts/suggested");
  return r.items;
}

export async function createBankAccount(
  payload: Omit<BankAccountSuggestion, "bank_name" | "bic">
    & Partial<Pick<BankAccount, "bank_name" | "bic" | "serie" | "contrapartida_codigo">>,
): Promise<BankAccount> {
  return apiFetch("/api/erp/bank/accounts", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateBankAccount(
  id: string,
  patch: Partial<Omit<
    BankAccount,
    | "id" | "has_statement_header" | "drive_formato" | "drive_leida_at" | "drive_resultado"
    | "drive_pendientes"
  >>,
): Promise<BankAccount> {
  return apiFetch(`/api/erp/bank/accounts/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export async function deleteBankAccount(id: string): Promise<void> {
  await apiFetch(`/api/erp/bank/accounts/${id}`, { method: "DELETE" });
}

/** Sube el extracto (.xls/.xlsx/.csv). Identifica la cuenta por el IBAN de la
 *  cabecera; si no está dada de alta el backend responde 409 (no importa a
 *  ciegas). Nunca concilia nada por sí solo: solo propone. */
export async function importBankStatement(
  file: File, opts: { accountId?: string; runMatch?: boolean } = {},
): Promise<BankImportSummary> {
  const form = new FormData();
  form.append("file", file);
  if (opts.accountId) form.append("account_id", opts.accountId);
  form.append("run_match", String(opts.runMatch ?? true));
  return apiUpload("/api/erp/bank/import", form);
}

/** Con qué cuenta de servicio se comparten las hojas del banco (la de
 *  Seguimiento) y la hora del repaso diario. */
export type BankDriveInfo = {
  configured: boolean;
  service_account_email: string | null;
  hora: string;
};

export async function getBankDrive(): Promise<BankDriveInfo> {
  return apiFetch("/api/erp/bank/drive");
}

/** El resultado de «Traer de Drive» en una cuenta: el resumen del import de
 *  siempre más lo de Drive, o el error (Drive no responde, no compartida…). */
export type BankDriveTraida = BankImportSummary & {
  account_id: string;
  account_name: string;
  drive?: BankDriveResultado;
};

/** «Traer de Drive»: lee la hoja de esa cuenta (o de todas las que la
 *  tienen), importa lo que falta y escribe lo pendiente. No escribe en
 *  FACTUSOL. */
export async function traerBankDrive(accountId?: string): Promise<BankDriveTraida[]> {
  const q = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  const r = await apiFetch<{ resultados: BankDriveTraida[] }>(
    `/api/erp/bank/drive/traer${q}`, { method: "POST" },
  );
  return r.resultados;
}

/** El export del banco contra la hoja de Drive de la cuenta: cuántos trae,
 *  cuántos ya están y los que faltan (de más reciente a más antiguo). Con
 *  `aplicado`, ya se han añadido arriba de la hoja y se han importado. */
export type BankExportPlan = {
  total: number;
  ya: number;
  nuevos: number;
  movimientos: { fecha_oper: string; concepto: string; importe: number; saldo: number | null }[];
  aplicado: boolean;
  importacion?: BankDriveTraida;
  /** Las filas ya están en la hoja pero no se pudieron leer ahora. */
  error_importacion?: string;
};

/** Sube el fichero del banco tal cual (.xls/.xlsx/.csv). Sin `aplicar`, la
 *  vista previa; con `aplicar`, añade a la hoja lo que falta (sin tocar
 *  ninguna fila que ya estaba) y lo importa. */
export async function anadirExportBankDrive(
  file: File, accountId: string, aplicar: boolean,
): Promise<BankExportPlan> {
  const form = new FormData();
  form.append("file", file);
  form.append("account_id", accountId);
  form.append("aplicar", String(aplicar));
  return apiUpload("/api/erp/bank/drive/anadir", form);
}

/** «Escribir ahora»: lo conciliado pendiente de esa cuenta, a su hoja. */
export async function escribirBankDrive(accountId: string): Promise<BankDriveEscritura> {
  return apiFetch(`/api/erp/bank/drive/escribir?account_id=${encodeURIComponent(accountId)}`,
    { method: "POST" });
}

export async function runBankMatch(accountId?: string): Promise<BankMatchStats> {
  const q = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  return apiFetch(`/api/erp/bank/match${q}`, { method: "POST" });
}

export type BankMovementFilters = {
  /** Un movimiento concreto (el enlace «Ver el movimiento» de la ficha). */
  movimiento?: string;
  account_id?: string;
  desde?: string;
  hasta?: string;
  confidence?: BankConfidence | "none";
  status?: BankMovement["status"];
  /** El ejercicio: los movimientos de ese año. */
  ejercicio?: string;
  /** «Incluir facturas externas»: sin ella, solo se proponen facturas con pedido. */
  externas?: boolean;
  /** Solo los que casan únicamente con facturas externas (el contador pulsado). */
  solo_externas?: boolean;
  /** Solo los conciliados cuyo cobro aún no está en FACTUSOL (el contador). */
  sin_cobro?: boolean;
  limit?: number;
  offset?: number;
};

/** Los años que tienen movimientos en el banco (Conciliación), con su recuento:
 *  el extracto va más atrás que los ejercicios de FACTUSOL. */
export type BankAnio = {
  anio: string; movimientos: number; pending: number; reconciled: number; discarded: number;
  pending_amount: number;
};
export type BankEjercicios = {
  en_curso: string; anios: BankAnio[]; total: Omit<BankAnio, "anio">;
};

export async function getBankEjercicios(accountId?: string): Promise<BankEjercicios> {
  return apiFetch(`/api/erp/bank/ejercicios${qs({ account_id: accountId })}`);
}

export async function listBankMovements(filters: BankMovementFilters = {}): Promise<BankMovementsPage> {
  const { externas, solo_externas, sin_cobro, ...rest } = filters;
  return apiFetch(`/api/erp/bank/movements${qs({
    ...rest,
    externas: externas === undefined ? undefined : String(externas),
    solo_externas: solo_externas ? "true" : undefined,
    sin_cobro: sin_cobro ? "true" : undefined,
  })}`);
}

/** «Conciliados sin cobro registrado» del ejercicio en curso: conciliados
 *  cuyo cobro aún no está en FACTUSOL. `ok: false` si FACTUSOL no responde. */
export async function getBankSinCobro(
  accountId?: string,
): Promise<{ ok: boolean; ejercicio: string; count: number; ids: string[] }> {
  return apiFetch(`/api/erp/bank/sin-cobro${qs({ account_id: accountId })}`);
}

/** Confirma la propuesta. Sin `externas` (la casilla apagada), el backend no
 *  confirma nada contra una factura externa que no se está enseñando. */
export async function confirmBankMovement(id: string, externas = true): Promise<BankMovement> {
  return apiFetch(`/api/erp/bank/movements/${id}/confirm?externas=${String(externas)}`,
    { method: "POST" });
}

export type BankReassignTarget = {
  serie: number;
  codigo: number;
  numero?: string;
  importe: number;
  cliente_nombre?: string | null;
  cliente_codigo?: string | null;
};

/** Elegir otra factura o repartir entre varias (queda confirmado: es una
 *  decisión explícita). `learnPayer` recuerda pagador → cliente. */
export async function reassignBankMovement(
  id: string, targets: BankReassignTarget[], learnPayer = false,
): Promise<BankMovement> {
  return apiFetch(`/api/erp/bank/movements/${id}/reassign`, {
    method: "POST", body: JSON.stringify({ targets, learn_payer: learnPayer }),
  });
}

/** «No es un cobro de cliente» (con motivo). `learn` recuerda el pagador
 *  como exclusión para importaciones futuras. */
export async function discardBankMovement(
  id: string, reason: string, learn = false,
): Promise<BankMovement> {
  return apiFetch(`/api/erp/bank/movements/${id}/discard`, {
    method: "POST", body: JSON.stringify({ reason, learn }),
  });
}

export async function reopenBankMovement(id: string): Promise<BankMovement> {
  return apiFetch(`/api/erp/bank/movements/${id}/reopen`, { method: "POST" });
}

/** Acción en bloque (un clic): confirma las propuestas de confianza alta. */
/** Confirma las de confianza alta de lo que se ve: el ejercicio elegido y,
 *  sin `externas`, solo las propuestas contra facturas con pedido. */
export async function confirmAllHighBank(
  accountId?: string, opts: { externas?: boolean; ejercicio?: string } = {},
): Promise<{ confirmed: number }> {
  const q = qs({
    account_id: accountId,
    externas: opts.externas === undefined ? undefined : String(opts.externas),
    ejercicio: opts.ejercicio,
  });
  return apiFetch(`/api/erp/bank/movements/confirm-high${q}`, { method: "POST" });
}

export async function listBankRules(): Promise<BankRule[]> {
  const r = await apiFetch<{ items: BankRule[] }>("/api/erp/bank/rules");
  return r.items;
}

export async function createBankRule(
  payload: Pick<BankRule, "kind" | "pattern"> & Partial<Pick<BankRule, "client_codcli" | "client_nombre" | "note">>,
): Promise<{ id: string }> {
  return apiFetch("/api/erp/bank/rules", { method: "POST", body: JSON.stringify(payload) });
}

export async function deleteBankRule(id: string): Promise<void> {
  await apiFetch(`/api/erp/bank/rules/${id}`, { method: "DELETE" });
}

/** Excel NUEVO con el formato del extracto y FACTURA/PRESUPUESTO/PEDIDO
 *  rellenas con lo confirmado. El original no se toca. */
export async function downloadBankExport(
  accountId: string, opts: { desde?: string; hasta?: string } = {},
): Promise<Blob> {
  return apiDownloadBlob(`/api/erp/bank/export${qs({ account_id: accountId, ...opts })}`);
}

/** El CSV de cobros y lo que cuenta el servidor: cuántas facturas lleva y
 *  cuántas se han dejado fuera, y por qué. */
export type BankCobrosCsv = {
  blob: Blob;
  incluidas: number;
  /** Cobro parcial: el script registraría el saldo entero. */
  parciales: number;
  /** No están pendientes en FACTUSOL (ya cobradas, o de otro ejercicio). */
  noPendientes: number;
  /** El movimiento tiene conciliado más que su importe. */
  noCuadran: number;
  /** La cuenta del banco no tiene contrapartida de cobro. */
  sinContrapartida: number;
};

/** CSV para `registrar_cobros_lote.ps1` (serie, codigo, cuenta, forma,
 *  fecha, observaciones) con lo conciliado y no descartado que cuadra con el
 *  saldo pendiente de FACTUSOL. No escribe en FACTUSOL: lo registra el
 *  script, que simula primero. */
export async function downloadBankCobrosCsv(
  opts: { accountId?: string; desde?: string; hasta?: string } = {},
): Promise<BankCobrosCsv> {
  const { blob, headers } = await apiDownloadConCabeceras(`/api/erp/bank/export-cobros${qs({
    account_id: opts.accountId, desde: opts.desde, hasta: opts.hasta,
  })}`);
  const n = (h: string) => Number(headers.get(`X-Cobros-${h}`) ?? 0) || 0;
  return {
    blob,
    incluidas: n("Incluidos"),
    parciales: n("Parciales"),
    noPendientes: n("No-Pendientes"),
    noCuadran: n("No-Cuadran"),
    sinContrapartida: n("Sin-Contrapartida"),
  };
}

// --- Lote 3 · Cola SAT — edición inline de seguimiento ----------------------

/** Valores de seguimiento que edita la Cola SAT (ya recortados por el backend:
 *  null si quedan vacíos). */
export type SeguimientoFieldsPatch = {
  serial_number: string | null;
  whiterip_license: string | null;
  shipping_origin: string | null;
};

/** Lote 3 · Cola SAT — guarda nº de serie, licencia WhiteRIP y origen del
 *  envío desde la cola SIN salir de ella. Reusa el MISMO endpoint de la ficha
 *  (`PATCH /api/erp/orders/{id}/seguimiento`, `updateOrderSeguimiento`) sin
 *  duplicar la llamada; solo envía los tres campos de la cola (no toca
 *  observaciones) y devuelve los valores ya normalizados por el backend. */
export async function updateSeguimientoFields(
  orderId: string,
  payload: {
    serial_number?: string;
    whiterip_license?: string;
    shipping_origin?: string;
  },
): Promise<SeguimientoFieldsPatch> {
  const r = await updateOrderSeguimiento(orderId, payload);
  return {
    serial_number: r.serial_number,
    whiterip_license: r.whiterip_license,
    shipping_origin: r.shipping_origin,
  };
}

/** Lote 3 · ficha — FUENTE ÚNICA del estado de cobro de la ficha. El estado
 *  leído EN VIVO de FACTUSOL (`cobroLive`) manda sobre el persistido en el
 *  pedido (`factusol_cobro_status`), que puede quedar en `null` justo después
 *  de un re-vínculo. Todos los indicadores de cobro de la ficha (rejilla /
 *  casilla «Cobro», bloque ámbar «pendiente de cobro», badge FACTUSOL y el
 *  guard del botón «Registrar cobro») derivan de aquí, para que nunca
 *  convivan «cobrado» y «no cobrado». */
export function resolveOrderCobroStatus(
  order: { factusol_cobro_status?: FactusolCobroStatus | null },
  cobroLive: { status?: string | null } | null | undefined,
): FactusolCobroStatus | null {
  const live = cobroLive?.status === "cobrada" || cobroLive?.status === "pendiente"
    ? cobroLive.status
    : null;
  return live ?? order.factusol_cobro_status ?? null;
}

/** Lote 4 · cliente FACTUSOL del pedido, resuelto por CODCLI — también los WEB.
 *  De dónde salió el CODCLI: el CLIPCL del pedido de cliente F_PCL que cargó la
 *  app externa (Lote 6, PREFERENTE en los web), la empresa CRM vinculada, el
 *  CLIFAC de la factura o el CLIALB del albarán. */
export type OrderFactusolCustomerSource =
  | "pedido_cliente" | "company" | "factura" | "albaran";

export type OrderFactusolCustomer = {
  found: boolean;
  codcli: string | null;
  source: OrderFactusolCustomerSource | null;
  /** Ficha F_CLI normalizada (nº, nombre, NIF, régimen…). */
  cliente: FactusolCustomer | null;
  /** Campos identificativos vacíos que se pueden completar (p. ej. `"nif"`). */
  missing: string[];
  company_id: string | null;
};

/** Cliente FACTUSOL (F_CLI) del pedido por su CODCLI. `found:false` = ni empresa
 *  vinculada, ni factura, ni albarán con los que localizarlo. Solo lectura. */
export async function getOrderFactusolCustomer(orderId: string): Promise<OrderFactusolCustomer> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/factusol-customer`);
}

/** Campos completables del cliente F_CLI (solo texto). Se envía únicamente lo
 *  que entra el operador; los vacíos no se tocan. */
export type OrderFactusolCustomerFields = {
  nombre?: string;
  nif?: string;
  direccion?: string;
  ciudad?: string;
  cp?: string;
  provincia?: string;
  email?: string;
  telefono?: string;
};

/** Completa en FACTUSOL (F_CLI, ActualizarRegistro — SOLO las columnas que
 *  cambian) los datos que faltan del cliente del pedido (p. ej. el NIF). NUNCA
 *  inventa: solo escribe lo entrado. Deja auditoría. */
export async function completeOrderFactusolCustomer(
  orderId: string,
  fields: OrderFactusolCustomerFields,
): Promise<{
  ok: boolean;
  order_id: string;
  codcli: string;
  source: OrderFactusolCustomerSource | null;
  changed: boolean;
  written: Record<string, string>;
  cliente: FactusolCustomer | null;
  missing: string[];
}> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/factusol-customer`, {
    method: "POST",
    body: JSON.stringify({ confirm: true, ...fields }),
  });
}

/** Lote 6 · vincula la EMPRESA del pedido al cliente FACTUSOL EXACTO que
 *  resuelve su CODCLI (en un pedido web, el CLIPCL del F_PCL que cargó la app
 *  externa — autoritativo). NUNCA crea un cliente en FACTUSOL (lo duplicaría):
 *  solo escribe `Company.factusol_company_id` en el CRM. Deja auditoría. */
export async function linkOrderFactusolCompany(orderId: string): Promise<{
  ok: boolean;
  order_id: string;
  company_id: string;
  codcli: string;
  source: OrderFactusolCustomerSource | null;
  linked: boolean;
}> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/factusol-customer/link`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
}

/** Lote 5 · #3 — guarda el nº de seguimiento del pedido (Cola SAT «Listos»).
 *  Solo persiste el tracking: NO marca el pedido recogido ni enviado. Gate de
 *  VISTA (mismo que «Marcar recogido»), así que el taller/SAT puede rellenarlo.
 *  Vacío → null. En un envío con OTRO courier (no Genei) guarda también el
 *  courier (`courier` ausente = no se toca; "" lo borra). */
export async function setOrderTracking(
  orderId: string, tracking: string | null, courier?: string | null,
): Promise<{ id: string; tracking_number: string | null; courier?: string | null }> {
  const body: Record<string, string | null> = { tracking_number: tracking };
  if (courier !== undefined) body.courier = courier;
  return apiFetch(`/api/erp/orders/${orderId}/tracking`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** Lote 7 · P4 — «Crear pedido» desde un ALBARÁN o una FACTURA de FACTUSOL
 *  (Documentos). Reutiliza el alta de pedido desde documento, ampliada a estos
 *  dos tipos: el cliente se resuelve por CLIALB/CLIFAC (o `company_id` explícito
 *  del cruce CRM de la fila) y el pedido queda ligado a su albarán / factura.
 *  Solo se LEE el documento: `create_albaran` va a false porque el albarán /
 *  la factura ya EXISTEN en FACTUSOL (no se escribe nada allí). Devuelve el
 *  pedido creado (id + nº) para reflejarlo en la fila, como «Vincular». */
export async function createOrderFromDocumentType(
  docType: "albaranes" | "facturas", serie: number, codigo: number,
  opts: { company_id?: string | null; contact_id?: string | null } = {},
): Promise<OrderDetail & AlbaranJobExtra> {
  return apiFetch<OrderDetail & AlbaranJobExtra>("/api/erp/orders/from-factusol", {
    method: "POST",
    body: JSON.stringify({
      doc_type: docType,
      serie,
      codigo,
      company_id: opts.company_id ?? undefined,
      contact_id: opts.contact_id ?? undefined,
      create_albaran: false,
    }),
  });
}

/** Lote 7 · P1 — cambia la SERIE (empresa emisora) de un pedido MANUAL. Manda
 *  en `resolve_serie`, así que todo lo que BoHub emita desde el pedido sale en
 *  esa empresa. Si el pedido YA tiene albarán, el backend encola en el worker
 *  serial el borrado del albarán viejo y su re-creación en la serie nueva
 *  (`factusol_serie_job_id`; se sigue con `getQuoteJobStatus`); sin albarán,
 *  solo registra la serie. 409 si ya tiene FACTURA (se anula desde FACTUSOL) o
 *  si es un pedido web. `confirm` obligatorio. */
/** Cambia la FECHA del pedido (solo los que no vienen de la tienda; el
 *  backend responde 409 `web_order` para un pedido web). Queda en la auditoría. */
export async function changeOrderDate(
  orderId: string, fecha: string,
): Promise<{ id: string; changed: boolean; placed_at: string | null }> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/fecha`, {
    method: "PATCH",
    body: JSON.stringify({ fecha }),
  });
}

export async function changeOrderFactusolSerie(
  orderId: string, serie: number,
): Promise<OrderDetail & {
  factusol_serie_job_id?: string | null;
  requested_serie?: number;
}> {
  return apiFetch(`/api/erp/orders/${encodeURIComponent(orderId)}/factusol-serie`, {
    method: "POST",
    body: JSON.stringify({ serie, confirm: true }),
  });
}


// --- ERP · Cuadre: descuadres entre BoHub, FACTUSOL, envíos y la hoja ----------

export type CuadreSeveridad = "alta" | "media" | "baja";

/** De dónde lee una comprobación: la BD de BoHub (al momento), FACTUSOL o las
 *  tiendas WooCommerce (estas dos, en segundo plano en el worker). */
export type CuadreFuente = "mysql" | "factusol" | "woocommerce";

/** Una comprobación del registro del Cuadre. */
export type CuadreComprobacion = {
  id: string;
  titulo: string;
  descripcion: string;
  severidad: CuadreSeveridad;
  fuente: CuadreFuente;
  grupo: "dinero" | "envios" | "documentos" | "integraciones" | "crm";
  /** Umbral en días por defecto (null = la comprobación no usa umbral). */
  dias_defecto: number | null;
  /** Qué significa el umbral («Avisar pasados N días»). */
  dias_texto: string | null;
  orden: number;
  /** Activa si Configuración ERP no dice otra cosa. */
  activa_defecto?: boolean;
};

export type CuadreConfig = {
  /** Versión de los valores por defecto (la pone el backend). */
  version?: number;
  nocturno_activo: boolean;
  hora: string;
  checks: Record<string, { activo: boolean; dias: number | null }>;
};

/** Una pasada (nocturna o «Comprobar ahora») sobre una fuente. */
export type CuadrePasada = {
  id: string;
  fuente: CuadreFuente;
  origen: "nocturno" | "manual";
  estado: "en_cola" | "corriendo" | "ok" | "con_errores" | "error";
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
  error: string | null;
  resumen: Record<string, { hallazgos?: number; nuevos?: number; error?: string }>;
};

export type CuadreTarjeta = CuadreComprobacion & {
  dias: number | null;
  abiertos: number;
  revisados: number;
  nuevos: number;
};

export type CuadreResumen = {
  contadores: Record<CuadreSeveridad | "total", number>;
  ultima_pasada: CuadrePasada | null;
  ultimas_por_fuente: Record<string, CuadrePasada | null>;
  /** Pasadas en cola / corriendo: la pantalla enseña «comprobando…». */
  en_curso: CuadrePasada[];
  nocturno: { activo: boolean; hora: string };
  checks: CuadreTarjeta[];
};

export type CuadreHallazgo = {
  id: string;
  check_id: string;
  check_titulo: string;
  severidad: CuadreSeveridad;
  estado: "abierto" | "revisado" | "resuelto";
  entidad_tipo: "pedido" | "factura" | "presupuesto" | "fila_hoja" | string;
  entidad_id: string;
  etiqueta: string;
  detalle: string;
  pista_de_arreglo: string;
  /** Enlace a la entidad (pedido, documento, fila). */
  enlace: string | null;
  /** Botón «a donde se arregla»: pantalla con la acción que ya existe. */
  arreglo_enlace: string | null;
  arreglo_boton: string | null;
  datos: Record<string, unknown>;
  motivo: string | null;
  visto_por: string | null;
  revisado_at: string | null;
  primera_vez_at: string | null;
  ultima_vez_at: string | null;
  abierto_at: string | null;
  nuevo: boolean;
};

export type CuadreFiltros = {
  check_id?: string;
  severidad?: CuadreSeveridad;
  solo_nuevos?: boolean;
  incluir_revisados?: boolean;
};

export async function getCuadreResumen(): Promise<CuadreResumen> {
  return apiFetch<CuadreResumen>("/api/erp/cuadre/resumen");
}

export async function listCuadreHallazgos(
  filtros: CuadreFiltros = {},
): Promise<{ items: CuadreHallazgo[]; total: number }> {
  return apiFetch(`/api/erp/cuadre/hallazgos${qs({
    check_id: filtros.check_id,
    severidad: filtros.severidad,
    solo_nuevos: filtros.solo_nuevos ? "true" : undefined,
    incluir_revisados: filtros.incluir_revisados ? "true" : undefined,
  })}`);
}

/** «Revisado / no es un descuadre», con motivo corto obligatorio. */
export async function revisarCuadreHallazgo(id: string, motivo: string): Promise<CuadreHallazgo> {
  return apiFetch<CuadreHallazgo>(`/api/erp/cuadre/hallazgos/${encodeURIComponent(id)}/revisar`, {
    method: "POST", body: JSON.stringify({ motivo }),
  });
}

/** «Volver a incluir» un descuadre revisado. */
export async function reincluirCuadreHallazgo(id: string): Promise<CuadreHallazgo> {
  return apiFetch<CuadreHallazgo>(
    `/api/erp/cuadre/hallazgos/${encodeURIComponent(id)}/reincluir`, { method: "POST" },
  );
}

/** «Comprobar ahora»: BoHub al momento; FACTUSOL en segundo plano (worker). */
export async function comprobarCuadre(): Promise<{
  lanzadas: Partial<Record<CuadreFuente, { id?: string; estado: string } | null>>;
  resumen: CuadreResumen;
}> {
  return apiFetch("/api/erp/cuadre/comprobar", { method: "POST" });
}

/** Excel con todos los descuadres abiertos. */
export async function exportCuadreXlsx(): Promise<Blob> {
  return apiDownloadBlob("/api/erp/cuadre/export");
}

// --- ERP · Cuadre → Colas: registros de fallidos de RQ ------------------------
// Hasta ahora la única salida para 149.603 trabajos fallidos era entrar al
// contenedor. Reencolar y vaciar son de admin y van con vista previa.

export interface ColasResumen {
  fallidos_por_cola: Record<string, number>;
  fallidos_total: number;
  bytes_por_trabajo_medio: number;
  bytes_estimados: number;
  redis_usada_bytes: number;
  muestra: number;
}

export interface ColaTrabajoFallido {
  id: string;
  funcion: string;
  fecha: string | null;
  argumentos: string;
  error: string;
}

export interface ColaOperacion {
  cola: string;
  probar: boolean;
  en_registro: number;
  /** reencolar */
  elegidos?: number;
  reencolados?: number;
  fallos_al_reencolar?: number;
  revisados?: number;
  ventana_agotada?: boolean;
  /** vaciar */
  descartados?: number;
  sin_datos?: number;
  fallos?: number;
  quedan?: number;
  tope?: number;
  ejemplo?: { id: string; funcion: string; fecha: string | null };
}

export async function getColasResumen(): Promise<ColasResumen> {
  return apiFetch<ColasResumen>("/api/erp/cuadre/colas");
}

/** Los fallidos de una cola con sus argumentos: es lo que hace falta para
 *  repetir a mano una operación perdida. */
export async function listColaFallidos(
  cola: string, opts: { limite?: number; funcion?: string } = {},
): Promise<{
  cola: string; funcion: string | null; en_el_registro: number;
  mostrados: number; trabajos: ColaTrabajoFallido[];
}> {
  return apiFetch(
    `/api/erp/cuadre/colas/${encodeURIComponent(cola)}/fallidos${qs({
      limite: opts.limite, funcion: opts.funcion,
    })}`,
  );
}

export async function reencolarCola(
  cola: string, opts: { probar: boolean; limite?: number; funcion?: string },
): Promise<ColaOperacion> {
  return apiFetch<ColaOperacion>("/api/erp/cuadre/colas/reencolar", {
    method: "POST",
    body: JSON.stringify({
      cola, probar: opts.probar, limite: opts.limite ?? 500,
      ...(opts.funcion ? { funcion: opts.funcion } : {}),
    }),
  });
}

export async function vaciarCola(
  cola: string, opts: { probar: boolean },
): Promise<ColaOperacion> {
  return apiFetch<ColaOperacion>("/api/erp/cuadre/colas/vaciar", {
    method: "POST",
    body: JSON.stringify({ cola, probar: opts.probar }),
  });
}

// --- Respuesta a leads (Fase 1): clasificar, preparar el borrador, colocar ----
// La Fase 1 NO envía ni un correo al cliente: clasifica, deja un borrador,
// coloca en el pipeline y crea una tarea. Todo lo de aquí es leer, corregir a
// mano y simular en seco; la configuración va en `ErpSettings.lead_response`.

/** Ventana horaria de la espera del workflow (hora de Madrid). */
export type LeadResponseVentana = {
  enabled: boolean;
  start: string;
  end: string;
  weekdays_only: boolean;
};

export type LeadResponseConfig = {
  /** Interruptor general. Apagado, el paso «Clasificar lead» no hace nada. */
  activo: boolean;
  /** Leads que se procesan al día; al llegar se para y se avisa. */
  tope_diario: number;
  /** 0–1. Por debajo, la lista lo marca (la Fase 2 no enviará). */
  umbral_confianza: number;
  /** Solo se procesan leads más recientes que esto (horas). */
  antiguedad_horas: number;
  ventana: LeadResponseVentana;
  /** `interes:idioma` (o `interes+interes:idioma` para una combinación, con
   *  los códigos ordenados) → id de plantilla. "" = sin plantilla a
   *  propósito; sin entrada = se busca por nombre («Lead · <etiqueta>
   *  (<IDIOMA>)»). Ver `claveMapaLeads` en `lib/leadsMapa`. */
  mapa: Record<string, string>;
  remitentes: {
    /** Clave de la web → dirección desde la que sale el borrador. */
    por_web: Record<string, string>;
    /** Cuenta de AgileCRM → clave de la web (los leads de Agile no traen web). */
    por_cuenta_agile: Record<string, string>;
  };
};

/** Un interés del catálogo del clasificador (`lead_interests`): el código es
 *  lo que se guarda, la etiqueta lo que se ve, la descripción lo que se le
 *  manda al modelo; los no comerciales no llevan plantilla de venta. */
export type LeadInteres = {
  /** El código (igual que `codigo`; `id` por los desplegables). */
  id: string;
  codigo: string;
  /** La etiqueta (igual que `etiqueta`; `label` por los desplegables). */
  label: string;
  etiqueta: string;
  descripcion: string;
  comercial: boolean;
  orden: number;
  activo: boolean;
};

export type LeadInteresConUso = LeadInteres & {
  en_uso: { clasificaciones: number; en_mapa: number };
};

/** Un interés comercial × idioma sin plantilla (ni en el mapa ni por nombre). */
export type LeadHueco = { interes: string; etiqueta: string; idioma: string; a_proposito: boolean };

export type LeadResponseCatalogo = {
  /** Todo el catálogo, activos e inactivos, en orden. */
  intereses: LeadInteres[];
  idiomas: string[];
  plantillas: { id: string; name: string }[];
  /** Lo que se resuelve por nombre para cada `interes:idioma` (null = no hay). */
  mapa_por_nombre: Record<string, string | null>;
  huecos?: LeadHueco[];
  webs: { clave: string; web: string; marca: string; remitente_defecto: string | null }[];
  cuentas_agile: { account_id: string; display_name: string; enabled: boolean }[];
};

/** Opción de un desplegable de intereses: los inactivos solo se enseñan si
 *  la clasificación ya los lleva. */
export type LeadInteresOpcion = { id: string; label: string; comercial?: boolean; activo?: boolean };

/** Un lead clasificado de verdad (fila de `lead_classifications`). */
export type LeadClasificacion = {
  id: string;
  contacto: { id: string; nombre: string; email: string };
  fuente: "web_form" | "agilecrm" | string;
  referencia: string;
  lead_at: string | null;
  web: string | null;
  cuenta_agile: string | null;
  productos: string[];
  texto: string;
  idioma: string | null;
  idioma_fuente: string | null;
  idioma_formulario: string | null;
  discrepancia_idioma: boolean;
  /** El interés PRINCIPAL (el primero de `intereses`). */
  interes: string | null;
  interes_texto: string;
  /** Todos los intereses que dijo el clasificador, por relevancia (códigos),
   *  sus etiquetas y el texto «A + B». */
  intereses: string[];
  intereses_texto: string;
  intereses_etiquetas?: string[];
  interes_fuente: string | null;
  es_spam: boolean;
  confianza: number;
  bajo_umbral: boolean;
  motivo: string | null;
  proveedor: string | null;
  modelo: string | null;
  estado: "clasificado" | "spam" | "preparado" | "sin_plantilla" | "omitido" | string;
  estado_detalle: string | null;
  plantilla: string | null;
  remitente: string | null;
  borrador_id: string | null;
  borrador_url: string | null;
  tarea_id: string | null;
  run_id: string | null;
  /** La clasificación que manda: la corregida a mano si la hay. */
  efectivo: {
    idioma: string | null;
    interes: string | null;
    interes_texto: string;
    intereses: string[];
    intereses_texto: string;
    intereses_etiquetas?: string[];
    es_spam: boolean;
  };
  correccion: {
    corregida: boolean;
    idioma: string | null;
    interes: string | null;
    /** La lista corregida entera, en el orden elegido a mano. */
    intereses: string[];
    es_spam: boolean | null;
    nota: string | null;
    por: string | null;
    cuando: string | null;
  };
  creado: string | null;
  /** Para la ficha del contacto: la consulta entera (sin recortar), el
   *  contexto de entrada (web, formulario, idioma del formulario, productos,
   *  país…) y la discrepancia de idioma en palabras. */
  texto_completo?: string;
  contexto?: Record<string, unknown>;
  idioma_discrepancia_texto?: string | null;
};

/** Las clasificaciones de un contacto, la última arriba (ficha del contacto). */
export type LeadClasificacionesContacto = {
  umbral_confianza: number;
  total: number;
  items: LeadClasificacion[];
  opciones: { idiomas: string[]; intereses: LeadInteresOpcion[] };
};

export type LeadClasificaciones = {
  dias: number;
  umbral_confianza: number;
  total: number;
  corregidas: number;
  items: LeadClasificacion[];
  opciones: { idiomas: string[]; intereses: LeadInteresOpcion[] };
};

export type LeadCorreccion = {
  idioma?: string;
  /** Un solo interés (compatibilidad): equivale a `intereses: [interes]`. */
  interes?: string;
  /** Todos los intereses, en el orden elegido; el primero es el principal. */
  intereses?: string[];
  es_spam?: boolean;
  nota?: string;
};

/** Lo que el modo en seco dice de un lead: qué haría, sin hacerlo. */
export type LeadEnSecoFila = {
  contacto_id: string;
  nombre: string;
  email: string;
  fuente: string;
  referencia: string;
  lead_at: string | null;
  web: string | null;
  idioma_formulario: string | null;
  productos: string[];
  texto: string;
  clasificacion: {
    idioma: string | null;
    interes: string;
    interes_texto?: string;
    intereses?: string[];
    intereses_texto?: string;
    es_spam: boolean;
    confianza: number;
    motivo: string;
    idioma_fuente?: string | null;
    interes_fuente?: string | null;
    discrepancia_idioma?: boolean;
    proveedor?: string | null;
    modelo?: string | null;
  };
  ya_clasificado: boolean;
  haria: {
    etapa: string;
    plantilla: string | null;
    remitente: string | null;
    tarea: boolean;
    aviso: string | null;
  };
};

export type LeadEnSecoInforme = {
  dias: number;
  limite: number;
  nada_escrito: boolean;
  resumen: {
    total: number;
    spam: number;
    sin_plantilla: number;
    con_discrepancia_idioma: number;
    ya_clasificados: number;
    por_interes: Record<string, number>;
    por_idioma: Record<string, number>;
    proveedor: string;
  };
  items: LeadEnSecoFila[];
};

export type LeadWorkflowEstado = {
  existe: boolean;
  id: string | null;
  status: string | null;
  url: string | null;
  pipeline_ok: boolean;
  pipeline_aviso: string | null;
};

export async function listLeadClasificaciones(dias = 15): Promise<LeadClasificaciones> {
  return apiFetch<LeadClasificaciones>(`/api/erp/leads/clasificaciones${qs({ dias })}`);
}

/** Todas las clasificaciones de un contacto, la última arriba. Lo ve quien ve
 *  la ficha; corregir sigue exigiendo `erp.config`. */
export async function listContactLeadClasificaciones(
  contactId: string,
): Promise<LeadClasificacionesContacto> {
  return apiFetch<LeadClasificacionesContacto>(
    `/api/erp/leads/contactos/${encodeURIComponent(contactId)}`,
  );
}

/** La corrección a mano: queda registrada y manda en la ficha del contacto. */
export async function corregirLeadClasificacion(
  id: string, payload: LeadCorreccion,
): Promise<LeadClasificacion> {
  return apiFetch<LeadClasificacion>(
    `/api/erp/leads/clasificaciones/${encodeURIComponent(id)}/corregir`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

/** Qué habría hecho la Fase 1 con los leads de los últimos N días. No escribe
 *  nada. Sin `limite` manda el tope de serie del servidor (100 leads; cada
 *  uno es una llamada al proveedor dentro de la petición). */
export async function simularLeadsEnSeco(dias = 15, limite?: number): Promise<LeadEnSecoInforme> {
  return apiFetch<LeadEnSecoInforme>("/api/erp/leads/en-seco", {
    method: "POST", body: JSON.stringify({ dias, ...(limite ? { limite } : {}) }),
  });
}

export async function getLeadWorkflow(): Promise<LeadWorkflowEstado> {
  return apiFetch<LeadWorkflowEstado>("/api/erp/leads/workflow");
}

/** Crea el workflow «Respuesta a leads (Fase 1)» en BORRADOR. 409 si ya existe. */
export async function crearLeadWorkflow(): Promise<{
  id: string; name: string; status: string; url: string;
}> {
  return apiFetch("/api/erp/leads/workflow", { method: "POST" });
}

// --- el catálogo de intereses del clasificador (Configuración ERP) -----------
// Un interés nuevo o una descripción afinada entran en el clasificador en la
// siguiente clasificación, sin desplegar nada. No se borra lo que está en uso:
// se desactiva (lo clasificado sigue legible).

export type LeadInteresNuevo = {
  codigo: string;
  etiqueta: string;
  descripcion?: string;
  comercial?: boolean;
  orden?: number;
};

export type LeadInteresCambios = Partial<{
  etiqueta: string;
  descripcion: string;
  comercial: boolean;
  orden: number;
  activo: boolean;
}>;

export async function listLeadIntereses(): Promise<{ items: LeadInteresConUso[] }> {
  return apiFetch<{ items: LeadInteresConUso[] }>("/api/erp/leads/intereses");
}

export async function createLeadInteres(payload: LeadInteresNuevo): Promise<LeadInteresConUso> {
  return apiFetch<LeadInteresConUso>("/api/erp/leads/intereses", {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function updateLeadInteres(
  codigo: string, cambios: LeadInteresCambios,
): Promise<LeadInteresConUso> {
  return apiFetch<LeadInteresConUso>(`/api/erp/leads/intereses/${encodeURIComponent(codigo)}`, {
    method: "PATCH", body: JSON.stringify(cambios),
  });
}

/** 409 si está en uso (clasificaciones o filas del mapa): desactívalo. */
export async function deleteLeadInteres(codigo: string): Promise<void> {
  await apiFetch(`/api/erp/leads/intereses/${encodeURIComponent(codigo)}`, { method: "DELETE" });
}

// --- Base de conocimiento (Configuración ERP → Base de conocimiento) -------------

/** Una web o dominio de correo del grupo (`sitios_web`). */
export type SitioWeb = {
  clave: string;
  dominio: string;
  host: string;
  dominios_extra: string[];
  marca: string;
  empresa: string;
  tipo: "web" | "correo";
  idioma_defecto: string;
  idiomas: string[];
  plugin_traduccion: "qtranslate" | "wpml" | null;
  prefijo_idioma_defecto: boolean;
  slugs_traducidos: boolean;
  soporte_propio: boolean;
  remitente_defecto: string | null;
  posicion: number;
  activo: boolean;
};

export type SitioWebNuevo = {
  clave: string;
  dominio: string;
  marca: string;
  empresa: string;
  host?: string | null;
  dominios_extra?: string[];
  tipo?: "web" | "correo";
  idioma_defecto?: string;
  idiomas?: string[];
  plugin_traduccion?: "qtranslate" | "wpml" | null;
  prefijo_idioma_defecto?: boolean;
  slugs_traducidos?: boolean;
  soporte_propio?: boolean;
  remitente_defecto?: string | null;
};

export type SitioWebCambios = Partial<Omit<SitioWebNuevo, "clave"> & { activo: boolean; posicion: number }>;

/** Una ruta: de soporte (web × idioma → URL) o página de producto (interés → URL). */
export type KbRuta = {
  id: string;
  tipo: "soporte" | "producto";
  sitio: string;
  idioma: string;
  interes: string;
  url: string;
  nota: string | null;
  activo: boolean;
  posicion: number;
  comprobado_en: string | null;
  comprobado_http: number | null;
  comprobado_error: string | null;
};

export type KbRutaNueva = {
  tipo: "soporte" | "producto";
  sitio: string;
  url: string;
  idioma?: string;
  interes?: string;
  nota?: string;
};

export type KbRutaCambios = Partial<{ url: string; nota: string; activo: boolean; posicion: number }>;

/** Una categoría de la KB de FLUX (un modelo) y las palabras con las que se reconoce. */
export type FluxCategoria = {
  categoria_id: string;
  modelo: string;
  palabras: string[];
  posicion: number;
  activo: boolean;
  enlace_es: string;
};

export type FluxSincronizacion = {
  id: string;
  origen: "programada" | "manual";
  estado: "ok" | "parcial" | "error";
  inicio: string | null;
  fin: string | null;
  articulos: number;
  nuevos: number;
  actualizados: number;
  idos: number;
  locales: Record<string, { articulos: number; nuevos: number; actualizados: number; idos: number; error: string | null }>;
  detalle: string | null;
};

export type FluxEstado = {
  categorias: FluxCategoria[];
  locales: Record<string, string>;
  articulos_por_idioma: Record<string, number>;
  idos: number;
  ultima_sincronizacion: FluxSincronizacion | null;
};

/** Un producto del catálogo en UN idioma (`kb_catalogo`). El precio se enseña
 *  tal cual está escrito en el origen (`precio_texto`); `precio_num` solo ordena. */
export type CatalogoProducto = {
  id: string;
  fuente: "compositor" | "woocommerce";
  tipo: "maquina" | "consumible" | "repuesto";
  marca: string;
  origen_id: string;
  nombre: string;
  descripcion: string | null;
  caracteristicas: string[];
  categoria: string | null;
  imagen: string | null;
  precio_texto: string | null;
  precio_num: number | null;
  moneda: string | null;
  /** False cuando el origen dice «Consultar»: no hay precio público (el
   *  asistente dice que el precio se prepara a medida, nunca «Consultar»). */
  precio_publico: boolean;
  /** Una entrada del origen que no es un producto (un premio, sin precio ni
   *  área): se guarda para verla, no entra en el catálogo del asistente. */
  descartado: boolean;
  motivo_descarte: string | null;
  enlace: string | null;
  idioma: string;
  sitio: string | null;
  actualizado_origen: string | null;
  visible: boolean;
  datos: Record<string, unknown>;
  visto_en: string | null;
  ido_en: string | null;
};

export type CatalogoSincronizacion = {
  id: string;
  /** El catálogo (compositor, tiendas) y, en el mismo histórico, las imágenes
   *  de las webs y la tabla de vídeos. */
  fuente: "compositor" | "woocommerce" | "imagenes" | "videos";
  origen: "programada" | "manual";
  estado: "ok" | "parcial" | "error";
  inicio: string | null;
  fin: string | null;
  productos: number;
  nuevos: number;
  actualizados: number;
  idos: number;
  origen_actualizado_en: string | null;
  resumen: Record<string, { productos: number; nuevos: number; actualizados: number; idos: number;
    error: string | null; sin_tienda?: boolean; marcas?: number; por_idioma?: Record<string, number>;
    con_precio?: number;
    /** Filas que la base rechazó y se saltaron (la pasada termina igual), con el motivo. */
    saltadas?: number; errores?: string[];
    /** Compositor: marcas sin ningún producto (no cuentan), entradas descartadas
     *  (premios) y productos sin precio público («Consultar»). */
    marcas_sin_producto?: string[]; descartados?: string[]; sin_precio_publico?: string[];
    filas?: number }>;
  detalle: string | null;
};

export type CatalogoFuenteEstado = {
  productos: number;
  por_tipo: Record<string, number>;
  por_idioma: Record<string, number>;
  por_tienda?: Record<string, number>;
  clave_configurada?: boolean;
  ultima_sincronizacion: CatalogoSincronizacion | null;
};

export type CatalogoMarca = {
  nombre: string;
  logo: string | null;
  color: string | null;
  urls: Record<string, string>;
  idioma_defecto: string;
  ida: boolean;
};

export type CatalogoEstado = {
  fuentes: { compositor: CatalogoFuenteEstado; woocommerce: CatalogoFuenteEstado };
  idos: number;
  con_precio: number;
  /** Entradas descartadas (no son productos) y productos sin precio público. */
  descartados: number;
  sin_precio_publico: number;
  marcas: CatalogoMarca[];
  iva_por_fuente: Record<string, "con" | "sin" | null>;
  precio_meses_orientativo: number;
  tipos: string[];
};

/** Una fila interés → máquinas (marca + patrón del nombre) y lo que casa hoy. */
export type InteresProducto = {
  id: string;
  interes: string;
  etiqueta: string;
  marca: string;
  patron: string;
  nota: string | null;
  posicion: number;
  activo: boolean;
  coincide: { origen_id: string; nombre: string; marca: string }[];
};

/** Las palabras que señalan un interés en la consulta o en la instrucción
 *  (`uv` = la UV de cualquier tamaño). `propias`: cambiadas en la pantalla
 *  (si no, las de partida). */
export type PalabrasInteres = {
  interes: string;
  etiqueta: string;
  palabras: string[];
  propias: boolean;
};

export type InteresProductosResuelto = {
  filas: InteresProducto[];
  de_la_base: boolean;
  huecos: { interes: string; etiqueta: string; motivo: string }[];
  revisado: boolean;
  hallazgo: string;
  catalogo_vacio: boolean;
  /** Ausente en una API anterior a las palabras por interés. */
  palabras?: PalabrasInteres[];
};

/** Un producto como lo ve el asistente: precio tal cual (y de qué idioma es),
 *  orientativo o no, enlace en su idioma. */
export type CatalogoProductoAsistente = {
  origen_id: string;
  fuente: string;
  tipo: string;
  nombre: string;
  marca: string;
  categoria: string | null;
  caracteristicas: string[];
  precio_texto: string | null;
  moneda: string | null;
  precio_idioma: string | null;
  idioma_respaldo: boolean;
  orientativo: boolean;
  actualizado_origen: string | null;
  enlace: string | null;
  idioma_enlace: string | null;
  interes: string | null;
  /** False: el origen dice «Consultar» (sin precio público). */
  precio_publico?: boolean;
  /** De dónde salió: lo que nombra el cliente en el hilo, lo que pide la
   *  instrucción de quien envía, o el interés clasificado. */
  eleccion?: "hilo" | "instruccion" | "consulta" | "interes" | "hilo_posible"
    | "instruccion_posible";
};

export type CatalogoAsistente = {
  maquinas: CatalogoProductoAsistente[];
  consumibles: CatalogoProductoAsistente[];
  sin_maquina: string[];
  intereses_instruccion?: string[];
  /** Los intereses que añade la consulta del cliente por sus palabras. */
  intereses_consulta?: string[];
  palabras_consulta?: Record<string, string[]>;
  menciones?: { hilo: string[]; instruccion: string[]; flojas: string[] };
  avisos: Record<string, string>;
  iva_por_fuente: Record<string, "con" | "sin" | null>;
  precio_meses_orientativo: number;
};

export type Conocimiento = {
  sitios: SitioWeb[];
  sitios_de_la_base: boolean;
  rutas: KbRuta[];
  flux: FluxEstado;
  catalogo: CatalogoEstado;
  interes_productos: InteresProductosResuelto;
  /** Las imágenes de las webs y la tabla de vídeos (Fase 3; una API anterior no lo trae). */
  medios?: MediosEstado;
  idiomas: string[];
};

// --- Imágenes de las webs y vídeos (correos compuestos) -------------------------------

export type MediosEstado = {
  imagenes: {
    total: number; por_sitio: Record<string, number>; favoritas: number; ocultas: number;
    ultima_sincronizacion: CatalogoSincronizacion | null;
  };
  videos: {
    total: number; por_idioma: Record<string, number>; por_canal: Record<string, number>;
    temas: number; ocultos: number; cortos: number;
    /** Si el servidor tiene `YOUTUBE_API_KEY`. */
    clave_configurada: boolean;
    ultima_sincronizacion: CatalogoSincronizacion | null;
  };
  canales: { handle: string; marca: string }[];
  sincronizacion_diaria: boolean;
};

/** Una imagen del índice de la biblioteca de medios de las webs. */
export type KbImagen = {
  id: string;
  sitio: string;
  media_id: number;
  /** El título del adjunto: lo único por lo que se busca (las webs no tienen texto alternativo). */
  titulo: string;
  url: string;
  /** Un tamaño pequeño de la misma imagen, para la pantalla. */
  miniatura: string;
  ancho: number;
  alto: number;
  mime: string | null;
  pagina: string | null;
  publicado_en: string | null;
  favorita: boolean;
  oculta: boolean;
  ido_en: string | null;
};

/** Un vídeo de la tabla (solo YouTube). */
export type KbVideo = {
  id: string;
  plataforma: "youtube";
  video_id: string;
  url: string;
  titulo: string;
  descripcion: string | null;
  idioma: string | null;
  /** De dónde salió el idioma: `manual`, `titulo`, `metadatos` o `audio`. */
  idioma_fuente: string | null;
  idioma_metadatos: string | null;
  idioma_audio: string | null;
  /** El mismo vídeo en varios idiomas comparte tema. */
  tema: string | null;
  marca: string;
  productos: { fuente: string; origen_id: string; nombre: string }[];
  /** Los campos fijados a mano (la sincronización no los pisa). */
  manual: string[];
  miniatura: string | null;
  miniatura_calidad: "maxresdefault" | "mqdefault" | null;
  duracion: number | null;
  corto: boolean;
  canal: string | null;
  origen: "canal" | "web" | "manual";
  webs: string[];
  visible: boolean;
  publicado_en: string | null;
  ido_en: string | null;
};

export type KbVideoCambios = Partial<{
  idioma: string; tema: string; marca: string; productos: { origen_id: string }[];
  visible: boolean; automatico: ("idioma" | "tema" | "marca" | "productos")[];
}>;

function consulta(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    q.set(k, String(v));
  }
  const texto = q.toString();
  return texto ? `?${texto}` : "";
}

export async function getKbImagenes(filtros: {
  sitio?: string; q?: string; favoritas?: boolean; con_ocultas?: boolean; limite?: number;
} = {}): Promise<{ imagenes: KbImagen[]; total: number; limite: number }> {
  return apiFetch(`/api/erp/conocimiento/imagenes${consulta(filtros)}`);
}

export async function updateKbImagen(
  id: string, cambios: { favorita?: boolean; oculta?: boolean },
): Promise<{ imagen: KbImagen; estado: MediosEstado["imagenes"] }> {
  return apiFetch(`/api/erp/conocimiento/imagenes/${encodeURIComponent(id)}`, {
    method: "PATCH", body: JSON.stringify(cambios),
  });
}

export async function sincronizarKbImagenes(): Promise<{
  sincronizacion: CatalogoSincronizacion; estado: MediosEstado["imagenes"];
}> {
  return apiFetch("/api/erp/conocimiento/imagenes/sincronizar", { method: "POST" });
}

export async function getKbVideos(filtros: {
  q?: string; idioma?: string; canal?: string; origen?: string; tema?: string;
  con_ocultos?: boolean; con_cortos?: boolean; limite?: number;
} = {}): Promise<{ videos: KbVideo[]; total: number; limite: number }> {
  return apiFetch(`/api/erp/conocimiento/videos${consulta(filtros)}`);
}

export async function createKbVideo(url: string): Promise<{
  video: KbVideo; estado: MediosEstado["videos"];
}> {
  return apiFetch("/api/erp/conocimiento/videos", { method: "POST", body: JSON.stringify({ url }) });
}

export async function updateKbVideo(id: string, cambios: KbVideoCambios): Promise<{
  video: KbVideo; estado: MediosEstado["videos"];
}> {
  return apiFetch(`/api/erp/conocimiento/videos/${encodeURIComponent(id)}`, {
    method: "PATCH", body: JSON.stringify(cambios),
  });
}

export async function sincronizarKbVideos(): Promise<{
  sincronizacion: CatalogoSincronizacion; estado: MediosEstado["videos"];
}> {
  return apiFetch("/api/erp/conocimiento/videos/sincronizar", { method: "POST" });
}

/** Un enlace que el asistente puede poner en un borrador, con su procedencia. */
export type KbEnlace = {
  url: string;
  origen: string;
  sitio: string | null;
  idioma: string | null;
  etiqueta: string | null;
};

export type KbResolucion = {
  soporte: KbEnlace | null;
  producto: KbEnlace | null;
  flux: {
    modelo: string | null;
    categoria_id: string | null;
    intencion: "kb" | "soporte" | null;
    enlace: KbEnlace | null;
    kb: KbEnlace;
    soporte: KbEnlace | null;
    articulos: { titulo: string; url: string; outdated: boolean }[];
  } | null;
  catalogo?: CatalogoAsistente;
};

/** La configuración del blob `conocimiento` (va con el resto de ajustes). */
export type ConocimientoConfig = {
  flux_sync_activa: boolean;
  flux_sync_hora: string;
  flux_palabras_kb: string[];
  flux_palabras_soporte: string[];
  catalogo_sync_activa: boolean;
  catalogo_sync_hora: string;
  /** Un precio sin confirmar en más de estos meses es «orientativo». */
  precio_meses_orientativo: number;
  /** Declarado por fuente, nunca deducido: `con`, `sin` o null (sin declarar). */
  iva_por_fuente: { compositor: "con" | "sin" | null; woocommerce: "con" | "sin" | null };
  /** Bart ha dado por revisada la propuesta interés → máquinas. */
  interes_productos_revisado: boolean;
  /** Sincronizar cada día las imágenes de las webs y la tabla de vídeos (detrás del catálogo). */
  medios_sync_activa?: boolean;
  /** Los canales de YouTube cuyos vídeos van a la tabla, con su marca del catálogo. */
  youtube_canales?: { handle: string; marca: string }[];
};

export async function getConocimiento(): Promise<Conocimiento> {
  return apiFetch<Conocimiento>("/api/erp/conocimiento");
}

export async function createSitioWeb(payload: SitioWebNuevo): Promise<Conocimiento> {
  return apiFetch<Conocimiento>("/api/erp/conocimiento/sitios", {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function updateSitioWeb(clave: string, cambios: SitioWebCambios): Promise<Conocimiento> {
  return apiFetch<Conocimiento>(`/api/erp/conocimiento/sitios/${encodeURIComponent(clave)}`, {
    method: "PATCH", body: JSON.stringify(cambios),
  });
}

export async function createKbRuta(payload: KbRutaNueva): Promise<Conocimiento> {
  return apiFetch<Conocimiento>("/api/erp/conocimiento/rutas", {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function updateKbRuta(id: string, cambios: KbRutaCambios): Promise<Conocimiento> {
  return apiFetch<Conocimiento>(`/api/erp/conocimiento/rutas/${encodeURIComponent(id)}`, {
    method: "PATCH", body: JSON.stringify(cambios),
  });
}

/** Pide cada URL activa y guarda el código que devuelve (tarda unos segundos). */
export async function comprobarKbRutas(): Promise<{
  resultados: { id: string; url: string; http: number | null; ok: boolean; error: string | null }[];
  rutas: KbRuta[];
}> {
  return apiFetch("/api/erp/conocimiento/rutas/comprobar", { method: "POST" });
}

export async function createFluxCategoria(payload: {
  categoria_id: string; modelo: string; palabras?: string[];
}): Promise<Conocimiento> {
  return apiFetch<Conocimiento>("/api/erp/conocimiento/flux/categorias", {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function updateFluxCategoria(
  categoriaId: string,
  cambios: Partial<{ modelo: string; palabras: string[]; activo: boolean; posicion: number }>,
): Promise<Conocimiento> {
  return apiFetch<Conocimiento>(
    `/api/erp/conocimiento/flux/categorias/${encodeURIComponent(categoriaId)}`,
    { method: "PATCH", body: JSON.stringify(cambios) },
  );
}

/** «Sincronizar ahora»: una pasada completa del índice de la KB de FLUX. */
export async function sincronizarFlux(): Promise<
  { sincronizacion: FluxSincronizacion } & Omit<FluxEstado, "categorias" | "locales">
> {
  return apiFetch("/api/erp/conocimiento/flux/sincronizar", { method: "POST" });
}

/** Qué enlaces (y qué máquinas y consumibles del catálogo) le saldrían al
 *  asistente para esa web, idioma, interés, texto y máquina del cliente. */
export async function resolverConocimiento(payload: {
  sitio?: string | null; idioma?: string | null; interes?: string | null;
  texto?: string | null; modelo?: string | null; maquina?: string | null;
}): Promise<KbResolucion> {
  return apiFetch<KbResolucion>("/api/erp/conocimiento/resolver", {
    method: "POST", body: JSON.stringify(payload),
  });
}

// --- el catálogo (Fase 2) --------------------------------------------------------------

export type CatalogoFiltros = {
  fuente?: string; tipo?: string; idioma?: string; marca?: string; sitio?: string; q?: string;
  con_idos?: boolean; con_descartados?: boolean; limite?: number;
};

/** La tabla del catálogo, filtrada (los idos solo si se piden). */
export async function getCatalogoProductos(filtros: CatalogoFiltros = {}): Promise<{
  productos: CatalogoProducto[]; total: number; limite: number;
}> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filtros)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    params.set(k, String(v));
  }
  const q = params.toString();
  return apiFetch(`/api/erp/conocimiento/catalogo/productos${q ? `?${q}` : ""}`);
}

/** «Sincronizar ahora» el compositor, las tiendas o las dos (en la petición). */
export async function sincronizarCatalogo(fuente: "compositor" | "woocommerce" | null): Promise<{
  sincronizaciones: CatalogoSincronizacion[];
  catalogo: CatalogoEstado;
  interes_productos: InteresProductosResuelto;
}> {
  return apiFetch("/api/erp/conocimiento/catalogo/sincronizar", {
    method: "POST", body: JSON.stringify({ fuente }),
  });
}

export async function updateCatalogoMarca(
  nombre: string, cambios: { idioma_defecto: string },
): Promise<Conocimiento> {
  return apiFetch<Conocimiento>(
    `/api/erp/conocimiento/catalogo/marcas/${encodeURIComponent(nombre)}`,
    { method: "PATCH", body: JSON.stringify(cambios) },
  );
}

export async function createInteresProducto(payload: {
  interes: string; marca: string; patron?: string; nota?: string | null;
}): Promise<Conocimiento> {
  return apiFetch<Conocimiento>("/api/erp/conocimiento/catalogo/intereses", {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function updateInteresProducto(
  id: string,
  cambios: Partial<{ interes: string; marca: string; patron: string; nota: string; activo: boolean;
    posicion: number }>,
): Promise<Conocimiento> {
  return apiFetch<Conocimiento>(
    `/api/erp/conocimiento/catalogo/intereses/${encodeURIComponent(id)}`,
    { method: "PATCH", body: JSON.stringify(cambios) },
  );
}

export async function deleteInteresProducto(id: string): Promise<Conocimiento> {
  return apiFetch<Conocimiento>(
    `/api/erp/conocimiento/catalogo/intereses/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

/** Las palabras de un interés; `null` vuelve a las de partida. */
export async function updatePalabrasInteres(
  interes: string, palabras: string[] | null,
): Promise<Conocimiento> {
  return apiFetch<Conocimiento>(
    `/api/erp/conocimiento/catalogo/intereses/palabras/${encodeURIComponent(interes)}`,
    { method: "PUT", body: JSON.stringify({ palabras }) },
  );
}

// --- La IA en números (ERP · Leads → «Uso de la IA») --------------------------------

export type UsoIAGrupo = {
  clave: string;
  llamadas: number;
  tokens_entrada: number;
  tokens_salida: number;
  /** Tokens leídos de la caché de prompts (a una décima parte del precio). */
  tokens_cache_lectura?: number;
  coste_usd: number;
};

/** Lo que cuesta un borrador TERMINADO de verdad: el primero más sus retoques. */
export type UsoIAConversaciones = {
  total: number;
  terminadas: number;
  correcciones: number;
  coste_terminadas_usd: number;
  coste_medio_terminada_usd: number;
  turnos_medios_terminada: number;
};

export type UsoIA = {
  dias: number;
  llamadas: number;
  tokens_entrada: number;
  tokens_salida: number;
  coste_usd: number;
  por_proposito: UsoIAGrupo[];
  por_modelo: UsoIAGrupo[];
  por_dia: { dia: string; llamadas: number; coste_usd: number }[];
  borradores: {
    generados: number;
    regenerados: number;
    descartados: number;
    enviados_tal_cual: number;
    enviados_editados: number;
    total: number;
  };
  conversaciones?: UsoIAConversaciones;
  ultimas: {
    id: string;
    cuando: string | null;
    proposito: string;
    modelo: string;
    tokens_entrada: number;
    tokens_salida: number;
    tokens_cache_lectura?: number;
    tokens_cache_escritura?: number;
    coste_usd: number;
    duracion_ms: number | null;
    usuario_id: string | null;
    referencia_tipo: string | null;
    referencia_id: string | null;
    ok: boolean;
    error: string | null;
  }[];
};

/** Llamadas, tokens y coste reales de la IA en los últimos `dias` días. */
export async function getUsoIA(dias = 30): Promise<UsoIA> {
  return apiFetch<UsoIA>(`/api/erp/ia/uso${qs({ dias })}`);
}

/** La configuración del asistente de respuesta (blob `asistente`). */
export type AsistenteConfig = {
  presupuesto_tokens: number;
  historico_max: number;
  asuntos_max: number;
  dominios_gratuitos: string[];
  /** El modelo del primer borrador; null = el de ANTHROPIC_MODEL. */
  modelo_borrador: string | null;
  /** El modelo de las correcciones («más corto», «en inglés»): Haiku por defecto. */
  modelo_correccion: string;
  /** Los modelos que se ofrecen en el desplegable; se edita aquí, sin desplegar. */
  modelos: string[];
  /** Apagado por defecto: el pensamiento se factura como salida y redactar no lo necesita. */
  pensamiento_extendido: boolean;
};

// --- Ofertas · propuestas comerciales ANTES del presupuesto de FACTUSOL -------
//
// Viven en BoHub (`/api/erp/ofertas`): se crean para un contacto del CRM sin
// cliente de FACTUSOL ni datos fiscales, se envían por email con el PDF y,
// cuando el cliente dice que sí, «Convertir en presupuesto» (`convertOferta`)
// es el ÚNICO camino que escribe en FACTUSOL. Todo lo demás es lectura o
// escritura en BoHub.

export type OfertaEstado = "borrador" | "enviada" | "caducada" | "convertida";

/** Texto de cada estado tal como se enseña (pill y filtros). */
export const OFERTA_ESTADO_LABELS: Record<OfertaEstado, string> = {
  borrador: "Borrador",
  enviada: "Enviada",
  caducada: "Caducada",
  convertida: "Convertida",
};

export type OfertaIdioma = FactusolPdfLang;

/** Una línea tal como la devuelve la API (`total` lo calcula el servidor). */
export type OfertaLinea = {
  id?: string;
  posicion: number;
  codart: string | null;
  sku: string | null;
  descripcion: string;
  cantidad: number;
  precio: number;
  descuento_pct: number;
  iva_pct: number;
  total?: number;
  /** Línea solo de texto (sin artículo): vale para la oferta, pero bloquea
   *  la conversión (`lineas_sin_articulo`). */
  text_only: boolean;
};

/** Una línea para ENVIAR (POST / PATCH de ofertas y plantillas). */
export type OfertaLineaIn = {
  codart?: string | null;
  sku?: string | null;
  descripcion: string;
  cantidad: number;
  precio: number;
  descuento_pct: number;
  iva_pct: number;
};

/** Un envío por email de la oferta (historial). */
export type OfertaEnvio = {
  id: string;
  enviado_en: string;
  usuario_id: string | null;
  version: number;
  fecha_oferta: string | null;
  total: number | null;
  idioma: string | null;
  para: string[];
  cc: string[];
  asunto: string | null;
  from_alias: string | null;
  adjunto: string | null;
  message_id: string | null;
  thread_id: string | null;
};

export type Oferta = {
  id: string;
  /** `OF-2026-0001` (prefijo de Configuración ERP · Ofertas). */
  numero: string;
  anio: number;
  secuencia: number;
  /** Estado EFECTIVO: una enviada ya vencida sale `caducada`. */
  estado: OfertaEstado;
  estado_guardado: OfertaEstado;
  vencida: boolean;
  contact_id: string | null;
  contacto: { id: string; nombre: string; email: string | null; company_id: string | null } | null;
  company_id: string | null;
  empresa: { id: string; nombre: string; factusol_company_id: string | null } | null;
  plantilla_id: string | null;
  /** Empresa emisora: 1 Bomedia · 2 MQ Europe · 4 Lambert · 5 Streamtec. */
  serie: number;
  emisora: string;
  idioma: OfertaIdioma;
  con_iva: boolean;
  /** ISO yyyy-mm-dd. */
  fecha: string;
  validez_dias: number;
  valida_hasta: string;
  referencia: string | null;
  notas: string | null;
  base: number;
  iva_pct: number;
  iva: number;
  total: number;
  version: number;
  /** El presupuesto de FACTUSOL en que se convirtió (null hasta entonces). */
  factusol: { serie: number; codpre: number; numero: string; ejercicio: string | null } | null;
  convertida_en: string | null;
  enviada_en: string | null;
  caducada_en: string | null;
  num_envios: number;
  ultimo_envio: OfertaEnvio | null;
  /** Se cambió después del último envío: el cliente tiene una versión vieja. */
  pendiente_de_envio: boolean;
  created_by_user_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** Solo en `getOferta`, `createOferta` y `updateOferta`. */
  lineas?: OfertaLinea[];
  envios?: OfertaEnvio[];
};

export type OfertasMeta = {
  prefijo: string;
  validez_dias: number;
  series: { serie: number; nombre: string }[];
  idiomas: string[];
  estados: OfertaEstado[];
};

/** Contacto de la empresa de la oferta, candidato a destinatario (misma forma
 *  que `PickContact` de `CompanyContactsPicker`). */
export type OfertaEmailContact = {
  id: string;
  name: string;
  email: string | null;
  has_email: boolean;
  /** Premarcado en «Para»: el contacto de la oferta o, si no, el principal con email. */
  is_primary: boolean;
};

export type OfertaEmailPreview = {
  oferta_id: string;
  numero: string;
  /** Email del contacto de la oferta, por si no hay contactos de empresa. */
  to: string;
  lang: string;
  lang_source: string;
  subject: string;
  body_text: string;
  /** Remitente: el de la EMPRESA EMISORA de la serie; si no, el del usuario. */
  from_alias: string;
  from_alias_source: "serie" | "usuario";
  from_alias_ok: boolean | null;
  from_alias_problem: string | null;
  attachment_filename: string;
  company_id: string | null;
  company_name: string | null;
  contact_id: string | null;
  company_contacts: OfertaEmailContact[];
  /** A quién saluda `{contacto}` en el cuerpo (null = a la empresa). */
  contacto_id: string | null;
  markers: Record<string, string>;
  version: number;
  pendiente_de_envio: boolean;
};

export type OfertaEmailSendPayload = {
  /** OBLIGATORIO `true`: nada se envía sin un clic con confirmación. */
  confirm: boolean;
  to: string[];
  cc?: string[];
  bcc?: string[];
  subject: string;
  body_text: string;
  /** El HTML del borrador (asistente o editor); sin él, va el texto. */
  body_html?: string | null;
  lang: OfertaIdioma;
  from_alias: string;
  /** Índice de la cuenta bancaria de la emisora para el PDF (default: la marcada). */
  bank?: number | null;
};

export type OfertaEmailResult = {
  sent: boolean;
  message_id: string;
  thread_id: string;
  to: string[];
  cc: string[];
  bcc: string[];
  lang: string;
  numero: string;
  attachment_filename: string;
  /** Tras enviar: `enviada` y su fecha. */
  estado: OfertaEstado;
  enviada_en: string | null;
  /** Etapas del pipeline a las que se movió el contacto («Propuesta enviada»). */
  etapas_movidas: string[];
};

/** Lo que devuelve el job de conversión cuando termina. */
export type OfertaConvertResult = {
  oferta_id: string;
  numero: string;
  codpre: number;
  serie: number;
  ejercicio: string | null;
  /** Nº del presupuesto en FACTUSOL (`2-000075`). */
  factusol_numero: string;
  lines: number;
  total: number;
  warning?: string | null;
};

/** Estado del job de conversión (mismo contrato que `pollQuoteJob`). */
export type OfertaConvertJobStatus =
  | { status: "pending" }
  | { status: "finished"; result: OfertaConvertResult }
  /** `company_not_linked` / `lineas_sin_articulo` / `oferta_convertida` se
   *  rechazan ANTES de encolar (409/422 en `convertOferta`); aquí llegan
   *  los fallos del propio worker. */
  | { status: "failed"; error?: string; code?: string };

export type OfertaListParams = {
  contact_id?: string;
  company_id?: string;
  estado?: OfertaEstado;
  q?: string;
  /** Hasta 500 (200 por defecto en el servidor). */
  limit?: number;
};

export type OfertaCreatePayload = {
  contact_id: string;
  serie: number;
  plantilla_id?: string | null;
  /** Sin `lineas`, las de la plantilla. */
  lineas?: OfertaLineaIn[];
  /** Por defecto: el del contacto → empresa → país → emisora. */
  idioma?: OfertaIdioma;
  con_iva?: boolean;
  validez_dias?: number;
  /** ISO yyyy-mm-dd; por defecto hoy. */
  fecha?: string;
  referencia?: string | null;
  notas?: string | null;
};

/** Al editar, la fecha pasa a HOY y `valida_hasta` se recalcula, salvo que
 *  se mande `fecha` a mano. 409 `oferta_convertida` si ya está convertida. */
export type OfertaPatchPayload = {
  serie?: number;
  lineas?: OfertaLineaIn[];
  idioma?: OfertaIdioma;
  con_iva?: boolean;
  validez_dias?: number;
  fecha?: string;
  referencia?: string | null;
  notas?: string | null;
};

export async function getOfertasMeta(): Promise<OfertasMeta> {
  return apiFetch<OfertasMeta>("/api/erp/ofertas/meta");
}

/** Ofertas (sin líneas) por contacto, empresa, estado o texto. */
export async function listOfertas(params: OfertaListParams = {}): Promise<Oferta[]> {
  const r = await apiFetch<{ items: Oferta[] }>(
    `/api/erp/ofertas${qs({
      contact_id: params.contact_id, company_id: params.company_id,
      estado: params.estado, q: params.q, limit: params.limit,
    })}`,
  );
  return r.items;
}

/** La oferta con sus líneas y su historial de envíos. */
export async function getOferta(id: string): Promise<Oferta> {
  return apiFetch<Oferta>(`/api/erp/ofertas/${encodeURIComponent(id)}`);
}

export async function createOferta(payload: OfertaCreatePayload): Promise<Oferta> {
  return apiFetch<Oferta>("/api/erp/ofertas", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateOferta(id: string, patch: OfertaPatchPayload): Promise<Oferta> {
  return apiFetch<Oferta>(`/api/erp/ofertas/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

/** Ruta (relativa a la API) del PDF de la oferta. Va con auth, así que para
 *  verlo hay que pasar por `openOfertaPdf` / `downloadOfertaPdf`. */
export function ofertaPdfUrl(
  id: string, lang?: OfertaIdioma, opts: { bank?: number } = {},
): string {
  return `/api/erp/ofertas/${encodeURIComponent(id)}/pdf${qs({ lang, bank: opts.bank })}`;
}

/** El PDF como blob (para `saveBlob` o adjuntarlo). */
export async function downloadOfertaPdf(
  id: string, lang?: OfertaIdioma, opts: { bank?: number } = {},
): Promise<Blob> {
  return apiDownloadBlob(ofertaPdfUrl(id, lang, opts));
}

/** Descarga el PDF con auth y lo abre en una pestaña nueva (la misma
 *  mecánica que `openShippingFile`: el navegador no manda el token en un
 *  enlace directo). */
export async function openOfertaPdf(
  id: string, lang?: OfertaIdioma, opts: { bank?: number } = {},
): Promise<void> {
  const blob = await downloadOfertaPdf(id, lang, opts);
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener");
  // El navegador retiene el blob mientras la pestaña lo usa; lo liberamos
  // tras un margen para no cortar la apertura.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Previsualización OBLIGATORIA antes de enviar (asunto, cuerpo, remitente,
 *  contactos de la empresa). `lang` fuerza el idioma; sin él, el de la oferta. */
export async function getOfertaEmailPreview(
  id: string, lang?: OfertaIdioma,
): Promise<OfertaEmailPreview> {
  return apiFetch<OfertaEmailPreview>(
    `/api/erp/ofertas/${encodeURIComponent(id)}/email-preview${qs({ lang })}`,
  );
}

/** Envía la oferta con el PDF adjunto. Exige `confirm: true`. Errores: 400
 *  `confirmation_required` / `gmail_unavailable`, 422 `no_recipient`, 409
 *  `oferta_convertida`, 403 si el alias no está permitido. No toca FACTUSOL. */
export async function sendOfertaEmail(
  id: string, payload: OfertaEmailSendPayload,
): Promise<OfertaEmailResult> {
  return apiFetch<OfertaEmailResult>(`/api/erp/ofertas/${encodeURIComponent(id)}/email`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** «Convertir en presupuesto»: el ÚNICO camino de las ofertas que ESCRIBE en
 *  FACTUSOL (encola el job en `factusol:writes`). Antes de encolar rechaza:
 *  409 `company_not_linked` (`detail.company_id` → ficha de la empresa para
 *  vincular / crear el cliente; null → ficha del contacto, `detail.contact_id`),
 *  422 `lineas_sin_articulo` (`detail.posiciones`) y 409 `oferta_convertida`.
 *  Se sondea con `getOfertaConvertStatus(job_id)`. */
export async function convertOferta(
  id: string,
): Promise<{ job_id: string; oferta_id: string; numero: string }> {
  return apiFetch(`/api/erp/ofertas/${encodeURIComponent(id)}/convertir`, {
    method: "POST",
  });
}

export async function getOfertaConvertStatus(jobId: string): Promise<OfertaConvertJobStatus> {
  return apiFetch<OfertaConvertJobStatus>(
    `/api/erp/ofertas/convertir/status/${encodeURIComponent(jobId)}`,
  );
}

// --- Plantillas de documento (líneas para oferta, proforma o pedido) ----------

/** A qué se aplica una plantilla al usarla: la misma sirve para los tres. */
export type PlantillaDestino = "oferta" | "proforma" | "pedido";
/** De dónde se guardó: proforma (`presupuesto`), albarán, pedido o a mano. */
export type PlantillaOrigenTipo = "presupuesto" | "albaran" | "pedido" | "manual";

/** Línea de la plantilla que explica por qué salió en una búsqueda. */
export type PlantillaCoincidencia = { posicion: number; sku: string | null; descripcion: string };

export type PlantillaDocumento = {
  id: string;
  nombre: string;
  descripcion: string | null;
  activa: boolean;
  /** Una plantilla por idioma: la misma oferta en ES y en DE son dos. */
  idioma: OfertaIdioma;
  carpeta_id: string | null;
  /** Ruta de la carpeta («DTF / Escuelas»); null = sin carpeta. */
  carpeta: string | null;
  etiquetas: string[];
  origen_tipo: PlantillaOrigenTipo | null;
  origen_serie: number | null;
  origen_codigo: number | null;
  origen_order_id: string | null;
  /** Nº visible del documento de origen («2-000040», «PED-…»). */
  origen_numero: string | null;
  num_lineas: number;
  created_at: string | null;
  updated_at: string | null;
  /** Solo al listar para un cliente: está en su idioma. */
  idioma_coincide?: boolean;
  /** Solo al buscar: las líneas donde está lo buscado. */
  coincidencias?: PlantillaCoincidencia[];
  /** Solo en el detalle, el alta y la edición. */
  lineas?: OfertaLinea[];
};

export type PlantillaCarpetaNode = {
  id: string;
  nombre: string;
  parent_id: string | null;
  num_plantillas: number;
  /** La carpeta por defecto del usuario (★): se abre ahí y ahí se guardan. */
  es_defecto: boolean;
  children: PlantillaCarpetaNode[];
  /** Solo para administración: a quién está acotada. */
  acceso_user_ids?: string[];
};

export type PlantillasMeta = {
  idiomas: OfertaIdioma[];
  destinos: PlantillaDestino[];
  max_profundidad: number;
  carpeta_defecto: string | null;
  /** Puede acotar el acceso a las carpetas (administración). */
  gestiona_acceso: boolean;
  /** El usuario solo ve algunas carpetas (y no puede guardar en la raíz). */
  acceso_restringido: boolean;
};

export type PlantillaEtiqueta = { etiqueta: string; norm: string; num: number };

export type PlantillaUsuario = {
  id: string;
  nombre: string;
  email: string;
  rol: string;
  /** Ya tiene alguna carpeta: solo ve las suyas. */
  restringido: boolean;
};

/** El cliente para el que se va a crear el documento: con él salen primero
 *  las plantillas de su idioma. */
export type PlantillaCliente = {
  contact_id?: string | null;
  company_id?: string | null;
  order_id?: string | null;
  serie?: number | null;
};

export type PlantillaListParams = PlantillaCliente & {
  /** Busca en el nombre, la descripción, las etiquetas y DENTRO de las líneas. */
  q?: string;
  /** Incluye sus subcarpetas. */
  carpeta_id?: string | null;
  sin_carpeta?: boolean;
  etiquetas?: string[];
  idioma?: OfertaIdioma | "";
  activas?: boolean;
  limit?: number;
};

export type PlantillaListResult = {
  items: PlantillaDocumento[];
  total: number;
  idioma_cliente: OfertaIdioma | null;
  idioma_fuente: string | null;
};

/** De qué documento se guarda (solo se LEE: nada se escribe en FACTUSOL). */
export type PlantillaOrigenIn =
  | { tipo: "presupuesto" | "albaran"; serie: number; codigo: number; ejercicio?: string | null }
  | { tipo: "pedido"; order_id: string };

export type PlantillaCreatePayload = {
  nombre: string;
  descripcion?: string | null;
  idioma?: OfertaIdioma;
  /** Sin el campo: la carpeta por defecto del usuario. `null` = sin carpeta. */
  carpeta_id?: string | null;
  etiquetas?: string[];
  activa?: boolean;
  origen?: PlantillaOrigenIn;
  lineas?: OfertaLineaIn[];
};

export type PlantillaPatch = {
  nombre?: string;
  descripcion?: string | null;
  idioma?: OfertaIdioma;
  /** `null` = a la raíz; sin el campo, no se mueve. */
  carpeta_id?: string | null;
  etiquetas?: string[];
  activa?: boolean;
  lineas?: OfertaLineaIn[];
};

/** Líneas de la plantilla en la forma del alta de proforma (`createQuote`). */
export type PlantillaLineaProforma = {
  codart: string;
  sku: string | null;
  description: string;
  quantity: number;
  unit_price: number;
  discount_pct: number;
  iva_pct: number;
  text_only: boolean;
};

/** Líneas de la plantilla en la forma del alta de pedido (`createOrder`). */
export type PlantillaLineaPedido = {
  product_sku: string;
  product_codart: string | null;
  description: string;
  quantity: number;
  unit_price: number;
  tax_rate: number;
};

export type PlantillaLineasPorDestino = {
  oferta: OfertaLineaIn;
  proforma: PlantillaLineaProforma;
  pedido: PlantillaLineaPedido;
};

export type PlantillaLineasResult<D extends PlantillaDestino> = {
  plantilla: PlantillaDocumento;
  destino: D;
  lineas: PlantillaLineasPorDestino[D][];
  /** Lo que no cabe tal cual en el destino (p. ej. el descuento en un pedido). */
  avisos: string[];
};

const PLANTILLAS = "/api/erp/plantillas";

export async function getPlantillasMeta(): Promise<PlantillasMeta> {
  return apiFetch<PlantillasMeta>(`${PLANTILLAS}/meta`);
}

export async function listPlantillaEtiquetas(): Promise<PlantillaEtiqueta[]> {
  const r = await apiFetch<{ items: PlantillaEtiqueta[] }>(`${PLANTILLAS}/etiquetas`);
  return r.items;
}

/** Administración: a quién se puede acotar el acceso a una carpeta. */
export async function listPlantillaUsuarios(): Promise<PlantillaUsuario[]> {
  const r = await apiFetch<{ items: PlantillaUsuario[] }>(`${PLANTILLAS}/usuarios`);
  return r.items;
}

/** Las plantillas que el usuario ve, con el buscador y los filtros. */
export async function listPlantillas(params: PlantillaListParams = {}): Promise<PlantillaListResult> {
  const sp = new URLSearchParams();
  const set = (k: string, v: string | number | boolean | null | undefined) => {
    if (v !== undefined && v !== null && v !== "" && v !== false) sp.set(k, String(v));
  };
  set("q", params.q?.trim());
  set("carpeta_id", params.carpeta_id);
  set("sin_carpeta", params.sin_carpeta);
  set("idioma", params.idioma);
  set("activas", params.activas);
  set("contact_id", params.contact_id);
  set("company_id", params.company_id);
  set("order_id", params.order_id);
  set("serie", params.serie);
  set("limit", params.limit);
  for (const e of params.etiquetas ?? []) sp.append("etiqueta", e);
  const s = sp.toString();
  return apiFetch<PlantillaListResult>(`${PLANTILLAS}${s ? `?${s}` : ""}`);
}

/** Una plantilla con sus líneas. */
export async function getPlantilla(id: string): Promise<PlantillaDocumento> {
  return apiFetch<PlantillaDocumento>(`${PLANTILLAS}/${encodeURIComponent(id)}`);
}

/** «Guardar como plantilla» (desde una proforma, un albarán o un pedido) o
 *  una plantilla nueva con las líneas a mano. */
export async function createPlantilla(payload: PlantillaCreatePayload): Promise<PlantillaDocumento> {
  return apiFetch<PlantillaDocumento>(PLANTILLAS, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updatePlantilla(id: string, patch: PlantillaPatch): Promise<PlantillaDocumento> {
  return apiFetch<PlantillaDocumento>(`${PLANTILLAS}/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

/** Las líneas de la plantilla en la forma del flujo del destino (la
 *  plantilla solo aporta las líneas) y los avisos de lo que no cabe tal cual. */
export async function getPlantillaLineas<D extends PlantillaDestino>(
  id: string, destino: D,
): Promise<PlantillaLineasResult<D>> {
  return apiFetch<PlantillaLineasResult<D>>(
    `${PLANTILLAS}/${encodeURIComponent(id)}/lineas${qs({ destino })}`,
  );
}

export async function listPlantillaCarpetas(): Promise<{
  items: PlantillaCarpetaNode[];
  carpeta_defecto: string | null;
}> {
  return apiFetch(`${PLANTILLAS}/carpetas`);
}

export async function createPlantillaCarpeta(payload: {
  nombre: string;
  parent_id?: string | null;
}): Promise<{ id: string; nombre: string; parent_id: string | null }> {
  return apiFetch(`${PLANTILLAS}/carpetas`, { method: "POST", body: JSON.stringify(payload) });
}

/** Renombrar o mover (`parent_id: null` = a la raíz; sin el campo, no se mueve). */
export async function updatePlantillaCarpeta(
  id: string, patch: { nombre?: string; parent_id?: string | null },
): Promise<{ id: string; nombre: string; parent_id: string | null }> {
  return apiFetch(`${PLANTILLAS}/carpetas/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

/** Borra la carpeta, no su contenido: plantillas y subcarpetas suben a la raíz. */
export async function deletePlantillaCarpeta(id: string): Promise<{ ok: boolean }> {
  return apiFetch(`${PLANTILLAS}/carpetas/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** Administración: quién tiene acceso a la carpeta (quien esté en alguna
 *  carpeta solo ve las suyas; quien no esté en ninguna, las ve todas). */
export async function setPlantillaCarpetaAcceso(
  id: string, userIds: string[],
): Promise<{ id: string; acceso_user_ids: string[] }> {
  return apiFetch(`${PLANTILLAS}/carpetas/${encodeURIComponent(id)}/acceso`, {
    method: "PUT",
    body: JSON.stringify({ user_ids: userIds }),
  });
}

/** La carpeta por defecto del usuario (null = ninguna). */
export async function setPlantillaCarpetaDefecto(
  carpetaId: string | null,
): Promise<{ carpeta_defecto: string | null }> {
  return apiFetch(`${PLANTILLAS}/preferencias/carpeta`, {
    method: "PUT",
    body: JSON.stringify({ carpeta_id: carpetaId }),
  });
}

// --- Histórico de ventas: «¿quién compró qué?» (30/10/2026) ------------------
// Lo que BoHub ha volcado de FACTUSOL (solo lectura de FACTUSOL). La carga de
// un ejercicio se encola y la pantalla consulta su estado.

export type HistoricoEstado = "cargado" | "cargando" | "no_disponible" | "error" | "sin_cargar";

export type HistoricoEjercicio = {
  ejercicio: string;
  estado: HistoricoEstado;
  cargado_at: string | null;
  intento_at: string | null;
  detalle: string | null;
  facturas: number;
  albaranes: number;
  lineas: number;
  lineas_producto: number;
  lineas_nota: number;
  articulos: number;
  segundos: number | null;
};

export type HistoricoEstadoCarga = {
  en_curso: string;
  ejercicios: HistoricoEjercicio[];
  notas: { lineas: number; producto: number; notas: number };
};

export type HistoricoClase = "maquina" | "consumible" | "recambio" | "accesorio" | "otro";
export const HISTORICO_CLASE_LABEL: Record<HistoricoClase, string> = {
  maquina: "Máquina", consumible: "Consumible", recambio: "Recambio", accesorio: "Accesorio",
  otro: "Otro",
};

/** Un artículo de una familia de máquinas (o marcado como máquina): «es
 *  máquina» se decide por artículo (31/10/2026). */
export type MaquinaArticulo = {
  codart: string;
  descripcion: string | null;
  familia: string | null;
  familia_nombre: string | null;
  gama: string | null;
  /** Líneas vendidas (de las series que cuentan). */
  lineas: number;
  estado: "maquina" | "no_maquina" | "sin_revisar";
  clase_propia: string | null;
  /** Clase de su familia hoy (la marcada como máquina puede ser de otra). */
  familia_clase?: string | null;
  /** Exige evidencia positiva: «máquina» solo si el nombre lo dice; «dudoso»
   *  si hay pista a favor y en contra; null sin pistas. */
  propuesta: "maquina" | "no_maquina" | "dudoso" | null;
  pista_si: string | null;
  pista_no: string | null;
};

export type MaquinasResumen = {
  items: MaquinaArticulo[];
  marcadas: number;
  total: number;
  sin_revisar: number;
  /** Las que «Aceptar todas» aplicaría (sin revisar y con propuesta clara). */
  propuestas: number;
  reglas: { id: number; patron: string; orden: number; tipo: "maquina" | "no_maquina" }[];
  aplicadas?: { maquina: number; no_maquina: number };
};

export async function getHistoricoMaquinas(): Promise<MaquinasResumen> {
  return apiFetch("/api/erp/historico/maquinas");
}

export async function marcarHistoricoMaquina(
  codart: string, esMaquina: boolean,
): Promise<MaquinasResumen> {
  return apiFetch(`/api/erp/historico/maquinas/${encodeURIComponent(codart)}`, {
    method: "PUT", body: JSON.stringify({ es_maquina: esMaquina }),
  });
}

export async function aceptarPropuestasMaquinas(): Promise<MaquinasResumen> {
  return apiFetch("/api/erp/historico/maquinas/aceptar-propuestas", { method: "POST" });
}

export async function crearPistaArticulo(
  patron: string, tipo: "maquina" | "no_maquina",
): Promise<MaquinasResumen> {
  return apiFetch("/api/erp/historico/reglas-articulo", {
    method: "POST", body: JSON.stringify({ patron, tipo }),
  });
}

export async function borrarPistaArticulo(id: number): Promise<MaquinasResumen> {
  return apiFetch(`/api/erp/historico/reglas-articulo/${id}`, { method: "DELETE" });
}

export type SerieVenta = {
  serie: string;
  nombre: string | null;
  documentos: number;
  lineas: number;
  cuenta: boolean;
};

export type SeriesVenta = {
  items: SerieVenta[];
  /** Lo que se queda fuera con lo marcado. */
  fuera: { documentos: number; lineas: number };
};

export async function getHistoricoSeries(): Promise<SeriesVenta> {
  return apiFetch("/api/erp/historico/series");
}

export async function marcarHistoricoSerie(serie: string, cuenta: boolean): Promise<SeriesVenta> {
  return apiFetch(`/api/erp/historico/series/${encodeURIComponent(serie)}`, {
    method: "PUT", body: JSON.stringify({ cuenta }),
  });
}

/** Un artículo con clase propia, por encima de la de su familia. */
export type ArticuloClase = {
  codart: string;
  clase: HistoricoClase;
  por: string | null;
  at: string | null;
  nombre: string | null;
  familia: string | null;
  familia_nombre: string | null;
  clase_familia: HistoricoClase | null;
};

export async function getArticulosClase(): Promise<{ items: ArticuloClase[] }> {
  return apiFetch("/api/erp/historico/articulos-clase");
}

/** Pone la clase propia de un artículo (todos los años y clientes); null la
 *  quita y vuelve a la de su familia. */
export async function setClaseArticulo(
  codart: string, clase: HistoricoClase | null,
): Promise<{ items: ArticuloClase[] }> {
  return apiFetch(`/api/erp/historico/articulos-clase/${encodeURIComponent(codart)}`, {
    method: "PUT",
    body: JSON.stringify({ clase }),
  });
}

/** Una gama (UV LED, Láser…): en una máquina, qué es; en lo demás, para qué
 *  máquina sirve. La lista se amplía desde la pantalla. */
export type HistoricoGama = { codigo: string; nombre: string };

/** Lo que proponen las reglas para una familia. `revisar`: choca con lo que
 *  ya tiene y solo cambia si se acepta a mano; `descartada`: chocaba y se
 *  mantuvo la suya. */
export type HistoricoPropuesta = {
  clase: HistoricoClase | null;
  gama: string | null;
  estado: "sin_propuesta" | "propuesta" | "coincide" | "revisar" | "descartada";
};

export type HistoricoFamilia = {
  codigo: string;
  descripcion: string | null;
  clase: HistoricoClase | null;
  gama: string | null;
  articulos: number;
  ventas: number;
  propuesta: HistoricoPropuesta | null;
};

/** Regla que propone clase y/o gama: por el código exacto de la familia o
 *  porque su nombre contiene el patrón. Gana la de menor orden. */
export type HistoricoRegla = {
  id: number;
  campo: "codigo" | "nombre";
  patron: string;
  clase: HistoricoClase | null;
  gama: string | null;
  orden: number;
};

export type HistoricoArticulo = {
  codart: string;
  descripcion: string | null;
  familia: string | null;
  ultimo_ejercicio: string;
  ventas: number;
};

export type HistoricoComprador = {
  codcli: string | null;
  nombre: string | null;
  empresa: { id: string; nombre: string } | null;
  ultima_compra: {
    fecha: string | null;
    /** Solo si la fecha de FACTUSOL es imposible (p. ej. 1900). */
    fecha_original: string | null;
    ejercicio: string;
    tipo: "factura" | "albaran";
    documento: string;
  } | null;
  unidades: number;
  compras: number;
  articulos: { codart: string; descripcion: string | null }[];
};

export type HistoricoFiltro = {
  codart?: string;
  q?: string;
  familia?: string;
  clase?: string;
  gama?: string;
  desde?: number;
  hasta?: number;
};

export async function getHistoricoEstado(): Promise<HistoricoEstadoCarga> {
  return apiFetch("/api/erp/historico/ejercicios");
}

/** Encola la carga de esos ejercicios (vacío = los trece). */
export async function cargarHistorico(ejercicios: string[] = []): Promise<{ encolada: string[] }> {
  return apiFetch("/api/erp/historico/cargar", {
    method: "POST",
    body: JSON.stringify({ ejercicios }),
  });
}

export async function getHistoricoFamilias(): Promise<{
  items: HistoricoFamilia[]; clases: HistoricoClase[]; gamas: HistoricoGama[];
}> {
  return apiFetch("/api/erp/historico/familias");
}

type ClasificacionFamilia = { codigo: string; clase: HistoricoClase | null; gama: string | null };

/** Cambia la clase y/o la gama (lo que no se pasa, no se toca; null la quita). */
export async function clasificarHistoricoFamilia(
  codigo: string, cambios: { clase?: HistoricoClase | null; gama?: string | null },
): Promise<ClasificacionFamilia> {
  return apiFetch(`/api/erp/historico/familias/${encodeURIComponent(codigo)}`, {
    method: "PATCH",
    body: JSON.stringify(cambios),
  });
}

/** Acepta la propuesta de una familia, también si estaba para revisar. */
export async function aceptarPropuestaFamilia(codigo: string): Promise<ClasificacionFamilia> {
  return apiFetch(`/api/erp/historico/familias/${encodeURIComponent(codigo)}/aceptar`,
    { method: "POST" });
}

/** «Mantener la mía»: deja de marcarla para revisar. */
export async function mantenerClasificacionFamilia(codigo: string): Promise<ClasificacionFamilia> {
  return apiFetch(`/api/erp/historico/familias/${encodeURIComponent(codigo)}/mantener`,
    { method: "POST" });
}

/** Aplica las propuestas que no chocan; las marcadas para revisar, no. */
export async function aceptarPropuestasFamilias(): Promise<{ aplicadas: number; revisar: number }> {
  return apiFetch("/api/erp/historico/familias/aceptar-propuestas", { method: "POST" });
}

export async function crearHistoricoGama(nombre: string): Promise<HistoricoGama> {
  return apiFetch("/api/erp/historico/gamas", {
    method: "POST",
    body: JSON.stringify({ nombre }),
  });
}

export async function getHistoricoReglas(): Promise<{ items: HistoricoRegla[] }> {
  return apiFetch("/api/erp/historico/reglas");
}

export async function crearHistoricoRegla(
  regla: Omit<HistoricoRegla, "id">,
): Promise<HistoricoRegla> {
  return apiFetch("/api/erp/historico/reglas", {
    method: "POST",
    body: JSON.stringify(regla),
  });
}

export async function borrarHistoricoRegla(id: number): Promise<void> {
  await apiFetch(`/api/erp/historico/reglas/${id}`, { method: "DELETE" });
}

export async function buscarHistoricoArticulos(q: string): Promise<{ items: HistoricoArticulo[] }> {
  return apiFetch(`/api/erp/historico/articulos${qs({ q })}`);
}

export async function getHistoricoCompradores(f: HistoricoFiltro): Promise<{
  items: HistoricoComprador[]; total: number; sin_empresa: number;
}> {
  return apiFetch(`/api/erp/historico/compradores${qs({ ...f })}`);
}

export async function downloadHistoricoCompradoresCsv(f: HistoricoFiltro): Promise<Blob> {
  return apiDownloadBlob(`/api/erp/historico/compradores.csv${qs({ ...f })}`);
}

// --- Ficha de un documento (factura, albarán, proforma) de cualquier año -----

export type HistoricoTipoDocumento = "factura" | "albaran" | "proforma";

export type FichaDocumentoLinea = {
  posicion: number | null;
  codart: string | null;
  descripcion: string | null;
  /** Nombre del artículo en el catálogo DE SU AÑO. */
  articulo: string | null;
  familia: string | null;
  clase: HistoricoClase | null;
  gama: string | null;
  cantidad: number | null;
  precio: number | null;
  total: number | null;
  /** Línea sin artículo: una nota (portes, condiciones…), no una venta. */
  es_nota: boolean;
  /** «Proforma 1-000500»: de qué documento sale, como lo apunta FACTUSOL. */
  viene_de?: string | null;
};

export type FichaDocumento = {
  /** «volcado» (cualquier año) o «vivo» (de hoy, aún sin volcar). */
  fuente: "volcado" | "vivo";
  cargado_at: string | null;
  /** El estado de cobro / total se han leído en vivo (ejercicio en curso). */
  estado_en_vivo: boolean;
  tipo: HistoricoTipoDocumento;
  tipo_label: string;
  doc_type: FactusolDocType;
  ejercicio: string;
  serie: string;
  codigo: number;
  numero: string;
  fecha: string | null;
  fecha_original: string | null;
  cliente: { codcli: string | null; nombre: string | null;
             empresa: { id: string; nombre: string } | null };
  referencia: string | null;
  /** Con impuestos; null si se volcó antes de guardar el total. */
  total: number | null;
  /** Suma de las líneas de producto, sin impuestos. */
  suma_lineas: number;
  estado: string | null;
  estado_label: string | null;
  pedido: { id: string; order_number: string } | null;
  lineas: FichaDocumentoLinea[];
};

export async function getFichaDocumento(
  tipo: string, ejercicio: string, serie: string, codigo: string,
): Promise<FichaDocumento> {
  return apiFetch(`/api/erp/historico/documentos/${encodeURIComponent(tipo)}/${
    encodeURIComponent(ejercicio)}/${encodeURIComponent(serie)}/${encodeURIComponent(codigo)}`);
}

/** Ruta de la ficha de un documento. */
export function fichaDocumentoHref(
  tipo: HistoricoTipoDocumento, ejercicio: string | number, serie: string | number,
  codigo: string | number,
): string {
  return `/erp/documentos/ficha/${tipo}/${ejercicio}/${serie}/${Number(codigo)}`;
}

// --- Parque instalado (ficha de empresa) ------------------------------------

export type ParqueDocRef = {
  tipo: "factura" | "albaran"; serie: string; codigo: number; ejercicio: string; numero: string;
  clave?: string;
};

export type ParqueMaquina = {
  /** De dónde sale: una factura, un albarán o añadida a mano. */
  origen: "factura" | "albaran" | "manual";
  /** Máquina volcada: su clave estable (para darla de baja). */
  clave: string | null;
  /** Máquina manual: su id. */
  id: string | null;
  codart: string | null;
  articulo: string | null;
  familia: string | null;
  gama: string | null;
  gama_nombre: string | null;
  fecha: string | null;
  fecha_original: string | null;
  ejercicio: string | null;
  cantidad: number;
  documento: { tipo: "factura" | "albaran"; serie: string; codigo: number; numero: string } | null;
  /** Los albaranes de esta misma venta, emparejados por el enlace de FACTUSOL. */
  albaranes?: ParqueDocRef[];
  /** Albarán sin enlace con una factura del mismo artículo cerca: quizá la misma venta. */
  posible_misma_venta?: ParqueDocRef | null;
  nota?: string | null;
  baja: { at: string | null; motivo: string | null; por: string | null } | null;
};

export type ParqueEmpresa = {
  codcli: string | null;
  /** Sin cliente de FACTUSOL vinculado: no se sabe qué ha comprado. */
  sin_cliente_factusol: boolean;
  maquinas: ParqueMaquina[];
  activas: number;
};

export type ComprasDocumento = {
  tipo: HistoricoTipoDocumento;
  tipo_label: string;
  ejercicio: string;
  serie: string;
  codigo: number;
  numero: string;
  fecha: string | null;
  fecha_original: string | null;
  total: number | null;
  estado_label: string | null;
};

export type ComprasEmpresa = {
  codcli: string | null;
  sin_cliente_factusol: boolean;
  /** El año elegido, o «todos». */
  ejercicio: string;
  en_curso: string;
  /** Años con documentos de este cliente, del más reciente al más antiguo. */
  ejercicios: string[];
  documentos: ComprasDocumento[];
};

export async function getParqueEmpresa(companyId: string): Promise<ParqueEmpresa> {
  return apiFetch(`/api/erp/parque/empresas/${encodeURIComponent(companyId)}`);
}

export async function getComprasEmpresa(
  companyId: string, ejercicio?: string,
): Promise<ComprasEmpresa> {
  return apiFetch(`/api/erp/parque/empresas/${encodeURIComponent(companyId)}/compras${
    qs({ ejercicio })}`);
}

export async function anadirMaquinaParque(
  companyId: string,
  datos: { descripcion: string; codart?: string; gama?: string; fecha?: string; nota?: string },
): Promise<{ id: string }> {
  return apiFetch(`/api/erp/parque/empresas/${encodeURIComponent(companyId)}/maquinas`, {
    method: "POST", body: JSON.stringify(datos),
  });
}

export async function borrarMaquinaParque(companyId: string, maquinaId: string): Promise<void> {
  await apiFetch(`/api/erp/parque/empresas/${encodeURIComponent(companyId)}/maquinas/${
    encodeURIComponent(maquinaId)}`, { method: "DELETE" });
}

export async function darBajaMaquina(
  companyId: string, maquina: { clave?: string | null; maquina_id?: string | null },
  motivo?: string,
): Promise<ParqueEmpresa> {
  return apiFetch(`/api/erp/parque/empresas/${encodeURIComponent(companyId)}/bajas`, {
    method: "POST", body: JSON.stringify({ ...maquina, motivo }),
  });
}

export async function deshacerBajaMaquina(
  companyId: string, maquina: { clave?: string | null; maquina_id?: string | null },
): Promise<ParqueEmpresa> {
  return apiFetch(`/api/erp/parque/empresas/${encodeURIComponent(companyId)}/bajas/deshacer`, {
    method: "POST", body: JSON.stringify(maquina),
  });
}
