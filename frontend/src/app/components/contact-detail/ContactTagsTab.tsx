"use client";

/**
 * PR-Ficha-Cleanup. Pestaña "Tags" del detalle contacto.
 *
 * Pre-cleanup: las etiquetas estaban embutidas en el strip horizontal
 * con un max de 3 chips visibles + "+N". Los comerciales con > 5 tags
 * acababan viendo "+N" inútil. Movemos a una pestaña dedicada con
 * lista completa + editor.
 *
 * UX:
 *   - Chips grandes (no `is-dense`) para que se lean cómodos.
 *   - Cada chip lleva un ✕ para quitar (save-on-click, sin botón
 *     "guardar" general — la operación es atómica por tag).
 *   - TagPicker abajo del listado para añadir nuevas con autocomplete
 *     + crear inline.
 */
import { X } from "lucide-react";
import type { Tag } from "../../lib/api";
import { TagPicker } from "../TagPicker";

type Props = {
  tags: Tag[];
  onAddTag: (choice: { tag_id?: string; tag_name?: string }) => Promise<void>;
  onRemoveTag: (tagId: string) => Promise<void>;
};

export function ContactTagsTab({ tags, onAddTag, onRemoveTag }: Props) {
  return (
    <section className="contact-tags-tab">
      <header className="contact-tags-tab-header">
        <h2>Tags del contacto</h2>
        <p className="muted small">
          {tags.length === 0
            ? "Aún no hay tags. Usa el buscador para añadir o crear una nueva."
            : `${tags.length} tag${tags.length === 1 ? "" : "s"} aplicada${
                tags.length === 1 ? "" : "s"
              }.`}
        </p>
      </header>

      {tags.length > 0 ? (
        <ul className="contact-tags-list">
          {tags.map((tag) => (
            <li
              key={tag.id}
              className="contact-tags-list-item"
              style={{
                ["--tag-color" as string]: tag.color ?? "var(--color-primary)",
              }}
            >
              <span className="contact-tags-list-name">{tag.name}</span>
              <button
                type="button"
                className="contact-tags-list-remove"
                aria-label={`Quitar tag ${tag.name}`}
                onClick={() => onRemoveTag(tag.id)}
              >
                <X size={11} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="contact-tags-add">
        <span className="contact-tags-add-label">Añadir tag</span>
        <TagPicker
          excludeTagIds={tags.map((t) => t.id)}
          onPick={async (choice) => {
            await onAddTag(choice);
          }}
        />
      </div>
    </section>
  );
}
