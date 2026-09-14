import { apiFetch } from "./api";

export type Company = {
  id: string;
  name: string;
  website: string | null;
  domain: string | null;
  tax_id: string | null;
  vat: string | null;
  country: string | null;
  region: string | null;
  state: string | null;
  city: string | null;
  address_line: string | null;
  postal_code: string | null;
  sector: string | null;
  size_category: string | null;
  notes: string | null;
  source: string;
  is_active: boolean;
  /** C-3: CODCLI del cliente en FACTUSOL (null si no está vinculado). */
  factusol_company_id: string | null;
  external_references: Record<string, unknown>;
  custom_fields: Record<string, unknown>;
  contacts_count: number;
  created_at: string;
  updated_at: string;
};

export type CompanyWrite = {
  name: string;
  website?: string | null;
  domain?: string | null;
  tax_id?: string | null;
  vat?: string | null;
  country?: string | null;
  region?: string | null;
  state?: string | null;
  city?: string | null;
  address_line?: string | null;
  postal_code?: string | null;
  sector?: string | null;
  size_category?: string | null;
  notes?: string | null;
  source?: string;
  external_references?: Record<string, unknown>;
  custom_fields?: Record<string, unknown>;
};

export type CompanyList = {
  items: Company[];
  total: number;
};

export type CompanyListFilters = {
  q?: string;
  country?: string;
  source?: string;
  has_contacts?: boolean;
  limit?: number;
  offset?: number;
};

export async function listCompanies(
  filters: CompanyListFilters = {},
): Promise<CompanyList> {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.country) params.set("country", filters.country);
  if (filters.source) params.set("source", filters.source);
  if (filters.has_contacts !== undefined) {
    params.set("has_contacts", String(filters.has_contacts));
  }
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.offset !== undefined) params.set("offset", String(filters.offset));
  const qs = params.toString();
  return apiFetch<CompanyList>(`/api/companies${qs ? `?${qs}` : ""}`);
}

export async function getCompany(id: string): Promise<Company> {
  return apiFetch<Company>(`/api/companies/${id}`);
}

