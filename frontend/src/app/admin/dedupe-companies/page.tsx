"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import {
  findDuplicateCompanies,
  mergeDuplicateCompanies,
  pickDefaultKeep,
  type DuplicateCompany,
  type DuplicateGroup,
  type DuplicatesResult,
} from "../../lib/companiesApi";
import { extractErrorMessage } from "../../lib/errors";

/** Campos que la principal completa desde las absorbidas, con su etiqueta.
 *  Mismo orden que `FILLABLE_FIELDS` en el backend. */
const FILLABLE: [keyof DuplicateCompany, string][] = [
  ["address_line", "dirección"], ["city", "ciudad"], ["postal_code", "CP"],
  ["state", "provincia"], ["country", "país"], ["website", "web"],
  ["domain", "dominio"], ["notes", "notas"],
];

/** Qué aportaría esta empresa si se absorbiese: campos que ella tiene y la
 *  principal no. Es lo que decide si vale la pena mirar el grupo. */
function contributions(row: DuplicateCompany, keep: DuplicateCompany): string[] {
  if (row.id === keep.id) return [];
  return FILLABLE
    .filter(([field]) => !String(keep[field] ?? "").trim()
                         && String(row[field] ?? "").trim())
    .map(([, label]) => label);
}

/** Checkbox de tres estados: `indeterminate` es propiedad del DOM, no atributo. */
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
    <label className="field-toggle">
      <input ref={ref} type="checkbox" checked={checked} disabled={disabled}
             aria-label="Marcar todos los grupos"
             onChange={(e) => onChange(e.target.checked)} />
      <span>Marcar todos los grupos</span>
    </label>
  );
}

/** Deduplicar empresas por NIF (Fase C · C-7).
 *
 *  Tras los imports masivos de C-6 aparecieron empresas repetidas con el mismo
 *  `tax_id` — «Exatronic Lda» dos veces, porque en FACTUSOL hay dos CODCLI con
 *  el mismo NIF. Aquí se agrupan y se fusionan eligiendo la principal.
 *
 *  El apply **borra** empresas: por eso nada se fusiona sin marcarlo y el modal
 *  de confirmación dice el número exacto de filas que van a desaparecer. */
