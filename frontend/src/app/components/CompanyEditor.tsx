"use client";

import { useState } from "react";
import { updateCompany, type Company } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

export function CompanyEditor({ company }: Readonly<{ company: Company }>) {
  const [name, setName] = useState(company.name);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function onSave() {
    setError(null);
    setSaved(false);
    try {
      const updated = await updateCompany(company.id, { name });
      setName(updated.name);
      setSaved(true);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo actualizar la empresa"));
    }
  }

  return (
    <div className="company-editor">
      <input value={name} onChange={(event) => setName(event.target.value)} aria-label="Nombre empresa" />
      <button className="button secondary small" type="button" onClick={onSave}>Guardar</button>
      {saved ? <span className="success-text">Guardado</span> : null}
      {error ? <span className="danger-text">{error}</span> : null}
    </div>
  );
}
