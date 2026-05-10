"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  isPasswordCompliant,
  PasswordRequirements,
  PASSWORD_MIN_LENGTH,
} from "../components/PasswordRequirements";
import { confirmPasswordReset, requestPasswordReset } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

export default function PasswordResetPage() {
  const [token, setToken] = useState("");

  // Auto-fill the token when the user clicks the email link
  // (https://crm.tudominio.com/password-reset?token=...). Reading
  // window.location.search inside an effect keeps this client-only and
  // sidesteps the Next.js useSearchParams Suspense requirement.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const fromUrl = new URLSearchParams(window.location.search).get("token");
    if (fromUrl) setToken(fromUrl);
  }, []);

  // newPassword drives PasswordRequirements; the requirements list reacts to
  // each keystroke on "Nueva contraseña", not on the confirm box.
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const compliant = isPasswordCompliant(newPassword);
  const showMatch = confirmPassword.length > 0;
  const matches = newPassword === confirmPassword;
  const canSubmit = compliant && matches;

  async function onRequest(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      const response = await requestPasswordReset(String(form.get("email")));
      setMessage(response.message);
      // In production the API never returns the token; the user gets the link
      // by email. In dev/test the token is included and we autofill the form
      // so Codespaces and the test suite can complete the flow.
      if (response.reset_token) setToken(response.reset_token);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo solicitar reset"));
    }
  }

  async function onConfirm(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      await confirmPasswordReset(String(form.get("token")), String(form.get("new_password")));
      setMessage("Contraseña restablecida. Ya puedes iniciar sesión.");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo restablecer la contraseña"));
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
        <button className="button" type="submit">Solicitar enlace de recuperación</button>
        <p className="muted">
          Si la cuenta existe recibirás un enlace por email. En entornos de desarrollo el token
          aparece directamente en la respuesta para facilitar las pruebas.
        </p>
      </form>
      <form className="form-card" onSubmit={onConfirm}>
        <label>Token<input name="token" value={token} onChange={(event) => setToken(event.target.value)} required /></label>
        <label>
          Nueva contraseña
          <input
            name="new_password"
            type="password"
            minLength={PASSWORD_MIN_LENGTH}
            required
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            autoComplete="new-password"
          />
        </label>
        <PasswordRequirements password={newPassword} />
        <label>
          Confirmar nueva contraseña
          <input
            name="confirm_password"
            type="password"
            required
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            autoComplete="new-password"
          />
        </label>
        {showMatch ? (
          <p className={`password-match ${matches ? "ok" : "miss"}`}>
            <span aria-hidden="true">{matches ? "✓" : "✗"}</span>
            {matches ? " Las contraseñas coinciden" : " Las contraseñas no coinciden"}
          </p>
        ) : null}
        <button className="button" type="submit" disabled={!canSubmit}>
          Restablecer
        </button>
      </form>
    </main>
  );
}
