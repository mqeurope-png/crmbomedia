"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import {
  getContact,
  type Contact,
  type ExternalReference,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import { ContactEditForm } from "./ContactEditForm";

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatAddress(contact: Contact): string | null {
  const parts = [
    contact.address_city,
    contact.address_state,
    contact.address_country_name ?? contact.address_country,
  ].filter((part): part is string => Boolean(part && part.trim()));
  return parts.length ? parts.join(", ") : null;
}

function ownerSummary(metadata: Record<string, unknown> | null | undefined): string | null {
  if (!metadata || typeof metadata !== "object") return null;
  const owner = (metadata as { owner?: unknown }).owner;
  if (!owner || typeof owner !== "object") return null;
  const ownerObj = owner as { name?: unknown; email?: unknown; id?: unknown };
  const label = [ownerObj.name, ownerObj.email]
    .filter((value): value is string => typeof value === "string" && value.trim() !== "")
    .join(" · ");
  if (label) return label;
  return typeof ownerObj.id === "string" ? `ID ${ownerObj.id}` : null;
}

function renderCustomValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

type RowProps = { label: string; value: string | number | null | undefined };

function Row({ label, value }: RowProps) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function ExternalReferenceCard({ reference }: { reference: ExternalReference }) {
  const owner = ownerSummary(reference.metadata);
  return (
    <li className="external-ref">
      <div className="external-ref-header">
        <strong>{reference.system}</strong>
        <span className="muted">{reference.account_id}</span>
      </div>
      <dl className="definition-list">
        <Row label="ID externo" value={reference.external_id} />
        <Row label="Etiqueta" value={reference.account_label} />
        <Row label="Origen" value={reference.origin_detail} />
        <Row label="Propietario remoto" value={owner} />
        <Row
          label="Creado en origen"
          value={reference.external_created_at ? formatDateTime(reference.external_created_at) : null}
        />
        <Row
          label="Actualizado en origen"
          value={reference.external_updated_at ? formatDateTime(reference.external_updated_at) : null}
        />
      </dl>
    </li>
  );
}

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
  const address = formatAddress(contact);
  const customEntries = contact.custom_fields
    ? Object.entries(contact.custom_fields)
    : [];
  const externalRefs = contact.external_refs ?? [];

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
            <Row label="Teléfono" value={contact.phone} />
            <Row label="Origen" value={contact.origin} />
            <Row label="Estado comercial" value={contact.commercial_status} />
            <Row label="Tags" value={contact.tags || null} />
            <Row label="Lead score" value={contact.lead_score} />
            <Row label="Dirección" value={address} />
            <Row label="Activo" value={contact.is_active ? "Sí" : "No"} />
          </dl>
        </article>
        <article className="card">
          <h2>Campos personalizados</h2>
          {customEntries.length ? (
            <dl className="definition-list">
              {customEntries.map(([key, value]) => (
                <Row key={key} label={key} value={renderCustomValue(value)} />
              ))}
            </dl>
          ) : (
            <p className="muted">Sin campos personalizados.</p>
          )}
        </article>
        <article className="card">
          <h2>Referencias externas</h2>
          {externalRefs.length ? (
            <ul className="external-ref-list">
              {externalRefs.map((reference) => (
                <ExternalReferenceCard key={reference.id} reference={reference} />
              ))}
            </ul>
          ) : (
            <p className="muted">Sin referencias externas todavía.</p>
          )}
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