export async function createCompany(payload: CompanyWrite): Promise<Company> {
  return apiFetch<Company>("/api/companies", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateCompany(
  id: string,
  payload: CompanyWrite,
): Promise<Company> {
  return apiFetch<Company>(`/api/companies/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteCompany(id: string): Promise<void> {
  await apiFetch(`/api/companies/${id}`, { method: "DELETE" });
}

export async function mergeCompanies(
  source_id: string,
  target_id: string,
): Promise<Company> {
  return apiFetch<Company>(
    `/api/companies/${source_id}/merge/${target_id}`,
    { method: "POST" },
  );
}

export type CompanyContact = {
  id: string;
  first_name: string;
  last_name: string | null;
  email: string | null;
  phone: string | null;
  commercial_status: string;
  owner_user_id: string | null;
};

export async function listCompanyContacts(
  id: string,
): Promise<CompanyContact[]> {
  return apiFetch<CompanyContact[]>(`/api/companies/${id}/contacts`);
}

export async function assignContactCompany(
  contact_id: string,
  company_id: string | null,
): Promise<{ contact_id: string; company_id: string | null }> {
  return apiFetch(`/api/contacts/${contact_id}/assign-company`, {
    method: "POST",
    body: JSON.stringify({ company_id }),
  });
}

// Sprint Filtros & Listas — PR-F. Bulk dispatch para la migración de
// /companies. Antes la pantalla legacy no tenía bulk en absoluto.

export type CompanyBulkAction =
  | "activate"
  | "deactivate"
  | "change_sector";

export type CompanyBulkResult = {
  action: CompanyBulkAction;
  affected_count: number;
  company_ids: string[];
};

export async function bulkCompanyAction(
  companyIds: string[],
  action: CompanyBulkAction,
  payload: Record<string, unknown> = {},
): Promise<CompanyBulkResult> {
  return apiFetch<CompanyBulkResult>("/api/companies/bulk-action", {
    method: "POST",
    body: JSON.stringify({
      company_ids: companyIds,
      action,
      payload,
    }),
  });
}

// --- deduplicar por NIF (Fase C · C-7) --------------------------------------

/** Una de las empresas de un grupo de duplicados, con lo que aporta. */
export type DuplicateCompany = {
  id: string;
  name: string;
  city: string | null;
  address_line: string | null;
  postal_code: string | null;
  state: string | null;
  country: string | null;
  website: string | null;
  domain: string | null;
  notes: string | null;
  factusol_company_id: string | null;
  source: string;
  created_at: string;
  contacts_count: number;
  orders_count: number;
  tasks_count: number;
};

export type DuplicateGroup = {
  tax_id: string;
  companies: DuplicateCompany[];
};

export type DuplicatesResult = {
  total_groups: number;
  total_companies_involved: number;
  groups: DuplicateGroup[];
};

export type MergeResult = {
  merged_groups: number;
  companies_deleted: number;
  contacts_moved: number;
  orders_moved: number;
  tasks_moved: number;
  results: {
    keep_id: string;
    merged_ids: string[];
    contacts_moved: number;
    orders_moved: number;
    tasks_moved: number;
    filled_fields: string[];
    discarded_factusol_codclis: string[];
  }[];
  errors: { keep_id: string; merge_ids: string[]; error: string }[];
};

export async function findDuplicateCompanies(): Promise<DuplicatesResult> {
  return apiFetch<DuplicatesResult>(
    "/api/admin/companies/duplicates?by=tax_id");
}

export async function mergeDuplicateCompanies(
  operations: { keep_id: string; merge_ids: string[] }[],
): Promise<MergeResult> {
  return apiFetch<MergeResult>("/api/admin/companies/merge", {
    method: "POST",
    body: JSON.stringify({ operations }),
  });
}

/** Qué empresa del grupo viene premarcada como principal.
 *
 *  Por orden: más pedidos (más historia comercial que conservar), más
 *  contactos, más antigua, y por último la que tenga vínculo con FACTUSOL.
 *  El operador puede cambiarla. */
export function pickDefaultKeep(companies: DuplicateCompany[]): string {
  const score = (c: DuplicateCompany): number[] => [
    c.orders_count,
    c.contacts_count,
    // created_at ascendente: se niega para que «más antigua» puntúe más alto.
    -new Date(c.created_at).getTime(),
    c.factusol_company_id ? 1 : 0,
  ];
  let best = companies[0];
  if (!best) return "";
  let bestScore = score(best);
  for (const c of companies.slice(1)) {
    const s = score(c);
    // Comparación lexicográfica: el primer criterio que difiera decide.
    for (let i = 0; i < s.length; i += 1) {
      if (s[i] === bestScore[i]) continue;
      if (s[i] > bestScore[i]) { best = c; bestScore = s; }
      break;
    }
  }
  return best.id;
}

// --- Fase 2 (rediseño de flujo): «Crear empresa» -----------------------------

/** Lo que la pantalla «Crear empresa» sabe de los datos fiscales ANTES de
 *  guardar: régimen de IVA detectado (país + NIF-IVA, la misma regla que fija
 *  la ficha F_CLI), duplicados en el CRM y en FACTUSOL, y el gancho VIES. */
export type FiscalCheck = {
  country_iso2: string | null;
  in_eu: boolean;
  regime: "nacional" | "intracomunitario" | "exportacion";
  regime_label: string;
  regime_reason: string;
  vat_normalized: string | null;
  duplicates: {
    crm: {
      id: string; name: string; tax_id: string | null; vat: string | null;
      country: string | null; factusol_company_id: string | null;
    }[];
    /** Cliente F_CLI con ese NIF (null si no hay o no se pudo comprobar). */
    factusol: { codcli: string; nombre: string | null; nif: string | null } | null;
    factusol_checked: boolean;
    factusol_error: string | null;
  };
  /** Validación VIES (PR aparte): hoy siempre «pendiente». */
  vies: {
    status: "pendiente" | "valido" | "no_valido" | "desconocido";
    valid: boolean | null;
    checked_at: string | null;
  };
};

export async function fiscalCheck(params: {
  tax_id?: string; vat?: string; country?: string; exclude_id?: string;
}): Promise<FiscalCheck> {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v && v.trim()) sp.set(k, v.trim());
  }
  return apiFetch<FiscalCheck>(`/api/companies/fiscal-check?${sp.toString()}`);
}
