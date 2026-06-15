import { apiFetch } from "./api";

export type ContactPhone = {
  id: string;
  contact_id: string;
  label: string | null;
  number: string;
  is_primary: boolean;
  source: string;
  created_at: string;
  updated_at: string;
};

export type ContactPhoneWrite = {
  label?: string | null;
  number: string;
  is_primary?: boolean;
  source?: string;
};

export const listContactPhones = (contactId: string) =>
  apiFetch<ContactPhone[]>(`/api/contacts/${contactId}/phones`);

export const createContactPhone = (
  contactId: string,
  payload: ContactPhoneWrite,
) =>
  apiFetch<ContactPhone>(`/api/contacts/${contactId}/phones`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateContactPhone = (
  contactId: string,
  phoneId: string,
  payload: ContactPhoneWrite,
) =>
  apiFetch<ContactPhone>(`/api/contacts/${contactId}/phones/${phoneId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

export const deleteContactPhone = async (
  contactId: string,
  phoneId: string,
) => {
  await apiFetch(`/api/contacts/${contactId}/phones/${phoneId}`, {
    method: "DELETE",
  });
};

export const setPrimaryPhone = (contactId: string, phoneId: string) =>
  apiFetch<ContactPhone>(
    `/api/contacts/${contactId}/phones/${phoneId}/primary`,
    { method: "POST" },
  );
