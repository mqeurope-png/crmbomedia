"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
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

const roles: Role[] = ["admin", "manager", "user", "viewer"];

export default function AdminUsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [createPassword, setCreatePassword] = useState("");
  const [createConfirm, setCreateConfirm] = useState("");
  const [editPasswords, setEditPasswords] = useState<Record<string, string>>({});

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
        role: form.get("role"),
      });
      event.currentTarget.reset();
      setCreatePassword("");
      setCreateConfirm("");
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
    try {
      await updateUser(user.id, {
        full_name: data.get("full_name"),
        role: data.get("role"),
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
      <Link href="/" className="back-link">← Volver al dashboard</Link>
      <section className="hero compact">
        <p className="eyebrow">Administración</p>
        <h1>Usuarios y roles</h1>
      </section>
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
              <label>Rol<select name="role" defaultValue="viewer">{roles.map((role) => <option key={role} value={role}>{role}</option>)}</select></label>
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
                      <select name="role" defaultValue={user.role}>{roles.map((role) => <option key={role} value={role}>{role}</option>)}</select>
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
                      <button className="button secondary small" type="button" onClick={() => toggleActive(user)}>{user.is_active ? "Desactivar" : "Reactivar"}</button>
                    </form>
                    {draft.length > 0 ? <PasswordRequirements password={draft} /> : null}
                  </li>
                );
              })}
            </ul>
          </article>
        </section>
      ) : null}
    </main>
  );
}
