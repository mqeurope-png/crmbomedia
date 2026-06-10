"use client";

import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { getCompanies, type Company } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import { CreateContactForm } from "./CreateContactForm";

export default function NewContactPage() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    getCompanies()
      .then(setCompanies)
      .catch((err) => setError(extractErrorMessage(err, "Comprueba el backend.")))
      .finally(() => setIsLoading(false));
  }, []);

  return (
    <main className="shell narrow">
      <PageHeader
        title="Crear contacto"
        eyebrow="Contactos"
        crumbs={[{ label: "Contactos", href: "/contacts" }, { label: "Nuevo" }]}
      />
      {isLoading ? <p className="muted">Cargando empresas...</p> : null}
      {error ? <ErrorState title="No se pudo cargar el formulario" message={error} /> : null}
      {!isLoading && !error ? <CreateContactForm companies={companies} /> : null}
    </main>
  );
}
