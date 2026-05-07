"use client";

import Link from "next/link";
import { useState } from "react";
import { confirmPasswordReset, requestPasswordReset } from "../lib/api";

export default function PasswordResetPage() {
  const [token, setToken] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onRequest(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      const response = await requestPasswordReset(String(form.get("email")));
      setMessage(response.message);
      if (response.reset_token) setToken(response.reset_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo solicitar reset");
    }
  }

  async function onConfirm(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      await confirmPasswordReset(String(form.get("token")), String(form.get("new_password")));
      setMessage("Contraseña restablecida. Ya puedes iniciar sesión.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo restablecer la contraseña");
    }
  }

  return (
    <main className="shell narrow">
      <Link href="/login" className="back-link">← Volver al login</Link>
      <section className="hero compact"><p className="eyebrow">Recuperación</p><h1>Restablecer contraseña</h1></section>
      {error ? <div className="error-state">{error}</div> : null}
      {message ? <div className="success-state">{message}</div> : null}
      <form className="form-card" onSubmit={onRequest}>
        <label>Email<input name="email" type="email" required /></label>
        <button className="button" type="submit">Solicitar token</button>
      </form>
      <form className="form-card" onSubmit={onConfirm}>
        <label>Token<input name="token" value={token} onChange={(event) => setToken(event.target.value)} required /></label>
        <label>Nueva contraseña<input name="new_password" type="password" minLength={8} required /></label>
        <button className="button" type="submit">Restablecer</button>
      </form>
    </main>
  );
}
