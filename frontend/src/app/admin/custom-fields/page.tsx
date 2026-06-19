"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { apiFetch } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

type CustomFieldDefinition = {
  id: string;
  key: string;
  label: string | null;
  type: string;
  source: string;
  description: string | null;
};

type Draft = {
  key: string;
  label: string;
  type: string;
  description: string;
};

const EMPTY_DRAFT: Draft = {
  key: "",
  label: "",
  type: "text",
  description: "",
};

/**
 * PR-Fixes-Pase-4 Bug 6. Admin de custom fields manuales.
 *
 * Hasta el PR #208 el dropdown del editor de workflows solo veía los
 * campos importados de AgileCRM porque escaneaba `contacts.custom_fields`.
 * Aquí los admins crean definiciones manuales que aparecen en el
 * dropdown aunque no haya ningún contacto que las use todavía.
 */
export default function CustomFieldsAdminPage() {
  const [definitions, setDefinitions] = useState<CustomFieldDefinition[]>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const rows = await apiFetch<CustomFieldDefinition[]>(
        "/api/admin/custom-fields",
      );
      setDefinitions(rows);
      setError(null);
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudieron cargar los custom fields."),
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.key.trim()) return;
    setSaving(true);
    try {
      await apiFetch("/api/admin/custom-fields", {
        method: "POST",
        body: JSON.stringify({
          key: draft.key.trim(),
          label: draft.label.trim() || null,
          type: draft.type,
          description: draft.description.trim() || null,
        }),
      });
      setDraft(EMPTY_DRAFT);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el custom field."));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(definition: CustomFieldDefinition) {
    if (
      !window.confirm(
        `¿Borrar el custom field "${definition.key}"? Los valores ya almacenados en contactos NO se borrarán.`,
      )
    ) {
      return;
    }
    try {
      await apiFetch(`/api/admin/custom-fields/${definition.key}`, {
        method: "DELETE",
      });
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar el custom field."));
    }
  }

  return (
    <div className="page">
      <PageHeader
        title="Custom fields"
        description="Definiciones manuales de campos personalizados del CRM. Aparecen en el editor de workflows aunque ningún contacto los use todavía."
      />

      {error ? <ErrorState title="Error" message={error} /> : null}

      <div className="form-card">
        <h3>Crear nuevo</h3>
        <form onSubmit={handleSubmit} className="form-grid">
          <label>
            Key (sin espacios)
            <input
              type="text"
              value={draft.key}
              onChange={(e) => setDraft({ ...draft, key: e.target.value })}
              placeholder="sector_empresa"
              required
              maxLength={120}
            />
          </label>
          <label>
            Etiqueta humana (opcional)
            <input
              type="text"
              value={draft.label}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })}
              placeholder="Sector de la empresa"
              maxLength={200}
            />
          </label>
          <label>
            Tipo
            <select
              value={draft.type}
              onChange={(e) => setDraft({ ...draft, type: e.target.value })}
            >
              <option value="text">Texto</option>
              <option value="number">Número</option>
              <option value="date">Fecha</option>
              <option value="boolean">Sí / No</option>
            </select>
          </label>
          <label>
            Descripción (opcional)
            <textarea
              rows={2}
              value={draft.description}
              onChange={(e) =>
                setDraft({ ...draft, description: e.target.value })
              }
            />
          </label>
          <button type="submit" className="button" disabled={saving}>
            {saving ? "Guardando…" : "Crear custom field"}
          </button>
        </form>
      </div>

      <div className="form-card">
        <h3>Definiciones existentes</h3>
        {isLoading ? (
          <p className="muted">Cargando…</p>
        ) : definitions.length === 0 ? (
          <p className="muted">No hay custom fields manuales todavía.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Key</th>
                <th>Etiqueta</th>
                <th>Tipo</th>
                <th>Origen</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {definitions.map((d) => (
                <tr key={d.id}>
                  <td>
                    <code>{d.key}</code>
                  </td>
                  <td>{d.label || <span className="muted">—</span>}</td>
                  <td>{d.type}</td>
                  <td>{d.source}</td>
                  <td>
                    <button
                      type="button"
                      className="button secondary small"
                      onClick={() => handleDelete(d)}
                    >
                      Borrar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
