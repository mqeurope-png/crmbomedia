"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
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

const roles: Role[] = ["admin", "manager", "user", "viewer"];

export default function AdminUsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  async function loadUsers() {
    const [currentUser, userList] = await Promise.all([getCurrentUser(), getUsers()]);
    if (currentUser.role !== "admin") {
      throw new Error("No tienes permisos de administrador");
    }
    setUsers(userList);
  }

  useEffect(() => {
    loadUsers()
      .catch((err) => setError(err instanceof Error ? err.message : "No se pudieron cargar usuarios"))
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
      setMessage("Usuario creado");
      await loadUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el usuario");
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
      await loadUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo actualizar el usuario");
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
      setError(err instanceof Error ? err.message : "No se pudo cambiar el estado del usuario");
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
              <label>Contraseña<input name="password" type="password" required minLength={8} /></label>
              <label>Rol<select name="role" defaultValue="viewer">{roles.map((role) => <option key={role} value={role}>{role}</option>)}</select></label>
              <button className="button" type="submit">Crear</button>
            </form>
          </article>
          <article className="card wide-card">
            <h2>Usuarios existentes</h2>
            <ul className="item-list">
              {users.map((user) => (
                <li key={user.id}>
                  <form className="user-edit-row" onSubmit={(event) => { event.preventDefault(); saveUser(user, event.currentTarget); }}>
                    <strong>{user.email}</strong>
                    <input name="full_name" defaultValue={user.full_name} required />
                    <select name="role" defaultValue={user.role}>{roles.map((role) => <option key={role} value={role}>{role}</option>)}</select>
                    <select name="is_active" defaultValue={String(user.is_active)}><option value="true">Activo</option><option value="false">Inactivo</option></select>
                    <input name="new_password" type="password" placeholder="Nueva contraseña opcional" minLength={8} />
                    <button className="button secondary small" type="submit">Guardar</button>
                    <button className="button secondary small" type="button" onClick={() => toggleActive(user)}>{user.is_active ? "Desactivar" : "Reactivar"}</button>
                  </form>
                </li>
              ))}
            </ul>
          </article>
        </section>
      ) : null}
    </main>
  );
}
