"use client";

import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  createFactusolQuote,
  duplicateFactusolQuote,
  listFactusolQuotes,
  searchFactusolArticles,
  type FactusolArticle,
  type FactusolQuote,
} from "../../lib/erpApi";

type Mode = "quick" | "articles" | "duplicate";

type LineRow = {
  codart: string;
  description: string;
  quantity: string;
  unit_price: string;
  iva_pct: string;
};

const EMPTY_LINE: LineRow = {
  codart: "", description: "", quantity: "1", unit_price: "", iva_pct: "21",
};

function num(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Alta de proforma FACTUSOL (C-4), con los 3 modos con los que Bart trabaja:
 *
 *  - **rápido**: una referencia de texto y un importe. Es lo que más se usa —
 *    F_PRE es mono-línea, así que una proforma «de una frase» es su forma
 *    nativa.
 *  - **con artículos**: busca en F_ART y compone el desglose. El CRM guarda las
 *    líneas en su caché porque FACTUSOL no puede (ver `factusol-proformas.md`).
 *  - **duplicar**: parte de una proforma anterior del mismo cliente.
 */
export function CreateQuoteModal({
  companyId,
  companyName,
  onCreated,
  onCancel,
}: {
  companyId: string;
  companyName: string;
  onCreated: (jobId: string) => void;
  onCancel: () => void;
}) {
  const [mode, setMode] = useState<Mode>("quick");
  const [referencia, setReferencia] = useState("");
  const [quickAmount, setQuickAmount] = useState("");
  const [quickIva, setQuickIva] = useState("21");
  const [lines, setLines] = useState<LineRow[]>([{ ...EMPTY_LINE }]);
  const [fecha, setFecha] = useState(today());
  const [articleQuery, setArticleQuery] = useState("");
  const [articles, setArticles] = useState<FactusolArticle[]>([]);
  const [quotes, setQuotes] = useState<FactusolQuote[]>([]);
  const [sourceCodpre, setSourceCodpre] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Buscador de artículos (debounced, mismo patrón que el resto del ERP).
  useEffect(() => {
    if (mode !== "articles" || articleQuery.trim().length < 2) {
      setArticles([]);
      return;
    }
    const handle = window.setTimeout(() => {
      searchFactusolArticles(articleQuery.trim())
        .then(setArticles)
        .catch(() => setArticles([]));
    }, 300);
    return () => window.clearTimeout(handle);
  }, [mode, articleQuery]);

  // Proformas previas del cliente para el modo duplicar.
  useEffect(() => {
    if (mode !== "duplicate") return;
    listFactusolQuotes({ company_id: companyId, days_back: 365 })
      .then((r) => setQuotes(r.items))
      .catch(() => setQuotes([]));
  }, [mode, companyId]);

  const total = useMemo(() => {
    if (mode === "quick") return num(quickAmount);
    return lines.reduce((sum, l) => sum + num(l.quantity) * num(l.unit_price), 0);
  }, [mode, quickAmount, lines]);

  function updateLine(i: number, key: keyof LineRow, value: string) {
    setLines((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: value } : r)));
  }

  function addArticle(a: FactusolArticle) {
    setLines((rs) => {
      const next: LineRow = {
        codart: a.codart ?? "",
        description: a.descripcion ?? a.codart ?? "",
        quantity: "1",
        unit_price: String(a.precio || ""),
        iva_pct: String(a.iva_pct || 21),
      };
      // La primera fila vacía se reutiliza en vez de dejar un hueco.
      const isBlank = (r: LineRow) => !r.description.trim() && !r.codart.trim();
      return rs.length === 1 && isBlank(rs[0]) ? [next] : [...rs, next];
    });
    setArticleQuery("");
    setArticles([]);
  }

  const valid = mode === "quick"
    ? referencia.trim().length > 0
    : mode === "articles"
      ? lines.some((l) => l.description.trim() && num(l.quantity) > 0)
      : sourceCodpre.length > 0;

  async function submit() {
    if (!valid) return;
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "duplicate") {
        const r = await duplicateFactusolQuote(sourceCodpre);
        onCreated(r.job_id);
        return;
      }
      const payloadLines = mode === "quick"
        // Modo rápido: una sola línea con el texto y el importe. Así la
        // proforma sigue teniendo desglose cacheado y se puede duplicar.
        ? [{
            description: referencia.trim(),
            quantity: 1,
            unit_price: num(quickAmount),
            iva_pct: num(quickIva),
          }]
        : lines
            .filter((l) => l.description.trim() && num(l.quantity) > 0)
            .map((l) => ({
              codart: l.codart.trim() || undefined,
              description: l.description.trim(),
              quantity: num(l.quantity),
              unit_price: num(l.unit_price),
              iva_pct: num(l.iva_pct),
            }));
      const r = await createFactusolQuote({
        company_id: companyId,
        referencia: mode === "quick" ? referencia.trim() : "",
        lines: payloadLines,
        fecha: fecha || null,
      });
      onCreated(r.job_id);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo crear la proforma."));
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label="Nueva proforma FACTUSOL">
      <div className="modal-dialog">
        <h2>Nueva proforma · {companyName}</h2>
        <p className="form-error">
          Se creará un presupuesto <strong>real</strong> en FACTUSOL.
        </p>
        {error ? <p className="form-error">{error}</p> : null}

        <div className="tab-bar">
          <button type="button" className={`tab${mode === "quick" ? " is-active" : ""}`}
                  onClick={() => setMode("quick")}>
            Rápida
          </button>
          <button type="button" className={`tab${mode === "articles" ? " is-active" : ""}`}
                  onClick={() => setMode("articles")}>
            Con artículos
          </button>
          <button type="button" className={`tab${mode === "duplicate" ? " is-active" : ""}`}
                  onClick={() => setMode("duplicate")}>
            Duplicar
          </button>
        </div>

        {mode === "quick" ? (
          <>
            <label className="field">
              <span>Concepto</span>
              <input type="text" value={referencia} maxLength={250}
                     placeholder="Suministro y montaje de pantalla LED 3x2"
                     onChange={(e) => setReferencia(e.target.value)} />
            </label>
            <span className="muted small">
              {referencia.length}/250 · es el texto que Bart verá en FACTUSOL.
            </span>
            <div className="form-row">
              <label className="field">
                <span>Importe (base)</span>
                <input type="number" min="0" step="0.01" value={quickAmount}
                       onChange={(e) => setQuickAmount(e.target.value)} />
              </label>
              <label className="field">
                <span>IVA %</span>
                <input type="number" min="0" step="1" value={quickIva}
                       onChange={(e) => setQuickIva(e.target.value)} />
              </label>
            </div>
          </>
        ) : null}

        {mode === "articles" ? (
          <>
            <label className="field">
              <span>Buscar artículo</span>
              <input type="text" value={articleQuery}
                     placeholder="Código, EAN o descripción…"
                     onChange={(e) => setArticleQuery(e.target.value)} />
            </label>
            {articles.length > 0 ? (
              <ul className="erp-article-hits">
                {articles.slice(0, 8).map((a) => (
                  <li key={a.codart ?? a.eanart ?? a.descripcion}>
                    <button type="button" className="button small secondary"
                            onClick={() => addArticle(a)}>
                      + {a.codart} · {a.descripcion}
                      {a.precio ? ` · ${a.precio.toFixed(2)} €` : ""}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            <table className="data-table">
              <thead>
                <tr>
                  <th>Artículo</th><th>Descripción</th><th>Cant.</th>
                  <th>Precio</th><th>Total</th><th />
                </tr>
              </thead>
              <tbody>
                {lines.map((l, i) => (
                  <tr key={i}>
                    <td>
                      <input type="text" value={l.codart}
                             aria-label={`Artículo línea ${i + 1}`}
                             onChange={(e) => updateLine(i, "codart", e.target.value)} />
                    </td>
                    <td>
                      <input type="text" value={l.description}
                             aria-label={`Descripción línea ${i + 1}`}
                             onChange={(e) => updateLine(i, "description", e.target.value)} />
                    </td>
                    <td>
                      <input type="number" min="0" step="1" value={l.quantity}
                             aria-label={`Cantidad línea ${i + 1}`}
                             onChange={(e) => updateLine(i, "quantity", e.target.value)} />
                    </td>
                    <td>
                      <input type="number" min="0" step="0.01" value={l.unit_price}
                             aria-label={`Precio línea ${i + 1}`}
                             onChange={(e) => updateLine(i, "unit_price", e.target.value)} />
                    </td>
                    <td>{(num(l.quantity) * num(l.unit_price)).toFixed(2)}</td>
                    <td>
                      {lines.length > 1 ? (
                        <button type="button" className="button small secondary"
                                aria-label={`Eliminar línea ${i + 1}`}
                                onClick={() => setLines((rs) => rs.filter((_, j) => j !== i))}>
                          ✕
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button type="button" className="button small secondary"
                    onClick={() => setLines((rs) => [...rs, { ...EMPTY_LINE }])}>
              + Añadir línea
            </button>
            <p className="muted small">
              FACTUSOL guarda el presupuesto en una sola línea: el desglose se
              resume en su referencia y el detalle completo lo conserva el CRM.
            </p>
          </>
        ) : null}

        {mode === "duplicate" ? (
          quotes.length === 0 ? (
            <p className="muted">Este cliente no tiene proformas previas.</p>
          ) : (
            <label className="field">
              <span>Proforma a duplicar</span>
              <select value={sourceCodpre}
                      onChange={(e) => setSourceCodpre(e.target.value)}>
                <option value="">— Elige una —</option>
                {quotes.map((q) => (
                  <option key={q.codpre ?? ""} value={q.codpre ?? ""}>
                    nº {q.codpre} · {q.fecha ?? "—"} · {q.total.toFixed(2)} € ·{" "}
                    {q.referencia.slice(0, 60)}
                  </option>
                ))}
              </select>
            </label>
          )
        ) : (
          <label className="field">
            <span>Fecha</span>
            <input type="date" value={fecha}
                   onChange={(e) => setFecha(e.target.value)} />
          </label>
        )}

        {mode !== "duplicate" ? (
          <p className="erp-manual-total">
            Base: <strong>{total.toFixed(2)} EUR</strong>
          </p>
        ) : null}

        <div className="modal-actions">
          <button type="button" className="button secondary"
                  onClick={onCancel} disabled={submitting}>
            Cancelar
          </button>
          <button type="button" className="button"
                  onClick={submit} disabled={!valid || submitting}>
            {submitting
              ? "Creando…"
              : mode === "duplicate" ? "Duplicar proforma" : "Crear proforma"}
          </button>
        </div>
      </div>
    </div>
  );
}
