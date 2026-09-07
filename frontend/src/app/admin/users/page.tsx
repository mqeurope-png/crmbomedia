"use client";

import { useEffect, useState } from "react";
import { AliasManager } from "../../components/AliasManager";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { ResetPasswordModal } from "../../components/ResetPasswordModal";
import { RoleSelect } from "../../components/RoleSelect";
import { scopeChangeWarning } from "../../lib/roles";
import {
  isPasswordCompliant,
  PasswordRequirements,
  PASSWORD_MIN_LENGTH,
} from "../../components/PasswordRequirements";
import {
  adminUpdateUserPassword,
  createUser,
  deactivateUser,
  getCurrentUser,
  getUsers,
  reactivateUser,
  updateUser,
  type Role,
  type User,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

export default function AdminUsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [createPassword, setCreatePassword] = useState("");
  const [createConfirm, setCreateConfirm] = useState("");
  const [createRole, setCreateRole] = useState<Role>("viewer");
  // ERP-F2-fix1 — rol elegido por fila de edición (controlado, para leer el
  // aviso de cambio de ámbito y decidir el rol al guardar).
  const [editRoles, setEditRoles] = useState<Record<string, Role>>({});
  const [editPasswords, setEditPasswords] = useState<Record<string, string>>({});
  const [resetUser, setResetUser] = useState<User | null>(null);

  const createCompliant = isPasswordCompliant(createPassword);
  const createMatchesShow = createConfirm.length > 0;
  const createMatches = createPassword === createConfirm;
  const canCreate = createCompliant && createMatches;

  async function loadUsers() {
    const [currentUser, userList] = await Promise.all([getCurrentUser(), getUsers()]);
    if (currentUser.role !== "admin") {
      throw new Error("No tienes permisos de administrador");
    }
    setUsers(userList);
  }

  useEffect(() => {
    loadUsers()
      .catch((err) => setError(extractErrorMessage(err, "No se pudieron cargar usuarios")))
      .finally(() => setIsLoading(false));
  }, []);

  async function onCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    const form = new FormData(event.currentTarget);
    try {
      await createUser({
        email: form.get("email"),
        full_name: form.get("full_name"),
        password: form.get("password"),
        role: createRole,
      });
      event.currentTarget.reset();
      setCreatePassword("");
      setCreateConfirm("");
      setCreateRole("viewer");
      setMessage("Usuario creado");
      await loadUsers();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el usuario"));
    }
  }

  async function saveUser(user: User, form: HTMLFormElement) {
    setError(null);
    setMessage(null);
    const data = new FormData(form);
    const nextRole = editRoles[user.id] ?? user.role;
    // ERP-F2-fix1 — el cambio de ámbito (CRM↔ERP) tiene consecuencias: se
    // confirma antes de guardar, no es un ajuste menor.
    const warning = scopeChangeWarning(user.role, nextRole);
    if (warning && !window.confirm(`${warning}\n\n¿Guardar el cambio?`)) {
      return;
    }
    try {
      await updateUser(user.id, {
        full_name: data.get("full_name"),
        role: nextRole,
        is_active: data.get("is_active") === "true",
      });
      const password = String(data.get("new_password") ?? "");
      if (password) {
        await adminUpdateUserPassword(user.id, password);
      }
      setMessage("Usuario actualizado");
      setEditPasswords((prev) => ({ ...prev, [user.id]: "" }));
      await loadUsers();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo actualizar el usuario"));
    }
  }

  async function toggleActive(user: User) {
    setError(null);
    try {
      if (user.is_active) {
        await deactivateUser(user.id);
      } else {
        await reactivateUser(user.id);
      }
      await loadUsers();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cambiar el estado del usuario"));
    }
  }

  return (
    <main className="shell">
      <PageHeader title="Usuarios y roles" eyebrow="Administración" />
      {isLoading ? <p className="muted">Cargando usuarios...</p> : null}
      {error ? <ErrorState title="Error de permisos o carga" message={error} /> : null}
      {message ? <div className="success-state">{message}</div> : null}
      {!error ? (
        <section className="grid two">
          <article className="card">
            <h2>Crear usuario</h2>
            <form className="form-card embedded" onSubmit={onCreate}>
              <label>Email<input name="email" type="email" required /></label>
              <label>Nombre<input name="full_name" required /></label>
              <label>
                Contraseña
                <input
                  name="password"
                  type="password"
                  required
                  minLength={PASSWORD_MIN_LENGTH}
                  value={createPassword}
                  onChange={(event) => setCreatePassword(event.target.value)}
                  autoComplete="new-password"
                />
              </label>
              <PasswordRequirements password={createPassword} />
              <label>
                Confirmar contraseña
                <input
                  name="confirm_password"
                  type="password"
                  required
                  value={createConfirm}
                  onChange={(event) => setCreateConfirm(event.target.value)}
                  autoComplete="new-password"
                />
              </label>
              {createMatchesShow ? (
                <p className={`password-match ${createMatches ? "ok" : "miss"}`}>
                  <span aria-hidden="true">{createMatches ? "✓" : "✗"}</span>
                  {createMatches ? " Las contraseñas coinciden" : " Las contraseñas no coinciden"}
                </p>
              ) : null}
              <label>Rol
                <RoleSelect name="role" value={createRole} onChange={setCreateRole} />
              </label>
              <button className="button" type="submit" disabled={!canCreate}>
                Crear
              </button>
            </form>
          </article>
          <article className="card wide-card">
            <h2>Usuarios existentes</h2>
            <ul className="item-list">
              {users.map((user) => {
                const draft = editPasswords[user.id] ?? "";
                return (
                  <li key={user.id}>
                    <form className="user-edit-row" onSubmit={(event) => { event.preventDefault(); saveUser(user, event.currentTarget); }}>
                      <strong>{user.email}</strong>
                      <input name="full_name" defaultValue={user.full_name} required />
                      <RoleSelect
                        name="role"
                        value={editRoles[user.id] ?? user.role}
                        originalRole={user.role}
                        onChange={(role) =>
                          setEditRoles((prev) => ({ ...prev, [user.id]: role }))
                        }
                      />
                      <select name="is_active" defaultValue={String(user.is_active)}><option value="true">Activo</option><option value="false">Inactivo</option></select>
                      <input
                        name="new_password"
                        type="password"
                        placeholder="Nueva contraseña opcional"
                        minLength={PASSWORD_MIN_LENGTH}
                        value={draft}
                        onChange={(event) =>
                          setEditPasswords((prev) => ({ ...prev, [user.id]: event.target.value }))
                        }
                        autoComplete="new-password"
                      />
                      <button
                        className="button secondary small"
                        type="submit"
                        disabled={draft.length > 0 && !isPasswordCompliant(draft)}
                      >
                        Guardar
                      </button>
                      <button className="button secondary small" type="button" onClick={() => setResetUser(user)}>Resetear contraseña</button>
                      <button className="button secondary small" type="button" onClick={() => toggleActive(user)}>{user.is_active ? "Desactivar" : "Reactivar"}</button>
                    </form>
                    {draft.length > 0 ? <PasswordRequirements password={draft} /> : null}
                    <AliasManager userId={user.id} />
                  </li>
                );
              })}
            </ul>
          </article>
        </section>
      ) : null}
      <ResetPasswordModal
        open={!!resetUser}
        onClose={() => setResetUser(null)}
        userId={resetUser?.id ?? ""}
        userEmail={resetUser?.email ?? ""}
      />
    </main>
  );
}
