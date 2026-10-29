"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  confirmarCobroBanco,
  getOrderCobroBanco,
  waitForInvoiceCollectionJob,
  type BankConfidence,
  type CobroBanco,
  type CobroBancoCandidato,
  type CobroBancoConciliado,
  type CobroBancoMovimiento,
} from "../../lib/erpApi";

/** DD/MM/AAAA del tramo de fecha de un ISO (sin zona horaria). */
function dmy(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
}

function eur(n: number): string {
  return `${n.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
}

/** Las mismas pastillas que Conciliación. */
const CONFIANZA: Record<BankConfidence, { label: string; tone: string }> = {
  alta: { label: "Confianza alta", tone: "ok" },
  media: { label: "Confianza media", tone: "warn" },
  baja: { label: "Confianza baja", tone: "muted" },
};

/** «Ver el movimiento»: la Conciliación con solo ese movimiento. */
export function movimientoHref(id: string): string {
  return `/erp/conciliacion?movimiento=${encodeURIComponent(id)}`;
}

/** Una línea con lo que se vio en el banco: fecha valor · importe · cuenta ·
 *  «concepto». */
function MovimientoLinea({ m, importe }: { m: CobroBancoMovimiento; importe?: number }) {
  return (
    <p className="erp-cobro-banco-mov">
      <strong>{dmy(m.fecha_valor)}</strong> · <strong>{eur(importe ?? m.importe)}</strong>
      {m.cuenta.nombre ? <> · {m.cuenta.nombre}</> : null}
      {m.concepto ? <> · <span className="erp-cobro-banco-concepto">«{m.concepto}»</span></> : null}
    </p>
  );
}

/** El cobro, propuesto desde el banco (paso «Cobro» de la línea de vida).
 *
 *  Busca el movimiento del banco que pagó la factura del pedido —el mismo
 *  casador que Conciliación, empezando por el nº de factura en el concepto— y
 *  lo enseña con su confianza y el porqué. «Marcar cobrado con esta fecha»
 *  concilia el movimiento con la factura, registra el cobro en FACTUSOL con
 *  la FECHA VALOR del banco y la contrapartida de su cuenta, y pone la factura
 *  en la hoja de Drive del banco; antes, se dice todo eso y se confirma.
 *
 *  Sin movimiento, no se dice nada. Varios candidatos: se enseñan todos y no
 *  se preselecciona ninguno. Menor que lo pendiente: es un cobro parcial y se
 *  dice. Ya cobrada: no se propone nada; como mucho, con qué movimiento se
 *  cobró. */
export function CobroBancoBlock({
  orderId, refreshKey = 0, canConfirm, onDone,
}: {
  orderId: string;
  /** Sube con cada recarga de la ficha: se vuelve a pedir. */
  refreshKey?: number;
  /** Quien registra cobros (`erp.cobro.register`): ve el botón. */
  canConfirm: boolean;
  /** Tras registrar el cobro (recargar la ficha y su cobro en vivo). */
  onDone?: () => void;
}) {
  const [data, setData] = useState<CobroBanco | null>(null);
  const [elegido, setElegido] = useState<CobroBancoCandidato | CobroBancoConciliado | null>(null);
  const [confirmo, setConfirmo] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hecho, setHecho] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    Promise.resolve()
      .then(() => getOrderCobroBanco(orderId))
      .then((r) => { if (alive) setData(r); })
      // Sin datos del banco la ficha sigue igual: el cobro se registra a mano.
      .catch(() => { if (alive) setData(null); });
    return () => { alive = false; };
  }, [orderId, refreshKey, tick]);

  if (!data || !data.aplica || data.estado === "nada") return null;

  if (data.estado === "cobrada") {
    if (data.conciliados.length === 0) return null;
    return (
      <div className="erp-cobro-banco is-done" aria-label="Cobro en el banco">
        <p className="erp-cobro-banco-t">Cobrado con este movimiento del banco:</p>
        {data.conciliados.map((m) => (
          <div key={m.id} className="erp-cobro-banco-item">
            <MovimientoLinea m={m} importe={m.importe_conciliado} />
            <Link href={movimientoHref(m.id)} className="small">Ver el movimiento</Link>
          </div>
        ))}
      </div>
    );
  }

  const saldo = data.factura.saldo_pendiente;
  const numero = data.factura.numero;

  /** Lo que se registraría con ese movimiento (su parte de ESTA factura). */
  function importeDe(m: CobroBancoCandidato | CobroBancoConciliado): number {
    const base = "importe_conciliado" in m ? m.importe_conciliado : m.importe;
    return Math.min(base, saldo);
  }

  async function registrar() {
    if (!elegido) return;
    setBusy(true);
    setError(null);
    try {
      const res = await confirmarCobroBanco(orderId, elegido.id);
      if (res.status === "already") {
        setHecho(`La factura ${numero} ya constaba cobrada en FACTUSOL: no se ha registrado nada.`);
      } else {
        if (!res.job_id) throw new Error("FACTUSOL no devolvió el trabajo del cobro.");
        const st = await waitForInvoiceCollectionJob(res.job_id);
        if (st.status === "pending") {
          setError("El cobro sigue en cola en FACTUSOL: el movimiento ya está conciliado. "
            + "Vuelve a mirar la ficha en unos segundos.");
          return;
        }
        if (st.status === "failed" || (!st.result.registered && st.result.status !== "already")) {
          const motivo = st.status === "failed"
            ? st.error ?? "error"
            : st.result.motivo ?? st.result.status;
          setError(`El movimiento queda conciliado con la ${numero}, pero FACTUSOL no ha `
            + `registrado el cobro (${motivo}). Regístralo con «Registrar cobro en FACTUSOL».`);
          return;
        }
        setHecho(res.parcial
          ? `Cobro parcial de ${eur(res.importe)} registrado con fecha ${dmy(res.movimiento.fecha_valor)}.`
            + ` Quedan ${eur(Math.max(saldo - res.importe, 0))} pendientes de la ${numero}.`
          : `Cobro de ${eur(res.importe)} registrado con fecha ${dmy(res.movimiento.fecha_valor)}:`
            + ` la ${numero} consta cobrada.`);
      }
      setElegido(null);
      setConfirmo(false);
      setTick((n) => n + 1);
      onDone?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo registrar el cobro con ese movimiento."));
    } finally {
      setBusy(false);
    }
  }

  const panel = elegido ? (
    <div className="erp-cobro-banco-confirm" role="group" aria-label="Confirmar el cobro">
      <p>Al confirmar:</p>
      <ul>
        <li>
          se registra en FACTUSOL un cobro de <strong>{eur(importeDe(elegido))}</strong> de la{" "}
          {numero} con fecha <strong>{dmy(elegido.fecha_valor)}</strong> (la fecha valor del
          banco) en la cuenta{" "}
          <strong>
            {elegido.cuenta.contrapartida_codigo ?? "—"}
            {elegido.cuenta.contrapartida_nombre ? ` · ${elegido.cuenta.contrapartida_nombre}` : ""}
          </strong>
          , forma de pago Transferencia;
        </li>
        <li>el movimiento queda conciliado con la {numero}, igual que desde Conciliación;</li>
        <li>la factura se escribe en la hoja de Drive del banco ({elegido.cuenta.nombre ?? "su cuenta"}).</li>
      </ul>
      {importeDe(elegido) < saldo - 0.005 ? (
        <p className="form-info small" role="alert">
          Es un cobro PARCIAL: quedarán {eur(saldo - importeDe(elegido))} pendientes y la factura
          no se marca cobrada.
        </p>
      ) : null}
      {!elegido.cuenta.contrapartida_codigo ? (
        <p className="form-error small" role="alert">
          La cuenta {elegido.cuenta.nombre} no tiene contrapartida de cobro: enlázala en
          Conciliación → Cuentas antes.
        </p>
      ) : null}
      <label className="field-toggle">
        <input
          type="checkbox" aria-label="Confirmo el cobro con este movimiento"
          checked={confirmo} disabled={busy} onChange={(e) => setConfirmo(e.target.checked)}
        />
        <span>Confirmo que este movimiento es el cobro de la {numero}.</span>
      </label>
      <div className="erp-cobro-banco-actions">
        <button
          type="button" className="button small"
          disabled={!confirmo || busy || !elegido.cuenta.contrapartida_codigo}
          onClick={() => void registrar()}
        >
          {busy ? "Registrando…" : "Registrar el cobro"}
        </button>
        <button
          type="button" className="button small secondary" disabled={busy}
          onClick={() => { setElegido(null); setConfirmo(false); setError(null); }}
        >
          Cancelar
        </button>
      </div>
    </div>
  ) : null;

  if (data.estado === "conciliado") {
    return (
      <div className="erp-cobro-banco" aria-label="Cobro en el banco">
        <p className="erp-cobro-banco-t">Lo hemos visto en el banco (ya conciliado):</p>
        {data.conciliados.map((m) => {
          const ofrecer = canConfirm && !m.registrado && data.por_registrar > 0.005 && !elegido;
          return (
            <div key={m.id} className="erp-cobro-banco-item">
              <MovimientoLinea m={m} importe={m.importe_conciliado} />
              {m.importe_conciliado < saldo - 0.005 && !m.registrado ? (
                <p className="muted small">
                  Pago parcial: cubre {eur(m.importe_conciliado)} de {eur(saldo)} pendientes.
                </p>
              ) : null}
              <div className="erp-cobro-banco-actions">
                {m.registrado ? (
                  <span className="muted small">Cobro registrado desde la ficha el {dmy(m.registrado)}</span>
                ) : ofrecer ? (
                  <button type="button" className="button small" onClick={() => setElegido(m)}>
                    Registrar el cobro con esta fecha
                  </button>
                ) : null}
                <Link href={movimientoHref(m.id)} className="button small secondary">
                  Ver el movimiento
                </Link>
              </div>
            </div>
          );
        })}
        {data.por_registrar <= 0.005 && !data.conciliados.some((m) => m.registrado) ? (
          <p className="muted small">Lo conciliado ya consta cobrado en FACTUSOL.</p>
        ) : null}
        {panel}
        {error ? <p className="form-error small">{error}</p> : null}
        {hecho ? <p className="form-success small" role="status">{hecho}</p> : null}
      </div>
    );
  }

  // estado === "candidatos"
  const varios = data.candidatos.length > 1;
  return (
    <div className="erp-cobro-banco" aria-label="Cobro en el banco">
      <p className="erp-cobro-banco-t">
        {varios
          ? `Hay ${data.candidatos.length} movimientos del banco que pueden ser este cobro: elige el bueno.`
          : "Lo hemos visto en el banco:"}
      </p>
      {data.candidatos.map((m) => (
        <div key={m.id} className="erp-cobro-banco-item">
          <MovimientoLinea m={m} />
          <p className="small">
            <span className={`badge ${CONFIANZA[m.confianza].tone}`}>{CONFIANZA[m.confianza].label}</span>{" "}
            <span className="muted">· {m.motivo}</span>
          </p>
          {m.parcial ? (
            <p className="form-info small">
              Es menos que lo pendiente ({eur(saldo)}): sería un cobro parcial, la factura no
              quedaría cobrada.
            </p>
          ) : null}
          {m.excede ? (
            <p className="form-info small">
              Es más que lo pendiente ({eur(saldo)}): si paga varias facturas, repártelo en
              Conciliación.
            </p>
          ) : null}
          <div className="erp-cobro-banco-actions">
            {canConfirm && !m.excede && !elegido ? (
              <button type="button" className="button small" onClick={() => setElegido(m)}>
                {varios ? "Es este: marcar cobrado con esta fecha" : "Marcar cobrado con esta fecha"}
              </button>
            ) : null}
            <Link href={movimientoHref(m.id)} className="button small secondary">
              Ver el movimiento
            </Link>
          </div>
        </div>
      ))}
      {panel}
      {error ? <p className="form-error small">{error}</p> : null}
      {hecho ? <p className="form-success small" role="status">{hecho}</p> : null}
    </div>
  );
}
