"use client";

import { useEffect, useState } from "react";
import {
  createUserAlias,
  deleteUserAlias,
  listUserAliases,
  updateUserAlias,
  type UserEmailAlias,
} from "../lib/userAliasesApi";
import { extractErrorMessage } from "../lib/errors";

/**
 * CRM-GMAIL Parte F — direcciones de ENTRADA de un usuario
 * (`user_email_aliases`).
 *
 * Admin-only (la página /admin/users ya está protegida). Lista las
 * direcciones que «posee» el usuario: el correo que llega a ellas se le
 * atribuye (lo ve en «Míos» y en el filtro por dirección de la Bandeja) y su
 * dominio cuenta como nuestro para capturar correo. Permite añadir,
 * activar/desactivar y borrar. La dirección es única global: si ya está
 * asignada a otro usuario, el backend devuelve 409.
 *
 * NO son remitentes: no le dejan escribir desde ellas. El desplegable «De» se
 * gestiona en «Remitentes» (`RemitentesUsuario`). Hasta el 28/10/2026 este
 * bloque se llamaba «Alias de email conectados» y se confundía con eso.
 */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function AliasManager({ userId }: { userId: string }) {
  const [aliases, setAliases] = useState<UserEmailAlias[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  async function reload() {
    setAliases(await listUserAliases(userId));
  }

  useEffect(() => {
    reload()
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudieron cargar los alias")),
      )
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const draftValid = EMAIL_RE.test(draft.trim());

  async function onAdd() {
    setError(null);
    setBusy(true);
    try {
      await createUserAlias(userId, draft.trim().toLowerCase());
      setDraft("");
      await reload();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo añadir el alias"));
    } finally {
      setBusy(false);
    }
  }

  async function onToggle(alias: UserEmailAlias) {
    setError(null);
    try {
      await updateUserAlias(userId, alias.id, !alias.active);
      await reload();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cambiar el estado"));
    }
  }

  async function onDelete(alias: UserEmailAlias) {
    setError(null);
    try {
      await deleteUserAlias(userId, alias.id);
      await reload();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar el alias"));
    }
  }

  return (
    <div className="alias-manager">
      <p className="alias-manager-title muted small">
        Direcciones de entrada (correo que recibe)
      </p>
      <p className="muted small">
        El correo que llega a estas direcciones se le atribuye: lo ve en
        «Míos» y en el filtro por dirección de la Bandeja. Cada dirección es de
        un solo usuario. No le dejan escribir desde ellas: eso son los
        remitentes, en el bloque de arriba.
      </p>
      {loading ? (
        <p className="muted small">Cargando direcciones…</p>
      ) : aliases.length === 0 ? (
        <p className="muted small">Sin direcciones de entrada.</p>
      ) : (
        <ul className="alias-list" aria-label="Direcciones de entrada">
          {aliases.map((alias) => (
            <li key={alias.id} className="alias-row">
              <span className="alias-email">{alias.alias_email}</span>
              <span className={`badge ${alias.active ? "ok" : "muted"}`}>
                {alias.active ? "Activo" : "Inactivo"}
              </span>
              <button
                type="button"
                className="button secondary small"
                onClick={() => onToggle(alias)}
              >
                {alias.active ? "Desactivar" : "Activar"}
              </button>
              <button
                type="button"
                className="button danger small"
                aria-label={`Borrar alias ${alias.alias_email}`}
                onClick={() => onDelete(alias)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="alias-add">
        <input
          type="email"
          placeholder="nuevo@alias.com"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-label="Nuevo alias de email"
        />
        <button
          type="button"
          className="button small"
          disabled={!draftValid || busy}
          onClick={onAdd}
        >
          + Añadir alias
        </button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
    </div>
  );
}
