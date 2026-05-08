"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { deactivateContact, updateContact, type Contact } from "../../lib/api";

export function ContactEditForm({ contact }: Readonly<{ contact: Contact }>) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    const form = new FormData(event.currentTarget);
    try {
      await updateContact(contact.id, {
        first_name: form.get("first_name"),
        last_name: form.get("last_name") || null,
        email: form.get("email"),
        phone: form.get("phone") || null,
        commercial_status: form.get("commercial_status") || "new",
        marketing_consent: form.get("marketing_consent") || "unknown",
      });
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo actualizar el contacto");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function onDeactivate() {
    setError(null);
    try {
      await deactivateContact(contact.id);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo desactivar el contacto");
    }
  }

  return (
    <form className="form-card" onSubmit={onSubmit}>
      {error ? <div className="error-state">{error}</div> : null}
      <label>
        Nombre
        <input name="first_name" defaultValue={contact.first_name} required maxLength={120} />
      </label>
      <label>
        Apellidos
        <input name="last_name" defaultValue={contact.last_name ?? ""} maxLength={160} />
      </label>
      <label>
        Email
        <input name="email" type="email" defaultValue={contact.email} required />
      </label>
      <label>
        Teléfono
        <input name="phone" defaultValue={contact.phone ?? ""} maxLength={80} />
      </label>
      <label>
        Estado comercial
        <input name="commercial_status" defaultValue={contact.commercial_status} maxLength={80} />
      </label>
      <label>
        Consentimiento marketing
        <select name="marketing_consent" defaultValue={contact.marketing_consent}>
          <option value="unknown">Desconocido</option>
          <option value="granted">Concedido</option>
          <option value="denied">Denegado</option>
          <option value="unsubscribed">Baja</option>
        </select>
      </label>
      <div className="actions inline-actions">
        <button className="button" type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Guardando..." : "Guardar cambios"}
        </button>
        <button className="button danger" type="button" onClick={onDeactivate}>
          Desactivar
        </button>
      </div>
    </form>
  );
}
