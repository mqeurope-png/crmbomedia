"use client";

import Link from "next/link";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useState } from "react";
import {
  confirmTotp,
  disableTotp,
  getCurrentUser,
  setupTotp,
  type User,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

// The page has three modes:
//   * "view"     — show current 2FA state and the activate / disable buttons.
//   * "setup"    — secret + QR + 6-digit input + Confirm.
//   * "codes"    — show backup codes once, with the "I saved them" button.
// Mode transitions are driven by user actions; the underlying user state is
// re-fetched after each successful mutation to keep the badge accurate.
type Mode = "view" | "setup" | "codes";

export default function SecurityPage() {
  const [user, setUser] = useState<User | null>(null);
  const [mode, setMode] = useState<Mode>("view");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // setup state
  const [secret, setSecret] = useState("");
  const [otpauthUri, setOtpauthUri] = useState("");
  const [confirmCode, setConfirmCode] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // codes state
  const [backupCodes, setBackupCodes] = useState<string[]>([]);

  // disable state
  const [showDisable, setShowDisable] = useState(false);
  const [disablePassword, setDisablePassword] = useState("");

  async function refreshUser() {
    const me = await getCurrentUser();
    setUser(me);
  }

  useEffect(() => {
    refreshUser().catch((err) =>
      setError(extractErrorMessage(err, "No se pudo cargar el usuario actual")),
    );
  }, []);

  async function onStartSetup() {
    setError(null);
    setMessage(null);
    try {
      const response = await setupTotp();
      setSecret(response.secret);
      setOtpauthUri(response.otpauth_uri);
      setConfirmCode("");
      setMode("setup");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo iniciar la configuración de 2FA"));
    }
  }

  async function onConfirm(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const response = await confirmTotp(confirmCode.trim());
      setBackupCodes(response.backup_codes);
      setMode("codes");
      await refreshUser();
    } catch (err) {
      setError(extractErrorMessage(err, "El código no es válido"));
    } finally {
      setIsSubmitting(false);
    }
  }

  function onFinishedSavingCodes() {
    setBackupCodes([]);
    setMode("view");
    setMessage("2FA activado correctamente.");
  }

  async function onDisable(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await disableTotp(disablePassword);
      setDisablePassword("");
      setShowDisable(false);
      setMessage("2FA desactivado.");
      await refreshUser();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo desactivar 2FA"));
    } finally {
      setIsSubmitting(false);
    }
  }

  if (!user && !error) {
    return (
      <main className="shell narrow">
        <p className="muted">Cargando...</p>
      </main>
    );
  }

  return (
    <main className="shell narrow">
      <Link href="/" className="back-link">← Volver al dashboard</Link>
      <section className="hero compact">
        <p className="eyebrow">Cuenta</p>
        <h1>Seguridad</h1>
      </section>

      {error ? <div className="error-state">{error}</div> : null}
      {message ? <div className="success-state">{message}</div> : null}

      {mode === "view" && user ? (
        <article className="form-card">
          <h2>Autenticación de doble factor (2FA)</h2>
          <p>
            Estado:{" "}
            {user.totp_enabled ? (
              <strong className="badge ok">Activada</strong>
            ) : (
              <strong className="badge miss">Desactivada</strong>
            )}
          </p>
          {user.requires_2fa_setup ? (
            <p className="muted">
              Tu cuenta tiene rol <strong>admin</strong>: 2FA es obligatorio para
              acceder a la gestión de usuarios, auditoría e integraciones.
            </p>
          ) : null}
          {!user.totp_enabled ? (
            <button className="button" type="button" onClick={onStartSetup}>
              Activar 2FA
            </button>
          ) : (
            <>
              {!showDisable ? (
                <button
                  className="button secondary"
                  type="button"
                  onClick={() => setShowDisable(true)}
                >
                  Desactivar 2FA
                </button>
              ) : (
                <form onSubmit={onDisable}>
                  <p className="muted">
                    Confirma con tu contraseña actual. Tras desactivar perderás los códigos
                    de respaldo.
                  </p>
                  <label>
                    Contraseña actual
                    <input
                      type="password"
                      autoComplete="current-password"
                      required
                      value={disablePassword}
                      onChange={(event) => setDisablePassword(event.target.value)}
                    />
                  </label>
                  <div className="actions">
                    <button
                      className="button danger"
                      type="submit"
                      disabled={isSubmitting || !disablePassword}
                    >
                      {isSubmitting ? "Desactivando..." : "Confirmar desactivación"}
                    </button>
                    <button
                      className="button secondary"
                      type="button"
                      onClick={() => {
                        setShowDisable(false);
                        setDisablePassword("");
                      }}
                    >
                      Cancelar
                    </button>
                  </div>
                </form>
              )}
            </>
          )}
        </article>
      ) : null}

      {mode === "setup" ? (
        <article className="form-card">
          <h2>Configurar 2FA</h2>
          <p>
            Escanea el código QR con Google Authenticator, Authy, 1Password o cualquier
            app TOTP. Si no puedes escanear, introduce el secreto manualmente.
          </p>
          <div className="totp-qr">
            <QRCodeSVG value={otpauthUri} size={196} includeMargin />
          </div>
          <label>
            Secreto (para entrada manual)
            <input type="text" readOnly value={secret} aria-label="TOTP secret" />
          </label>
          <form onSubmit={onConfirm}>
            <label>
              Introduce el código de 6 dígitos que muestra la app
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
                required
                pattern="\d{6}"
                value={confirmCode}
                onChange={(event) => setConfirmCode(event.target.value)}
              />
            </label>
            <div className="actions">
              <button
                className="button"
                type="submit"
                disabled={isSubmitting || confirmCode.length !== 6}
              >
                {isSubmitting ? "Verificando..." : "Confirmar y activar"}
              </button>
              <button
                className="button secondary"
                type="button"
                onClick={() => setMode("view")}
              >
                Cancelar
              </button>
            </div>
          </form>
        </article>
      ) : null}

      {mode === "codes" ? (
        <article className="form-card">
          <h2>Códigos de respaldo</h2>
          <p>
            Guarda estos códigos en un sitio seguro (gestor de contraseñas).{" "}
            <strong>No volverán a mostrarse.</strong> Cada código sirve una sola vez para
            iniciar sesión si pierdes acceso a tu app TOTP.
          </p>
          <ul className="backup-codes">
            {backupCodes.map((code) => (
              <li key={code}>
                <code>{code}</code>
              </li>
            ))}
          </ul>
          <button className="button" type="button" onClick={onFinishedSavingCodes}>
            He guardado mis códigos de respaldo
          </button>
        </article>
      ) : null}
    </main>
  );
}
