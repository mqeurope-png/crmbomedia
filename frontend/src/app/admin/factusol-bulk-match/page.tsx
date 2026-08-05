"use client";

import { useMemo, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { extractErrorMessage } from "../../lib/errors";
import {
  BULK_MATCH_FIELDS,
  BULK_MATCH_FIELD_LABELS,
  bulkMatchApply,
  bulkMatchDryRun,
  type BulkMatchDryRun,
  type BulkMatchRow,
} from "../../lib/erpApi";

type Filter = "unlinked_only" | "all";

/** Selección del operador por empresa: qué candidato y qué campos. */
type Selection = {
  codcli: string;
  fields: Set<string>;
  apply: boolean;
};

const CONFIDENCE_TONE: Record<string, string> = {
  high: "ok", medium: "warn", low: "muted",
};
const MATCH_LABELS: Record<string, string> = {
  nif: "NIF exacto", email: "Email exacto", name: "Nombre parecido",
};

/** Conciliación masiva CRM ↔ FACTUSOL (C-5).
 *
 *  El CRM arrastra empresas de imports heterogéneos con datos sucios; F_CLI es
 *  la fuente limpia. Dos tiempos: el dry-run propone y enseña las diferencias,
 *  y solo se escribe lo que el operador marca, empresa por empresa y campo por
 *  campo. Nada es bulk-todo-o-nada. */
export default function FactusolBulkMatchPage() {
  const [filter, setFilter] = useState<Filter>("unlinked_only");
  const [onlyWithDifferences, setOnlyWithDifferences] = useState(false);
  const [data, setData] = useState<BulkMatchDryRun | null>(null);
  const [selections, setSelections] = useState<Record<string, Selection>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);

  /** Relee y repuebla la tabla. NO toca `summary`/`error`: se llama también
   *  como refresco después de aplicar, y borrar el resultado ahí dejaría al
   *  operador sin saber qué pasó. Limpiarlos es cosa de quien inicia la acción. */
  async function runDryRun() {
    setRunning(true);
    try {
      const result = await bulkMatchDryRun({ filter, batch_size: 200 });
      setData(result);
      // Por defecto: primer candidato, todos los campos, sin aplicar. El
      // «aplicar» se marca a mano — es lo que escribe en la base.
      setSelections(Object.fromEntries(result.matches.map((m) => [
        m.crm_company_id,
        {
          codcli: m.candidates[0]?.factusol_codcli ?? "",
          fields: new Set<string>(BULK_MATCH_FIELDS),
          apply: false,
        },
      ])));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo ejecutar el dry-run."));
    } finally {
      setRunning(false);
    }
  }

  const rows = useMemo(() => {
    if (!data) return [];
    if (!onlyWithDifferences) return data.matches;
    return data.matches.filter((m) =>
      m.candidates.some((c) => c.differing_fields > 0));
  }, [data, onlyWithDifferences]);

  const selectedCount = Object.values(selections).filter((s) => s.apply).length;

  function update(id: string, patch: Partial<Selection>) {
    setSelections((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }

  function toggleField(id: string, field: string) {
    setSelections((prev) => {
      const fields = new Set(prev[id].fields);
      if (fields.has(field)) fields.delete(field);
      else fields.add(field);
      return { ...prev, [id]: { ...prev[id], fields } };
    });
  }

  async function applySelected() {
    const operations = Object.entries(selections)
      .filter(([, s]) => s.apply && s.codcli && s.fields.size > 0)
      .map(([crm_company_id, s]) => ({
        crm_company_id,
        factusol_codcli: s.codcli,
        fields_to_sync: [...s.fields],
      }));
    if (operations.length === 0) return;
    setApplying(true);
    setError(null);
    try {
      const r = await bulkMatchApply(operations);
      setSummary(
        `${r.applied} empresa(s) actualizada(s)`
        + (r.errors.length ? ` · ${r.errors.length} con error` : "")
        + ".",
      );
      if (r.errors.length) {
        setError(r.errors.map((e) => `${e.crm_company_id}: ${e.error}`).join(" · "));
      }
      // Relanza el dry-run: las aplicadas ya están vinculadas y salen de la lista.
      await runDryRun();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo aplicar."));
    } finally {
      setApplying(false);
    }
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Conciliar CRM ↔ FACTUSOL"
        eyebrow="Admin"
        description="Propone parejas empresa-cliente y sincroniza los datos limpios de FACTUSOL. Nada se escribe sin marcarlo."
        crumbs={[{ label: "Admin" }, { label: "Conciliar FACTUSOL" }]}
      />

      <section className="erp-card">
        <div className="form-row">
          <label className="field">
            <span>Empresas</span>
            <select value={filter} aria-label="Empresas"
                    onChange={(e) => setFilter(e.target.value as Filter)}>
              <option value="unlinked_only">Solo sin vincular</option>
              <option value="all">Todas (refresco masivo)</option>
            </select>
          </label>
          <label className="field-toggle">
            <input type="checkbox" checked={onlyWithDifferences}
                   onChange={(e) => setOnlyWithDifferences(e.target.checked)} />
            <span>Solo con diferencias</span>
          </label>
        </div>
        <button type="button" className="button" disabled={running}
                onClick={() => { setError(null); setSummary(null); runDryRun(); }}>
          {running ? "Analizando…" : "Ejecutar dry-run"}
        </button>
        <p className="muted small">
          El dry-run <strong>no modifica nada</strong>: solo lee el CRM y F_CLI.
        </p>
      </section>

      {error ? <p className="form-error">{error}</p> : null}
      {summary ? <p className="form-info" role="status">{summary}</p> : null}

      {data ? (
        <section className="erp-card">
          <p className="muted small">
            {data.total_crm_companies} empresa(s) analizada(s) ·{" "}
            {data.total_factusol_customers} cliente(s) en FACTUSOL ·{" "}
            <strong>{data.matches.length}</strong> con candidato ·{" "}
            {data.no_match.length} sin match.
          </p>

          {rows.length === 0 ? (
            <p className="muted">Sin resultados con estos filtros.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Aplicar</th><th>Empresa CRM</th><th>Cliente FACTUSOL</th>
                  <th>Coincidencia</th><th>Difieren</th><th />
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <BulkMatchRowView
                    key={m.crm_company_id}
                    row={m}
                    selection={selections[m.crm_company_id]}
                    expanded={expanded === m.crm_company_id}
                    onToggleExpand={() => setExpanded(
                      expanded === m.crm_company_id ? null : m.crm_company_id)}
                    onUpdate={(patch) => update(m.crm_company_id, patch)}
                    onToggleField={(f) => toggleField(m.crm_company_id, f)}
                  />
                ))}
              </tbody>
            </table>
          )}

          <div className="form-actions">
            <button type="button" className="button"
                    disabled={applying || selectedCount === 0}
                    onClick={applySelected}>
              {applying
                ? "Aplicando…"
                : `Aplicar seleccionadas (${selectedCount})`}
            </button>
          </div>
        </section>
      ) : null}
    </main>
  );
}

