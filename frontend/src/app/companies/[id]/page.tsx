"use client";

import { Building2, FileText, Save, Users } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { getCurrentUser } from "../../lib/api";
import {
  type Company,
  type CompanyContact,
  type CompanyWrite,
  type FiscalCheck,
  type ViesStatus,
  archiveCompany,
  deleteCompany,
  fiscalCheck,
  getCompany,
  restoreCompany,
  listCompanyContacts,
  mergeCompanies,
  updateCompany,
  viesRevalidate,
} from "../../lib/companiesApi";
import { formatBackendDateTime } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";
import { ERP_EDIT_ROLES, type FactusolCustomer } from "../../lib/erpApi";
import { CompanyActivityPanel } from "../../components/erp/CompanyActivityPanel";
import { CompanyFactusolPanel } from "../../components/erp/CompanyFactusolPanel";
import { CompanyQuotesPanel } from "../../components/erp/CompanyQuotesPanel";
import { CompanySearch } from "../../components/CompanySearch";
import { ActionsMenu } from "../../components/erp/flow/ActionsMenu";
import { RegimePill } from "../../components/erp/flow/RegimePill";

type Tab = "data" | "contacts" | "quotes";
type Sync = { customer: FactusolCustomer | null; diffs: { field: string }[] | null };

/** Chip VIES de la cabecera / fila de «Datos fiscales» (Fase VIES). */
const VIES_CHIP: Record<ViesStatus, { text: string; tone: string }> = {
  valido: { text: "✓ verificado en VIES", tone: "ok" },
  no_valido: { text: "VAT no válido en VIES", tone: "bad" },
  desconocido: { text: "VIES no disponible · pendiente", tone: "warn" },
  pendiente: { text: "VIES: pendiente de validar", tone: "muted" },
};

/** Explicación única de por qué una acción está desactivada mientras la
 *  empresa sigue archivada (Lote 2 · PR-2). */
const ARCHIVED_HINT = "Empresa archivada: pulsa «Reactivar» para volver a operar con ella.";

/** Ficha de empresa (rediseño de flujo, Fase 3 · Lote 2 PR-2).
 *
 *  Arriba, lo que decide el trabajo: nombre fiscal, NIF + localidad y tres
 *  pastillas junto al nombre (estado Activa/Archivada, vínculo «En FACTUSOL ·
 *  CLI-x» / «Solo CRM» y régimen de IVA) más el chip VIES; si está archivada,
 *  una banda gris arriba del todo con motivo y fecha y «Reactivar» como acción
 *  primaria (el resto de acciones se queda, desactivado y con el porqué). La
 *  barra de alerta cuando los datos del CRM no coinciden con FACTUSOL (con
 *  «Traer datos de FACTUSOL»); «Datos fiscales» como panel de pares con la fila
 *  VIES siempre presente («No aplica» o estado + fecha + «Volver a comprobar»)
 *  y «Actividad reciente» en una sola tabla con filtro por tipo (pedidos con
 *  su cola, facturas con su cobro, proformas). Acciones rápidas: Nuevo pedido,
 *  Nueva proforma, Traer datos; y en «⋯» el «Comprobar régimen de IVA» de
 *  siempre, Revalidar en VIES, Fusionar, Archivar/Reactivar y Borrar.
 *
 *  Debajo se conserva todo lo que había: las pestañas Datos (formulario
 *  completo), Contactos y Proformas FACTUSOL, y la sección FACTUSOL
 *  (vincular / crear / diferencias / traer datos / régimen). */
