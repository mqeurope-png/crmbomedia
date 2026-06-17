"use client";

import {
  Activity as ActivityIcon,
  Briefcase,
  CheckSquare,
  Layers,
  LifeBuoy,
  Mail,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ContactAddressSection } from "../../components/ContactAddressSection";
import { ContactCompanySection } from "../../components/ContactCompanySection";
import { ContactCustomFieldsSection } from "../../components/ContactCustomFieldsSection";
import { ContactDetailHeader } from "../../components/contact-detail/ContactDetailHeader";
import { ContactKeyDataStrip } from "../../components/contact-detail/ContactKeyDataStrip";
import { ContactSummaryTab } from "../../components/contact-detail/ContactSummaryTab";
import { ContactSupportTab } from "../../components/contact-detail/ContactSupportTab";
import { ContactEmailsSection } from "../../components/ContactEmailsSection";
import { ContactAssignmentsSection } from "../../components/ContactAssignmentsSection";
import { ContactNotesSection } from "../../components/ContactNotesSection";
import { ContactPhonesSection } from "../../components/ContactPhonesSection";
import { ContactProfessionalSection } from "../../components/ContactProfessionalSection";
import { EmailEventBadges } from "../../components/email/EmailEventBadges";
import { ContactPipelinesSection } from "../../components/ContactPipelinesSection";
import { ContactTasksSection } from "../../components/ContactTasksSection";
import { EmailComposerModal } from "../../components/EmailComposerModal";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { RefreshExternalDataButton } from "../../components/RefreshExternalDataButton";
import { TaskModal } from "../../components/TaskModal";
import {
  getMessageEvents,
  type EmailEvent,
} from "../../lib/emailTrackingApi";
import {
  deactivateContact,
  getContact,
  type ActivityEvent,
  type Contact,
  type ExternalRefreshResult,
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

type Tab = "summary" | "activity" | "emails" | "tasks" | "opportunities" | "support";

const TABS: Array<{ id: Tab; label: string; icon: React.ElementType }> = [
  { id: "summary", label: "Resumen", icon: Sparkles },
  { id: "activity", label: "Actividad", icon: ActivityIcon },
  { id: "emails", label: "Emails", icon: Mail },
  { id: "tasks", label: "Tareas", icon: CheckSquare },
  { id: "opportunities", label: "Oportunidades", icon: Layers },
  { id: "support", label: "Soporte", icon: LifeBuoy },
];

export default function ContactDetailPage() {
  const params = useParams<{ id: string }>();
  const [contact, setContact] = useState<Contact | null>(null);
  const [refreshWarnings, setRefreshWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>("summary");
  const [showComposer, setShowComposer] = useState(false);
  const [showTaskModal, setShowTaskModal] = useState(false);
  const [overflowOpen, setOverflowOpen] = useState(false);
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

  // El primary owner sale del bloque assignments; sin endpoint cruzado
  // en este nivel mostramos un placeholder neutro y dejamos el dato
  // real al card "Comerciales asignados" del sidebar.
  const ownerName = null;
  const ownerInitials = null;
  const assignedSince = contact.updated_at ?? contact.created_at ?? null;
  const lastActivityAt =
    contact.activity_events?.[0]?.occurred_at ??
    contact.updated_at_external ??
    contact.updated_at ??
    null;
  const origin = contact.origin ?? null;
  const tags = contact.tag_objects ?? [];

  return (
    <main className="shell shell-wide contact-detail contact-detail-v2">
      <nav className="contact-breadcrumb">
        <Link href="/contacts" className="muted small">
          Contactos
        </Link>
        <span className="muted small"> · </span>
        <span className="muted small">
          {[contact.first_name, contact.last_name].filter(Boolean).join(" ") ||
            "(Sin nombre)"}
        </span>
      </nav>

      <ContactDetailHeader
        contact={contact}
        ownerName={ownerName}
        ownerInitials={ownerInitials}
        assignedSince={assignedSince}
        onSendEmail={() => setShowComposer(true)}
        onCreateTask={() => setShowTaskModal(true)}
        onLogCall={() => setShowTaskModal(true)}
        onEdit={() => {
          // Scrolla al card de info, donde el operador puede editar los
          // campos inline. Sin modal de edición global todavía.
          const target = document.getElementById("sidebar-info");
          target?.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
        onOpenOverflow={() => setOverflowOpen((v) => !v)}
        overflowOpen={overflowOpen}
        overflowChildren={
          <>
            <RefreshExternalDataButton
              contactId={contact.id}
              onDone={handleRefreshDone}
            />
            {contact.is_active ? (
              <button
                type="button"
                className="contact-header-overflow-item is-danger"
                onClick={() => {
                  setOverflowOpen(false);
                  handleDeactivate();
                }}
              >
                Desactivar contacto
              </button>
            ) : (
              <span className="badge bad">Inactivo</span>
            )}
          </>
        }
      />

      <ContactKeyDataStrip
        contact={contact}
        tags={tags}
        origin={origin}
        lastActivityAt={lastActivityAt}
      />

      {refreshWarnings.length > 0 ? (
        <ul className="freshness-warnings">
          {refreshWarnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}

      <div className="contact-detail-grid contact-detail-grid-v2">
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
                  <Icon size={14} aria-hidden /> {t.label}
                </button>
              );
            })}
          </nav>

          <div className="contact-detail-tab-body">
            {activeTab === "summary" ? (
              <ContactSummaryTab
                events={contact.activity_events ?? []}
                onSeeAllActivity={() => setActiveTab("activity")}
              />
            ) : null}
            {activeTab === "activity" ? (
              <ActivityTab
                events={contact.activity_events ?? []}
                lastRefresh={contact.last_external_refresh_at}
              />
            ) : null}
            {activeTab === "tasks" ? (
              <ContactTasksSection contactId={contact.id} />
            ) : null}
            {activeTab === "opportunities" ? (
              <ContactPipelinesSection contactId={contact.id} />
            ) : null}
            {activeTab === "emails" ? (
              <ContactEmailsSection
                contactId={contact.id}
                contactEmail={contact.email}
                onCompose={() => setShowComposer(true)}
              />
            ) : null}
            {activeTab === "support" ? <ContactSupportTab /> : null}
          </div>
        </section>

        <aside className="contact-detail-sidebar contact-detail-sidebar-v2">
          <div
            id="sidebar-info"
            className="contact-card contact-sidebar-card contact-sidebar-info"
          >
            <header className="contact-sidebar-card-header">
              <Briefcase size={14} aria-hidden />
              <h3>Información de contacto</h3>
            </header>
            <ContactPhonesSection contactId={contact.id} />
            <ContactProfessionalSection
              contact={contact}
              onSaved={loadContact}
            />
            <ContactAddressSection contact={contact} onSaved={loadContact} />
            <ContactCustomFieldsSection contact={contact} />
          </div>

          <div id="sidebar-company">
            <ContactCompanySection
              contactId={contact.id}
              companyId={contact.company_id ?? null}
              onChanged={loadContact}
            />
          </div>

          <ContactNotesSection contactId={contact.id} />

          <ContactAssignmentsSection contactId={contact.id} />
        </aside>
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
      {showTaskModal ? (
        <TaskModal
          contactId={contact.id}
          onClose={() => setShowTaskModal(false)}
          onCreated={async () => {
            setShowTaskModal(false);
            await loadContact();
            setActiveTab("tasks");
          }}
        />
      ) : null}
    </main>
  );
}

// ---------------------------------------------------------------------------
// Sub-views — la `ActivityTab` se mantiene 1:1 desde la versión anterior;
// pintar la timeline completa con tracking de email events es lógica que
// no aporta cambiar en PR-D (que es puramente visual).
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
