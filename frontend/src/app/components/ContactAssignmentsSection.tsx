"use client";

import { Plus, Star, Trash2, UserCog, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  type ContactAssignment,
  type User,
  assignContactToUser,
  deleteAssignment,
  getCurrentUser,
  getUsers,
  listContactAssignments,
  promoteAssignment,
} from "../lib/api";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { extractErrorMessage } from "../lib/errors";

type Props = { contactId: string };

/**
 * Sprint Reglas-Assign PR-D. Sección "Comerciales asignados" de la
 * ficha. Reemplaza al PATCH /api/contacts {owner_user_id} legacy con
 * el CRUD multi-comercial (primary + secundarios) introducido en PR-A
 * / PR-B / PR-C.
 *
 * Acciones:
 *   - "Asignarme" si el usuario actual no está en la lista — añade
 *     una row; si no hay primary, entra como primary, si ya hay,
 *     entra como secundario.
 *   - "Asignar a otro" con picker de usuarios (server-side search) →
 *     siempre como secundario (la promoción es un click después).
 *   - Estrella ⭐ para promover una row a primary (degrada al actual).
 *   - X para eliminar (si era el primary y queda alguien, el primero
 *     pasa a primary; si no, owner_user_id queda NULL).
 */
export function ContactAssignmentsSection({ contactId }: Props) {
  const [items, setItems] = useState<ContactAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [me, setMe] = useState<User | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  const [pickerUsers, setPickerUsers] = useState<User[]>([]);
  const [pickerLoading, setPickerLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listContactAssignments(contactId));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las asignaciones."));
    } finally {
      setLoading(false);
    }
  }, [contactId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser()
      .then((u) => {
        if (!cancelled) setMe(u);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!pickerOpen) return;
    let cancelled = false;
    setPickerLoading(true);
    getUsers({ q: debouncedQuery || undefined, limit: 50 })
      .then((users) => {
        if (cancelled) return;
        const taken = new Set(items.map((a) => a.user_id));
        setPickerUsers(
          users.filter((u) => u.is_active && !taken.has(u.id)),
        );
      })
      .catch(() => {
        if (!cancelled) setPickerUsers([]);
      })
      .finally(() => {
        if (!cancelled) setPickerLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [pickerOpen, debouncedQuery, items]);

  const userAlreadyInList = me && items.some((a) => a.user_id === me.id);
  const hasPrimary = items.some((a) => a.is_primary);

  const onAssignMe = async () => {
    if (!me) return;
    try {
      // Si no hay primary, entra como primary (asume "yo me hago cargo").
      // Si ya hay, entra como secundario (asume "yo apoyo").
      await assignContactToUser(contactId, me.id, { isPrimary: !hasPrimary });
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo asignar."));
    }
  };

  const onAssignOther = async (userId: string) => {
    try {
      await assignContactToUser(contactId, userId, { isPrimary: false });
      setPickerOpen(false);
      setQuery("");
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo asignar."));
    }
  };

  const onPromote = async (assignmentId: string) => {
    try {
      await promoteAssignment(contactId, assignmentId);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo promover."));
    }
  };

  const onRemove = async (assignmentId: string) => {
    if (!confirm("¿Quitar esta asignación?")) return;
    try {
      await deleteAssignment(contactId, assignmentId);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo quitar la asignación."));
    }
  };

  return (
    <section className="contact-card">
      <h4>
        <UserCog size={12} aria-hidden /> Comerciales asignados
      </h4>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : items.length === 0 ? (
        <p className="muted small">Sin asignaciones.</p>
      ) : (
        <ul className="contact-channel-list">
          {items.map((row) => {
            const isMe = me?.id === row.user_id;
            const ruleHint = row.source.startsWith("rule:");
            return (
              <li key={row.id} className="contact-channel-row">
                <button
                  type="button"
                  className={`contact-channel-primary${row.is_primary ? " is-on" : ""}`}
                  onClick={() => onPromote(row.id)}
                  title={row.is_primary ? "Primary" : "Promover a primary"}
                  disabled={row.is_primary}
                  aria-label={row.is_primary ? "Primary" : "Promover a primary"}
                >
                  <Star
                    size={12}
                    aria-hidden
                    fill={row.is_primary ? "#facc15" : "none"}
                    color={row.is_primary ? "#facc15" : "#cbd5e1"}
                  />
                </button>
                <div className="contact-assignment-info">
                  <strong>
                    {row.user.full_name || row.user.email}
                    {isMe ? <span className="muted small"> (tú)</span> : null}
                  </strong>
                  <span className="muted small">
                    {row.is_primary ? "Primary" : "Secundario"}
                    {ruleHint ? " · regla automática" : null}
                    {row.source === "backfill" ? " · histórico" : null}
                    {row.source.startsWith("brevo:") ? " · Brevo" : null}
                    {row.source.startsWith("agile:") ? " · Agile" : null}
                  </span>
                </div>
                <button
                  type="button"
                  className="btn small"
                  onClick={() => onRemove(row.id)}
                  title="Quitar asignación"
                  aria-label="Quitar asignación"
                >
                  <Trash2 size={11} aria-hidden />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div className="contact-assignment-actions">
        {me && !userAlreadyInList ? (
          <button
            type="button"
            className="btn small"
            onClick={onAssignMe}
          >
            <Plus size={11} aria-hidden /> Asignarme
          </button>
        ) : null}
        <button
          type="button"
          className="btn small"
          onClick={() => {
            setPickerOpen((open) => !open);
            setQuery("");
          }}
        >
          <Plus size={11} aria-hidden /> Asignar a otro
        </button>
      </div>

      {pickerOpen ? (
        <div className="contact-assignment-picker">
          <div className="contact-assignment-picker-header">
            <input
              type="text"
              placeholder="Buscar usuario…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
            <button
              type="button"
              className="btn small"
              onClick={() => {
                setPickerOpen(false);
                setQuery("");
              }}
              aria-label="Cerrar"
            >
              <X size={11} aria-hidden />
            </button>
          </div>
          {pickerLoading ? (
            <p className="muted small">Cargando…</p>
          ) : pickerUsers.length === 0 ? (
            <p className="muted small">
              {query ? "Sin resultados." : "Todos los usuarios activos ya están asignados."}
            </p>
          ) : (
            <ul className="contact-assignment-picker-list">
              {pickerUsers.map((u) => (
                <li key={u.id}>
                  <button
                    type="button"
                    className="contact-assignment-picker-item"
                    onClick={() => onAssignOther(u.id)}
                  >
                    <strong>{u.full_name || u.email}</strong>
                    {u.full_name ? (
                      <span className="muted small">{u.email}</span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </section>
  );
}
