"use client";

/**
 * Strip horizontal de datos clave del contacto. PR-D BoHub.
 *
 * 8 cells: Email | Teléfono | Empresa | Origen | Etiquetas | Última
 * actividad | Score | Estado del ciclo. Si el campo es largo (e.g.
 * 5 etiquetas), se trunca con "+N" + tooltip nativo.
 */
import { Copy, Phone as PhoneIcon, Plus, X } from "lucide-react";
import { useState } from "react";
import type { Contact, Tag } from "../../lib/api";
import { TagPicker } from "../TagPicker";
import { InlineEdit } from "./InlineEdit";

type Props = {
  contact: Contact;
  companyName?: string | null;
  tags: Tag[];
  origin?: string | null;
  lastActivityAt?: string | null;
  primaryPhone?: string | null;
  /** PATCH callback compartido con header — recibe el payload parcial
      y devuelve cuando la mutación está aplicada. */
  onPatch: (payload: Record<string, unknown>) => Promise<void>;
  /** Tags inline edit handlers — el wrapper TagPicker existe (Sprint
      Filtros) y solo pulsamos sus callbacks. PR-Dc. */
  onAddTag?: (choice: { tag_id?: string; tag_name?: string }) => Promise<void>;
  onRemoveTag?: (tagId: string) => Promise<void>;
};

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const STATUS_LABEL: Record<string, string> = {
  new: "Lead nuevo",
  qualified: "Calificado",
  working: "Trabajando",
  won: "Cliente",
  lost: "Perdido",
};

const STATUS_OPTIONS: ReadonlyArray<[string, string]> = [
  ["new", "Lead nuevo"],
  ["qualified", "Calificado"],
  ["working", "Trabajando"],
  ["won", "Cliente"],
  ["lost", "Perdido"],
];

function copyToClipboard(value: string) {
  if (typeof navigator !== "undefined" && navigator.clipboard) {
    navigator.clipboard.writeText(value).catch(() => undefined);
  }
}

export function ContactKeyDataStrip({
  contact,
  companyName,
  tags,
  origin,
  lastActivityAt,
  primaryPhone,
  onPatch,
  onAddTag,
  onRemoveTag,
}: Props) {
  const [editingTags, setEditingTags] = useState(false);
  const visibleTags = tags.slice(0, 3);
  const extraTags = Math.max(0, tags.length - visibleTags.length);
  const phone = primaryPhone ?? contact.phone ?? null;

  return (
    <section className="contact-strip" aria-label="Datos clave">
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Email</span>
        <span className="contact-strip-value contact-strip-value-link">
          {contact.email ? (
            <>
              <a href={`mailto:${contact.email}`} title="Abrir en cliente de correo">
                {contact.email}
              </a>
              <button
                type="button"
                className="contact-strip-copy"
                onClick={() => copyToClipboard(contact.email)}
                aria-label="Copiar email"
                title="Copiar email"
              >
                <Copy size={11} aria-hidden />
              </button>
            </>
          ) : (
            <span className="muted">—</span>
          )}
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Teléfono</span>
        <span className="contact-strip-value contact-strip-value-link">
          {phone ? (
            <>
              <a href={`tel:${phone}`}>{phone}</a>
              <button
                type="button"
                className="contact-strip-copy"
                onClick={() => copyToClipboard(phone)}
                aria-label="Copiar teléfono"
                title="Copiar teléfono"
              >
                <PhoneIcon size={11} aria-hidden />
              </button>
            </>
          ) : (
            <span className="muted">—</span>
          )}
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Empresa</span>
        <span className="contact-strip-value">
          {companyName ?? <span className="muted">Sin empresa</span>}
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Origen del lead</span>
        <span className="contact-strip-value">
          {origin ?? <span className="muted">—</span>}
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Etiquetas</span>
        <span className="contact-strip-value contact-strip-tags">
          {tags.length === 0 ? <span className="muted">—</span> : null}
          {visibleTags.map((t) => (
            <span
              key={t.id}
              className="contact-strip-tag"
              style={{ ["--tag-color" as string]: t.color ?? "var(--color-primary)" }}
            >
              {t.name}
              {editingTags && onRemoveTag ? (
                <button
                  type="button"
                  className="contact-strip-tag-remove"
                  aria-label={`Quitar ${t.name}`}
                  onClick={() => onRemoveTag(t.id)}
                >
                  <X size={10} aria-hidden />
                </button>
              ) : null}
            </span>
          ))}
          {extraTags > 0 ? (
            <span
              className="contact-strip-tag is-extra"
              title={tags
                .slice(3)
                .map((t) => t.name)
                .join(", ")}
            >
              +{extraTags}
            </span>
          ) : null}
          {/* Toggle edición — un solo botón "+" para abrir el TagPicker
              inline. Bart spec: save al blur del picker; aquí el blur
              cierra el modo edición. */}
          {onAddTag ? (
            editingTags ? (
              <span
                className="contact-strip-tag-picker"
                onBlur={(e) => {
                  // El TagPicker tiene su propio handler de clic-fuera;
                  // aquí solo cerramos cuando el focus salga del wrapper.
                  if (
                    !e.currentTarget.contains(e.relatedTarget as Node | null)
                  ) {
                    setEditingTags(false);
                  }
                }}
              >
                <TagPicker
                  excludeTagIds={tags.map((t) => t.id)}
                  onPick={async (choice) => {
                    await onAddTag(choice);
                  }}
                />
              </span>
            ) : (
              <button
                type="button"
                className="contact-strip-tag-add"
                aria-label="Editar etiquetas"
                onClick={() => setEditingTags(true)}
              >
                <Plus size={11} aria-hidden />
              </button>
            )
          ) : null}
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Última actividad</span>
        <span className="contact-strip-value">
          {lastActivityAt ? formatDate(lastActivityAt) : <span className="muted">—</span>}
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Score</span>
        <span className="contact-strip-value">
          {/* Bart: editable por cualquier user — click → input numérico
              save-on-blur. Sin validación de rango (mantenemos el
              lead_score libre como el modelo backend). */}
          <InlineEdit
            kind="number"
            value={contact.lead_score ?? null}
            ariaLabel="Lead score"
            emptyLabel="—"
            display={
              contact.lead_score !== null && contact.lead_score !== undefined ? (
                <strong>{contact.lead_score}</strong>
              ) : (
                <span className="muted">—</span>
              )
            }
            onSave={(next) => onPatch({ lead_score: next })}
          />
        </span>
      </div>
      <div className="contact-strip-cell">
        <span className="contact-strip-label">Estado del ciclo</span>
        <span className="contact-strip-value">
          <InlineEdit
            kind="select"
            value={contact.commercial_status ?? "new"}
            options={STATUS_OPTIONS}
            ariaLabel="Estado del ciclo"
            display={
              <span>
                {STATUS_LABEL[contact.commercial_status ?? "new"] ??
                  contact.commercial_status ??
                  "—"}
              </span>
            }
            onSave={(next) => onPatch({ commercial_status: next })}
          />
        </span>
      </div>
    </section>
  );
}
