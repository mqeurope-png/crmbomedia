"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError } from "../../lib/api";
import {
  convertOferta,
  getOferta,
  getOfertaConvertStatus,
  type Oferta,
  type OfertaConvertResult,
} from "../../lib/erpApi";
import { extractErrorMessage, formatFastApiDetail } from "../../lib/errors";
import { ErpModalShell } from "./ErpModalShell";

const POLL_MS = 2000;
const POLL_MAX_TRIES = 30;  // ~60 s: el worker de FACTUSOL es serie, puede haber cola

type Outcome =
  | { status: "finished"; result: OfertaConvertResult }
  | { status: "failed"; error: string }
  | { status: "timeout" };

/** Sondea el job de conversión (mismo patrón que `pollQuoteJob`, contra
 *  `getOfertaConvertStatus`). */
async function pollConvert(jobId: string): Promise<Outcome> {
  for (let i = 0; i < POLL_MAX_TRIES; i++) {
    const s = await getOfertaConvertStatus(jobId);
    if (s.status === "finished") return { status: "finished", result: s.result };
    if (s.status === "failed") {
      return { status: "failed", error: s.error || "La conversión falló en FACTUSOL." };
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
  return { status: "timeout" };
}

type Unlinked = {
  company_id: string | null;
  contact_id: string | null;
  company_name: string | null;
  detail: string;
};
type SinArticulo = { posiciones: number[]; detail: string };
type Phase = "confirm" | "working" | "done" | "timeout";

/** «Convertir en presupuesto»: el ÚNICO camino de las ofertas que escribe en
 *  FACTUSOL, y solo tras la confirmación explícita. Antes de encolar el
 *  backend puede rechazar: 409 `company_not_linked` → se ofrece el flujo de
 *  siempre (#502): la ficha de la empresa para vincular o crear el cliente, o
 *  la del contacto si no tiene empresa; 422 `lineas_sin_articulo` → las
 *  posiciones y «Editar líneas». Encolada (202), se sondea el job hasta
 *  `finished` / `failed`; en éxito enseña el nº del presupuesto con enlace a
 *  Proformas y llama a `onConverted` con la oferta recargada. */
export function ConvertOfertaDialog({
  oferta,
  onClose,
  onConverted,
  onEditLines,
}: {
  oferta: Oferta;
  onClose: () => void;
  onConverted: (oferta: Oferta) => void;
  /** Al pulsar «Editar líneas» tras un 422: el padre abre el editor con esas
   *  posiciones resaltadas. Sin él, el diálogo solo se cierra (`onClose`). */
  onEditLines?: (posiciones: number[]) => void;
}) {
  // Nº de líneas: la lista no las trae, se piden (solo lectura, informativo).
  const [lineas, setLineas] = useState<number | null>(oferta.lineas ? oferta.lineas.length : null);
  const [phase, setPhase] = useState<Phase>("confirm");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [unlinked, setUnlinked] = useState<Unlinked | null>(null);
  const [sinArticulo, setSinArticulo] = useState<SinArticulo | null>(null);
  const [result, setResult] = useState<OfertaConvertResult | null>(null);

  const ofertaId = oferta.id;
  const hasLines = Boolean(oferta.lineas);
  useEffect(() => {
    if (hasLines) return;
    let alive = true;
    getOferta(ofertaId)
      .then((full) => { if (alive) setLineas(full.lineas?.length ?? 0); })
      .catch(() => { /* informativo: sin el nº de líneas se convierte igual */ });
    return () => { alive = false; };
  }, [ofertaId, hasLines]);

  const working = phase === "working";

  async function confirm() {
    setPhase("working");
    setError(null);
    setUnlinked(null);
    setSinArticulo(null);
    setNotice(`Creando el presupuesto de la oferta ${oferta.numero} en FACTUSOL… La cola va en serie y puede tardar.`);
    try {
      const r = await convertOferta(oferta.id);
      const outcome = await pollConvert(r.job_id);
      if (outcome.status === "finished") {
        setResult(outcome.result);
        setNotice(null);
        setPhase("done");
        // La oferta recargada (estado convertida + nº del presupuesto); si la
        // recarga falla, se compone con lo que devolvió el job.
        const fresh = await getOferta(oferta.id).catch(() => null);
        onConverted(fresh ?? {
          ...oferta,
          estado: "convertida",
          estado_guardado: "convertida",
          factusol: {
            serie: outcome.result.serie, codpre: outcome.result.codpre,
            numero: outcome.result.factusol_numero, ejercicio: outcome.result.ejercicio,
          },
        });
      } else if (outcome.status === "failed") {
        setNotice(null);
        setError(`No se pudo convertir la oferta ${oferta.numero}: ${outcome.error}`);
        setPhase("confirm");
      } else {
        setNotice(
          "La conversión sigue en curso en FACTUSOL (la cola va en serie). Cierra y "
          + "recarga la lista en unos segundos: la oferta aparecerá como convertida.",
        );
        setPhase("timeout");
      }
    } catch (e) {
      setNotice(null);
      setPhase("confirm");
      if (e instanceof ApiError && e.code === "company_not_linked") {
        const d = (e.detail ?? {}) as Partial<Unlinked>;
        setUnlinked({
          company_id: d.company_id ?? null,
          contact_id: d.contact_id ?? oferta.contact_id,
          company_name: d.company_name ?? oferta.empresa?.nombre ?? null,
          // El motivo viene en `detail.detail` (HTTPException con objeto).
          detail: formatFastApiDetail(e.detail, "La empresa no está vinculada a un cliente de FACTUSOL."),
        });
        return;
      }
      if (e instanceof ApiError && e.code === "lineas_sin_articulo") {
        const d = (e.detail ?? {}) as { posiciones?: number[] };
        setSinArticulo({
          posiciones: d.posiciones ?? [],
          detail: formatFastApiDetail(e.detail, "Hay líneas sin artículo de FACTUSOL."),
        });
        return;
      }
      setError(extractErrorMessage(e, "No se pudo convertir la oferta."));
    }
  }

  function editLines() {
    if (onEditLines) onEditLines(sinArticulo?.posiciones ?? []);
    else onClose();
  }

  const cliente = oferta.empresa?.nombre ?? oferta.contacto?.nombre ?? "—";

  return (
    <ErpModalShell
      label="Convertir oferta en presupuesto"
      title={<>Convertir la oferta <span className="mono">{oferta.numero}</span> en presupuesto</>}
      onClose={onClose}
      disabled={working}
    >
      {phase === "done" && result ? (
        <>
          <p className="form-success" role="status">
            Presupuesto <strong className="mono">{result.factusol_numero}</strong> creado en
            FACTUSOL ({result.lines} {result.lines === 1 ? "línea" : "líneas"},{" "}
            {result.total.toFixed(2)} €).
          </p>
          {result.warning ? <p className="form-error" role="alert">{result.warning}</p> : null}
          <div className="modal-actions">
            <Link href="/erp/proformas" className="button secondary">Ver en Proformas</Link>
            <button type="button" className="button" onClick={onClose}>Cerrar</button>
          </div>
        </>
      ) : phase === "timeout" ? (
        <>
          {notice ? <p className="form-info" role="status">{notice}</p> : null}
          <div className="modal-actions">
            <button type="button" className="button" onClick={onClose}>Cerrar</button>
          </div>
        </>
      ) : (
        <>
          <p>
            Crea el presupuesto en FACTUSOL en la serie{" "}
            <strong>{oferta.serie} · {oferta.emisora}</strong> con{" "}
            <strong>{lineas === null ? "…" : lineas}</strong>{" "}
            {lineas === 1 ? "línea" : "líneas"}, a nombre de <strong>{cliente}</strong>
            {" "}({oferta.total.toFixed(2)} € {oferta.con_iva ? "IVA incl." : "sin IVA"}).
          </p>
          <p className="form-error">
            Hazlo <strong>solo si el cliente ha aceptado</strong> la oferta: es la única
            acción de las ofertas que escribe en FACTUSOL.
          </p>

          {unlinked ? (
            <div className="form-info" role="alert">
              <p>{unlinked.detail}</p>
              {unlinked.company_id ? (
                <>
                  <p className="muted small">
                    La empresa <strong>{unlinked.company_name ?? ""}</strong> no está
                    vinculada a un cliente de FACTUSOL. Vincúlala (o créalo) desde su
                    ficha, en el panel FACTUSOL, y vuelve a convertir.
                  </p>
                  <Link href={`/companies/${unlinked.company_id}`} className="button small">
                    Vincular o crear el cliente en FACTUSOL
                  </Link>
                </>
              ) : (
                <>
                  <p className="muted small">
                    El contacto no tiene empresa: asígnale una (o créasela) desde su
                    ficha y vuelve a convertir.
                  </p>
                  {unlinked.contact_id ? (
                    <Link href={`/contacts/${unlinked.contact_id}`} className="button small">
                      Ir a la ficha del contacto
                    </Link>
                  ) : null}
                </>
              )}
            </div>
          ) : null}

          {sinArticulo ? (
            <div className="form-info" role="alert">
              <p>{sinArticulo.detail}</p>
              <p className="muted small">
                Líneas sin artículo: <strong>{sinArticulo.posiciones.join(", ") || "—"}</strong>.
                Elige el artículo del catálogo en cada una y vuelve a convertir.
              </p>
              <button type="button" className="button small" onClick={editLines}>
                Editar líneas
              </button>
            </div>
          ) : null}

          {error ? <p className="form-error" role="alert">{error}</p> : null}
          {notice ? <p className="form-info" role="status">{notice}</p> : null}

          <div className="modal-actions">
            <button type="button" className="button secondary" onClick={onClose} disabled={working}>
              Cancelar
            </button>
            <button type="button" className="button" onClick={() => void confirm()}
                    disabled={working || Boolean(unlinked) || Boolean(sinArticulo)}>
              {working ? "Convirtiendo…" : "Convertir en presupuesto"}
            </button>
          </div>
        </>
      )}
    </ErpModalShell>
  );
}
