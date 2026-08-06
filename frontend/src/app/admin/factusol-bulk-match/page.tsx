"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { extractErrorMessage } from "../../lib/errors";
import {
  BULK_MATCH_FIELDS,
  BULK_MATCH_FIELD_LABELS,
  bulkMatchApply,
  bulkMatchByEmailApply,
  bulkMatchByEmailDryRun,
  bulkMatchDryRun,
  importOrphansApply,
  importOrphansDryRun,
  type BulkMatchByEmailDryRun,
  type BulkMatchByEmailRow,
  type BulkMatchCandidate,
  type BulkMatchDryRun,
  type BulkMatchRow,
  type FactusolOrphan,
  type ImportOrphansDryRun,
} from "../../lib/erpApi";

type Mode = "by_company" | "by_contact_email" | "import_orphans";
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

/** A partir de aquí, «Aplicar seleccionadas» pide confirmación. Marcar todo con
 *  un clic hace muy fácil lanzar un lote enorme sin querer, y revertirlo es SQL
 *  a mano. Por debajo del umbral no se molesta al operador. */
const CONFIRM_THRESHOLD = 50;

function emptySelection(codcli: string): Selection {
  return { codcli, fields: new Set<string>(BULK_MATCH_FIELDS), apply: false };
}

/** Candidato preseleccionado en un multi-match: el de `codcli` **mayor**.
 *
 *  Los CODCLI de Bomedia son autonuméricos, así que el mayor es el cliente
 *  creado más tarde — el que suele traer los datos buenos. Caso real: un mismo
 *  email casa con 2123, 2210 y 2278; el bueno es el 2278.
 *
 *  `factusol_codcli` llega como string, hay que comparar como número (si no,
 *  «999» ganaría a «2278»). Un codcli no numérico nunca gana: la comparación
 *  con `NaN` es falsa, así que se queda el primero. */
function pickDefaultCodcli(candidates: BulkMatchCandidate[]): string {
  const codclis = candidates
    .map((c) => c.factusol_codcli)
    .filter((c): c is string => Boolean(c));
  if (codclis.length === 0) return "";
  return codclis.reduce((best, c) =>
    Number.parseInt(c, 10) > Number.parseInt(best, 10) ? c : best);
}

/** ¿Aplicar esta fila mueve el contacto de empresa?
 *
 *  Su empresa apunta a **otro** cliente de FACTUSOL: el contacto está mal
 *  agrupado. Hasta C-5-fix4 esto bloqueaba la fila; desde C-5-fix5 se reasigna
 *  a la empresa que le corresponde y la original no se toca. */
function isReassignment(row: BulkMatchByEmailRow, codcli: string): boolean {
  return Boolean(row.company_factusol_id && row.company_factusol_id !== codcli);
}

/** Checkbox de tres estados. `indeterminate` es una propiedad del DOM, no un
 *  atributo: no se puede pasar por JSX y hay que escribirla por ref. */
