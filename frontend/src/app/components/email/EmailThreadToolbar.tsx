"use client";

import {
  Archive,
  Folder as FolderIcon,
  Forward,
  MailWarning,
  MailX,
  Reply,
  ReplyAll,
  Star,
  Tag,
  Trash2,
  Undo2,
} from "lucide-react";
import { useRef, useState } from "react";
import {
  type EmailFolder,
  type EmailLabel,
  type EmailThreadDetail,
} from "../../lib/emailsApi";

/** CRM-BANDEJA — toolbar del hilo agrupada en 3 secciones con divisores
 *  verticales:
 *    Estado:     estrella · archivar · papelera · marcar no leído
 *    Clasificar: spam · etiquetar · mover a carpeta
 *    Acciones:   Responder (primario) · Responder a todos · Reenviar
 */

type Props = {
  thread: EmailThreadDetail;
  folders: EmailFolder[];
  labels: EmailLabel[];
  appliedLabelIds: Set<string>;
  onStarToggle: () => void;
  onArchiveOrRestore: () => void;
  onTrash: () => void;
  onMarkUnread: () => void;
  onSpam: () => void;
  onMove: (folderId: string | null) => void;
  onToggleLabel: (labelId: string, applied: boolean) => void;
  onReply: () => void;
  onReplyAll: () => void;
  onForward: () => void;
};

export function EmailThreadToolbar({
  thread,
  folders,
  labels,
  appliedLabelIds,
  onStarToggle,
  onArchiveOrRestore,
  onTrash,
  onMarkUnread,
  onSpam,
  onMove,
  onToggleLabel,
  onReply,
  onReplyAll,
  onForward,
}: Props) {
  const [moveOpen, setMoveOpen] = useState(false);
  const [labelOpen, setLabelOpen] = useState(false);
  const moveRef = useRef<HTMLDivElement>(null);
  const labelRef = useRef<HTMLDivElement>(null);

  return (
    <div
      className="email-thread-toolbar"
      role="toolbar"
      aria-label="Acciones del hilo"
    >
      {/* Grupo Estado ------------------------------------------------ */}
      <div className="email-toolbar-group" aria-label="Estado">
        <IconButton
          icon={Star}
          label={thread.is_starred ? "Quitar estrella" : "Destacar con estrella"}
          active={thread.is_starred}
          onClick={onStarToggle}
        />
        <IconButton
          icon={thread.state === "inbox" ? Archive : Undo2}
          label={
            thread.state === "inbox"
              ? "Archivar (sale de la bandeja, no se borra)"
              : "Restaurar a la bandeja"
          }
          onClick={onArchiveOrRestore}
        />
        {thread.state !== "trashed" ? (
          <IconButton
            icon={Trash2}
            label="Mover a la papelera"
            onClick={onTrash}
          />
        ) : null}
        <IconButton
          icon={MailX}
          label="Marcar como no leído"
          onClick={onMarkUnread}
        />
      </div>

      <span className="email-toolbar-divider" aria-hidden />

      {/* Grupo Clasificar -------------------------------------------- */}
      <div className="email-toolbar-group" aria-label="Clasificar">
        {thread.state !== "spam" ? (
          <IconButton
            icon={MailWarning}
            label="Marcar como spam"
            onClick={onSpam}
          />
        ) : null}
        <div className="email-bulk-dropdown-wrap" ref={labelRef}>
          <IconButton
            icon={Tag}
            label="Etiquetar el hilo"
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
                labels.map((l) => {
                  const applied = appliedLabelIds.has(l.id);
                  return (
                    <button
                      key={l.id}
                      type="button"
                      className={`email-bulk-dropdown-item${applied ? " is-applied" : ""}`}
                      onClick={() => {
                        setLabelOpen(false);
                        onToggleLabel(l.id, applied);
                      }}
                    >
                      <Tag
                        size={12}
                        aria-hidden
                        color={l.color ?? "#9ca3af"}
                        fill={applied ? l.color ?? "#9ca3af" : "transparent"}
                      />
                      {l.name}
                      {applied ? (
                        <span className="muted small"> (aplicada)</span>
                      ) : null}
                    </button>
                  );
                })
              )}
            </div>
          ) : null}
        </div>
        <div className="email-bulk-dropdown-wrap" ref={moveRef}>
          <IconButton
            icon={FolderIcon}
            label="Mover a carpeta"
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
                onClick={() => {
                  setMoveOpen(false);
                  onMove(null);
                }}
              >
                Bandeja (sin carpeta)
              </button>
              {folders.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className="email-bulk-dropdown-item"
                  onClick={() => {
                    setMoveOpen(false);
                    onMove(f.id);
                  }}
                >
                  <FolderIcon
                    size={12}
                    aria-hidden
                    color={f.color ?? "#9ca3af"}
                  />
                  {f.name}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <span className="email-toolbar-divider" aria-hidden />

      {/* Grupo Acciones principales ---------------------------------- */}
      <div
        className="email-toolbar-group email-toolbar-group-actions"
        aria-label="Acciones principales"
      >
        <button
          type="button"
          className="button small"
          title="Responder al remitente"
          onClick={onReply}
        >
          <Reply size={12} aria-hidden /> Responder
        </button>
        <button
          type="button"
          className="button secondary small"
          title="Responder a todos los destinatarios"
          onClick={onReplyAll}
        >
          <ReplyAll size={12} aria-hidden /> Responder a todos
        </button>
        <button
          type="button"
          className="button secondary small"
          title="Reenviar el último mensaje"
          onClick={onForward}
        >
          <Forward size={12} aria-hidden /> Reenviar
        </button>
      </div>
    </div>
  );
}

function IconButton({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: React.ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`email-bulk-btn${active ? " is-active" : ""}`}
      onClick={onClick}
      title={label}
      aria-label={label}
    >
      <Icon size={13} aria-hidden />
    </button>
  );
}
