"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getCurrentUser } from "../lib/api";
import {
  createCompany,
  fiscalCheck,
  type Company,
  type CompanyWrite,
  type FiscalCheck,
} from "../lib/companiesApi";
import { createFactusolCustomer, ERP_EDIT_ROLES } from "../lib/erpApi";
import { extractErrorMessage } from "../lib/errors";

const CHECK_DEBOUNCE_MS = 400;

/** Países habituales para el `datalist` (el backend acepta ISO2 o nombre y
 *  lo normaliza; cualquier otro se escribe a mano). */
const COUNTRY_HINTS = [
  "ES", "FR", "PT", "DE", "IT", "NL", "BE", "AT", "IE", "PL", "SE", "DK", "FI",
  "GB", "CH", "NO", "US", "MX", "AR", "CL", "MA",
];

const LANGS = [
  { value: "", label: "— por el país —" },
  { value: "es", label: "Español" }, { value: "en", label: "English" },
  { value: "de", label: "Deutsch" }, { value: "fr", label: "Français" },
  { value: "nl", label: "Nederlands" },
];

const REGIME_TONE: Record<FiscalCheck["regime"], string> = {
  nacional: "n", intracomunitario: "p", exportacion: "t",
};
const REGIME_SHORT: Record<FiscalCheck["regime"], string> = {
  nacional: "Nacional · con IVA",
  intracomunitario: "Intracomunitario · sin IVA",
  exportacion: "Exportación · sin IVA",
};

export type CompanyCreated = {
  company: Company;
  /** Resultado del alta en FACTUSOL si se pidió: `null` si no se pidió. */
  factusol: { codcli: string; created: boolean; regime_label: string | null } | null;
  /** El alta CRM fue bien pero FACTUSOL no: se avisa, no se pierde la empresa. */
  factusolError: string | null;
};

/** «Crear empresa» (rediseño de flujo, Fase 2). Los datos fiscales mandan: de
 *  aquí sale todo (pedidos, albaranes, facturas, IVA).
 *
 *  - Identificación fiscal: nombre fiscal, NIF/CIF, NIF-IVA y país → el
 *    régimen de IVA se detecta al vuelo (`GET /api/companies/fiscal-check`,
 *    la misma regla que fija la ficha F_CLI) y se enseña con su porqué.
 *  - Anti-duplicados: si ya existe una empresa con ese NIF en el CRM se avisa
 *    y se ofrece usar la existente; si existe en FACTUSOL, «Crear también en
 *    FACTUSOL» la VINCULA en vez de crear otra ficha.
 *  - «Crear también en FACTUSOL» (roles de edición del ERP): tras crear la
 *    empresa se da de alta el cliente F_CLI con su régimen y se vincula.
 *  - Gancho VIES: el chip del régimen dirá «verificado en VIES» / «pendiente»
 *    / «VAT no válido» cuando entre su PR; hoy el backend devuelve `pendiente`.
 *
 *  Se usa como pantalla (`/companies/new`) y dentro del modal del buscador
 *  (crear y elegir sin salir). Es un superconjunto del alta rápida de antes
 *  (nombre + dominio). */