export default function CompanyDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [company, setCompany] = useState<Company | null>(null);
  const [contacts, setContacts] = useState<CompanyContact[]>([]);
  const [tab, setTab] = useState<Tab>("data");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [canEdit, setCanEdit] = useState(false);
  // Régimen detectado por país + NIF-IVA (misma regla que la ficha F_CLI).
  const [fiscal, setFiscal] = useState<FiscalCheck | null>(null);
  // Sincronía CRM ↔ FACTUSOL, la calcula la sección FACTUSOL de abajo.
  const [sync, setSync] = useState<Sync | null>(null);
  // Señales hacia las secciones de abajo (misma previsualización + confirmación).
  const [pullSignal, setPullSignal] = useState(0);
  const [regimeSignal, setRegimeSignal] = useState(0);
  const [quoteSignal, setQuoteSignal] = useState(0);
  // Fase VIES: «Revalidar en VIES» (botón: fuerza) y la comprobación al
  // cargar cuando está pendiente / VIES no respondió (sin forzar: el backend
  // decide si toca).
  const [viesBusy, setViesBusy] = useState(false);
  const [viesError, setViesError] = useState<string | null>(null);
  const viesAutoRef = useRef<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, list] = await Promise.all([
        getCompany(params.id),
        listCompanyContacts(params.id),
      ]);
      setCompany(c);
      setContacts(list);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar la empresa."));
    } finally {
      setLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setCanEdit((ERP_EDIT_ROLES as readonly string[]).includes(u.role)))
      .catch(() => setCanEdit(false));
  }, []);

  // Régimen por país + NIF-IVA (best-effort: sin país ni NIF no hay nada).
  const taxId = company?.tax_id ?? "";
  const vat = company?.vat ?? "";
  const country = company?.country ?? "";
  useEffect(() => {
    if (!company || (!taxId && !vat && !country)) {
      setFiscal(null);
      return;
    }
    let alive = true;
    Promise.resolve()
      .then(() => fiscalCheck({ tax_id: taxId, vat, country, exclude_id: company.id }))
      .then((r) => { if (alive) setFiscal(r); })
      .catch(() => { if (alive) setFiscal(null); });
    return () => { alive = false; };
    // Solo los datos fiscales cambian el régimen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company?.id, taxId, vat, country]);

  const companyId = company?.id ?? null;
  const revalidateVies = useCallback(async (force: boolean) => {
    if (!companyId) return;
    setViesBusy(true);
    setViesError(null);
    try {
      const r = await Promise.resolve().then(() => viesRevalidate(companyId, { force }));
      // Solo lo de VIES: no pisa lo que el operador esté editando en «Datos».
      setCompany((prev) => (prev && prev.id === companyId ? {
        ...prev,
        vies: r.vies,
        vies_status: r.company.vies_status,
        vies_checked_at: r.company.vies_checked_at,
        vies_vat: r.company.vies_vat,
        vies_name: r.company.vies_name,
        vies_address: r.company.vies_address,
      } : prev));
      setFiscal((prev) => (prev ? {
        ...prev, regime: r.regime, regime_label: r.regime_label,
        regime_reason: r.regime_reason, vies: r.vies,
      } : prev));
    } catch (err) {
      if (force) setViesError(extractErrorMessage(err, "No se pudo consultar VIES."));
    } finally {
      setViesBusy(false);
    }
  }, [companyId]);

  // Al cargar: si el NIF-IVA está pendiente de validar, VIES no respondió la
  // última vez, o el veredicto guardado es «no válido» (puede haber cambiado:
  // un alta reciente en VIES, o un resultado erróneo) se pide la validación
  // una vez, sin forzar: el backend decide si toca por antigüedad.
  const viesApplies = !!company?.vies?.applies;
  const viesStatus = company?.vies?.status ?? null;
  useEffect(() => {
    if (!companyId || !viesApplies) return;
    if (viesStatus !== "pendiente" && viesStatus !== "desconocido" && viesStatus !== "no_valido") return;
    if (viesAutoRef.current === companyId) return;
    viesAutoRef.current = companyId;
    void revalidateVies(false);
  }, [companyId, viesApplies, viesStatus, revalidateVies]);

  const onSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!company) return;
    setSaving(true);
    const payload: CompanyWrite = {
      name: company.name,
      website: company.website,
      domain: company.domain,
      tax_id: company.tax_id,
      vat: company.vat,
      country: company.country,
      region: company.region,
      state: company.state,
      city: company.city,
      address_line: company.address_line,
      postal_code: company.postal_code,
      sector: company.sector,
      size_category: company.size_category,
      notes: company.notes,
      source: company.source,
    };
    try {
      const updated = await updateCompany(company.id, payload);
      setCompany(updated);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar."));
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    if (!company) return;
    if (
      !confirm(
        `¿Borrar la empresa "${company.name}"? Los contactos quedarán sin asignación.`,
      )
    )
      return;
    try {
      await deleteCompany(company.id);
      router.push("/companies");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar."));
    }
  };

  // Archivado reversible (limpieza de empresas): oculta la empresa de listados
  // y buscadores, sin borrar; «Reactivar» (endpoint `restore`) la devuelve.
  // Sus pedidos / contactos / tareas se conservan.
  const onArchive = async () => {
    if (!company) return;
    if (!confirm(
      `¿Archivar "${company.name}"? Queda fuera de listados y buscadores (reversible con `
      + "«Reactivar»); no se borra nada.",
    )) return;
    setArchiving(true);
    setError(null);
    try {
      const updated = await archiveCompany(company.id);
      setCompany(updated);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo archivar."));
    } finally {
      setArchiving(false);
    }
  };

  const onRestore = async () => {
    if (!company) return;
    setArchiving(true);
    setError(null);
    try {
      const updated = await restoreCompany(company.id);
      setCompany(updated);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo reactivar."));
    } finally {
      setArchiving(false);
    }
  };

  if (loading) return <main className="shell"><p className="muted">Cargando…</p></main>;
  if (error || !company)
    return (
      <main className="shell"><p className="form-error">{error}</p></main>
    );

  const onChange = <K extends keyof Company>(
    key: K,
    value: Company[K],
  ) => setCompany((prev) => (prev ? { ...prev, [key]: value } : prev));

  const linked = !!company.factusol_company_id;
  // Una empresa archivada no muestra las alertas de flujo (sin vincular / CRM
  // ≠ FACTUSOL / VIES): su banner propio manda y no se le pide acción.
  const archived = !!company.is_archived;
  const diffs = sync?.diffs ?? null;
  const differs = !!diffs && diffs.length > 0;
  const nif = company.tax_id || company.vat || null;
  const lugar = [company.city, company.country].filter(Boolean).join(", ");
  const vies = company.vies?.applies ? company.vies : null;
  const viesChip = vies?.status ? VIES_CHIP[vies.status] : null;
  const viesInvalid = vies?.status === "no_valido";
  const syncLabel = !linked
    ? { text: "sin vincular", tone: "muted" }
    : sync === null || (sync.customer === null && diffs === null)
      ? { text: "comprobando…", tone: "muted" }
      : differs
        ? { text: "difiere de FACTUSOL", tone: "warn" }
        : { text: "sincronizado", tone: "ok" };

  function nuevaProforma() {
    setTab("quotes");
    setQuoteSignal((n) => n + 1);
  }

  return (
    <main className="shell shell-wide erp-flow company-ficha">
      <PageHeader
        title={company.name}
        eyebrow="Empresa"
        crumbs={[
          { label: "Empresas", href: "/companies" },
          { label: company.name },
        ]}
        actions={
          <>
            {/* Fase 1 — alta de pedido con esta empresa precargada. Archivada:
                el botón se queda, desactivado y con el porqué (el primario de
                la pantalla pasa a ser «Reactivar»). */}
            {archived ? (
              <button type="button" className="button small secondary" disabled title={ARCHIVED_HINT}>
                + Nuevo pedido
              </button>
            ) : (
              <Link
                href={`/erp/orders/new?company_id=${company.id}`}
                className="button small"
              >
                + Nuevo pedido
              </Link>
            )}
            <button
              type="button"
              className="button small secondary"
              disabled={!linked || archived}
              title={archived
                ? ARCHIVED_HINT
                : linked
                  ? "Crea una proforma en FACTUSOL para esta empresa"
                  : "Vincula la empresa a un cliente de FACTUSOL para crear proformas"}
              onClick={nuevaProforma}
            >
              Nueva proforma
            </button>
            {linked && canEdit ? (
              <button
                type="button"
                className="button small secondary"
                disabled={archived}
                title={archived
                  ? ARCHIVED_HINT
                  : "Sobrescribe los datos de la empresa CRM con los de FACTUSOL (pide confirmación; no toca FACTUSOL)"}
                onClick={() => setPullSignal((n) => n + 1)}
              >
                Traer datos
              </button>
            ) : null}
            <ActionsMenu label="Más acciones de la empresa">
              {linked && canEdit ? (
                <button type="button" disabled={archived} title={archived ? ARCHIVED_HINT : undefined}
                        onClick={() => setRegimeSignal((n) => n + 1)}>
                  Comprobar régimen de IVA
                </button>
              ) : null}
              {vies ? (
                <button type="button" disabled={viesBusy || archived}
                        title={archived ? ARCHIVED_HINT : undefined}
                        onClick={() => void revalidateVies(true)}>
                  Revalidar en VIES
                </button>
              ) : null}
              <button type="button" onClick={() => setMergeOpen(true)}>Fusionar</button>
              {archived ? (
                <button type="button" disabled={archiving} onClick={onRestore}>Reactivar</button>
              ) : (
                <button type="button" disabled={archiving} onClick={onArchive}>Archivar</button>
              )}
              <button type="button" onClick={onDelete}>Borrar</button>
            </ActionsMenu>
          </>
        }
      />

      {/* Lote 2 · PR-2: archivada = banda gris arriba del todo con motivo y
          fecha, y «Reactivar» como acción primaria de la pantalla (mismo
          endpoint `restore` que el antiguo «Restaurar»). */}
      {archived ? (
        <div className="company-archived-band" role="status" aria-label="Empresa archivada">
          <div className="company-archived-band-txt">
            <p>
              <strong>Empresa archivada</strong>
              {company.archived_at
                ? ` el ${formatBackendDateTime(company.archived_at, { day: "2-digit", month: "short", year: "numeric" })}`
                : ""}
              {" · "}Motivo: {company.archived_reason || "sin indicar"}.
            </p>
            <p className="company-archived-band-hint">
              Está fuera de listados y buscadores y no genera alertas. No se ha borrado nada.
              Mientras siga archivada no se pueden crear pedidos ni proformas, ni traer datos o
              comprobar el régimen y el VIES en FACTUSOL.
            </p>
          </div>
          <button type="button" className="button" disabled={archiving} onClick={onRestore}>
            {archiving ? "Reactivando…" : "Reactivar"}
          </button>
        </div>
      ) : null}

      {/* Cabecera bajo el nombre: NIF + localidad, y estado / vínculo FACTUSOL
          / régimen como tres pastillas (más el chip VIES). */}
      <div className="company-ficha-id">
        <p className="company-ficha-nif">
          {nif ? <strong className="mono">{nif}</strong> : <span className="muted">sin NIF</span>}
          {lugar ? <span>· {lugar}</span> : null}
        </p>
        <p className="company-ficha-pills" aria-label="Estado, vínculo FACTUSOL y régimen">
          <span className={`erp-flow-pill ${archived ? "is-n" : "is-g"}`}>
            {archived ? "Archivada" : "Activa"}
          </span>
          {linked ? (
            <span className="erp-flow-pill is-g"
                  title={`Cliente nº ${company.factusol_company_id} en FACTUSOL (F_CLI)`}>
              En FACTUSOL · CLI-{company.factusol_company_id}
            </span>
          ) : (
            <span className="erp-flow-pill is-n"
                  title="Sin cliente en FACTUSOL: sin F_CLI no hay albarán, factura ni proforma">
              Solo CRM
            </span>
          )}
          {fiscal ? <RegimePill regime={fiscal.regime} country={fiscal.country_iso2} /> : null}
          {viesChip ? (
            <span className={`badge ${viesChip.tone}`} style={{ textTransform: "none", letterSpacing: 0 }}
                  title={vies?.name ? `Según VIES: ${vies.name}` : undefined}>
              {viesChip.text}
            </span>
          ) : null}
        </p>
      </div>

      {error ? <p className="form-error">{error}</p> : null}

      {/* Barra de alerta: VAT no válido en VIES (bloquea la exención), CRM ≠
          FACTUSOL (con «Traer datos»), o sin vincular. */}
      {!archived && (viesInvalid || differs || !linked) ? (
        <div className={`erp-flow-alertbar${viesInvalid || !linked ? " is-blocking" : ""}`}
             role="alert" aria-label="Alertas de la empresa">
          {viesInvalid ? (
            <div className="erp-flow-alert">
              <span aria-hidden>!</span>
              <span>
                El NIF-IVA {vies?.vat} NO es válido en VIES: no se puede eximir de IVA y se
                factura como nacional con IVA. Corrige el NIF-IVA en «Datos» o revalida.
              </span>
              <span className="erp-flow-alert-fix">
                <button type="button" className="button small secondary" disabled={viesBusy}
                        onClick={() => void revalidateVies(true)}>
                  {viesBusy ? "Consultando VIES…" : "Revalidar en VIES"}
                </button>
              </span>
            </div>
          ) : null}
          {differs ? (
            <div className="erp-flow-alert">
              <span aria-hidden>!</span>
              <span>
                Los datos del CRM no coinciden con FACTUSOL
                {" "}({diffs.map((d) => d.field).join(", ")}). FACTUSOL es la fuente de verdad.
              </span>
              {canEdit ? (
                <span className="erp-flow-alert-fix">
                  <button type="button" className="button small secondary"
                          onClick={() => setPullSignal((n) => n + 1)}>
                    Traer datos de FACTUSOL
                  </button>
                </span>
              ) : null}
            </div>
          ) : !linked ? (
            <div className="erp-flow-alert">
              <span aria-hidden>!</span>
              <span>
                Empresa sin vincular a FACTUSOL: sin cliente F_CLI no hay albarán, factura ni proforma.
              </span>
              <span className="erp-flow-alert-fix">
                <a href="#factusol" className="button small secondary">Vincular o crear en FACTUSOL</a>
              </span>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="erp-flow-grid2">
        <section className="erp-flow-panel company-fiscal" aria-label="Datos fiscales">
          <h3>
            Datos fiscales{" "}
            <span className={`badge ${syncLabel.tone}`} style={{ textTransform: "none", letterSpacing: 0 }}>
              {syncLabel.text}
            </span>
          </h3>
          {/* Panel de pares etiqueta/valor (E5): dos columnas desde 1280 px y
              una por debajo. La fila VIES está SIEMPRE, aunque diga «No
              aplica», para que el hueco no genere dudas. */}
          <dl className="erp-kv-grid">
            <dt>NIF / VAT</dt>
            <dd className="mono">
              {company.tax_id || "—"}{company.vat && company.vat !== company.tax_id ? ` · ${company.vat}` : ""}
            </dd>
            <dt>Régimen IVA</dt>
            <dd>
              {fiscal ? (
                <>
                  {fiscal.regime_label}
                  <span className="muted small"> · {fiscal.regime_reason}</span>
                </>
              ) : "—"}
              {sync?.customer?.regime_label && fiscal && sync.customer.regime !== fiscal.regime ? (
                <span className="badge warn" title="La ficha F_CLI tiene otro régimen: usa «Comprobar régimen de IVA»">
                  FACTUSOL: {sync.customer.regime_label}
                </span>
              ) : null}
            </dd>
            <dt>VIES</dt>
            <dd className="company-fiscal-vies">
              {vies ? (
                <>
                  <span className={`badge ${viesChip?.tone ?? "muted"}`}
                        style={{ textTransform: "none", letterSpacing: 0 }}>
                    {viesChip ? viesChip.text : "—"}
                  </span>
                  {vies.name ? <span>{vies.name}</span> : null}
                  <span className="muted">
                    {vies.checked_at
                      ? `comprobado el ${formatBackendDateTime(vies.checked_at)}`
                      : "todavía sin comprobar"}
                  </span>
                  {vies.status === "desconocido" || vies.status === "pendiente" ? (
                    <span className="muted">
                      se reintenta solo en segundo plano
                      {vies.next_retry_at ? ` (próximo intento ${formatBackendDateTime(vies.next_retry_at)})` : ""}
                    </span>
                  ) : null}
                  <button type="button" className="button small secondary" disabled={viesBusy || archived}
                          title={archived
                            ? ARCHIVED_HINT
                            : "Consulta el NIF-IVA en el servicio oficial de la UE (salta la caché)"}
                          onClick={() => void revalidateVies(true)}>
                    {viesBusy ? "Consultando VIES…" : "Volver a comprobar"}
                  </button>
                  {viesError ? <span className="form-error small">{viesError}</span> : null}
                </>
              ) : (
                <>
                  <span className="erp-flow-pill is-n">No aplica</span>
                  <span className="muted">solo para NIF-IVA de la UE fuera de España</span>
                </>
              )}
            </dd>
            <dt>Dirección</dt>
            <dd>
              {[company.address_line, [company.postal_code, company.city].filter(Boolean).join(" ")]
                .filter(Boolean).join(" · ") || "—"}
            </dd>
            <dt>País</dt>
            <dd>
              {company.country || "—"}
              {fiscal?.country_iso2 && fiscal.country_iso2 !== company.country ? ` (${fiscal.country_iso2})` : ""}
            </dd>
            <dt>Web</dt>
            <dd>{company.website || company.domain || "—"}</dd>
          </dl>
        </section>

        <CompanyActivityPanel
          companyId={company.id}
          factusolCodcli={company.factusol_company_id}
          contactsCount={contacts.length}
        />
      </div>

      <div className="tab-bar">
        <button
          type="button"
          className={`tab${tab === "data" ? " is-active" : ""}`}
          onClick={() => setTab("data")}
        >
          <Building2 size={12} aria-hidden /> Datos
        </button>
        <button
          type="button"
          className={`tab${tab === "contacts" ? " is-active" : ""}`}
          onClick={() => setTab("contacts")}
        >
          <Users size={12} aria-hidden /> Contactos ({contacts.length})
        </button>
        <button
          type="button"
          className={`tab${tab === "quotes" ? " is-active" : ""}`}
          onClick={() => setTab("quotes")}
        >
          <FileText size={12} aria-hidden /> Proformas FACTUSOL
        </button>
      </div>

      {tab === "data" ? (
        <form className="company-edit-form erp-flow-panel" onSubmit={onSave}>
          <div className="form-grid">
            <label className="field">
              Nombre
              <input
                type="text"
                value={company.name}
                onChange={(e) => onChange("name", e.target.value)}
                required
              />
            </label>
            <label className="field">
              Sitio web
              <input
                type="text"
                value={company.website ?? ""}
                onChange={(e) => onChange("website", e.target.value || null)}
                placeholder="https://bomedia.net"
              />
            </label>
            <label className="field">
              Dominio
              <input
                type="text"
                value={company.domain ?? ""}
                onChange={(e) => onChange("domain", e.target.value || null)}
                placeholder="bomedia.net"
              />
            </label>
            <label className="field">
              CIF
              <input
                type="text"
                value={company.tax_id ?? ""}
                onChange={(e) => onChange("tax_id", e.target.value || null)}
              />
            </label>
            <label className="field">
              VAT
              <input
                type="text"
                value={company.vat ?? ""}
                onChange={(e) => onChange("vat", e.target.value || null)}
              />
            </label>
            <label className="field">
              Sector
              <input
                type="text"
                value={company.sector ?? ""}
                onChange={(e) => onChange("sector", e.target.value || null)}
              />
            </label>
            <label className="field">
              País
              <input
                type="text"
                value={company.country ?? ""}
                onChange={(e) => onChange("country", e.target.value || null)}
              />
            </label>
            <label className="field">
              Región
              <input
                type="text"
                value={company.region ?? ""}
                onChange={(e) => onChange("region", e.target.value || null)}
              />
            </label>
            <label className="field">
              Provincia
              <input
                type="text"
                value={company.state ?? ""}
                onChange={(e) => onChange("state", e.target.value || null)}
              />
            </label>
            <label className="field">
              Ciudad
              <input
                type="text"
                value={company.city ?? ""}
                onChange={(e) => onChange("city", e.target.value || null)}
              />
            </label>
            <label className="field">
              Dirección
              <input
                type="text"
                value={company.address_line ?? ""}
                onChange={(e) =>
                  onChange("address_line", e.target.value || null)
                }
              />
            </label>
            <label className="field">
              Código postal
              <input
                type="text"
                value={company.postal_code ?? ""}
                onChange={(e) =>
                  onChange("postal_code", e.target.value || null)
                }
              />
            </label>
          </div>
          <label className="field">
            Notas
            <textarea
              rows={5}
              value={company.notes ?? ""}
              onChange={(e) => onChange("notes", e.target.value || null)}
            />
          </label>
          <p className="muted small">
            Fuente: {company.source} · Actualizada{" "}
            {formatBackendDateTime(company.updated_at)}
          </p>
          <div className="form-actions">
            <button
              type="submit"
              className="button small"
              disabled={saving}
            >
              <Save size={11} aria-hidden /> {saving ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </form>
      ) : null}

      {tab === "contacts" ? (
        contacts.length === 0 ? (
          <p className="muted">No hay contactos vinculados.</p>
        ) : (
          <div className="erp-flow-panel erp-flow-table">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Nombre</th>
                  <th>Email</th>
                  <th>Teléfono</th>
                  <th>Estado</th>
                </tr>
              </thead>
              <tbody>
                {contacts.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Link href={`/contacts/${c.id}`}>
                        {c.first_name} {c.last_name ?? ""}
                      </Link>
                    </td>
                    <td className="muted small">{c.email || "—"}</td>
                    <td className="muted small">{c.phone || "—"}</td>
                    <td className="muted small">{c.commercial_status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}

      {tab === "quotes" ? (
        <CompanyQuotesPanel
          companyId={company.id}
          companyName={company.name}
          factusolCodcli={company.factusol_company_id}
          createSignal={quoteSignal}
          onOrderCreated={(orderId) => router.push(`/erp/orders/${orderId}`)}
        />
      ) : null}

      {/* C-3: vínculo con el cliente FACTUSOL (solo link, sin auto-sync). La
          alerta «CRM ≠ FACTUSOL» ya está arriba: aquí quedan la tabla de
          diferencias, vincular / crear, traer datos y el régimen. */}
      <div id="factusol">
        <CompanyFactusolPanel
          company={company}
          inlineDiffAlert={false}
          pullSignal={pullSignal}
          regimeSignal={regimeSignal}
          onSync={setSync}
          onPulled={() => void load()}
          onLinked={(codcli) =>
            setCompany((prev) => (prev ? { ...prev, factusol_company_id: codcli } : prev))
          }
          onMerged={(survivorId) => router.push(`/companies/${survivorId}`)}
        />
      </div>

      {mergeOpen ? (
        <MergeDialog
          source={company}
          onClose={() => setMergeOpen(false)}
          onMerged={(target) => {
            setMergeOpen(false);
            router.push(`/companies/${target.id}`);
          }}
        />
      ) : null}
    </main>
  );
}

/** «Fusionar»: elige la empresa destino con el buscador unificado (Fase 2).
 *  Antes usaba los mismos overlays anidados y clases `.btn` que el modal roto
 *  de la ficha de contacto. */
function MergeDialog({
  source,
  onClose,
  onMerged,
}: {
  source: Company;
  onClose: () => void;
  onMerged: (target: Company) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onPick = async (target: Company) => {
    if (target.id === source.id) {
      setError("Esa es la misma empresa.");
      return;
    }
    if (
      !confirm(
        `¿Fusionar "${source.name}" en "${target.name}"? Todo lo de "${source.name}" (pedidos, contactos, tareas, actividad y vínculo FACTUSOL) pasa a "${target.name}" y "${source.name}" se archiva (reversible: no se borra).`,
      )
    )
      return;
    setBusy(true);
    try {
      const merged = await mergeCompanies(source.id, target.id);
      onMerged(merged);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo fusionar."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-dialog company-picker-dialog" role="dialog" aria-modal="true"
           aria-labelledby="merge-title" onMouseDown={(e) => e.stopPropagation()}>
        <div className="company-picker-head">
          <h2 id="merge-title">Fusionar &quot;{source.name}&quot; con otra empresa</h2>
          <button type="button" className="button small secondary" onClick={onClose}
                  aria-label="Cerrar" disabled={busy}>
            ✕
          </button>
        </div>
        <p className="muted small">
          Busca la empresa destino. Los contactos de &quot;{source.name}&quot;
          se reasignarán y &quot;{source.name}&quot; se borrará.
        </p>
        {error ? <p className="form-error">{error}</p> : null}
        <CompanySearch autoFocus label="Empresa destino" onPick={(c) => void onPick(c)} />
        <div className="form-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
        </div>
      </div>
    </div>
  );
}
