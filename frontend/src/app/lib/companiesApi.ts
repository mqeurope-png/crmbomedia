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
