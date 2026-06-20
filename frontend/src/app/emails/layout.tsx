"use client";

import { ChevronLeft } from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { EmailComposerModal } from "../components/EmailComposerModal";
import { DraftListPanel } from "../components/email/DraftListPanel";
import { EmailFiltersBar } from "../components/email/EmailFiltersBar";
import { EmailFolderDialog } from "../components/email/EmailFolderDialog";
import { EmailLabelDialog } from "../components/email/EmailLabelDialog";
import { EmailSidebar } from "../components/email/EmailSidebar";
import { EmailThreadList } from "../components/email/EmailThreadList";
import {
  type EmailFolder,
  type EmailLabel,
  listEmailDrafts,
  listEmailFolders,
  listEmailLabels,
} from "../lib/emailsApi";

/** Three-pane mailbox shell: sidebar (folders + labels) | thread list
 *  | right pane (the route's `page.tsx` — empty state on /emails, a
 *  thread detail on /emails/[thread_id]). The layout owns the
 *  folders + labels fetch so navigating between threads keeps them
 *  in cache, and hosts the compose modal so the sidebar's
 *  "Redactar" button works from every route inside /emails.
 *
 *  PR-Fix-Emails-Responsive-Mobile. En portrait <768px el grid de
 *  3 columnas no cabe. Se introduce una máquina de estados móvil
 *  (`folders` → `list` → `thread`) que el CSS aplica vía el
 *  atributo `data-mobile-view`. En tablet/desktop sigue el grid
 *  normal sin tocar lo que ya funcionaba. */
export default function EmailsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // Drafts shares the layout's middle column with the thread list;
  // when the user lands on /emails/drafts we swap `EmailThreadList`
  // for `DraftListPanel` so "Borradores" behaves the same way as
  // "Estrellados" / "Archivados" (middle pane filters; right pane
  // shows the selected item). /emails/drafts/* deeper routes (none
  // today but room to grow) keep the panel mounted too.
  const isDraftsRoute =
    pathname === "/emails/drafts" ||
    pathname.startsWith("/emails/drafts/");

  // PR-Fix-Emails-Responsive-Mobile. Detecta si la URL es un thread
  // detail (`/emails/{uuid}`) — en mobile se muestra solo ese pane
  // con un botón "atrás" para volver a la lista. Plantillas /
  // programados son rutas propias y siguen siendo "list".
  const isThreadRoute =
    pathname.startsWith("/emails/") &&
    !pathname.startsWith("/emails/drafts") &&
    !pathname.startsWith("/emails/plantillas") &&
    !pathname.startsWith("/emails/programados");

  const [folders, setFolders] = useState<EmailFolder[]>([]);
  const [labels, setLabels] = useState<EmailLabel[]>([]);
  const [draftsCount, setDraftsCount] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [composeOpen, setComposeOpen] = useState(false);
  // PR-Fix-Emails-Responsive-Mobile. State machine 1-col móvil.
  // `folders` (inicial) → sidebar visible. `list` → middle visible.
  // `thread` → cuando isThreadRoute=true, derivado en `mobileView`.
  const [mobileView, setMobileView] = useState<"folders" | "list">(
    "folders",
  );
  const [folderEdit, setFolderEdit] = useState<{
    open: boolean;
    target: EmailFolder | null;
  }>({ open: false, target: null });
  const [labelEdit, setLabelEdit] = useState<{
    open: boolean;
    target: EmailLabel | null;
  }>({ open: false, target: null });

  // PR-Fix-Emails-Responsive-Mobile. Cada vez que cambian la ruta
  // o los query-params (folder_id / label_id / state) sin estar en
  // un thread detail, asumimos que el usuario hizo click en una
  // entrada del sidebar y queremos saltar a la vista de lista en
  // mobile. El guard `didMount` evita que el render inicial fuerce
  // la transición (queremos arrancar en "folders" la primera vez).
  const didMount = useRef(false);
  const navSignature = `${pathname}?${searchParams.toString()}`;
  useEffect(() => {
    if (!didMount.current) {
      didMount.current = true;
      return;
    }
    if (!isThreadRoute) {
      setMobileView("list");
    }
  }, [navSignature, isThreadRoute]);

  const loadFolders = useCallback(async () => {
    try {
      setFolders(await listEmailFolders());
    } catch {
      setFolders([]);
    }
  }, []);

  const loadLabels = useCallback(async () => {
    try {
      setLabels(await listEmailLabels());
    } catch {
      setLabels([]);
    }
  }, []);

  const loadDraftsCount = useCallback(async () => {
    try {
      const drafts = await listEmailDrafts();
      setDraftsCount(drafts.length);
    } catch {
      setDraftsCount(0);
    }
  }, []);

  useEffect(() => {
    void loadFolders();
    void loadLabels();
    void loadDraftsCount();
  }, [loadFolders, loadLabels, loadDraftsCount]);

  const refreshAll = useCallback(() => {
    void loadFolders();
    void loadLabels();
    void loadDraftsCount();
    setRefreshKey((k) => k + 1);
  }, [loadFolders, loadLabels, loadDraftsCount]);

  const effectiveMobileView = isThreadRoute ? "thread" : mobileView;

  return (
    <main className="email-mailbox" data-mobile-view={effectiveMobileView}>
      <EmailSidebar
        folders={folders}
        labels={labels}
        draftsCount={draftsCount}
        onComposeClick={() => setComposeOpen(true)}
        onEditFolder={(target) => setFolderEdit({ open: true, target })}
        onEditLabel={(target) => setLabelEdit({ open: true, target })}
        onChanged={refreshAll}
      />
      <div className="email-middle-column">
        {/* PR-Fix-Emails-Responsive-Mobile. Botón "Carpetas" visible
            sólo en mobile (CSS oculta en ≥768px). Devuelve al
            usuario al sidebar como vista principal. */}
        <button
          type="button"
          className="email-mobile-back"
          onClick={() => setMobileView("folders")}
          aria-label="Volver a carpetas"
        >
          <ChevronLeft size={16} aria-hidden /> Carpetas
        </button>
        {isDraftsRoute ? null : <EmailFiltersBar />}
        {isDraftsRoute ? (
          <DraftListPanel
            refreshKey={refreshKey}
            onChanged={refreshAll}
          />
        ) : (
          <EmailThreadList
            folders={folders}
            labels={labels}
            refreshKey={refreshKey}
          />
        )}
      </div>
      <section className="email-thread-pane">{children}</section>

      {composeOpen ? (
        // PR-Fix-Modal-Nuevo-Email-Layout. Wrap como panel derecho
        // fijo (45vw) — no oscurece la lista de hilos a la
        // izquierda. Patrón estándar Gmail-style.
        <div
          className="email-compose-panel"
          role="presentation"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setComposeOpen(false);
          }}
        >
          <EmailComposerModal
            onClose={() => setComposeOpen(false)}
            onSent={() => {
              setComposeOpen(false);
              refreshAll();
            }}
          />
        </div>
      ) : null}

      <EmailFolderDialog
        open={folderEdit.open}
        folder={folderEdit.target}
        folders={folders}
        onClose={() => setFolderEdit({ open: false, target: null })}
        onSaved={refreshAll}
      />
      <EmailLabelDialog
        open={labelEdit.open}
        label={labelEdit.target}
        onClose={() => setLabelEdit({ open: false, target: null })}
        onSaved={refreshAll}
      />
    </main>
  );
}
