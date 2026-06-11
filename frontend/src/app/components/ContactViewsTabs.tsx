"use client";

import { Plus, Settings2 } from "lucide-react";
import { useState } from "react";
import type { SavedView } from "../lib/api";

type Props = {
  views: SavedView[];
  activeId: string | null;
  /** Set when the user has tweaked the query builder away from the
   * active view's saved state. Renders a "·" badge on the active tab. */
  isDirty: boolean;
  onSelect: (view: SavedView | null) => void;
  onCreate: () => void;
  onEdit: (view: SavedView) => void;
  onDuplicate: (view: SavedView) => void;
  onSetDefault: (view: SavedView) => void;
  onDelete: (view: SavedView) => void;
};

const ALL_TAB_ID = "__all__";

/** Brevo-style horizontal tabs of saved views. "Todos los contactos"
 * sits at the left as a permanent reset tab (active when no saved
 * view is loaded); each saved view becomes a tab to its right. The
 * cogwheel on the active tab opens a small menu with the per-view
 * actions (rename / share / duplicate / set-default / delete).
 *
 * Sharing / set-default flow through the existing endpoints; the
 * component is purely presentation + dispatching.
 */
export function ContactViewsTabs({
  views,
  activeId,
  isDirty,
  onSelect,
  onCreate,
  onEdit,
  onDuplicate,
  onSetDefault,
  onDelete,
}: Props) {
  const [menuOpen, setMenuOpen] = useState<string | null>(null);

  const activeView = views.find((v) => v.id === activeId) ?? null;
  const isAllActive = activeId === null;

  return (
    <nav className="contact-views-tabs" aria-label="Vistas guardadas">
      <ul className="contact-views-tabs-list">
        <li>
          <button
            type="button"
            className={`contact-views-tab ${isAllActive ? "is-active" : ""}`}
            onClick={() => onSelect(null)}
          >
            Todos los contactos
            {isAllActive && isDirty ? (
              <span
                className="contact-views-tab-dot"
                title="Cambios sin guardar"
              >
                ·
              </span>
            ) : null}
          </button>
        </li>
        {views.map((view) => {
          const isActive = view.id === activeId;
          return (
            <li key={view.id}>
              <button
                type="button"
                className={`contact-views-tab ${isActive ? "is-active" : ""}`}
                onClick={() => onSelect(view)}
              >
                {view.name}
                {view.is_default ? (
                  <span className="contact-views-tab-default" title="Por defecto">
                    ★
                  </span>
                ) : null}
                {isActive && isDirty ? (
                  <span
                    className="contact-views-tab-dot"
                    title="Cambios sin guardar"
                  >
                    ·
                  </span>
                ) : null}
              </button>
              <button
                type="button"
                className="contact-views-tab-menu-button"
                onClick={(e) => {
                  e.stopPropagation();
                  setMenuOpen(menuOpen === view.id ? null : view.id);
                }}
                aria-haspopup="menu"
                aria-expanded={menuOpen === view.id}
                aria-label={`Acciones de ${view.name}`}
              >
                <Settings2 size={13} aria-hidden />
              </button>
              {menuOpen === view.id ? (
                <ViewActionsMenu
                  view={view}
                  onClose={() => setMenuOpen(null)}
                  onEdit={onEdit}
                  onDuplicate={onDuplicate}
                  onSetDefault={onSetDefault}
                  onDelete={onDelete}
                />
              ) : null}
            </li>
          );
        })}
        <li>
          <button
            type="button"
            className="contact-views-tab-add"
            onClick={onCreate}
            title="Crear vista nueva"
          >
            <Plus size={13} aria-hidden /> Nueva vista
          </button>
        </li>
      </ul>
      {activeView ? (
        <p className="muted small contact-views-tabs-meta">
          {activeView.is_shared
            ? "Vista compartida con el equipo."
            : "Vista privada."}
        </p>
      ) : null}
    </nav>
  );
}

/** Pop-up menu attached to the active tab. Auto-closes when the user
 * picks an action or clicks outside. */
function ViewActionsMenu({
  view,
  onClose,
  onEdit,
  onDuplicate,
  onSetDefault,
  onDelete,
}: {
  view: SavedView;
  onClose: () => void;
  onEdit: (view: SavedView) => void;
  onDuplicate: (view: SavedView) => void;
  onSetDefault: (view: SavedView) => void;
  onDelete: (view: SavedView) => void;
}) {
  return (
    <>
      <div
        className="contact-views-tab-menu-overlay"
        onClick={onClose}
        aria-hidden
      />
      <ul className="contact-views-tab-menu" role="menu">
        <li>
          <button
            type="button"
            onClick={() => {
              onEdit(view);
              onClose();
            }}
          >
            Renombrar / compartir
          </button>
        </li>
        <li>
          <button
            type="button"
            onClick={() => {
              onDuplicate(view);
              onClose();
            }}
          >
            Duplicar
          </button>
        </li>
        <li>
          <button
            type="button"
            onClick={() => {
              onSetDefault(view);
              onClose();
            }}
          >
            {view.is_default ? "Quitar de por defecto" : "Marcar por defecto"}
          </button>
        </li>
        <li>
          <button
            type="button"
            className="danger"
            onClick={() => {
              onDelete(view);
              onClose();
            }}
          >
            Borrar vista
          </button>
        </li>
      </ul>
    </>
  );
}

export { ALL_TAB_ID };
