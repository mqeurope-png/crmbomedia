"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { CompanyCreateForm, type CompanyCreated } from "../../components/CompanyCreateForm";

/** Pantalla «Crear empresa» (rediseño de flujo, Fase 2). Antes `/companies/new`
 *  no existía (caía en la ruta `[id]`) y la única alta era el modal rápido
 *  con nombre + dominio. Acepta `?name=` para llegar prerrellenada desde el
 *  buscador («＋ Crear empresa nueva «…»»).
 *
 *  Si todo va bien lleva a la ficha nueva. Si la empresa se creó pero el alta
 *  en FACTUSOL falló, se queda aquí diciéndolo (la empresa NO se pierde: se
 *  vincula después desde su ficha). */
function NewCompanyScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const initialName = params.get("name") ?? "";
  const [partial, setPartial] = useState<CompanyCreated | null>(null);

  function onCreated(result: CompanyCreated) {
    if (result.factusolError) {
      setPartial(result);
      return;
    }
    router.push(`/companies/${result.company.id}`);
  }

  return (
    <main className="shell erp-flow">
      <PageHeader
        title="Crear empresa"
        eyebrow="Empresas"
        description="Los datos fiscales mandan: de aquí sale todo (pedidos, albaranes, facturas, IVA)."
        crumbs={[{ label: "Empresas", href: "/companies" }, { label: "Nueva" }]}
      />
      <div className="company-create-wrap">
        {partial ? (
          <section className="erp-flow-panel" aria-label="Resultado del alta">
            <h3>Empresa creada</h3>
            <p>
              <strong>{partial.company.name}</strong> está en el CRM, pero no se pudo
              dar de alta en FACTUSOL:
            </p>
            <p className="form-error" role="alert">{partial.factusolError}</p>
            <p className="muted small">
              Puedes crear o vincular el cliente FACTUSOL desde la ficha de la empresa
              cuando FACTUSOL responda.
            </p>
            <div className="form-actions">
              <Link href={`/companies/${partial.company.id}`} className="button">
                Abrir la ficha de «{partial.company.name}»
              </Link>
              <button type="button" className="button secondary" onClick={() => setPartial(null)}>
                Crear otra
              </button>
            </div>
          </section>
        ) : (
          <CompanyCreateForm
            initialName={initialName}
            onCreated={onCreated}
            onCancel={() => router.push("/companies")}
          />
        )}
      </div>
    </main>
  );
}

export default function NewCompanyPage() {
  // `useSearchParams` exige Suspense en el app router.
  return (
    <Suspense fallback={<main className="shell"><p className="muted">Cargando…</p></main>}>
      <NewCompanyScreen />
    </Suspense>
  );
}
