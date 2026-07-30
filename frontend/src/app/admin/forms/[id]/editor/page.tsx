"use client";

import { useParams } from "next/navigation";
import { PageHeader } from "../../../../components/PageHeader";
import { WebFormEditor } from "../../../../components/web-forms/WebFormEditor";

export default function FormEditorPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  return (
    <main className="shell shell-wide">
      <PageHeader
        title={id === "new" ? "Nuevo formulario" : "Editar formulario"}
        eyebrow="Formularios"
        crumbs={[
          { label: "Formularios", href: "/admin/forms" },
          { label: "Editor" },
        ]}
      />
      <WebFormEditor formId={id} />
    </main>
  );
}
