"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ModalCloseButton } from "../../components/ModalCloseButton";
import { PageHeader } from "../../components/PageHeader";
import { useModalBehaviour } from "../../components/useModalBehaviour";
import { getCurrentUser, type User } from "../../lib/api";
import { Cap, can } from "../../lib/capabilities";
import {
  confirmAllHighBank,
  confirmBankMovement,
  createBankAccount,
  createBankRule,
  deleteBankAccount,
  deleteBankRule,
  discardBankMovement,
  downloadBankCobrosCsv,
  downloadBankExport,
  escribirBankDrive,
  getBankDrive,
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
  traerBankDrive,
  updateBankAccount,
  type BankAccount,
  type BankAccountSuggestion,
  type BankCobrosCsv,
  type BankConfidence,
  type BankDriveEscritura,
  type BankDriveInfo,
  type BankDriveTraida,
  type BankExportPlan,
  type BankImportSummary,
  type BankMovement,
  type BankProposal,
  type BankMovementFilters,
  type BankMovementsPage,
  type BankNoEntendida,
  type BankReassignTarget,
  type BankRule,
  type Contrapartida,
  type FactusolDocument,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ExportBancoADrive } from "../../components/erp/ExportBancoADrive";
import { EjercicioSelector, useEjercicio } from "../../components/erp/EjercicioSelector";
import { RegistrarCobroModal } from "../../components/erp/RegistrarCobroModal";

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

/** «2026-10-03» → «03/10/2026» (sin pasar por Date: es una fecha, sin hora). */
function fechaCorta(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}

/** El resultado de importar un extracto: lo importado, lo que entró casado
 *  por la columna `factura` y las filas que no se entendieron (se importa el
 *  resto igual), que se pueden desplegar para verlas. */
