"use client";

import { Inbox, Search, Star } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  archiveThread,
  type EmailFolder,
  type EmailLabel,
  type EmailThread,
  type EmailThreadStateValue,
  listEmailThreads,
  markThreadUnread,
  restoreThread,
  starThread,
  trashThread,
  unstarThread,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";
import { useEmailKeyboardShortcuts } from "../../lib/useEmailKeyboardShortcuts";
import { EmailEventBadges } from "./EmailEventBadges";
import { EmailBulkActionsBar } from "./EmailBulkActionsBar";

type Props = {
  folders: EmailFolder[];
  labels: EmailLabel[];
  /** Bumped externally to force a refetch (e.g. after a mutation in
   *  the right pane). */
  refreshKey: number;
};

function formatRelative(value: string): string {
  const d = new Date(value);
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

/** Map a URL `state` param to the typed union the API expects. */
function parseState(raw: string | null): EmailThreadStateValue {
  if (
    raw === "archived" ||
    raw === "trashed" ||
    raw === "spam" ||
    raw === "inbox"
  ) {
    return raw;
  }
  return "inbox";
}

export function EmailThreadList({ folders, labels, refreshKey }: Props) {
  const router = useRouter();
  const params = useSearchParams();
  const routeParams = useParams<{ thread_id?: string }>();
  const openThreadId = routeParams.thread_id ?? null;

  const [threads, setThreads] = useState<EmailThread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState(params.get("q") ?? "");
  const [debounced, setDebounced] = useState(params.get("q") ?? "");
  // Selection management. Set keeps ops O(1); lastClickedIdx powers
  // the Shift-click range-select gesture.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const lastClickedIdx = useRef<number | null>(null);
  // Keyboard cursor — the row j/k navigates over. NULL means "no
  // row focused"; the first j moves it to 0 so the user doesn't
  // have to scroll-then-click to start a session.
  const [cursor, setCursor] = useState<number | null>(null);
  const rowRefs = useRef<(HTMLLIElement | null)[]>([]);

  // Debounce search input by 300 ms.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebounced(searchInput.trim());
    }, 300);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  const state = parseState(params.get("state"));
  const folderId = params.get("folder_id");
  const labelId = params.get("label_id");
  const starred = params.get("starred") === "true" ? true : undefined;

  const fetchThreads = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listEmailThreads(undefined, debounced || undefined, {
        state,
        folder_id: folderId ?? undefined,
        label_id: labelId ?? undefined,
        starred,
      });
      setThreads(page.items);
      // Drop any selection that no longer exists in the new page
      // (e.g. after archiving the previously-selected rows).
      setSelected((prev) => {
        const next = new Set<string>();
        for (const t of page.items) if (prev.has(t.id)) next.add(t.id);
        return next;
      });
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los hilos."));
    } finally {
      setLoading(false);
    }
  }, [debounced, state, folderId, labelId, starred]);

  useEffect(() => {
    fetchThreads();
  }, [fetchThreads, refreshKey]);

  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  const toggleSelect = useCallback(
    (id: string, idx: number, shift: boolean) => {
      setSelected((prev) => {
        const next = new Set(prev);
        if (shift && lastClickedIdx.current !== null) {
          const [from, to] = [lastClickedIdx.current, idx].sort((a, b) => a - b);
          const slice = threads.slice(from, to + 1).map((t) => t.id);
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
    [threads],
  );

  const allVisibleSelected =
    threads.length > 0 && threads.every((t) => selected.has(t.id));
  const someSelected = selected.size > 0;

  const toggleSelectAll = useCallback(() => {
    setSelected((prev) => {
      if (allVisibleSelected) return new Set();
      const next = new Set(prev);
      for (const t of threads) next.add(t.id);
      return next;
    });
  }, [allVisibleSelected, threads]);

  const onStar = useCallback(
    async (t: EmailThread, value: boolean) => {
      // Optimistic update so the star doesn't lag the click.
      setThreads((prev) =>
        prev.map((th) =>
          th.id === t.id ? { ...th, is_starred: value } : th,
        ),
      );
      try {
        if (value) await starThread(t.id);
        else await unstarThread(t.id);
      } catch (err) {
        setThreads((prev) =>
          prev.map((th) =>
            th.id === t.id ? { ...th, is_starred: !value } : th,
          ),
        );
        setError(extractErrorMessage(err, "No se pudo cambiar la estrella."));
      }
    },
    [],
  );

  // The keyboard cursor target — the row j/k navigates over and
  // single-key shortcuts (e/#/s/u/o) act on. Resolves to the focused
  // row in the current page; null when the list is empty.
  const cursorThread = cursor !== null ? threads[cursor] ?? null : null;

  const moveCursor = useCallback(
    (delta: number) => {
      setCursor((prev) => {
        if (threads.length === 0) return null;
        const start = prev ?? -1;
        const next = Math.max(0, Math.min(threads.length - 1, start + delta));
        // Scroll into view on the next tick once the row picks up
        // the `is-cursor` class.
        window.requestAnimationFrame(() => {
          rowRefs.current[next]?.scrollIntoView({
            block: "nearest",
            behavior: "smooth",
          });
        });
        return next;
      });
    },
    [threads.length],
  );

  // Wraps a per-row mutation: locks the API call, refetches, keeps
  // the cursor on the same index so j/k keep working after archive.
  const runOnCursor = useCallback(
    async (fn: (t: EmailThread) => Promise<unknown>) => {
      if (!cursorThread) return;
      try {
        await fn(cursorThread);
        await fetchThreads();
      } catch (err) {
        setError(extractErrorMessage(err, "No se pudo aplicar la acción."));
      }
    },
    [cursorThread, fetchThreads],
  );

  // List-context shortcuts. Disabled while a thread is open so the
  // thread page's own handlers don't double-fire.
  useEmailKeyboardShortcuts(
    {
      onNext: () => moveCursor(1),
      onPrev: () => moveCursor(-1),
      onOpen: () => {
        if (cursorThread) router.push(`/emails/${cursorThread.id}`);
      },
      onArchive: () => runOnCursor((t) => archiveThread(t.id)),
      onTrash: () => runOnCursor((t) => trashThread(t.id)),
      onStar: () =>
        cursorThread && onStar(cursorThread, !cursorThread.is_starred),
      onMarkUnread: () => runOnCursor((t) => markThreadUnread(t.id)),
    },
    !openThreadId,
  );

  // Silence "imported but unused" warnings on the symbols the
  // shortcuts wire up — `restoreThread` is only reached via the
  // thread detail page today, but we keep it imported here so a
  // future "u to restore" mapping in the trashed view doesn't
  // require a roundtrip through this file.
  void restoreThread;

  return (
    <div className="email-list-pane">
      <div className="email-list-toolbar">
        <label className="email-list-selectall">
          <input
            type="checkbox"
            checked={allVisibleSelected}
            onChange={toggleSelectAll}
            aria-label="Seleccionar todos los hilos visibles"
          />
        </label>
        <div className="email-search">
          <Search size={13} aria-hidden />
          <input
            type="search"
            value={searchInput}
            onChange={(e) => {
              setSearchInput(e.target.value);
              // Persist search in the URL so refresh keeps the
              // query — debounced separately above before triggering
              // the fetch.
              const next = new URLSearchParams(params.toString());
              if (e.target.value.trim()) next.set("q", e.target.value.trim());
              else next.delete("q");
              router.replace(`/emails?${next.toString()}`);
            }}
            placeholder="Buscar en emails…"
            aria-label="Buscar hilos por contacto, asunto o cuerpo"
          />
        </div>
      </div>

      {someSelected ? (
        <EmailBulkActionsBar
          selectedIds={selectedIds}
          currentState={state}
          folders={folders}
          labels={labels}
          onClearSelection={() => setSelected(new Set())}
          onChanged={fetchThreads}
        />
      ) : null}

      {error ? <p className="form-error">{error}</p> : null}

      {loading ? (
        <p className="muted email-list-empty">Cargando…</p>
      ) : threads.length === 0 ? (
        <p className="muted email-list-empty">
          {debounced ? (
            <>Ningún hilo coincide con &quot;{debounced}&quot;.</>
          ) : (
            <>
              <Inbox size={14} aria-hidden /> No hay hilos en esta vista.
            </>
          )}
        </p>
      ) : (
        <ul className="email-list-items">
          {threads.map((t, idx) => {
            const isSelected = selected.has(t.id);
            const isOpen = openThreadId === t.id;
            const isCursor = cursor === idx;
            const unread = t.has_unread_replies;
            const labelsForThread = t.labels ?? [];
            return (
              <li
                key={t.id}
                ref={(el) => {
                  rowRefs.current[idx] = el;
                }}
                className={[
                  "email-list-row",
                  unread ? "is-unread" : "",
                  isSelected ? "is-selected" : "",
                  isOpen ? "is-open" : "",
                  isCursor ? "is-cursor" : "",
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
                      toggleSelect(t.id, idx, shift);
                    }}
                    onClick={(e) => {
                      const me = e as unknown as MouseEvent;
                      if (me.shiftKey) {
                        e.preventDefault();
                        toggleSelect(t.id, idx, true);
                      }
                    }}
                    aria-label={`Seleccionar hilo ${t.subject ?? "(sin asunto)"}`}
                  />
                </label>
                <button
                  type="button"
                  className="email-list-star"
                  aria-label={t.is_starred ? "Quitar estrella" : "Marcar"}
                  onClick={() => onStar(t, !t.is_starred)}
                >
                  <Star
                    size={14}
                    aria-hidden
                    fill={t.is_starred ? "#facc15" : "none"}
                    color={t.is_starred ? "#facc15" : "#cbd5e1"}
                  />
                </button>
                <Link href={`/emails/${t.id}`} className="email-list-link">
                  <span className="email-list-contact">
                    {t.contact_name || "(sin nombre)"}
                    {t.message_count > 1 ? (
                      <span className="muted small">
                        {" "}
                        ({t.message_count})
                      </span>
                    ) : null}
                  </span>
                  <span className="email-list-subject">
                    {t.subject || "(sin asunto)"}
                    {t.last_message_snippet ? (
                      <>
                        <span className="email-subject-sep"> · </span>
                        <span className="email-snippet">
                          {t.last_message_snippet}
                        </span>
                      </>
                    ) : null}
                  </span>
                  <span className="email-list-meta">
                    {labelsForThread.length > 0 ? (
                      <span className="email-list-labels">
                        {labelsForThread.map((label) => (
                          <span
                            key={label.id}
                            className="email-list-label-chip"
                            style={{
                              backgroundColor:
                                (label.color ?? "#e5e7eb") + "33",
                              color: label.color ?? "#1d2940",
                              borderColor: label.color ?? "#e5e7eb",
                            }}
                          >
                            {label.name}
                          </span>
                        ))}
                      </span>
                    ) : null}
                    {t.tracking && Object.keys(t.tracking).length > 0 ? (
                      <EmailEventBadges counts={t.tracking} compact />
                    ) : null}
                    <span className="email-list-date muted small">
                      {formatRelative(t.last_message_at)}
                    </span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
