"use client";

import { Briefcase } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useRouter } from "next/navigation";
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
import { ContactWorkflowsTab } from "../../components/contact-detail/ContactWorkflowsTab";
import { ContactTagsPreviewCard } from "../../components/contact-detail/ContactTagsPreviewCard";
import { ContactTagsTab } from "../../components/contact-detail/ContactTagsTab";
import { ContactTasksPendingCard } from "../../components/contact-detail/ContactTasksPendingCard";
import { ContactUnsubscribeStatusCard } from "../../components/contact-detail/ContactUnsubscribeStatusCard";
import { ContactEmailsSection } from "../../components/ContactEmailsSection";
import { ContactAssignmentsSection } from "../../components/ContactAssignmentsSection";
import { HistorialTab } from "../../components/contact-detail/HistorialTab";
import {
  RegisterCallModal,
  RunWorkflowMenu,
} from "../../components/contact-detail/RegisterCallModal";
import { ContactNotesSection } from "../../components/ContactNotesSection";
import { ContactPhonesSection } from "../../components/ContactPhonesSection";
import { ContactProfessionalSection } from "../../components/ContactProfessionalSection";
import { ContactPipelinesSection } from "../../components/ContactPipelinesSection";
import { ContactTasksSection } from "../../components/ContactTasksSection";
import { EmailComposerModal } from "../../components/EmailComposerModal";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { RefreshExternalDataButton } from "../../components/RefreshExternalDataButton";
import { TaskModal } from "../../components/TaskModal";
import { getCompany } from "../../lib/companiesApi";
import { ContactEditForm } from "./ContactEditForm";
import { CONTACT_DETAIL_TABS, type ContactTab } from "./tabs";
import {
  addTagToContact,
  deactivateContact,
  deleteContactHard,
  getContact,
  getCurrentUser,
  listContactAssignments,
  removeTagFromContact,
  type User as CurrentUser,
  updateContact,
  type Contact,
  type ContactAssignment,
  type ExternalRefreshResult,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

type Tab = ContactTab;

export default function ContactDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [contact, setContact] = useState<Contact | null>(null);
  const [refreshWarnings, setRefreshWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>("summary");
  const [showComposer, setShowComposer] = useState(false);
  const [showTaskModal, setShowTaskModal] = useState(false);
  // Sprint Ficha 360.
  const [showCallModal, setShowCallModal] = useState(false);
  const [showRunWorkflow, setShowRunWorkflow] = useState(false);
  const [historyTick, setHistoryTick] = useState(0);
  const [overflowOpen, setOverflowOpen] = useState(false);
  // PR-Ficha-Fix. Modal "Editar contacto" completo. El botón ✎ del
  // header lo abre; cerrar (Cancel/X) limpia; Save → confirma →
  // PATCH → refresh.
  const [editOpen, setEditOpen] = useState(false);
  // PR-Backlog-Consolidado B1. Modal de confirmación para borrar
  // contacto definitivamente — requiere teclear BORRAR.
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  useEffect(() => {
    void getCurrentUser().then(setCurrentUser).catch(() => {});
  }, []);
  const canHardDelete =
    currentUser?.role === "admin" || currentUser?.role === "manager";
  // PR-Ficha-Cleanup. Contador que la pestaña Emails usa como dep
  // del useEffect. Tras enviar desde el header / la propia tab,
  // bumpeamos para forzar el refetch — fix del bug "email no
  // aparece en pestaña Emails de la ficha tras enviarlo".
  const [emailsRefreshKey, setEmailsRefreshKey] = useState(0);
  const [primaryAssignment, setPrimaryAssignment] =
    useState<ContactAssignment | null>(null);
  const [companyName, setCompanyName] = useState<string | null>(null);
  // Bug 11 fix: el strip cabecera ahora lee el teléfono primario de
  // `contact_phones` (tabla nueva) en lugar de `contact.phone` (legacy)
  // para que add/edit desde el sidebar se reflejen al instante.
  const [primaryPhone, setPrimaryPhone] = useState<string | null>(null);
  const autoRefreshed = useRef(false);
  // PR-Hotfix-Notas-Widget. Las notas/tareas de AgileCRM se importan
  // ON-DEMAND al abrir la ficha (auto-refresh de abajo) — al primer
  // mount la BD puede no tenerlas todavía. Este tick sube cuando un
  // refresh externo termina, para que los widgets self-fetch del
  // Resumen (Notas recientes) re-fetcheen y vean las filas recién
  // importadas — igual que la pestaña Notas, que siempre se abre
  // DESPUÉS del refresh y por eso nunca falló. `externalRefreshing`
  // mantiene el "Cargando…" del widget mientras el refresh está en
  // vuelo, en vez de un "Sin notas todavía" transitorio y engañoso.
  const [externalRefreshTick, setExternalRefreshTick] = useState(0);
  const [externalRefreshing, setExternalRefreshing] = useState(false);

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

  // Bug 11: cargar teléfonos para alimentar la cabecera con el
  // primario (o el primero por position) en vez del legacy
  // `contact.phone`. Se re-ejecuta tras un PATCH del contacto vía
  // `refreshSignal` para reflejar add/remove desde el sidebar.
  const [phoneRefreshTick, setPhoneRefreshTick] = useState(0);
  // PR-Fix-Regresiones-PR237: callback estable para que
  // ContactPhonesSection no rebuild su `load` cada render. El loop
  // crítico del PR #237 venía del binding inline `() => setX(...)`.
  const bumpPhoneTick = useCallback(
    () => setPhoneRefreshTick((n) => n + 1),
    [],
  );
  useEffect(() => {
    if (!contact?.id) {
      setPrimaryPhone(null);
      return;
    }
    let cancelled = false;
    import("../../lib/contactChannelsApi")
      .then((mod) => mod.listContactPhones(contact.id))
      .then((rows) => {
        if (cancelled) return;
        if (!rows.length) {
          // Fallback al legacy si no hay filas (contacto sin migrar).
          setPrimaryPhone(contact.phone ?? null);
          return;
        }
        const primary = rows.find((p) => p.is_primary) ?? rows[0];
        setPrimaryPhone(primary?.number ?? null);
      })
      .catch(() => {
        if (!cancelled) setPrimaryPhone(contact.phone ?? null);
      });
    return () => {
      cancelled = true;
    };
  }, [contact?.id, contact?.phone, phoneRefreshTick]);

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
      // El refresh ya commiteó las notas/tareas importadas — avisar a
      // los widgets self-fetch para que re-fetcheen (y soltar el estado
      // "refrescando" que mantenía su spinner).
      setExternalRefreshing(false);
      setExternalRefreshTick((n) => n + 1);
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
    setExternalRefreshing(true);
    import("../../lib/api").then(({ refreshContactExternalData }) => {
      refreshContactExternalData(contact.id)
        .then(handleRefreshDone)
        .catch(() => setExternalRefreshing(false));
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

  async function handleHardDelete() {
    if (!contact) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteContactHard(contact.id);
      router.push("/contacts");
    } catch (err) {
      setDeleteError(
        extractErrorMessage(err, "No se pudo borrar el contacto."),
      );
    } finally {
      setDeleting(false);
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
        onLogCall={() => setShowCallModal(true)}
        onRunWorkflow={() => setShowRunWorkflow(true)}
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
            {/* PR-Backlog-Consolidado B1. Hard delete admin/manager-only.
                El backend también lo gate por rol — esto solo evita
                pintar el botón a quien no puede ejecutarlo. */}
            {canHardDelete ? (
              <button
                type="button"
                className="contact-header-overflow-item is-danger"
                onClick={() => {
                  setOverflowOpen(false);
                  setDeleteOpen(true);
                  setDeleteConfirmText("");
                  setDeleteError(null);
                }}
              >
                Borrar contacto (definitivo)
              </button>
            ) : null}
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
        primaryPhone={primaryPhone}
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
            {CONTACT_DETAIL_TABS.map((t) => {
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
                  contactId={contact.id}
                  events={contact.activity_events ?? []}
                  onSeeAllActivity={() => setActiveTab("history")}
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
                    refreshKey={externalRefreshTick}
                    refreshing={externalRefreshing}
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
            {activeTab === "tasks" ? (
              <ContactTasksSection contactId={contact.id} />
            ) : null}
            {activeTab === "notes" ? (
              <ContactNotesSection contactId={contact.id} />
            ) : null}
            {activeTab === "history" ? (
              <HistorialTab contactId={contact.id} refreshKey={historyTick} />
            ) : null}
            {activeTab === "tags" ? (
              <ContactTagsTab
                tags={tags}
                onAddTag={async (choice) => {
                  // PR-TagPicker-Ficha-Contacto. Antes los errores del
                  // POST (p.ej. un 403 histórico) se tragaban en
                  // silencio y "no pasaba nada". Ahora se propagan al
                  // toast de la ficha.
                  try {
                    await addTagToContact(contact.id, choice);
                    await loadContact();
                  } catch (err) {
                    setError(
                      extractErrorMessage(err, "No se pudo añadir la tag."),
                    );
                  }
                }}
                onRemoveTag={async (tagId) => {
                  try {
                    await removeTagFromContact(contact.id, tagId);
                    await loadContact();
                  } catch (err) {
                    setError(
                      extractErrorMessage(err, "No se pudo quitar la tag."),
                    );
                  }
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
            {activeTab === "workflows" ? (
              <ContactWorkflowsTab
                contactId={contact.id}
                canManage={canHardDelete}
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
            <ContactPhonesSection
              contactId={contact.id}
              onChanged={bumpPhoneTick}
            />
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
        // PR-Fix-Modal-Nuevo-Email-Layout. Wrap consistente con
        // `emails/layout.tsx` y `emails/drafts/page.tsx`: el
        // composer es un panel derecho fijo (45vw) sin oscurecer la
        // ficha del contacto, no un overlay full-screen.
        <div
          className="email-compose-panel"
          role="presentation"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setShowComposer(false);
          }}
        >
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
        </div>
      ) : null}
      <RegisterCallModal
        contactId={contact.id}
        open={showCallModal}
        onClose={() => setShowCallModal(false)}
        onSaved={() => setHistoryTick((t) => t + 1)}
        onRequestCompose={() => setShowComposer(true)}
        currentStarRating={contact.star_rating}
      />
      <RunWorkflowMenu
        contactId={contact.id}
        open={showRunWorkflow}
        onClose={() => setShowRunWorkflow(false)}
        onRan={() => setHistoryTick((t) => t + 1)}
      />
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
        // PR-Fix-Regresiones-PR237 Bug 12. La cabecera lee el owner de
        // `primaryAssignment` (fuente de verdad de `contact_assignments`).
        // `contact.owner_user_id` es un cache desnormalizado que puede
        // estar NULL para contactos legacy aunque sí haya un primary —
        // por eso el modal mostraba "(Sin propietario)" mientras la
        // cabecera mostraba el owner correcto. Pasamos primary explícito
        // como fallback. Si NO hay primary, mantiene el valor del
        // contact (puede ser null = realmente sin owner).
        fallbackOwnerUserId={primaryAssignment?.user.id ?? null}
        fallbackOwnerLabel={primaryAssignment?.user.full_name ?? null}
      />

      {/* PR-Backlog-Consolidado B1. Modal de confirmación doble. */}
      {deleteOpen ? (
        <div
          className="modal-backdrop"
          onClick={() => !deleting && setDeleteOpen(false)}
        >
          <div
            className="modal-card contact-delete-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <h2>Borrar contacto definitivamente</h2>
            <p>
              Esta acción <strong>no se puede deshacer</strong>. Vas a
              borrar definitivamente{" "}
              <strong>
                {contact.first_name} {contact.last_name ?? ""}
              </strong>{" "}
              junto con todas sus tareas, notas, asignaciones y oportunidades.
              Los emails históricos se preservan con `contact_id = NULL` por
              auditoría. Los workflow runs activos quedan cancelados.
            </p>
            <p className="muted small">
              Escribe <code>BORRAR</code> para confirmar.
            </p>
            <input
              type="text"
              value={deleteConfirmText}
              onChange={(e) => setDeleteConfirmText(e.target.value)}
              placeholder="BORRAR"
              autoFocus
              disabled={deleting}
            />
            {deleteError ? (
              <p className="form-error">{deleteError}</p>
            ) : null}
            <div className="modal-actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => setDeleteOpen(false)}
                disabled={deleting}
              >
                Cancelar
              </button>
              <button
                type="button"
                className="button is-danger"
                onClick={handleHardDelete}
                disabled={deleteConfirmText !== "BORRAR" || deleting}
              >
                {deleting ? "Borrando…" : "Borrar definitivamente"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
