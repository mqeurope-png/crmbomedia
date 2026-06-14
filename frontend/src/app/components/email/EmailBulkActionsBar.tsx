"use client";

import {
  Archive,
  Folder as FolderIcon,
  Mail,
  MailOpen,
  MailWarning,
  Star,
  StarOff,
  Tag,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { useRef, useState } from "react";
import type {
  EmailFolder,
  EmailLabel,
  EmailThreadStateValue,
} from "../../lib/emailsApi";
import {
  bulkAddLabel,
  bulkArchive,
  bulkMarkRead,
  bulkMarkUnread,
  bulkMove,
  bulkRemoveLabel,
  bulkRestore,
  bulkSpam,
  bulkStar,
  bulkTrash,
  bulkUnstar,
} from "../../lib/emailsApi";

type Props = {
  selectedIds: string[];
  currentState: EmailThreadStateValue;
  folders: EmailFolder[];
  labels: EmailLabel[];
  onClearSelection: () => void;
  /** Called after every successful mutation so the parent can
   *  refetch the thread list. */
  onChanged: () => void;
};

/** Sticky bar that appears at the top of the thread list when the
 *  operator has at least one row selected. Action set is shaped by
 *  the current state — there's no point showing "Archive" when
 *  every selected thread is already archived. */
export function EmailBulkActionsBar({
  selectedIds,
  currentState,
  folders,
  labels,
  onClearSelection,
  onChanged,
}: Props) {
  const count = selectedIds.length;
  const [busy, setBusy] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [labelOpen, setLabelOpen] = useState(false);
  const moveRef = useRef<HTMLDivElement>(null);
  const labelRef = useRef<HTMLDivElement>(null);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
      onClearSelection();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="email-bulk-bar" role="region" aria-label="Acciones masivas">
      <span className="email-bulk-count">{count} seleccionado{count > 1 ? "s" : ""}</span>

      {currentState !== "archived" ? (
        <BulkButton
          icon={Archive}
          label="Archivar"
          disabled={busy}
          onClick={() => run(() => bulkArchive(selectedIds))}
        />
      ) : null}
      {currentState !== "trashed" ? (
        <BulkButton
          icon={Trash2}
          label="A papelera"
          disabled={busy}
          onClick={() => run(() => bulkTrash(selectedIds))}
        />
      ) : null}
      {currentState !== "spam" ? (
        <BulkButton
          icon={MailWarning}
          label="Spam"
          disabled={busy}
          onClick={() => run(() => bulkSpam(selectedIds))}
        />
      ) : null}
      {currentState !== "inbox" ? (
        <BulkButton
          icon={Undo2}
          label="Restaurar"
          disabled={busy}
          onClick={() => run(() => bulkRestore(selectedIds))}
        />
      ) : null}

      <BulkButton
        icon={Star}
        label="Marcar"
        disabled={busy}
        onClick={() => run(() => bulkStar(selectedIds))}
      />
      <BulkButton
        icon={StarOff}
        label="Quitar marca"
        disabled={busy}
        onClick={() => run(() => bulkUnstar(selectedIds))}
      />
      <BulkButton
        icon={MailOpen}
        label="Marcar leído"
        disabled={busy}
        onClick={() => run(() => bulkMarkRead(selectedIds))}
      />
      <BulkButton
        icon={Mail}
        label="Marcar no leído"
        disabled={busy}
        onClick={() => run(() => bulkMarkUnread(selectedIds))}
      />

      <div className="email-bulk-dropdown-wrap" ref={moveRef}>
        <BulkButton
          icon={FolderIcon}
          label="Mover"
          disabled={busy}
          onClick={() => {
            setMoveOpen((v) => !v);
            setLabelOpen(false);
          }}
        />
        {moveOpen ? (
          <div className="email-bulk-dropdown">
            <button
              type="button"
              className="email-bulk-dropdown-item"
              onClick={() =>
                run(async () => {
                  await bulkMove(selectedIds, null);
                  setMoveOpen(false);
                })
              }
            >
              <Mail size={12} aria-hidden /> Bandeja (sin carpeta)
            </button>
            {folders.length === 0 ? (
              <span className="muted small email-bulk-dropdown-empty">
                Aún no tienes carpetas.
              </span>
            ) : (
              folders.map((f) => (
                <button
                  type="button"
                  key={f.id}
                  className="email-bulk-dropdown-item"
                  onClick={() =>
                    run(async () => {
                      await bulkMove(selectedIds, f.id);
                      setMoveOpen(false);
                    })
                  }
                >
                  <FolderIcon
                    size={12}
                    aria-hidden
                    color={f.color ?? "#9ca3af"}
                  />
                  {f.name}
                </button>
              ))
            )}
          </div>
        ) : null}
      </div>

      <div className="email-bulk-dropdown-wrap" ref={labelRef}>
        <BulkButton
          icon={Tag}
          label="Etiquetar"
          disabled={busy}
          onClick={() => {
            setLabelOpen((v) => !v);
            setMoveOpen(false);
          }}
        />
        {labelOpen ? (
          <div className="email-bulk-dropdown">
            {labels.length === 0 ? (
              <span className="muted small email-bulk-dropdown-empty">
                Aún no tienes etiquetas.
              </span>
            ) : (
              labels.map((l) => (
                <div key={l.id} className="email-bulk-dropdown-label-row">
                  <button
                    type="button"
                    className="email-bulk-dropdown-item"
                    onClick={() =>
                      run(async () => {
                        await bulkAddLabel(selectedIds, l.id);
                        setLabelOpen(false);
                      })
                    }
                  >
                    <Tag
                      size={12}
                      aria-hidden
                      color={l.color ?? "#9ca3af"}
                      fill={l.color ?? "transparent"}
                    />
                    Añadir “{l.name}”
                  </button>
                  <button
                    type="button"
                    className="email-bulk-dropdown-item subtle"
                    aria-label={`Quitar ${l.name}`}
                    onClick={() =>
                      run(async () => {
                        await bulkRemoveLabel(selectedIds, l.id);
                        setLabelOpen(false);
                      })
                    }
                  >
                    <X size={11} aria-hidden /> Quitar
                  </button>
                </div>
              ))
            )}
          </div>
        ) : null}
      </div>

      <button
        type="button"
        className="email-bulk-clear"
        onClick={onClearSelection}
        disabled={busy}
      >
        <X size={12} aria-hidden /> Limpiar
      </button>
    </div>
  );
}

function BulkButton({
  icon: Icon,
  label,
  onClick,
  disabled,
}: {
  icon: React.ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="email-bulk-btn"
      onClick={onClick}
      disabled={disabled}
      title={label}
    >
      <Icon size={13} aria-hidden />
      <span className="email-bulk-btn-label">{label}</span>
    </button>
  );
}