export function CompanyCreateForm({
  initialName = "",
  onCreated,
  onCancel,
  onUseExisting,
  compact = false,
}: {
  initialName?: string;
  onCreated: (result: CompanyCreated) => void;
  onCancel?: () => void;
  /** Ya existe con ese NIF: usar esa en vez de crear otra (el modal la elige;
   *  la pantalla enlaza a su ficha). */
  onUseExisting?: (company: { id: string; name: string }) => void;
  /** Dentro de un modal: sin cabeceras grandes. */
  compact?: boolean;
}) {
  const [name, setName] = useState(initialName);
  const [taxId, setTaxId] = useState("");
  const [vat, setVat] = useState("");
  const [country, setCountry] = useState("");
  const [addressLine, setAddressLine] = useState("");
  const [postalCode, setPostalCode] = useState("");
  const [city, setCity] = useState("");
  const [stateProv, setStateProv] = useState("");
  const [website, setWebsite] = useState("");
  const [domain, setDomain] = useState("");
  const [language, setLanguage] = useState("");
  const [sector, setSector] = useState("");
  const [notes, setNotes] = useState("");
  const [canFactusol, setCanFactusol] = useState(false);
  const [alsoFactusol, setAlsoFactusol] = useState(true);
  const [check, setCheck] = useState<FiscalCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setCanFactusol((ERP_EDIT_ROLES as readonly string[]).includes(u.role)))
      .catch(() => setCanFactusol(false));
  }, []);

  // Comprobación fiscal al vuelo: régimen + duplicados, con debounce.
  useEffect(() => {
    const tax = taxId.trim();
    const v = vat.trim();
    const c = country.trim();
    if (!tax && !v && !c) {
      setCheck(null);
      setChecking(false);
      return;
    }
    let alive = true;
    setChecking(true);
    const handle = window.setTimeout(() => {
      fiscalCheck({ tax_id: tax, vat: v, country: c })
        .then((r) => { if (alive) setCheck(r); })
        .catch(() => { if (alive) setCheck(null); })
        .finally(() => { if (alive) setChecking(false); });
    }, CHECK_DEBOUNCE_MS);
    return () => { alive = false; window.clearTimeout(handle); };
  }, [taxId, vat, country]);

  const dupCrm = check?.duplicates.crm ?? [];
  const dupFactusol = check?.duplicates.factusol ?? null;
  const hasNif = !!(taxId.trim() || vat.trim());

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    const payload: CompanyWrite = {
      name: name.trim(),
      tax_id: taxId.trim() || null,
      vat: vat.trim() || null,
      country: country.trim() || null,
      address_line: addressLine.trim() || null,
      postal_code: postalCode.trim() || null,
      city: city.trim() || null,
      state: stateProv.trim() || null,
      website: website.trim() || null,
      domain: domain.trim() || null,
      sector: sector.trim() || null,
      notes: notes.trim() || null,
      ...(language ? { language } : {}),
    } as CompanyWrite;
    let company: Company;
    try {
      company = await createCompany(payload);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear la empresa."));
      setSubmitting(false);
      return;
    }
    // Alta en FACTUSOL (opcional): la empresa YA existe en el CRM; si esto
    // falla se avisa y se puede vincular después desde su ficha.
    let factusol: CompanyCreated["factusol"] = null;
    let factusolError: string | null = null;
    if (canFactusol && alsoFactusol) {
      try {
        const r = await createFactusolCustomer({
          crm_type: "company", crm_id: company.id,
          nombre: company.name,
          nif: (taxId.trim() || vat.trim()) || undefined,
          direccion: addressLine.trim() || undefined,
          ciudad: city.trim() || undefined,
          cp: postalCode.trim() || undefined,
          provincia: stateProv.trim() || undefined,
          pais: country.trim() || undefined,
          vat: vat.trim() || undefined,
        });
        factusol = {
          codcli: r.factusol_codcli, created: r.created,
          regime_label: r.regime_label ?? null,
        };
      } catch (err) {
        factusolError = extractErrorMessage(err, "No se pudo crear el cliente en FACTUSOL.");
      }
    }
    setSubmitting(false);
    onCreated({ company, factusol, factusolError });
  }

  return (
    <form className={`company-create${compact ? " is-compact" : ""}`} onSubmit={onSubmit}>
      {error ? <p className="form-error" role="alert">{error}</p> : null}

      <section className="erp-flow-panel" aria-label="Identificación fiscal">
        <h3>Identificación fiscal</h3>
        <div className="company-create-row">
          <label className="field company-create-span2">
            <span>Nombre fiscal *</span>
            <input
              type="text" value={name} required maxLength={300} autoFocus
              placeholder="SAS La Maison de la Plaque"
              onChange={(e) => setName(e.target.value)}
            />
          </label>
        </div>
        <div className="company-create-row company-create-row3">
          <label className="field">
            <span>NIF / CIF</span>
            <input
              type="text" value={taxId} maxLength={64} placeholder="B64113590"
              onChange={(e) => setTaxId(e.target.value)}
            />
          </label>
          <label className="field">
            <span>NIF-IVA (VAT intracomunitario)</span>
            <input
              type="text" value={vat} maxLength={40} placeholder="FR16339753527"
              onChange={(e) => setVat(e.target.value)}
            />
          </label>
          <label className="field">
            <span>País</span>
            <input
              type="text" value={country} maxLength={120} list="company-create-countries"
              placeholder="ES, FR, Francia…"
              onChange={(e) => setCountry(e.target.value)}
            />
            <datalist id="company-create-countries">
              {COUNTRY_HINTS.map((c) => <option key={c} value={c} />)}
            </datalist>
          </label>
        </div>

        {/* Régimen detectado (misma regla que la ficha F_CLI) + gancho VIES. */}
        {check ? (
          <p className="company-create-detect" role="status">
            ✓ Régimen detectado:{" "}
            <span className={`erp-flow-pill is-${REGIME_TONE[check.regime]}`}>
              {REGIME_SHORT[check.regime]}
            </span>
            <span className="muted small company-create-reason">
              {check.regime_reason}
              {check.in_eu && (check.vat_normalized || vat.trim()) ? (
                check.vies.status === "valido" ? " · ✓ verificado en VIES"
                : check.vies.status === "no_valido" ? " · VAT no válido en VIES"
                : " · VIES: pendiente de validar"
              ) : ""}
            </span>
          </p>
        ) : checking ? (
          <p className="muted small">Comprobando datos fiscales…</p>
        ) : null}

        {/* Anti-duplicados: CRM y FACTUSOL. */}
        {dupCrm.length > 0 ? (
          <div className="company-create-dup" role="alert">
            <p>
              ! Ya existe en el CRM con ese NIF:{" "}
              {dupCrm.map((c, i) => (
                <span key={c.id}>
                  {i > 0 ? ", " : ""}
                  <strong>{c.name}</strong>
                  {c.factusol_company_id ? ` (FACTUSOL nº ${c.factusol_company_id})` : " (solo CRM)"}
                </span>
              ))}
              . No crees otra.
            </p>
            <div className="form-actions">
              {onUseExisting ? (
                <button type="button" className="button small secondary"
                        onClick={() => onUseExisting({ id: dupCrm[0].id, name: dupCrm[0].name })}>
                  Usar «{dupCrm[0].name}»
                </button>
              ) : (
                <Link href={`/companies/${dupCrm[0].id}`} className="button small secondary">
                  Abrir «{dupCrm[0].name}»
                </Link>
              )}
            </div>
          </div>
        ) : null}
        {hasNif && check ? (
          <p className="company-create-hint muted small">
            {dupFactusol ? (
              <>
                i Ya existe en FACTUSOL con ese NIF: cliente nº {dupFactusol.codcli}
                {dupFactusol.nombre ? ` «${dupFactusol.nombre}»` : ""}.
                {canFactusol ? " Al guardar se vinculará a ese cliente (no se crea otro)." : ""}
              </>
            ) : check.duplicates.factusol_checked ? (
              <>i No existe en FACTUSOL con ese NIF. {canFactusol && alsoFactusol ? "Se creará como cliente nuevo al guardar." : ""}</>
            ) : (
              <>i No se pudo comprobar en FACTUSOL{check.duplicates.factusol_error ? ` (${check.duplicates.factusol_error})` : ""}.</>
            )}
          </p>
        ) : null}
      </section>

      <section className="erp-flow-panel" aria-label="Dirección">
        <h3>Dirección</h3>
        <div className="company-create-row">
          <label className="field company-create-span2">
            <span>Domicilio</span>
            <input type="text" value={addressLine} maxLength={500}
                   placeholder="Rue du Chemin Noir"
                   onChange={(e) => setAddressLine(e.target.value)} />
          </label>
        </div>
        <div className="company-create-row company-create-row3">
          <label className="field">
            <span>CP</span>
            <input type="text" value={postalCode} maxLength={20}
                   onChange={(e) => setPostalCode(e.target.value)} />
          </label>
          <label className="field">
            <span>Población</span>
            <input type="text" value={city} maxLength={200}
                   onChange={(e) => setCity(e.target.value)} />
          </label>
          <label className="field">
            <span>Provincia</span>
            <input type="text" value={stateProv} maxLength={200}
                   onChange={(e) => setStateProv(e.target.value)} />
          </label>
        </div>
      </section>

      <section className="erp-flow-panel" aria-label="Contacto">
        <h3>Contacto</h3>
        <div className="company-create-row">
          <label className="field">
            <span>Web</span>
            <input type="text" value={website} maxLength={500} placeholder="https://bomedia.net"
                   onChange={(e) => setWebsite(e.target.value)} />
          </label>
          <label className="field">
            <span>Dominio</span>
            <input type="text" value={domain} maxLength={255} placeholder="bomedia.net"
                   onChange={(e) => setDomain(e.target.value)} />
          </label>
        </div>
        <div className="company-create-row">
          <label className="field">
            <span>Idioma de los documentos</span>
            <select value={language} aria-label="Idioma de los documentos"
                    onChange={(e) => setLanguage(e.target.value)}>
              {LANGS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Sector</span>
            <input type="text" value={sector} maxLength={120}
                   onChange={(e) => setSector(e.target.value)} />
          </label>
        </div>
        <label className="field">
          <span>Notas</span>
          <textarea value={notes} rows={2} onChange={(e) => setNotes(e.target.value)} />
        </label>
      </section>

      <div className="company-create-actions">
        <button type="submit" className="button" disabled={submitting || !name.trim()}>
          {submitting ? "Creando…" : "Crear empresa"}
        </button>
        {onCancel ? (
          <button type="button" className="button secondary" onClick={onCancel} disabled={submitting}>
            Cancelar
          </button>
        ) : null}
        {canFactusol ? (
          <label className="checkbox-inline company-create-factusol">
            <input
              type="checkbox" checked={alsoFactusol}
              aria-label="Crear también en FACTUSOL"
              onChange={(e) => setAlsoFactusol(e.target.checked)}
            />
            <span className="small">Crear también en FACTUSOL</span>
          </label>
        ) : null}
      </div>
    </form>
  );
}
