"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";
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

/** Lo que el régimen significa en la factura (Lote 2 · «régimen explicado,
 *  no etiquetado»): la consecuencia, no la etiqueta. El porqué técnico lo
 *  da `regime_reason` del backend, que se enseña debajo. */
export function regimeConsequence(check: Pick<FiscalCheck, "regime" | "vies">): string {
  if (check.regime === "intracomunitario") {
    return check.vies?.status === "valido"
      ? "La factura saldrá sin IVA: el VAT está verificado en VIES."
      : "La factura saldrá sin IVA si el VAT es válido.";
  }
  if (check.regime === "exportacion") {
    return "La factura saldrá sin IVA: exportación fuera de la UE.";
  }
  if (check.vies?.applies && check.vies.status === "no_valido") {
    return "La factura saldrá con IVA: el VAT no es válido en VIES y no se puede eximir.";
  }
  return "La factura saldrá con IVA español.";
}

/** La hora de la comprobación VIES, siempre visible: «hoy a las 11:42»,
 *  «ayer a las 11:42» o «el 14/09/2026 a las 10:00». */
export function describeCheckedAt(
  iso: string | null | undefined, now: Date = new Date(),
): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const two = (n: number) => String(n).padStart(2, "0");
  const time = `${two(d.getHours())}:${two(d.getMinutes())}`;
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(d, now)) return `hoy a las ${time}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, yesterday)) return `ayer a las ${time}`;
  return `el ${two(d.getDate())}/${two(d.getMonth() + 1)}/${d.getFullYear()} a las ${time}`;
}

/** Los cuatro estados de VIES que ve el operador (más «comprobando»). */
type ViesView = "comprobando" | "valido" | "no_valido" | "desconocido" | "pendiente";

export type CompanyCreated = {
  company: Company;
  /** Resultado del alta en FACTUSOL si se pidió: `null` si no se pidió. */
  factusol: { codcli: string; created: boolean; regime_label: string | null } | null;
  /** El alta CRM fue bien pero FACTUSOL no: se avisa, no se pierde la empresa. */
  factusolError: string | null;
};

/** «Crear empresa» (rediseño de flujo, Fase 2 · revisión Lote 2). Los datos
 *  fiscales mandan: de aquí sale todo (pedidos, albaranes, facturas, IVA).
 *
 *  - Identificación fiscal: nombre fiscal, país, NIF/CIF y NIF-IVA → el
 *    régimen de IVA se detecta al vuelo (`GET /api/companies/fiscal-check`,
 *    la misma regla que fija la ficha F_CLI) y se explica por su
 *    consecuencia («La factura saldrá sin IVA si el VAT es válido») más el
 *    porqué del backend.
 *  - Anti-duplicados AL TECLEAR el NIF: si ya existe una empresa con ese NIF
 *    en el CRM aparece su tarjeta (nombre, NIF, población, «En FACTUSOL» /
 *    «Solo CRM») con «Usar esta» y «Crear empresa» se desactiva con el motivo
 *    escrito: el alta devuelve 409 con cualquier duplicado exacto, así que no
 *    hay «crear de todas formas» que el backend permita. Si existe solo en
 *    FACTUSOL, «Crear también en FACTUSOL» la VINCULA en vez de crear otra.
 *  - VIES con cuatro estados y hora: comprobando / válido / no válido / VIES
 *    caído, siempre con «comprobado hoy a las 11:42», y «Volver a comprobar»
 *    (`force`: salta la caché) a mano.
 *  - «Crear también en FACTUSOL» (roles de edición del ERP) va con lo fiscal
 *    y dice qué implica: tras crear la empresa se da de alta el cliente F_CLI
 *    con su régimen y se vincula.
 *
 *  Se usa como pantalla (`/companies/new`) y dentro del modal del buscador
 *  (crear y elegir sin salir), siempre con el texto tecleado como nombre
 *  (`initialName`). Es un superconjunto del alta rápida de antes. */
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
  const id = useId();
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
  const [revalidating, setRevalidating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Nº de la última comprobación lanzada: una respuesta vieja (el operador
  // siguió tecleando o pulsó «Volver a comprobar») nunca pisa a la nueva.
  const checkSeq = useRef(0);

  useEffect(() => {
    getCurrentUser()
      .then((u) => setCanFactusol((ERP_EDIT_ROLES as readonly string[]).includes(u.role)))
      .catch(() => setCanFactusol(false));
  }, []);

  const runCheck = useCallback(
    (params: { tax_id: string; vat: string; country: string }, force = false) => {
      const seq = ++checkSeq.current;
      return fiscalCheck(force ? { ...params, force: true } : params)
        .then((r) => { if (checkSeq.current === seq) setCheck(r); })
        .catch(() => { if (checkSeq.current === seq) setCheck(null); })
        .finally(() => {
          if (checkSeq.current === seq) {
            setChecking(false);
            setRevalidating(false);
          }
        });
    },
    [],
  );

  // Comprobación fiscal al vuelo: régimen + duplicados + VIES, con debounce.
  useEffect(() => {
    const tax = taxId.trim();
    const v = vat.trim();
    const c = country.trim();
    if (!tax && !v && !c) {
      checkSeq.current += 1;
      setCheck(null);
      setChecking(false);
      setRevalidating(false);
      return;
    }
    setChecking(true);
    const handle = window.setTimeout(() => {
      void runCheck({ tax_id: tax, vat: v, country: c });
    }, CHECK_DEBOUNCE_MS);
    return () => { checkSeq.current += 1; window.clearTimeout(handle); };
  }, [taxId, vat, country, runCheck]);

  /** «Volver a comprobar»: VIES en vivo, saltando la caché. */
  function revalidate() {
    const tax = taxId.trim();
    const v = vat.trim();
    const c = country.trim();
    if (!tax && !v && !c) return;
    setRevalidating(true);
    void runCheck({ tax_id: tax, vat: v, country: c }, true);
  }

  const dupCrm = check?.duplicates.crm ?? [];
  const dupFactusol = check?.duplicates.factusol ?? null;
  const hasNif = !!(taxId.trim() || vat.trim());
  // El alta devuelve 409 con cualquier empresa del CRM con ese NIF, así que
  // el botón se desactiva con el motivo escrito y la salida es «Usar esta».
  const blockedBy = dupCrm[0] ?? null;
  const blockedReason = blockedBy
    ? `No se puede crear: ya existe «${blockedBy.name}» con ese NIF. Usa la existente o corrige el NIF.`
    : null;

  const viesApplies = !!check?.vies?.applies;
  const viesView: ViesView | null = revalidating
    ? "comprobando"
    : checking && viesApplies
      ? "comprobando"
      : viesApplies
        ? (check?.vies?.status ?? "pendiente")
        : null;
  const viesWhen = describeCheckedAt(check?.vies?.checked_at);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || blockedReason) return;
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

  // Qué pasa con FACTUSOL al guardar (vincular / crear / no se pudo mirar).
  const factusolHint = hasNif && check ? (
    <p className="company-create-hint">
      {dupFactusol ? (
        <>
          Ya existe en FACTUSOL con ese NIF: cliente nº {dupFactusol.codcli}
          {dupFactusol.nombre ? ` «${dupFactusol.nombre}»` : ""}.
          {canFactusol ? " Al guardar se vinculará a ese cliente (no se crea otro)." : ""}
        </>
      ) : check.duplicates.factusol_checked ? (
        <>
          No existe en FACTUSOL con ese NIF.
          {canFactusol && alsoFactusol ? " Se creará como cliente nuevo al guardar." : ""}
        </>
      ) : (
        <>
          No se pudo comprobar en FACTUSOL
          {check.duplicates.factusol_error ? ` (${check.duplicates.factusol_error})` : ""}.
        </>
      )}
    </p>
  ) : null;

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
            <span>País</span>
            <input
              type="text" value={country} maxLength={120} list={`${id}-countries`}
              placeholder="ES, FR, Francia…"
              onChange={(e) => setCountry(e.target.value)}
            />
            <datalist id={`${id}-countries`}>
              {COUNTRY_HINTS.map((c) => <option key={c} value={c} />)}
            </datalist>
          </label>
          <label className="field">
            <span>NIF / CIF</span>
            <input
              type="text" className="company-create-nif" value={taxId} maxLength={64}
              placeholder="B64113590"
              onChange={(e) => setTaxId(e.target.value)}
            />
          </label>
          <label className="field">
            <span>NIF-IVA (VAT intracomunitario)</span>
            <input
              type="text" className="company-create-nif" value={vat} maxLength={40}
              placeholder="FR16339753527"
              onChange={(e) => setVat(e.target.value)}
            />
          </label>
        </div>

        {/* Anti-duplicados al teclear: la candidata con «Usar esta» ANTES de
            dejar seguir (el botón de crear queda desactivado, ver abajo). */}
        {dupCrm.length > 0 ? (
          <div className="company-create-dup" role="alert" aria-label="Ya existe con ese NIF">
            <p className="company-create-dup-title">
              <strong>Ya existe en el CRM con ese NIF.</strong> Usa la existente en vez de crear otra.
            </p>
            <ul className="company-create-candidates">
              {dupCrm.map((c) => {
                const linked = !!c.factusol_company_id;
                const meta = [c.city, c.country].filter(Boolean).join(" · ");
                return (
                  <li key={c.id} className="company-create-candidate">
                    <span className="company-create-candidate-main">
                      <strong>{c.name}</strong>
                      <span className="company-create-candidate-meta">
                        <span className="mono">{c.tax_id || c.vat || "sin NIF"}</span>
                        {meta ? ` · ${meta}` : ""}
                      </span>
                    </span>
                    <span className={`company-pill ${linked ? "is-factusol" : "is-crm"}`}>
                      {linked ? `En FACTUSOL · nº ${c.factusol_company_id}` : "Solo CRM"}
                    </span>
                    {onUseExisting ? (
                      <button
                        type="button"
                        className={`button small${linked ? "" : " secondary"}`}
                        aria-label={`Usar «${c.name}»`}
                        onClick={() => onUseExisting({ id: c.id, name: c.name })}
                      >
                        Usar esta
                      </button>
                    ) : (
                      <Link
                        href={`/companies/${c.id}`}
                        className={`button small${linked ? "" : " secondary"}`}
                        aria-label={`Abrir «${c.name}»`}
                      >
                        Abrir esta
                      </Link>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}

        {/* Régimen explicado: la consecuencia en la factura + el porqué. */}
        {check ? (
          <div
            className={`company-create-regime is-${REGIME_TONE[check.regime]}`}
            role="status"
            aria-label="Régimen de IVA"
          >
            <p>
              <strong>Régimen detectado: {REGIME_SHORT[check.regime]}.</strong>{" "}
              {regimeConsequence(check)}
            </p>
            <p className="company-create-reason">{check.regime_reason}</p>
          </div>
        ) : checking ? (
          <p className="muted small company-create-checking">
            <span className="company-create-spinner" aria-hidden="true" />
            Comprobando datos fiscales…
          </p>
        ) : null}

        {/* VIES: cuatro estados, siempre con la hora, y «Volver a comprobar». */}
        {viesView ? (
          <div
            className={`company-create-vies is-${viesView}`}
            role="status"
            aria-label="Validación VIES"
          >
            <p>
              {viesView === "comprobando" ? (
                <>
                  <span className="company-create-spinner" aria-hidden="true" />
                  Comprobando el VAT en VIES…
                </>
              ) : viesView === "valido" ? (
                <>
                  <strong>VAT válido en VIES</strong>
                  {viesWhen ? ` · comprobado ${viesWhen}` : ""}
                  {check?.vies?.name ? (
                    <span className="company-create-vies-detail">Según VIES: {check.vies.name}</span>
                  ) : null}
                </>
              ) : viesView === "no_valido" ? (
                <>
                  <strong>VAT no válido en VIES</strong>
                  {viesWhen ? ` · comprobado ${viesWhen}` : ""}.
                  {" "}No se puede eximir de IVA: la factura saldrá con IVA.
                </>
              ) : viesView === "desconocido" ? (
                <>
                  <strong>VIES no disponible ahora</strong>
                  {viesWhen ? ` · último intento ${viesWhen}` : ""}.
                  {" "}Se sigue por país + NIF-IVA y se reintentará más tarde.
                  {check?.vies?.error ? (
                    <span className="company-create-vies-detail">Motivo: {check.vies.error}</span>
                  ) : null}
                </>
              ) : (
                <>
                  <strong>VIES: pendiente de validar</strong>
                  {viesWhen ? ` · último intento ${viesWhen}` : ""}.
                  {" "}Aún no hay veredicto; el régimen sigue por país + NIF-IVA.
                </>
              )}
            </p>
            {viesView !== "comprobando" ? (
              <button type="button" className="button small secondary" onClick={revalidate}
                      disabled={submitting}>
                Volver a comprobar
              </button>
            ) : null}
          </div>
        ) : null}

        {/* «Crear también en FACTUSOL» con su consecuencia, junto a lo fiscal. */}
        {canFactusol ? (
          <div className="company-create-factusol">
            <input
              id={`${id}-factusol`}
              type="checkbox" checked={alsoFactusol}
              aria-label="Crear también en FACTUSOL"
              aria-describedby={`${id}-factusol-why`}
              onChange={(e) => setAlsoFactusol(e.target.checked)}
            />
            <div className="company-create-factusol-text">
              <label htmlFor={`${id}-factusol`}><strong>Crear también en FACTUSOL</strong></label>
              <p id={`${id}-factusol-why`} className="company-create-factusol-why">
                Se dará de alta como cliente en el software de facturación.
                Hace falta para poder emitir facturas a esta empresa.
              </p>
              {factusolHint}
            </div>
          </div>
        ) : factusolHint}
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

      {/* Botón desactivado con el motivo escrito debajo, nunca gris sin más. */}
      <div className="company-create-actions">
        {blockedReason ? (
          <p id={`${id}-blocked`} className="company-create-blocked">{blockedReason}</p>
        ) : null}
        <div className="company-create-buttons">
          {onCancel ? (
            <button type="button" className="button secondary" onClick={onCancel} disabled={submitting}>
              Cancelar
            </button>
          ) : null}
          <button
            type="submit" className="button"
            disabled={submitting || !name.trim() || !!blockedReason}
            aria-describedby={blockedReason ? `${id}-blocked` : undefined}
          >
            {submitting ? "Creando…" : "Crear empresa"}
          </button>
        </div>
      </div>
    </form>
  );
}
