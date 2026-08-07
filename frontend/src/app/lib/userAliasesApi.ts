import { apiFetch } from "./api";

/**
 * CRM-GMAIL — registro de alias de correo ENTRANTE por usuario.
 *
 * Distinto de los alias Send-As (`emailsApi.ts` / `getEmailAliases`), que son
 * preferencias de envío. Estos definen de quién es la bandeja del correo que
 * LLEGA a cada alias (unique global) y alimentan el filtro de visibilidad.
 */

export type UserEmailAlias = {
  id: string;
  user_id: string;
  alias_email: string;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export const listUserAliases = (userId: string) =>
  apiFetch<UserEmailAlias[]>(`/api/users/${userId}/aliases`);

export const createUserAlias = (userId: string, aliasEmail: string) =>
  apiFetch<UserEmailAlias>(`/api/users/${userId}/aliases`, {
    method: "POST",
    body: JSON.stringify({ alias_email: aliasEmail }),
  });

export const updateUserAlias = (
  userId: string,
  aliasId: string,
  active: boolean,
) =>
  apiFetch<UserEmailAlias>(`/api/users/${userId}/aliases/${aliasId}`, {
    method: "PATCH",
    body: JSON.stringify({ active }),
  });

export const deleteUserAlias = (userId: string, aliasId: string) =>
  apiFetch<void>(`/api/users/${userId}/aliases/${aliasId}`, {
    method: "DELETE",
  });
