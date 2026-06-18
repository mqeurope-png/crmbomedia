"use client";

/**
 * PR-Ficha-Cleanup. Card "Tags" en pestaña Resumen del contacto.
 *
 * Pre-cleanup: las tags vivían en el strip; el operador veía 3 chips
 * + "+N" sin tooltip útil. Ahora hay una pestaña dedicada (Tags) con
 * el editor completo; este card resume las 8 primeras en el Resumen
 * y enlaza a la pestaña si hay más.
 */
import { ArrowUpRight, Tag as TagIcon } from "lucide-react";
import type { Tag } from "../../lib/api";

type Props = {
  tags: Tag[];
  onSeeAll?: () => void;
};

const MAX_VISIBLE = 8;

export function ContactTagsPreviewCard({ tags, onSeeAll }: Props) {
  const visible = tags.slice(0, MAX_VISIBLE);
  const extra = Math.max(0, tags.length - visible.length);
  return (
    <article className="card contact-summary-card">
      <header className="contact-summary-card-header">
        <h3>
          <TagIcon size={14} aria-hidden /> Tags
        </h3>
      </header>
      {tags.length === 0 ? (
        <p className="muted small">Sin tags todavía.</p>
      ) : (
        <ul className="contact-tags-preview-list">
          {visible.map((tag) => (
            <li
              key={tag.id}
              className="contact-strip-tag"
              style={{
                ["--tag-color" as string]: tag.color ?? "var(--color-primary)",
              }}
            >
              {tag.name}
            </li>
          ))}
          {extra > 0 ? (
            <li
              className="contact-strip-tag is-extra"
              title={tags
                .slice(MAX_VISIBLE)
                .map((t) => t.name)
                .join(", ")}
            >
              +{extra}
            </li>
          ) : null}
        </ul>
      )}
      {onSeeAll && tags.length > 0 ? (
        <button
          type="button"
          className="contact-summary-link"
          onClick={onSeeAll}
        >
          Ver todas <ArrowUpRight size={12} aria-hidden />
        </button>
      ) : null}
    </article>
  );
}
