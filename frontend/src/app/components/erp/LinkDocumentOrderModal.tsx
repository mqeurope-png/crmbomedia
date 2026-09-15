"use client";

import { useEffect, useState } from "react";
import {
  getDocumentLinkCandidates,
  linkDocumentToOrder,
  type FactusolDocType,
  type FactusolLinkCandidate,
  type FactusolLinkCandidates,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const SINGULAR: Partial<Record<FactusolDocType, string>> = {
  albaranes: "albarán",
  facturas: "factura",
};

const MATCH_LABEL: Record<FactusolLinkCandidate["match"], string> = {
  referencia: "Por referencia",
  cliente: "Mismo cliente",
  busqueda: "Por nº de pedido",
};

const MATCH_HINT: Record<FactusolLinkCandidate["match"], string> = {
  referencia: "Su referencia común coincide con la del documento: casi seguro es este.",
  cliente: "Pedidos de la misma empresa (comprueba el importe y la fecha).",
  busqueda: "Resultados de la búsqueda por número de pedido.",
};

function eur(n: number | null | undefined): string {
  return n == null ? "—" : `${n.toFixed(2)} €`;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}

export type LinkedOrder = { id: string; order_number: string };

/** Lote 2 · PR-2 — «Vincular a pedido»: un albarán o una factura creados
 *  directamente en FACTUSOL se apuntan a un pedido YA existente de BoHub.
 *  Lista las sugerencias del backend (por referencia común → fuerte; misma
 *  empresa → débil) y permite buscar por nº de pedido; «Usar este» pasa a la
 *  confirmación. Escribe SOLO en el pedido (serie + nº de factura, o nº de
 *  albarán); FACTUSOL no se modifica. Si el pedido ya tenía otro documento
 *  de ese tipo, o el documento ya apuntaba a otro pedido, se avisa y el
 *  vínculo se manda con `force`. */
export function LinkDocumentOrderModal({
  docType,
  serie,
  codigo,
  numero,
  onClose,
  onLinked,
}: {
  docType: FactusolDocType;
  serie: number;
  codigo: number | string;
  numero: string;
  onClose: () => void;
  /** Se llama tras vincular, con el pedido elegido. */
  onLinked: (order: LinkedOrder) => void;
}) {
  const [data, setData] = useState<FactusolLinkCandidates | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [searching, setSearching] = useState(false);
  const [chosen, setChosen] = useState<FactusolLinkCandidate | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const singular = SINGULAR[docType] ?? "documento";
  const title = "Vincular a pedido";

  // El modal se monta por documento: `loading` arranca en true y solo se
  // apaga aquí (nada de setState síncrono dentro del efecto).
  useEffect(() => {
    let alive = true;
    Promise.resolve()
      .then(() => getDocumentLinkCandidates(docType, serie, codigo))
      .then((res) => { if (alive) setData(res); })
      .catch((e) => {
        if (alive) setLoadError(extractErrorMessage(e, "No se pudieron cargar los pedidos."));
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [docType, serie, codigo]);

  async function search() {
    setSearching(true);
    setLoadError(null);
    try {
      setData(await getDocumentLinkCandidates(docType, serie, codigo, q));
    } catch (e) {
      setLoadError(extractErrorMessage(e, "No se pudieron cargar los pedidos."));
    } finally {
      setSearching(false);
    }
  }

  // Conflictos conocidos ANTES de enviar (misma lógica que el 409 del
  // backend): el pedido ya tiene otro documento de este tipo, o el documento
  // ya apunta a otro pedido. Se avisa y se manda `force`.
  const bareCodigo = String(Number(codigo));
  const otherLinked = (data?.linked_orders ?? []).filter((o) => o.id !== chosen?.id);
  const chosenHasOther = !!chosen?.current_link
    && chosen.current_link !== numero && chosen.current_link !== bareCodigo;
  const needsForce = chosenHasOther || otherLinked.length > 0;

  async function submit() {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      const r = await linkDocumentToOrder(docType, serie, codigo, {
        order_id: chosen.id, confirm: true, force: needsForce || undefined,
      });
      onLinked(r.order);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo vincular el documento al pedido."));
    } finally {
      setBusy(false);
    }
  }

  const groups = (["referencia", "cliente", "busqueda"] as const)
    .map((match) => ({
      match,
      items: (data?.candidates ?? []).filter((c) => c.match === match),
    }))
    .filter((g) => g.items.length > 0);

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`${title} ${numero}`}>
      <div className="modal-dialog erp-modal">
        <h2>
          {title}{" "}
          <span className="muted">
            {singular.charAt(0).toUpperCase() + singular.slice(1)}{" "}
            <span className="mono">{numero}</span>
            {data?.doc.cliente_nombre ? ` · ${data.doc.cliente_nombre}` : ""}
            {data?.doc.referencia ? ` · ref. ${data.doc.referencia}` : ""}
          </span>
        </h2>

        {chosen ? (
          <>
            <div className="erp-cobro-target">
              <p>
                <strong>Pedido <span className="mono">{chosen.order_number}</span></strong>
                {chosen.company_name ? ` · ${chosen.company_name}` : ""}
              </p>
              <p className="small">
                {fmtDate(chosen.placed_at)} · <span className="mono">{eur(chosen.total_amount)}</span>
                {data?.doc.total != null
                  ? <> · {singular} <span className="mono">{eur(data.doc.total)}</span></>
                  : null}
              </p>
            </div>
            {chosenHasOther ? (
              <p className="form-error" role="alert">
                El pedido ya tiene {singular}{" "}
                <strong className="mono">{chosen.current_link}</strong>: al vincular se
                sustituye por {numero}.
              </p>
            ) : null}
            {otherLinked.length > 0 ? (
              <p className="form-error" role="alert">
                {singular.charAt(0).toUpperCase() + singular.slice(1)} {numero} ya está
                vinculado al pedido{" "}
                <strong className="mono">{otherLinked.map((o) => o.order_number).join(", ")}</strong>.
                Ese pedido no se toca; quedará apuntando también a este.
              </p>
            ) : null}
            <p className="muted small">
              Se escribirá en el pedido {chosen.order_number} de BoHub el nº de{" "}
              {singular} <strong className="mono">{numero}</strong>
              {docType === "facturas" ? " (serie + número)" : ""}. FACTUSOL no se modifica.
            </p>
            <label className="field-toggle">
              <input
                type="checkbox"
                aria-label="Confirmo el vínculo"
                checked={confirm}
                disabled={busy}
                onChange={(e) => setConfirm(e.target.checked)}
              />
              <span>Confirmo que este {singular} corresponde al pedido {chosen.order_number}.</span>
            </label>
            {error ? <p className="form-error">{error}</p> : null}
            <div className="modal-actions">
              <button type="button" className="button secondary" disabled={busy}
                      onClick={() => { setChosen(null); setConfirm(false); setError(null); }}>
                ← Elegir otro
              </button>
              <button
                type="button" className="button" disabled={!confirm || busy}
                title={!confirm ? "Marca la confirmación para poder vincular" : undefined}
                onClick={() => void submit()}
              >
                {busy ? "Vinculando…" : needsForce ? "Vincular de todas formas" : "Vincular"}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="muted small">
              Elige el pedido de BoHub al que corresponde este {singular}. Solo se
              apunta el nº en el pedido; en FACTUSOL no cambia nada.
            </p>
            <label className="field">
              <span>Buscar por nº de pedido</span>
              <span className="erp-doc-cliente-buscar">
                <input
                  type="search"
                  value={q}
                  placeholder="BOPRIN-99919, MAN-7001…"
                  aria-label="Buscar pedido por número"
                  disabled={searching}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") void search(); }}
                />
                <button type="button" className="button small secondary"
                        disabled={searching} onClick={() => void search()}>
                  {searching ? "Buscando…" : "Buscar"}
                </button>
              </span>
            </label>
            {loading ? <p className="muted">Buscando pedidos…</p> : null}
            {loadError ? <p className="form-error">{loadError}</p> : null}
            {data && !loading && (data.linked_orders.length > 0) ? (
              <p className="form-info" role="status">
                Ya vinculado al pedido{" "}
                <strong className="mono">
                  {data.linked_orders.map((o) => o.order_number).join(", ")}
                </strong>.
              </p>
            ) : null}
            {data && !loading && groups.length === 0 ? (
              <p className="muted">
                Sin pedidos sugeridos{data.q ? ` para «${data.q}»` : ""}. Busca por nº de
                pedido.
              </p>
            ) : null}
            {groups.map((g) => (
              <section key={g.match} className="erp-doc-link-group"
                       aria-label={MATCH_LABEL[g.match]}>
                <h3>{MATCH_LABEL[g.match]}</h3>
                <p className="muted small">{MATCH_HINT[g.match]}</p>
                <ul className="erp-doc-link-list">
                  {g.items.map((c) => (
                    <li key={c.id}>
                      <div className="erp-doc-link-main">
                        <strong className="mono">{c.order_number}</strong>
                        <span className="muted small">
                          {c.company_name ?? "—"} · {fmtDate(c.placed_at)} ·{" "}
                          <span className="mono">{eur(c.total_amount)}</span>
                        </span>
                        {c.current_link && c.current_link !== numero && c.current_link !== bareCodigo ? (
                          <span className="badge warn">
                            ya tiene {singular} {c.current_link}
                          </span>
                        ) : null}
                        {c.current_link === numero || c.current_link === bareCodigo ? (
                          <span className="badge ok">ya vinculado a este</span>
                        ) : null}
                      </div>
                      <button
                        type="button" className="button small secondary"
                        aria-label={`Usar este: ${c.order_number}`}
                        onClick={() => { setChosen(c); setError(null); }}
                      >
                        Usar este
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            <div className="modal-actions">
              <button type="button" className="button secondary" onClick={onClose}>
                Cancelar
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
