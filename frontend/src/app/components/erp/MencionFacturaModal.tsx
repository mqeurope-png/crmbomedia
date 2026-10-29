"use client";

import { useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";
import {
  aplicarMencion,
  getMencionesLegales,
  previewMencion,
  type MencionInput,
  type MencionLegal,
  type MencionPlan,
} from "../../lib/erpApi";
import { idiomaMencion, mencionParaIdioma } from "../../lib/mencionesIdioma";

const LIBRE = "__libre__";

/** «Añadir mención legal» a una factura YA emitida (Ceuta y Melilla,
 *  Canarias, Fuera de la UE… o un texto libre). Escribe en FACTUSOL, así que
 *  va en dos pasos: vista previa con el texto exacto y en qué observación de
 *  la factura (la primera vacía; nunca se pisa una que tenga texto), y
 *  confirmación. Avisa si la factura ya se envió (el PDF que tiene el cliente
 *  no la lleva). Queda en Auditoría. */
export function MencionFacturaModal({
  orderId, orderNumber, factura, idioma, onClose, onDone,
}: {
  orderId: string;
  orderNumber: string;
  factura: string;
  /** El idioma del pedido: la mención va en él si tiene texto revisado. */
  idioma?: string | null;
  onClose: () => void;
  onDone?: () => void;
}) {
  const [items, setItems] = useState<MencionLegal[] | null>(null);
  const [max, setMax] = useState(255);
  const [eleccion, setEleccion] = useState("");
  const [texto, setTexto] = useState("");
  const [plan, setPlan] = useState<MencionPlan | null>(null);
  const [confirmo, setConfirmo] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hecho, setHecho] = useState<string | null>(null);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose, disabled: busy });

  useEffect(() => {
    let alive = true;
    Promise.resolve()
      .then(() => getMencionesLegales())
      .then((r) => { if (alive) { setItems(r.items); setMax(r.max); } })
      .catch((e) => { if (alive) setError(extractErrorMessage(e, "No se pudo cargar la lista de menciones.")); });
    return () => { alive = false; };
  }, []);

  const elegida = items?.find((m) => m.id === eleccion) ?? null;
  const lengua = idiomaMencion(idioma);
  const aplicable = elegida ? mencionParaIdioma(elegida, lengua) : null;
  const input: MencionInput = eleccion === LIBRE
    ? { texto: texto.trim() }
    : { mencion_id: eleccion || null };
  const listo = eleccion === LIBRE ? texto.trim().length > 0 : !!elegida;

  function cambiar(next: string) {
    setEleccion(next);
    setPlan(null);
    setConfirmo(false);
    setError(null);
  }

  async function verPrevia() {
    setBusy(true);
    setError(null);
    try {
      setPlan(await previewMencion(orderId, input));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo preparar la vista previa."));
    } finally {
      setBusy(false);
    }
  }

  async function aplicar() {
    if (!plan?.campo) return;
    setBusy(true);
    setError(null);
    try {
      const r = await aplicarMencion(orderId, { ...input, campo: plan.campo });
      setHecho(`Mención añadida a la factura ${r.factura}. Sale en su PDF desde ahora.`
        + (r.ya_enviada ? " El cliente ya tenía la factura: reenvíasela si la necesita." : ""));
      onDone?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo añadir la mención a la factura."));
    } finally {
      setBusy(false);
    }
  }

  const title = `Añadir mención legal a la factura ${factura} · ${orderNumber}`;
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={title} {...overlayProps}>
      <div className="modal-dialog">
        <div className="modal-header">
          <h2>{title}</h2>
          <ModalCloseButton onClose={requestClose} disabled={busy} placement="header" />
        </div>
        <div className="modal-body">
          <p className="muted small">
            Se escribe en las observaciones de la factura en FACTUSOL (salen en su PDF). No
            cambia importes, IVA ni numeración. Si a la factura le faltaba del todo la mención
            de exención, puede hacer falta una rectificativa: eso lo decide la gestoría.
          </p>
          {items ? (
            <div className="modal-form">
              <label>
                <span>Mención</span>
                <select
                  aria-label="Mención legal" value={eleccion} disabled={busy || !!hecho}
                  onChange={(e) => cambiar(e.target.value)}
                >
                  <option value="">— elige una —</option>
                  {items.map((m) => (
                    <option key={m.id} value={m.id} disabled={!mencionParaIdioma(m, lengua)}>
                      {m.nombre}{!m.texto ? " (sin texto)" : !m.revisado ? " (sin revisar)" : ""}
                    </option>
                  ))}
                  <option value={LIBRE}>Texto libre…</option>
                </select>
              </label>
              {items.some((m) => !m.revisado) ? (
                <p className="muted small">
                  Las que salen «sin revisar» son textos de referencia: se revisan y se marcan en
                  Configuración ERP antes de poder usarlas.
                </p>
              ) : null}
              {aplicable ? (
                <>
                  <p className="erp-mencion-texto">«{aplicable.texto}»</p>
                  {aplicable.aviso && !plan ? (
                    <p className="form-info small" role="alert">⚠ {aplicable.aviso}</p>
                  ) : null}
                </>
              ) : null}
              {eleccion === LIBRE ? (
                <label>
                  <span>Texto ({texto.length}/{max})</span>
                  <textarea
                    aria-label="Texto de la mención" rows={3} maxLength={max} value={texto}
                    disabled={busy || !!hecho}
                    onChange={(e) => { setTexto(e.target.value); setPlan(null); setConfirmo(false); }}
                  />
                </label>
              ) : null}
            </div>
          ) : null}
          {plan ? (
            <div className="erp-mencion-plan" aria-label="Vista previa">
              {plan.ya_esta ? (
                <p className="form-info">La factura {plan.factura} ya lleva esa mención: no hay nada que hacer.</p>
              ) : plan.campo ? (
                <>
                  <p>
                    Se escribirá en <strong>{plan.campo === "OB1FAC" ? "Observaciones 1" : "Observaciones 2"}</strong>{" "}
                    ({plan.campo}) de la factura {plan.factura}, tal cual:
                  </p>
                  <p className="erp-mencion-texto">«{plan.texto}»</p>
                  {plan.actuales.OB1FAC || plan.actuales.OB2FAC ? (
                    <p className="muted small">
                      Ya tiene: {[plan.actuales.OB1FAC, plan.actuales.OB2FAC].filter(Boolean)
                        .map((t) => `«${t}»`).join(" · ")} (no se toca).
                    </p>
                  ) : null}
                </>
              ) : null}
              {plan.avisos.map((a) => <p key={a} className="form-info small" role="alert">⚠ {a}</p>)}
              {plan.campo && !plan.ya_esta && !hecho ? (
                <label className="field-toggle">
                  <input
                    type="checkbox" aria-label="Confirmo la mención" checked={confirmo} disabled={busy}
                    onChange={(e) => setConfirmo(e.target.checked)}
                  />
                  <span>Confirmo que quiero escribir esta mención en la factura emitida.</span>
                </label>
              ) : null}
            </div>
          ) : null}
          {error ? <p className="form-error">{error}</p> : null}
          {hecho ? <p className="form-success" role="status">{hecho}</p> : null}
        </div>
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            {hecho ? "Cerrar" : "Cancelar"}
          </button>
          {!hecho && !plan ? (
            <button type="button" className="button" disabled={!listo || busy} onClick={() => void verPrevia()}>
              {busy ? "Consultando FACTUSOL…" : "Ver cómo queda"}
            </button>
          ) : null}
          {!hecho && plan?.campo && !plan.ya_esta ? (
            <button type="button" className="button" disabled={!confirmo || busy} onClick={() => void aplicar()}>
              {busy ? "Escribiendo…" : "Añadir a la factura"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
