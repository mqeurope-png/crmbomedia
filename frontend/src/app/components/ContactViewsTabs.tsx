"use client";

import { Plus, Settings2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
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

/** Brevo-style horizontal tabs of saved views.
 *
 * The cogwheel on each tab opens a portalled dropdown — the previous
 * `position: absolute` implementation was being clipped by the
 * list's `overflow-x: auto` (rendering the menu invisible while
 * leaving the horizontal scrollbar as a phantom "arrow"). Portalling
 * to `document.body` sidesteps the clip and pins the menu to the
 * cogwheel's bounding rect.
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
  const buttonRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const activeView = views.find((v) => v.id === activeId) ?? null;
  const isAllActive = activeId === null;

  const openMenuView = menuOpen
    ? views.find((v) => v.id === menuOpen) ?? null
    : null;
  const anchor = menuOpen ? buttonRefs.current[menuOpen] ?? null : null;

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
                ref={(el) => {
                  buttonRefs.current[view.id] = el;
                }}
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
      {openMenuView && anchor ? (
        <PortalledViewActionsMenu
          view={openMenuView}
          anchor={anchor}
          onClose={() => setMenuOpen(null)}
          onEdit={onEdit}
          onDuplicate={onDuplicate}
          onSetDefault={onSetDefault}
          onDelete={onDelete}
        />
      ) : null}
    </nav>
  );
}

/** Dropdown rendered via React Portal so the parent's
 * `overflow-x: auto` (the horizontal-scrollable tabs list) doesn't
 * clip it. Positioned absolutely against the cogwheel's bounding
 * rect, re-measured each open. */
function PortalledViewActionsMenu({
  view,
  anchor,
  onClose,
  onEdit,
  onDuplicate,
  onSetDefault,
  onDelete,
}: {
  view: SavedView;
  anchor: HTMLElement;
  onClose: () => void;
  onEdit: (view: SavedView) => void;
  onDuplicate: (view: SavedView) => void;
  onSetDefault: (view: SavedView) => void;
  onDelete: (view: SavedView) => void;
}) {
  const [coords, setCoords] = useState<{ top: number; right: number } | null>(
    null,
  );

  useEffect(() => {
    function position() {
      const rect = anchor.getBoundingClientRect();
      setCoords({
        top: rect.bottom + window.scrollY + 4,
        right: window.innerWidth - rect.right - window.scrollX,
      });
    }
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => {
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
    };
  }, [anchor]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (typeof document === "undefined") return null;
  if (!coords) return null;

  return createPortal(
    <>
      <div
        className="contact-views-tab-menu-overlay"
        onClick={onClose}
        aria-hidden
      />
      <ul
        className="contact-views-tab-menu"
        role="menu"
        style={{
          position: "fixed",
          top: coords.top,
          right: coords.right,
        }}
      >
        <li>
          <button
            type="button"
            onClick={() => {
              onSetDefault(view);
              onClose();
            }}
          >
            {view.is_default ? "Quitar de por defecto" : "Marcar como predeterminada"}
          </button>
        </li>
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
    </>,
    document.body,
  );
}

export { ALL_TAB_ID };
