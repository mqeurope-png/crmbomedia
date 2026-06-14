"use client";

import {
  Archive,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Folder,
  Inbox,
  MailWarning,
  Pencil,
  Plus,
  Star,
  Tag,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import type {
  EmailFolder,
  EmailLabel,
  EmailThreadStateValue,
} from "../../lib/emailsApi";
import { deleteEmailFolder, deleteEmailLabel } from "../../lib/emailsApi";

type Props = {
  folders: EmailFolder[];
  labels: EmailLabel[];
  onComposeClick: () => void;
  onEditFolder: (folder: EmailFolder | null) => void;
  onEditLabel: (label: EmailLabel | null) => void;
  onChanged: () => void;
};

/** Top-level "boxes" the sidebar always shows. `inbox` is the default
 *  view; `starred` reuses the inbox state with the starred filter on
 *  top so the same archive/trash buttons keep working. */
type SystemView = {
  key: string;
  label: string;
  icon: React.ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  state: EmailThreadStateValue;
  starred?: boolean;
};

const SYSTEM_VIEWS: SystemView[] = [
  { key: "inbox", label: "Bandeja", icon: Inbox, state: "inbox" },
  {
    key: "starred",
    label: "Estrellados",
    icon: Star,
    state: "inbox",
    starred: true,
  },
  { key: "archived", label: "Archivados", icon: Archive, state: "archived" },
  { key: "trashed", label: "Papelera", icon: Trash2, state: "trashed" },
  { key: "spam", label: "Spam", icon: MailWarning, state: "spam" },
];

function buildHref(
  params: URLSearchParams,
  overrides: Record<string, string | null>,
): string {
  const next = new URLSearchParams(params.toString());
  for (const [key, value] of Object.entries(overrides)) {
    if (value === null) next.delete(key);
    else next.set(key, value);
  }
  // Strip filter that don't apply to the new view so jumping between
  // system views resets the secondary filters.
  const qs = next.toString();
  return qs ? `/emails?${qs}` : "/emails";
}

export function EmailSidebar({
  folders,
  labels,
  onComposeClick,
  onEditFolder,
  onEditLabel,
  onChanged,
}: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const scheduledActive = pathname === "/emails/programados";
  const params = useSearchParams();
  const currentState = params.get("state") || "inbox";
  const currentFolder = params.get("folder_id");
  const currentLabel = params.get("label_id");
  const currentStarred = params.get("starred") === "true";

  // Folder tree: nest by `parent_id`. Top-level are folders with
  // parent_id === null; children indent one level. The CRM's API
  // doesn't enforce a depth limit, but the spec caps at 3 — we
  // render whatever we get.
  const tree = useMemo(() => {
    const byParent = new Map<string | null, EmailFolder[]>();
    for (const f of folders) {
      const parent = f.parent_id ?? null;
      if (!byParent.has(parent)) byParent.set(parent, []);
      byParent.get(parent)!.push(f);
    }
    return byParent;
  }, [folders]);

  const renderFolder = (folder: EmailFolder, depth: number) => {
    const children = tree.get(folder.id) ?? [];
    const isActive = currentFolder === folder.id;
    return (
      <li key={folder.id}>
        <FolderRow
          folder={folder}
          isActive={isActive}
          depth={depth}
          onClick={() => router.push(buildHref(params, {
            folder_id: folder.id,
            label_id: null,
            starred: null,
            state: "inbox",
          }))}
          onEdit={() => onEditFolder(folder)}
          onDelete={async () => {
            if (!confirm(`¿Borrar la carpeta "${folder.name}"?`)) return;
            await deleteEmailFolder(folder.id);
            if (isActive) {
              router.push(buildHref(params, { folder_id: null }));
            }
            onChanged();
          }}
        />
        {children.length > 0 ? (
          <ul className="email-sidebar-folder-children">
            {children.map((c) => renderFolder(c, depth + 1))}
          </ul>
        ) : null}
      </li>
    );
  };

  return (
    <aside className="email-sidebar">
      <button
        type="button"
        className="email-sidebar-compose"
        onClick={onComposeClick}
      >
        <Pencil size={14} aria-hidden /> Redactar
      </button>

      <nav aria-label="Carpetas del sistema">
        <ul className="email-sidebar-list">
          {SYSTEM_VIEWS.map((view) => {
            const Icon = view.icon;
            const isActive =
              !scheduledActive &&
              currentFolder === null &&
              currentLabel === null &&
              currentState === view.state &&
              currentStarred === !!view.starred;
            return (
              <li key={view.key}>
                <button
                  type="button"
                  className={`email-sidebar-item${isActive ? " is-active" : ""}`}
                  onClick={() =>
                    router.push(
                      buildHref(params, {
                        state: view.state,
                        starred: view.starred ? "true" : null,
                        folder_id: null,
                        label_id: null,
                      }),
                    )
                  }
                >
                  <Icon size={14} aria-hidden />
                  <span>{view.label}</span>
                </button>
              </li>
            );
          })}
          <li>
            <Link
              href="/emails/programados"
              className={`email-sidebar-item${scheduledActive ? " is-active" : ""}`}
            >
              <CalendarClock size={14} aria-hidden />
              <span>Programados</span>
            </Link>
          </li>
        </ul>
      </nav>

      <SectionHeader
        title="Carpetas"
        actionLabel="Nueva carpeta"
        onAction={() => onEditFolder(null)}
      />
      {folders.length === 0 ? (
        <p className="email-sidebar-empty">Sin carpetas todavía.</p>
      ) : (
        <ul className="email-sidebar-list">
          {(tree.get(null) ?? []).map((f) => renderFolder(f, 0))}
        </ul>
      )}

      <SectionHeader
        title="Etiquetas"
        actionLabel="Nueva etiqueta"
        onAction={() => onEditLabel(null)}
      />
      {labels.length === 0 ? (
        <p className="email-sidebar-empty">Sin etiquetas todavía.</p>
      ) : (
        <ul className="email-sidebar-list">
          {labels.map((label) => {
            const isActive = currentLabel === label.id;
            return (
              <li key={label.id}>
                <div className="email-sidebar-row">
                  <button
                    type="button"
                    className={`email-sidebar-item${isActive ? " is-active" : ""}`}
                    onClick={() =>
                      router.push(
                        buildHref(params, {
                          label_id: label.id,
                          folder_id: null,
                          starred: null,
                          state: "inbox",
                        }),
                      )
                    }
                  >
                    <Tag
                      size={12}
                      aria-hidden
                      color={label.color ?? "#9ca3af"}
                      fill={label.color ?? "transparent"}
                    />
                    <span>{label.name}</span>
                  </button>
                  <button
                    type="button"
                    className="email-sidebar-edit"
                    aria-label={`Editar ${label.name}`}
                    onClick={() => onEditLabel(label)}
                  >
                    <Pencil size={11} aria-hidden />
                  </button>
                  <button
                    type="button"
                    className="email-sidebar-edit"
                    aria-label={`Borrar ${label.name}`}
                    onClick={async () => {
                      if (
                        !confirm(`¿Borrar la etiqueta "${label.name}"?`)
                      )
                        return;
                      await deleteEmailLabel(label.id);
                      if (isActive) {
                        router.push(buildHref(params, { label_id: null }));
                      }
                      onChanged();
                    }}
                  >
                    <X size={11} aria-hidden />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}

function SectionHeader({
  title,
  actionLabel,
  onAction,
}: {
  title: string;
  actionLabel: string;
  onAction: () => void;
}) {
  return (
    <div className="email-sidebar-section">
      <span>{title}</span>
      <button
        type="button"
        className="email-sidebar-edit"
        aria-label={actionLabel}
        title={actionLabel}
        onClick={onAction}
      >
        <Plus size={12} aria-hidden />
      </button>
    </div>
  );
}

function FolderRow({
  folder,
  isActive,
  depth,
  onClick,
  onEdit,
  onDelete,
}: {
  folder: EmailFolder;
  isActive: boolean;
  depth: number;
  onClick: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(true);
  const hasChildren = depth === 0;
  return (
    <div
      className="email-sidebar-row"
      style={{ paddingLeft: depth * 12 }}
    >
      {hasChildren ? (
        <button
          type="button"
          className="email-sidebar-toggle"
          aria-label={open ? "Plegar" : "Desplegar"}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        </button>
      ) : null}
      <button
        type="button"
        className={`email-sidebar-item${isActive ? " is-active" : ""}`}
        onClick={onClick}
      >
        <Folder
          size={13}
          aria-hidden
          color={folder.color ?? "#9ca3af"}
        />
        <span>{folder.name}</span>
        {folder.is_system ? (
          <CheckCircle2 size={10} aria-hidden color="#9ca3af" />
        ) : null}
      </button>
      {!folder.is_system ? (
        <>
          <button
            type="button"
            className="email-sidebar-edit"
            aria-label={`Editar ${folder.name}`}
            onClick={onEdit}
          >
            <Pencil size={11} aria-hidden />
          </button>
          <button
            type="button"
            className="email-sidebar-edit"
            aria-label={`Borrar ${folder.name}`}
            onClick={onDelete}
          >
            <X size={11} aria-hidden />
          </button>
        </>
      ) : null}
    </div>
  );
}
