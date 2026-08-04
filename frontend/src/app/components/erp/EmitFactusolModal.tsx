"use client";

import { useEffect, useState } from "react";
import {
  getFactusolFormasPago,
  type EmitFactusolOptions,
  type FormaPago,
} from "../../lib/erpApi";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Modal de emisión de factura FACTUSOL (C-2-fix2): reproduce el diálogo
 *  «Nueva factura» del escritorio con 5 campos (Tipo, Serie, Fecha, Forma de
 *  pago, Observaciones). Las formas de pago se cargan de F_FOP; los valores
 *  reales del desplegable los confirma la validación de Bart. */
export function EmitFactusolModal({
  totalAmount,
  currency,
  onSubmit,
  onCancel,
  submitting,
}: {
  totalAmount: number;
  currency: string;
  onSubmit: (options: EmitFactusolOptions) => void;
  onCancel: () => void;
  submitting?: boolean;
}) {
  const [tipfac, setTipfac] = useState("1");
  const [serfac, setSerfac] = useState("");
  const [fecfac, setFecfac] = useState(today());
  const [fopfac, setFopfac] = useState("");
  const [comfac, setComfac] = useState("");
  const [formasPago, setFormasPago] = useState<FormaPago[]>([]);

  useEffect(() => {
    let alive = true;
    getFactusolFormasPago()
      .then((items) => { if (alive) setFormasPago(items); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, []);

  function submit() {
    onSubmit({
      tipfac: tipfac.trim() || "1",
      serfac: serfac.trim() || null,
      fecfac: fecfac || null,
      fopfac: fopfac || null,
      comfac: comfac.trim() || null,
    });
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label="Emitir factura FACTUSOL">
      <div className="modal-dialog">
        <h2>Emitir factura en FACTUSOL</h2>
        <p>
          Total: <strong>{totalAmount.toFixed(2)} {currency}</strong>
        </p>
        <p className="form-error">
          Se creará una factura <strong>real</strong> en FACTUSOL. Esta acción
          no es reversible desde el CRM.
        </p>

        <label className="field">
          <span>Tipo</span>
          <input
            type="text"
            value={tipfac}
            onChange={(e) => setTipfac(e.target.value)}
          />
        </label>
        <span className="muted small">1 = factura ordinaria (código FACTUSOL)</span>

        <label className="field">
          <span>Serie</span>
          <input
            type="text"
            value={serfac}
            placeholder="(sin serie)"
            onChange={(e) => setSerfac(e.target.value)}
          />
        </label>

        <label className="field">
          <span>Fecha de emisión</span>
          <input
            type="date"
            value={fecfac}
            onChange={(e) => setFecfac(e.target.value)}
          />
        </label>

        <label className="field">
          <span>Forma de pago</span>
          <select value={fopfac} onChange={(e) => setFopfac(e.target.value)}>
            <option value="">— Sin especificar —</option>
            {formasPago.map((f) => (
              <option key={f.codigo ?? f.nombre} value={f.codigo ?? ""}>
                {f.nombre}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Observaciones</span>
          <textarea
            value={comfac}
            rows={2}
            onChange={(e) => setComfac(e.target.value)}
          />
        </label>

        <div className="modal-actions">
          <button type="button" className="button secondary"
                  onClick={onCancel} disabled={submitting}>
            Cancelar
          </button>
          <button type="button" className="button"
                  onClick={submit} disabled={submitting}>
            {submitting ? "Generando…" : "Emitir factura"}
          </button>
        </div>
      </div>
    </div>
  );
}
