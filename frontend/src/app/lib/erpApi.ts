import { apiFetch } from "./api";

/** BoHub ERP Fase A — cliente de la API de pedidos (PR 3 backend). */

export type PaymentStatus =
  | "pending" | "paid" | "partial_paid" | "credit_approved" | "failed" | "refunded";
export type PreparationStatus =
  | "pending_review" | "in_queue" | "preparing" | "packed" | "blocked";
export type TransportStatus =
  | "not_shipped" | "label_created" | "in_transit" | "delivered" | "incident" | "returned";
export type InvoiceStatus =
  | "not_invoiced" | "pending" | "generated" | "error" | "credit_note";
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
};

export type Blocker = { code: string; detail: string };

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
};

export type PendingOrder = OrderSummary & { blockers: Blocker[] };

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
  sort?: string;
  limit?: number;
};

export async function listOrders(filters: OrderFilters = {}): Promise<OrderSummary[]> {
  const r = await apiFetch<{ items: OrderSummary[] }>(`/api/erp/orders${qs(filters)}`);
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
  // transport
  not_shipped: { label: "Sin enviar", tone: "muted" },
  label_created: { label: "Etiqueta creada", tone: "active" },
  in_transit: { label: "En tránsito", tone: "active" },
  delivered: { label: "Entregado", tone: "ok" },
  incident: { label: "Incidencia", tone: "bad" },
  returned: { label: "Devuelto", tone: "bad" },
  // invoice
  not_invoiced: { label: "Sin facturar", tone: "muted" },
  generated: { label: "Facturada", tone: "ok" },
  error: { label: "Error factura", tone: "bad" },
  credit_note: { label: "Abono", tone: "muted" },
};

export const DOMAIN_LABELS: Record<StatusDomain, string> = {
  payment: "Pago",
  preparation: "Preparación",
  transport: "Transporte",
  invoice: "Facturación",
};

/** Roles con permiso de edición/aprobación en el ERP (espejo de deps.py). */
export const ERP_EDIT_ROLES = ["admin", "pedidos"] as const;