function ResumenImport({ summary }: { summary: BankImportSummary }) {
  const [abiertas, setAbiertas] = useState(false);
  const filas: BankNoEntendida[] = summary.no_entendidas ?? [];
  const total = summary.no_entendidas_total ?? filas.length;
  const anotadas = summary.anotadas;
  return (
    <div className="erp-bank-import-summary" role="status">
      {summary.resumen ? (
        <p>
          <strong>{summary.resumen}</strong>
          {summary.account_name ? <> · {summary.account_name}</> : null}
          {summary.formato ? <span className="muted small"> · formato {summary.formato}</span> : null}
        </p>
      ) : null}
      {anotadas && (anotadas.confirmadas || anotadas.propuestas || anotadas.sin_factura) ? (
        <p className="muted small">
          Venían anotados en la columna factura: <strong>{anotadas.confirmadas}</strong> conciliados
          {anotadas.propuestas ? <> · {anotadas.propuestas} con una nota, para revisar</> : null}
          {anotadas.sin_factura ? <> · {anotadas.sin_factura} con texto que no es una factura</> : null}
          {anotadas.nuevas_en_reimportados ? (
            <> · {anotadas.nuevas_en_reimportados} ya importado{anotadas.nuevas_en_reimportados === 1 ? "" : "s"} con la anotación nueva</>
          ) : null}
        </p>
      ) : null}
      {summary.continuaciones ? (
        <p className="muted small">
          {summary.continuaciones} fila{summary.continuaciones === 1 ? "" : "s"} sin fecha unida
          {summary.continuaciones === 1 ? "" : "s"} al movimiento de encima (el pagador, la factura…).
        </p>
      ) : null}
      {summary.recolocadas ? (
        <p className="muted small">
          {summary.recolocadas} fila{summary.recolocadas === 1 ? "" : "s"} corrida
          {summary.recolocadas === 1 ? "" : "s"} recolocada{summary.recolocadas === 1 ? "" : "s"} (un
          nombre o un texto partido por una coma).
        </p>
      ) : null}
      {total > 0 ? (
        <>
          <button
            type="button"
            className="button small secondary"
            aria-expanded={abiertas}
            onClick={() => setAbiertas((v) => !v)}
          >
            {abiertas ? "Ocultar" : "Ver"} las {total} fila{total === 1 ? "" : "s"} no entendida{total === 1 ? "" : "s"}
          </button>
          {abiertas ? (
            <table className="data-table erp-bank-rejected" aria-label="Filas no entendidas">
              <thead>
                <tr><th>Fila</th><th>Por qué</th><th>Lo que trae</th></tr>
              </thead>
              <tbody>
                {filas.map((f) => (
                  <tr key={f.fila}>
                    <td>{f.fila}</td>
                    <td>{f.motivo}</td>
                    <td className="muted small">{f.valores.join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {abiertas && filas.length < total ? (
            <p className="muted small">Se enseñan las {filas.length} primeras.</p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/** Lo que el CSV de cobros lleva y lo que deja fuera (lo cuenta el servidor
 *  en las cabeceras `X-Cobros-*`). */
function avisoCsvCobros(csv: BankCobrosCsv): string {
  const n = csv.incluidas;
  const fuera = [
    csv.parciales ? `${csv.parciales} parcial${csv.parciales === 1 ? "" : "es"} (el script registraría el saldo entero)` : null,
    csv.noCuadran ? `${csv.noCuadran} con más conciliado que el importe del movimiento` : null,
    csv.noPendientes ? `${csv.noPendientes} que no está${csv.noPendientes === 1 ? "" : "n"} pendiente${csv.noPendientes === 1 ? "" : "s"} en FACTUSOL` : null,
    csv.sinContrapartida ? `${csv.sinContrapartida} de una cuenta sin contrapartida` : null,
  ].filter(Boolean);
  return (
    `CSV de cobros descargado: ${n} factura${n === 1 ? "" : "s"}. ` +
    (fuera.length ? `Fuera del CSV: ${fuera.join(", ")}; esas se registran a mano. ` : "") +
    "Pásalo a registrar_cobros_lote.ps1: simula primero y se salta lo ya cobrado."
  );
}

/** Lo que Bart anotó a mano en el extracto (columnas `factura` / `pedido`),
 *  siempre a la vista, salvo que ya lo diga la propuesta que salió de ahí. */
function AnotadoEnExtracto({ movement: m }: { movement: BankMovement }) {
  const lista = m.status === "reconciled" ? m.reconciled : m.proposals;
  if (lista.some((p) => p.origen === "extracto")) return null;
  const partes = [
    m.anotacion_factura ? `factura «${m.anotacion_factura}»` : null,
    m.anotacion_pedido ? `pedido «${m.anotacion_pedido}»` : null,
  ].filter(Boolean);
  if (!partes.length) return null;
  return <div className="muted small">Anotado en el extracto: {partes.join(" · ")}</div>;
}

const HOJA_TEXTO: Record<string, string> = {
  pendiente: "Pendiente de escribir en la hoja de Drive.",
  ocupada: "En la hoja, la celda «factura» ya tenía algo: BoHub no la toca.",
  no_encontrada: "Este movimiento ya no está en la hoja de Drive: no se ha escrito nada.",
  corrida: "Su fila está corrida en la hoja: no se escribe hasta que se corrija.",
  sin_columna: "La hoja no tiene columna «factura»: no se escribe.",
};

/** Lo que BoHub ha hecho (o no) en la hoja de Drive con este movimiento. */
function EnLaHoja({ movement: m }: { movement: BankMovement }) {
  if (!m.hoja_estado) return null;
  if (m.hoja_estado === "escrita") {
    const partes = [
      m.hoja_factura ? `factura «${m.hoja_factura}»` : null,
      m.hoja_pedido ? `pedido «${m.hoja_pedido}»` : null,
    ].filter(Boolean);
    return (
      <div className="muted small erp-bank-hoja">
        {partes.length ? `Escrito en la hoja: ${partes.join(" · ")}` : "En la hoja: ya estaba escrito."}
      </div>
    );
  }
  return <div className="muted small erp-bank-hoja">{HOJA_TEXTO[m.hoja_estado] ?? null}</div>;
}

/** Lo que pasó al escribir en la hoja, en una línea. */
function escrituraTexto(e: BankDriveEscritura | null | undefined): string | null {
  if (!e) return null;
  if (e.error) return e.error;
  const partes = [
    e.escritas ? `${e.escritas} escrito${e.escritas === 1 ? "" : "s"} en la hoja` : null,
    e.ocupadas ? `${e.ocupadas} con la celda «factura» ya ocupada (no se tocan)` : null,
    e.no_encontradas ? `${e.no_encontradas} que ya no están en la hoja` : null,
    e.corridas ? `${e.corridas} en filas corridas de la hoja` : null,
    e.sin_columna ? `${e.sin_columna} sin escribir: la hoja no tiene columna «factura»` : null,
    e.movidas ? `${e.movidas} por reintentar (la hoja cambió mientras se escribía)` : null,
  ].filter(Boolean);
  return partes.length ? partes.join(" · ") : null;
}

/** Lo que salió del casado tras importar (o por qué no se pudo). */
function ResumenCasado({ matching }: { matching: BankImportSummary["matching"] }) {
  if (!matching) return null;
  if (!matching.ok) return <> · {matching.detail}</>;
  return (
    <>
      {" "}· propuestas <strong>{matching.proposed}</strong>
      {" "}(alta {matching.by_confidence?.alta ?? 0},
      media {matching.by_confidence?.media ?? 0},
      baja {matching.by_confidence?.baja ?? 0}) ·
      sin propuesta {matching.no_proposal} ·
      excluidos {matching.excluded}
    </>
  );
}

/** Lo que dice la pantalla tras añadir el export a la hoja: lo añadido y lo
 *  que de verdad ha entrado en BoHub (puede ser menos si Drive aún no da la
 *  hoja nueva; entra en la próxima lectura). */
function avisoExportAnadido(hecho: BankExportPlan): string {
  const entrados = hecho.importacion?.ok ? hecho.importacion.imported ?? 0 : 0;
  const enBohub = entrados
    ? ` ${entrados === 1 ? "Ha entrado 1 movimiento" : `Han entrado ${entrados} movimientos`} en BoHub.`
    : "";
  if (!hecho.aplicado) {
    return `La hoja ya tenía todos los movimientos del fichero.${enBohub}`;
  }
  const n = hecho.nuevos;
  return `${n} ${n === 1 ? "movimiento añadido" : "movimientos añadidos"} a la hoja de Drive.${enBohub}`;
}

/** El resultado de «Traer de Drive» en una cuenta. */
function TraidaDeDrive({ traida: t }: { traida: BankDriveTraida }) {
  if (!t.ok) {
    return <p className="form-error" role="alert">{t.account_name}: {t.detail}</p>;
  }
  const escrito = escrituraTexto(t.drive?.escritura);
  return (
    <>
      <ResumenImport summary={t} />
      <p className="muted small">
        Leído de Drive ({t.drive?.formato === "xlsx" ? ".xlsx subido: solo se lee" : "hoja de Google"})
        {t.duplicates ? <> · {t.duplicates} ya estaban</> : null}
        {escrito ? <> · {escrito}</> : null}
        <ResumenCasado matching={t.matching} />
      </p>
    </>
  );
}

/** `useSearchParams` exige Suspense en el app router (`?movimiento=`). */
export default function ConciliacionPage() {
  return (
    <Suspense fallback={<main className="shell shell-wide"><p className="muted">Cargando…</p></main>}>
      <ConciliacionScreen />
    </Suspense>
  );
}

/** ERP-F4-A — Conciliación bancaria. Importa el extracto, PROPONE el casado
 *  con las facturas pendientes y deja que la persona decida. Nada se concilia
 *  solo. NO escribe en FACTUSOL (eso va en F-4-B). Con `?movimiento=<id>`
 *  («Ver el movimiento» de la ficha del pedido) enseña solo ese. */
function ConciliacionScreen() {
  const searchParams = useSearchParams();
  const movimiento = searchParams?.get("movimiento") || undefined;
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
  // El extracto por Drive: con quién se comparten las hojas y lo traído.
  const [drive, setDrive] = useState<BankDriveInfo | null>(null);
  const [traidas, setTraidas] = useState<BankDriveTraida[] | null>(null);
  const [tab, setTab] = useState<"revision" | "cuentas" | "reglas">("revision");
  // Acciones por movimiento (elegir otra / repartir / descartar).
  const [reassignFor, setReassignFor] = useState<BankMovement | null>(null);
  const [discardFor, setDiscardFor] = useState<BankMovement | null>(null);

  // Roles y permisos: la conciliación bancaria es admin-only (capacidad
  // `erp.conciliacion`). El backend vuelve a comprobarlo en cada endpoint.
  const canEdit = can(user, Cap.CONCILIACION);
  const isAdmin = user?.role === "admin";

  // El ejercicio (el de FACTUSOL): los movimientos de ese año.
  const { info: ejercicios, ejercicio, anterior, setEjercicio } = useEjercicio();
  // Un ejercicio anterior es solo consulta: ni confirmar ni elegir.
  const canDecidir = canEdit && (!anterior || !!movimiento);
  // Cobrar, solo en el ejercicio en curso (es el que lee y escribe FACTUSOL).
  const canCobrar = can(user, Cap.COBRO_REGISTER) && !anterior;
  // El cobro de una factura conciliada, con su movimiento (el diálogo único).
  const [cobrando, setCobrando] = useState<{ mov: BankMovement; rec: BankProposal } | null>(null);
  // «Incluir facturas externas»: apagada al entrar en la pantalla; vive
  // mientras se está en ella (sobrevive a recargar la lista tras confirmar o
  // registrar) y no se guarda en ningún sitio.
  const [incluirExternas, setIncluirExternas] = useState(false);
  // El contador «solo casan con facturas externas», pulsado.
  const [soloExternas, setSoloExternas] = useState(false);

  const loadMovements = useCallback(async () => {
    // Sin saber el ejercicio en curso no se pide nada (saldrían todos los años).
    if (!movimiento && !ejercicio) return;
    try {
      // Un movimiento concreto va sin los demás filtros: puede estar ya
      // conciliado y el estado por defecto es «Pendiente».
      setPage(await listBankMovements(movimiento ? { movimiento, externas: incluirExternas } : {
        ...filters, limit: 200, ejercicio: ejercicio ?? undefined, externas: incluirExternas,
        solo_externas: !incluirExternas && soloExternas,
      }));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar los movimientos."));
    }
  }, [filters, movimiento, ejercicio, incluirExternas, soloExternas]);

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
    getBankDrive().then(setDrive).catch(() => setDrive(null));
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
    setTraidas(null);
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

  /** «Traer de Drive»: la hoja de la cuenta elegida, o de todas las que la tienen. */
  async function onTraerDrive() {
    setBusy(true);
    setError(null);
    setNotice(null);
    setImportSummary(null);
    setTraidas(null);
    try {
      setTraidas(await traerBankDrive(filters.account_id));
      await Promise.all([loadMovements(), loadAccounts()]);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo traer el extracto de Drive."));
    } finally {
      setBusy(false);
    }
  }

  const counters = page?.counters;
  const conHoja = accounts.filter((a) => a.drive_file_id);
  const cuentaConHoja = conHoja.find((a) => a.id === filters.account_id);
  const puedeTraer = filters.account_id
    ? conHoja.some((a) => a.id === filters.account_id)
    : conHoja.length > 0;

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
          drive={drive}
          isAdmin={isAdmin}
          onChanged={async () => { await Promise.all([loadAccounts(), loadMovements()]); }}
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
                  <option value="">Todas / detectar la cuenta del extracto</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>{a.name} · {a.iban}</option>
                  ))}
                </select>
              </label>
              {canEdit ? (
                <label className="field">
                  <span>Fichero (.xls / .xlsx / .csv)</span>
                  <input
                    type="file"
                    accept=".xls,.xlsx,.csv"
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
              {canEdit && conHoja.length > 0 ? (
                <button
                  type="button"
                  className="button small"
                  disabled={busy || !puedeTraer}
                  title={puedeTraer
                    ? "Lee la hoja de Drive entera e importa lo que falta. El fichero a mano sigue valiendo."
                    : "Esta cuenta no tiene hoja de Drive: ponla en la pestaña Cuentas."}
                  onClick={() => void onTraerDrive()}
                >
                  Traer de Drive
                </button>
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
                    setError(null);
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
              <button
                type="button"
                className="button small secondary"
                disabled={busy}
                title="Lo conciliado y no descartado cuyo saldo en FACTUSOL cuadra, para registrar_cobros_lote.ps1 (simula primero y se salta lo ya cobrado). Cada cobro va con la fecha valor del movimiento. No escribe en FACTUSOL."
                onClick={async () => {
                  setBusy(true);
                  setError(null);
                  setNotice(null);
                  try {
                    const csv = await downloadBankCobrosCsv({
                      accountId: filters.account_id, desde: filters.desde, hasta: filters.hasta,
                    });
                    saveBlob(csv.blob, "cobros_conciliados.csv");
                    setNotice(avisoCsvCobros(csv));
                  } catch (e) {
                    setError(extractErrorMessage(e, "No se pudo descargar el CSV de cobros."));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Descargar CSV de cobros
              </button>
            </div>
            {conHoja.length > 0 && drive ? (
              drive.configured ? (
                <p className="muted small">
                  Hojas de Drive conectadas con {drive.service_account_email}. BoHub lee la hoja
                  entera y solo añade lo que falta; en «factura» y «pedido» solo escribe en celdas
                  vacías: nunca borra filas, ni reordena, ni pisa lo escrito a mano. Repaso
                  automático cada día a las {drive.hora}.
                </p>
              ) : (
                <p className="form-warning">
                  Hay cuentas con hoja de Drive, pero falta la cuenta de servicio de Google: un
                  admin la pega en Configuración ERP (la misma de Seguimiento). Mientras, el
                  fichero a mano sigue valiendo.
                </p>
              )
            ) : null}
            {canEdit && drive?.configured && cuentaConHoja ? (
              <ExportBancoADrive
                key={cuentaConHoja.id}
                accountId={cuentaConHoja.id}
                accountName={cuentaConHoja.name}
                disabled={busy}
                onAnadido={async (hecho) => {
                  setImportSummary(null);
                  setTraidas(hecho.importacion ? [hecho.importacion] : null);
                  setNotice(avisoExportAnadido(hecho));
                  if (hecho.error_importacion) setError(hecho.error_importacion);
                  await Promise.all([loadMovements(), loadAccounts()]);
                }}
              />
            ) : canEdit && drive?.configured && conHoja.length > 0 && !filters.account_id ? (
              <p className="muted small">
                Para subir el export del banco a su hoja de Drive, elige antes la cuenta.
              </p>
            ) : null}
            {importSummary ? <ResumenImport summary={importSummary} /> : null}
            {traidas ? traidas.map((t) => <TraidaDeDrive key={t.account_id} traida={t} />) : null}
            {importSummary ? (
              <p className="muted small" role="status">
                Importados <strong>{importSummary.imported}</strong> de {importSummary.total_rows} ·
                descartados por repetidos <strong>{importSummary.duplicates}</strong>
                <ResumenCasado matching={importSummary.matching} />
              </p>
            ) : null}
          </section>

          <section className="erp-card">
            {movimiento ? (
              <div className="erp-doc-filters">
                <span className="badge info">Mostrando un solo movimiento</span>
                <Link href="/erp/conciliacion" className="button small secondary">Ver todos</Link>
              </div>
            ) : (
              <div className="erp-doc-filters">
                <EjercicioSelector
                  info={ejercicios} ejercicio={ejercicio}
                  onChange={setEjercicio} que="Los movimientos"
                />
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
                {canDecidir ? (
                  <button
                    type="button"
                    className="button small"
                    disabled={busy}
                    onClick={() => {
                      if (!window.confirm("¿Confirmar TODAS las propuestas de confianza alta? Es una decisión tuya, no automática.")) return;
                      void act("Confirmadas las de confianza alta.", () => confirmAllHighBank(
                        filters.account_id, { externas: incluirExternas, ejercicio: ejercicio ?? undefined },
                      ));
                    }}
                  >
                    Confirmar todas las de confianza alta
                  </button>
                ) : null}
              </div>
            )}
            {!movimiento ? (
              <div className="erp-doc-filters">
                <label className={`erp-doc-chip ${incluirExternas ? "is-active" : ""}`}
                       title="Que las propuestas y «Elegir otra» ofrezcan también facturas que solo están en FACTUSOL, sin pedido de BoHub">
                  <input
                    type="checkbox"
                    checked={incluirExternas}
                    onChange={(e) => { setIncluirExternas(e.target.checked); setSoloExternas(false); }}
                  />
                  {" "}Incluir facturas externas (solo FACTUSOL, sin pedido)
                </label>
                {!incluirExternas && (page?.counters.solo_externas ?? 0) > 0 ? (
                  <button
                    type="button"
                    className={`erp-doc-chip ${soloExternas ? "is-active" : ""}`}
                    aria-pressed={soloExternas}
                    title="Por conciliar que solo casan con facturas externas: no se ofrecen con la casilla apagada"
                    onClick={() => setSoloExternas((v) => !v)}
                  >
                    {page?.counters.solo_externas} por conciliar solo casan con facturas externas
                    {soloExternas ? <> <span aria-hidden>✕</span></> : null}
                  </button>
                ) : null}
              </div>
            ) : null}

            {!page ? <p className="muted">Cargando…</p> : page.items.length === 0 ? (
              <p className="muted">
                {movimiento ? "No se encuentra ese movimiento." : "Sin movimientos que casen los filtros."}
              </p>
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
                      <td>
                        {m.fecha_oper}
                        {/* La fecha valor es con la que se registra el cobro. */}
                        {m.fecha_valor && m.fecha_valor !== m.fecha_oper ? (
                          <div className="muted small">valor {fechaCorta(m.fecha_valor)}</div>
                        ) : null}
                      </td>
                      <td>
                        <div>{m.concepto}</div>
                        {m.payer_name ? <div className="muted small">Pagador: {m.payer_name}</div> : null}
                      </td>
                      <td><strong>{eur(m.importe)}</strong></td>
                      <td>
                        {(m.status === "reconciled" ? m.reconciled : m.proposals).length === 0 ? (
                          <>
                            <span className="muted">Sin propuesta</span>
                            {m.status === "pending" && (m.externas_ocultas ?? 0) > 0 ? (
                              <div className="muted small erp-bank-externas">
                                {m.externas_ocultas === 1
                                  ? "Hay 1 factura externa (solo FACTUSOL, sin pedido) que encaja"
                                  : `Hay ${m.externas_ocultas} facturas externas (solo FACTUSOL, sin pedido) que encajan`}
                                : no se ofrece{m.externas_ocultas === 1 ? "" : "n"} con
                                «Incluir facturas externas» apagada.
                              </div>
                            ) : null}
                          </>
                        ) : (
                          <ul className="erp-bank-proposals">
                            {(m.status === "reconciled" ? m.reconciled : m.proposals).map((p) => (
                              <li key={p.id}>
                                <strong>{p.numero}</strong>
                                {/* El camino de vuelta: de la factura a la ficha de su pedido. */}
                                {p.pedido ? (
                                  <>
                                    {" · "}
                                    <Link href={`/erp/orders/${encodeURIComponent(p.pedido.id)}`}
                                      title="Abrir la ficha del pedido">
                                      {p.pedido.order_number}
                                    </Link>
                                  </>
                                ) : (
                                  <span className="erp-doc-externa-pill"
                                        title="Solo está en FACTUSOL: no tiene pedido de BoHub detrás.">
                                    Externa · sin pedido
                                  </span>
                                )}
                                {" · "}{p.cliente_nombre ?? "—"} · {eur(p.importe)}
                                {p.origen === "extracto" ? (
                                  <span className="badge info small" title="Bart lo anotó a mano en la columna factura del extracto">
                                    {" "}anotado en el extracto
                                  </span>
                                ) : null}
                                {p.origen === "pedido" ? (
                                  <span className="badge info small" title="Se confirmó desde la ficha del pedido">
                                    {" "}desde la ficha del pedido
                                  </span>
                                ) : null}
                                {p.reason ? <div className="muted small">{p.reason}</div> : null}
                              </li>
                            ))}
                          </ul>
                        )}
                        <AnotadoEnExtracto movement={m} />
                        <EnLaHoja movement={m} />
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
                        {canDecidir && m.status === "pending" ? (
                          <>
                            {m.proposals.length > 0 ? (
                              <button type="button" className="button small" disabled={busy}
                                onClick={() => act(`Conciliado ${m.fecha_oper}.`, () => confirmBankMovement(m.id, incluirExternas))}>
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
                        {canDecidir && m.status !== "pending" ? (
                          <button type="button" className="button small secondary" disabled={busy}
                            onClick={() => act("Movimiento reabierto.", () => reopenBankMovement(m.id))}>
                            Reabrir
                          </button>
                        ) : null}
                        {/* La herramienta de cobro, con este movimiento: la misma de
                            la ficha y de Documentos. Las externas, solo con la casilla. */}
                        {canCobrar && m.status === "reconciled"
                          ? m.reconciled.filter((r) => incluirExternas || r.pedido).map((r) => (
                            <button key={r.id} type="button" className="button small" disabled={busy}
                              aria-label={`Registrar cobro de la ${r.numero}`}
                              onClick={() => setCobrando({ mov: m, rec: r })}>
                              Registrar cobro{m.reconciled.length > 1 ? ` · ${r.numero}` : ""}
                            </button>
                          ))
                          : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      ) : null}

      {cobrando ? (
        <RegistrarCobroModal
          factura={{ serie: cobrando.rec.serie, codigo: cobrando.rec.codigo, numero: cobrando.rec.numero }}
          movimientoId={cobrando.mov.id}
          onClose={() => setCobrando(null)}
          onDone={() => void loadMovements()}
        />
      ) : null}

      {reassignFor ? (
        <ReassignModal
          movement={reassignFor}
          incluirExternas={incluirExternas}
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
  accounts, suggested, drive, isAdmin, onChanged, onError,
}: {
  accounts: BankAccount[];
  suggested: BankAccountSuggestion[];
  drive: BankDriveInfo | null;
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
      <p className="muted small">
        Y puede tener su <strong>hoja de Drive</strong>: BoHub la lee sola (cada día
        a las {drive?.hora ?? "05:20"} y con «Traer de Drive») y, al conciliar, escribe la
        factura y el pedido en la fila del movimiento, solo en celdas vacías.
        {drive?.service_account_email ? (
          <> Comparte cada hoja con <span className="mono">{drive.service_account_email}</span>:
            como <strong>Lector</strong> si BoHub solo la lee, como <strong>Editor</strong> si
            además escribe.</>
        ) : (
          <> Falta la cuenta de servicio de Google: se pega en Configuración ERP (la misma de
            Seguimiento).</>
        )}
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
              <HojaDriveCuenta
                account={a}
                drive={drive}
                isAdmin={isAdmin}
                onChanged={onChanged}
                onError={onError}
              />
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

// --- la hoja de Drive de una cuenta ------------------------------------------------

/** El enlace para abrir el fichero: una hoja de Google o un .xlsx en Drive. */
function enlaceHoja(a: BankAccount): string {
  const id = encodeURIComponent(a.drive_file_id ?? "");
  return a.drive_formato === "hoja"
    ? `https://docs.google.com/spreadsheets/d/${id}/edit`
    : `https://drive.google.com/file/d/${id}/view`;
}

function cuando(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("es-ES", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** La hoja de Drive de una cuenta: cuál es, qué hace BoHub con ella, cómo
 *  fue la última lectura y lo pendiente de escribir. El admin la conecta. */
function HojaDriveCuenta({
  account: a, drive, isAdmin, onChanged, onError,
}: {
  account: BankAccount;
  drive: BankDriveInfo | null;
  isAdmin: boolean;
  onChanged: () => Promise<void>;
  onError: (msg: string) => void;
}) {
  const [editando, setEditando] = useState(false);
  const [fichero, setFichero] = useState(a.drive_file_id ?? "");
  const [pestana, setPestana] = useState(a.drive_pestana ?? "");
  const [escribir, setEscribir] = useState(a.drive_escribir ?? true);
  const [ocupado, setOcupado] = useState(false);
  const [escrito, setEscrito] = useState<{ texto: string; error: boolean } | null>(null);
  const r = a.drive_resultado;
  const email = drive?.service_account_email ?? "la cuenta de servicio";

  async function guardar() {
    setOcupado(true);
    try {
      await updateBankAccount(a.id, {
        drive_file_id: fichero.trim() || null,
        drive_pestana: pestana.trim() || null,
        drive_escribir: escribir,
      });
      setEditando(false);
      await onChanged();
    } catch (e) {
      onError(extractErrorMessage(e, "No se pudo guardar la hoja de Drive."));
    } finally {
      setOcupado(false);
    }
  }

  async function escribirAhora() {
    setOcupado(true);
    setEscrito(null);
    try {
      const hecho = await escribirBankDrive(a.id);
      setEscrito({ texto: escrituraTexto(hecho) ?? "Nada que escribir.", error: Boolean(hecho.error) });
      await onChanged();
    } catch (e) {
      onError(extractErrorMessage(e, "No se pudo escribir en la hoja de Drive."));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div className="erp-bank-drive" role="group" aria-label={`Hoja de Drive de ${a.name}`}>
      <div className="muted small">
        Hoja de Drive:{" "}
        {a.drive_file_id ? (
          <>
            <a href={enlaceHoja(a)} target="_blank" rel="noreferrer">
              {a.drive_formato === "hoja" ? "hoja de Google" : a.drive_formato === "xlsx" ? ".xlsx subido" : "abrir"}
            </a>
            {a.drive_pestana ? <> · pestaña «{a.drive_pestana}»</> : null}
            {" · "}
            {!a.drive_escribir
              ? "BoHub solo la lee"
              : a.drive_formato === "xlsx"
                ? "BoHub la lee; no puede escribir en un .xlsx"
                : "BoHub la lee y escribe la factura y el pedido"}
          </>
        ) : "sin conectar"}
      </div>
      {a.drive_file_id && a.drive_escribir && a.drive_formato === "xlsx" ? (
        <p className="form-warning small">
          Es un .xlsx subido: BoHub lo lee, pero no puede escribir en él sin subirlo entero.
          Para que escriba la factura y el pedido, ábrelo en Drive y usa Archivo → Guardar
          como hoja de cálculo de Google; comparte la hoja nueva con {email} como Editor y
          pon aquí su enlace.
        </p>
      ) : null}
      {r?.at ? (
        <p className="muted small">
          Última lectura ({r.origen === "programada" ? "repaso diario" : "a mano"}, {cuando(r.at)}):{" "}
          {r.ok ? (
            <>
              {r.imported ?? 0} nuevos · {r.duplicates ?? 0} ya estaban
              {r.no_entendidas_total ? <> · {r.no_entendidas_total} filas no entendidas</> : null}
            </>
          ) : <span className="form-error">{r.error}</span>}
        </p>
      ) : null}
      {r?.escritura && escrituraTexto(r.escritura) ? (
        <p className={r.escritura.error ? "form-error small" : "muted small"}>
          Última escritura ({cuando(r.escritura.at)}): {escrituraTexto(r.escritura)}
        </p>
      ) : null}
      {a.drive_pendientes ? (
        <p className="muted small">
          {a.drive_pendientes} conciliado{a.drive_pendientes === 1 ? "" : "s"} pendiente
          {a.drive_pendientes === 1 ? "" : "s"} de escribir en la hoja.
          {isAdmin && a.drive_formato !== "xlsx" ? (
            <button type="button" className="button small secondary" style={{ marginLeft: 8 }}
              disabled={ocupado} onClick={() => void escribirAhora()}>
              Escribir ahora
            </button>
          ) : null}
        </p>
      ) : null}
      {escrito ? (
        <p className={escrito.error ? "form-error small" : "form-success small"}
          role={escrito.error ? "alert" : "status"}>
          {escrito.texto}
        </p>
      ) : null}
      {isAdmin ? (
        editando ? (
          <form className="form-card embedded" onSubmit={(e) => { e.preventDefault(); void guardar(); }}>
            <label>
              Hoja de Drive (enlace o identificador; vacío: sin hoja)
              <input value={fichero} onChange={(e) => setFichero(e.target.value)}
                placeholder="https://docs.google.com/spreadsheets/d/…" />
            </label>
            <label>
              Pestaña (vacía: la primera)
              <input value={pestana} onChange={(e) => setPestana(e.target.value)} />
            </label>
            <label className="muted small">
              <input type="checkbox" checked={escribir} onChange={(e) => setEscribir(e.target.checked)} />{" "}
              Escribir la factura y el pedido en la hoja al conciliar (solo en celdas vacías)
            </label>
            <p className="muted small">
              Comparte la hoja con <span className="mono">{email}</span> como{" "}
              <strong>{escribir ? "Editor" : "Lector"}</strong>.
            </p>
            <div className="modal-actions">
              <button type="button" className="button small secondary" onClick={() => setEditando(false)}>
                Cancelar
              </button>
              <button type="submit" className="button small" disabled={ocupado}>Guardar</button>
            </div>
          </form>
        ) : (
          <button
            type="button"
            className="button small secondary"
            onClick={() => {
              // Lo guardado, no lo que se tecleó la vez anterior.
              setFichero(a.drive_file_id ?? "");
              setPestana(a.drive_pestana ?? "");
              setEscribir(a.drive_escribir ?? true);
              setEditando(true);
            }}
          >
            {a.drive_file_id ? "Cambiar la hoja de Drive" : "Conectar hoja de Drive"}
          </button>
        )
      ) : null}
    </div>
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
  movement, incluirExternas, onClose, onDone,
}: {
  movement: BankMovement;
  /** Sin la casilla, la búsqueda solo da facturas con pedido de BoHub. */
  incluirExternas: boolean;
  onClose: () => void;
  onDone: (targets: BankReassignTarget[], learn: boolean) => Promise<void>;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<FactusolDocument[]>([]);
  const [targets, setTargets] = useState<BankReassignTarget[]>([]);
  const [learn, setLearn] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose });

  useEffect(() => {
    const handle = window.setTimeout(() => {
      listFactusolDocuments("facturas", {
        q: q.trim() || undefined, limit: 15, linked: incluirExternas ? undefined : true,
      })
        .then((r) => setResults(r.items))
        .catch(() => setResults([]));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [q, incluirExternas]);

  const assigned = targets.reduce((s, t) => s + t.importe, 0);
  const remaining = Math.round((movement.importe - assigned) * 100) / 100;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Elegir factura" {...overlayProps}>
      <div className="modal-dialog erp-emit-modal">
        <h2>Elegir / repartir · {eur(movement.importe)}</h2>
        <ModalCloseButton onClose={requestClose} />
        <p className="muted small">{movement.concepto}</p>
        <label className="field">
          <span>Buscar factura</span>
          <input aria-label="Buscar factura" value={q} onChange={(e) => setQ(e.target.value)} placeholder="número, cliente…" />
        </label>
        <ul className="item-list">
          {results.filter((d) => d.serie !== null && typeof d.codigo === "number").map((d) => (
            <li key={`${d.serie}-${d.codigo}`}>
              <strong>{d.numero}</strong>
              {d.order ? null : (
                <span className="erp-doc-externa-pill"
                      title="Solo está en FACTUSOL: no tiene pedido de BoHub detrás.">
                  Externa · sin pedido
                </span>
              )}
              {" "}· {d.cliente_nombre ?? "—"} · saldo {eur(d.saldo_pendiente ?? d.total)}
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
  const { overlayProps, requestClose } = useModalBehaviour({ onClose });
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="No es cobro de cliente" {...overlayProps}>
      <div className="modal-dialog erp-emit-modal">
        <h2>No es un cobro de cliente</h2>
        <ModalCloseButton onClose={requestClose} />
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
