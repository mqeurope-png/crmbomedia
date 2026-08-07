"use client";

import { useState } from "react";
import { Modal } from "./Modal";
import { adminResetUserPassword } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

/**
 * CRM-PERFIL — modal de reset de contraseña por admin. Confirma, genera una
 * contraseña aleatoria en el backend y la muestra UNA sola vez con botón de
 * copiar. Al cerrar, la contraseña se pierde (hay que resetear de nuevo).
 */
export function ResetPasswordModal({
  open,
  onClose,
  userId,
  userEmail,
}: {
  open: boolean;
  onClose: () => void;
  userId: string;
  userEmail: string;
}) {
  const [phase, setPhase] = useState<"confirm" | "done">("confirm");
  const [password, setPassword] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleClose() {
    // Al cerrar, olvidamos la contraseña generada.
    setPhase("confirm");
    setPassword(null);
    setCopied(false);
    setError(null);
    onClose();
  }

  async function handleReset() {
    setBusy(true);
    setError(null);
    try {
      const res = await adminResetUserPassword(userId);
      setPassword(res.password);
      setPhase("done");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo resetear la contraseña."));
    } finally {
      setBusy(false);
    }
  }

  async function handleCopy() {
    if (!password) return;
    try {
      await navigator.clipboard.writeText(password);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Modal open={open} onClose={handleClose} title="Resetear contraseña" size="small">
      {phase === "confirm" ? (
        <div className="reset-pw-confirm">
          <p>
            Se generará una contraseña nueva para{" "}
            <strong>{userEmail}</strong>. La contraseña actual dejará de
            funcionar de inmediato.
          </p>
          {error ? <p className="form-error">{error}</p> : null}
          <div className="modal-actions">
            <button
              type="button"
              className="button secondary small"
              onClick={handleClose}
              disabled={busy}
            >
              Cancelar
            </button>
            <button
              type="button"
              className="button danger small"
              onClick={handleReset}
              disabled={busy}
            >
              {busy ? "Reseteando…" : "Resetear contraseña"}
            </button>
          </div>
        </div>
      ) : (
        <div className="reset-pw-done">
          <p className="form-success">
            Contraseña reseteada para <strong>{userEmail}</strong>.
          </p>
          <p className="muted small">
            ⚠️ Cópiala ahora y comunícasela al usuario:{" "}
            <strong>no se volverá a mostrar</strong>. Si la pierdes, tendrás
            que resetear de nuevo.
          </p>
          <div className="reset-pw-value">
            <code data-testid="reset-pw-value">{password}</code>
            <button
              type="button"
              className="button small"
              onClick={handleCopy}
            >
              {copied ? "¡Copiada!" : "Copiar"}
            </button>
          </div>
          <div className="modal-actions">
            <button
              type="button"
              className="button small"
              onClick={handleClose}
            >
              Hecho
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
