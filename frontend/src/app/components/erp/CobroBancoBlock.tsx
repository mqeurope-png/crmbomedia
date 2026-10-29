"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getOrderCobroBanco,
  type BankConfidence,
  type CobroBanco,
  type CobroBancoCandidato,
  type CobroBancoConciliado,
  type CobroBancoMovimiento,
} from "../../lib/erpApi";
import { RegistrarCobroModal } from "./RegistrarCobroModal";

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
 *  abre LA herramienta de cobro (`RegistrarCobroModal`, la misma de
 *  Conciliación y Documentos) con ese movimiento: concilia, registra el cobro
 *  en FACTUSOL con la FECHA VALOR del banco y la contrapartida de su cuenta, y
 *  pone la factura en la hoja de Drive del banco; antes, lo dice todo y se
 *  confirma.
 *
 *  Sin movimiento, no se dice nada. Varios candidatos: se enseñan todos y no
 *  se preselecciona ninguno. Menor que lo pendiente: es un cobro parcial y se
 *  dice. Si su cobro ya está en FACTUSOL, solo se concilia («Conciliar con
 *  esta factura»). Ya cobrada: no se propone nada; como mucho, con qué
 *  movimiento se cobró. */
export function CobroBancoBlock({
  orderId, orderNumber, refreshKey = 0, canConfirm, canConciliar, onDone,
}: {
  orderId: string;
  /** Para el título del diálogo de cobro. */
  orderNumber?: string;
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
  // El movimiento elegido: abre el diálogo de cobro con él.
  const [elegido, setElegido] = useState<CobroBancoCandidato | CobroBancoConciliado | null>(null);
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

  const sueltos = data && data.aplica ? data.cobros_sueltos ?? [] : [];
  const mensajes = (
    <>
      {sueltos.length && data?.aplica && data.estado !== "cobrada" ? (
        <div className="form-info small" role="note" aria-label="Cobros ya en FACTUSOL">
          En FACTUSOL esta factura ya tiene {sueltos.length === 1 ? "un cobro" : `${sueltos.length} cobros`}{" "}
          que no {sueltos.length === 1 ? "es" : "son"} de ningún movimiento del banco:{" "}
          {sueltos.map((x) => `${dmy(x.fecha)} · ${eur(x.importe)}${x.cuenta ? ` · cuenta ${x.cuenta}` : ""}`)
            .join("; ")}. Si alguno es el pago de un movimiento de aquí, no lo registres otra vez.
        </div>
      ) : null}
    </>
  );

  // El diálogo de cobro se queda abierto aunque al terminar la factura pase a
  // cobrada (si no, se llevaría el resultado y lo de la hoja).
  const panel = elegido ? (
    <RegistrarCobroModal
      orderId={orderId}
      orderNumber={orderNumber ?? (data && data.aplica ? data.factura.numero : undefined)}
      movimientoId={elegido.id}
      onClose={() => { setElegido(null); setTick((n) => n + 1); }}
      onDone={() => { setTick((n) => n + 1); onDone?.(); }}
    />
  ) : null;

  if (!data || !data.aplica || data.estado === "nada") return panel;

  if (data.estado === "cobrada") {
    if (data.conciliados.length === 0) return panel;
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
        {panel}
      </div>
    );
  }

  const saldo = data.factura.saldo_pendiente;

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
