"use client";

import { useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  getContrapartidas,
  getFactusolFormasPago,
  getOrderFactusolCobro,
  registerInvoiceCollection,
  waitForInvoiceCollectionJob,
  type Contrapartida,
  type FormaPago,
  type OrderCobroInfo,
} from "../../lib/erpApi";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function eur(n: number | null | undefined): string {
  return n == null ? "—" : `${n.toFixed(2)} €`;
}

/** «Registrar cobro en FACTUSOL» — modal COMPARTIDO por la ficha del pedido y
 *  la fila de la bandeja. Reutiliza el motor F-4-B tal cual: el cobro se
 *  registra con `POST /factusol/documents/facturas/{serie}/{codigo}/collection`
 *  (solo `F_LCO` + `ESTFAC=2`, cola `factusol:writes`, idempotente).
 *
 *  Al abrir resuelve la factura del pedido EN VIVO (serie + número, importe,
 *  saldo, forma de pago, cuenta sugerida y avisos). Ya cobrada → estado
 *  «Cobrado», sin doble cobro. Con líneas de cobro previas (anticipo /
 *  posible doble cobro) AVISA pero deja decidir. Confirmación explícita antes
 *  de escribir; al terminar re-comprueba la factura (saldo ≈ 0). */
export function RegistrarCobroModal({
  orderId,
  orderNumber,
  onClose,
  onDone,
}: {
  orderId: string;
  orderNumber: string;
  onClose: () => void;
  /** Se llama con el estado re-comprobado tras registrar (o si ya estaba cobrada). */
  onDone?: (info: OrderCobroInfo) => void;
}) {
  const [info, setInfo] = useState<OrderCobroInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cuentas, setCuentas] = useState<Contrapartida[]>([]);
  const [formas, setFormas] = useState<FormaPago[]>([]);
  const [cuenta, setCuenta] = useState("");
  const [fecha, setFecha] = useState(today());
  const [forma, setForma] = useState("");
  const [observaciones, setObservaciones] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.resolve()
      .then(() => getOrderFactusolCobro(orderId))
      .then((res) => {
        if (!alive) return;
        setInfo(res);
        if (res.suggested_cuenta?.codigo) setCuenta(res.suggested_cuenta.codigo);
        if (res.forma_pago_nombre) setForma(res.forma_pago_nombre);
      })
      .catch((e) => {
        if (alive) setLoadError(extractErrorMessage(e, "No se pudo consultar la factura en FACTUSOL."));
      })
      .finally(() => { if (alive) setLoading(false); });
    Promise.resolve()
      .then(() => getContrapartidas())
      .then((items) => { if (alive) setCuentas(items ?? []); })
      .catch(() => undefined);
    Promise.resolve()
      .then(() => getFactusolFormasPago())
      .then((items) => { if (alive) setFormas(items ?? []); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [orderId]);

  const pendiente = info?.status === "pendiente";
  const cobrada = info?.status === "cobrada";
  const invoice = info?.invoice ?? null;
  const cuentaNombre = cuentas.find((c) => c.codigo === cuenta)?.nombre
    ?? (info?.suggested_cuenta?.codigo === cuenta ? info?.suggested_cuenta?.nombre : undefined)
    ?? cuenta;
  const formaKnown = formas.some((f) => f.nombre === forma);
  const canSubmit = pendiente && !!invoice?.serie && !!cuenta && !!fecha && confirm && !busy && !success;

  async function submit() {
    if (!info || !invoice || invoice.serie == null || invoice.codigo == null) return;
    setBusy(true);
    setError(null);
    try {
      const res = await registerInvoiceCollection(invoice.serie, invoice.codigo, {
        confirm: true, cuenta, fecha,
        forma: forma || null, observaciones: observaciones.trim() || null,
      });
      if (res.status === "already") {
        const fresh = await getOrderFactusolCobro(orderId);
        setInfo(fresh);
        setSuccess(`La factura ${res.numero} ya constaba cobrada en FACTUSOL: no se ha registrado nada.`);
        onDone?.(fresh);
        return;
      }
      if (!res.job_id) throw new Error("FACTUSOL no devolvió el job del cobro.");
      const st = await waitForInvoiceCollectionJob(res.job_id);
      if (st.status === "pending") {
        setError("El cobro sigue en cola en FACTUSOL; vuelve a comprobar el pedido en unos segundos.");
        return;
      }
      if (st.status === "failed") {
        setError(`No se pudo registrar el cobro en FACTUSOL: ${st.error ?? "error"}`);
        return;
      }
      if (!st.result.registered && st.result.status !== "already") {
        setError(`No se pudo registrar el cobro en FACTUSOL: ${st.result.motivo ?? st.result.status}`);
        return;
      }
      // Re-chequeo: la factura debe quedar con saldo ≈ 0 / ESTFAC=2.
      const fresh = await getOrderFactusolCobro(orderId);
      setInfo(fresh);
      if (fresh.status === "cobrada") {
        setSuccess(
          `Cobro de ${eur(st.result.importe ?? res.importe)} registrado en FACTUSOL para la factura `
          + `${res.numero} (cuenta ${res.contrapartida.nombre}). La factura consta cobrada.`,
        );
      } else {
        setSuccess(
          `Cobro registrado en FACTUSOL para la factura ${res.numero}, pero al re-comprobar el saldo `
          + `sigue en ${eur(fresh.saldo_pendiente)}. Revísalo en ERP · Documentos.`,
        );
      }
      onDone?.(fresh);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo registrar el cobro en FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  const title = `Registrar cobro en FACTUSOL · ${orderNumber}`;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal-dialog">
        <div className="modal-header">
          <h2>{title}</h2>
          <button type="button" className="modal-close" aria-label="Cerrar" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          {loading ? <p className="muted">Consultando la factura en FACTUSOL…</p> : null}
          {loadError ? <p className="form-error">{loadError}</p> : null}
          {info && !loading ? (
            <>
              {info.status === "sin_factura" ? (
                <p className="form-info" role="status">
                  Este pedido aún no tiene factura en FACTUSOL: emite la factura primero.
                </p>
              ) : null}
              {info.status === "unresolved" || info.status === "not_found" ? (
                <p className="form-info" role="status">{info.detail}</p>
              ) : null}
              {invoice && (pendiente || cobrada) ? (
                <div className="erp-cobro-target">
                  <p>
                    <strong>Factura {invoice.numero}</strong>
                    {info.cliente ? ` · ${info.cliente}` : ""}
                    {info.referencia ? ` · ref. ${info.referencia}` : ""}
                  </p>
                  <p className="small">
                    Total {eur(info.total)} · cobrado {eur(info.total_cobrado)} ·{" "}
                    saldo pendiente <strong>{eur(info.saldo_pendiente)}</strong>
                    {info.estfac != null ? ` · ESTFAC=${info.estfac}` : ""}
                    {info.cobros ? ` · ${info.cobros} línea(s) de cobro` : ""}
                  </p>
                  {cobrada ? (
                    <p>
                      <span className="badge ok">Cobrado FACTUSOL</span>{" "}
                      <span className="muted small">
                        La factura ya consta cobrada en FACTUSOL: no se registra un segundo cobro.
                      </span>
                    </p>
                  ) : null}
                </div>
              ) : null}
              {(info.warnings ?? []).map((w) => (
                <p key={w} className="form-info" role="alert">⚠ {w}</p>
              ))}
              {pendiente ? (
                <div className="modal-form">
                  <label>
                    <span>Cuenta / contrapartida (donde entra el dinero)</span>
                    <select
                      aria-label="Cuenta del cobro"
                      value={cuenta}
                      disabled={busy || !!success}
                      onChange={(e) => setCuenta(e.target.value)}
                    >
                      <option value="">— elige la cuenta —</option>
                      {info.suggested_cuenta && !cuentas.some((c) => c.codigo === info.suggested_cuenta?.codigo) ? (
                        <option value={info.suggested_cuenta.codigo}>
                          {info.suggested_cuenta.codigo} · {info.suggested_cuenta.nombre}
                        </option>
                      ) : null}
                      {cuentas.map((c) => (
                        <option key={c.codigo} value={c.codigo}>
                          {c.codigo} · {c.nombre}
                          {info.suggested_cuenta?.codigo === c.codigo ? " (sugerida)" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Fecha del cobro</span>
                    <input
                      type="date"
                      aria-label="Fecha del cobro"
                      value={fecha}
                      disabled={busy || !!success}
                      onChange={(e) => setFecha(e.target.value)}
                    />
                  </label>
                  <label>
                    <span>Forma de pago</span>
                    <select
                      aria-label="Forma de pago"
                      value={forma}
                      disabled={busy || !!success}
                      onChange={(e) => setForma(e.target.value)}
                    >
                      <option value="">— sin indicar —</option>
                      {!formaKnown && forma ? <option value={forma}>{forma}</option> : null}
                      {formas.map((f) => (
                        <option key={f.codigo ?? f.nombre} value={f.nombre}>{f.nombre}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Observaciones (opcional)</span>
                    <input
                      type="text"
                      aria-label="Observaciones del cobro"
                      maxLength={255}
                      value={observaciones}
                      disabled={busy || !!success}
                      onChange={(e) => setObservaciones(e.target.value)}
                    />
                  </label>
                  <p className="muted small erp-cobro-summary">
                    Se escribirá <strong>1 línea de cobro en F_LCO</strong> de la factura{" "}
                    {invoice?.numero}: <strong>{eur(info.saldo_pendiente)}</strong> con fecha{" "}
                    {fecha || "—"} en la cuenta <strong>{cuentaNombre || "—"}</strong>
                    {forma ? ` (${forma})` : ""}, y la factura pasará a cobrada (ESTFAC=2).
                    No se toca ninguna otra cosa en FACTUSOL.
                  </p>
                  <label className="field-toggle">
                    <input
                      type="checkbox"
                      aria-label="Confirmo el cobro"
                      checked={confirm}
                      disabled={busy || !!success}
                      onChange={(e) => setConfirm(e.target.checked)}
                    />
                    <span>Confirmo que quiero registrar este cobro en FACTUSOL (escritura contable).</span>
                  </label>
                </div>
              ) : null}
            </>
          ) : null}
          {error ? <p className="form-error">{error}</p> : null}
          {success ? <p className="form-success" role="status">{success}</p> : null}
        </div>
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            {success || cobrada ? "Cerrar" : "Cancelar"}
          </button>
          {pendiente && !success ? (
            <button
              type="button"
              className="button"
              disabled={!canSubmit}
              title={!confirm ? "Marca la confirmación para poder registrar" : undefined}
              onClick={() => void submit()}
            >
              {busy ? "Registrando…" : "Registrar cobro"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
