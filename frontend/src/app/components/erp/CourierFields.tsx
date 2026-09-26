"use client";

import { useState } from "react";
import { COURIERS, suggestCourier } from "../../lib/couriers";
import { setOrderTracking } from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const OTRO = "__otro__";

/** Selector de COURIER de un envío que no es de Genei: los habituales + «Otro»
 *  (texto libre). Vacío = sin courier («otro courier»). */
export function CourierSelect({
  value, onChange, disabled = false, compact = false,
}: {
  value: string;
  onChange: (courier: string) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const known = COURIERS.includes(value);
  // «Otro» elegido a mano (aún vacío) o un courier que no está en la lista.
  const [otroMode, setOtroMode] = useState(false);
  const otro = otroMode || (!known && value !== "");
  return (
    <span className={`sat-courier${compact ? " is-compact" : ""}`}>
      <select
        aria-label="Courier" value={otro ? OTRO : value} disabled={disabled}
        onChange={(e) => {
          const v = e.target.value;
          if (v === OTRO) { setOtroMode(true); if (known) onChange(""); return; }
          setOtroMode(false);
          onChange(v);
        }}
      >
        <option value="">— Courier —</option>
        {COURIERS.map((c) => <option key={c} value={c}>{c}</option>)}
        <option value={OTRO}>Otro…</option>
      </select>
      {otro ? (
        <input type="text" aria-label="Otro courier" placeholder="Nombre del courier"
               maxLength={60} value={value} disabled={disabled}
               onChange={(e) => onChange(e.target.value)} />
      ) : null}
    </span>
  );
}

/** Al escribir el tracking: si aún no hay courier y el formato lo delata
 *  (`1Z…` → UPS, `0033…` → CTT Express), lo propone (se puede cambiar). */
export function withSuggestion(tracking: string, courier: string): string {
  if (courier.trim()) return courier;
  return suggestCourier(tracking) ?? courier;
}

/** Poner o corregir courier + tracking de un envío con OTRO courier ya
 *  recogido (en línea en «Enviados» y en la ficha), sin volver a pasar por
 *  «Marcar recogido». */
export function CourierTrackingEditor({
  orderId, tracking: initialTracking, courier: initialCourier, onSaved, onCancel,
}: {
  orderId: string;
  tracking: string | null;
  courier: string | null;
  onSaved: () => void;
  onCancel?: () => void;
}) {
  const [tracking, setTracking] = useState(initialTracking ?? "");
  const [courier, setCourier] = useState(
    initialCourier ?? suggestCourier(initialTracking) ?? "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true); setError(null);
    try {
      await setOrderTracking(orderId, tracking.trim() || null, courier.trim());
      onSaved();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar el envío."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sat-courier-editor" role="group" aria-label="Courier y seguimiento">
      <input type="text" aria-label="Nº de seguimiento" placeholder="Nº de seguimiento"
             maxLength={64} value={tracking} disabled={busy}
             onChange={(e) => {
               const t = e.target.value;
               setTracking(t);
               setCourier((c) => withSuggestion(t, c));
             }} />
      <CourierSelect value={courier} onChange={setCourier} disabled={busy} compact />
      <button type="button" className="button small" disabled={busy} onClick={() => void save()}>
        {busy ? "Guardando…" : "Guardar"}
      </button>
      {onCancel ? (
        <button type="button" className="button small secondary" disabled={busy} onClick={onCancel}>
          Cancelar
        </button>
      ) : null}
      {error ? <span className="form-error small" role="alert">{error}</span> : null}
    </div>
  );
}
