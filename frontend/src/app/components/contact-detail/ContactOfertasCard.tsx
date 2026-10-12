"use client";

import { ArrowUpRight, FileText } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { listOfertas, type Oferta } from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { OfertaEditorModal } from "../erp/OfertaEditorModal";
import { OfertaEstadoPill } from "../erp/OfertaEstadoPill";

type Props = {
  contactId: string;
  /** Nombre del contacto, para el editor («Nueva oferta» con el contacto fijo). */
  contactName?: string | null;
  /** `Cap.PROFORMAS` (lo calcula la ficha con `can`): sin ella la lista se ve
   *  igual, pero no hay botón «Nueva oferta». */
  canCreate?: boolean;
};

/** Cuántas ofertas caben en el recuadro; el resto, por «Ver todas». */
const PREVIEW_LIMIT = 5;

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}

/** Recuadro «Ofertas» del Resumen de la ficha: las ofertas del contacto
 *  (nº, estado, total, fecha y validez), «Ver todas» a la pantalla de ofertas
 *  filtrada por el contacto y «Nueva oferta», que abre el editor con el
 *  contacto fijo y recarga la lista al guardar. Solo lee; crear una oferta
 *  escribe en BoHub, nunca en FACTUSOL. */
export function ContactOfertasCard({ contactId, contactName, canCreate = false }: Props) {
  const [rows, setRows] = useState<Oferta[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // Sube al guardar una oferta nueva: vuelve a pedir la lista.
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let vivo = true;
    listOfertas({ contact_id: contactId })
      .then((r) => {
        if (vivo) {
          setRows(r);
          setError(null);
        }
      })
      .catch((e) => {
        if (vivo) setError(extractErrorMessage(e, "No se pudieron cargar las ofertas."));
      })
      .finally(() => {
        if (vivo) setLoading(false);
      });
    return () => {
      vivo = false;
    };
  }, [contactId, tick]);

  const visibles = rows.slice(0, PREVIEW_LIMIT);

  return (
    <article className="card contact-summary-card">
      <header className="contact-summary-card-header">
        <h3>
          <FileText size={14} aria-hidden /> Ofertas
        </h3>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : rows.length === 0 ? (
        <p className="muted small">El contacto no tiene ofertas todavía.</p>
      ) : (
        <ul className="contact-ofertas-summary" aria-label="Ofertas del contacto">
          {visibles.map((o) => (
            <li key={o.id} className="contact-ofertas-row">
              <div className="contact-ofertas-main">
                <strong className="mono">{o.numero}</strong>
                <OfertaEstadoPill estado={o.estado} vencida={o.vencida} />
                <span className="contact-ofertas-total">{o.total.toFixed(2)} €</span>
              </div>
              <p className="muted small">
                {fmtDate(o.fecha)} · válida hasta {fmtDate(o.valida_hasta)}
                {o.referencia ? ` · ${o.referencia}` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
      <div className="contact-ofertas-footer">
        {rows.length > 0 ? (
          <Link
            href={`/erp/ofertas?contact_id=${encodeURIComponent(contactId)}`}
            className="contact-summary-link"
          >
            Ver todas{rows.length > PREVIEW_LIMIT ? ` (${rows.length})` : ""}{" "}
            <ArrowUpRight size={12} aria-hidden />
          </Link>
        ) : null}
        {canCreate ? (
          <button type="button" className="button small" onClick={() => setCreating(true)}>
            + Nueva oferta
          </button>
        ) : null}
      </div>

      {creating ? (
        <OfertaEditorModal
          contactId={contactId}
          contactName={contactName}
          oferta={null}
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            setTick((t) => t + 1);
          }}
        />
      ) : null}
    </article>
  );
}
