"use client";

import { Phone, Plus, Star, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  type ContactPhone,
  createContactPhone,
  deleteContactPhone,
  listContactPhones,
  setPrimaryPhone,
  updateContactPhone,
} from "../lib/contactChannelsApi";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  contactId: string;
  // Bug 11: callback opcional para que el page se entere del cambio
  // y rehydrate `primaryPhone` que pinta en la cabecera.
  onChanged?: () => void;
};

export function ContactPhonesSection({ contactId, onChanged }: Props) {
  const [items, setItems] = useState<ContactPhone[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ label: "", number: "" });

  // PR-Fix-Regresiones-PR237 — LOOP CRÍTICO en PR #237.
  //
  // El antiguo `load` llamaba a `onChanged?.()` en cada fetch (incluido
  // el initial). Como el parent pasa `onChanged` como arrow inline
  // (referencia nueva cada render), y `load` era `useCallback([contactId,
  // onChanged])`, el flujo era:
  //
  //   useEffect → load() → onChanged() → parent re-render →
  //   new onChanged ref → load rebuilt → useEffect re-runs → LOOP
  //
  // Fix:
  // 1. `load` SOLO actualiza estado interno (`items`). NO llama a
  //    `onChanged`.
  // 2. Las mutaciones (add/edit/delete/setPrimary) llaman a `onChanged`
  //    DESPUÉS del refetch local — ahí sí queremos avisar al parent
  //    para que rehydrate la cabecera.
  // 3. `useEffect` depende SOLO de `contactId`, no de `load` ni
  //    `onChanged`. Una sola request al mount o al cambiar de contacto.
  const reload = useCallback(async () => {
    try {
      setItems(await listContactPhones(contactId));
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los teléfonos."));
    }
  }, [contactId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listContactPhones(contactId)
      .then((rows) => {
        if (!cancelled) setItems(rows);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            extractErrorMessage(err, "No se pudieron cargar los teléfonos."),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [contactId]);

  // Notify parent + reload after each mutation. Centralizado en una
  // función para que cada handler (add/edit/delete/setPrimary) la
  // llame en una línea sin riesgo de olvidar el `onChanged`.
  const afterMutation = useCallback(async () => {
    await reload();
    onChanged?.();
  }, [reload, onChanged]);

  const onAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.number.trim()) return;
    try {
      await createContactPhone(contactId, {
        label: draft.label.trim() || null,
        number: draft.number.trim(),
      });
      setAdding(false);
      setDraft({ label: "", number: "" });
      await afterMutation();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo añadir."));
    }
  };

  const onPrimary = async (id: string) => {
    try {
      await setPrimaryPhone(contactId, id);
      await afterMutation();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo marcar primario."));
    }
  };

  const onDelete = async (id: string) => {
    if (!confirm("¿Borrar este teléfono?")) return;
    try {
      await deleteContactPhone(contactId, id);
      await afterMutation();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar."));
    }
  };

  const onChangeLabel = async (row: ContactPhone, label: string) => {
    try {
      await updateContactPhone(contactId, row.id, {
        label: label.trim() || null,
        number: row.number,
        is_primary: row.is_primary,
        source: row.source,
      });
      await afterMutation();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la etiqueta."));
    }
  };

  return (
    <section className="contact-card">
      <h4>
        <Phone size={12} aria-hidden /> Teléfonos
      </h4>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : items.length === 0 && !adding ? (
        <p className="muted small">Sin teléfonos.</p>
      ) : (
        <ul className="contact-channel-list">
          {items.map((row) => (
            <li key={row.id} className="contact-channel-row">
              <button
                type="button"
                className={`contact-channel-primary${row.is_primary ? " is-on" : ""}`}
                onClick={() => onPrimary(row.id)}
                title={row.is_primary ? "Primario" : "Marcar primario"}
              >
                <Star
                  size={12}
                  aria-hidden
                  fill={row.is_primary ? "#facc15" : "none"}
                  color={row.is_primary ? "#facc15" : "#cbd5e1"}
                />
              </button>
              <a href={`tel:${row.number}`}>{row.number}</a>
              <input
                type="text"
                className="contact-channel-label"
                defaultValue={row.label ?? ""}
                onBlur={(e) => {
                  if ((e.target.value || "") !== (row.label ?? "")) {
                    void onChangeLabel(row, e.target.value);
                  }
                }}
                placeholder="etiqueta"
              />
              <button
                type="button"
                className="button secondary small"
                onClick={() => onDelete(row.id)}
              >
                <Trash2 size={11} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <form onSubmit={onAdd} className="contact-channel-add">
          <input
            type="text"
            placeholder="etiqueta (móvil, centralita…)"
            value={draft.label}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
          />
          <input
            type="tel"
            placeholder="+34 600 12 34 56"
            value={draft.number}
            onChange={(e) => setDraft({ ...draft, number: e.target.value })}
            required
            autoFocus
          />
          <button type="submit" className="button small">
            Añadir
          </button>
          <button
            type="button"
            className="button secondary small"
            onClick={() => {
              setAdding(false);
              setDraft({ label: "", number: "" });
            }}
          >
            Cancelar
          </button>
        </form>
      ) : (
        <button
          type="button"
          className="button secondary small"
          onClick={() => setAdding(true)}
        >
          <Plus size={11} aria-hidden /> Añadir teléfono
        </button>
      )}
    </section>
  );
}
