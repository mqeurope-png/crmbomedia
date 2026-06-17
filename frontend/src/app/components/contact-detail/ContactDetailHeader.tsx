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
import { InlineEdit } from "./InlineEdit";

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
  /** PATCH callback compartido por todos los inline edits del header
      (nombre, puesto). Recibe el payload parcial y devuelve cuando la
      mutación está aplicada. */
  onPatch: (payload: Record<string, unknown>) => Promise<void>;
  onSendEmail: () => void;
  onCreateTask: () => void;
  onLogCall: () => void;
  onEdit: () => void;
  onOpenOverflow: () => void;
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

const STATUS_OPTIONS: ReadonlyArray<[string, string]> = [
  ["new", "Lead nuevo"],
  ["qualified", "Calificado"],
  ["working", "Trabajando"],
  ["won", "Cliente"],
  ["lost", "Perdido"],
];

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
  onPatch,
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
            {/* Click sobre el nombre → input inline. Save al blur o Enter
                vía PATCH `first_name + last_name`. Si solo escribe 1 palabra
                queda como first_name + last_name vacío — el split por
                primer espacio cubre la mayoría de casos. */}
            <h1 className="contact-header-name">
              <InlineEdit
                value={fullName === "(Sin nombre)" ? "" : fullName}
                emptyLabel="(Sin nombre)"
                ariaLabel="Nombre completo"
                display={<span>{fullName}</span>}
                onSave={async (next) => {
                  const parts = next.split(" ");
                  const first = parts.shift() ?? "";
                  const last = parts.join(" ");
                  await onPatch({
                    first_name: first || null,
                    last_name: last || null,
                  });
                }}
              />
            </h1>
            <span className={`contact-status-chip is-${status.tone}`}>
              <span className="contact-status-dot" aria-hidden />
              <InlineEdit
                kind="select"
                value={contact.commercial_status ?? "new"}
                options={STATUS_OPTIONS}
                ariaLabel="Estado comercial"
                display={<span>{status.label}</span>}
                onSave={(next) => onPatch({ commercial_status: next })}
              />
            </span>
          </div>
          {/* Subtítulo limpio — el link "Empresa asociada" que loopeaba al
              sidebar se quita; el dato vive en el sidebar derecha. */}
          <p className="contact-header-subtitle">
            <InlineEdit
              value={contact.job_title ?? ""}
              emptyLabel="Sin puesto"
              ariaLabel="Puesto"
              display={
                contact.job_title ? (
                  <span>{contact.job_title}</span>
                ) : (
                  <span className="muted">Sin puesto</span>
                )
              }
              onSave={(next) =>
                onPatch({ job_title: next.trim() || null })
              }
            />
          </p>
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
