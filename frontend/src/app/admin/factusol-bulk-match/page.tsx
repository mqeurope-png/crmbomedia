"use client";

import { useMemo, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { extractErrorMessage } from "../../lib/errors";
import {
  BULK_MATCH_FIELDS,
  BULK_MATCH_FIELD_LABELS,
  bulkMatchApply,
  bulkMatchByEmailApply,
  bulkMatchByEmailDryRun,
  bulkMatchDryRun,
  type BulkMatchByEmailDryRun,
  type BulkMatchByEmailRow,
  type BulkMatchDryRun,
  type BulkMatchRow,
} from "../../lib/erpApi";

type Mode = "by_company" | "by_contact_email";
type Filter = "unlinked_only" | "all";

/** Selección del operador por fila: qué candidato y qué campos. */
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

function emptySelection(codcli: string): Selection {
  return { codcli, fields: new Set<string>(BULK_MATCH_FIELDS), apply: false };
}

/** Conciliación masiva CRM ↔ FACTUSOL (C-5 + C-5-fix1).
 *
 *  Dos modos:
 *  - **Contactos por email** (C-5-fix1, por defecto): match exacto de email,
 *    itera contactos y actualiza la EMPRESA a la que pertenecen. Mucho menos
 *    ruido.
 *  - **Empresas por NIF/nombre** (C-5): más cobertura, pero la mayoría de las
 *    empresas del CRM no tienen NIF y el nombre difuso da falsos positivos
 *    («4d Factory» ↔ «FACTORY»).
 *
 *  En los dos: el dry-run propone, y solo se escribe lo que se marca. */
export default function FactusolBulkMatchPage() {
  const [mode, setMode] = useState<Mode>("by_contact_email");
  const [filter, setFilter] = useState<Filter>("unlinked_only");
  const [onlyWithDifferences, setOnlyWithDifferences] = useState(false);
  const [data, setData] = useState<BulkMatchDryRun | null>(null);
  const [emailData, setEmailData] = useState<BulkMatchByEmailDryRun | null>(null);
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
      if (mode === "by_contact_email") {
        const result = await bulkMatchByEmailDryRun({ batch_size: 200 });
        setEmailData(result);
        setData(null);
        setSelections(Object.fromEntries(result.matches.map((m) => [
          m.contact_id,
          emptySelection(m.candidates[0]?.factusol_codcli ?? ""),
        ])));
      } else {
        const result = await bulkMatchDryRun({ filter, batch_size: 200 });
        setData(result);
        setEmailData(null);
        setSelections(Object.fromEntries(result.matches.map((m) => [
          m.crm_company_id,
          emptySelection(m.candidates[0]?.factusol_codcli ?? ""),
        ])));
      }
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo ejecutar el dry-run."));
    } finally {
      setRunning(false);
    }
  }

  const companyRows = useMemo(() => {
    if (!data) return [];
    if (!onlyWithDifferences) return data.matches;
    return data.matches.filter((m) =>
      m.candidates.some((c) => c.differing_fields > 0));
  }, [data, onlyWithDifferences]);

  const emailRows = useMemo(() => {
    if (!emailData) return [];
    if (!onlyWithDifferences) return emailData.matches;
    return emailData.matches.filter((m) =>
      m.candidates.some((c) => c.differing_fields > 0));
  }, [emailData, onlyWithDifferences]);

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
    const picked = Object.entries(selections)
      .filter(([, s]) => s.apply && s.codcli && s.fields.size > 0);
    if (picked.length === 0) return;
    setApplying(true);
    setError(null);
    try {
      if (mode === "by_contact_email") {
        const r = await bulkMatchByEmailApply(picked.map(([contact_id, s]) => ({
          contact_id, factusol_codcli: s.codcli, fields_to_sync: [...s.fields],
        })));
        setSummary(
          `${r.applied} empresa(s) actualizada(s)`
          + (r.skipped.length ? ` · ${r.skipped.length} omitida(s)` : "")
          + (r.errors.length ? ` · ${r.errors.length} con error` : "")
          + ".",
        );
        // Un `skipped` no es un fallo, pero el operador tiene que verlo: si no,
        // creería que se aplicó y no fue así.
        const problems = [
          ...r.skipped.map((s) => s.detail ?? `${s.contact_id}: ${s.result}`),
          ...r.errors.map((e) => `${e.contact_id}: ${e.error}`),
        ];
        if (problems.length) setError(problems.join(" · "));
      } else {
        const r = await bulkMatchApply(picked.map(([crm_company_id, s]) => ({
          crm_company_id, factusol_codcli: s.codcli,
          fields_to_sync: [...s.fields],
        })));
        setSummary(
          `${r.applied} empresa(s) actualizada(s)`
          + (r.errors.length ? ` · ${r.errors.length} con error` : "")
          + ".",
        );
        if (r.errors.length) {
          setError(r.errors.map((e) => `${e.crm_company_id}: ${e.error}`).join(" · "));
        }
      }
      // Relanza el dry-run: las aplicadas ya están vinculadas y salen de la lista.
      await runDryRun();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo aplicar."));
    } finally {
      setApplying(false);
    }
  }

  const byEmail = mode === "by_contact_email";
  const loaded = byEmail ? emailData : data;

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Conciliar CRM ↔ FACTUSOL"
        eyebrow="Admin"
        description="Trae los datos limpios de FACTUSOL al CRM. Nada se escribe sin marcarlo."
        crumbs={[{ label: "Admin" }, { label: "Conciliar FACTUSOL" }]}
      />

      <section className="erp-card">
        <div className="form-row">
          <label className="field">
            <span>Modo</span>
            <select value={mode} aria-label="Modo"
                    onChange={(e) => {
                      setMode(e.target.value as Mode);
                      setData(null);
                      setEmailData(null);
                      setSelections({});
                    }}>
              <option value="by_contact_email">
                Contactos por email (recomendado)
              </option>
              <option value="by_company">Empresas por NIF/nombre</option>
            </select>
          </label>
          {!byEmail ? (
            <label className="field">
              <span>Empresas</span>
              <select value={filter} aria-label="Empresas"
                      onChange={(e) => setFilter(e.target.value as Filter)}>
                <option value="unlinked_only">Solo sin vincular</option>
                <option value="all">Todas (refresco masivo)</option>
              </select>
            </label>
          ) : null}
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
          {byEmail
            ? "Busca el email del contacto en FACTUSOL y actualiza la empresa a la que pertenece. Match exacto: sin falsos positivos."
            : "Busca por NIF exacto o nombre parecido. El nombre parecido es una sugerencia — revísala antes de marcar."}
          {" "}El dry-run <strong>no modifica nada</strong>.
        </p>
      </section>

      {error ? <p className="form-error">{error}</p> : null}
      {summary ? <p className="form-info" role="status">{summary}</p> : null}

      {loaded ? (
        <section className="erp-card">
          {emailData ? (
            <p className="muted small">
              {emailData.total_contacts_with_email} contacto(s) con email ·{" "}
              <strong>{emailData.matches.length}</strong> con match ·{" "}
              {emailData.no_match_count} sin match ·{" "}
              {emailData.matches_without_company} sin empresa CRM.
            </p>
          ) : data ? (
            <p className="muted small">
              {data.total_crm_companies} empresa(s) analizada(s) ·{" "}
              {data.total_factusol_customers} cliente(s) en FACTUSOL ·{" "}
              <strong>{data.matches.length}</strong> con candidato ·{" "}
              {data.no_match.length} sin match.
            </p>
          ) : null}

          {(byEmail ? emailRows.length : companyRows.length) === 0 ? (
            <p className="muted">Sin resultados con estos filtros.</p>
          ) : (
            <table className="data-table">
              <thead>
                {byEmail ? (
                  <tr>
                    <th>Aplicar</th><th>Contacto CRM</th><th>Email</th>
                    <th>Empresa actual</th><th>Cliente FACTUSOL</th>
                    <th>Difieren</th><th />
                  </tr>
                ) : (
                  <tr>
                    <th>Aplicar</th><th>Empresa CRM</th><th>Cliente FACTUSOL</th>
                    <th>Coincidencia</th><th>Difieren</th><th />
                  </tr>
                )}
              </thead>
              <tbody>
                {byEmail
                  ? emailRows.map((m) => (
                      <ByEmailRowView
                        key={m.contact_id}
                        row={m}
                        selection={selections[m.contact_id]}
                        expanded={expanded === m.contact_id}
                        onToggleExpand={() => setExpanded(
                          expanded === m.contact_id ? null : m.contact_id)}
                        onUpdate={(patch) => update(m.contact_id, patch)}
                        onToggleField={(f) => toggleField(m.contact_id, f)}
                      />
                    ))
                  : companyRows.map((m) => (
                      <ByCompanyRowView
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

/** Diff campo a campo, compartido por los dos modos. */
function DiffTable({
  differences, selection, label, onToggleField,
}: {
  differences: { field: string; crm: string; factusol: string; differs: boolean }[];
  selection: Selection;
  label: string;
  onToggleField: (field: string) => void;
}) {
  return (
    <>
      <table className="data-table erp-bulk-diff">
        <thead>
          <tr>
            <th>Sincronizar</th><th>Campo</th>
            <th>CRM (actual)</th><th>FACTUSOL (nuevo)</th>
          </tr>
        </thead>
        <tbody>
          {differences.map((d) => (
            <tr key={d.field} className={d.differs ? "" : "muted"}>
              <td>
                <input type="checkbox" checked={selection.fields.has(d.field)}
                       aria-label={`Sincronizar ${BULK_MATCH_FIELD_LABELS[d.field] ?? d.field} de ${label}`}
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
        Un valor vacío en FACTUSOL nunca pisa el del CRM: el objetivo es limpiar
        datos, no borrarlos.
      </p>
    </>
  );
}

function ByEmailRowView({
  row, selection, expanded, onToggleExpand, onUpdate, onToggleField,
}: {
  row: BulkMatchByEmailRow;
  selection?: Selection;
  expanded: boolean;
  onToggleExpand: () => void;
  onUpdate: (patch: Partial<Selection>) => void;
  onToggleField: (field: string) => void;
}) {
  if (!selection) return null;
  const chosen = row.candidates.find(
    (c) => c.factusol_codcli === selection.codcli) ?? row.candidates[0];
  // El backend saltaría estos dos casos igualmente; deshabilitar el checkbox
  // evita que el operador crea que los ha aplicado.
  const noCompany = !row.company_id;
  const linkedElsewhere = Boolean(
    row.company_factusol_id && row.company_factusol_id !== selection.codcli);
  const blocked = noCompany || linkedElsewhere;
  const reason = noCompany
    ? "El contacto no tiene empresa en el CRM: no hay nada que actualizar."
    : linkedElsewhere
      ? `Su empresa ya está vinculada al cliente ${row.company_factusol_id}.`
      : "";

  return (
    <>
      <tr>
        <td>
          <input type="checkbox" checked={selection.apply} disabled={blocked}
                 title={reason}
                 aria-label={`Aplicar a ${row.contact_name}`}
                 onChange={(e) => onUpdate({ apply: e.target.checked })} />
        </td>
        <td><strong>{row.contact_name}</strong></td>
        <td className="erp-bulk-email">{row.contact_email}</td>
        <td>
          {row.company_name ? (
            <>
              {row.company_name}
              {linkedElsewhere ? (
                <>
                  <br />
                  <span className="badge warn">
                    Ya vinculada a {row.company_factusol_id}
                  </span>
                </>
              ) : null}
            </>
          ) : (
            <span className="badge warn">Sin empresa CRM</span>
          )}
        </td>
        <td>
          {row.candidates.length > 1 ? (
            <div className="erp-bulk-candidates">
              {row.candidates.map((c) => (
                <label key={c.factusol_codcli} className="erp-bulk-candidate">
                  <input type="radio" name={`cand-${row.contact_id}`}
                         value={c.factusol_codcli ?? ""}
                         checked={selection.codcli === c.factusol_codcli}
                         aria-label={`Cliente ${c.factusol_codcli} para ${row.contact_name}`}
                         onChange={() => onUpdate({ codcli: c.factusol_codcli ?? "" })} />
                  <span>nº {c.factusol_codcli} · {c.factusol_nofcli}</span>
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
          <span className="badge muted">
            {chosen?.differing_fields ?? 0} campo(s)
          </span>
        </td>
        <td>
          <button type="button" className="button small secondary"
                  aria-expanded={expanded} onClick={onToggleExpand}>
            {expanded ? "Ocultar" : "Ver diferencias"}
          </button>
        </td>
      </tr>
      {expanded && chosen ? (
        <tr>
          <td colSpan={7}>
            {blocked ? <p className="form-error">{reason}</p> : null}
            <DiffTable differences={chosen.differences} selection={selection}
                       label={row.contact_name} onToggleField={onToggleField} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function ByCompanyRowView({
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
                  <input type="radio" name={`cand-${row.crm_company_id}`}
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
                  aria-expanded={expanded} onClick={onToggleExpand}>
            {expanded ? "Ocultar" : "Ver diferencias"}
          </button>
        </td>
      </tr>
      {expanded && chosen ? (
        <tr>
          <td colSpan={6}>
            <DiffTable differences={chosen.differences} selection={selection}
                       label={row.crm_name} onToggleField={onToggleField} />
          </td>
        </tr>
      ) : null}
    </>
  );
}
