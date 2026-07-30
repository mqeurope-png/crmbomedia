"use client";

import { Code2, ListChecks, Pencil, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { listForms, type WebFormListItem } from "../../lib/formsApi";

/** Lista de formularios web con filtros por marca / idioma / activo. */
export function WebFormsList() {
  const [forms, setForms] = useState<WebFormListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [brand, setBrand] = useState("");
  const [language, setLanguage] = useState("");
  const [onlyActive, setOnlyActive] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listForms()
      .then((rows) => {
        if (!cancelled) setForms(rows);
      })
      .catch((err) => {
        if (!cancelled) setError(extractErrorMessage(err, "No se pudieron cargar los formularios."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const brands = useMemo(
    () => Array.from(new Set(forms.map((f) => f.brand).filter(Boolean))) as string[],
    [forms],
  );
  const languages = useMemo(
    () => Array.from(new Set(forms.map((f) => f.language))),
    [forms],
  );

  const filtered = forms.filter(
    (f) =>
      (!brand || f.brand === brand) &&
      (!language || f.language === language) &&
      (!onlyActive || f.is_active),
  );

  return (
    <div className="wf-list">
      <div className="wf-list-toolbar">
        <div className="wf-list-filters">
          <select value={brand} onChange={(e) => setBrand(e.target.value)} aria-label="Marca">
            <option value="">Todas las marcas</option>
            {brands.map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Idioma">
            <option value="">Todos los idiomas</option>
            {languages.map((l) => (
              <option key={l} value={l}>{l.toUpperCase()}</option>
            ))}
          </select>
          <label className="checkbox-inline small">
            <input type="checkbox" checked={onlyActive} onChange={(e) => setOnlyActive(e.target.checked)} />
            Solo activos
          </label>
        </div>
        <Link href="/admin/forms/new/editor" className="button small">
          <Plus size={13} aria-hidden /> Crear formulario
        </Link>
      </div>

      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : filtered.length === 0 ? (
        <p className="muted">No hay formularios{forms.length ? " con esos filtros" : " todavía"}.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Nombre</th>
              <th>Slug</th>
              <th>Marca</th>
              <th>Idioma</th>
              <th>Submits</th>
              <th>Spam</th>
              <th>Activo</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {filtered.map((f) => (
              <tr key={f.id}>
                <td>{f.name}</td>
                <td className="muted small">{f.slug}</td>
                <td>{f.brand ?? "—"}</td>
                <td>{f.language.toUpperCase()}</td>
                <td>{f.submissions_total}</td>
                <td>{f.submissions_spam}</td>
                <td>{f.is_active ? "Sí" : "No"}</td>
                <td className="wf-row-actions">
                  <Link className="button small secondary" href={`/admin/forms/${f.id}/editor`} title="Editar">
                    <Pencil size={12} aria-hidden />
                  </Link>
                  <Link className="button small secondary" href={`/admin/forms/${f.id}/submissions`} title="Submits">
                    <ListChecks size={12} aria-hidden />
                  </Link>
                  <Link className="button small secondary" href={`/admin/forms/${f.id}/embed`} title="Embed">
                    <Code2 size={12} aria-hidden />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
