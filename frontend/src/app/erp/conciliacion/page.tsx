"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { getCurrentUser, type User } from "../../lib/api";
import {
  confirmAllHighBank,
  confirmBankMovement,
  createBankAccount,
  createBankRule,
  deleteBankAccount,
  deleteBankRule,
  discardBankMovement,
  downloadBankExport,
  ERP_EDIT_ROLES,
  getContrapartidas,
  importBankStatement,
  listBankAccounts,
  listBankMovements,
  listBankRules,
  listFactusolDocuments,
  listSuggestedBankAccounts,
  reassignBankMovement,
  reopenBankMovement,
  runBankMatch,
  saveBlob,
  updateBankAccount,
  type BankAccount,
  type BankAccountSuggestion,
  type BankConfidence,
  type BankImportSummary,
  type BankMovement,
  type BankMovementFilters,
  type BankMovementsPage,
  type BankReassignTarget,
  type BankRule,
  type Contrapartida,
  type FactusolDocument,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const CONFIDENCE_LABEL: Record<BankConfidence, { label: string; tone: string }> = {
  alta: { label: "Alta", tone: "ok" },
  media: { label: "Media", tone: "warn" },
  baja: { label: "Baja", tone: "muted" },
};

const STATUS_LABEL: Record<BankMovement["status"], string> = {
  pending: "Pendiente",
  reconciled: "Conciliado",
  discarded: "Descartado",
};

function eur(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : `${n.toFixed(2)} €`;
}

/** ERP-F4-A — Conciliación bancaria. Importa el extracto, PROPONE el casado
 *  con las facturas pendientes y deja que la persona decida. Nada se concilia
 *  solo. NO escribe en FACTUSOL (eso va en F-4-B). */
export default function ConciliacionPage() {
  const [user, setUser] = useState<User | null>(null);
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [suggested, setSuggested] = useState<BankAccountSuggestion[]>([]);
  const [rules, setRules] = useState<BankRule[]>([]);
  const [page, setPage] = useState<BankMovementsPage | null>(null);
  const [filters, setFilters] = useState<BankMovementFilters>({ status: "pending" });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [importSummary, setImportSummary] = useState<BankImportSummary | null>(null);
  const [tab, setTab] = useState<"revision" | "cuentas" | "reglas">("revision");
  // Acciones por movimiento (elegir otra / repartir / descartar).
  const [reassignFor, setReassignFor] = useState<BankMovement | null>(null);
  const [discardFor, setDiscardFor] = useState<BankMovement | null>(null);

  const canEdit = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);
  const isAdmin = user?.role === "admin";

  const loadMovements = useCallback(async () => {
    try {
      setPage(await listBankMovements({ ...filters, limit: 200 }));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar los movimientos."));
    }
  }, [filters]);

  const loadAccounts = useCallback(async () => {
    try {
      setAccounts(await listBankAccounts());
      setSuggested(await listSuggestedBankAccounts().catch(() => []));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar las cuentas."));
    }
  }, []);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
    void loadAccounts();
    listBankRules().then(setRules).catch(() => setRules([]));
  }, [loadAccounts]);

  useEffect(() => {
    void loadMovements();
  }, [loadMovements]);

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(label);
      await loadMovements();
    } catch (e) {
      setError(extractErrorMessage(e, "La acción falló."));
    } finally {
      setBusy(false);
    }
  }

  async function onImport(file: File) {
    setBusy(true);
    setError(null);
    setImportSummary(null);
    try {
      const summary = await importBankStatement(file, { accountId: filters.account_id });
      setImportSummary(summary);
      await loadMovements();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo importar el extracto."));
    } finally {
      setBusy(false);
    }
  }

  const counters = page?.counters;

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Conciliación bancaria"
        eyebrow="ERP"
        description="Importa el extracto, revisa las propuestas de cobro y confirma. Nada se concilia solo; FACTUSOL no se toca."
      />

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}

      {counters ? (
        <section className="erp-home-widgets" aria-label="Resumen">
          <span className="erp-home-stat">
            <span className="erp-home-stat-value">{counters.pending}</span>
            <span className="erp-home-stat-label">Por conciliar · {eur(counters.pending_amount)}</span>
          </span>
          <span className="erp-home-stat">
            <span className="erp-home-stat-value">{counters.reconciled}</span>
            <span className="erp-home-stat-label">Conciliados</span>
          </span>
          <span className="erp-home-stat">
            <span className="erp-home-stat-value">{counters.discarded}</span>
            <span className="erp-home-stat-label">Descartados</span>
          </span>
        </section>
      ) : null}

      <nav className="erp-tabs" aria-label="Secciones">
        {(["revision", "cuentas", "reglas"] as const).map((t) => (
          <button
            key={t}
            type="button"
            className={`button small ${tab === t ? "" : "secondary"}`}
            onClick={() => setTab(t)}
          >
            {t === "revision" ? "Revisión" : t === "cuentas" ? "Cuentas" : "Reglas aprendidas"}
          </button>
        ))}
      </nav>

      {tab === "cuentas" ? (
        <AccountsPanel
          accounts={accounts}
          suggested={suggested}
          isAdmin={isAdmin}
          onChanged={loadAccounts}
          onError={setError}
        />
      ) : null}

      {tab === "reglas" ? (
        <RulesPanel
          rules={rules}
          canEdit={canEdit}
          onChanged={async () => {
            setRules(await listBankRules().catch(() => []));
          }}
          onError={setError}
        />
      ) : null}

      {tab === "revision" ? (
        <>
          <section className="erp-card">
            <h3>Importar extracto</h3>
            <div className="erp-doc-filters">
              <label className="field">
                <span>Cuenta</span>
                <select
                  aria-label="Cuenta"
                  value={filters.account_id ?? ""}
                  onChange={(e) => setFilters((f) => ({ ...f, account_id: e.target.value || undefined }))}
                >
                  <option value="">Todas / detectar por IBAN</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>{a.name} · {a.iban}</option>
                  ))}
                </select>
              </label>
              {canEdit ? (
                <label className="field">
                  <span>Fichero (.xlsx / .csv)</span>
                  <input
                    type="file"
                    accept=".xlsx,.csv"
                    aria-label="Extracto bancario"
                    disabled={busy}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void onImport(f);
                      e.currentTarget.value = "";
                    }}
                  />
                </label>
              ) : null}
              {canEdit ? (
                <button
                  type="button"
                  className="button small secondary"
                  disabled={busy}
                  onClick={() => act("Propuestas recalculadas.", () => runBankMatch(filters.account_id))}
                >
                  Recalcular propuestas
                </button>
              ) : null}
              {filters.account_id ? (
                <button
                  type="button"
                  className="button small secondary"
                  disabled={busy}
                  onClick={async () => {
                    try {
                      const blob = await downloadBankExport(filters.account_id!, {
                        desde: filters.desde, hasta: filters.hasta,
                      });
                      saveBlob(blob, "extracto_conciliado.xlsx");
                    } catch (e) {
                      setError(extractErrorMessage(e, "No se pudo exportar."));
                    }
                  }}
                >
                  Descargar Excel conciliado
                </button>
              ) : null}
            </div>
            {importSummary ? (
              <p className="muted small" role="status">
                Importados <strong>{importSummary.imported}</strong> de {importSummary.total_rows} ·
                descartados por repetidos <strong>{importSummary.duplicates}</strong>
                {importSummary.matching?.ok ? (
                  <>
                    {" "}· propuestas <strong>{importSummary.matching.proposed}</strong>
                    {" "}(alta {importSummary.matching.by_confidence?.alta ?? 0},
                    media {importSummary.matching.by_confidence?.media ?? 0},
                    baja {importSummary.matching.by_confidence?.baja ?? 0}) ·
                    sin propuesta {importSummary.matching.no_proposal} ·
                    excluidos {importSummary.matching.excluded}
                  </>
                ) : importSummary.matching ? (
                  <> · {importSummary.matching.detail}</>
                ) : null}
              </p>
            ) : null}
          </section>

          <section className="erp-card">
            <div className="erp-doc-filters">
              <label className="field">
                <span>Desde</span>
                <input type="date" aria-label="Desde" value={filters.desde ?? ""}
                  onChange={(e) => setFilters((f) => ({ ...f, desde: e.target.value || undefined }))} />
              </label>
              <label className="field">
                <span>Hasta</span>
                <input type="date" aria-label="Hasta" value={filters.hasta ?? ""}
                  onChange={(e) => setFilters((f) => ({ ...f, hasta: e.target.value || undefined }))} />
              </label>
              <label className="field">
                <span>Confianza</span>
                <select aria-label="Confianza" value={filters.confidence ?? ""}
                  onChange={(e) => setFilters((f) => ({
                    ...f, confidence: (e.target.value || undefined) as BankMovementFilters["confidence"],
                  }))}>
                  <option value="">Todas</option>
                  <option value="alta">Alta</option>
                  <option value="media">Media</option>
                  <option value="baja">Baja</option>
                  <option value="none">Sin propuesta</option>
                </select>
              </label>
              <label className="field">
                <span>Estado</span>
                <select aria-label="Estado" value={filters.status ?? ""}
                  onChange={(e) => setFilters((f) => ({
                    ...f, status: (e.target.value || undefined) as BankMovementFilters["status"],
                  }))}>
                  <option value="">Todos</option>
                  <option value="pending">Pendiente</option>
                  <option value="reconciled">Conciliado</option>
                  <option value="discarded">Descartado</option>
                </select>
              </label>
              {canEdit ? (
                <button
                  type="button"
                  className="button small"
                  disabled={busy}
                  onClick={() => {
                    if (!window.confirm("¿Confirmar TODAS las propuestas de confianza alta? Es una decisión tuya, no automática.")) return;
                    void act("Confirmadas las de confianza alta.", () => confirmAllHighBank(filters.account_id));
                  }}
                >
                  Confirmar todas las de confianza alta
                </button>
              ) : null}
            </div>

            {!page ? <p className="muted">Cargando…</p> : page.items.length === 0 ? (
              <p className="muted">Sin movimientos que casen los filtros.</p>
            ) : (
              <table className="data-table erp-bank-table">
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Concepto / pagador</th>
                    <th>Importe</th>
                    <th>Propuesta</th>
                    <th>Confianza</th>
                    <th>Estado</th>
                    <th>Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((m) => (
                    <tr key={m.id} className={`is-${m.status}`}>
                      <td>{m.fecha_oper}</td>
                      <td>
                        <div>{m.concepto}</div>
                        {m.payer_name ? <div className="muted small">Pagador: {m.payer_name}</div> : null}
                      </td>
                      <td><strong>{eur(m.importe)}</strong></td>
                      <td>
                        {(m.status === "reconciled" ? m.reconciled : m.proposals).length === 0 ? (
                          <span className="muted">Sin propuesta</span>
                        ) : (
                          <ul className="erp-bank-proposals">
                            {(m.status === "reconciled" ? m.reconciled : m.proposals).map((p) => (
                              <li key={p.id}>
                                <strong>{p.numero}</strong> · {p.cliente_nombre ?? "—"} · {eur(p.importe)}
                                {p.reason ? <div className="muted small">{p.reason}</div> : null}
                              </li>
                            ))}
                          </ul>
                        )}
                        {m.status === "discarded" && m.discard_reason ? (
                          <div className="muted small">Motivo: {m.discard_reason}</div>
                        ) : null}
                      </td>
                      <td>
                        {m.confidence ? (
                          <span className={`badge ${CONFIDENCE_LABEL[m.confidence].tone}`}>
                            {CONFIDENCE_LABEL[m.confidence].label}
                          </span>
                        ) : "—"}
                      </td>
                      <td>{STATUS_LABEL[m.status]}</td>
                      <td className="erp-bank-actions">
                        {canEdit && m.status === "pending" ? (
                          <>
                            {m.proposals.length > 0 ? (
                              <button type="button" className="button small" disabled={busy}
                                onClick={() => act(`Conciliado ${m.fecha_oper}.`, () => confirmBankMovement(m.id))}>
                                Confirmar
                              </button>
                            ) : null}
                            <button type="button" className="button small secondary" disabled={busy}
                              onClick={() => setReassignFor(m)}>
                              Elegir / repartir
                            </button>
                            <button type="button" className="button small secondary" disabled={busy}
                              onClick={() => setDiscardFor(m)}>
                              No es cobro
                            </button>
                          </>
                        ) : null}
                        {canEdit && m.status !== "pending" ? (
                          <button type="button" className="button small secondary" disabled={busy}
                            onClick={() => act("Movimiento reabierto.", () => reopenBankMovement(m.id))}>
                            Reabrir
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      ) : null}

      {reassignFor ? (
        <ReassignModal
          movement={reassignFor}
          onClose={() => setReassignFor(null)}
          onDone={async (targets, learn) => {
            const mov = reassignFor;
            setReassignFor(null);
            await act(`Conciliado ${mov.fecha_oper} a mano.`, () => reassignBankMovement(mov.id, targets, learn));
          }}
        />
      ) : null}

      {discardFor ? (
        <DiscardModal
          movement={discardFor}
          onClose={() => setDiscardFor(null)}
          onDone={async (reason, learn) => {
            const mov = discardFor;
            setDiscardFor(null);
            await act("Marcado como «no es cobro de cliente».", () => discardBankMovement(mov.id, reason, learn));
            setRules(await listBankRules().catch(() => []));
          }}
        />
      ) : null}
    </main>
  );
}

// --- cuentas -----------------------------------------------------------------

function AccountsPanel({
  accounts, suggested, isAdmin, onChanged, onError,
}: {
  accounts: BankAccount[];
  suggested: BankAccountSuggestion[];
  isAdmin: boolean;
  onChanged: () => Promise<void>;
  onError: (msg: string) => void;
}) {
  const [name, setName] = useState("");
  const [iban, setIban] = useState("");
  const [bank, setBank] = useState("");
  const [serie, setSerie] = useState("");
  const [contrapartida, setContrapartida] = useState("");
  // ERP-F5 — catálogo de contrapartidas de cobro (configurable en /erp/settings).
  const [contrapartidas, setContrapartidas] = useState<Contrapartida[]>([]);

  useEffect(() => {
    getContrapartidas().then(setContrapartidas).catch(() => setContrapartidas([]));
  }, []);

  async function add(payload: {
    name: string; iban: string; bank_name?: string | null; bic?: string | null;
    currency?: string; serie?: number | null; contrapartida_codigo?: string | null;
  }) {
    try {
      await createBankAccount({ currency: "EUR", ...payload });
      setName(""); setIban(""); setBank(""); setSerie(""); setContrapartida("");
      await onChanged();
    } catch (e) {
      onError(extractErrorMessage(e, "No se pudo dar de alta la cuenta."));
    }
  }

  async function link(accountId: string, codigo: string) {
    try {
      await updateBankAccount(accountId, { contrapartida_codigo: codigo || null });
      await onChanged();
    } catch (e) {
      onError(extractErrorMessage(e, "No se pudo enlazar la contrapartida."));
    }
  }

  const contrapartidaSelect = (value: string, onChange: (v: string) => void, label: string) => (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Sin contrapartida</option>
      {contrapartidas.map((c) => (
        <option key={c.codigo} value={c.codigo}>{c.codigo} · {c.nombre}</option>
      ))}
    </select>
  );

  return (
    <section className="erp-card">
      <h3>Cuentas bancarias</h3>
      <p className="muted small">
        Cada cuenta se enlaza con su <strong>contrapartida de cobro</strong> de
        FACTUSOL (el destino donde entra el dinero: «6 Bomedia Sabadell», «2 MQ
        Europe Belfius», «8 Streamtec Sabadell»…). Es lo que usará el registro
        del cobro. El catálogo se edita en Configuración ERP.
      </p>
      {accounts.length === 0 ? <p className="muted">Sin cuentas dadas de alta.</p> : (
        <ul className="item-list">
          {accounts.map((a) => (
            <li key={a.id}>
              <strong>{a.name}</strong> · {a.iban}{a.bank_name ? ` · ${a.bank_name}` : ""}
              {a.serie ? ` · serie ${a.serie}` : ""}
              {" · contrapartida: "}
              {isAdmin
                ? contrapartidaSelect(
                  a.contrapartida_codigo ?? "",
                  (v) => { void link(a.id, v); },
                  `Contrapartida de ${a.name}`,
                )
                : (a.contrapartida_nombre
                  ? `${a.contrapartida_codigo} · ${a.contrapartida_nombre}`
                  : (a.contrapartida_codigo ?? "sin enlazar"))}
              {isAdmin ? (
                <button type="button" className="button small secondary" style={{ marginLeft: 8 }}
                  onClick={async () => {
                    if (!window.confirm(`¿Dar de baja la cuenta ${a.name}? Se borran sus movimientos importados.`)) return;
                    try { await deleteBankAccount(a.id); await onChanged(); }
                    catch (e) { onError(extractErrorMessage(e, "No se pudo dar de baja.")); }
                  }}>
                  Dar de baja
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {isAdmin && suggested.length > 0 ? (
        <>
          <h4>Sugeridas por FACTUSOL (F_BAN)</h4>
          <ul className="item-list">
            {suggested.map((s) => (
              <li key={s.iban}>
                {s.name} · {s.iban}
                <button type="button" className="button small" style={{ marginLeft: 8 }}
                  onClick={() => add({ name: s.name, iban: s.iban, bank_name: s.bank_name, bic: s.bic })}>
                  Dar de alta
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {isAdmin ? (
        <form className="form-card embedded" onSubmit={(e) => {
          e.preventDefault();
          void add({
            name, iban, bank_name: bank || null, serie: serie ? Number(serie) : null,
            contrapartida_codigo: contrapartida || null,
          });
        }}>
          <h4>Nueva cuenta</h4>
          <label>Nombre<input value={name} onChange={(e) => setName(e.target.value)} required /></label>
          <label>IBAN<input value={iban} onChange={(e) => setIban(e.target.value)} required /></label>
          <label>Banco<input value={bank} onChange={(e) => setBank(e.target.value)} /></label>
          <label>Serie (empresa emisora)<input value={serie} onChange={(e) => setSerie(e.target.value)} inputMode="numeric" /></label>
          <label>
            Contrapartida de cobro (FACTUSOL)
            {contrapartidaSelect(contrapartida, setContrapartida, "Contrapartida de la nueva cuenta")}
          </label>
          <button className="button" type="submit">Dar de alta</button>
        </form>
      ) : null}
    </section>
  );
}

// --- reglas aprendidas ----------------------------------------------------------

function RulesPanel({
  rules, canEdit, onChanged, onError,
}: {
  rules: BankRule[];
  canEdit: boolean;
  onChanged: () => Promise<void>;
  onError: (msg: string) => void;
}) {
  const [pattern, setPattern] = useState("");
  return (
    <section className="erp-card">
      <h3>Reglas aprendidas</h3>
      <p className="muted small">
        Lo que decides se recuerda: exclusiones (no es cobro de cliente) y pagador → cliente.
        Son configurables, no están cableadas.
      </p>
      <ul className="item-list">
        {rules.map((r) => (
          <li key={r.id}>
            <span className="badge muted">{r.kind === "exclude_pattern" ? "Excluir" : "Pagador → cliente"}</span>{" "}
            <strong>{r.pattern}</strong>
            {r.client_nombre ? ` → ${r.client_nombre}` : ""}
            {r.note ? <span className="muted small"> · {r.note}</span> : null}
            {canEdit ? (
              <button type="button" className="button small secondary" style={{ marginLeft: 8 }}
                onClick={async () => {
                  try { await deleteBankRule(r.id); await onChanged(); }
                  catch (e) { onError(extractErrorMessage(e, "No se pudo borrar la regla.")); }
                }}>
                Quitar
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {canEdit ? (
        <form className="form-card embedded" onSubmit={async (e) => {
          e.preventDefault();
          try {
            await createBankRule({ kind: "exclude_pattern", pattern, note: "añadida a mano" });
            setPattern("");
            await onChanged();
          } catch (err) { onError(extractErrorMessage(err, "No se pudo crear la regla.")); }
        }}>
          <label>Nueva exclusión (texto del concepto/pagador)
            <input value={pattern} onChange={(e) => setPattern(e.target.value)} required />
          </label>
          <button className="button small" type="submit">Añadir</button>
        </form>
      ) : null}
    </section>
  );
}

// --- elegir otra factura / repartir --------------------------------------------

function ReassignModal({
  movement, onClose, onDone,
}: {
  movement: BankMovement;
  onClose: () => void;
  onDone: (targets: BankReassignTarget[], learn: boolean) => Promise<void>;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<FactusolDocument[]>([]);
  const [targets, setTargets] = useState<BankReassignTarget[]>([]);
  const [learn, setLearn] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      listFactusolDocuments("facturas", { q: q.trim() || undefined, limit: 15 })
        .then((r) => setResults(r.items))
        .catch(() => setResults([]));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [q]);

  const assigned = targets.reduce((s, t) => s + t.importe, 0);
  const remaining = Math.round((movement.importe - assigned) * 100) / 100;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Elegir factura">
      <div className="modal-dialog erp-emit-modal">
        <h2>Elegir / repartir · {eur(movement.importe)}</h2>
        <p className="muted small">{movement.concepto}</p>
        <label className="field">
          <span>Buscar factura</span>
          <input aria-label="Buscar factura" value={q} onChange={(e) => setQ(e.target.value)} placeholder="número, cliente…" />
        </label>
        <ul className="item-list">
          {results.filter((d) => d.serie !== null && typeof d.codigo === "number").map((d) => (
            <li key={`${d.serie}-${d.codigo}`}>
              <strong>{d.numero}</strong> · {d.cliente_nombre ?? "—"} · saldo {eur(d.saldo_pendiente ?? d.total)}
              <button type="button" className="button small secondary" style={{ marginLeft: 8 }}
                onClick={() => setTargets((t) => [...t, {
                  serie: d.serie as number, codigo: d.codigo as number, numero: d.numero,
                  importe: Math.min(remaining, d.saldo_pendiente ?? d.total ?? remaining),
                  cliente_nombre: d.cliente_nombre, cliente_codigo: d.cliente_codigo,
                }])}>
                Añadir
              </button>
            </li>
          ))}
        </ul>
        {targets.length > 0 ? (
          <ul className="item-list">
            {targets.map((t, i) => (
              <li key={`${t.serie}-${t.codigo}-${i}`}>
                {t.numero} ·{" "}
                <input type="number" step="0.01" aria-label={`Importe ${t.numero}`} value={t.importe}
                  onChange={(e) => setTargets((ts) => ts.map((x, j) => j === i ? { ...x, importe: Number(e.target.value) } : x))}
                  style={{ width: 110 }} />
                <button type="button" className="button small secondary" style={{ marginLeft: 8 }}
                  onClick={() => setTargets((ts) => ts.filter((_, j) => j !== i))}>Quitar</button>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="muted small">Asignado {eur(assigned)} · resta {eur(remaining)}</p>
        <label className="muted small">
          <input type="checkbox" checked={learn} onChange={(e) => setLearn(e.target.checked)} />{" "}
          Recordar este pagador → cliente para futuras importaciones
        </label>
        {err ? <p className="form-error">{err}</p> : null}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>Cancelar</button>
          <button type="button" className="button" disabled={targets.length === 0 || Math.abs(remaining) > 0.01}
            onClick={async () => {
              setErr(null);
              try { await onDone(targets, learn); }
              catch (e) { setErr(extractErrorMessage(e, "No se pudo conciliar.")); }
            }}>
            Confirmar reparto
          </button>
        </div>
      </div>
    </div>
  );
}

// --- descartar ------------------------------------------------------------------

function DiscardModal({
  movement, onClose, onDone,
}: {
  movement: BankMovement;
  onClose: () => void;
  onDone: (reason: string, learn: boolean) => Promise<void>;
}) {
  const [reason, setReason] = useState("no es cobro de cliente");
  const [learn, setLearn] = useState(true);
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="No es cobro de cliente">
      <div className="modal-dialog erp-emit-modal">
        <h2>No es un cobro de cliente</h2>
        <p className="muted small">{movement.concepto} · {eur(movement.importe)}</p>
        <label className="field">
          <span>Motivo</span>
          <input aria-label="Motivo" value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <label className="muted small">
          <input type="checkbox" checked={learn} onChange={(e) => setLearn(e.target.checked)} />{" "}
          Recordarlo: no volver a proponer este pagador
        </label>
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>Cancelar</button>
          <button type="button" className="button" disabled={!reason.trim()}
            onClick={() => void onDone(reason.trim(), learn)}>
            Descartar
          </button>
        </div>
      </div>
    </div>
  );
}
