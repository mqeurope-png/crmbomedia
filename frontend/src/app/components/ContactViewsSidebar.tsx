"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { listSegments, type SavedView, type Segment } from "../lib/api";

type Props = {
  views: SavedView[];
  activeId: string | null;
  onSelect: (view: SavedView) => void;
  onCreate: () => void;
  onEdit: (view: SavedView) => void;
  onDuplicate: (view: SavedView) => void;
  onSetDefault: (view: SavedView) => void;
  onDelete: (view: SavedView) => void;
};

/**
 * Left rail listing the operator's own views first, then any shared
 * row from another owner. Each row has a kebab menu that opens to the
 * applicable actions — edit/delete only show for owners; duplicate
 * and set-default are universal so a shared view can be promoted into
 * an operator's own default by going via the duplicate path.
 */
export function ContactViewsSidebar({
  views,
  activeId,
  onSelect,
  onCreate,
  onEdit,
  onDuplicate,
  onSetDefault,
  onDelete,
}: Props) {
  const own = views.filter((view) => view.is_owner);
  const shared = views.filter((view) => !view.is_owner);

  return (
    <aside className="views-sidebar" aria-label="Vistas guardadas">
      <div className="views-sidebar-header">
        <strong>Vistas</strong>
        <button type="button" className="button small" onClick={onCreate}>
          + Nueva
        </button>
      </div>
      <ViewsSection
        title="Mis vistas"
        empty="Aún no tienes vistas guardadas."
        views={own}
        activeId={activeId}
        onSelect={onSelect}
        onEdit={onEdit}
        onDuplicate={onDuplicate}
        onSetDefault={onSetDefault}
        onDelete={onDelete}
      />
      <MySegmentsSection />
      <ViewsSection
        title="Vistas compartidas"
        empty="Ningún compañero ha compartido vistas."
        views={shared}
        activeId={activeId}
        onSelect={onSelect}
        onEdit={onEdit}
        onDuplicate={onDuplicate}
        onSetDefault={onSetDefault}
        onDelete={onDelete}
      />
    </aside>
  );
}

function ViewsSection({
  title,
  empty,
  views,
  activeId,
  onSelect,
  onEdit,
  onDuplicate,
  onSetDefault,
  onDelete,
}: {
  title: string;
  empty: string;
  views: SavedView[];
  activeId: string | null;
  onSelect: (view: SavedView) => void;
  onEdit: (view: SavedView) => void;
  onDuplicate: (view: SavedView) => void;
  onSetDefault: (view: SavedView) => void;
  onDelete: (view: SavedView) => void;
}) {
  return (
    <section className="views-section">
      <h3>{title}</h3>
      {views.length === 0 ? (
        <p className="muted small">{empty}</p>
      ) : (
        <ul>
          {views.map((view) => (
            <ViewRow
              key={view.id}
              view={view}
              isActive={view.id === activeId}
              onSelect={onSelect}
              onEdit={onEdit}
              onDuplicate={onDuplicate}
              onSetDefault={onSetDefault}
              onDelete={onDelete}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function ViewRow({
  view,
  isActive,
  onSelect,
  onEdit,
  onDuplicate,
  onSetDefault,
  onDelete,
}: {
  view: SavedView;
  isActive: boolean;
  onSelect: (view: SavedView) => void;
  onEdit: (view: SavedView) => void;
  onDuplicate: (view: SavedView) => void;
  onSetDefault: (view: SavedView) => void;
  onDelete: (view: SavedView) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <li className={`view-row${isActive ? " is-active" : ""}`}>
      <button
        type="button"
        className="view-row-main"
        onClick={() => onSelect(view)}
        aria-pressed={isActive}
      >
        <div className="view-row-title">
          <span>{view.name}</span>
          {view.is_default ? (
            <span className="view-row-badge view-row-badge-default">★</span>
          ) : null}
          {view.is_shared && view.is_owner ? (
            <span className="view-row-badge view-row-badge-shared">↗</span>
          ) : null}
        </div>
        {view.description ? (
          <span className="view-row-description muted small">
            {view.description}
          </span>
        ) : null}
      </button>
      <button
        type="button"
        className="view-row-menu-trigger"
        aria-label="Acciones de la vista"
        onClick={() => setMenuOpen((value) => !value)}
      >
        ⋮
      </button>
      {menuOpen ? (
        <div className="view-row-menu" role="menu">
          {view.is_owner ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                onEdit(view);
              }}
            >
              Editar
            </button>
          ) : null}
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setMenuOpen(false);
              onDuplicate(view);
            }}
          >
            Duplicar
          </button>
          {view.is_owner ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                onSetDefault(view);
              }}
            >
              {view.is_default
                ? "Quitar como default"
                : "Marcar como default"}
            </button>
          ) : null}
          {view.is_owner ? (
            <button
              type="button"
              role="menuitem"
              className="danger-text"
              onClick={() => {
                setMenuOpen(false);
                onDelete(view);
              }}
            >
              Eliminar
            </button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function MySegmentsSection() {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listSegments()
      .then(setSegments)
      .catch(() => setError("Segmentos no disponibles"));
  }, []);

  return (
    <section className="views-section">
      <h3>Mis segmentos</h3>
      {error ? (
        <p className="muted small">{error}</p>
      ) : segments.length === 0 ? (
        <p className="muted small">
          Crea segmentos desde la pantalla de <Link href="/segments">Segmentos</Link>.
        </p>
      ) : (
        <ul>
          {segments.slice(0, 8).map((segment) => (
            <li key={segment.id} className="view-row">
              <Link
                href={`/segments/${segment.id}`}
                className="view-row-main segment-sidebar-section"
              >
                <span className="view-row-title">
                  {segment.color ? (
                    <span
                      className="tag-color-swatch"
                      style={{ background: segment.color }}
                      aria-hidden
                    />
                  ) : null}
                  <span>{segment.name}</span>
                </span>
                <span className="muted small">
                  {segment.cached_count ?? "?"} contactos
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
