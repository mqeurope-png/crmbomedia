"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";
import {
  cobrarFacturaConMovimiento,
  confirmarCobroBanco,
  getContrapartidas,
  getFactusolFacturaCobro,
  getFactusolFormasPago,
  getOrderFactusolCobro,
  registerInvoiceCollection,
  waitForInvoiceCollectionJob,
  type CobroBancoResultado,
  type CobroHoja,
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

/** DD/MM/AAAA del tramo de fecha de un ISO. */
function dmy(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
}

/** Lo que pasó en la hoja de Drive, en una frase. El cobro ya está hecho: un
 *  fallo aquí se avisa, no lo deshace. */
function fraseHoja(h: CobroHoja, numero: string): { texto: string; ok: boolean } {
  const donde = `la hoja de Drive de ${h.cuenta}${h.pestana ? ` (pestaña ${h.pestana})` : ""}`;
  switch (h.estado) {
    case "escrita":
      return { ok: true, texto: `Escrita la ${numero} en la columna «factura» de ${donde}.` };
    case "ocupada":
      return { ok: true, texto: `La celda «factura» de esa fila en ${donde} ya tenía algo escrito: no se ha tocado.` };
    case "no_encontrada":
      return { ok: false, texto: `No se ha encontrado la fila de ese movimiento en ${donde}: no se ha escrito nada. Se reintenta en el repaso diario.` };
    case "en_cola":
      return { ok: true, texto: `${h.detalle ?? "Se escribe en un momento"} (${donde}).` };
    case "pendiente":
      return { ok: true, texto: `La fila de ese movimiento ha cambiado en ${donde} mientras se escribía: se escribe en la siguiente pasada.` };
    case "corrida":
      return { ok: false, texto: `La fila de ese movimiento está corrida en ${donde} (la factura no cae en su columna): no se ha escrito. Arréglala en la hoja.` };
    case "sin_columna":
      return { ok: false, texto: `${donde[0].toUpperCase()}${donde.slice(1)} no tiene columna «factura»: no se ha escrito.` };
    default:
      return { ok: false, texto: `El cobro está registrado, pero ${donde} no se ha podido escribir: ${h.detalle ?? "error"}. Queda anotado y se reintenta.` };
  }
}

/** «Registrar cobro en FACTUSOL» — LA herramienta de cobro (29/10/2026): el mismo
 *  diálogo, texto, comprobaciones y apunte en Auditoría desde la ficha del
 *  pedido (y su paso «Cobro» con el movimiento del banco), la bandeja,
 *  Conciliación y ERP · Documentos.
 *
 *  Con un movimiento del banco identificado (`movimientoId`, o el conciliado
 *  con la factura que aún no está en FACTUSOL) el cobro va con SU fecha valor y
 *  la contrapartida de su cuenta, concilia el movimiento y escribe la factura
 *  en la hoja de Drive de esa cuenta: el diálogo lo dice antes y cuenta después
 *  qué pasó en la hoja (un fallo allí no deshace el cobro). Ya cobrada: no se
 *  ofrece registrar; se enseña contra qué se cobró. Una factura externa (sin
 *  pedido de BoHub) lo dice en una línea.
 *
 *  Sin movimiento reutiliza el motor F-4-B tal cual: `POST /factusol/documents/
 *  facturas/{serie}/{codigo}/collection` (solo `F_LCO` + `ESTFAC`, cola
 *  `factusol:writes`, idempotente). Con movimiento, `cobro-banco` (el de la
 *  ficha, también por factura), que concilia y llama a ese mismo motor.
 *
 *  Se abre de dos formas: con un pedido de BoHub (`orderId`/`orderNumber`, que
 *  resuelve su factura) o con una factura de FACTUSOL directa (`factura`,
 *  serie+número, sin pedido). En ambos casos resuelve EN VIVO importe, saldo,
 *  forma de pago, cuenta sugerida y avisos. Ya cobrada → «Cobrado», sin doble
 *  cobro. Con líneas de cobro previas AVISA pero deja decidir. Confirmación
 *  explícita antes de escribir; al terminar re-comprueba la factura (saldo ≈ 0). */
export function RegistrarCobroModal({
  orderId,
  orderNumber,
  factura,
  movimientoId,
  onClose,
  onDone,
}: {
  orderId?: string;
  orderNumber?: string;
  /** Fase 5 — cobro de una factura de FACTUSOL sin pedido de BoHub. */
  factura?: { serie: number; codigo: number | string; numero: string };
  /** El movimiento del banco con el que se cobra (ficha, Conciliación). */
  movimientoId?: string;
  onClose: () => void;
  /** Se llama con el estado re-comprobado tras registrar (o si ya estaba cobrada). */
  onDone?: (info: OrderCobroInfo) => void;
}) {
  // Deps por primitivos (no el objeto `factura`, que el padre recrea en cada
  // render): así el efecto de carga no se re-dispara en bucle.
  const facSerie = factura?.serie;
  const facCodigo = factura?.codigo;
  const reload = useCallback(
    (): Promise<OrderCobroInfo> =>
      facSerie !== undefined && facCodigo !== undefined
        ? getFactusolFacturaCobro(facSerie, facCodigo, movimientoId)
        : getOrderFactusolCobro(orderId as string, movimientoId),
    [facSerie, facCodigo, orderId, movimientoId],
  );
  const [info, setInfo] = useState<OrderCobroInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cuentas, setCuentas] = useState<Contrapartida[]>([]);
  const [formas, setFormas] = useState<FormaPago[]>([]);
  const [cuenta, setCuenta] = useState("");
  // Importe: por defecto lo PENDIENTE en FACTUSOL (leído en vivo); editable
  // para un cobro parcial. Nunca más que lo pendiente.
  const [importe, setImporte] = useState("");
  const [fecha, setFecha] = useState(today());
  const [forma, setForma] = useState("");
  const [observaciones, setObservaciones] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  // Con el movimiento del banco (si lo hay) salvo que se pida registrarlo sin él.
  const [sinMovimiento, setSinMovimiento] = useState(false);
  // FACTUSOL ya tiene un cobro igual suelto: «es otro pago» se confirma aparte.
  const [dobleCobro, setDobleCobro] = useState<string | null>(null);
  const [otroPago, setOtroPago] = useState(false);
  const [hoja, setHoja] = useState<{ texto: string; ok: boolean } | null>(null);
  // El backend ha frenado el cobro con el movimiento por un posible doble
  // cobro o un cobro en cola: ya no se ofrece registrarlo «sin el movimiento»
  // (sería saltarse esa comprobación).
  const [sinSalida, setSinSalida] = useState(false);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose, disabled: busy });

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.resolve()
      .then(() => reload())
      .then((res) => {
        if (!alive) return;
        setInfo(res);
        if (res.suggested_cuenta?.codigo) setCuenta(res.suggested_cuenta.codigo);
        if (res.saldo_pendiente != null) setImporte(res.saldo_pendiente.toFixed(2));
        if (res.forma_pago_nombre) setForma(res.forma_pago_nombre);
        // Bloque B: si al dar de alta el pedido se apuntó la fecha del pago,
        // se prellena aquí (si no, se queda «hoy»).
        if (res.suggested_fecha) setFecha(res.suggested_fecha);
        // Con el movimiento del banco: SU fecha valor, la cuenta de su banco y
        // lo que paga de esta factura.
        const m = res.movimiento;
        if (m) {
          setFecha(m.fecha_valor);
          if (m.cuenta.contrapartida_codigo) setCuenta(m.cuenta.contrapartida_codigo);
          setImporte(Math.min(m.importe_factura, res.saldo_pendiente ?? m.importe_factura).toFixed(2));
          setForma("Transferencia");
        }
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
  }, [reload]);

  const pendiente = info?.status === "pendiente";
  const cobrada = info?.status === "cobrada";
  const invoice = info?.invoice ?? null;
  const mov = !sinMovimiento ? info?.movimiento ?? null : null;
  // Su cobro ya está en FACTUSOL: solo se concilia el movimiento.
  const soloConciliar = !!mov?.ya_en_factusol;
  const sinContrapartida = !!mov && !mov.cuenta.contrapartida_codigo;
  const cuentaNombre = cuentas.find((c) => c.codigo === cuenta)?.nombre
    ?? (info?.suggested_cuenta?.codigo === cuenta ? info?.suggested_cuenta?.nombre : undefined)
    ?? cuenta;
  const formaKnown = formas.some((f) => f.nombre === forma);
  const saldo = info?.saldo_pendiente ?? null;
  const importeNum = Number(String(importe).replace(",", "."));
  const importeOk = Number.isFinite(importeNum) && importeNum > 0;
  const importeExcede = importeOk && saldo != null && importeNum > saldo + 0.005;
  const parcial = importeOk && !importeExcede && saldo != null && importeNum < saldo - 0.005;
  const canSubmit = pendiente && !!invoice?.serie && !!cuenta && !!fecha && confirm && !busy
    && !success && importeOk && !importeExcede && !sinContrapartida
    && (!dobleCobro || otroPago);

  /** Con el movimiento del banco: lo mismo desde la ficha que por factura. */
  async function submitConMovimiento() {
    if (!info || !mov || !invoice || invoice.serie == null || invoice.codigo == null) return;
    setBusy(true);
    setError(null);
    let res: CobroBancoResultado;
    try {
      res = orderId
        ? await confirmarCobroBanco(orderId, mov.id, { forzar: otroPago })
        : await cobrarFacturaConMovimiento(invoice.serie, invoice.codigo, mov.id, { forzar: otroPago });
    } catch (e) {
      if (e instanceof ApiError && (e.code === "posible_doble_cobro" || e.code === "en_cola")) {
        setSinSalida(true);
      }
      if (e instanceof ApiError && e.code === "posible_doble_cobro") {
        setDobleCobro(extractErrorMessage(e, "Puede ser un doble cobro."));
      } else {
        setError(extractErrorMessage(e, "No se pudo registrar el cobro con ese movimiento."));
        if (e instanceof ApiError && e.code === "cobro_no_encolado") onDone?.(info);
      }
      setBusy(false);
      return;
    }
    if (res.hoja) setHoja(fraseHoja(res.hoja, res.numero));
    const conciliado = `El movimiento queda conciliado con la ${res.numero}`;
    try {
      if (res.status === "already") {
        setSuccess(`El cobro ya constaba en FACTUSOL${res.fecha_factusol ? ` (${dmy(res.fecha_factusol)}`
          + `${res.cuenta_factusol ? `, cuenta ${res.cuenta_factusol}` : ""})` : ""}: no se ha `
          + `registrado otra vez. ${conciliado}.`);
      } else if (!res.job_id) {
        setError(`${conciliado}, pero FACTUSOL no devolvió el trabajo del cobro: vuelve a mirarlo en unos segundos.`);
      } else {
        const st = await waitForInvoiceCollectionJob(res.job_id);
        if (st.status === "pending") {
          setError(`El cobro sigue en cola en FACTUSOL. ${conciliado}. Vuelve a mirarlo en unos segundos.`);
        } else if (st.status === "failed" || (!st.result.registered && st.result.status !== "already")) {
          const motivo = st.status === "failed" ? st.error ?? "error" : st.result.motivo ?? st.result.status;
          setError(`${conciliado}, pero FACTUSOL no ha registrado el cobro (${motivo}).`);
        } else {
          setSuccess(res.parcial
            ? `Cobro parcial de ${eur(res.importe)} registrado en FACTUSOL con fecha ${dmy(res.movimiento.fecha_valor)} `
              + `(cuenta ${res.contrapartida?.nombre ?? cuentaNombre}). ${conciliado}.`
            : `Cobro de ${eur(res.importe)} registrado en FACTUSOL con fecha ${dmy(res.movimiento.fecha_valor)} `
              + `(cuenta ${res.contrapartida?.nombre ?? cuentaNombre}): la ${res.numero} consta cobrada. ${conciliado}.`);
        }
      }
      const fresh = await reload();
      setInfo(fresh);
      onDone?.(fresh);
    } catch (e) {
      setError(`${conciliado}, pero no se ha podido comprobar el cobro en FACTUSOL `
        + `(${extractErrorMessage(e, "error")}).`);
      onDone?.(info);
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (!info || !invoice || invoice.serie == null || invoice.codigo == null) return;
    if (mov) {
      await submitConMovimiento();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await registerInvoiceCollection(invoice.serie, invoice.codigo, {
        confirm: true, cuenta, fecha,
        forma: forma || null, observaciones: observaciones.trim() || null,
        // Solo si difiere de lo pendiente (así el total sigue siendo el saldo real).
        ...(parcial ? { importe: Math.round(importeNum * 100) / 100 } : {}),
      });
      if (res.status === "already") {
        const fresh = await reload();
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
      const fresh = await reload();
      setInfo(fresh);
      if (fresh.status === "cobrada") {
        setSuccess(
          `Cobro de ${eur(st.result.importe ?? res.importe)} registrado en FACTUSOL para la factura `
          + `${res.numero} (cuenta ${res.contrapartida.nombre}). La factura consta cobrada.`,
        );
      } else if (st.result.parcial || res.parcial) {
        setSuccess(
          `Cobro parcial de ${eur(st.result.importe ?? res.importe)} registrado en FACTUSOL para la `
          + `factura ${res.numero} (cuenta ${res.contrapartida.nombre}). Quedan `
          + `${eur(fresh.saldo_pendiente)} pendientes.`,
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

  const title = `Registrar cobro en FACTUSOL · ${orderNumber ?? factura?.numero ?? ""}`;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={title} {...overlayProps}>
      <div className="modal-dialog">
        <div className="modal-header">
          <h2>{title}</h2>
          <ModalCloseButton onClose={requestClose} disabled={busy} placement="header" />
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
              {info.externa ? (
                <p className="form-info small erp-cobro-externa" role="note">
                  Factura externa: no hay pedido de BoHub detrás. La trazabilidad se queda en
                  FACTUSOL.
                </p>
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
                  {info.total_mismatch ? (
                    /* El pedido y la factura no valen lo mismo (portes, líneas
                       cambiadas, recálculo tras emitir): el cobro va por lo
                       pendiente EN FACTUSOL, nunca por el total del pedido. */
                    <p className="form-info small" role="alert">
                      ⚠ Pedido {eur(info.total_mismatch.pedido)} · Factura{" "}
                      {eur(info.total_mismatch.factura)} · diferencia{" "}
                      {eur(Math.abs(info.total_mismatch.diferencia))}
                    </p>
                  ) : null}
                  {cobrada ? (
                    <>
                      <p>
                        <span className="badge ok">Cobrado FACTUSOL</span>{" "}
                        <span className="muted small">
                          La factura ya consta cobrada en FACTUSOL: no se registra un segundo cobro.
                        </span>
                      </p>
                      {(info.cobrado_con ?? []).length ? (
                        <ul className="small erp-cobro-cobrado-con" aria-label="Contra qué se cobró">
                          {(info.cobrado_con ?? []).map((c, i) => (
                            <li key={i}>
                              {dmy(c.fecha)} · {eur(c.importe)}
                              {c.cuenta ? ` · cuenta ${c.cuenta}` : ""}
                              {c.movimiento
                                ? ` · movimiento del banco del ${dmy(c.movimiento.fecha_valor)}`
                                  + `${c.movimiento.cuenta.nombre ? ` (${c.movimiento.cuenta.nombre})` : ""}`
                                : " · sin movimiento del banco"}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </>
                  ) : null}
                </div>
              ) : null}
              {(info.warnings ?? []).map((w) => (
                <p key={w} className="form-info" role="alert">⚠ {w}</p>
              ))}
              {pendiente && mov ? (
                <div className="erp-cobro-movimiento" aria-label="Movimiento del banco">
                  <p className="small">
                    Con el movimiento del banco del <strong>{dmy(mov.fecha_valor)}</strong>{" "}
                    (fecha valor) · <strong>{eur(mov.importe_factura)}</strong>
                    {mov.cuenta.nombre ? ` · ${mov.cuenta.nombre}` : ""}
                    {mov.concepto ? <> · <span className="muted">«{mov.concepto}»</span></> : null}
                  </p>
                  <p className="muted small">
                    La fecha del cobro es la fecha valor del banco y la cuenta, la de ese banco.
                    {success || soloConciliar || sinSalida ? null : (
                      <>
                        {" "}
                        <button type="button" className="button small tertiary"
                          disabled={busy} onClick={() => setSinMovimiento(true)}>
                          Registrar sin este movimiento
                        </button>
                      </>
                    )}
                  </p>
                  {sinContrapartida ? (
                    <p className="form-error small" role="alert">
                      La cuenta {mov.cuenta.nombre} no tiene contrapartida de cobro: enlázala en
                      Conciliación → Cuentas antes.
                    </p>
                  ) : null}
                  {soloConciliar ? (
                    <p className="form-info small" role="note">
                      Este cobro ya consta en FACTUSOL: al confirmar solo se concilia el movimiento
                      con la factura; no se registra otra vez.
                    </p>
                  ) : null}
                  {info.hoja ? (
                    <p className="form-info small erp-cobro-hoja" role="note">
                      También se escribirá <strong>{info.hoja.factura}</strong> en la columna
                      «factura» de la fila de este movimiento en la hoja de Drive de{" "}
                      <strong>{info.hoja.cuenta}</strong>
                      {info.hoja.pestana ? ` (pestaña ${info.hoja.pestana})` : ""}, solo si esa celda
                      está vacía. Si la hoja falla, el cobro en FACTUSOL no se deshace.
                    </p>
                  ) : null}
                </div>
              ) : null}
              {dobleCobro ? (
                <>
                  <p className="form-error small" role="alert">{dobleCobro}</p>
                  <label className="field-toggle">
                    <input
                      type="checkbox" aria-label="Es otro pago: registrarlo igualmente"
                      checked={otroPago} disabled={busy}
                      onChange={(e) => setOtroPago(e.target.checked)}
                    />
                    <span>Es otro pago distinto: registrarlo igualmente.</span>
                  </label>
                </>
              ) : null}
              {pendiente ? (
                <div className="modal-form">
                  <label>
                    <span>Cuenta / contrapartida (donde entra el dinero)</span>
                    <select
                      aria-label="Cuenta del cobro"
                      value={cuenta}
                      disabled={busy || !!success || !!mov}
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
                  {info.suggested_cuenta && info.suggested_reason ? (
                    /* Por qué se propone esa cuenta (regla tienda × método de
                       pago de Woo, cuenta de la serie o pago apuntado). Es
                       solo una sugerencia: se puede elegir otra. */
                    <p className="muted small erp-cobro-sugerida" role="note">
                      {cuenta === info.suggested_cuenta.codigo ? "Sugerida" : `Sugerida: ${info.suggested_cuenta.codigo} · ${info.suggested_cuenta.nombre}`}
                      {" "}por: {info.suggested_reason}
                    </p>
                  ) : null}
                  <label>
                    <span>Fecha del cobro</span>
                    <input
                      type="date"
                      aria-label="Fecha del cobro"
                      value={fecha}
                      disabled={busy || !!success || !!mov}
                      onChange={(e) => setFecha(e.target.value)}
                    />
                  </label>
                  <label>
                    <span>Forma de pago</span>
                    <select
                      aria-label="Forma de pago"
                      value={forma}
                      disabled={busy || !!success || !!mov}
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
                    <span>Importe del cobro</span>
                    <input
                      type="number" inputMode="decimal" step="0.01" min="0.01"
                      aria-label="Importe del cobro"
                      value={importe}
                      disabled={busy || !!success || !!mov}
                      onChange={(e) => setImporte(e.target.value)}
                    />
                  </label>
                  {importeExcede ? (
                    <p className="form-error small" role="alert">
                      No puede ser más que lo pendiente en FACTUSOL ({eur(saldo)}).
                    </p>
                  ) : parcial ? (
                    <p className="form-info small" role="note">
                      Cobro parcial: la factura quedará con {eur((saldo ?? 0) - importeNum)} pendientes.
                    </p>
                  ) : null}
                  <label>
                    <span>Observaciones (opcional)</span>
                    <input
                      type="text"
                      aria-label="Observaciones del cobro"
                      maxLength={255}
                      value={observaciones}
                      disabled={busy || !!success || !!mov}
                      onChange={(e) => setObservaciones(e.target.value)}
                    />
                  </label>
                  {soloConciliar ? null : <p className="muted small erp-cobro-summary">
                    Se escribirá <strong>1 línea de cobro en F_LCO</strong> de la factura{" "}
                    {invoice?.numero}: <strong>{importeOk ? eur(importeNum) : "—"}</strong> con fecha{" "}
                    {fecha || "—"} en la cuenta <strong>{cuentaNombre || "—"}</strong>
                    {forma ? ` (${forma})` : ""}, y la factura pasará a{" "}
                    {parcial ? "cobro parcial (ESTFAC=1)" : "cobrada (ESTFAC=2)"}.
                    No se toca ninguna otra cosa en FACTUSOL.
                  </p>}
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
          {hoja ? (
            <p className={hoja.ok ? "form-info small" : "form-error small"}
               role={hoja.ok ? "status" : "alert"} aria-label="La hoja de Drive">
              {hoja.texto}
            </p>
          ) : null}
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
              {soloConciliar
                ? (busy ? "Conciliando…" : "Conciliar")
                : (busy ? "Registrando…" : "Registrar cobro")}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
