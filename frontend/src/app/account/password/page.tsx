"use client";

import Link from "next/link";
import { useState } from "react";
import {
  isPasswordCompliant,
  PasswordRequirements,
  PASSWORD_MIN_LENGTH,
} from "../../components/PasswordRequirements";
import { changePassword } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

export default function ChangePasswordPage() {
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // newPassword drives PasswordRequirements; confirmPassword only feeds the
  // matches check. Wiring requirements to the first field makes the live
  // checklist react to every keystroke on "Nueva contraseña" instead of the
  // confirm box.
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const compliant = isPasswordCompliant(newPassword);
  const showMatch = confirmPassword.length > 0;
  const matches = newPassword === confirmPassword;
  const canSubmit = compliant && matches;

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      await changePassword(String(form.get("current_password")), String(form.get("new_password")));
      setMessage("Contraseña actualizada");
      event.currentTarget.reset();
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cambiar la contraseña"));
    }
  }

  return (
    <main className="shell narrow">
      <Link href="/" className="back-link">← Volver al dashboard</Link>
      <section className="hero compact"><p className="eyebrow">Cuenta</p><h1>Cambiar contraseña</h1></section>
      <form className="form-card" onSubmit={onSubmit}>
        {error ? <div className="error-state">{error}</div> : null}
        {message ? <div className="success-state">{message}</div> : null}
        <label>Contraseña actual<input name="current_password" type="password" required autoComplete="current-password" /></label>
        <label>
          Nueva contraseña
          <input
            name="new_password"
            type="password"
            required
            minLength={PASSWORD_MIN_LENGTH}
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
          Cambiar contraseña
        </button>
      </form>
    </main>
  );
}
