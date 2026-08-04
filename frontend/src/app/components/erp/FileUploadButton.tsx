"use client";

import { useRef, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";

/** Botón reutilizable de subida (PDF/imagen). Abre un input file oculto,
 *  valida el tipo con `accept` y delega el fichero elegido a `onFile`. */
export function FileUploadButton({
  label,
  accept = "application/pdf,image/png,image/jpeg",
  onFile,
  disabled,
  className = "button small secondary",
}: {
  label: string;
  accept?: string;
  onFile: (file: File) => Promise<void> | void;
  disabled?: boolean;
  className?: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handle(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // permite reintentar el mismo fichero
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await onFile(file);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo subir el archivo."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={className}
        disabled={disabled || busy}
        onClick={() => inputRef.current?.click()}
      >
        {busy ? "Subiendo…" : label}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        hidden
        aria-label={label}
        onChange={handle}
      />
      {error ? <p className="form-error small" role="status">{error}</p> : null}
    </>
  );
}
