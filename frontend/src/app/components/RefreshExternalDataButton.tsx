"use client";

import { useCallback, useState } from "react";
import {
  refreshContactExternalData,
  type ExternalRefreshResult,
} from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  contactId: string;
  onDone?: (result: ExternalRefreshResult) => void;
  /** Render style: "primary" for the main CTA, "ghost" for the
   * background auto-refresh that just shows a tiny spinner. */
  variant?: "primary" | "ghost";
};

export function RefreshExternalDataButton({
  contactId,
  onDone,
  variant = "primary",
}: Props) {
  const [isPending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handle = useCallback(async () => {
    setPending(true);
    setError(null);
    try {
      const result = await refreshContactExternalData(contactId);
      onDone?.(result);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo actualizar."));
    } finally {
      setPending(false);
    }
  }, [contactId, onDone]);

  const label = isPending
    ? "Actualizando…"
    : variant === "ghost"
      ? "Actualizar"
      : "Actualizar desde AgileCRM";

  return (
    <div className="refresh-external-button">
      <button
        type="button"
        className={
          variant === "primary" ? "button" : "button secondary small"
        }
        onClick={handle}
        disabled={isPending}
      >
        {label}
      </button>
      {error ? <span className="muted small refresh-error">{error}</span> : null}
    </div>
  );
}
