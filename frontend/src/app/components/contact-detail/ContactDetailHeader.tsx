"use client";

/**
 * Cabecera de la ficha del contacto: UNA sola banda.
 *
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │ [TF] Nombre  (Lead nuevo) ☆☆☆☆☆          [✉][+][📞][⚡][✎][⋮]  │
 *   │      Propietario B Bart · Asignado el 10 oct · añadir puesto      │
 *   │      EMAIL x@y.z ⧉ · TELÉFONO 555… · ORIGEN Formulario web · … │
 *   └──────────────────────────────────────────────────────────────────┘
 *
 * Antes eran dos bloques (esta cabecera y una tira de datos clave aparte)
 * que se comían media pantalla antes de llegar a las pestañas. Los datos
 * clave (`ContactKeyDataStrip`) viven ahora dentro, en una fila compacta
 * que omite lo vacío; las estrellas y el puesto van en línea, sin fila
 * propia. Al bajar, una versión reducida (nombre, estado y acciones) se
 * queda pegada arriba: es lo que hace falta a mano mientras se lee el
 * resto.
 */
import { Mail, MoreVertical, Pencil, Phone, Plus, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { StarRating } from "../StarRating";
import type { Contact, User } from "../../lib/api";
import { formatBackendDateTime } from "../../lib/dates";
import { ContactKeyDataStrip } from "./ContactKeyDataStrip";
import { InlineEdit } from "./InlineEdit";

type Action = {
  key: string;
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
};

type Props = {
  contact: Contact;
  ownerName?: string | null;
  ownerInitials?: string | null;
  assignedSince?: string | null;
  /** Datos clave que antes iban en la tira aparte. */
  companyName?: string | null;
  lastActivityAt?: string | null;
  primaryPhone?: string | null;
  /** PATCH callback compartido por todos los inline edits del header
      (nombre, puesto, estado, estrellas, score). */
  onPatch: (payload: Record<string, unknown>) => Promise<void>;
  onSendEmail: () => void;
  onCreateTask: () => void;
  onLogCall: () => void;
  onRunWorkflow?: () => void;
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

const formatDate = (value?: string | null) =>
  formatBackendDateTime(value, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });

function HeaderActions({ actions }: { actions: Action[] }) {
  return (
    <>
      {actions.map((a) => (
        <button
          key={a.key}
          type="button"
          className="button small secondary"
          onClick={a.onClick}
        >
          {a.icon} {a.label}
        </button>
      ))}
    </>
  );
}

export function ContactDetailHeader({
  contact,
  ownerName,
  ownerInitials,
  assignedSince,
  companyName,
  lastActivityAt,
  primaryPhone,
  onPatch,
  onSendEmail,
  onCreateTask,
  onLogCall,
  onRunWorkflow,
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

  // La barra reducida aparece cuando la cabecera entera ya no se ve. Se
  // observa la propia cabecera (el scroll vive en `.app-shell-content`,
  // pero la intersección con la ventana ya descuenta ese recorte).
  const cabeceraRef = useRef<HTMLElement | null>(null);
  const [pegada, setPegada] = useState(false);
  useEffect(() => {
    const el = cabeceraRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const observador = new IntersectionObserver(
      ([entrada]) => setPegada(!entrada.isIntersecting),
      { threshold: 0 },
    );
    observador.observe(el);
    return () => observador.disconnect();
  }, []);

  const actions: Action[] = [
    { key: "email", label: "Enviar correo", icon: <Mail size={14} aria-hidden />, onClick: onSendEmail },
    { key: "task", label: "Crear tarea", icon: <Plus size={14} aria-hidden />, onClick: onCreateTask },
    { key: "call", label: "Registrar llamada", icon: <Phone size={14} aria-hidden />, onClick: onLogCall },
    {
      key: "run-workflow",
      label: "Ejecutar workflow",
      icon: <Zap size={14} aria-hidden />,
      onClick: onRunWorkflow ?? (() => undefined),
    },
    { key: "edit", label: "Editar", icon: <Pencil size={14} aria-hidden />, onClick: onEdit },
  ];

  const estadoChip = (editable: boolean) => (
    <span className={`contact-status-chip is-${status.tone}`}>
      <span className="contact-status-dot" aria-hidden />
      {editable ? (
        <InlineEdit
          kind="select"
          value={contact.commercial_status ?? "new"}
          options={STATUS_OPTIONS}
          ariaLabel="Estado comercial"
          display={<span>{status.label}</span>}
          onSave={(next) => onPatch({ commercial_status: next })}
        />
      ) : (
        <span>{status.label}</span>
      )}
    </span>
  );

  return (
    <>
      {/* Versión reducida, pegada arriba al bajar. Ocupa 0 px de alto
          mientras no se ve para no mover nada del resto. */}
      <div
        className={`contact-header-sticky${pegada ? " is-visible" : ""}`}
        aria-hidden={!pegada}
        data-testid="contact-header-sticky"
      >
        <div className="contact-header-sticky-inner">
          <span className="contact-header-avatar contact-header-avatar-mini" aria-hidden>
            {initials(contact.first_name, contact.last_name)}
          </span>
          <span className="contact-header-sticky-name">{fullName}</span>
          {estadoChip(false)}
          <div className="contact-header-actions contact-header-actions-mini">
            <HeaderActions actions={actions} />
          </div>
        </div>
      </div>

      <header className="contact-header-card" ref={cabeceraRef}>
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
              {estadoChip(true)}
              {/* PR-Consolidado — Star Rating, editable en caliente; en la
                  misma línea que el nombre, sin fila propia. */}
              <span className="contact-header-star-rating">
                <StarRating
                  value={
                    typeof contact.star_rating === "number"
                      ? contact.star_rating
                      : 0
                  }
                  editable
                  size="sm"
                  ariaLabel="Valoración del contacto"
                  onChange={(next) =>
                    onPatch({ star_rating: next === 0 ? null : next })
                  }
                />
              </span>
            </div>
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
              {" · "}
              {/* El puesto, en la misma línea: si está vacío no gasta una
                  fila para decir «Sin puesto», solo ofrece añadirlo. */}
              <span className="contact-header-puesto">
                <InlineEdit
                  value={contact.job_title ?? ""}
                  emptyLabel="Añadir puesto"
                  ariaLabel="Puesto"
                  display={
                    contact.job_title ? (
                      <span className="contact-header-puesto-valor">{contact.job_title}</span>
                    ) : (
                      <span className="contact-header-puesto-vacio">añadir puesto</span>
                    )
                  }
                  onSave={(next) =>
                    onPatch({ job_title: next.trim() || null })
                  }
                />
              </span>
            </p>
          </div>
        </div>
        <div className="contact-header-actions">
          <HeaderActions actions={actions} />
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
              <div className="contact-header-overflow-menu" role="menu">
                {overflowChildren}
              </div>
            ) : null}
          </div>
        </div>
        {/* Los datos clave, a todo el ancho de la banda (debajo del nombre
            y de los botones) para que quepan en una línea. */}
        <ContactKeyDataStrip
          contact={contact}
          companyName={companyName}
          lastActivityAt={lastActivityAt}
          primaryPhone={primaryPhone}
          onPatch={onPatch}
        />
      </header>
    </>
  );
}
