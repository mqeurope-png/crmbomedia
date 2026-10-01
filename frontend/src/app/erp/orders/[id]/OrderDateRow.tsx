"use client";

import { useState } from "react";
import { changeOrderDate } from "../../../lib/erpApi";
import { extractErrorMessage } from "../../../lib/errors";

/** Día (AAAA-MM-DD) de un ISO, sin zona horaria: el mismo que guarda BoHub. */
function dia(iso: string | null | undefined): string {
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(iso ?? "");
  return m ? m[1] : "";
}

function dmy(d: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
}

/** «Fecha del pedido» en la ficha, con «Cambiar fecha» en los pedidos que no
 *  vienen de la tienda (manuales, muestras, creados desde un documento de
 *  FACTUSOL). En un pedido web la fecha es la de la tienda: el botón queda
 *  desactivado y el porqué va en el tooltip. La fecha nueva vale para todo lo
 *  que usa la fecha del pedido; no toca FACTUSOL. */
export function OrderDateRow({
  orderId,
  placedAt,
  createdAt,
  isWeb,
  canEdit,
  onSaved,
}: {
  orderId: string;
  placedAt: string | null;
  createdAt?: string | null;
  isWeb: boolean;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const actual = dia(placedAt) || dia(createdAt);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(actual);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function guardar() {
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      await changeOrderDate(orderId, value);
      setEditing(false);
      onSaved();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cambiar la fecha del pedido."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="erp-flow-kv">
      <span className="k">Fecha del pedido</span>
      <div className="v erp-flow-kv-actions">
        {editing ? (
          <>
            <input
              type="date" aria-label="Fecha del pedido" value={value} disabled={busy}
              onChange={(e) => setValue(e.target.value)}
            />
            <button type="button" className="button small" disabled={busy || !value}
                    onClick={() => void guardar()}>
              {busy ? "Guardando…" : "Guardar"}
            </button>
            <button type="button" className="button small secondary" disabled={busy}
                    onClick={() => { setEditing(false); setValue(actual); setError(null); }}>
              Cancelar
            </button>
          </>
        ) : (
          <>
            <span>{actual ? dmy(actual) : "—"}</span>
            {canEdit ? (
              <button
                type="button" className="button small secondary"
                disabled={isWeb}
                title={isWeb
                  ? "La fecha de un pedido web es la de la tienda: no se puede cambiar."
                  : "Pon la fecha real del pedido (p. ej. la del documento de FACTUSOL). No cambia las fechas de factura, cobro ni envío."}
                onClick={() => { setValue(actual); setEditing(true); }}
              >
                Cambiar fecha
              </button>
            ) : null}
          </>
        )}
        {error ? <p className="form-error small" role="alert">{error}</p> : null}
      </div>
    </div>
  );
}
