"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { getContact, type Contact } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import { ContactEditForm } from "./ContactEditForm";

export default function ContactDetailPage() {
  const params = useParams<{ id: string }>();
  const [contact, setContact] = useState<Contact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    getContact(params.id)
      .then(setContact)
      .catch((err) => setError(extractErrorMessage(err, "Comprueba el backend.")))
      .finally(() => setIsLoading(false));
  }, [params.id]);

  if (isLoading) {
    return <main className="shell"><p className="muted">Cargando contacto...</p></main>;
  }

  if (error || !contact) {
    return (
      <main className="shell narrow">
        <Link href="/" className="back-link">← Volver al dashboard</Link>
        <ErrorState title="No se pudo cargar el contacto" message={error ?? "Contacto no encontrado"} />
      </main>
    );
  }

  const fullName = [contact.first_name, contact.last_name].filter(Boolean).join(" ");

  return (
    <main className="shell">
      <Link href="/" className="back-link">← Volver al dashboard</Link>
      <section className="detail-header">
        <div>
          <p className="eyebrow">Ficha de contacto</p>
          <h1>{fullName}</h1>
          <p className="lead">{contact.email}</p>
        </div>
        <span className={`status status-${contact.marketing_consent}`}>
          {contact.marketing_consent}
        </span>
      </section>

      <section className="grid two">
        <article className="card">
          <h2>Editar contacto</h2>
          <ContactEditForm contact={contact} />
        </article>
        <article className="card">
          <h2>Datos CRM</h2>
          <dl className="definition-list">
            <dt>Teléfono</dt><dd>{contact.phone ?? "—"}</dd>
            <dt>Origen</dt><dd>{contact.origin ?? "—"}</dd>
            <dt>Estado comercial</dt><dd>{contact.commercial_status}</dd>
            <dt>Tags</dt><dd>{contact.tags || "—"}</dd>
            <dt>Activo</dt><dd>{contact.is_active ? "Sí" : "No"}</dd>
          </dl>
        </article>
        <article className="card">
          <h2>Notas</h2>
          {contact.notes?.length ? (
            <ul className="item-list">
              {contact.notes.map((note) => <li key={note.id}>{note.body}</li>)}
            </ul>
          ) : <p className="muted">Sin notas todavía.</p>}
        </article>
        <article className="card">
          <h2>Tareas</h2>
          {contact.tasks?.length ? (
            <ul className="item-list">
              {contact.tasks.map((task) => <li key={task.id}>{task.title} · {task.status}</li>)}
            </ul>
          ) : <p className="muted">Sin tareas pendientes.</p>}
        </article>
      </section>
    </main>
  );
}
