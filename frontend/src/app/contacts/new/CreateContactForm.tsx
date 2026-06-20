"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createContact, type Company } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

export function CreateContactForm({ companies }: Readonly<{ companies: Company[] }>) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

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
      company_id: form.get("company_id") || null,
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

  return (
    <form className="form-card" onSubmit={onSubmit}>
      {error ? <div className="error-state">{error}</div> : null}
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
      <label>
        Empresa
        <select name="company_id" defaultValue="">
          <option value="">Sin empresa</option>
          {companies.map((company) => (
            <option key={company.id} value={company.id}>{company.name}</option>
          ))}
        </select>
      </label>
      <label>
        Consentimiento marketing
        <select name="marketing_consent" defaultValue="unknown">
          <option value="unknown">Desconocido</option>
          <option value="granted">Concedido</option>
          <option value="denied">Denegado</option>
          <option value="unsubscribed">Baja</option>
        </select>
      </label>
      <button className="button" type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Creando..." : "Crear contacto"}
      </button>
    </form>
  );
}
