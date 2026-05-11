"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { login, verifyTotp } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

const FLASH_MESSAGES: Record<string, string> = {
  "password-reset-success": "Contraseña actualizada. Inicia sesión con la nueva contraseña.",
};

type Stage = "password" | "totp";

export default function LoginPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("password");
  // The pre-2FA temp token never leaves component state. It is consumed
  // exactly once by /api/auth/2fa/verify and then discarded.
  const [tempToken, setTempToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const key = params.get("flash");
    if (key && FLASH_MESSAGES[key]) {
      setFlash(FLASH_MESSAGES[key]);
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  async function onPasswordSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    const form = new FormData(event.currentTarget);
    try {
      const result = await login(
        String(form.get("email")),
        String(form.get("password")),
      );
      if (result.requires_2fa) {
        setTempToken(result.access_token);
        setStage("totp");
      } else {
        router.push("/");
      }
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo iniciar sesión"));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function on2faSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!tempToken) return;
    setError(null);
    setIsSubmitting(true);
    try {
      await verifyTotp(tempToken, code.trim());
      router.push("/");
    } catch (err) {
      setError(extractErrorMessage(err, "Código incorrecto o expirado"));
    } finally {
      setIsSubmitting(false);
    }
  }

  function backToPassword() {
    setStage("password");
    setTempToken(null);
    setCode("");
    setError(null);
  }

  return (
    <main className="shell narrow">
      <section className="hero compact">
        <p className="eyebrow">Acceso CRM</p>
        <h1>{stage === "password" ? "Iniciar sesión" : "Verificación 2FA"}</h1>
        <p className="lead">
          {stage === "password"
            ? "Usa tu usuario interno para acceder al CRM MVP."
            : "Introduce el código de 6 dígitos de tu app de autenticación o un código de respaldo."}
        </p>
      </section>

      {stage === "password" ? (
        <form className="form-card" onSubmit={onPasswordSubmit}>
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
      ) : (
        <form className="form-card" onSubmit={on2faSubmit}>
          {error ? <div className="error-state">{error}</div> : null}
          <label>
            Código (6 dígitos) o código de respaldo
            <input
              name="code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
              required
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </label>
          <button className="button" type="submit" disabled={isSubmitting || !code.trim()}>
            {isSubmitting ? "Verificando..." : "Verificar"}
          </button>
          <button
            className="button secondary small"
            type="button"
            onClick={backToPassword}
          >
            ← Volver al login
          </button>
          <p className="muted">
            ¿Has perdido tu app y los códigos de respaldo? Pide al admin del sistema que
            ejecute <code>scripts/reset-user-2fa.py</code> en el VPS.
          </p>
        </form>
      )}
    </main>
  );
}
