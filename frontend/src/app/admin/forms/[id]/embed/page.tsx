"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PageHeader } from "../../../../components/PageHeader";
import { WebFormEmbedCode } from "../../../../components/web-forms/WebFormEmbedCode";
import { extractErrorMessage } from "../../../../lib/errors";
import { getEmbedCode, type EmbedCode } from "../../../../lib/formsApi";

export default function FormEmbedPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [embed, setEmbed] = useState<EmbedCode | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getEmbedCode(id)
      .then(setEmbed)
      .catch((err) => setError(extractErrorMessage(err, "No se pudo cargar el embed.")));
  }, [id]);

  return (
    <main className="shell">
      <PageHeader
        title="Código de embed"
        eyebrow="Formularios"
        description="Copia uno de estos snippets en la web donde quieras el formulario."
        crumbs={[
          { label: "Formularios", href: "/admin/forms" },
          { label: "Embed" },
        ]}
        actions={
          <Link href={`/admin/forms/${id}/editor`} className="button secondary small">
            Editar formulario
          </Link>
        }
      />
      {error ? <p className="form-error">{error}</p> : null}
      {embed ? <WebFormEmbedCode embed={embed} /> : !error ? <p className="muted">Cargando…</p> : null}
    </main>
  );
}
