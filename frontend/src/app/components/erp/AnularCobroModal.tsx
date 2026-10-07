"use client";

import { useCallback, useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";
import {
  annulOrderCobro,
  correctOrderCobro,
  getContrapartidas,
  getOrderFactusolCobro,
  waitForAnnulCobroJob,
  type BohubCobro,
  type Contrapartida,
  type OrderCobroInfo,
} from "../../lib/erpApi";

function eur(n: number | null | undefined): string {
  return n == null ? "—" : `${n.toFixed(2).replace(".", ",")} €`;
}

function fechaEs(iso: string | null | undefined): string {
  const [y, m, d] = String(iso ?? "").slice(0, 10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : "—";
}

function cobroLabel(c: BohubCobro): string {
  const cuenta = c.contrapartida_nombre ? `${c.contrapartida} · ${c.contrapartida_nombre}` : (c.contrapartida ?? "—");
  return `${eur(c.importe)} del ${fechaEs(c.fecha)} · contrapartida ${cuenta}`;
}

/** «Anular / corregir cobro» — SOLO los cobros que registró BoHub (según su
 *  auditoría): anular borra SU línea de cobro en FACTUSOL (F_LCO) y deja la
 *  factura pendiente (o parcial si tiene otros cobros); corregir = anular +
 *  registrar de nuevo con la fecha / cuenta / importe correctos, en una sola
 *  acción. Si la línea se editó en FACTUSOL no se toca y se explica. Modal
 *  compartido por la ficha y la cola «Por cobrar». */
export function AnularCobroModal({
  orderId,
  orderNumber,
  onClose,
  onDone,
}: {
  orderId: string;
  orderNumber: string;
  onClose: () => void;
  onDone?: (info: OrderCobroInfo) => void;
}) {
  const [info, setInfo] = useState<OrderCobroInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cuentas, setCuentas] = useState<Contrapartida[]>([]);
  const [target, setTarget] = useState<BohubCobro | null>(null);
  const [mode, setMode] = useState<"anular" | "corregir" | null>(null);
  const [fecha, setFecha] = useState("");
  const [cuenta, setCuenta] = useState("");
  const [importe, setImporte] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose, disabled: busy });

  const reload = useCallback(() => getOrderFactusolCobro(orderId), [orderId]);

  useEffect(() => {
    let alive = true;
    Promise.resolve()
      .then(() => reload())
      .then((r) => { if (alive) setInfo(r); })
      .catch((e) => {
        if (alive) setLoadError(extractErrorMessage(e, "No se pudo consultar la factura en FACTUSOL."));
      });
    Promise.resolve()
      .then(() => getContrapartidas())
      .then((items) => { if (alive) setCuentas(items ?? []); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [reload]);

  const cobros = info?.bohub_cobros ?? [];
  const activos = cobros.filter((c) => c.anulable);

  function start(c: BohubCobro, m: "anular" | "corregir") {
    setTarget(c);
    setMode(m);
    setConfirm(false);
    setError(null);
    setFecha(String(c.fecha ?? "").slice(0, 10));
    setCuenta(c.contrapartida ?? "");
    setImporte(c.importe != null ? c.importe.toFixed(2) : "");
  }

  // Tras anular, lo pendiente será lo de ahora + lo de este cobro.
  const maxCorregido = target && info?.saldo_pendiente != null
    ? info.saldo_pendiente + (target.importe ?? 0) : null;
  const importeNum = Number(String(importe).replace(",", "."));
  const importeOk = Number.isFinite(importeNum) && importeNum > 0
    && (maxCorregido == null || importeNum <= maxCorregido + 0.005);
  const canSubmit = !!target && confirm && !busy && !success
    && (mode === "anular" || (!!fecha && !!cuenta && importeOk));

  async function submit() {
    if (!target || !mode) return;
    setBusy(true);
    setError(null);
    try {
      const queued = mode === "anular"
        ? await annulOrderCobro(orderId, target.id)
        : await correctOrderCobro(orderId, target.id, {
          cuenta, fecha, importe: Math.round(importeNum * 100) / 100,
        });
      const st = await waitForAnnulCobroJob(queued.job_id);
      if (st.status === "pending") {
        setError("La anulación sigue en cola en FACTUSOL; vuelve a comprobar el pedido en unos segundos.");
        return;
      }
      if (st.status === "failed") {
        setError(`No se pudo anular el cobro en FACTUSOL: ${st.error ?? "error"}`);
        return;
      }
      const r = st.result;
      if (!r.annulled) {
        setError(r.motivo ?? `No se pudo anular el cobro (${r.status}).`);
        return;
      }
      const fresh = await reload();
      setInfo(fresh);
      if (mode === "corregir") {
        const nuevo = r.correccion;
        setSuccess(nuevo?.registered
          ? `Cobro corregido: anulado el de ${cobroLabel(target)} y registrado el nuevo de `
            + `${eur(nuevo.importe)}. Quedan ${eur(fresh.saldo_pendiente)} pendientes.`
          : `Cobro anulado, pero el nuevo NO se registró: ${nuevo?.motivo ?? nuevo?.status ?? "error"}. `
            + "Regístralo con «Registrar cobro».");
      } else {
        setSuccess(
          `Cobro de ${cobroLabel(target)} anulado en FACTUSOL. La factura queda con `
          + `${eur(fresh.saldo_pendiente)} pendientes.`,
        );
      }
      onDone?.(fresh);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo anular el cobro en FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  const title = `Anular / corregir cobro · ${orderNumber}`;
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={title} {...overlayProps}>
      <div className="modal-dialog">
        <div className="modal-header">
          <h2>{title}</h2>
          <ModalCloseButton onClose={requestClose} disabled={busy} placement="header" />
        </div>
        <div className="modal-body">
          {!info && !loadError ? <p className="muted">Consultando la factura en FACTUSOL…</p> : null}
          {loadError ? <p className="form-error">{loadError}</p> : null}
          {info ? (
            <>
              {info.invoice ? (
                <p className="small">
                  <strong>Factura {info.invoice.numero}</strong> · total {eur(info.total)} ·
                  cobrado {eur(info.total_cobrado)} · pendiente <strong>{eur(info.saldo_pendiente)}</strong>
                </p>
              ) : null}
              {cobros.length === 0 ? (
                <p className="form-info" role="status">
                  No hay cobros registrados por BoHub en esta factura. Los cobros hechos a mano
                  en FACTUSOL no se pueden anular desde aquí.
                </p>
              ) : (
                <ul className="erp-cobros-bohub" aria-label="Cobros registrados por BoHub">
                  {cobros.map((c) => (
                    <li key={c.id} className={c.anulado ? "muted" : undefined}>
                      <span>
                        Línea {c.linlco ?? "?"} · {cobroLabel(c)}
                        {c.anulado ? ` · anulado${c.anulado_at ? ` el ${fechaEs(c.anulado_at)}` : ""}` : ""}
                      </span>
                      {c.anulable && !success ? (
                        <span className="erp-cobros-bohub-actions">
                          <button type="button" className="button small secondary" disabled={busy}
                                  aria-label={`Anular cobro línea ${c.linlco}`}
                                  onClick={() => start(c, "anular")}>Anular</button>
                          <button type="button" className="button small secondary" disabled={busy}
                                  aria-label={`Corregir cobro línea ${c.linlco}`}
                                  onClick={() => start(c, "corregir")}>Corregir</button>
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              {cobros.length > 0 && activos.length === 0 && !success ? (
                <p className="muted small">Todos los cobros de BoHub de esta factura ya están anulados.</p>
              ) : null}
              {target && mode && !success ? (
                <div className="modal-form erp-anular-cobro" aria-label={mode === "anular" ? "Anular cobro" : "Corregir cobro"}>
                  <p className="form-info" role="note">
                    {mode === "anular"
                      ? "Se BORRARÁ en FACTUSOL la línea de cobro de "
                      : "Se ANULARÁ en FACTUSOL la línea de cobro de "}
                    <strong>{cobroLabel(target)}</strong> (línea {target.linlco} de F_LCO) y la factura
                    volverá a pendiente (o a parcial si tiene otros cobros)
                    {mode === "corregir" ? "; después se registrará el cobro con los datos de abajo" : ""}.
                    Es una escritura en FACTUSOL. Si la línea se ha cambiado en FACTUSOL, no se toca.
                  </p>
                  {mode === "corregir" ? (
                    <>
                      <label>
                        <span>Fecha del cobro</span>
                        <input type="date" aria-label="Nueva fecha del cobro" value={fecha}
                               disabled={busy} onChange={(e) => setFecha(e.target.value)} />
                      </label>
                      <label>
                        <span>Cuenta / contrapartida</span>
                        <select aria-label="Nueva cuenta del cobro" value={cuenta} disabled={busy}
                                onChange={(e) => setCuenta(e.target.value)}>
                          <option value="">— elige la cuenta —</option>
                          {cuentas.map((c) => (
                            <option key={c.codigo} value={c.codigo}>{c.codigo} · {c.nombre}</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span>Importe</span>
                        <input type="number" inputMode="decimal" step="0.01" min="0.01"
                               aria-label="Nuevo importe del cobro" value={importe} disabled={busy}
                               onChange={(e) => setImporte(e.target.value)} />
                      </label>
                      {!importeOk && importe ? (
                        <p className="form-error small" role="alert">
                          El importe tiene que ser mayor que 0 y no más de {eur(maxCorregido)}.
                        </p>
                      ) : null}
                    </>
                  ) : null}
                  <label className="field-toggle">
                    <input type="checkbox" aria-label="Confirmo la anulación" checked={confirm}
                           disabled={busy} onChange={(e) => setConfirm(e.target.checked)} />
                    <span>Confirmo que quiero {mode === "anular" ? "anular" : "corregir"} este cobro en FACTUSOL.</span>
                  </label>
                </div>
              ) : null}
            </>
          ) : null}
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          {success ? <p className="form-success" role="status">{success}</p> : null}
        </div>
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            {success ? "Cerrar" : "Cancelar"}
          </button>
          {target && mode && !success ? (
            <button type="button" className="button danger" disabled={!canSubmit}
                    onClick={() => void submit()}>
              {busy ? "Escribiendo en FACTUSOL…" : mode === "anular" ? "Anular cobro" : "Corregir cobro"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