export default function DedupeCompaniesPage() {
  const [data, setData] = useState<DuplicatesResult | null>(null);
  /** `tax_id → id de la empresa que se queda`. */
  const [keeps, setKeeps] = useState<Record<string, string>>({});
  /** `tax_id → marcado para fusionar`. */
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [merging, setMerging] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);

  /** Relee y repuebla. NO toca `summary`/`error`: se llama también como
   *  refresco después de fusionar, y borrar el resultado ahí dejaría al
   *  operador sin saber qué pasó. */
  async function search() {
    setRunning(true);
    try {
      const result = await findDuplicateCompanies();
      setData(result);
      setKeeps(Object.fromEntries(
        result.groups.map((g) => [g.tax_id, pickDefaultKeep(g.companies)])));
      setPicked({});
      setExpanded(null);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo buscar duplicados."));
    } finally {
      setRunning(false);
    }
  }

  const groups = useMemo(() => data?.groups ?? [], [data]);
  const pickedGroups = useMemo(
    () => groups.filter((g) => picked[g.tax_id]), [groups, picked]);

  /** Lo que el modal tiene que decir antes de borrar nada. */
  const impact = useMemo(() => {
    let companies = 0, contacts = 0, orders = 0, tasks = 0;
    for (const g of pickedGroups) {
      for (const c of g.companies) {
        if (c.id === keeps[g.tax_id]) continue;
        companies += 1;
        contacts += c.contacts_count;
        orders += c.orders_count;
        tasks += c.tasks_count;
      }
    }
    return { groups: pickedGroups.length, companies, contacts, orders, tasks };
  }, [pickedGroups, keeps]);

  const allPicked = groups.length > 0 && pickedGroups.length === groups.length;
  const somePicked = pickedGroups.length > 0 && !allPicked;

  function toggleAll(checked: boolean) {
    setPicked(checked
      ? Object.fromEntries(groups.map((g) => [g.tax_id, true]))
      : {});
  }

  async function mergePicked() {
    setConfirming(false);
    const operations = pickedGroups.map((g) => ({
      keep_id: keeps[g.tax_id],
      merge_ids: g.companies.filter((c) => c.id !== keeps[g.tax_id])
        .map((c) => c.id),
    })).filter((op) => op.keep_id && op.merge_ids.length > 0);
    if (operations.length === 0) return;

    setMerging(true);
    setError(null);
    try {
      const r = await mergeDuplicateCompanies(operations);
      const descartados = r.results.flatMap((x) => x.discarded_factusol_codclis);
      setSummary([
        `${r.merged_groups} grupo(s) fusionado(s)`,
        `${r.companies_deleted} empresa(s) borrada(s)`,
        `${r.contacts_moved} contacto(s), ${r.orders_moved} pedido(s) y `
        + `${r.tasks_moved} tarea(s) movidos`,
        ...(r.errors.length ? [`${r.errors.length} con error`] : []),
      ].join(" · ") + ".");
      const problems = [
        ...r.errors.map((e) => `${e.keep_id}: ${e.error}`),
        // Puede haber facturación colgando de esos CODCLI: si no se dice,
        // nadie se entera de que se perdió el vínculo.
        ...(descartados.length
          ? [`Códigos FACTUSOL descartados al fusionar: ${descartados.join(", ")}.`]
          : []),
      ];
      if (problems.length) setError(problems.join(" · "));
      await search();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo fusionar."));
    } finally {
      setMerging(false);
    }
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Deduplicar empresas por CIF"
        eyebrow="Admin"
        description="Agrupa las empresas del CRM que comparten NIF y las fusiona en una. Nada se fusiona sin marcarlo."
        crumbs={[{ label: "Admin" }, { label: "Deduplicar empresas" }]}
      />

      <section className="erp-card">
        <button type="button" className="button" disabled={running}
                onClick={() => { setError(null); setSummary(null); search(); }}>
          {running ? "Buscando…" : "Buscar duplicados por CIF"}
        </button>
        <p className="muted small">
          Solo agrupa por <strong>NIF exacto</strong>: por nombre o email habría
          falsos positivos, y aquí se <strong>borran</strong> empresas. Las que
          no tienen NIF se ignoran. La búsqueda <strong>no modifica nada</strong>.
        </p>
      </section>

      {error ? <p className="form-error">{error}</p> : null}
      {summary ? <p className="form-info" role="status">{summary}</p> : null}

      {data ? (
        <section className="erp-card">
          <p className="muted small">
            <strong>{data.total_groups}</strong> grupo(s) de duplicados ·{" "}
            {data.total_companies_involved} empresa(s) afectada(s).
          </p>

          {groups.length === 0 ? (
            <p className="muted">No hay empresas duplicadas por NIF.</p>
          ) : (
            <>
              <MasterCheckbox checked={allPicked} indeterminate={somePicked}
                              disabled={merging} onChange={toggleAll} />
              {groups.map((g) => (
                <GroupCard
                  key={g.tax_id}
                  group={g}
                  keepId={keeps[g.tax_id] ?? ""}
                  picked={Boolean(picked[g.tax_id])}
                  expanded={expanded === g.tax_id}
                  onToggleExpand={() => setExpanded(
                    expanded === g.tax_id ? null : g.tax_id)}
                  onPick={(v) => setPicked((p) => ({ ...p, [g.tax_id]: v }))}
                  onKeep={(id) => setKeeps((k) => ({ ...k, [g.tax_id]: id }))}
                />
              ))}
            </>
          )}

          <div className="form-actions">
            <button type="button" className="button danger"
                    disabled={merging || impact.groups === 0}
                    onClick={() => setConfirming(true)}>
              {merging
                ? "Fusionando…"
                : `Fusionar seleccionadas (${impact.groups})`}
            </button>
          </div>
        </section>
      ) : null}

      {confirming ? (
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Confirmar fusión">
          <div className="modal-dialog">
            <h2>Confirmar fusión</h2>
            <p>
              Vas a fusionar <strong>{impact.groups}</strong> grupo(s). Esto{" "}
              <strong>borrará {impact.companies} empresa(s)</strong> del CRM y
              moverá {impact.contacts} contacto(s), {impact.orders} pedido(s) y{" "}
              {impact.tasks} tarea(s) a la principal de cada grupo.
            </p>
            <p className="muted small">
              Los datos de las borradas quedan en <code>audit_logs</code>: son
              reversibles solo via SQL manual. ¿Continuar?
            </p>
            <div className="modal-actions">
              <button type="button" className="button secondary"
                      onClick={() => setConfirming(false)}>
                Cancelar
              </button>
              <button type="button" className="button danger"
                      onClick={mergePicked}>
                Sí, fusionar y borrar {impact.companies}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}

function GroupCard({
  group, keepId, picked, expanded, onToggleExpand, onPick, onKeep,
}: {
  group: DuplicateGroup;
  keepId: string;
  picked: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
  onPick: (picked: boolean) => void;
  onKeep: (id: string) => void;
}) {
  const keep = group.companies.find((c) => c.id === keepId)
    ?? group.companies[0];

  return (
    <div className="erp-card erp-dedupe-group">
      <div className="form-row">
        <label className="field-toggle">
          <input type="checkbox" checked={picked}
                 aria-label={`Marcar grupo ${group.tax_id} para fusionar`}
                 onChange={(e) => onPick(e.target.checked)} />
          <span>
            <strong>CIF: {group.tax_id}</strong> · {group.companies.length}{" "}
            empresas
          </span>
        </label>
        <button type="button" className="button small secondary"
                aria-expanded={expanded} onClick={onToggleExpand}>
          {expanded ? "Ocultar" : "Ver detalle"}
        </button>
      </div>

      {expanded ? (
        <table className="data-table">
          <thead>
            <tr>
              <th>Mantener</th><th>Nombre</th><th>Ciudad</th>
              <th>Nº FACTUSOL</th><th>Creada</th>
              <th>Contactos</th><th>Pedidos</th><th>Aporta</th>
            </tr>
          </thead>
          <tbody>
            {group.companies.map((c) => {
              const aporta = contributions(c, keep);
              return (
                <tr key={c.id} className={c.id === keepId ? "" : "muted"}>
                  <td>
                    <input type="radio" name={`keep-${group.tax_id}`}
                           checked={c.id === keepId}
                           aria-label={`Mantener ${c.name} (${c.id})`}
                           onChange={() => onKeep(c.id)} />
                  </td>
                  <td><strong>{c.name}</strong></td>
                  <td>{c.city || "—"}</td>
                  <td>{c.factusol_company_id || "—"}</td>
                  <td>{new Date(c.created_at).toLocaleDateString("es-ES")}</td>
                  <td>{c.contacts_count}</td>
                  <td>{c.orders_count}</td>
                  <td>
                    {aporta.length ? (
                      <span className="badge warn">
                        aportará: {aporta.join(", ")}
                      </span>
                    ) : c.id === keepId ? (
                      <span className="badge ok">principal</span>
                    ) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
