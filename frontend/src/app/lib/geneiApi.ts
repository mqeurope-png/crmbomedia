// Genei (envíos) — capa de API del frontend. PR-1: preparar, comparar agencias,
// crear el envío, traer la etiqueta, «Actualizar estado» y eliminar; más los
// ajustes del carrier. El pago y el webhook son PR-2.
import { apiFetch } from "./api";
import type { ShipmentFile } from "./erpApi";

export type GeneiDestination = {
  name: string;
  contact: string;
  dni: string;
  email: string;
  phone: string;
  address: string;
  postalCode: string;
  town: string;
  isoCountry: string;
  observations: string;
};

export type GeneiPackage = {
  weight: number;
  height: number;
  width: number;
  length: number;
};

export type GeneiState = {
  shipment_code?: string;
  agency_id?: string;
  courier?: string | null;
  state_code?: number | null;
  state_bucket?: string;
  state_label?: string;
  tracking?: string | null;
  payment_url?: string | null;
  transaction_id?: string | null;
  created_at?: string;
  paid_at?: string;
  label_fetched_at?: string;
  refreshed_at?: string;
  /** Envío TRAMITADO (Genei estado 1+): la etiqueta ya se puede descargar.
   *  Antes (pendiente de pago / de tramitar) no se ofrece. */
  label_available?: boolean;
};

export type GeneiPrefill = {
  order_id: string;
  configured: boolean;
  destination: GeneiDestination;
  missing: string[];
  /** Bultos reales medidos por el SAT al embalar (vacío si aún no hay). */
  packages: GeneiPackage[];
  default_package: GeneiPackage;
  preferred_couriers: string[];
  origin_address_id: string | null;
  /** El pedido está embalado («Embalados»): requisito para crear el envío. */
  is_packed: boolean;
  state: GeneiState;
  /** De dónde sale cada dato del destino (pedido, destinatario, contacto,
   *  documento FACTUSOL, ficha F_CLI, empresa…). */
  destination_sources?: Record<string, string>;
};

export type GeneiAgencyOption = {
  agency_id: string;
  name: string;
  price: number | null;
  home_delivery: boolean;
};

export type GeneiPricesResult = {
  order_id: string;
  default: GeneiAgencyOption | null;
  home_options: GeneiAgencyOption[];
  all_options: GeneiAgencyOption[];
  preferred_couriers: string[];
};

export type GeneiShipmentSummary = {
  shipment_code: string | null;
  state_code: number | null;
  state_bucket: string;
  state_label: string;
  tracking: string | null;
  courier: string | null;
  payment_url: string | null;
};

export type GeneiConfig = {
  configured: boolean;
  username: string;
  base_url: string;
  default_address_id: string | null;
  preferred_couriers: Record<string, string[]>;
  default_package: GeneiPackage;
  origin: { iso_country: string; postal_code: string; town: string };
  is_warehouse: boolean;
  /** PR-2: base pública del backend para el webhook de estados. */
  webhook_base_url: string;
  /** El webhook está operativo (base + secreto + credenciales). El secreto no se devuelve. */
  webhook_configured: boolean;
};

const base = (orderId: string) => `/api/erp/orders/${orderId}/genei`;

/** Datos para crear el envío. Con `completar`, lo que falte del destino
 *  (dirección, teléfono, email) se completa leyendo FACTUSOL — se usa al
 *  ABRIR «Crear envío», no al pintar la sección. */
export function geneiPrefill(
  orderId: string, opts: { completar?: boolean } = {},
): Promise<GeneiPrefill> {
  return apiFetch(`${base(orderId)}/prefill${opts.completar ? "?completar=true" : ""}`);
}

export function geneiPrices(
  orderId: string,
  body: { destination: GeneiDestination; packages: GeneiPackage[]; home_only?: boolean },
): Promise<GeneiPricesResult> {
  return apiFetch(`${base(orderId)}/prices`, {
    method: "POST", body: JSON.stringify(body),
  });
}

export function geneiCreateShipment(
  orderId: string,
  body: {
    agency_id: string; destination: GeneiDestination;
    packages: GeneiPackage[]; observations?: string | null;
  },
): Promise<{ order_id: string; summary: GeneiShipmentSummary; state: GeneiState }> {
  return apiFetch(`${base(orderId)}/shipments`, {
    method: "POST", body: JSON.stringify(body),
  });
}

export function geneiFetchLabel(
  orderId: string,
): Promise<{
  order_id: string; file: ShipmentFile;
  transition_applied: boolean; transition_reason: string | null; state: GeneiState;
}> {
  return apiFetch(`${base(orderId)}/label`, { method: "POST" });
}

export function geneiRefresh(
  orderId: string,
): Promise<{ order_id: string; summary: GeneiShipmentSummary; state: GeneiState }> {
  return apiFetch(`${base(orderId)}/refresh`, { method: "POST" });
}

/** «Pagar y tramitar»: paga el envío por API (contra el saldo de la cuenta), sin
 *  popup. Lo dispara una persona con el botón; nunca automático. */
export function geneiPay(
  orderId: string,
): Promise<{ order_id: string; summary: GeneiShipmentSummary; state: GeneiState }> {
  return apiFetch(`${base(orderId)}/pay`, { method: "POST" });
}

export function geneiDeleteShipment(orderId: string): Promise<{ order_id: string; deleted: boolean }> {
  return apiFetch(`${base(orderId)}/shipment`, { method: "DELETE" });
}

export function getGeneiConfig(): Promise<GeneiConfig> {
  return apiFetch(`/api/erp/genei/config`);
}

export function saveGeneiConfig(body: {
  username?: string; password?: string; base_url?: string;
  default_address_id?: string | null;
  preferred_couriers?: Record<string, string[]>;
  default_package?: GeneiPackage;
  origin?: { iso_country: string; postal_code: string; town: string };
  is_warehouse?: boolean;
  webhook_base_url?: string;
}): Promise<GeneiConfig> {
  return apiFetch(`/api/erp/genei/config`, {
    method: "PUT", body: JSON.stringify(body),
  });
}

/** Color de pastilla por bucket de estado Genei (reutiliza los tonos del ERP). */
export function geneiStateTone(bucket: string | undefined): string {
  switch (bucket) {
    case "delivered": return "ok";
    case "in_transit": return "info";
    case "ready": return "info";
    case "incident": return "bad";
    case "closed": return "muted";
    case "created": return "warn";
    case "processing": return "warn";
    default: return "muted";
  }
}
