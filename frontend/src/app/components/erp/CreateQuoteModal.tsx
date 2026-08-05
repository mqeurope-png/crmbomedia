"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { listCompanies, type Company } from "../../lib/companiesApi";
import { extractErrorMessage } from "../../lib/errors";
import {
  createFactusolQuote,
  getFactusolQuote,
  searchFactusolQuotes,
  type FactusolArticle,
  type FactusolQuote,
} from "../../lib/erpApi";
import { ArticleAutocompleteInput } from "./ArticleAutocompleteInput";

type Mode = "articles" | "duplicate";

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
const REFPRE_MAX_LENGTH = 250;

function num(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Alta de proforma FACTUSOL. Dos modos:
 *
 *  - **Con artículos**: buscador F_ART + tabla de líneas editable. Una proforma
 *    «simple» se hace aquí con una sola línea escrita a mano, sin tocar el
 *    catálogo — por eso C-4-fix2 retiró la pestaña «Rápida», que duplicaba esto.
 *  - **Duplicar**: parte de una proforma anterior **de cualquier cliente**.
 *
 *  Sobre duplicar: se carga la plantilla en la tabla y se crea una proforma
 *  NUEVA con el cliente destino. No se usa `POST /quotes/{codpre}/duplicate`
 *  porque ese endpoint copia la fila F_PRE entera, incluido `CLIPRE`: duplicar
 *  la de otro cliente dejaría la nueva a nombre del cliente equivocado.
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
  const [mode, setMode] = useState<Mode>("articles");
  const [lines, setLines] = useState<LineRow[]>([{ ...EMPTY_LINE }]);
  const [fecha, setFecha] = useState(today());
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

  const total = useMemo(
    () => lines.reduce((sum, l) => sum + num(l.quantity) * num(l.unit_price), 0),
    [lines],
  );

  function updateLine(i: number, key: keyof LineRow, value: string) {
    setLines((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: value } : r)));
  }

  /** Rellena la línea con el artículo elegido del catálogo. El precio de venta
   *  se deja EN BLANCO si FACTUSOL no lo tiene: forzar «0.00» invita a emitir
   *  una proforma a cero sin que nadie lo note. */
  function applyArticle(i: number, a: FactusolArticle) {
    setLines((rs) => rs.map((r, j) => (j === i ? {
      ...r,
      codart: a.sku ?? a.codart ?? "",
      description: a.descripcion ?? a.sku ?? "",
      unit_price: a.precio_venta ? String(a.precio_venta) : "",
      iva_pct: String(a.iva_pct || 21),
    } : r)));
  }

  /** Carga una proforma como plantilla — siempre al modo «Con artículos», para
   *  poder editarla y añadir más líneas del catálogo.
   *
   *  Si la plantilla no tiene desglose cacheado (proforma vieja hecha en el
   *  FACTUSOL de escritorio, donde F_PRE es mono-línea) se reconstruye **una**
   *  línea con su referencia y su total, en vez de dejar la tabla vacía. */
  const loadTemplate = useCallback(async (quote: FactusolQuote) => {
    if (!quote.codpre) return;
    setError(null);
    try {
      const full = await getFactusolQuote(quote.codpre);
      const rows: LineRow[] = (full.lines ?? []).map((l) => ({
        codart: l.codart ?? "",
        description: l.description,
        quantity: String(l.quantity),
        unit_price: String(l.unit_price),
        iva_pct: String(l.iva_pct),
      }));
      setLines(rows.length > 0 ? rows : [{
        ...EMPTY_LINE,
        description: (full.referencia || `Proforma ${quote.codpre}`)
          .slice(0, REFPRE_MAX_LENGTH),
        quantity: "1",
        unit_price: String(full.total),
      }]);
      setLoadedFrom(quote.codpre);
      setMode("articles");
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cargar la plantilla."));
    }
  }, []);

  const valid = lines.some((l) => l.description.trim() && num(l.quantity) > 0);

  async function submit() {
    if (!valid) return;
    setSubmitting(true);
    setError(null);
    try {
      const r = await createFactusolQuote({
        company_id: targetId,
        lines: lines
          .filter((l) => l.description.trim() && num(l.quantity) > 0)
          .map((l) => ({
            codart: l.codart.trim() || undefined,
            description: l.description.trim(),
            quantity: num(l.quantity),
            unit_price: num(l.unit_price),
            iva_pct: num(l.iva_pct),
          })),
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
      <div className="modal-dialog modal-wide">
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
          <button type="button" className={`tab${mode === "articles" ? " is-active" : ""}`}
                  onClick={() => setMode("articles")}>
            Con artículos
          </button>
          <button type="button" className={`tab${mode === "duplicate" ? " is-active" : ""}`}
                  onClick={() => setMode("duplicate")}>
            Duplicar
          </button>
        </div>

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
          <>
            <p className="muted small">
              Busca en el catálogo escribiendo en <strong>SKU</strong> o{" "}
              <strong>Descripción</strong>. Para conceptos que no son artículos
              (mano de obra, portes) escribe la línea a mano y deja el SKU vacío.
            </p>
            <table className="data-table erp-quote-lines">
              <thead>
                <tr>
                  <th>SKU (opcional)</th><th>Descripción</th><th>Cant.</th>
                  <th>Precio ud.</th><th>IVA %</th><th>Total</th><th />
                </tr>
              </thead>
              <tbody>
                {lines.map((l, i) => (
                  <tr key={i}>
                    <td>
                      <ArticleAutocompleteInput
                        value={l.codart}
                        ariaLabel={`SKU línea ${i + 1}`}
                        placeholder="CDR80WPT"
                        onChange={(v) => updateLine(i, "codart", v)}
                        onPick={(a) => applyArticle(i, a)}
                      />
                    </td>
                    <td>
                      <ArticleAutocompleteInput
                        value={l.description}
                        ariaLabel={`Descripción línea ${i + 1}`}
                        placeholder="Descripción del artículo o concepto"
                        onChange={(v) => updateLine(i, "description", v)}
                        onPick={(a) => applyArticle(i, a)}
                      />
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
                    <td>
                      <input type="number" min="0" step="1" value={l.iva_pct}
                             aria-label={`IVA línea ${i + 1}`}
                             onChange={(e) => updateLine(i, "iva_pct", e.target.value)} />
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
            <div className="erp-quote-foot">
              <button type="button" className="button small secondary"
                      onClick={() => setLines((rs) => [...rs, { ...EMPTY_LINE }])}>
                + Añadir línea
              </button>
              <label className="field">
                <span>Fecha</span>
                <input type="date" value={fecha}
                       onChange={(e) => setFecha(e.target.value)} />
              </label>
              <p className="erp-manual-total">
                Base: <strong>{total.toFixed(2)} EUR</strong>
              </p>
            </div>
            <p className="muted small">
              FACTUSOL guarda el presupuesto en una sola línea: el desglose se
              resume en su referencia y el detalle completo lo conserva el CRM.
            </p>
          </>
        )}

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
