"use client";

/**
 * Sprint Filtros & Listas (PR-C) — `<EntityViewsTabs>` genérica.
 *
 * Tabs horizontales tipo Brevo: "Todas" + una pestaña por vista
 * guardada (`/api/entity-views/{entity}`), con menú de acciones
 * portalled en cada pestaña (renombrar, duplicar, default, borrar).
 *
 * Heredera del `ContactViewsTabs`: misma UX, mismo patrón portal, pero
 * tipada contra `EntityView` y agnóstica de la entidad. El componente
 * solo presenta + emite callbacks; la pantalla decide qué hacer al
 * seleccionar, editar, etc.
 */
import { Plus, Settings2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { EntityView } from "../../lib/entityViewsApi";

type Props = {
  views: EntityView[];
  activeId: string | null;
  /** Tweaked-away-from-saved badge on the active tab. */
  isDirty: boolean;
  onSelect: (view: EntityView | null) => void;
  onCreate: () => void;
  onEdit: (view: EntityView) => void;
  onDuplicate: (view: EntityView) => void;
  onSetDefault: (view: EntityView) => void;
  onDelete: (view: EntityView) => void;
};

const ALL_TAB_ID = "__all__";

export function EntityViewsTabs({
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
  const [menuAnchor, setMenuAnchor] = useState<{
    view: EntityView;
    element: HTMLElement;
  } | null>(null);

  return (
    <nav className="entity-views-tabs" aria-label="Vistas guardadas">
      <button
        type="button"
        className={`entity-views-tab${
          activeId === null || activeId === ALL_TAB_ID
            ? " is-active"
            : ""
        }`}
        onClick={() => onSelect(null)}
      >
        Todas
      </button>
      {views.map((view) => {
        const isActive = view.id === activeId;
        return (
          <span
            key={view.id}
            className={`entity-views-tab-wrap${isActive ? " is-active" : ""}`}
          >
            <button
              type="button"
              className={`entity-views-tab${isActive ? " is-active" : ""}`}
              onClick={() => onSelect(view)}
            >
              {view.name}
              {view.is_default ? " ★" : ""}
              {isActive && isDirty ? " ·" : ""}
              {!view.is_owner && view.is_shared ? (
                <span className="muted small entity-views-tab-shared">
                  {" "}(compartida)
                </span>
              ) : null}
            </button>
            {view.is_owner ? (
              <CogwheelButton
                onOpen={(element) => setMenuAnchor({ view, element })}
              />
            ) : null}
          </span>
        );
      })}
      <button
        type="button"
        className="entity-views-tab-new"
        onClick={onCreate}
        title="Nueva vista"
      >
        <Plus size={11} aria-hidden /> Nueva vista
      </button>

      {menuAnchor ? (
        <PortalledMenu
          view={menuAnchor.view}
          anchor={menuAnchor.element}
          onClose={() => setMenuAnchor(null)}
          onEdit={onEdit}
          onDuplicate={onDuplicate}
          onSetDefault={onSetDefault}
          onDelete={onDelete}
        />
      ) : null}
    </nav>
  );
}

function CogwheelButton({
  onOpen,
}: {
  onOpen: (element: HTMLElement) => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  return (
    <button
      ref={ref}
      type="button"
      className="entity-views-tab-cog"
      onClick={() => ref.current && onOpen(ref.current)}
      title="Acciones de vista"
      aria-label="Acciones de vista"
    >
      <Settings2 size={11} aria-hidden />
    </button>
  );
}

function PortalledMenu({
  view,
  anchor,
  onClose,
  onEdit,
  onDuplicate,
  onSetDefault,
  onDelete,
}: {
  view: EntityView;
  anchor: HTMLElement;
  onClose: () => void;
  onEdit: (view: EntityView) => void;
  onDuplicate: (view: EntityView) => void;
  onSetDefault: (view: EntityView) => void;
  onDelete: (view: EntityView) => void;
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
        className="entity-views-menu-overlay"
        onClick={onClose}
        aria-hidden
      />
      <ul
        className="entity-views-menu"
        role="menu"
        style={{ position: "fixed", top: coords.top, right: coords.right }}
      >
        <li>
          <button
            type="button"
            onClick={() => {
              onSetDefault(view);
              onClose();
            }}
          >
            {view.is_default
              ? "Quitar de por defecto"
              : "Marcar como predeterminada"}
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
