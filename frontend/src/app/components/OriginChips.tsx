import { ExternalLink } from "lucide-react";
import type {
  ExternalReference,
  ExternalReferenceSummary,
} from "../lib/api";

const SYSTEM_LABELS: Record<string, string> = {
  agilecrm: "AgileCRM",
  brevo: "Brevo",
  freshdesk: "Freshdesk",
  factusol: "FactuSOL",
  manual: "Manual",
};

function systemLabel(system: string, explicit?: string | null): string {
  if (explicit) return explicit;
  return SYSTEM_LABELS[system] ?? system;
}

/** A single origin pill, optionally with an ↗ deep link. Reuses the
 * `.tag-chip` visual language so origins read like the rest of the
 * chip UI. The per-system tint comes from `origin-chip--<system>`. */
function Chip({
  system,
  label,
  externalId,
  externalUrl,
  size,
}: {
  system: string;
  label: string;
  externalId?: string | null;
  externalUrl?: string | null;
  size: "dense" | "regular";
}) {
  const inner = (
    <>
      <span className="origin-chip-system">{label}</span>
      {externalId ? (
        <span className="origin-chip-id">ID {externalId}</span>
      ) : null}
      {externalUrl ? <ExternalLink size={size === "dense" ? 11 : 13} aria-hidden /> : null}
    </>
  );
  const className = `tag-chip origin-chip origin-chip--${system}`;
  if (externalUrl) {
    return (
      <a
        className={className}
        href={externalUrl}
        target="_blank"
        rel="noopener noreferrer"
        title={`Abrir en ${label}`}
      >
        {inner}
      </a>
    );
  }
  return <span className={className}>{inner}</span>;
}

/** Full origin chips for the contact detail card — system label,
 * account label, external id and a deep link when available. */
export function OriginChips({
  references,
}: {
  references: ExternalReference[];
}) {
  if (!references.length) {
    return <span className="muted">—</span>;
  }
  return (
    <ul className="tag-chip-list origin-chip-list">
      {references.map((ref) => {
        const label = systemLabel(ref.system, ref.system_label);
        const full = ref.account_label ? `${label} · ${ref.account_label}` : label;
        return (
          <li key={ref.id}>
            <Chip
              system={ref.system}
              label={full}
              externalId={ref.external_id}
              externalUrl={ref.external_url}
              size="regular"
            />
          </li>
        );
      })}
    </ul>
  );
}

/** Compact origin chips for the contacts list — just the system
 * label per origin, no ids or links (the row stays scannable). */
export function OriginChipsSummary({
  summary,
}: {
  summary: ExternalReferenceSummary[] | undefined;
}) {
  if (!summary || !summary.length) return <>—</>;
  // One chip per distinct system; a contact in two AgileCRM accounts
  // still reads as a single "AgileCRM" origin in the list.
  const systems = Array.from(new Set(summary.map((r) => r.system)));
  return (
    <ul className="tag-chip-list tag-chip-list--dense origin-chip-list">
      {systems.map((system) => (
        <li key={system}>
          <Chip system={system} label={systemLabel(system)} size="dense" />
        </li>
      ))}
    </ul>
  );
}
