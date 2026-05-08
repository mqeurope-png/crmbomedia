"use client";

import Link from "next/link";
import { useState } from "react";
import { changePassword } from "../../lib/api";

export default function ChangePasswordPage() {
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      await changePassword(String(form.get("current_password")), String(form.get("new_password")));
      setMessage("Contraseña actualizada");
      event.currentTarget.reset();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar la contraseña");
    }
  }

  return (
    <main className="shell narrow">
      <Link href="/" className="back-link">← Volver al dashboard</Link>
      <section className="hero compact"><p className="eyebrow">Cuenta</p><h1>Cambiar contraseña</h1></section>
      <form className="form-card" onSubmit={onSubmit}>
        {error ? <div className="error-state">{error}</div> : null}
        {message ? <div className="success-state">{message}</div> : null}
        <label>Contraseña actual<input name="current_password" type="password" required /></label>
        <label>Nueva contraseña<input name="new_password" type="password" required minLength={8} /></label>
        <button className="button" type="submit">Cambiar contraseña</button>
      </form>
    </main>
  );
}
