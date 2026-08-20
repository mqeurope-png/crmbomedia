"use client";

import { useEffect, useState } from "react";
import {
  getFactusolFormasPago,
  getFactusolSeries,
  type EmitFactusolOptions,
  type FactusolSerie,
  type FormaPago,
} from "../../lib/erpApi";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Modal de emisión de factura FACTUSOL (C-2-fix2): reproduce el diálogo
 *  «Nueva factura» del escritorio con 5 campos (Tipo, Serie, Fecha, Forma de
 *  pago, Observaciones). Las formas de pago se cargan de F_FOP; los valores
 *  reales del desplegable los confirma la validación de Bart.
 *
 *  ERP-E2: la «Serie» pasa de campo de texto libre a desplegable de EMPRESA
 *  EMISORA. En FACTUSOL la serie no es un dato de la factura: identifica la
 *  empresa y va codificada en el rango del número (serie 5 ⇒ 5xxxxx). Por eso
 *  escribirla como columna `SERFAC` rompía todas las emisiones. */
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
  const [serie, setSerie] = useState<number | null>(null);
  const [fecfac, setFecfac] = useState(today());
  const [fopfac, setFopfac] = useState("");
  const [comfac, setComfac] = useState("");
  const [formasPago, setFormasPago] = useState<FormaPago[]>([]);
  const [series, setSeries] = useState<FactusolSerie[]>([]);

  useEffect(() => {
    let alive = true;
    getFactusolFormasPago()
      .then((items) => { if (alive) setFormasPago(items); })
      .catch(() => undefined);
    getFactusolSeries()
      .then((r) => {
        if (!alive) return;
        // Las series con nombre (las empresas de Bart) primero; el resto
        // detrás, disponibles pero sin estorbar.
        setSeries(
          [...r.items].sort(
            (a, b) => Number(b.is_known) - Number(a.is_known) || a.serie - b.serie,
          ),
        );
        // ERP-E2-fix1: NO se preselecciona ninguna. El default es «heredar la
        // del pedido»: preseleccionar una la forzaría siempre, que es
        // exactamente lo que facturó un pedido de Streamtec como Bomedia.
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, []);

  function submit() {
    onSubmit({
      tipfac: tipfac.trim() || "1",
      serie,
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
          <span>Empresa emisora / Serie</span>
          <select
            value={serie ?? ""}
            onChange={(e) =>
              setSerie(e.target.value ? Number(e.target.value) : null)
            }
          >
            {series.length === 0 ? (
              <option value="">— Cargando —</option>
            ) : null}
            <option value="">
              Heredar la del pedido en FACTUSOL (recomendado)
            </option>
            {series.map((s) => (
              <option key={s.serie} value={s.serie}>
                {s.serie} · {s.nombre}
              </option>
            ))}
          </select>
        </label>
        <span className="muted small">
          {serie === null
            ? "Se usará la serie con la que el pedido ya está en FACTUSOL."
            : `Se fuerza la serie ${serie}, ignorando la del pedido.`}
        </span>

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
