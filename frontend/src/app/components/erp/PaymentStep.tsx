"use client";

import { useEffect, useState } from "react";
import {
  getContrapartidas,
  getFactusolFormasPago,
  type Contrapartida,
  type FormaPago,
  type PaymentIntentInput,
} from "../../lib/erpApi";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Valor inicial del paso: «sin pago» con la forma de pago que trae el
 *  documento de FACTUSOL (`FOPPRE`/`FOPPCL`), si la trae. */
export function initialPayment(
  formaPago?: string | null, formaPagoNombre?: string | null,
): PaymentIntentInput {
  return {
    paid: false,
    forma_pago: formaPago ?? null,
    forma_pago_nombre: formaPagoNombre ?? null,
    contrapartida: null,
    fecha: today(),
  };
}

/** ¿Se puede enviar? «Pagado» exige la cuenta; «sin pago» siempre vale. */
export function paymentReady(value: PaymentIntentInput): boolean {
  return !value.paid || Boolean((value.contrapartida ?? "").trim());
}

/** Fase 2 — paso de confirmación de pago al convertir (opción B, decidida
 *  con Bart): se elige la forma de pago (por defecto la del documento) y si
 *  está pagado.
 *
 *  - **Sin pago** (por defecto): solo se apunta la forma de pago; el pedido
 *    queda pendiente, sin cobro ni intención.
 *  - **Pagado**: NO se emite factura. Se apunta el pago (cuenta donde entró
 *    el dinero + fecha) y el cobro se registra en FACTUSOL (F-4-B) cuando
 *    exista la factura de ese pedido.
 *
 *  Catálogos best-effort: sin FACTUSOL, la forma de pago se escribe a mano. */
export function PaymentStep({
  value,
  onChange,
  disabled = false,
}: {
  value: PaymentIntentInput;
  onChange: (next: PaymentIntentInput) => void;
  disabled?: boolean;
}) {
  const [formas, setFormas] = useState<FormaPago[]>([]);
  const [cuentas, setCuentas] = useState<Contrapartida[]>([]);

  useEffect(() => {
    let alive = true;
    Promise.resolve()
      .then(() => getFactusolFormasPago())
      .then((items) => { if (alive) setFormas(items ?? []); })
      .catch(() => undefined);
    Promise.resolve()
      .then(() => getContrapartidas())
      .then((items) => { if (alive) setCuentas(items ?? []); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, []);

  const formaCodigo = value.forma_pago ?? "";
  const formaKnown = formas.some((f) => (f.codigo ?? "") === formaCodigo);

  function setForma(codigo: string) {
    const hit = formas.find((f) => (f.codigo ?? "") === codigo);
    onChange({
      ...value,
      forma_pago: codigo || null,
      forma_pago_nombre: hit ? hit.nombre : (codigo ? value.forma_pago_nombre ?? null : null),
    });
  }

  return (
    <fieldset className="erp-payment-step" disabled={disabled}>
      <legend>Pago</legend>
      <div className="form-row">
        <label className="field">
          <span>Forma de pago</span>
          <select
            aria-label="Forma de pago"
            value={formaCodigo}
            onChange={(e) => setForma(e.target.value)}
          >
            <option value="">— sin indicar —</option>
            {!formaKnown && formaCodigo ? (
              <option value={formaCodigo}>
                {value.forma_pago_nombre
                  ? `${value.forma_pago_nombre} (${formaCodigo})`
                  : `Código ${formaCodigo}`}
              </option>
            ) : null}
            {formas.map((f) => (
              <option key={f.codigo ?? f.nombre} value={f.codigo ?? ""}>
                {f.nombre}{f.codigo ? ` (${f.codigo})` : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="field">
          <span>Estado del pago</span>
          <label className="field-toggle">
            <input
              type="radio"
              name="erp-payment-state"
              aria-label="Sin pago"
              checked={!value.paid}
              onChange={() => onChange({ ...value, paid: false })}
            />
            <span>Sin pago (pendiente)</span>
          </label>
          <label className="field-toggle">
            <input
              type="radio"
              name="erp-payment-state"
              aria-label="Pagado"
              checked={value.paid}
              onChange={() => onChange({ ...value, paid: true, fecha: value.fecha || today() })}
            />
            <span>Pagado</span>
          </label>
        </div>
      </div>
      {value.paid ? (
        <div className="form-row">
          <label className="field">
            <span>Cuenta donde entró el dinero</span>
            <select
              aria-label="Cuenta del cobro"
              value={value.contrapartida ?? ""}
              onChange={(e) => onChange({ ...value, contrapartida: e.target.value || null })}
            >
              <option value="">— elige la cuenta —</option>
              {cuentas.map((c) => (
                <option key={c.codigo} value={c.codigo}>{c.codigo} · {c.nombre}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Fecha del cobro</span>
            <input
              type="date"
              aria-label="Fecha del cobro"
              value={value.fecha ?? ""}
              onChange={(e) => onChange({ ...value, fecha: e.target.value || null })}
            />
          </label>
        </div>
      ) : null}
      <p className="muted small">
        {value.paid
          ? "No se emite ninguna factura. El pago queda apuntado en el pedido y el cobro se registra en FACTUSOL cuando exista la factura."
          : "Solo se apunta la forma de pago: el pedido queda pendiente de pago, sin cobro."}
      </p>
    </fieldset>
  );
}
