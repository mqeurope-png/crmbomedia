"use client";

import { PageHeader } from "../../components/PageHeader";
import { WebFormsList } from "../../components/web-forms/WebFormsList";

export default function AdminFormsPage() {
  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Formularios web"
        eyebrow="Admin"
        description="Genera formularios para embeber en cualquier web y capturar leads en BoHub."
        crumbs={[{ label: "Admin" }, { label: "Formularios" }]}
      />
      <WebFormsList />
    </main>
  );
}
