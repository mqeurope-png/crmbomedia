"use client";

import { useCallback, useEffect, useState } from "react";
import { EmailComposerModal } from "../components/EmailComposerModal";
import { EmailFolderDialog } from "../components/email/EmailFolderDialog";
import { EmailLabelDialog } from "../components/email/EmailLabelDialog";
import { EmailSidebar } from "../components/email/EmailSidebar";
import { EmailThreadList } from "../components/email/EmailThreadList";
import {
  type EmailFolder,
  type EmailLabel,
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
  const [folders, setFolders] = useState<EmailFolder[]>([]);
  const [labels, setLabels] = useState<EmailLabel[]>([]);
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

  useEffect(() => {
    void loadFolders();
    void loadLabels();
  }, [loadFolders, loadLabels]);

  const refreshAll = useCallback(() => {
    void loadFolders();
    void loadLabels();
    setRefreshKey((k) => k + 1);
  }, [loadFolders, loadLabels]);

  return (
    <main className="email-mailbox">
      <EmailSidebar
        folders={folders}
        labels={labels}
        onComposeClick={() => setComposeOpen(true)}
        onEditFolder={(target) => setFolderEdit({ open: true, target })}
        onEditLabel={(target) => setLabelEdit({ open: true, target })}
        onChanged={refreshAll}
      />
      <EmailThreadList
        folders={folders}
        labels={labels}
        refreshKey={refreshKey}
      />
      <section className="email-thread-pane">{children}</section>

      {composeOpen ? (
        <EmailComposerModal
          onClose={() => setComposeOpen(false)}
          onSent={() => {
            setComposeOpen(false);
            refreshAll();
          }}
        />
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
