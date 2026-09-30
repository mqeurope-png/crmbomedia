"use client";

import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../lib/api";
import { listCompanies, type Company } from "../../lib/companiesApi";
import {
  createFactusolCustomerAndLink,
  linkFactusolCustomer,
  linkOrderDocument,
  listFactusolDocuments,
  previewLinkOrderDocument,
  searchFactusolCustomers,
  type FactusolDocument,
  type LinkableDocType,
  type LinkDocumentPreview,
  type OrderDetail,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const DOC_TABS: { value: LinkableDocType; label: string; singular: string }[] = [
  { value: "facturas", label: "Factura", singular: "la factura" },
  { value: "albaranes", label: "Albarán", singular: "el albarán" },
  { value: "presupuestos", label: "Proforma", singular: "la proforma" },
];

function eur(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${n.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
}

function lineBase(l: LinkDocumentPreview["lines"][number]): number {
  return l.quantity * l.unit_price * (1 - (l.discount_pct || 0) / 100);
}

/** «Vincular documento FACTUSOL» a una MUESTRA: albarán, proforma o factura,
 *  elegido por serie + número con buscador (como ERP · Documentos). Antes de
 *  vincular enseña lo que cargará el pedido (cliente → empresa CRM, líneas,
 *  base / IVA / total, serie, forma de pago). Si el cliente FACTUSOL no tiene
 *  empresa en el CRM, abre aquí mismo el flujo de vincular a una existente o
 *  crearla con sus datos — nunca falla en silencio. No escribe en FACTUSOL. */
export function VincularDocumentoModal({
  orderId,
  orderNumber,
  initial,
  onClose,
  onDone,
}: {
  orderId: string;
  orderNumber: string;
  /** «Reprocesar vínculo»: el documento ya apuntado, preseleccionado. */
  initial?: { doc_type: LinkableDocType; serie: number; codigo: number } | null;
  onClose: () => void;
  onDone?: (detail: OrderDetail) => void;
}) {
  const [docType, setDocType] = useState<LinkableDocType>(initial?.doc_type ?? "facturas");
  const [q, setQ] = useState("");
  const [results, setResults] = useState<FactusolDocument[]>([]);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<{ serie: number; codigo: number } | null>(
    initial ? { serie: initial.serie, codigo: initial.codigo } : null,
  );
  const [preview, setPreview] = useState<LinkDocumentPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewKey, setPreviewKey] = useState(0);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Cliente FACTUSOL sin empresa CRM: vincular a una existente o crearla.
  const [companyQuery, setCompanyQuery] = useState("");
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState("");
  const [companyBusy, setCompanyBusy] = useState(false);
  const [companyNotice, setCompanyNotice] = useState<string | null>(null);

  const tab = DOC_TABS.find((t) => t.value === docType) ?? DOC_TABS[0];

  // Buscador de documentos (serie + número, cliente, referencia).
  useEffect(() => {
    if (initial && selected) return;
    let alive = true;
    const handle = window.setTimeout(() => {
      setSearching(true);
      listFactusolDocuments(docType, { q: q.trim() || undefined, limit: 15 })
        .then((page) => { if (alive) setResults(page.items ?? []); })
        .catch(() => { if (alive) setResults([]); })
        .finally(() => { if (alive) setSearching(false); });
    }, 300);
    return () => { alive = false; window.clearTimeout(handle); };
  }, [docType, q, initial, selected]);

  // Lo que cargaría el pedido con el documento elegido.
  useEffect(() => {
    if (!selected) return;
    let alive = true;
    Promise.resolve()
      .then(() => {
        if (alive) { setPreview(null); setPreviewError(null); setConfirm(false); }
        return previewLinkOrderDocument(orderId, { doc_type: docType, ...selected });
      })
      .then((p) => { if (alive) setPreview(p); })
      .catch((e) => {
        if (alive) setPreviewError(extractErrorMessage(e, "No se pudo leer el documento en FACTUSOL."));
      });
    return () => { alive = false; };
  }, [orderId, docType, selected, previewKey]);

  const needsCompany = !!preview && !preview.company_linked;
  useEffect(() => {
    if (!needsCompany) return;
    const handle = window.setTimeout(() => {
      listCompanies({ q: companyQuery || undefined, limit: 20 })
        .then((page) => setCompanies(page.items.filter((c) => !c.factusol_company_id)))
        .catch(() => setCompanies([]));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [needsCompany, companyQuery]);

  const totals = useMemo(() => {
    if (!preview) return null;
    const base = preview.lines.reduce((acc, l) => acc + lineBase(l), 0);
    const total = preview.total ?? null;
    return { base, total, iva: total != null ? total - base : null };
  }, [preview]);

  async function createCompanyFromFactusol() {
    if (!preview?.cliente_codigo) return;
    setCompanyBusy(true);
    setCompanyNotice(null);
    try {
      const hits = await searchFactusolCustomers(preview.cliente_codigo, "codcli");
      const cust = hits.find((c) => String(c.codcli ?? "") === String(preview.cliente_codigo)) ?? hits[0];
      const nombre = cust?.nombre ?? cust?.nofcli ?? preview.cliente_nombre ?? "";
      await createFactusolCustomerAndLink({
        factusol_codcli: preview.cliente_codigo,
        factusol_customer_data: {
          nombre,
          nif: cust?.nif ?? "",
          direccion: cust?.domcli ?? "",
          ciudad: cust?.pobcli ?? "",
          cp: cust?.cpocli ?? "",
          provincia: cust?.procli ?? "",
          telefono: cust?.telcli?.trim() || undefined,
          email: cust?.emacli ?? undefined,
          pais: cust?.paicli ?? undefined,
        },
      });
      setCompanyNotice(`Empresa CRM «${nombre}» creada y vinculada al cliente FACTUSOL ${preview.cliente_codigo}.`);
      setPreviewKey((k) => k + 1);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo crear la empresa CRM."));
    } finally {
      setCompanyBusy(false);
    }
  }

  async function linkExistingCompany() {
    if (!preview?.cliente_codigo || !companyId) return;
    setCompanyBusy(true);
    setCompanyNotice(null);
    try {
      await linkFactusolCustomer({
        crm_type: "company", crm_id: companyId, factusol_codcli: preview.cliente_codigo,
      });
      const comp = companies.find((c) => c.id === companyId);
      setCompanyNotice(`Cliente FACTUSOL ${preview.cliente_codigo} vinculado a «${comp?.name ?? "la empresa"}».`);
      setPreviewKey((k) => k + 1);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo vincular el cliente a la empresa."));
    } finally {
      setCompanyBusy(false);
    }
  }

  async function submit() {
    if (!selected || !preview || needsCompany || preview.linked_elsewhere) return;
    setBusy(true);
    setError(null);
    try {
      const detail = await linkOrderDocument(orderId, { doc_type: docType, ...selected });
      onDone?.(detail);
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.code === "factusol_customer_unlinked") {
        setPreviewKey((k) => k + 1);
      }
      setError(extractErrorMessage(e, "No se pudo vincular el documento."));
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = !!preview && !needsCompany && !preview.linked_elsewhere && confirm && !busy;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Vincular documento FACTUSOL a ${orderNumber}`}>
      <div className="modal-dialog erp-modal">
        <div className="modal-header">
          <h2>Vincular documento FACTUSOL <span className="muted">{orderNumber}</span></h2>
          <button type="button" className="modal-close" aria-label="Cerrar" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <p className="muted small">
            La muestra carga los datos del documento (cliente, líneas, importes, serie y
            forma de pago) y pasa a comportarse como un pedido creado desde él. Conserva su
            número y la marca «muestra». No se escribe nada en FACTUSOL.
          </p>
          <div className="erp-tabs" role="tablist" aria-label="Tipo de documento">
            {DOC_TABS.map((t) => (
              <button key={t.value} type="button" role="tab" aria-selected={docType === t.value}
                      className={`erp-tab${docType === t.value ? " active" : ""}`}
                      disabled={busy}
                      onClick={() => { setDocType(t.value); setSelected(null); setPreview(null); }}>
                {t.label}
              </button>
            ))}
          </div>
          {!selected ? (
            <>
              <label className="field">
                <span>Buscar {tab.label.toLowerCase()} (serie-número, cliente o referencia)</span>
                <input type="search" aria-label="Buscar documento FACTUSOL" value={q}
                       placeholder="2-526110, PREMO…" onChange={(e) => setQ(e.target.value)} />
              </label>
              {searching ? <p className="muted small">Buscando en FACTUSOL…</p> : null}
              <ul className="item-list small" aria-label="Documentos encontrados">
                {results.map((d) => (
                  <li key={d.numero}>
                    <button type="button" className="button small secondary"
                            aria-label={`Elegir ${tab.label.toLowerCase()} ${d.numero}`}
                            disabled={d.serie == null || d.codigo == null}
                            onClick={() => setSelected({ serie: Number(d.serie), codigo: Number(d.codigo) })}>
                      {d.numero}
                    </button>{" "}
                    {d.cliente_nombre ?? "—"} · {eur(d.total)}
                    {d.fecha ? ` · ${d.fecha.slice(0, 10)}` : ""}
                  </li>
                ))}
                {!searching && results.length === 0 ? (
                  <li className="muted">Sin resultados.</li>
                ) : null}
              </ul>
            </>
          ) : (
            <>
              <p>
                <button type="button" className="button small secondary"
                        disabled={busy} onClick={() => { setSelected(null); setPreview(null); }}>
                  ← Elegir otro
                </button>
              </p>
              {previewError ? <p className="form-error" role="alert">{previewError}</p> : null}
              {!preview && !previewError ? <p className="muted">Leyendo el documento en FACTUSOL…</p> : null}
              {preview ? (
                <div aria-label="Datos que se cargarán">
                  <p>
                    <strong>{tab.label} {preview.numero}</strong> · cliente FACTUSOL{" "}
                    {preview.cliente_codigo ?? "—"} {preview.cliente_nombre ?? ""}
                  </p>
                  <dl className="erp-flow-kv-list small">
                    <div className="erp-flow-kv"><dt className="k">Empresa CRM</dt>
                      <dd className="v">{preview.company_name ?? "sin vincular"}</dd></div>
                    <div className="erp-flow-kv"><dt className="k">Serie</dt>
                      <dd className="v">{preview.serie}</dd></div>
                    <div className="erp-flow-kv"><dt className="k">Base</dt>
                      <dd className="v">{eur(totals?.base)}</dd></div>
                    <div className="erp-flow-kv"><dt className="k">IVA</dt>
                      <dd className="v">{eur(totals?.iva)}</dd></div>
                    <div className="erp-flow-kv"><dt className="k">Total</dt>
                      <dd className="v"><strong>{eur(totals?.total)}</strong></dd></div>
                    <div className="erp-flow-kv"><dt className="k">Forma de pago</dt>
                      <dd className="v">{preview.forma_pago_nombre ?? preview.forma_pago ?? "—"}</dd></div>
                  </dl>
                  <ul className="item-list small" aria-label="Líneas del documento">
                    {preview.lines.map((l, i) => (
                      <li key={`${i}-${l.description}`}>
                        {l.quantity} × {l.description} · {eur(lineBase(l))}
                      </li>
                    ))}
                  </ul>
                  {preview.linked_elsewhere ? (
                    <p className="form-error" role="alert">
                      Este documento ya está vinculado al pedido {preview.linked_elsewhere.order_number}.
                      Desvincúlalo allí primero.
                    </p>
                  ) : null}
                  {needsCompany ? (
                    <div className="form-info" role="region" aria-label="Vincular empresa CRM">
                      <p>
                        El cliente FACTUSOL <strong>{preview.cliente_codigo}</strong>{" "}
                        ({preview.cliente_nombre ?? "—"}) no tiene empresa en el CRM. Vincúlalo
                        a una existente o créala con sus datos para poder vincular el documento.
                      </p>
                      <button type="button" className="button small" disabled={companyBusy}
                              onClick={() => void createCompanyFromFactusol()}>
                        Crear empresa CRM con estos datos y vincular
                      </button>
                      <label className="field">
                        <span>… o vincular a una empresa CRM existente</span>
                        <input type="search" aria-label="Buscar empresa CRM" value={companyQuery}
                               onChange={(e) => setCompanyQuery(e.target.value)} />
                      </label>
                      <select aria-label="Empresa CRM" value={companyId}
                              onChange={(e) => setCompanyId(e.target.value)}>
                        <option value="">— elige la empresa —</option>
                        {companies.map((c) => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))}
                      </select>{" "}
                      <button type="button" className="button small secondary"
                              disabled={!companyId || companyBusy}
                              onClick={() => void linkExistingCompany()}>
                        Vincular empresa
                      </button>
                    </div>
                  ) : null}
                  {companyNotice ? <p className="form-success" role="status">{companyNotice}</p> : null}
                  {!needsCompany && !preview.linked_elsewhere ? (
                    <label className="checkbox">
                      <input type="checkbox" checked={confirm} disabled={busy}
                             aria-label="Confirmo cargar los datos del documento"
                             onChange={(e) => setConfirm(e.target.checked)} />{" "}
                      Confirmo: el cliente, las líneas y los importes de la muestra se
                      sustituyen por los de {tab.singular} {preview.numero}.
                    </label>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
          {error ? <p className="form-error" role="alert">{error}</p> : null}
        </div>
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="button" className="button" disabled={!canSubmit}
                  onClick={() => void submit()}>
            {busy ? "Vinculando…" : "Vincular y cargar datos"}
          </button>
        </div>
      </div>
    </div>
  );
}
