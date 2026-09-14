"use client";

import { PageHeader } from "../../components/PageHeader";
import { CreateContactForm } from "./CreateContactForm";

/** Alta de contacto. Rediseño de flujo (Fase 2): ya no precarga TODAS las
 *  empresas para un desplegable; la empresa se busca en vivo desde el
 *  formulario. */
export default function NewContactPage() {
  return (
    <main className="shell narrow">
      <PageHeader
        title="Crear contacto"
        eyebrow="Contactos"
        description="El buscador de empresa es el mismo aquí y en la ficha."
        crumbs={[{ label: "Contactos", href: "/contacts" }, { label: "Nuevo" }]}
      />
      <CreateContactForm />
    </main>
  );
}