function MasterCheckbox({
  checked, indeterminate, disabled, onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input ref={ref} type="checkbox" checked={checked} disabled={disabled}
           aria-label="Seleccionar todas"
           title="Marca todas las filas visibles que se pueden aplicar"
           onChange={(e) => onChange(e.target.checked)} />
  );
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
  const [orphanData, setOrphanData] = useState<ImportOrphansDryRun | null>(null);
  const [orphansOnlyWithEmail, setOrphansOnlyWithEmail] = useState(false);
  const [selections, setSelections] = useState<Record<string, Selection>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  /** Relee y repuebla la tabla. NO toca `summary`/`error`: se llama también
   *  como refresco después de aplicar, y borrar el resultado ahí dejaría al
   *  operador sin saber qué pasó. Limpiarlos es cosa de quien inicia la acción. */
  async function runDryRun() {
    setRunning(true);
    try {
      if (mode === "import_orphans") {
        const result = await importOrphansDryRun({
          filter: orphansOnlyWithEmail ? "only_with_email" : "all",
        });
        setOrphanData(result);
        setData(null);
        setEmailData(null);
        setSelections(Object.fromEntries(
          result.orphans
            .filter((o) => o.codcli)
            .map((o) => [o.codcli as string, emptySelection(o.codcli as string)]),
        ));
      } else if (mode === "by_contact_email") {
        const result = await bulkMatchByEmailDryRun();
        setEmailData(result);
        setData(null);
        setOrphanData(null);
        setSelections(Object.fromEntries(result.matches.map((m) => [
          m.contact_id,
          emptySelection(pickDefaultCodcli(m.candidates)),
        ])));
      } else {
        const result = await bulkMatchDryRun({ filter, batch_size: 200 });
        setData(result);
        setEmailData(null);
        setOrphanData(null);
        setSelections(Object.fromEntries(result.matches.map((m) => [
          m.crm_company_id,
          emptySelection(pickDefaultCodcli(m.candidates)),
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

  /** El modo importación no filtra por diferencias: no hay nada previo con
   *  qué compararse. Lo que sí filtra —«solo con email»— lo aplica el backend
   *  en el dry-run, así que aquí llega ya resuelto. */
  const orphanRows = useMemo(() => orphanData?.orphans ?? [], [orphanData]);

  const selectedCount = Object.values(selections).filter((s) => s.apply).length;

  /** Filas **visibles** (respeta «Solo con diferencias») que se pueden marcar.
   *  Es el universo sobre el que actúa el checkbox master.
   *
   *  Desde C-5-fix5 son todas: el único caso que estaba bloqueado —empresa
   *  vinculada a otro CODCLI— ahora se reasigna en vez de saltarse. */
  const applicableIds = useMemo(
    () => {
      if (mode === "import_orphans") {
        return orphanRows.map((o) => o.codcli).filter((c): c is string => !!c);
      }
      return mode === "by_contact_email"
        ? emailRows.map((m) => m.contact_id)
        : companyRows.map((m) => m.crm_company_id);
    },
    [mode, emailRows, companyRows, orphanRows],
  );

  const applicableSelected =
    applicableIds.filter((id) => selections[id]?.apply).length;
  const allSelected =
    applicableIds.length > 0 && applicableSelected === applicableIds.length;
  const someSelected = applicableSelected > 0 && !allSelected;

  function toggleAll(checked: boolean) {
    setSelections((prev) => {
      const next = { ...prev };
      for (const id of applicableIds) {
        if (next[id]) next[id] = { ...next[id], apply: checked };
      }
      return next;
    });
  }

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
    setConfirming(false);
    // En importación no hay campos que elegir: la empresa nace entera.
    const picked = Object.entries(selections).filter(([, s]) =>
      s.apply && s.codcli
      && (mode === "import_orphans" || s.fields.size > 0));
    if (picked.length === 0) return;
    setApplying(true);
    setError(null);
    try {
      if (mode === "import_orphans") {
        const r = await importOrphansApply(picked.map(([codcli]) => codcli));
        setSummary([
          `${r.imported_company_and_contact} empresa(s) creada(s) con contacto`,
          `${r.imported_company_only} empresa(s) creada(s) sin contacto`,
          ...(r.skipped_race
            ? [`${r.skipped_race} omitida(s) por conflicto`]
            : []),
          ...(r.errors.length ? [`${r.errors.length} con error`] : []),
        ].join(" · ") + ".");
        if (r.errors.length) {
          setError(r.errors.map((e) => `${e.codcli}: ${e.error}`).join(" · "));
        }
      } else if (mode === "by_contact_email") {
        const r = await bulkMatchByEmailApply(picked.map(([contact_id, s]) => ({
          contact_id, factusol_codcli: s.codcli, fields_to_sync: [...s.fields],
        })));
        setSummary([
          `${r.refreshed} actualizada(s)`,
          `${r.created_new_company} empresa(s) creada(s)`,
          ...(r.linked_existing_company
            ? [`${r.linked_existing_company} asignada(s) a empresa existente`]
            : []),
          // El desglose entre «a existente» y «a nueva» solo se enseña cuando
          // hay reasignaciones: si no, sería ruido en todos los lotes.
          ...(r.reassigned
            ? [`${r.reassigned} reasignada(s) a empresa correcta `
               + `(${r.reassigned_to_existing_company} a empresa existente, `
               + `${r.reassigned_to_new_company} a empresa nueva)`]
            : []),
          ...(r.skipped_already_linked_other
            ? [`${r.skipped_already_linked_other} omitida(s)`]
            : []),
          ...(r.errors.length ? [`${r.errors.length} con error`] : []),
        ].join(" · ") + ".");
        // Un omitido no es un fallo, pero el operador tiene que verlo: si no,
        // creería que se aplicó y no fue así.
        const problems = [
          ...r.results
            .filter((x) => x.result === "skipped_already_linked_other")
            .map((x) => x.detail ?? `${x.contact_id}: ${x.result}`),
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
  const orphans = mode === "import_orphans";
  const loaded = orphans ? orphanData : byEmail ? emailData : data;
  const masterCheckbox = (
    <MasterCheckbox checked={allSelected} indeterminate={someSelected}
                    disabled={applying || applicableIds.length === 0}
                    onChange={toggleAll} />
  );

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
                      setOrphanData(null);
                      setSelections({});
                    }}>
              <option value="by_contact_email">
                Contactos por email (recomendado)
              </option>
              <option value="by_company">Empresas por NIF/nombre</option>
              <option value="import_orphans">
                Importar clientes de FACTUSOL que no están en el CRM
              </option>
            </select>
          </label>
          {!byEmail && !orphans ? (
            <label className="field">
              <span>Empresas</span>
              <select value={filter} aria-label="Empresas"
                      onChange={(e) => setFilter(e.target.value as Filter)}>
                <option value="unlinked_only">Solo sin vincular</option>
                <option value="all">Todas (refresco masivo)</option>
              </select>
            </label>
          ) : null}
          {/* «Solo con diferencias» no aplica a la importación: no hay nada
              previo con qué compararse. Su filtro es el del email. */}
          {orphans ? (
            <label className="field-toggle">
              <input type="checkbox" checked={orphansOnlyWithEmail}
                     onChange={(e) => setOrphansOnlyWithEmail(e.target.checked)} />
              <span>Solo los que tengan email</span>
            </label>
          ) : (
            <label className="field-toggle">
              <input type="checkbox" checked={onlyWithDifferences}
                     onChange={(e) => setOnlyWithDifferences(e.target.checked)} />
              <span>Solo con diferencias</span>
            </label>
          )}
        </div>
        <button type="button" className="button" disabled={running}
                onClick={() => { setError(null); setSummary(null); runDryRun(); }}>
          {running ? "Analizando…" : "Ejecutar dry-run"}
        </button>
        <p className="muted small">
          {orphans
            ? "Trae al CRM los clientes de FACTUSOL que no tiene ninguna empresa. Crea la empresa con los datos de F_CLI y, si hay email, un contacto etiquetado «factusol_import». Las empresas creadas quedan con source «factusol_import»: filtrables en /companies?source=factusol_import."
            : byEmail
              ? "Busca el email del contacto en FACTUSOL y actualiza la empresa a la que pertenece. Match exacto: sin falsos positivos."
              : "Busca por NIF exacto o nombre parecido. El nombre parecido es una sugerencia — revísala antes de marcar."}
          {" "}El dry-run <strong>no modifica nada</strong>.
        </p>
      </section>

      {error ? <p className="form-error">{error}</p> : null}
      {summary ? <p className="form-info" role="status">{summary}</p> : null}

      {loaded ? (
        <section className="erp-card">
          {orphanData ? (
            <p className="muted small">
              <strong>{orphanData.orphans_to_import}</strong> F_CLI huérfana(s) ·{" "}
              {orphanData.with_email} con email · {orphanData.without_email} sin
              email. ({orphanData.total_factusol_clientes} cliente(s) en
              FACTUSOL, {orphanData.linked_already} ya vinculado(s) al CRM.)
            </p>
          ) : emailData ? (
            <p className="muted small">
              {emailData.total_contacts_with_email} contacto(s) con email ·{" "}
              <strong>{emailData.matches.length}</strong> con match{" "}
              {/* «sin empresa» es un SUBCONJUNTO de los con match, no una
                  tercera categoría: se anida para que no parezca que suman. */}
              (de los cuales {emailData.matches_without_company} sin empresa
              CRM) · {emailData.no_match_count} sin match.
              {emailData.truncated ? (
                <>
                  {" "}
                  <strong className="form-error">
                    Resultado truncado: hay más contactos sin evaluar.
                  </strong>
                </>
              ) : null}
            </p>
          ) : data ? (
            <p className="muted small">
              {data.total_crm_companies} empresa(s) analizada(s) ·{" "}
              {data.total_factusol_customers} cliente(s) en FACTUSOL ·{" "}
              <strong>{data.matches.length}</strong> con candidato ·{" "}
              {data.no_match.length} sin match.
            </p>
          ) : null}

          {(orphans ? orphanRows.length
                    : byEmail ? emailRows.length : companyRows.length) === 0 ? (
            <p className="muted">Sin resultados con estos filtros.</p>
          ) : (
            <table className="data-table">
              <thead>
                {orphans ? (
                  <tr>
                    <th>{masterCheckbox}</th><th>Nº FACTUSOL</th><th>Nombre</th>
                    <th>NIF</th><th>Ciudad</th><th>Email</th><th>Se creará</th>
                  </tr>
                ) : byEmail ? (
                  <tr>
                    <th>{masterCheckbox}</th><th>Contacto CRM</th><th>Email</th>
                    <th>Empresa actual</th><th>Cliente FACTUSOL</th>
                    <th>Difieren</th><th />
                  </tr>
                ) : (
                  <tr>
                    <th>{masterCheckbox}</th><th>Empresa CRM</th>
                    <th>Cliente FACTUSOL</th>
                    <th>Coincidencia</th><th>Difieren</th><th />
                  </tr>
                )}
              </thead>
              <tbody>
                {orphans
                  ? orphanRows.map((o) => (
                      <OrphanRowView
                        key={o.codcli}
                        row={o}
                        selection={selections[o.codcli ?? ""]}
                        onUpdate={(patch) => update(o.codcli ?? "", patch)}
                      />
                    ))
                  : byEmail
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
                    onClick={() => {
                      if (selectedCount >= CONFIRM_THRESHOLD) setConfirming(true);
                      else applySelected();
                    }}>
              {applying
                ? "Aplicando…"
                : `Aplicar seleccionadas (${selectedCount})`}
            </button>
          </div>
        </section>
      ) : null}

      {confirming ? (
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Confirmar aplicación masiva">
          <div className="modal-dialog">
            <h2>Confirmar aplicación masiva</h2>
            <p>
              Vas a aplicar <strong>{selectedCount}</strong> operaciones. Esto{" "}
              {orphans
                ? `creará ${selectedCount} empresas nuevas en el CRM con los `
                  + "datos de FACTUSOL"
                : `modificará ${selectedCount} empresas del CRM con los datos `
                  + "de FACTUSOL"}
              . Los cambios son reversibles solo via SQL manual (audit_logs).
              ¿Continuar?
            </p>
            <div className="modal-actions">
              <button type="button" className="button secondary"
                      onClick={() => setConfirming(false)}>
                Cancelar
              </button>
              <button type="button" className="button danger"
                      onClick={applySelected}>
                Sí, aplicar {selectedCount} cambios
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}

/** Fila del modo importación: un cliente de FACTUSOL que no está en el CRM.
 *
 *  No hay diff que enseñar —no existe nada previo— así que tampoco hay
 *  «Ver diferencias» ni casillas por campo: la empresa nace con todo F_CLI. */
function OrphanRowView({
  row, selection, onUpdate,
}: {
  row: FactusolOrphan;
  selection?: Selection;
  onUpdate: (patch: Partial<Selection>) => void;
}) {
  if (!selection) return null;
  const name = row.nofcli || row.noccli || `Cliente ${row.codcli}`;

  return (
    <tr>
      <td>
        <input type="checkbox" checked={selection.apply}
               aria-label={`Importar ${name}`}
               onChange={(e) => onUpdate({ apply: e.target.checked })} />
      </td>
      <td>nº {row.codcli}</td>
      <td><strong>{name}</strong></td>
      <td>{row.nifcli || "—"}</td>
      <td>{row.pobcli || "—"}</td>
      <td className="erp-bulk-email">
        {row.emacli || <span className="muted">(sin email)</span>}
      </td>
      <td>
        <span className="badge active">
          {row.will_create_contact
            ? "Se creará empresa + contacto"
            : "Se creará solo empresa"}
        </span>
      </td>
    </tr>
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
  // Ninguna fila se bloquea ya. C-5-fix2 quitó el bloqueo de «sin empresa»
  // (se crea una) y C-5-fix5 el de «vinculada a otro CODCLI» (se reasigna).
  const noCompany = !row.company_id;
  const reassign = isReassignment(row, selection.codcli);
  const reason = reassign
    ? `Este contacto está actualmente en la empresa «${row.company_name}», `
      + `pero su email apunta al cliente FACTUSOL ${selection.codcli}. `
      + "Al aplicar, el contacto se moverá a la empresa correcta (existente o "
      + `nueva). La empresa «${row.company_name}» original no se toca.`
    : noCompany
      ? "Se creará una empresa nueva con los datos de FACTUSOL."
      : "";

  return (
    <>
      <tr>
        <td>
          <input type="checkbox" checked={selection.apply} title={reason}
                 aria-label={`Aplicar a ${row.contact_name}`}
                 onChange={(e) => onUpdate({ apply: e.target.checked })} />
        </td>
        <td><strong>{row.contact_name}</strong></td>
        <td className="erp-bulk-email">{row.contact_email}</td>
        <td>
          {row.company_name ? (
            <>
              {row.company_name}
              {reassign ? (
                <>
                  <br />
                  <span className="badge active" title={reason}>
                    Reasignar → {selection.codcli}
                  </span>
                </>
              ) : null}
            </>
          ) : (
            <span className="badge active">Se creará empresa</span>
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
            {reassign ? <p className="form-info">{reason}</p> : null}
            {noCompany ? (
              <p className="form-info">
                El contacto no tiene empresa: se creará una con todos los datos
                de FACTUSOL y se le asignará. Si ya existe una empresa vinculada
                a ese cliente, se usará esa en vez de crear otra.
              </p>
            ) : null}
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
