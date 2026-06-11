"use client";

import { ExternalLink } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { ContactEmailActivity } from "../../components/ContactEmailActivity";
import { ContactPipelinesSection } from "../../components/ContactPipelinesSection";
import { ContactTasksSection } from "../../components/ContactTasksSection";
import { RefreshExternalDataButton } from "../../components/RefreshExternalDataButton";
import {
  addTagToContact,
  getContact,
  removeTagFromContact,
  type ActivityEvent,
  type Contact,
  type ExternalReference,
  type ExternalRefreshResult,
  type Note,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import { TagChips } from "../../components/TagChips";
import { TagPicker } from "../../components/TagPicker";
import { OriginChips } from "../../components/OriginChips";
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

/** "Creado en origen": prefer the real source-system date, falling
 * back to the CRM row's `created_at` with a "del CRM" badge so the
 * operator knows it's not the contact's real age. The CRM date rides
 * along as a tooltip when the external date is shown. */
function CreatedRow({ contact }: { contact: Contact }) {
  if (contact.created_at_external) {
    return (
      <>
        <dt>Creado en origen</dt>
        <dd title={`En el CRM: ${formatDateTime(contact.created_at)}`}>
          {formatDateTime(contact.created_at_external)}
        </dd>
      </>
    );
  }
  if (!contact.created_at) return null;
  return (
    <>
      <dt>Creado</dt>
      <dd>
        {formatDateTime(contact.created_at)}{" "}
        <span className="badge muted">del CRM</span>
      </dd>
    </>
  );
}

// Soft mapping of AgileCRM activity_type → emoji marker. New types just
// fall back to the default bullet so we never crash on an unknown event.
const EVENT_TYPE_ICON: Record<string, string> = {
  EMAIL_SENT: "✉️",
  EMAIL_OPENED: "👁️",
  EMAIL_CLICKED: "🔗",
  CALL_LOG: "📞",
  NOTE: "🗒️",
  FORM_FILL: "📝",
  DEAL_CREATED: "💼",
  PAGE_VIEWED: "🌐",
  TASK_COMPLETED: "✅",
};

function eventIcon(eventType: string): string {
  return EVENT_TYPE_ICON[eventType] ?? "•";
}

function NoteCard({ note }: { note: Note }) {
  // Prefer the AgileCRM author name; fall back to the email when the
  // remote omits the name; only show "Sistema" when both are missing
  // (manual notes created from the CRM UI). Tooltip surfaces the
  // email so an operator can hover to disambiguate two authors with
  // the same display name.
  const author =
    note.external_author_name ||
    note.external_author_email ||
    "Sistema";
  const tooltip = note.external_author_email ?? undefined;
  const date = note.external_created_at ?? note.created_at;
  return (
    <li className="note-card">
      <div className="note-card-header">
        <strong title={tooltip}>{author}</strong>
        <span className="muted">{formatDateTime(date)}</span>
      </div>
      <p className="note-body">{note.body}</p>
    </li>
  );
}

function ActivityEventRow({ event }: { event: ActivityEvent }) {
  return (
    <li className="timeline-row">
      <span className="timeline-icon" aria-hidden>
        {eventIcon(event.event_type)}
      </span>
      <div className="timeline-content">
        <div className="timeline-meta">
          <strong>{event.subject || event.event_type}</strong>
          <span className="muted">{formatDateTime(event.occurred_at)}</span>
        </div>
        <span className="timeline-type">{event.event_type}</span>
        {event.body ? <p className="timeline-body">{event.body}</p> : null}
      </div>
    </li>
  );
}

function ExternalReferenceCard({ reference }: { reference: ExternalReference }) {
  const owner = ownerSummary(reference.metadata);
  return (
    <li className="external-ref">
      <div className="external-ref-header">
        <strong>{reference.system_label ?? reference.system}</strong>
        <span className="muted">{reference.account_label ?? reference.account_id}</span>
        {reference.external_url ? (
          <a
            href={reference.external_url}
            target="_blank"
            rel="noopener noreferrer"
            className="external-ref-link"
            title="Abrir en el sistema de origen"
          >
            <ExternalLink size={13} aria-hidden /> Abrir
          </a>
        ) : null}
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
  const [refreshWarnings, setRefreshWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  // Single-shot guard so we never re-trigger the auto-refresh when the
  // component re-renders after the freshness flips back to `fresh`.
  const autoRefreshed = useRef(false);

  const loadContact = useCallback(async () => {
    const fresh = await getContact(params.id);
    setContact(fresh);
    return fresh;
  }, [params.id]);

  useEffect(() => {
    loadContact()
      .catch((err) => setError(extractErrorMessage(err, "Comprueba el backend.")))
      .finally(() => setIsLoading(false));
  }, [loadContact]);

  const handleRefreshDone = useCallback(
    (result: ExternalRefreshResult) => {
      setRefreshWarnings(result.warnings);
      // Re-fetch so notes / tasks / timeline render the newly synced
      // rows alongside the updated freshness banner.
      loadContact().catch((err) =>
        setError(extractErrorMessage(err, "Comprueba el backend.")),
      );
    },
    [loadContact],
  );

  useEffect(() => {
    // Auto-refresh ONLY for `outdated` (or never refreshed). `stale`
    // gives the operator a chance to opt in manually so we don't burn
    // quota on background loads of contacts they were just glancing
    // at.
    if (!contact || autoRefreshed.current) return;
    if (contact.external_data_freshness !== "outdated") return;
    autoRefreshed.current = true;
    import("../../lib/api").then(({ refreshContactExternalData }) => {
      refreshContactExternalData(contact.id)
        .then(handleRefreshDone)
        .catch(() => {
          // The visible button still works; we swallow the auto-attempt
          // error so a transient network glitch doesn't render a red
          // banner.
        });
    });
  }, [contact, handleRefreshDone]);

  if (isLoading) {
    return <main className="shell"><p className="muted">Cargando contacto...</p></main>;
  }

  if (error || !contact) {
    return (
      <main className="shell narrow">
        <PageHeader
          title="Contacto"
          eyebrow="Ficha"
          crumbs={[{ label: "Contactos", href: "/contacts" }]}
        />
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
      <PageHeader
        title={fullName || "(Sin nombre)"}
        eyebrow="Ficha de contacto"
        description={contact.email ?? undefined}
        crumbs={[
          { label: "Contactos", href: "/contacts" },
          { label: fullName || "(Sin nombre)" },
        ]}
        actions={
          <span className={`status status-${contact.marketing_consent}`}>
            {contact.marketing_consent}
          </span>
        }
      />

      <section className="grid two">
        <article className="card">
          <h2>Editar contacto</h2>
          <ContactEditForm contact={contact} />
        </article>
        <article className="card">
          <h2>Datos CRM</h2>
          <dl className="definition-list">
            <Row label="Teléfono" value={contact.phone} />
            <CreatedRow contact={contact} />
            <dt>Origen</dt>
            <dd>
              <OriginChips references={externalRefs} />
            </dd>
            <Row label="Estado comercial" value={contact.commercial_status} />
            <Row label="Lead score" value={contact.lead_score} />
            <Row label="Dirección" value={address} />
            <Row label="Activo" value={contact.is_active ? "Sí" : "No"} />
          </dl>
          <div className="tag-section">
            <h3>Tags</h3>
            <TagChips
              tags={contact.tag_objects ?? []}
              onRemove={async (tagId) => {
                try {
                  await removeTagFromContact(contact.id, tagId);
                  await loadContact();
                } catch (err) {
                  setError(extractErrorMessage(err, "No se pudo quitar el tag."));
                }
              }}
            />
            <TagPicker
              excludeTagIds={(contact.tag_objects ?? []).map((t) => t.id)}
              onPick={async (choice) => {
                try {
                  await addTagToContact(contact.id, choice);
                  await loadContact();
                } catch (err) {
                  setError(extractErrorMessage(err, "No se pudo añadir el tag."));
                }
              }}
            />
          </div>
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
            <ul className="note-list">
              {contact.notes.map((note) => (
                <NoteCard key={note.id} note={note} />
              ))}
            </ul>
          ) : <p className="muted">Sin notas todavía.</p>}
        </article>
        <ContactTasksSection contactId={contact.id} />
        <ContactPipelinesSection contactId={contact.id} />
        <ContactEmailActivity contactId={contact.id} />
        <article className="card card-wide">
          <div className="section-title">
            <h2>Línea de tiempo</h2>
            {(contact.activity_events?.length ?? 0) > 0 ? (
              <span className="muted small">
                {contact.activity_events?.length} eventos recientes
              </span>
            ) : null}
          </div>
          <FreshnessBanner
            freshness={contact.external_data_freshness ?? "outdated"}
            refreshedAt={contact.last_external_refresh_at}
            warnings={refreshWarnings}
          />
          <div className="freshness-actions">
            <RefreshExternalDataButton
              contactId={contact.id}
              onDone={handleRefreshDone}
            />
          </div>
          {contact.activity_events?.length ? (
            <ul className="timeline-list">
              {contact.activity_events.map((event) => (
                <ActivityEventRow key={event.id} event={event} />
              ))}
            </ul>
          ) : contact.last_external_refresh_at ? (
            // We've already asked AgileCRM and the timeline is genuinely
            // empty for this contact — don't suggest the operator
            // pulls again, that would just waste quota.
            <p className="muted">
              Sin eventos en AgileCRM para este contacto. Última
              actualización: {formatDateTime(contact.last_external_refresh_at)}.
            </p>
          ) : (
            <p className="muted">
              Sin eventos sincronizados todavía. Pulsa &quot;Actualizar desde
              AgileCRM&quot; para traer notas, tareas y eventos del contacto.
            </p>
          )}
        </article>
      </section>
    </main>
  );
}

function FreshnessBanner({
  freshness,
  refreshedAt,
  warnings,
}: {
  freshness: "fresh" | "stale" | "outdated";
  refreshedAt: string | null | undefined;
  warnings: string[];
}) {
  const refreshedLabel = refreshedAt ? formatDateTime(refreshedAt) : "nunca";
  let bannerText: string;
  if (freshness === "fresh") {
    bannerText = `Datos al día · actualizados ${refreshedLabel}`;
  } else if (freshness === "stale") {
    bannerText = `Última actualización: ${refreshedLabel}`;
  } else {
    bannerText = refreshedAt
      ? `Datos no actualizados desde AgileCRM (última: ${refreshedLabel})`
      : "Datos no actualizados desde AgileCRM";
  }
  return (
    <div className={`freshness freshness-${freshness}`} role="status">
      <span>{bannerText}</span>
      {warnings.length ? (
        <ul className="freshness-warnings">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
