"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import {
  createTag,
  deleteTag,
  listTags,
  updateTag,
  type TagDetail,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import { TAG_PALETTE, isPaletteColor } from "../../lib/tagPalette";

type DraftTag = { name: string; color: string | null; description: string };

const EMPTY_DRAFT: DraftTag = { name: "", color: null, description: "" };

export default function TagsAdminPage() {
  const [tags, setTags] = useState<TagDetail[]>([]);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<DraftTag>(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const page = await listTags(query.trim() || undefined);
      setTags(page.items);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar la lista de tags."));
    } finally {
      setIsLoading(false);
    }
  }, [query]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const editing = useMemo(
    () => (editingId ? tags.find((t) => t.id === editingId) ?? null : null),
    [editingId, tags],
  );

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.name.trim()) return;
    try {
      if (editingId) {
        await updateTag(editingId, {
          name: draft.name,
          color: draft.color || null,
          description: draft.description || null,
        });
      } else {
        await createTag({
          name: draft.name,
          color: draft.color || null,
          description: draft.description || null,
        });
      }
      setDraft(EMPTY_DRAFT);
      setEditingId(null);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar el tag."));
    }
  }

  async function handleDelete(tag: TagDetail) {
    if (
      !window.confirm(
        `¿Borrar el tag "${tag.name}"? Se desvinculará de ${tag.contact_count} contacto(s).`,
      )
    )
      return;
    try {
      await deleteTag(tag.id);
      if (editingId === tag.id) {
        setDraft(EMPTY_DRAFT);
        setEditingId(null);
      }
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar el tag."));
    }
  }

  function startEdit(tag: TagDetail) {
    setEditingId(tag.id);
    setDraft({
      name: tag.name,
      color: tag.color || null,
      description: tag.description || "",
    });
  }

  function cancelEdit() {
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
  }

  return (
    <main className="shell">
      <Link href="/" className="back-link">
        ← Volver al dashboard
      </Link>
      <section className="hero compact">
        <p className="eyebrow">Administración</p>
        <h1>Tags de contactos</h1>
        <p className="lead">
          Los tags se comparten entre AgileCRM, importaciones y asignaciones
          manuales. Borrar un tag aquí lo retira de todos los contactos a la vez.
        </p>
      </section>

      <section className="grid two">
        <article className="card">
          <h2>{editing ? `Editar "${editing.name}"` : "Crear tag"}</h2>
          <form onSubmit={handleSubmit} className="stacked-form">
            <label>
              <span>Nombre</span>
              <input
                type="text"
                required
                maxLength={100}
                value={draft.name}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, name: event.target.value }))
                }
              />
            </label>
            <fieldset className="palette-fieldset">
              <legend>Color</legend>
              <div className="palette-grid" role="radiogroup" aria-label="Color del tag">
                <button
                  type="button"
                  role="radio"
                  aria-checked={!draft.color}
                  title="Sin color"
                  className={`palette-swatch palette-swatch-empty${!draft.color ? " is-selected" : ""}`}
                  onClick={() => setDraft((current) => ({ ...current, color: null }))}
                >
                  <span aria-hidden>∅</span>
                </button>
                {TAG_PALETTE.map((swatch) => {
                  const selected = draft.color?.toLowerCase() === swatch.hex;
                  return (
                    <button
                      key={swatch.hex}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      title={swatch.label}
                      className={`palette-swatch${selected ? " is-selected" : ""}`}
                      style={{ background: swatch.hex }}
                      onClick={() =>
                        setDraft((current) => ({
                          ...current,
                          color: swatch.hex,
                        }))
                      }
                    >
                      {selected ? <span aria-hidden>✓</span> : null}
                    </button>
                  );
                })}
              </div>
              {draft.color && !isPaletteColor(draft.color) ? (
                <p className="muted small">
                  Color personalizado heredado: {draft.color}. Selecciona uno de
                  la paleta para reemplazarlo.
                </p>
              ) : null}
            </fieldset>
            <label>
              <span>Descripción</span>
              <textarea
                maxLength={2000}
                rows={3}
                value={draft.description}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    description: event.target.value,
                  }))
                }
              />
            </label>
            <div className="form-actions">
              <button type="submit" className="button">
                {editing ? "Guardar cambios" : "Crear tag"}
              </button>
              {editing ? (
                <button
                  type="button"
                  className="button secondary"
                  onClick={cancelEdit}
                >
                  Cancelar
                </button>
              ) : null}
            </div>
          </form>
        </article>

        <article className="card card-wide">
          <div className="contact-toolbar">
            <input
              type="search"
              className="search-input"
              placeholder="Buscar tag…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          {error ? <ErrorState title="Error" message={error} /> : null}
          {isLoading && tags.length === 0 ? (
            <p className="muted">Cargando…</p>
          ) : tags.length === 0 ? (
            <p className="muted">No hay tags todavía.</p>
          ) : (
            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Color</th>
                    <th>Nombre</th>
                    <th>Descripción</th>
                    <th>Contactos</th>
                    <th aria-label="Acciones" />
                  </tr>
                </thead>
                <tbody>
                  {tags.map((tag) => (
                    <tr key={tag.id}>
                      <td>
                        <span
                          className="tag-color-swatch"
                          style={{ background: tag.color || "#cdd5e1" }}
                          aria-hidden
                        />
                      </td>
                      <td>{tag.name}</td>
                      <td className="muted">{tag.description || "—"}</td>
                      <td>{tag.contact_count}</td>
                      <td>
                        <button
                          type="button"
                          className="button secondary small"
                          onClick={() => startEdit(tag)}
                        >
                          Editar
                        </button>
                        <button
                          type="button"
                          className="button secondary small"
                          onClick={() => handleDelete(tag)}
                        >
                          Borrar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </article>
      </section>
    </main>
  );
}
