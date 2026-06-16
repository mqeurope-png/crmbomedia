import { apiFetch } from "./api";

export type ContactNote = {
  id: string;
  contact_id: string;
  content: string;
  source: string;
  pinned: boolean;
  created_by_user_id: string | null;
  /** Post-unification (migration 0049) — los siguientes campos vienen
   *  de las notas importadas del timeline AgileCRM. NULL para notas
   *  manuales o de Note1..Note10 custom-properties. */
  external_author_name: string | null;
  external_author_email: string | null;
  external_created_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ContactNoteWrite = {
  content: string;
  pinned?: boolean;
};

export const listContactNotes = (contactId: string) =>
  apiFetch<ContactNote[]>(`/api/contacts/${contactId}/notes`);

export const createContactNote = (
  contactId: string,
  payload: ContactNoteWrite,
) =>
  apiFetch<ContactNote>(`/api/contacts/${contactId}/notes`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateContactNote = (
  contactId: string,
  noteId: string,
  payload: ContactNoteWrite,
) =>
  apiFetch<ContactNote>(`/api/contacts/${contactId}/notes/${noteId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

export const deleteContactNote = async (
  contactId: string,
  noteId: string,
) => {
  await apiFetch(`/api/contacts/${contactId}/notes/${noteId}`, {
    method: "DELETE",
  });
};

export const pinContactNote = (contactId: string, noteId: string) =>
  apiFetch<ContactNote>(
    `/api/contacts/${contactId}/notes/${noteId}/pin`,
    { method: "POST" },
  );

export const unpinContactNote = (contactId: string, noteId: string) =>
  apiFetch<ContactNote>(
    `/api/contacts/${contactId}/notes/${noteId}/unpin`,
    { method: "POST" },
  );
