"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import {
  moveContactToStage,
  type PipelineContactCard,
  type PipelineContactsResponse,
  type PipelineStageGroup,
} from "../lib/api";
import { extractErrorMessage } from "../lib/errors";
import { TagChips } from "./TagChips";

type Props = {
  /** Snapshot returned by `GET /pipelines/{id}/contacts`. The
   *  component manages its own local copy so optimistic drags don't
   *  block the UI on the server round-trip. */
  data: PipelineContactsResponse;
  onError: (message: string) => void;
};

/**
 * Kanban view of one pipeline. Columns are the stages, cards are the
 * contact assignments. Drag-and-drop between columns triggers
 * `moveContactToStage` optimistically — on failure the card snaps
 * back and the parent receives the error message.
 *
 * Uses the native HTML5 drag-and-drop API like the rest of the app
 * (column configurator, stage editor) so the kanban doesn't pull in
 * dnd-kit just for this one screen.
 */
export function PipelineKanban({ data, onError }: Props) {
  const [groups, setGroups] = useState(data.stages);
  const dragCard = useRef<{ card: PipelineContactCard; fromStage: string } | null>(
    null,
  );

  const handleDrop = useCallback(
    async (toStageId: string) => {
      const payload = dragCard.current;
      dragCard.current = null;
      if (!payload || payload.fromStage === toStageId) return;
      // Snapshot lives outside React state so a rollback can restore
      // it without racing with the next render.
      let snapshot: PipelineStageGroup[] = [];
      setGroups((current) => {
        snapshot = current;
        let card: PipelineContactCard | undefined;
        const intermediate = current.map((stage) => {
          if (stage.stage_id !== payload.fromStage) return stage;
          const remaining: PipelineContactCard[] = [];
          for (const candidate of stage.contacts) {
            if (candidate.id === payload.card.id) card = candidate;
            else remaining.push(candidate);
          }
          return { ...stage, contacts: remaining, total: stage.total - 1 };
        });
        if (!card) return current;
        return intermediate.map((stage) => {
          if (stage.stage_id !== toStageId) return stage;
          return {
            ...stage,
            contacts: [card!, ...stage.contacts],
            total: stage.total + 1,
          };
        });
      });
      try {
        await moveContactToStage(payload.card.id, { stage_id: toStageId });
      } catch (err) {
        onError(extractErrorMessage(err, "No se pudo mover el contacto."));
        setGroups(snapshot);
      }
    },
    [onError],
  );

  return (
    <div className="kanban">
      {groups.map((stage) => (
        <section
          key={stage.stage_id}
          className={`kanban-column${stage.is_won ? " is-won" : ""}${
            stage.is_lost ? " is-lost" : ""
          }`}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            void handleDrop(stage.stage_id);
          }}
          style={
            stage.stage_color
              ? { borderTopColor: stage.stage_color }
              : undefined
          }
        >
          <header className="kanban-column-header">
            <span className="kanban-column-title">{stage.stage_name}</span>
            <span className="muted small">{stage.total}</span>
          </header>
          {stage.target_days ? (
            <span className="muted small kanban-column-target">
              SLA: {stage.target_days}d
            </span>
          ) : null}
          <div className="kanban-column-body">
            {stage.contacts.map((card) => (
              <CardItem
                key={card.id}
                card={card}
                onDragStart={() => {
                  dragCard.current = { card, fromStage: stage.stage_id };
                }}
                isStale={
                  stage.target_days != null &&
                  card.days_in_stage > stage.target_days
                }
              />
            ))}
            {stage.contacts.length === 0 ? (
              <p className="muted small">Sin contactos.</p>
            ) : null}
          </div>
        </section>
      ))}
    </div>
  );
}

function CardItem({
  card,
  onDragStart,
  isStale,
}: {
  card: PipelineContactCard;
  onDragStart: () => void;
  isStale: boolean;
}) {
  const fullName =
    [card.first_name, card.last_name].filter(Boolean).join(" ") ||
    "(Sin nombre)";
  return (
    <article
      className={`kanban-card${isStale ? " is-stale" : ""}`}
      draggable
      onDragStart={onDragStart}
    >
      <Link href={`/contacts/${card.contact_id}`}>
        <strong>{fullName}</strong>
      </Link>
      <span className="muted small">{card.email}</span>
      {card.phone ? <span className="muted small">{card.phone}</span> : null}
      <div className="kanban-card-meta">
        {card.lead_score != null ? (
          <span className="kanban-card-score">★ {card.lead_score}</span>
        ) : null}
        <span className="muted small">{card.days_in_stage}d en etapa</span>
      </div>
      {card.tags.length ? (
        <TagChips tags={card.tags} size="dense" />
      ) : null}
    </article>
  );
}
