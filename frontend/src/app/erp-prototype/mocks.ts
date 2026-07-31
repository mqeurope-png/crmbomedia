/** Sprint 0 — datos MOCK del prototipo ERP. Desechable: nada de esto se
 *  reusa en el MVP. Los shapes espejan docs/erp/data-model.md. */

export type Domain = "payment" | "preparation" | "shipping" | "invoicing";

export const DOMAIN_LABEL: Record<Domain, string> = {
  payment: "Pago",
  preparation: "Preparación",
  shipping: "Transporte",
  invoicing: "Facturación",
};

export const STATUS_META: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "muted" | "active" }> = {
  pending: { label: "Pendiente", tone: "warn" },
  paid: { label: "Pagado", tone: "ok" },
  payment_failed: { label: "Pago fallido", tone: "bad" },
  refunded: { label: "Reembolsado", tone: "muted" },
  queued: { label: "En cola", tone: "muted" },
  preparing: { label: "Preparando", tone: "active" },
  packed: { label: "Embalado", tone: "ok" },
  blocked: { label: "Bloqueado", tone: "bad" },
  not_shipped: { label: "Sin enviar", tone: "muted" },
  label_created: { label: "Etiqueta creada", tone: "active" },
  in_transit: { label: "En tránsito", tone: "active" },
  delivered: { label: "Entregado", tone: "ok" },
  incident: { label: "Incidencia", tone: "bad" },
  not_invoiced: { label: "Sin facturar", tone: "muted" },
  invoice_pending: { label: "Factura pendiente", tone: "warn" },
  invoiced: { label: "Facturada", tone: "ok" },
  invoice_error: { label: "Error factura", tone: "bad" },
};

export type MockOrder = {
  id: string;
  number: string;
  store: string;
  customer: string;
  company: string | null;
  total: string;
  placedAt: string;
  payment: string;
  preparation: string;
  shipping: string;
  invoicing: string;
  lines: { sku: string; name: string; qty: number; total: string; mapped: boolean }[];
  history: { at: string; domain: Domain; from: string; to: string; actor: string; via: string; evidence?: string }[];
};

