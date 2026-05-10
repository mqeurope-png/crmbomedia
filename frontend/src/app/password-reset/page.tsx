"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  isPasswordCompliant,
  PasswordRequirements,
  PASSWORD_MIN_LENGTH,
} from "../components/PasswordRequirements";
import { confirmPasswordReset, requestPasswordReset } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

// The page renders one of two mutually-exclusive flows depending on whether
// a reset token is present in the URL:
//   - "request": the user clicked "I forgot my password" and lands on
//     /password-reset (no query). We show the email-only form that triggers
//     an email with a recovery link.
//   - "confirm": the user clicked the link in that email and lands on
//     /password-reset?token=... We show the new-password form. The token
//     itself is read once from the URL and kept only in component state —
//     it is never rendered as an editable input or echoed back to the user.
type Mode = "loading" | "request" | "confirm";

export default function PasswordResetPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("loading");
  const [token, setToken] = useState("");

  useEffect(() => {
    if (typeof window === "undefined") return;
    const fromUrl = new URLSearchParams(window.location.search).get("token") ?? "";
    if (fromUrl.trim()) {
      setToken(fromUrl);
      setMode("confirm");
    } else {
      setMode("request");
    }
  }, []);

  if (mode === "loading") {
    return (
      <main className="shell narrow">
        <section className="hero compact">
          <p className="eyebrow">Recuperación</p>
          <h1>Restablecer contraseña</h1>
        </section>
        <p className="muted">Cargando...</p>
      </main>
    );
  }

  return mode === "confirm" ? (
    <ResetConfirmPanel token={token} onSuccess={() => router.push("/login?flash=password-reset-success")} />
  ) : (
    <ResetRequestPanel />
  );
}

function ResetRequestPanel() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await requestPasswordReset(email);
      setSubmitted(true);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo solicitar el enlace de recuperación"));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="shell narrow">
      <Link href="/login" className="back-link">← Volver al login</Link>
      <section className="hero compact">
        <p className="eyebrow">Recuperación</p>
        <h1>¿Olvidaste tu contraseña?</h1>
      </section>
      {error ? <div className="error-state">{error}</div> : null}
      {submitted ? (
        <div className="form-card">
          <div className="success-state">
            Si la cuenta existe, hemos enviado un enlace de recuperación al email indicado.
            Revisa la bandeja de entrada (y la carpeta de spam) y pulsa el enlace para
            establecer una contraseña nueva.
          </div>
          <p className="muted">
            <Link href="/login">Volver al login</Link>
          </p>
        </div>
      ) : (
        <form className="form-card" onSubmit={onSubmit}>
          <p className="muted">
            Introduce el email asociado a tu cuenta y te enviaremos un enlace para
            restablecer la contraseña.
          </p>
          <label>
            Email
            <input
              name="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <button className="button" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Enviando..." : "Solicitar enlace de recuperación"}
          </button>
        </form>
      )}
    </main>
  );
}

function ResetConfirmPanel({ token, onSuccess }: { token: string; onSuccess: () => void }) {
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const compliant = isPasswordCompliant(newPassword);
  const showMatch = confirmPassword.length > 0;
  const matches = newPassword === confirmPassword;
  const canSubmit = compliant && matches && !isSubmitting;

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await confirmPasswordReset(token, newPassword);
      onSuccess();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo restablecer la contraseña"));
    } finally {
      setIsSubmitting(false);
    }
  }

  // Heuristic: if the backend rejects the token (invalid / expired / already
  // used), surface a "request a new link" call to action so the user is not
  // stuck. We keep it heuristic because the backend currently returns 401
  // with the same "Invalid reset token" message for all token failures.
  const looksLikeTokenError = error
    ? /token|invalid|caducad|expir/i.test(error)
    : false;

  return (
    <main className="shell narrow">
      <Link href="/login" className="back-link">← Volver al login</Link>
      <section className="hero compact">
        <p className="eyebrow">Recuperación</p>
        <h1>Establecer nueva contraseña</h1>
      </section>
      {error ? (
        <div className="error-state">
          <p style={{ margin: 0 }}>{error}</p>
          {looksLikeTokenError ? (
            <p style={{ marginTop: 8, marginBottom: 0 }}>
              <Link href="/password-reset">Solicitar un nuevo enlace</Link>
            </p>
          ) : null}
        </div>
      ) : null}
      <form className="form-card" onSubmit={onSubmit}>
        <p className="muted">
          Elige una contraseña nueva. El enlace de recuperación es válido una sola vez.
        </p>
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
          {isSubmitting ? "Restableciendo..." : "Restablecer contraseña"}
        </button>
      </form>
    </main>
  );
}
