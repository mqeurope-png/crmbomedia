import { apiFetch, apiUpload } from "./api";

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
  | "already_invoiced_externally";
export type StatusDomain = "payment" | "preparation" | "transport" | "invoice";

export type OrderSummary = {
  id: string;
  order_number: string;
  external_source: string;
  store_id: string | null;
  contact_id: string | null;
  company_id: string | null;
  total_amount: number;
  currency: string;
  payment_status: PaymentStatus;
  preparation_status: PreparationStatus;
  transport_status: TransportStatus;
  invoice_status: InvoiceStatus;
  tracking_number: string | null;
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

export type OrderCreatePayload = {
  order_number: string;
  company_id?: string | null;
  contact_id?: string | null;
  currency?: string;
  notes?: string | null;
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
  packed: { label: "Embalado", tone: "ok" },
  blocked: { label: "Bloqueado", tone: "bad" },
  already_completed_externally: { label: "Externalizado", tone: "muted" },
  // transport
  not_shipped: { label: "Sin enviar", tone: "muted" },
  label_created: { label: "Etiqueta creada", tone: "active" },
  in_transit: { label: "En tránsito", tone: "active" },
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
  preparation_status: PreparationStatus;
  payment_status: PaymentStatus;
  total_amount: number;
  currency: string;
  lines: { sku: string; description: string; quantity: number }[];
};

export async function getSatQueue(): Promise<SatQueueItem[]> {
  const r = await apiFetch<{ items: SatQueueItem[] }>("/api/erp/sat/queue");
  return r.items;
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
  /** Flag reservado para Fase C. Tras B-2-fix5 el ERP confía en la fuente
   *  y no valida SKU/empresas, así que hoy es un no-op. */
  factusol_live: boolean;
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
