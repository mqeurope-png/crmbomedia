"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
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
      <Link href="/" className="back-link">← Volver al dashboard</Link>
      <h1>Crear contacto</h1>
      {isLoading ? <p className="muted">Cargando empresas...</p> : null}
      {error ? <ErrorState title="No se pudo cargar el formulario" message={error} /> : null}
      {!isLoading && !error ? <CreateContactForm companies={companies} /> : null}
    </main>
  );
}
