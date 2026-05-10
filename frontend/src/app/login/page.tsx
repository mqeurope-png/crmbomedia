"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { login } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

const FLASH_MESSAGES: Record<string, string> = {
  "password-reset-success": "Contraseña actualizada. Inicia sesión con la nueva contraseña.",
};

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // One-shot flash messages handed over by other pages via ?flash=<key>.
  // Read once on mount and strip the query param so a refresh does not
  // re-show the banner.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const key = params.get("flash");
    if (key && FLASH_MESSAGES[key]) {
      setFlash(FLASH_MESSAGES[key]);
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

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
        {flash ? <div className="success-state">{flash}</div> : null}
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