function BulkMatchRowView({
  row, selection, expanded, onToggleExpand, onUpdate, onToggleField,
}: {
  row: BulkMatchRow;
  selection?: Selection;
  expanded: boolean;
  onToggleExpand: () => void;
  onUpdate: (patch: Partial<Selection>) => void;
  onToggleField: (field: string) => void;
}) {
  if (!selection) return null;
  const chosen = row.candidates.find(
    (c) => c.factusol_codcli === selection.codcli) ?? row.candidates[0];

  return (
    <>
      <tr>
        <td>
          <input type="checkbox" checked={selection.apply}
                 aria-label={`Aplicar a ${row.crm_name}`}
                 onChange={(e) => onUpdate({ apply: e.target.checked })} />
        </td>
        <td>
          <strong>{row.crm_name}</strong>
          <br />
          <span className="muted small">{row.crm_tax_id || "sin NIF"}</span>
        </td>
        <td>
          {/* Varios candidatos: el operador elige. Pasa de verdad — hay
              clientes duplicados en F_CLI con el mismo NIF. */}
          {row.candidates.length > 1 ? (
            <div className="erp-bulk-candidates">
              {row.candidates.map((c) => (
                <label key={c.factusol_codcli} className="erp-bulk-candidate">
                  <input type="radio"
                         name={`cand-${row.crm_company_id}`}
                         value={c.factusol_codcli ?? ""}
                         checked={selection.codcli === c.factusol_codcli}
                         aria-label={`Cliente ${c.factusol_codcli} para ${row.crm_name}`}
                         onChange={() => onUpdate({ codcli: c.factusol_codcli ?? "" })} />
                  <span>
                    nº {c.factusol_codcli} · {c.factusol_nofcli}
                    {c.factusol_pobcli ? ` (${c.factusol_pobcli})` : ""}
                  </span>
                </label>
              ))}
            </div>
          ) : (
            <>
              nº {chosen?.factusol_codcli} · {chosen?.factusol_nofcli}
              <br />
              <span className="muted small">{chosen?.factusol_pobcli}</span>
            </>
          )}
        </td>
        <td>
          <span className={`badge ${CONFIDENCE_TONE[row.confidence] ?? "muted"}`}>
            {MATCH_LABELS[row.match_type] ?? row.match_type}
          </span>
        </td>
        <td>
          <span className="badge muted">
            {chosen?.differing_fields ?? 0} campo(s)
          </span>
        </td>
        <td>
          <button type="button" className="button small secondary"
                  aria-expanded={expanded}
                  onClick={onToggleExpand}>
            {expanded ? "Ocultar" : "Ver diferencias"}
          </button>
        </td>
      </tr>
      {expanded && chosen ? (
        <tr>
          <td colSpan={6}>
            <table className="data-table erp-bulk-diff">
              <thead>
                <tr>
                  <th>Sincronizar</th><th>Campo</th>
                  <th>CRM (actual)</th><th>FACTUSOL (nuevo)</th>
                </tr>
              </thead>
              <tbody>
                {chosen.differences.map((d) => (
                  <tr key={d.field} className={d.differs ? "" : "muted"}>
                    <td>
                      <input type="checkbox"
                             checked={selection.fields.has(d.field)}
                             aria-label={`Sincronizar ${BULK_MATCH_FIELD_LABELS[d.field] ?? d.field} de ${row.crm_name}`}
                             onChange={() => onToggleField(d.field)} />
                    </td>
                    <td>{BULK_MATCH_FIELD_LABELS[d.field] ?? d.field}</td>
                    <td>{d.crm || "—"}</td>
                    <td>
                      {d.differs ? <strong>{d.factusol || "—"}</strong>
                                 : (d.factusol || "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted small">
              Un valor vacío en FACTUSOL nunca pisa el del CRM: el objetivo es
              limpiar datos, no borrarlos.
            </p>
          </td>
        </tr>
      ) : null}
    </>
  );
}
