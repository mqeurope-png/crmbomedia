"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { listCompanies, type Company } from "../../lib/companiesApi";
import { extractErrorMessage } from "../../lib/errors";
import {
  createFactusolQuote,
  getFactusolQuote,
  searchFactusolArticles,
  searchFactusolQuotes,
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

const DEBOUNCE_MS = 300;
const TEMPLATE_DAYS_BACK = 365;
const REF_PREVIEW_CHARS = 60;

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
 *  - **duplicar**: parte de una proforma anterior **de cualquier cliente**.
 *
 *  C-4-fix1 — sobre el modo duplicar: se carga la plantilla en el formulario y
 *  se crea una proforma NUEVA con el cliente destino elegido. No se usa
 *  `POST /quotes/{codpre}/duplicate` porque ese endpoint copia la fila F_PRE
 *  entera, incluido `CLIPRE`: duplicar la de otro cliente dejaría la nueva
 *  proforma a nombre del cliente equivocado.
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
  // Cliente DESTINO: arranca en la empresa desde la que se abrió el modal.
  const [targetId, setTargetId] = useState(companyId);
  const [targetName, setTargetName] = useState(companyName);
  const [changingTarget, setChangingTarget] = useState(false);
  const [targetQuery, setTargetQuery] = useState("");
  const [targetOptions, setTargetOptions] = useState<Company[]>([]);
  // Modo duplicar: búsqueda libre entre TODAS las proformas.
  const [templateQuery, setTemplateQuery] = useState("");
  const [templates, setTemplates] = useState<FactusolQuote[]>([]);
  const [searchingTemplates, setSearchingTemplates] = useState(false);
  const [loadedFrom, setLoadedFrom] = useState<string | null>(null);
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
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [mode, articleQuery]);

  // Buscador de plantillas: NO filtra por cliente (C-4-fix1).
  useEffect(() => {
    if (mode !== "duplicate") return;
    let alive = true;
    setSearchingTemplates(true);
    const handle = window.setTimeout(() => {
      searchFactusolQuotes(templateQuery.trim(), { days_back: TEMPLATE_DAYS_BACK })
        .then((items) => { if (alive) setTemplates(items); })
        .catch(() => { if (alive) setTemplates([]); })
        .finally(() => { if (alive) setSearchingTemplates(false); });
    }, DEBOUNCE_MS);
    return () => { alive = false; window.clearTimeout(handle); };
  }, [mode, templateQuery]);

  // Empresas candidatas a cliente destino: solo las YA vinculadas a FACTUSOL,
  // que son las únicas que el backend acepta (si no, 409 company_not_linked).
  useEffect(() => {
    if (!changingTarget) return;
    const handle = window.setTimeout(() => {
      listCompanies({ q: targetQuery || undefined, limit: 20 })
        .then((page) =>
          setTargetOptions(page.items.filter((c) => c.factusol_company_id)))
        .catch(() => setTargetOptions([]));
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [changingTarget, targetQuery]);

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
        // El SKU comercial (EQUART) es el que el operativo reconoce.
        codart: a.sku ?? a.codart ?? "",
        description: a.descripcion ?? a.sku ?? "",
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

  /** Carga una proforma como plantilla. Si tiene desglose cacheado se copian
   *  sus líneas; si no (proforma del escritorio, F_PRE es mono-línea) se cae al
   *  modo rápido con su referencia y su base. */
  const loadTemplate = useCallback(async (quote: FactusolQuote) => {
    if (!quote.codpre) return;
    setError(null);
    try {
      const full = await getFactusolQuote(quote.codpre);
      if (full.lines && full.lines.length > 0) {
        setLines(full.lines.map((l) => ({
          codart: l.codart ?? "",
          description: l.description,
          quantity: String(l.quantity),
          unit_price: String(l.unit_price),
          iva_pct: String(l.iva_pct),
        })));
        setMode("articles");
      } else {
        setReferencia(full.referencia);
        setQuickAmount(String(full.base));
        setMode("quick");
      }
      setLoadedFrom(quote.codpre);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cargar la plantilla."));
    }
  }, []);

  const valid = mode === "quick"
    ? referencia.trim().length > 0
    : mode === "articles"
      ? lines.some((l) => l.description.trim() && num(l.quantity) > 0)
      : false;  // en modo duplicar hay que cargar una plantilla primero

  async function submit() {
    if (!valid) return;
    setSubmitting(true);
    setError(null);
    try {
      const payloadLines = mode === "quick"
        // Modo rápido: una sola línea con el texto y el importe. Así la
        // proforma sigue teniendo desglose cacheado y se puede reutilizar.
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
        company_id: targetId,
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
        <h2>Nueva proforma</h2>

        <div className="erp-quote-target">
          <span>
            Cliente destino: <strong>{targetName}</strong>
            {loadedFrom ? (
              <span className="muted small"> · plantilla nº {loadedFrom}</span>
            ) : null}
          </span>
          <button type="button" className="button small secondary"
                  onClick={() => setChangingTarget((v) => !v)}>
            Cambiar
          </button>
        </div>
        {changingTarget ? (
          <label className="field">
            <span>Empresa destino (vinculada a FACTUSOL)</span>
            <input type="text" list="erp-quote-target-companies"
                   value={targetQuery} placeholder="Buscar empresa…"
                   aria-label="Empresa destino"
                   onChange={(e) => {
                     setTargetQuery(e.target.value);
                     const hit = targetOptions.find((c) => c.name === e.target.value);
                     if (hit) {
                       setTargetId(hit.id);
                       setTargetName(hit.name);
                       setChangingTarget(false);
                     }
                   }} />
            <datalist id="erp-quote-target-companies">
              {targetOptions.map((c) => <option key={c.id} value={c.name} />)}
            </datalist>
          </label>
        ) : null}

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
                     placeholder="SKU, EAN o descripción…"
                     onChange={(e) => setArticleQuery(e.target.value)} />
            </label>
            {articles.length > 0 ? (
              <ul className="erp-article-hits">
                {articles.slice(0, 8).map((a) => (
                  <li key={a.codart ?? a.eanart ?? a.descripcion}>
                    <button type="button" className="button small secondary erp-article-hit"
                            onClick={() => addArticle(a)}>
                      <span className="erp-article-sku">{a.sku ?? a.codart}</span>
                      <span className="erp-article-desc">{a.descripcion}</span>
                      <span className="erp-article-price">
                        {a.precio ? `${a.precio.toFixed(2)} €` : "—"}
                      </span>
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
          <>
            <p className="form-info">
              Puedes duplicar <strong>cualquier</strong> proforma — se creará
              como nueva con el cliente que elijas arriba.
            </p>
            <label className="field">
              <span>Buscar plantilla</span>
              <input type="text" value={templateQuery}
                     placeholder="Buscar por nº, cliente o descripción…"
                     onChange={(e) => setTemplateQuery(e.target.value)} />
            </label>
            {searchingTemplates ? (
              <p className="muted small">Buscando…</p>
            ) : templates.length === 0 ? (
              <p className="muted small">Sin proformas que coincidan.</p>
            ) : (
              <ul className="erp-quote-list">
                {templates.slice(0, 20).map((q) => (
                  <li key={q.codpre ?? ""}>
                    <span>
                      <strong>nº {q.codpre}</strong> · {q.fecha ?? "—"} ·{" "}
                      {q.cliente_nombre ?? "—"} · {q.total.toFixed(2)} € ·{" "}
                      {q.referencia.slice(0, REF_PREVIEW_CHARS)}
                    </span>
                    <button type="button" className="button small"
                            onClick={() => loadTemplate(q)}>
                      Cargar esta plantilla
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
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
            {submitting ? "Creando…" : "Crear proforma"}
          </button>
        </div>
      </div>
    </div>
  );
}
