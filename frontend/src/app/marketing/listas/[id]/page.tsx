"use client";

/**
 * Sprint Filtros & Listas (Deuda #2 — post PR-Eb). El detalle de una
 * lista Brevo se rehace como **redirección pura** a `/contacts` con el
 * filtro `in_brevo_list IN [list_id]` pre-aplicado.
 *
 * Antes esta pantalla tenía su propia paginación + render de contactos
 * + acciones inline — duplicaba la UI de la lista de contactos sin
 * ninguno de los beneficios (bulk actions, sort, columnas
 * configurables, vistas guardadas). Tras la migración de
 * `/contacts` a `<EntityTable>` (PR-E), la fuente única de verdad
 * para listar contactos es esa pantalla. Aquí solo necesitamos
 * empujarlos allí con el filtro listo.
 *
 * Renombrar / Borrar la lista Brevo se mueven al index
 * (`/marketing/listas/page.tsx`) como kebab por fila — son acciones
 * raras que no merecen una pantalla propia.
 *
 * Si la cuenta Brevo no está configurada, mostramos el error sin
 * redirigir (no tiene sentido mandar al usuario a /contacts con un
 * filtro vacío).
 */
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorState } from "../../../components/ErrorState";
import { PageHeader } from "../../../components/PageHeader";
import { extractErrorMessage } from "../../../lib/errors";

export default function MarketingListDetailRedirect() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const listId = Number(params.id);
    if (!Number.isFinite(listId) || listId <= 0) {
      setError("Id de lista inválido.");
      return;
    }
    try {
      // Mismo formato que `contacts/page.tsx::serializeUrlState`:
      // `rules` = btoa(encodeURIComponent(JSON.stringify(tree))).
      const tree = {
        type: "rule",
        field: "in_brevo_list",
        comparator: "in",
        value: [String(listId)],
      };
      const encoded = btoa(encodeURIComponent(JSON.stringify(tree)));
      router.replace(`/contacts?rules=${encoded}`);
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo redirigir a la lista de contactos."),
      );
    }
  }, [params.id, router]);

  if (error) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Lista Brevo" eyebrow="Marketing" />
        <ErrorState title="Error" message={error} />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <PageHeader title="Abriendo lista…" eyebrow="Marketing" />
      <p className="muted">Redirigiendo a contactos con el filtro aplicado.</p>
    </main>
  );
}
