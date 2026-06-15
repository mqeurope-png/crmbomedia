"use client";

import {
  Activity as ActivityIcon,
  CheckSquare,
  Kanban,
  Mail,
  Phone as PhoneIcon,
  StickyNote,
  Trash2,
} from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ContactAddressSection } from "../../components/ContactAddressSection";
import { ContactCompanySection } from "../../components/ContactCompanySection";
import { ContactCustomFieldsSection } from "../../components/ContactCustomFieldsSection";
import { ContactEmailsSection } from "../../components/ContactEmailsSection";
import { ContactNotesSection } from "../../components/ContactNotesSection";
import { ContactPhonesSection } from "../../components/ContactPhonesSection";
import { ContactProfessionalSection } from "../../components/ContactProfessionalSection";
import { EmailEventBadges } from "../../components/email/EmailEventBadges";
import { ContactPipelinesSection } from "../../components/ContactPipelinesSection";
import { ContactTasksSection } from "../../components/ContactTasksSection";
import { EditableField } from "../../components/EditableField";
import { EmailComposerModal } from "../../components/EmailComposerModal";
import { ErrorState } from "../../components/ErrorState";
import { OriginChips } from "../../components/OriginChips";
import { PageHeader } from "../../components/PageHeader";
import { RefreshExternalDataButton } from "../../components/RefreshExternalDataButton";
import { TagChips } from "../../components/TagChips";
import { TagPicker } from "../../components/TagPicker";
import {
  getMessageEvents,
  type EmailEvent,
} from "../../lib/emailTrackingApi";
import {
  addTagToContact,
  deactivateContact,
  getContact,
  removeTagFromContact,
  updateContact,
  type ActivityEvent,
  type Contact,
  type ExternalRefreshResult,
  type Note,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

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

const COMMERCIAL_OPTIONS: ReadonlyArray<[string, string]> = [
  ["new", "Nuevo"],
  ["qualified", "Calificado"],
  ["working", "Trabajando"],
  ["won", "Ganado"],
  ["lost", "Perdido"],
];

const CONSENT_OPTIONS: ReadonlyArray<[string, string]> = [
  ["unknown", "Sin definir"],
  ["granted", "Otorgado"],
  ["denied", "Denegado"],
  ["unsubscribed", "Baja"],
];

type Tab = "activity" | "tasks" | "notes" | "pipelines" | "emails";

const TABS: Array<{ id: Tab; label: string; icon: React.ElementType }> = [
  { id: "activity", label: "Actividad", icon: ActivityIcon },
  { id: "tasks", label: "Tareas", icon: CheckSquare },
  { id: "notes", label: "Notas", icon: StickyNote },
  { id: "pipelines", label: "Pipelines", icon: Kanban },
  { id: "emails", label: "Emails", icon: Mail },
];

export default function ContactDetailPage() {
  const params = useParams<{ id: string }>();
  const [contact, setContact] = useState<Contact | null>(null);
  const [refreshWarnings, setRefreshWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>("activity");
  const [showComposer, setShowComposer] = useState(false);
  const autoRefreshed = useRef(false);

  const loadContact = useCallback(async () => {
    const fresh = await getContact(params.id);
    setContact(fresh);
    return fresh;
  }, [params.id]);

  useEffect(() => {
    loadContact()
      .catch((err) =>
        setError(extractErrorMessage(err, "Comprueba el backend.")),
      )
      .finally(() => setIsLoading(false));
  }, [loadContact]);

  const handleRefreshDone = useCallback(
    (result: ExternalRefreshResult) => {
      setRefreshWarnings(result.warnings);
      loadContact().catch((err) =>
        setError(extractErrorMessage(err, "Comprueba el backend.")),
      );
    },
    [loadContact],
  );

  useEffect(() => {
    if (!contact || autoRefreshed.current) return;
    if (contact.external_data_freshness !== "outdated") return;
    autoRefreshed.current = true;
    import("../../lib/api").then(({ refreshContactExternalData }) => {
      refreshContactExternalData(contact.id)
        .then(handleRefreshDone)
        .catch(() => undefined);
    });
  }, [contact, handleRefreshDone]);

  async function patch(payload: Record<string, unknown>): Promise<void> {
    if (!contact) return;
    try {
      await updateContact(contact.id, payload);
      await loadContact();
    } catch (err) {
      throw new Error(
        extractErrorMessage(err, "No se pudo actualizar el contacto."),
      );
    }
  }

  async function handleDeactivate() {
    if (!contact) return;
    if (
      !window.confirm(
        `¿Desactivar el contacto "${contact.first_name}"? Lo oculta del listado.`,
      )
    ) {
      return;
    }
    try {
      await deactivateContact(contact.id);
      await loadContact();
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo desactivar el contacto."),
      );
    }
  }

  if (isLoading) {
    return (
      <main className="shell">
        <p className="muted">Cargando contacto...</p>
      </main>
    );
  }

  if (error || !contact) {
    return (
      <main className="shell narrow">
        <PageHeader
          title="Contacto"
          eyebrow="Ficha"
          crumbs={[{ label: "Contactos", href: "/contacts" }]}
        />
        <ErrorState
          title="No se pudo cargar el contacto"
          message={error ?? "Contacto no encontrado"}
        />
      </main>
    );
  }

  const fullName =
    [contact.first_name, contact.last_name].filter(Boolean).join(" ") ||
    "(Sin nombre)";
  const externalRefs = contact.external_refs ?? [];

  return (
    <main className="shell shell-wide contact-detail">
      <PageHeader
        title={fullName}
        eyebrow="Ficha de contacto"
        crumbs={[
          { label: "Contactos", href: "/contacts" },
          { label: fullName },
        ]}
        actions={
          <>
            <RefreshExternalDataButton
              contactId={contact.id}
              onDone={handleRefreshDone}
            />
            {contact.is_active ? (
              <button
                type="button"
                className="button small danger"
                onClick={handleDeactivate}
                title="Desactivar contacto"
              >
                <Trash2 size={11} aria-hidden /> Desactivar
              </button>
            ) : (
              <span className="badge bad">Inactivo</span>
            )}
          </>
        }
      />

      {refreshWarnings.length > 0 ? (
        <ul className="freshness-warnings">
          {refreshWarnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}

      <div className="contact-detail-grid">
        <aside className="contact-detail-sidebar">
          <section className="contact-card">
            <h3 className="contact-card-title">{fullName}</h3>
            <ul className="contact-card-meta">
              {contact.email ? (
                <li>
                  <Mail size={11} aria-hidden /> {contact.email}
                </li>
              ) : null}
              {contact.phone ? (
                <li>
                  <PhoneIcon size={11} aria-hidden /> {contact.phone}
                </li>
              ) : null}
            </ul>
          </section>

          <section className="contact-card">
            <h4>Estado</h4>
            <EditableField
              label="Comercial"
              kind="select"
              options={COMMERCIAL_OPTIONS}
              display={contact.commercial_status ?? "new"}
              onSave={(v) => patch({ commercial_status: v })}
            />
            <EditableField
              label="Consentimiento"
              kind="select"
              options={CONSENT_OPTIONS}
              display={contact.marketing_consent ?? "unknown"}
              onSave={(v) => patch({ marketing_consent: v })}
            />
            <EditableField
              label="Lead score"
              display={
                contact.lead_score === null || contact.lead_score === undefined
                  ? ""
                  : String(contact.lead_score)
              }
              onSave={(v) =>
                patch({
                  lead_score: v.trim() === "" ? null : Number(v),
                })
              }
            />
            <EditableField
              label="Teléfono"
              display={contact.phone ?? ""}
              onSave={(v) => patch({ phone: v.trim() || null })}
            />
          </section>

          <section className="contact-card">
            <h4>Acciones rápidas</h4>
            <div className="actions">
              <button
                type="button"
                className="button small"
                onClick={() => setShowComposer(true)}
              >
                <Mail size={11} aria-hidden /> Email
              </button>
              <button
                type="button"
                className="button small secondary"
                onClick={() => setActiveTab("tasks")}
              >
                <CheckSquare size={11} aria-hidden /> Tarea
              </button>
            </div>
          </section>

          {externalRefs.length > 0 ? (
            <section className="contact-card">
              <h4>Origen</h4>
              <OriginChips references={externalRefs} />
            </section>
          ) : null}

          <ContactCompanySection
            contactId={contact.id}
            companyId={contact.company_id ?? null}
            onChanged={loadContact}
          />

          <ContactProfessionalSection
            contact={contact}
            onSaved={loadContact}
          />

          <ContactPhonesSection contactId={contact.id} />

          <ContactNotesSection contactId={contact.id} />

          <ContactAddressSection
            contact={contact}
            onSaved={loadContact}
          />

          <ContactCustomFieldsSection contact={contact} />

          <section className="contact-card">
            <h4>Tags</h4>
            <TagChips
              tags={contact.tag_objects ?? []}
              onRemove={async (tagId) => {
                try {
                  await removeTagFromContact(contact.id, tagId);
                  await loadContact();
                } catch (err) {
                  setError(
                    extractErrorMessage(err, "No se pudo quitar el tag."),
                  );
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
                  setError(
                    extractErrorMessage(err, "No se pudo añadir el tag."),
                  );
                }
              }}
            />
          </section>
        </aside>

        <section className="contact-detail-main">
          <nav className="contact-detail-tabs" aria-label="Pestañas">
            {TABS.map((t) => {
              const Icon = t.icon;
              return (
                <button
                  key={t.id}
                  type="button"
                  className={`contact-detail-tab ${
                    activeTab === t.id ? "is-active" : ""
                  }`}
                  onClick={() => setActiveTab(t.id)}
                >
                  <Icon size={12} aria-hidden /> {t.label}
                </button>
              );
            })}
          </nav>

          <div className="contact-detail-tab-body">
            {activeTab === "activity" ? (
              <ActivityTab
                events={contact.activity_events ?? []}
                lastRefresh={contact.last_external_refresh_at}
              />
            ) : null}
            {activeTab === "tasks" ? (
              <ContactTasksSection contactId={contact.id} />
            ) : null}
            {activeTab === "notes" ? (
              <NotesTab notes={contact.notes ?? []} />
            ) : null}
            {activeTab === "pipelines" ? (
              <ContactPipelinesSection contactId={contact.id} />
            ) : null}
            {activeTab === "emails" ? (
              <ContactEmailsSection
                contactId={contact.id}
                contactEmail={contact.email}
                onCompose={() => setShowComposer(true)}
              />
            ) : null}
          </div>
        </section>
      </div>
      {showComposer ? (
        <EmailComposerModal
          contactId={contact.id}
          contactEmail={contact.email}
          onClose={() => setShowComposer(false)}
          onSent={async () => {
            setShowComposer(false);
            await loadContact();
            setActiveTab("emails");
          }}
        />
      ) : null}
    </main>
  );
}

// ---------------------------------------------------------------------------
// Sub-views
// ---------------------------------------------------------------------------

const EVENT_TYPE_ICON: Record<string, string> = {
  EMAIL_SENT: "↗",
  EMAIL_OPENED: "👁",
  EMAIL_CLICKED: "🔗",
  CALL_LOG: "📞",
  NOTE: "🗒️",
  FORM_FILL: "📝",
  DEAL_CREATED: "💼",
  PAGE_VIEWED: "🌐",
  TASK_COMPLETED: "✅",
  "task.created": "📌",
  "task.completed": "✅",
  "task.updated": "📝",
  "task.deleted": "🗑️",
  "email.sent_from_crm": "↗",
  "email.reply_received": "↙",
  "email.thread_marked_read": "👁",
};

function readMetadata(
  meta: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  return meta && typeof meta === "object" ? meta : {};
}

function emailEventClass(type: string): string {
  if (type === "email.sent_from_crm" || type === "EMAIL_SENT") {
    return "timeline-row timeline-row-email-out";
  }
  if (type === "email.reply_received") {
    return "timeline-row timeline-row-email-in";
  }
  if (type === "EMAIL_OPENED" || type === "EMAIL_CLICKED") {
    return "timeline-row timeline-row-email-track";
  }
  return "timeline-row";
}

function ActivityTab({
  events,
  lastRefresh,
}: {
  events: ActivityEvent[];
  lastRefresh: string | null | undefined;
}) {
  // Sprint Email v2.3b — collect outbound-email message ids from the
  // event metadata and pull their tracking events. Bounded by how many
  // emails the contact has been sent; typical: a handful.
  const [eventsByMessage, setEventsByMessage] = useState<
    Record<string, EmailEvent[]>
  >({});
  useEffect(() => {
    const ids = events
      .map((e) => {
        const m = readMetadata(e.metadata);
        return e.event_type === "email.sent_from_crm" &&
          typeof m.message_id === "string"
          ? m.message_id
          : null;
      })
      .filter((x): x is string => Boolean(x));
    if (ids.length === 0) return;
    let cancelled = false;
    Promise.allSettled(ids.map((id) => getMessageEvents(id))).then((rs) => {
      if (cancelled) return;
      const next: Record<string, EmailEvent[]> = {};
      rs.forEach((r, idx) => {
        next[ids[idx]] =
          r.status === "fulfilled" ? r.value.events : [];
      });
      setEventsByMessage(next);
    });
    return () => {
      cancelled = true;
    };
  }, [events]);

  if (events.length === 0) {
    return (
      <p className="muted small">
        {lastRefresh
          ? `Sin eventos sincronizados. Última actualización: ${formatDateTime(
              lastRefresh,
            )}.`
          : "Sin eventos sincronizados. Pulsa \"Actualizar\" para traerlos."}
      </p>
    );
  }
  return (
    <ul className="timeline-list">
      {events.map((event) => {
        const meta = readMetadata(event.metadata);
        const threadId = typeof meta.thread_id === "string" ? meta.thread_id : null;
        const direction =
          typeof meta.direction === "string" ? meta.direction : null;
        const fromEmail =
          typeof meta.from_email === "string" ? meta.from_email : null;
        const to = typeof meta.to === "string" ? meta.to : null;
        const messageId =
          typeof meta.message_id === "string" ? meta.message_id : null;
        const trackingEvents = messageId
          ? eventsByMessage[messageId] ?? []
          : [];
        const snippet =
          typeof meta.snippet === "string" ? meta.snippet : event.body ?? null;
        const isEmail = event.event_type.startsWith("email.");
        const heading = (
          <strong>
            {threadId && isEmail ? (
              <a
                href={`/emails/${threadId}`}
                target="_blank"
                rel="noreferrer"
              >
                {event.subject || event.event_type}
              </a>
            ) : (
              event.subject || event.event_type
            )}
          </strong>
        );
        const subline =
          direction === "outbound" && to
            ? `Enviado a ${to}`
            : direction === "inbound" && fromEmail
              ? `Recibido de ${fromEmail}`
              : null;
        return (
          <li key={event.id} className={emailEventClass(event.event_type)}>
            <span className="timeline-icon" aria-hidden>
              {EVENT_TYPE_ICON[event.event_type] ?? "•"}
            </span>
            <div className="timeline-content">
              <div className="timeline-meta">
                {heading}
                <span className="muted">{formatDateTime(event.occurred_at)}</span>
              </div>
              {subline ? (
                <p className="timeline-subline muted small">{subline}</p>
              ) : (
                <span className="timeline-type">{event.event_type}</span>
              )}
              {snippet ? (
                <p className="timeline-body">&quot;{snippet}&quot;</p>
              ) : null}
              {event.event_type === "email.sent_from_crm" && messageId ? (
                <EmailEventBadges events={trackingEvents} />
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function NotesTab({ notes }: { notes: Note[] }) {
  if (notes.length === 0) {
    return <p className="muted small">Sin notas todavía.</p>;
  }
  return (
    <ul className="note-list">
      {notes.map((note) => {
        const author =
          note.external_author_name || note.external_author_email || "Sistema";
        const date = note.external_created_at ?? note.created_at;
        return (
          <li key={note.id} className="note-card">
            <div className="note-card-header">
              <strong title={note.external_author_email ?? undefined}>
                {author}
              </strong>
              <span className="muted">{formatDateTime(date)}</span>
            </div>
            <p className="note-body">{note.body}</p>
          </li>
        );
      })}
    </ul>
  );
}