export const ORDERS: MockOrder[] = [
  {
    id: "ord-1001", number: "MBO-2417", store: "mbolasers.com",
    customer: "Laura Pérez", company: "Rotulación Pérez SL", total: "4.890,00 €",
    placedAt: "2026-07-30 09:12",
    payment: "paid", preparation: "preparing", shipping: "not_shipped", invoicing: "invoice_pending",
    lines: [
      { sku: "SKU-MBO-3050", name: "MBO Laser 3050 80W", qty: 1, total: "4.500,00 €", mapped: true },
      { sku: "SKU-ROTATIVO", name: "Accesorio rotativo", qty: 1, total: "390,00 €", mapped: true },
    ],
    history: [
      { at: "2026-07-30 09:12", domain: "payment", from: "—", to: "pending", actor: "system", via: "webhook" },
      { at: "2026-07-30 09:14", domain: "payment", from: "pending", to: "paid", actor: "system", via: "webhook", evidence: "date_paid Woo" },
      { at: "2026-07-30 09:14", domain: "preparation", from: "—", to: "queued", actor: "system", via: "sync" },
      { at: "2026-07-31 08:02", domain: "preparation", from: "queued", to: "preparing", actor: "Marc (SAT)", via: "ui" },
      { at: "2026-07-30 09:15", domain: "invoicing", from: "not_invoiced", to: "invoice_pending", actor: "system", via: "sync" },
    ],
  },
  {
    id: "ord-1002", number: "MBO-2418", store: "mbolasers.com",
    customer: "Jordi Vila", company: null, total: "129,00 €",
    placedAt: "2026-07-30 11:40",
    payment: "paid", preparation: "packed", shipping: "in_transit", invoicing: "invoiced",
    lines: [{ sku: "FLUX-BEAMO-LENS", name: "Lente Beamo 30W", qty: 1, total: "129,00 €", mapped: true }],
    history: [
      { at: "2026-07-30 11:40", domain: "payment", from: "—", to: "paid", actor: "system", via: "webhook" },
      { at: "2026-07-30 16:20", domain: "preparation", from: "preparing", to: "packed", actor: "Marc (SAT)", via: "ui", evidence: "foto bulto #a41f" },
      { at: "2026-07-31 08:30", domain: "shipping", from: "label_created", to: "in_transit", actor: "Nuria (office)", via: "ui", evidence: "tracking GN-88123" },
      { at: "2026-07-31 08:31", domain: "invoicing", from: "invoice_pending", to: "invoiced", actor: "system", via: "api", evidence: "FACTUSOL A-2026/1187" },
    ],
  },
  {
    id: "ord-1003", number: "ARJ-0554", store: "artisjet-europe.com",
    customer: "Anna Costa", company: "Costa Print", total: "12.400,00 €",
    placedAt: "2026-07-29 17:05",
    payment: "pending", preparation: "queued", shipping: "not_shipped", invoicing: "not_invoiced",
    lines: [{ sku: "ARTIS-5000U", name: "ArtisJet 5000U", qty: 1, total: "12.400,00 €", mapped: false }],
    history: [
      { at: "2026-07-29 17:05", domain: "payment", from: "—", to: "pending", actor: "system", via: "webhook" },
    ],
  },
  {
    id: "ord-1004", number: "FLX-0201", store: "fluxlasers.es",
    customer: "Pau Riera", company: null, total: "3.150,00 €",
    placedAt: "2026-07-28 10:22",
    payment: "paid", preparation: "blocked", shipping: "not_shipped", invoicing: "invoice_error",
    lines: [{ sku: "FLUX-BEAMBOX", name: "Flux Beambox Pro", qty: 1, total: "3.150,00 €", mapped: true }],
    history: [
      { at: "2026-07-28 10:25", domain: "payment", from: "pending", to: "paid", actor: "system", via: "webhook" },
      { at: "2026-07-29 09:00", domain: "preparation", from: "preparing", to: "blocked", actor: "Marc (SAT)", via: "ui", evidence: "falta cable alimentación en stock" },
      { at: "2026-07-29 09:10", domain: "invoicing", from: "invoice_pending", to: "invoice_error", actor: "system", via: "api", evidence: "FACTUSOL 401 token" },
    ],
  },
];

export const SAT_QUEUE = ORDERS.filter((o) => ["queued", "preparing", "blocked"].includes(o.preparation))
  .map((o) => ({
    id: o.id, number: o.number, customer: o.customer,
    status: o.preparation, paid: o.payment === "paid",
    lines: o.lines.map((l) => `${l.qty}× ${l.name}`),
  }));

export const EXCEPTIONS = [
  {
    id: "exc-1", kind: "sku_unmapped", order: "ARJ-0554", status: "open",
    detail: "SKU 'ARTIS-5000U' sin mapping confirmado a CODART — facturación bloqueada.",
    at: "2026-07-29 17:06",
  },
  {
    id: "exc-2", kind: "factusol_write_failed", order: "FLX-0201", status: "open",
    detail: "EscribirRegistro F_FAC → 401 (token caducado, 3 reintentos). Reintentar.",
    at: "2026-07-29 09:10",
  },
  {
    id: "exc-3", kind: "shipping_incident", order: "MBO-2411", status: "ack",
    detail: "DSV: palé retenido en delegación de Zaragoza (incidencia 5512).",
    at: "2026-07-28 13:44",
  },
];

export const COMPANY_DEMO = {
  name: "Rotulación Pérez SL",
  cif: "B61234567",
  city: "Barcelona",
  email: "compras@rotulacionperez.es",
  phone: "+34 932 000 111",
  factusolCodcli: null as string | null, // null → botón "Crear en FACTUSOL"
  orders: ORDERS.filter((o) => o.company === "Rotulación Pérez SL"),
};
