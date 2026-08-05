import { apiDownloadBlob, apiFetch, apiUpload } from "./api";

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
  /** Fase C: nº de factura FACTUSOL (CODFAC) si ya se emitió; null si no. */
  factusol_invoice_number: string | null;
  approved_at: string | null;
  placed_at: string | null;
  created_at: string;
  /** B-2-fix4: seteado si el pedido se gestionó fuera del ERP. */
  externally_processed_at: string | null;
};

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

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export type OrderFilters = {
  payment?: string;
  preparation?: string;
  transport?: string;
  invoice?: string;
  store?: string;
  /** B-2-fix4: incluir los pedidos procesados externamente (por defecto ocultos). */
  show_external?: boolean;
  sort?: string;
  limit?: number;
};

export async function listOrders(filters: OrderFilters = {}): Promise<OrderSummary[]> {
  const { show_external, ...rest } = filters;
  const query = qs({ ...rest, show_external: show_external ? "true" : undefined });
  const r = await apiFetch<{ items: OrderSummary[] }>(`/api/erp/orders${query}`);
  return r.items;
}

export async function getOrder(id: string): Promise<OrderDetail> {
  return apiFetch<OrderDetail>(`/api/erp/orders/${id}`);
}

export async function listPendingApproval(): Promise<PendingOrder[]> {
  const r = await apiFetch<{ items: PendingOrder[] }>("/api/erp/orders/pending-approval");
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

export async function approveOrder(id: string): Promise<OrderDetail> {
  return apiFetch<OrderDetail>(`/api/erp/orders/${id}/approve`, { method: "POST" });
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
  lines: {
    product_sku: string;
    product_codart?: string | null;
    description?: string;
    quantity: number;
    unit_price: number;
    tax_rate?: number;
  }[];
};

export async function createOrder(payload: OrderCreatePayload): Promise<OrderDetail> {
  return apiFetch<OrderDetail>("/api/erp/orders", {
    method: "POST",
    body: JSON.stringify(payload),
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

export type SatQueueItem = {
  id: string;
  order_number: string;
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
  /** Fase D: presencia de albarán/etiqueta vigentes (para los chips). */
  has_albaran: boolean;
  has_etiqueta: boolean;
};

/** Cola SAT en 2 secciones (D-1-fix1): por embalar + listos para envío. */
export type SatQueue = {
  preparing: SatQueueItem[];
  ready_for_pickup: SatQueueItem[];
};

export async function getSatQueue(): Promise<SatQueue> {
  return apiFetch<SatQueue>("/api/erp/sat/queue");
}

/** «Marcar recogido»: el paquete salió del taller → transporte in_transit. */
export async function markPickedUp(
  orderId: string, trackingNumber?: string,
): Promise<{ order_id: string; transport_status: string; already_picked_up: boolean }> {
  return apiFetch(`/api/erp/orders/${orderId}/mark-picked-up`, {
    method: "POST",
    body: JSON.stringify(trackingNumber ? { tracking_number: trackingNumber } : {}),
  });
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

export async function attachDocument(
  orderId: string,
  file: File,
): Promise<{ document: Record<string, unknown> }> {
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

export async function resolveException(id: string, note: string): Promise<ErpExceptionRow> {
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
};

export async function getErpSettings(): Promise<ErpSettings> {
  return apiFetch<ErpSettings>("/api/erp/settings");
}

export async function updateErpSettings(patch: Partial<ErpSettings>): Promise<ErpSettings> {
  return apiFetch<ErpSettings>("/api/erp/settings", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
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
  | { status: "failed"; error?: string };

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
  serfac?: string | null;
  /** Fecha de emisión ISO `YYYY-MM-DD`; omitir → hoy (lo pone el backend). */
  fecfac?: string | null;
  fopfac?: string | null;
  comfac?: string | null;
};

export type FormaPago = { codigo: string | null; nombre: string };

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
  /** Vínculo CRM existente (null si el cliente aún no está en el CRM). */
  crm_link: { type: "company" | "contact"; id: string; name: string } | null;
  factusol_matches_crm_id: string | null;
};

export async function searchFactusolCustomers(
  q: string, by: "nif" | "email" | "name" = "nif",
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

export type CreateFactusolCustomerPayload = {
  crm_type: "company" | "contact";
  crm_id: string;
  nombre: string;
  nif?: string;
  direccion?: string;
  ciudad?: string;
  cp?: string;
  provincia?: string;
  pais?: string;
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
  };
}): Promise<{ company_id: string; factusol_codcli: string; created: boolean }> {
  return apiFetch("/api/erp/factusol/customers/create-crm-and-link", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function createFactusolCustomer(
  payload: CreateFactusolCustomerPayload,
): Promise<{ factusol_codcli: string; created: boolean }> {
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
  codart: string | null;
  description: string;
  quantity: number;
  unit_price: number;
  discount_pct: number;
  line_total: number;
  iva_pct: number;
};

/** Proforma (presupuesto F_PRE). Recuerda que F_PRE es MONO-LÍNEA: `lines`
 *  sale de la caché del CRM y está vacío en las proformas hechas en el
 *  FACTUSOL de escritorio (`line_source: "ref_text"`). */
export type FactusolQuote = {
  codpre: string | null;
  referencia: string;
  fecha: string | null;
  clipre: string | null;
  cliente_nombre: string | null;
  base: number;
  iva: number;
  total: number;
  lines?: FactusolQuoteLine[];
  line_source?: "cache" | "ref_text";
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

export async function listFactusolQuotes(
  opts: { company_id?: string; days_back?: number } = {},
): Promise<{ items: FactusolQuote[]; unlinked: boolean }> {
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

export async function getFactusolQuote(codpre: string): Promise<FactusolQuote> {
  return apiFetch(`/api/erp/factusol/quotes/${encodeURIComponent(codpre)}`);
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
  /** Solo el recuento: son miles y listarlos no aporta. */
  no_match_count: number;
  matches_without_company: number;
  ejercicio: string;
};

/** Un `skipped` no es un error: es un caso en el que aquí no toca escribir. */
export type BulkMatchSkip = {
  contact_id: string;
  result: "skipped_no_company" | "already_linked_other";
  detail?: string;
};

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
  skipped: BulkMatchSkip[];
  errors: { contact_id: string; error: string }[];
}> {
  return apiFetch("/api/erp/factusol/bulk-match/by-contact-email/apply", {
    method: "POST",
    body: JSON.stringify({ operations }),
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
  fopfac?: string | null;
  /** Dirección de envío elegida; omitir → la de la empresa CRM. */
  address?: {
    direccion?: string;
    ciudad?: string;
    cp?: string;
    provincia?: string;
    pais?: string;
  } | null;
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
): Promise<{ job_id: string; status: string; codpre: string }> {
  return apiFetch(`/api/erp/factusol/quotes/${encodeURIComponent(codpre)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function duplicateFactusolQuote(
  codpre: string,
): Promise<{ job_id: string; status: string; source_codpre: string }> {
  return apiFetch(
    `/api/erp/factusol/quotes/${encodeURIComponent(codpre)}/duplicate`,
    { method: "POST" },
  );
}

export async function convertFactusolQuoteToOrder(
  codpre: string,
): Promise<{ job_id: string; status: string; codpre: string }> {
  return apiFetch(
    `/api/erp/factusol/quotes/${encodeURIComponent(codpre)}/convert-to-order`,
    { method: "POST" },
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

export type ShipmentFileKind = "albaran" | "etiqueta";
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

export async function uploadShippingFile(
  orderId: string, kind: ShipmentFileKind, file: File,
): Promise<{ file: ShipmentFile }> {
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
export async function openShippingFile(file: ShipmentFile): Promise<void> {
  const blob = await apiDownloadBlob(file.download_url);
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener");
  // El navegador retiene el blob mientras la pestaña lo usa; lo liberamos tras
  // un margen para no cortar la apertura.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
