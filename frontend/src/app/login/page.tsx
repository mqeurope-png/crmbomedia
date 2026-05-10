"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { login } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    const form = new FormData(event.currentTarget);
    try {
      await login(String(form.get("email")), String(form.get("password")));
      router.push("/");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo iniciar sesión"));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="shell narrow">
      <section className="hero compact">
        <p className="eyebrow">Acceso CRM</p>
        <h1>Iniciar sesión</h1>
        <p className="lead">Usa tu usuario interno para acceder al CRM MVP.</p>
      </section>
      <form className="form-card" onSubmit={onSubmit}>
        {error ? <div className="error-state">{error}</div> : null}
        <label>
          Email
          <input name="email" type="email" required autoComplete="email" />
        </label>
        <label>
          Contraseña
          <input name="password" type="password" required autoComplete="current-password" />
        </label>
        <button className="button" type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Entrando..." : "Entrar"}
        </button>
        <p className="muted"><a href="/password-reset">¿Has olvidado la contraseña?</a></p>
      </form>
    </main>
  );
}
