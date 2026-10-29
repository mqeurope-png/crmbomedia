"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError } from "../../lib/api";
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

/** Fecha de su línea de cobro si ya está en FACTUSOL (solo candidatos). */
function yaEnFactusol(m: CobroBancoCandidato | CobroBancoConciliado): string | null {
  return "ya_en_factusol" in m ? m.ya_en_factusol : null;
}

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
 *  dice. Si su cobro ya está en FACTUSOL, solo se concilia («Conciliar con
 *  esta factura»). Ya cobrada: no se propone nada; como mucho, con qué
 *  movimiento se cobró. */
export function CobroBancoBlock({
  orderId, refreshKey = 0, canConfirm, canConciliar, onDone,
}: {
  orderId: string;
  /** Sube con cada recarga de la ficha: se vuelve a pedir. */
  refreshKey?: number;
  /** Quien registra cobros (`erp.cobro.register`): ve el botón. */
  canConfirm: boolean;
  /** Quien entra en Conciliación (`erp.conciliacion`): ve «Ver el
   *  movimiento»; sin ella, lo que se arregla allí se pide a un administrador. */
  canConciliar: boolean;
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

  /** «Ver el movimiento», solo para quien puede abrir Conciliación. */
  function verMovimiento(id: string, className = "button small secondary") {
    return canConciliar
      ? <Link href={movimientoHref(id)} className={className}>Ver el movimiento</Link>
      : null;
  }

  const mensajes = (
    <>
      {error ? <p className="form-error small">{error}</p> : null}
      {hecho ? <p className="form-success small" role="status">{hecho}</p> : null}
    </>
  );

  if (!data || !data.aplica || data.estado === "nada") return null;

  if (data.estado === "cobrada") {
    if (data.conciliados.length === 0 && !hecho) return null;
    return (
      <div className="erp-cobro-banco is-done" aria-label="Cobro en el banco">
        {data.conciliados.length ? (
          <p className="erp-cobro-banco-t">Cobrado con este movimiento del banco:</p>
        ) : null}
        {data.conciliados.map((m) => (
          <div key={m.id} className="erp-cobro-banco-item">
            <MovimientoLinea m={m} importe={m.importe_conciliado} />
            {verMovimiento(m.id, "small")}
          </div>
        ))}
        {mensajes}
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
    setHecho(null);
    let res: Awaited<ReturnType<typeof confirmarCobroBanco>>;
    try {
      res = await confirmarCobroBanco(orderId, elegido.id);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo registrar el cobro con ese movimiento."));
      setBusy(false);
      if (e instanceof ApiError && e.code === "cobro_no_encolado") {
        // El movimiento SÍ quedó conciliado (falló mandar el cobro): se cierra
        // el panel y se recarga, para ofrecer registrarlo de nuevo.
        setElegido(null);
        setConfirmo(false);
        setTick((n) => n + 1);
        onDone?.();
      }
      // Cualquier otro error: no se ha conciliado nada y el panel sigue abierto.
      return;
    }
    // El movimiento ya queda conciliado: pase lo que pase en FACTUSOL, se
    // cierra el panel y se recarga (si no, seguiría como candidato).
    const conciliado = `El movimiento queda conciliado con la ${numero}`;
    try {
      if (res.status === "already") {
        const cuando = res.fecha_factusol ? ` (registrado el ${dmy(res.fecha_factusol)}`
          + `${res.cuenta_factusol ? ` en la cuenta ${res.cuenta_factusol}` : ""})` : "";
        setHecho(`El cobro ya constaba en FACTUSOL${cuando}: el movimiento queda conciliado `
          + `con la ${numero}.`
          + (res.otra_cuenta
            ? " Está apuntado en otra cuenta que la de este banco: si era otro pago, registra "
              + "este con «Registrar cobro en FACTUSOL»."
            : ""));
      } else if (!res.job_id) {
        setError(`${conciliado}, pero FACTUSOL no devolvió el trabajo del cobro: vuelve a `
          + "mirar la ficha en unos segundos.");
      } else {
        const st = await waitForInvoiceCollectionJob(res.job_id);
        if (st.status === "pending") {
          setError("El cobro sigue en cola en FACTUSOL: el movimiento ya está conciliado. "
            + "Vuelve a mirar la ficha en unos segundos.");
        } else if (st.status === "finished" && st.result.status === "already") {
          setHecho(`La ${numero} ya constaba cobrada en FACTUSOL: no se ha registrado nada. `
            + `${conciliado}.`);
        } else if (st.status === "failed" || !st.result.registered) {
          const motivo = st.status === "failed"
            ? st.error ?? "error"
            : st.result.motivo ?? st.result.status;
          setError(`${conciliado}, pero FACTUSOL no ha registrado el cobro (${motivo}). `
            + "Vuelve a intentarlo con «Registrar el cobro con esta fecha».");
        } else {
          setHecho(res.parcial
            ? `Cobro parcial de ${eur(res.importe)} registrado con fecha ${dmy(res.movimiento.fecha_valor)}.`
              + ` Quedan ${eur(Math.max(saldo - res.importe, 0))} pendientes de la ${numero}.`
            : `Cobro de ${eur(res.importe)} registrado con fecha ${dmy(res.movimiento.fecha_valor)}:`
              + ` la ${numero} consta cobrada.`);
        }
      }
    } catch (e) {
      setError(`${conciliado}, pero no se ha podido comprobar el cobro en FACTUSOL `
        + `(${extractErrorMessage(e, "error")}): vuelve a mirar la ficha en unos segundos.`);
    } finally {
      setElegido(null);
      setConfirmo(false);
      setBusy(false);
      setTick((n) => n + 1);
      onDone?.();
    }
  }

  // Su cobro ya está en FACTUSOL: confirmar solo concilia.
  const soloConciliar = elegido ? yaEnFactusol(elegido) !== null : false;
  const panel = elegido ? (
    <div className="erp-cobro-banco-confirm" role="group" aria-label="Confirmar el cobro">
      <p>Al confirmar:</p>
      <ul>
        {soloConciliar ? null : (
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
        )}
        <li>el movimiento queda conciliado con la {numero}, igual que desde Conciliación;</li>
        <li>la factura se escribe en la hoja de Drive del banco ({elegido.cuenta.nombre ?? "su cuenta"}).</li>
      </ul>
      {!soloConciliar && importeDe(elegido) < saldo - 0.005 ? (
        <p className="form-info small" role="alert">
          Es un cobro PARCIAL: quedarán {eur(saldo - importeDe(elegido))} pendientes y la factura
          no se marca cobrada.
        </p>
      ) : null}
      {!elegido.cuenta.contrapartida_codigo ? (
        <p className="form-error small" role="alert">
          La cuenta {elegido.cuenta.nombre} no tiene contrapartida de cobro:{" "}
          {canConciliar
            ? "enlázala en Conciliación → Cuentas antes."
            : "pide a un administrador que la enlace en Conciliación → Cuentas."}
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
          {soloConciliar
            ? (busy ? "Conciliando…" : "Conciliar")
            : (busy ? "Registrando…" : "Registrar el cobro")}
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

  const varios = data.candidatos.length > 1;

  /** Un movimiento que puede ser el cobro (sin preseleccionar ninguno). */
  function candidato(m: CobroBancoCandidato) {
    // Con su cobro ya en FACTUSOL, lo pendiente ya lo descuenta: «parcial» y
    // «excede» no aplican, solo se concilia.
    const ya = m.ya_en_factusol;
    const boton = canConfirm && (ya || !m.excede) && !elegido ? (
      <button type="button" className="button small" onClick={() => setElegido(m)}>
        {ya
          ? "Conciliar con esta factura"
          : varios ? "Es este: marcar cobrado con esta fecha" : "Marcar cobrado con esta fecha"}
      </button>
    ) : null;
    const ver = verMovimiento(m.id);
    return (
      <div key={m.id} className="erp-cobro-banco-item">
        <MovimientoLinea m={m} />
        <p className="small">
          <span className={`badge ${CONFIANZA[m.confianza].tone}`}>{CONFIANZA[m.confianza].label}</span>{" "}
          <span className="muted">· {m.motivo}</span>
        </p>
        {ya ? (
          <p className="form-info small">
            Este cobro ya consta en FACTUSOL ({dmy(ya)}
            {m.ya_en_factusol_cuenta ? `, cuenta ${m.ya_en_factusol_cuenta}` : ""}): al
            confirmar solo se concilia el movimiento.
            {m.ya_en_factusol_otra_cuenta
              ? " Ojo: está apuntado en otra cuenta que la de este banco; si fue otro pago "
                + "del mismo importe (efectivo, tarjeta…), registra este con «Registrar cobro "
                + "en FACTUSOL»."
              : ""}
          </p>
        ) : null}
        {!ya && m.parcial ? (
          <p className="form-info small">
            Es menos que lo pendiente ({eur(saldo)}): sería un cobro parcial, la factura no
            quedaría cobrada.
          </p>
        ) : null}
        {!ya && m.excede ? (
          <p className="form-info small">
            Es más que lo pendiente ({eur(saldo)}): si paga varias facturas,{" "}
            {canConciliar
              ? "repártelo en Conciliación."
              : "pide a un administrador que lo reparta en Conciliación."}
          </p>
        ) : null}
        {boton || ver ? <div className="erp-cobro-banco-actions">{boton}{ver}</div> : null}
      </div>
    );
  }

  if (data.estado === "conciliado") {
    return (
      <div className="erp-cobro-banco" aria-label="Cobro en el banco">
        <p className="erp-cobro-banco-t">Lo hemos visto en el banco (ya conciliado):</p>
        {data.conciliados.map((m) => {
          // Solo lo que ni está en FACTUSOL ni en cola.
          const ofrecer = canConfirm && !m.registrado && !m.en_cola
            && data.por_registrar > 0.005 && !elegido;
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
                  <span className="muted small">Cobro registrado en FACTUSOL el {dmy(m.registrado)}</span>
                ) : m.en_cola ? (
                  <span className="muted small">
                    En cola en FACTUSOL: el cobro se registra en unos segundos.
                  </span>
                ) : ofrecer ? (
                  <button type="button" className="button small" onClick={() => setElegido(m)}>
                    Registrar el cobro con esta fecha
                  </button>
                ) : null}
                {verMovimiento(m.id)}
              </div>
            </div>
          );
        })}
        {data.por_registrar <= 0.005 && !data.conciliados.some((m) => m.registrado || m.en_cola) ? (
          <p className="muted small">Lo conciliado ya consta cobrado en FACTUSOL.</p>
        ) : null}
        {data.candidatos.length ? (
          <>
            <p className="erp-cobro-banco-t">
              Lo conciliado no cubre la factura; esto puede ser el resto:
            </p>
            {data.candidatos.map(candidato)}
          </>
        ) : null}
        {panel}
        {mensajes}
      </div>
    );
  }

  // estado === "candidatos"
  return (
    <div className="erp-cobro-banco" aria-label="Cobro en el banco">
      <p className="erp-cobro-banco-t">
        {varios
          ? `Hay ${data.candidatos.length} movimientos del banco que pueden ser este cobro: elige el bueno.`
          : "Lo hemos visto en el banco:"}
      </p>
      {data.candidatos.map(candidato)}
      {panel}
      {mensajes}
    </div>
  );
}
