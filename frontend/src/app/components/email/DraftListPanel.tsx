"use client";

import { Inbox, PenLine, Trash2 } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type EmailDraft,
  deleteEmailDraft,
  listEmailDrafts,
} from "../../lib/emailsApi";
import { parseBackendDate } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  /** Bumped by the layout's `refreshAll` so a send / discard in the
   *  right pane triggers a refetch here. */
  refreshKey: number;
  /** Notify the layout to refresh sidebar counts after a discard. */
  onChanged: () => void;
};

function formatRelative(value: string): string {
  const d = parseBackendDate(value);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString("es-ES", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  if (d.getFullYear() === now.getFullYear()) {
    return d.toLocaleDateString("es-ES", {
      day: "2-digit",
      month: "short",
    });
  }
  return d.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

/** Middle-column drafts list. Replaces `<EmailThreadList>` when the
 *  current pathname is `/emails/drafts` so "Borradores" behaves like
 *  every other system view (the middle pane filters to drafts; the
 *  right pane shows the selected one). Clicking a row sets `?id=...`
 *  in the URL; the right-pane `DraftsPage` reacts and opens the
 *  composer pre-hydrated with the chosen draft. */
export function DraftListPanel({ refreshKey, onChanged }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const selectedDraftId = params.get("id");

  const [drafts, setDrafts] = useState<EmailDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const lastClickedIdx = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const items = await listEmailDrafts();
      setDrafts(items);
      setSelected((prev) => {
        const next = new Set<string>();
        for (const d of items) if (prev.has(d.id)) next.add(d.id);
        return next;
      });
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudieron cargar los borradores."),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  const toggleSelect = useCallback(
    (id: string, idx: number, shift: boolean) => {
      setSelected((prev) => {
        const next = new Set(prev);
        if (shift && lastClickedIdx.current !== null) {
          const [from, to] = [lastClickedIdx.current, idx].sort(
            (a, b) => a - b,
          );
          const slice = drafts.slice(from, to + 1).map((d) => d.id);
          const allSelected = slice.every((sid) => next.has(sid));
          for (const sid of slice) {
            if (allSelected) next.delete(sid);
            else next.add(sid);
          }
        } else if (next.has(id)) {
          next.delete(id);
        } else {
          next.add(id);
        }
        return next;
      });
      lastClickedIdx.current = idx;
    },
    [drafts],
  );

  const allVisibleSelected =
    drafts.length > 0 && drafts.every((d) => selected.has(d.id));
  const someSelected = selected.size > 0;

  const toggleSelectAll = useCallback(() => {
    setSelected((prev) => {
      if (allVisibleSelected) return new Set();
      const next = new Set(prev);
      for (const d of drafts) next.add(d.id);
      return next;
    });
  }, [allVisibleSelected, drafts]);

  const openDraft = useCallback(
    (id: string) => {
      const sp = new URLSearchParams(params.toString());
      sp.set("id", id);
      router.push(`${pathname}?${sp.toString()}`);
    },
    [params, pathname, router],
  );

  const onDiscard = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0) return;
      const message =
        ids.length === 1
          ? "¿Descartar este borrador?"
          : `¿Descartar ${ids.length} borradores?`;
      if (!confirm(message)) return;
      setBusy(true);
      try {
        await Promise.all(ids.map((id) => deleteEmailDraft(id)));
        if (selectedDraftId && ids.includes(selectedDraftId)) {
          // Clear the right-pane preview if we just discarded it.
          const sp = new URLSearchParams(params.toString());
          sp.delete("id");
          const qs = sp.toString();
          router.replace(qs ? `${pathname}?${qs}` : pathname);
        }
        setSelected(new Set());
        await load();
        onChanged();
      } catch (err) {
        setError(extractErrorMessage(err, "No se pudo descartar."));
      } finally {
        setBusy(false);
      }
    },
    [load, onChanged, params, pathname, router, selectedDraftId],
  );

  return (
    <div className="email-list-pane">
      <div className="email-list-toolbar">
        <label className="email-list-selectall">
          <input
            type="checkbox"
            checked={allVisibleSelected}
            onChange={toggleSelectAll}
            aria-label="Seleccionar todos los borradores visibles"
          />
        </label>
        <span className="email-list-title">
          <PenLine size={13} aria-hidden /> Borradores
        </span>
      </div>

      {someSelected ? (
        <div className="email-bulk-bar" role="region" aria-label="Acciones masivas">
          <span className="email-bulk-count">
            {selectedIds.length} seleccionado
            {selectedIds.length > 1 ? "s" : ""}
          </span>
          <button
            type="button"
            className="email-bulk-btn"
            disabled={busy}
            onClick={() => onDiscard(selectedIds)}
            title="Descartar"
          >
            <Trash2 size={13} aria-hidden />
            <span className="email-bulk-btn-label">Descartar</span>
          </button>
          <button
            type="button"
            className="email-bulk-clear"
            onClick={() => setSelected(new Set())}
            disabled={busy}
          >
            Limpiar
          </button>
        </div>
      ) : null}

      {error ? <p className="form-error">{error}</p> : null}

      {loading ? (
        <p className="muted email-list-empty">Cargando…</p>
      ) : drafts.length === 0 ? (
        <p className="muted email-list-empty">
          <Inbox size={14} aria-hidden /> No tienes ningún borrador.
        </p>
      ) : (
        <ul className="email-list-items">
          {drafts.map((d, idx) => {
            const isSelected = selected.has(d.id);
            const isOpen = selectedDraftId === d.id;
            const subject = d.subject || "(sin asunto)";
            const to = d.to_emails[0] ?? "(sin destinatario)";
            const snippet = d.body_text
              ? d.body_text.slice(0, 160)
              : d.body_html
                ? d.body_html.replace(/<[^>]+>/g, " ").slice(0, 160)
                : "";
            return (
              <li
                key={d.id}
                className={[
                  "email-list-row",
                  isSelected ? "is-selected" : "",
                  isOpen ? "is-open" : "",
                ].join(" ")}
              >
                <label
                  className="email-list-check"
                  onClick={(e) => e.stopPropagation()}
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={(e) => {
                      const shift =
                        (e.nativeEvent as MouseEvent).shiftKey ?? false;
                      toggleSelect(d.id, idx, shift);
                    }}
                    onClick={(e) => {
                      const me = e as unknown as MouseEvent;
                      if (me.shiftKey) {
                        e.preventDefault();
                        toggleSelect(d.id, idx, true);
                      }
                    }}
                    aria-label={`Seleccionar ${subject}`}
                  />
                </label>
                <button
                  type="button"
                  className="email-list-link email-list-draft-link"
                  onClick={() => openDraft(d.id)}
                >
                  <span className="email-list-contact">
                    Para: {to}
                  </span>
                  <span className="email-list-subject">
                    {subject}
                    {snippet ? (
                      <>
                        <span className="email-subject-sep"> · </span>
                        <span className="email-snippet">{snippet}</span>
                      </>
                    ) : null}
                  </span>
                  <span className="email-list-meta">
                    <span className="email-list-date muted small">
                      {formatRelative(d.updated_at)}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
