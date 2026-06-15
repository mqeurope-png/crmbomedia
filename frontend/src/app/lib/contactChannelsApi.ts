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

export type ContactEmail = {
  id: string;
  contact_id: string;
  label: string | null;
  email: string;
  is_primary: boolean;
  is_verified: boolean;
  source: string;
  created_at: string;
  updated_at: string;
};

export type ContactEmailWrite = {
  label?: string | null;
  email: string;
  is_primary?: boolean;
  is_verified?: boolean;
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

export const listContactEmails = (contactId: string) =>
  apiFetch<ContactEmail[]>(`/api/contacts/${contactId}/emails`);

export const createContactEmail = (
  contactId: string,
  payload: ContactEmailWrite,
) =>
  apiFetch<ContactEmail>(`/api/contacts/${contactId}/emails`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateContactEmail = (
  contactId: string,
  emailId: string,
  payload: ContactEmailWrite,
) =>
  apiFetch<ContactEmail>(`/api/contacts/${contactId}/emails/${emailId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

export const deleteContactEmail = async (
  contactId: string,
  emailId: string,
) => {
  await apiFetch(`/api/contacts/${contactId}/emails/${emailId}`, {
    method: "DELETE",
  });
};

export const setPrimaryEmail = (contactId: string, emailId: string) =>
  apiFetch<ContactEmail>(
    `/api/contacts/${contactId}/emails/${emailId}/primary`,
    { method: "POST" },
  );
