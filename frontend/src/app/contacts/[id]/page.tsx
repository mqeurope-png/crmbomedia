"use client";

import {
  Activity as ActivityIcon,
  Briefcase,
  CheckSquare,
  Layers,
  LifeBuoy,
  Mail,
  Sparkles,
  StickyNote,
  Tag as TagIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ContactAddressSection } from "../../components/ContactAddressSection";
import { ContactCompanySection } from "../../components/ContactCompanySection";
import { ContactCustomFieldsSection } from "../../components/ContactCustomFieldsSection";
import { ContactDetailHeader } from "../../components/contact-detail/ContactDetailHeader";
import { ContactKeyDataStrip } from "../../components/contact-detail/ContactKeyDataStrip";
import { ContactBrevoEngagementCard } from "../../components/contact-detail/ContactBrevoEngagementCard";
import { ContactNotesPreviewCard } from "../../components/contact-detail/ContactNotesPreviewCard";
import { ContactSummaryTab, ContactSummaryPlaceholderCards } from "../../components/contact-detail/ContactSummaryTab";
import { ContactSupportTab } from "../../components/contact-detail/ContactSupportTab";
import { ContactTagsPreviewCard } from "../../components/contact-detail/ContactTagsPreviewCard";
import { ContactTagsTab } from "../../components/contact-detail/ContactTagsTab";
import { ContactTasksPendingCard } from "../../components/contact-detail/ContactTasksPendingCard";
import { ContactUnsubscribeStatusCard } from "../../components/contact-detail/ContactUnsubscribeStatusCard";
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
import { getCompany } from "../../lib/companiesApi";
import { ContactEditForm } from "./ContactEditForm";
import {
  getMessageEvents,
  type EmailEvent,
} from "../../lib/emailTrackingApi";
import {
  addTagToContact,
  deactivateContact,
  getContact,
  listContactAssignments,
  removeTagFromContact,
  updateContact,
  type ActivityEvent,
  type Contact,
  type ContactAssignment,
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

type Tab =
  | "summary"
  | "activity"
  | "emails"
  | "tasks"
  | "notes"
  | "tags"
  | "opportunities"
  | "support";

// Bart pidió re-añadir la pestaña "Notas" perdida en el rediseño
// PR-D. Posición entre Tareas y Oportunidades — el `tasks` flow + el
// `notes` flow comparten sidebar y resultaba intuitivo en la ficha
// vieja.
//
// PR-Ficha-Cleanup: nueva pestaña "Tags" entre Notas y Oportunidades.
// La cell del strip estaba abarrotada (max 3 chips + "+N"), y los
// comerciales necesitan ver/editar la lista completa con autocomplete.
const TABS: Array<{ id: Tab; label: string; icon: React.ElementType }> = [
  { id: "summary", label: "Resumen", icon: Sparkles },
  { id: "activity", label: "Actividad", icon: ActivityIcon },
  { id: "emails", label: "Emails", icon: Mail },
  { id: "tasks", label: "Tareas", icon: CheckSquare },
  { id: "notes", label: "Notas", icon: StickyNote },
  { id: "tags", label: "Tags", icon: TagIcon },
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
  // PR-Ficha-Fix. Modal "Editar contacto" completo. El botón ✎ del
  // header lo abre; cerrar (Cancel/X) limpia; Save → confirma →
  // PATCH → refresh.
  const [editOpen, setEditOpen] = useState(false);
  // PR-Ficha-Cleanup. Contador que la pestaña Emails usa como dep
  // del useEffect. Tras enviar desde el header / la propia tab,
  // bumpeamos para forzar el refetch — fix del bug "email no
  // aparece en pestaña Emails de la ficha tras enviarlo".
  const [emailsRefreshKey, setEmailsRefreshKey] = useState(0);
  const [primaryAssignment, setPrimaryAssignment] =
    useState<ContactAssignment | null>(null);
  const [companyName, setCompanyName] = useState<string | null>(null);
  const autoRefreshed = useRef(false);

  const loadContact = useCallback(async () => {
    const fresh = await getContact(params.id);
    setContact(fresh);
    return fresh;
  }, [params.id]);

  // Side fetches — primary assignment + nombre empresa. Sin estos, el
  // header pintaba "Sin propietario asignado" aunque hubiera primary
  // (bug PR-D) y el strip pintaba "Sin empresa" con company_id seteado.
  const reloadPrimary = useCallback(async () => {
    try {
      const rows = await listContactAssignments(params.id);
      const primary = rows.find((r) => r.is_primary) ?? null;
      setPrimaryAssignment(primary);
    } catch {
      setPrimaryAssignment(null);
    }
  }, [params.id]);

  useEffect(() => {
    reloadPrimary();
  }, [reloadPrimary]);

  useEffect(() => {
    if (!contact?.company_id) {
      setCompanyName(null);
      return;
    }
    let cancelled = false;
    getCompany(contact.company_id)
      .then((co) => {
        if (!cancelled) setCompanyName(co.name);
      })
      .catch(() => {
        if (!cancelled) setCompanyName(null);
      });
    return () => {
      cancelled = true;
    };
  }, [contact?.company_id]);

  // PATCH callback compartido por header + strip para los inline edits
  // (nombre, puesto, score, status). Refresca tanto el contacto como
  // el primario por si el cambio dispara un assignment side-effect.
  const handlePatch = useCallback(
    async (payload: Record<string, unknown>) => {
      try {
        await updateContact(params.id, payload);
        await loadContact();
      } catch (err) {
        throw new Error(
          extractErrorMessage(err, "No se pudo actualizar el contacto."),
        );
      }
    },
    [loadContact, params.id],
  );

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

  const ownerFull = primaryAssignment?.user.full_name ?? null;
  const ownerName = ownerFull;
  const ownerInitials = ownerFull
    ? ownerFull
        .split(" ")
        .map((p) => p[0])
        .filter(Boolean)
        .slice(0, 2)
        .join("")
        .toUpperCase()
    : null;
  const assignedSince =
    primaryAssignment?.assigned_at ?? contact.updated_at ?? null;
  const lastActivityAt =
    contact.activity_events?.[0]?.occurred_at ??
    contact.updated_at_external ??
    contact.updated_at ??
    null;
  // PR-Ficha-Cleanup: el strip ya no recibe `origin` como prop —
  // resuelve el label desde `external_references_summary` internamente.
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
        onPatch={handlePatch}
        onSendEmail={() => setShowComposer(true)}
        onCreateTask={() => setShowTaskModal(true)}
        onLogCall={() => setShowTaskModal(true)}
        onEdit={() => setEditOpen(true)}
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

      {/* PR-Ficha-Cleanup: el strip ya no recibe tags ni handlers de
          add/remove — los movimos a la pestaña Tags. Los callbacks
          siguen colgando del page state porque la pestaña Tags los
          usa via prop drilling. */}
      <ContactKeyDataStrip
        contact={contact}
        companyName={companyName}
        lastActivityAt={lastActivityAt}
        onPatch={handlePatch}
      />

      {refreshWarnings.length > 0 ? (
        <ul className="freshness-warnings">
          {refreshWarnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}

      <div className="contact-detail-grid-v2">
        <section className="contact-detail-main contact-detail-main-v2">
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
              <div className="contact-summary-wrapper">
                {/* PR-Ficha-Cleanup: `ContactSummaryTab` ahora renderiza
                    solo "Actividad reciente" + "Engagement por email" —
                    Oportunidades + Incidencias se movieron al final
                    porque eran placeholder que ocupaban posición prime. */}
                <ContactSummaryTab
                  events={contact.activity_events ?? []}
                  onSeeAllActivity={() => setActiveTab("activity")}
                />
                {/* PR-Ficha-Cleanup: nuevo orden del extras grid:
                      Tareas pendientes →
                      Notas recientes →
                      Engagement Brevo →
                      Tags (nuevo) →
                      Oportunidades vinculadas (placeholder) →
                      Incidencias recientes (placeholder)
                    Los dos placeholder van al final para no quitar
                    espacio a los cards con datos reales. */}
                <div className="contact-summary contact-summary-extra">
                  <ContactTasksPendingCard
                    contactId={contact.id}
                    onSeeAll={() => setActiveTab("tasks")}
                  />
                  <ContactNotesPreviewCard
                    contactId={contact.id}
                    onSeeAll={() => setActiveTab("notes")}
                  />
                  <ContactBrevoEngagementCard contactId={contact.id} />
                  <ContactTagsPreviewCard
                    tags={tags}
                    onSeeAll={() => setActiveTab("tags")}
                  />
                  <ContactSummaryPlaceholderCards />
                </div>
              </div>
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
            {activeTab === "notes" ? (
              <ContactNotesSection contactId={contact.id} />
            ) : null}
            {activeTab === "tags" ? (
              <ContactTagsTab
                tags={tags}
                onAddTag={async (choice) => {
                  await addTagToContact(contact.id, choice);
                  await loadContact();
                }}
                onRemoveTag={async (tagId) => {
                  await removeTagFromContact(contact.id, tagId);
                  await loadContact();
                }}
              />
            ) : null}
            {activeTab === "opportunities" ? (
              <ContactPipelinesSection contactId={contact.id} />
            ) : null}
            {activeTab === "emails" ? (
              <ContactEmailsSection
                contactId={contact.id}
                contactEmail={contact.email}
                onCompose={() => setShowComposer(true)}
                refreshKey={emailsRefreshKey}
              />
            ) : null}
            {activeTab === "support" ? <ContactSupportTab /> : null}
          </div>
        </section>

        <aside className="contact-detail-sidebar-v2">
          {/* PR-Contact-Unsubscribe-Admin: card auto-oculto que solo
              se pinta si el contacto está dado de baja. Bart pedía
              poder gestionarlo desde la ficha cuando enviar daba 422
              "Este contacto se ha dado de baja". */}
          <ContactUnsubscribeStatusCard
            contactId={contact.id}
            refreshKey={emailsRefreshKey}
          />
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

          {/* PR-Db dejó duplicada la card "Notas" en sidebar + tab.
              PR-Dc: el sidebar SIEMPRE se queda sin notas. Las notas
              viven en el tab "Notas" full + preview en Resumen. */}
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
            // PR-Ficha-Cleanup: si el operador ya estaba en la
            // pestaña Emails, `setActiveTab` es no-op y el useEffect
            // del listado no se refiraba. El bump del refreshKey
            // garantiza el refetch en todos los casos.
            setEmailsRefreshKey((k) => k + 1);
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
      <ContactEditForm
        contact={contact}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        onPatch={handlePatch}
      />
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
  // PR-Dc: compactamos a una línea por evento — icono + título +
  // (badges si aplica) + tiempo relativo. Snippets + sublines viven en
  // el `title=` para HOVER (sin reflow). Click en email.sent_from_crm
  // abre el thread en una pestaña nueva.
  return (
    <ul className="timeline-list timeline-list-dense">
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
        const titleText = event.subject || event.event_type;
        const subline =
          direction === "outbound" && to
            ? `Enviado a ${to}`
            : direction === "inbound" && fromEmail
              ? `Recibido de ${fromEmail}`
              : null;
        const tooltipParts = [
          formatDateTime(event.occurred_at),
          subline,
          snippet ? `"${snippet}"` : null,
        ].filter((p): p is string => Boolean(p));
        return (
          <li
            key={event.id}
            className={`${emailEventClass(event.event_type)} is-dense`}
            title={tooltipParts.join(" — ")}
          >
            <span className="timeline-icon" aria-hidden>
              {EVENT_TYPE_ICON[event.event_type] ?? "•"}
            </span>
            <span className="timeline-title-dense">
              {threadId && isEmail ? (
                <a
                  href={`/emails/${threadId}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  {titleText}
                </a>
              ) : (
                titleText
              )}
            </span>
            {event.event_type === "email.sent_from_crm" && messageId ? (
              <EmailEventBadges events={trackingEvents} />
            ) : null}
            <span className="muted small timeline-time-dense">
              {relativeTimeShort(event.occurred_at)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// Tiempo relativo compacto compartido. Coincide con `relativeTime` de
// ContactSummaryTab pero local para no acoplar archivos. Considerar
// extraer a `lib/time.ts` en un PR de cleanup posterior.
function relativeTimeShort(value: string | null | undefined): string {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const diffSec = Math.floor((Date.now() - then) / 1000);
  if (diffSec < 60) return "ahora";
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `hace ${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `hace ${hr}h`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `hace ${day}d`;
  const mo = Math.floor(day / 30);
  if (mo < 12) return `hace ${mo}mo`;
  return `hace ${Math.floor(mo / 12)}y`;
}
