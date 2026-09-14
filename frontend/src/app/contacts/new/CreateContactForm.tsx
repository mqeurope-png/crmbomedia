"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createContact } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import { CompanyCreateForm, type CompanyCreated } from "../../components/CompanyCreateForm";
import { CompanySearch } from "../../components/CompanySearch";

type Picked = { id: string; name: string; factusol_company_id: string | null };

/** Alta de contacto. Rediseño de flujo (Fase 2): la empresa se elige con el
 *  buscador unificado (nombre / CIF / NIF-IVA / dominio, con su estado
 *  FACTUSOL) en vez del desplegable con TODAS las empresas; si no existe se
 *  crea aquí mismo con el formulario de «Crear empresa» y queda elegida. El
 *  resto del alta (origen Manual, responsable = quien crea, consentimiento)
 *  no cambia. */
export function CreateContactForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [company, setCompany] = useState<Picked | null>(null);
  // Texto con el que se pidió «Crear empresa nueva «…»»: abre el formulario
  // embebido; null = buscador.
  const [creating, setCreating] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    const form = new FormData(event.currentTarget);
    // PR-Fix-Creación-Manual-Contacto. El campo "Origen" ya no se
    // envía desde el formulario manual — el backend lo fija a
    // "Manual" automáticamente. Mantenerlo aquí permitiría payloads
    // legacy contradecir esa decisión.
    const payload = {
      first_name: form.get("first_name"),
      last_name: form.get("last_name") || null,
      email: form.get("email"),
      phone: form.get("phone") || null,
      marketing_consent: form.get("marketing_consent") || "unknown",
      company_id: company?.id ?? null,
    };

    try {
      const contact = await createContact(payload);
      router.push(`/contacts/${contact.id}`);
      router.refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el contacto"));
    } finally {
      setIsSubmitting(false);
    }
  }

  function onCompanyCreated({ company: fresh, factusol, factusolError }: CompanyCreated) {
    setCompany({
      id: fresh.id, name: fresh.name,
      factusol_company_id: factusol?.codcli ?? fresh.factusol_company_id ?? null,
    });
    setCreating(null);
    setNotice(factusolError
      ? `Empresa «${fresh.name}» creada (sin alta en FACTUSOL: ${factusolError}).`
      : `Empresa «${fresh.name}» creada y elegida.`);
  }

  return (
    <form className="form-card" onSubmit={onSubmit}>
      {error ? <div className="error-state">{error}</div> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}
      <label>
        Nombre
        <input name="first_name" required maxLength={120} />
      </label>
      <label>
        Apellidos
        <input name="last_name" maxLength={160} />
      </label>
      <label>
        Email
        <input name="email" type="email" required />
      </label>
      <label>
        Teléfono
        <input name="phone" maxLength={80} />
      </label>
      {/*
        PR-Fix-Creación-Manual-Contacto. Se eliminó el input de
        "Origen" — todo contacto creado desde aquí queda con
        origin="Manual" automáticamente. Si el comercial quiere
        documentar cómo llegó el lead (teléfono, evento…), debe
        usar Notas o un custom field, no el campo Origen.
      */}
      <p className="form-note">
        Origen: <strong>Manual</strong> · Se asigna automáticamente a ti como
        responsable. Para anotar cómo llegó el lead usa el campo de notas.
      </p>

      {/* Empresa: buscador unificado (adiós al desplegable gigante). */}
      <div className="company-field" aria-label="Empresa del contacto">
        {company ? (
          <div className="company-field-picked">
            <span className="field"><span>Empresa</span></span>
            <p>
              <strong>{company.name}</strong>{" "}
              {company.factusol_company_id ? (
                <span className="badge ok">en FACTUSOL nº {company.factusol_company_id}</span>
              ) : (
                <span className="badge muted">solo CRM</span>
              )}
            </p>
            <div className="form-actions">
              <button type="button" className="button small secondary"
                      onClick={() => setCompany(null)}>
                Cambiar
              </button>
              <button type="button" className="button small secondary"
                      onClick={() => setCompany(null)}>
                Sin empresa
              </button>
            </div>
          </div>
        ) : creating !== null ? (
          <div className="company-field-create">
            <p className="field"><span>Nueva empresa</span></p>
            <CompanyCreateForm
              compact
              initialName={creating}
              onCreated={onCompanyCreated}
              onCancel={() => setCreating(null)}
              onUseExisting={(c) => {
                setCompany({ id: c.id, name: c.name, factusol_company_id: null });
                setCreating(null);
              }}
            />
          </div>
        ) : (
          <CompanySearch
            onPick={(c) => setCompany({
              id: c.id, name: c.name, factusol_company_id: c.factusol_company_id,
            })}
            onCreate={(name) => setCreating(name)}
          />
        )}
      </div>

      <label>
        Consentimiento marketing
        <select name="marketing_consent" defaultValue="unknown">
          <option value="unknown">Desconocido</option>
          <option value="granted">Concedido</option>
          <option value="denied">Denegado</option>
          <option value="unsubscribed">Baja</option>
        </select>
      </label>
      <button className="button" type="submit" disabled={isSubmitting || creating !== null}>
        {isSubmitting ? "Creando..." : "Crear contacto"}
      </button>
    </form>
  );
}
