"use client";

/**
 * Header de la ficha contacto BoHub (PR-D). Full-width.
 *
 *   ┌────────────────────────────────────────────────────────────┐
 *   │ [Avatar] Nombre + chip estado            [✉][✅][📞][✎][⋮] │
 *   │          Job title · Empresa                                │
 *   │          Propietario: Juan Sánchez · Asignado el 12 feb.   │
 *   └────────────────────────────────────────────────────────────┘
 *
 * Las 5 acciones del top-right encadenan los modals existentes (Email
 * Composer + Task Modal); el bouton ✎ es un placeholder que abre el
 * sidebar de la ficha (todavía no hay modal de edición global de
 * contacto — se edita inline por campo).
 */
import { Mail, MoreVertical, Pencil, Phone, Plus } from "lucide-react";
import type { Contact, User } from "../../lib/api";

type Action = {
  key: string;
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  variant?: "primary" | "secondary";
};

type Props = {
  contact: Contact;
  ownerName?: string | null;
  ownerInitials?: string | null;
  assignedSince?: string | null;
  onSendEmail: () => void;
  onCreateTask: () => void;
  onLogCall: () => void;
  onEdit: () => void;
  onOpenOverflow: () => void;
  /** Acciones extra del overflow (Refresh, Desactivar…). */
  overflowChildren?: React.ReactNode;
  overflowOpen: boolean;
  currentUser?: User | null;
};

const STATUS_LABELS: Record<string, { label: string; tone: string }> = {
  new: { label: "Lead nuevo", tone: "info" },
  qualified: { label: "Calificado", tone: "primary" },
  working: { label: "Trabajando", tone: "warning" },
  won: { label: "Cliente", tone: "success" },
  lost: { label: "Perdido", tone: "muted" },
};

function initials(first: string, last?: string | null): string {
  const f = (first ?? "").trim()[0] ?? "";
  const l = (last ?? "").trim()[0] ?? "";
  return (f + l).toUpperCase() || "?";
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function ContactDetailHeader({
  contact,
  ownerName,
  ownerInitials,
  assignedSince,
  onSendEmail,
  onCreateTask,
  onLogCall,
  onEdit,
  onOpenOverflow,
  overflowChildren,
  overflowOpen,
  currentUser: _currentUser,
}: Props) {
  void _currentUser;
  const fullName =
    [contact.first_name, contact.last_name].filter(Boolean).join(" ") ||
    "(Sin nombre)";
  const status = STATUS_LABELS[contact.commercial_status ?? "new"] ?? {
    label: contact.commercial_status ?? "—",
    tone: "muted",
  };
  const subtitleParts = [
    contact.job_title,
    contact.company_id ? "Empresa" : null,
  ].filter(Boolean);

  const actions: Action[] = [
    {
      key: "email",
      label: "Enviar correo",
      icon: <Mail size={14} aria-hidden />,
      onClick: onSendEmail,
      variant: "secondary",
    },
    {
      key: "task",
      label: "Crear tarea",
      icon: <Plus size={14} aria-hidden />,
      onClick: onCreateTask,
      variant: "secondary",
    },
    {
      key: "call",
      label: "Registrar llamada",
      icon: <Phone size={14} aria-hidden />,
      onClick: onLogCall,
      variant: "secondary",
    },
    {
      key: "edit",
      label: "Editar",
      icon: <Pencil size={14} aria-hidden />,
      onClick: onEdit,
      variant: "secondary",
    },
  ];

  return (
    <header className="contact-header-card">
      <div className="contact-header-main">
        <div className="contact-header-avatar" aria-hidden>
          {initials(contact.first_name, contact.last_name)}
        </div>
        <div className="contact-header-info">
          <div className="contact-header-name-row">
            <h1 className="contact-header-name">{fullName}</h1>
            <span className={`contact-status-chip is-${status.tone}`}>
              <span className="contact-status-dot" aria-hidden />
              {status.label}
            </span>
          </div>
          {subtitleParts.length ? (
            <p className="contact-header-subtitle">
              {contact.job_title}
              {contact.job_title && contact.company_id ? " · " : null}
              {contact.company_id ? (
                <a className="contact-header-company-link" href="#sidebar-company">
                  Empresa asociada
                </a>
              ) : null}
            </p>
          ) : null}
          <p className="contact-header-meta muted small">
            {ownerName ? (
              <>
                Propietario{" "}
                <span className="contact-header-owner">
                  <span className="contact-header-owner-avatar" aria-hidden>
                    {ownerInitials || "?"}
                  </span>
                  {ownerName}
                </span>
              </>
            ) : (
              <>Sin propietario asignado</>
            )}
            {assignedSince ? (
              <>
                {" · "}Asignado el {formatDate(assignedSince)}
              </>
            ) : null}
          </p>
        </div>
      </div>
      <div className="contact-header-actions">
        {actions.map((a) => (
          <button
            key={a.key}
            type="button"
            className={`button small ${a.variant === "primary" ? "" : "secondary"}`}
            onClick={a.onClick}
          >
            {a.icon} {a.label}
          </button>
        ))}
        <div className="contact-header-overflow">
          <button
            type="button"
            className="button small secondary contact-header-overflow-toggle"
            aria-label="Más acciones"
            aria-expanded={overflowOpen}
            onClick={onOpenOverflow}
          >
            <MoreVertical size={14} aria-hidden />
          </button>
          {overflowOpen ? (
            <div
              className="contact-header-overflow-menu"
              role="menu"
            >
              {overflowChildren}
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}
