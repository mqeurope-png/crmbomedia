"use client";

import { useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";
import {
  FACTURA_ENVIADA_MEDIO_LABEL,
  desmarcarFacturaEnviada,
  marcarFacturaEnviada,
  type FacturaEnviadaMedio,
  type InvoiceSent,
  type OrderDetail,
} from "../../lib/erpApi";

function dmy(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
}

/** Hoy en la zona del navegador (`YYYY-MM-DD`): la fecha por defecto. */
function hoy(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Cómo consta enviada, en una frase. */
export function facturaEnviadaTexto(s: InvoiceSent): string {
  if (s.via === "email") {
    return `Factura enviada al cliente por email el ${dmy(s.at)}`;
  }
  if (s.via === "manual") {
    const medio = s.medio ? FACTURA_ENVIADA_MEDIO_LABEL[s.medio].toLowerCase() : "por otro medio";
    return `Factura enviada al cliente el ${dmy(s.at)} (${medio}, marcada a mano`
      + `${s.by ? ` por ${s.by}` : ""})`;
  }
  return `Factura enviada al cliente el ${dmy(s.at)} (según la hoja de seguimiento)`;
}

/** El paso 6 de la línea de vida, «Factura enviada»: va ANTES del cobro (la
 *  factura se manda al cliente y luego se espera su pago) y ya no es
 *  opcional. Enviada → la fecha y cómo (email desde BoHub, marcada a mano u
 *  hoja de seguimiento). Sin enviar → «Enviar factura al cliente» y «Ya
 *  enviada…» (se mandó en papel, desde Gmail, con el albarán…), salvo que el
 *  paso sea el actual: entonces esos botones ya están en «Siguiente paso». */
export function FacturaEnviadaHito({
  order, hasInvoice, isNow, emailBusy, onSend, onMark, onUndone,
}: {
  order: OrderDetail;
  hasInvoice: boolean;
  /** Es el paso actual: los botones van en «Siguiente paso». */
  isNow: boolean;
  emailBusy: boolean;
  /** Sin él (quien no manda facturas al cliente) no hay botón de enviar. */
  onSend?: () => void;
  /** Sin él (quien no gestiona el pedido) no se puede marcar a mano. */
  onMark?: () => void;
  /** Tras deshacer la marca a mano. */
  onUndone?: (o: OrderDetail) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sent = order.invoice_sent ?? null;

  async function deshacer() {
    if (!window.confirm("¿Quitar la marca «ya enviada»? El paso vuelve a quedar pendiente.")) return;
    setBusy(true);
    setError(null);
    try {
      onUndone?.(await desmarcarFacturaEnviada(order.id));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo quitar la marca."));
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    const destinatarios = sent.to?.length ? `Enviada a ${sent.to.join(", ")}` : undefined;
    return (
      <div className="erp-flow-emailed-wrap">
        <p className="erp-flow-emailed is-sent" title={destinatarios ?? sent.nota ?? undefined}>
          {facturaEnviadaTexto(sent)}
        </p>
        {sent.via === "manual" && onMark ? (
          <button
            type="button" className="button small secondary" disabled={busy}
            onClick={() => void deshacer()}
          >
            Quitar la marca
          </button>
        ) : null}
        {error ? <p className="form-error small">{error}</p> : null}
      </div>
    );
  }
  if (!hasInvoice) {
    return (
      <p className="erp-flow-emailed is-muted">
        Cuando se emita la factura podrás enviarla al cliente, o marcarla como enviada.
      </p>
    );
  }
  return (
    <div className="erp-flow-emailed is-unsent">
      <span>Factura sin enviar al cliente</span>
      {!isNow ? (
        <FacturaEnviadaBotones emailBusy={emailBusy} onSend={onSend} onMark={onMark} />
      ) : null}
    </div>
  );
}

/** «Enviar factura al cliente» + «Ya enviada…»: en el hito y en «Siguiente
 *  paso» (cuando el paso actual es el envío de la factura). */
export function FacturaEnviadaBotones({
  emailBusy, onSend, onMark, label = "Enviar factura al cliente",
}: {
  emailBusy: boolean;
  onSend?: () => void;
  onMark?: () => void;
  label?: string;
}) {
  if (!onSend && !onMark) return null;
  return (
    <span className="erp-flow-emailed-btns">
      {onSend ? (
        <button
          type="button" className="button small" disabled={emailBusy}
          onClick={() => void onSend()}
        >
          {emailBusy ? "Localizando…" : label}
        </button>
      ) : null}
      {onMark ? (
        <button
          type="button" className="button small secondary"
          title="Ya se la mandaste por otro lado (papel, Gmail, con el albarán…)"
          onClick={onMark}
        >
          Ya enviada…
        </button>
      ) : null}
    </span>
  );
}

/** «Ya enviada»: marca a mano que la factura llegó al cliente por fuera de
 *  BoHub. Pide cuándo y cómo; queda en Auditoría con quién. No manda nada ni
 *  escribe en FACTUSOL. */
export function MarcarFacturaEnviadaModal({
  order, factura, onClose, onDone,
}: {
  order: OrderDetail;
  factura: string;
  onClose: () => void;
  onDone: (o: OrderDetail) => void;
}) {
  const [fecha, setFecha] = useState(hoy());
  const [medio, setMedio] = useState<FacturaEnviadaMedio>("papel");
  const [nota, setNota] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose, disabled: busy });
  const title = `Factura ${factura} ya enviada · ${order.order_number}`;
  const futura = fecha > hoy();

  async function guardar() {
    setBusy(true);
    setError(null);
    try {
      onDone(await marcarFacturaEnviada(order.id, {
        fecha: fecha || null, medio, nota: nota.trim() || null,
      }));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo marcar la factura como enviada."));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={title} {...overlayProps}>
      <div className="modal-dialog">
        <div className="modal-header">
          <h2>{title}</h2>
          <ModalCloseButton onClose={requestClose} disabled={busy} placement="header" />
        </div>
        <div className="modal-body">
          <p className="muted small">
            Para cuando la factura ya le llegó al cliente por otro lado. El paso «Factura
            enviada» queda hecho con esta fecha; se guarda quién lo marca y cuándo (Auditoría).
            No se manda nada al cliente.
          </p>
          <div className="modal-form">
            <label>
              <span>Fecha de envío</span>
              <input
                type="date" aria-label="Fecha de envío" value={fecha} max={hoy()}
                disabled={busy} onChange={(e) => setFecha(e.target.value)}
              />
            </label>
            <label>
              <span>Cómo se envió</span>
              <select
                aria-label="Cómo se envió" value={medio} disabled={busy}
                onChange={(e) => setMedio(e.target.value as FacturaEnviadaMedio)}
              >
                {(Object.keys(FACTURA_ENVIADA_MEDIO_LABEL) as FacturaEnviadaMedio[]).map((k) => (
                  <option key={k} value={k}>{FACTURA_ENVIADA_MEDIO_LABEL[k]}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Nota (opcional)</span>
              <input
                type="text" aria-label="Nota" maxLength={255} value={nota} disabled={busy}
                onChange={(e) => setNota(e.target.value)}
              />
            </label>
          </div>
          {futura ? <p className="form-error small">La fecha no puede ser futura.</p> : null}
          {error ? <p className="form-error">{error}</p> : null}
        </div>
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button
            type="button" className="button" disabled={busy || !fecha || futura}
            onClick={() => void guardar()}
          >
            {busy ? "Guardando…" : "Marcar como enviada"}
          </button>
        </div>
      </div>
    </div>
  );
}
