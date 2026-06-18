"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
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
 *  "Redactar" button works from every route inside /emails. */
export default function EmailsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  // Drafts shares the layout's middle column with the thread list;
  // when the user lands on /emails/drafts we swap `EmailThreadList`
  // for `DraftListPanel` so "Borradores" behaves the same way as
  // "Estrellados" / "Archivados" (middle pane filters; right pane
  // shows the selected item). /emails/drafts/* deeper routes (none
  // today but room to grow) keep the panel mounted too.
  const isDraftsRoute =
    pathname === "/emails/drafts" ||
    pathname.startsWith("/emails/drafts/");

  const [folders, setFolders] = useState<EmailFolder[]>([]);
  const [labels, setLabels] = useState<EmailLabel[]>([]);
  const [draftsCount, setDraftsCount] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [composeOpen, setComposeOpen] = useState(false);
  const [folderEdit, setFolderEdit] = useState<{
    open: boolean;
    target: EmailFolder | null;
  }>({ open: false, target: null });
  const [labelEdit, setLabelEdit] = useState<{
    open: boolean;
    target: EmailLabel | null;
  }>({ open: false, target: null });

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

  return (
    <main className="email-mailbox">
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
        // The composer renders as plain inline content (`.modal-backdrop`
        // isn't an overlay class — it was designed to live at the
        // bottom of the thread page for replies). Wrap it in a fixed
        // overlay here so a fresh "Redactar" from the sidebar shows
        // centred and isn't squashed inside the grid's columns.
        <div
          className="email-compose-overlay"
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
