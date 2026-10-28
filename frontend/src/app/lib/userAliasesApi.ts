import { apiFetch } from "./api";
import type { AliasPreferenceItem, EmailAlias } from "./emailsApi";

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

/**
 * Remitentes del «De» de un usuario (`user_email_alias_prefs`), desde su ficha
 * de Usuarios. Solo admin. Misma forma que Mi cuenta (`getEmailAliases` /
 * `putEmailAliasPreferences`), más las direcciones que Gmail no tiene
 * (`en_gmail=false`). Cada cambio queda en Auditoría.
 */
export const getUserSenders = (userId: string) =>
  apiFetch<EmailAlias[]>(`/api/users/${userId}/senders`);

export const putUserSenders = (
  userId: string,
  preferences: AliasPreferenceItem[],
) =>
  apiFetch<EmailAlias[]>(`/api/users/${userId}/senders`, {
    method: "PUT",
    body: JSON.stringify({ preferences }),
  });
