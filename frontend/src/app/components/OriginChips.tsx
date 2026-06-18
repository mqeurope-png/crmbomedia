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

function formatShortDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

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
 * account label, external id, deep link and the source-system
 * created/updated dates (when available). PR #58 added the date
 * columns to `external_references`; this card surfaces them so
 * the operator can see when the contact was first imported and
 * last refreshed in the source system, not just in the CRM. */
export function OriginChips({
  references,
}: {
  references: ExternalReference[];
}) {
  if (!references.length) {
    return <span className="muted">—</span>;
  }
  return (
    <ul className="origin-ref-list">
      {references.map((ref) => {
        const label = systemLabel(ref.system, ref.system_label);
        const full = ref.account_label ? `${label} · ${ref.account_label}` : label;
        const created = formatShortDate(ref.external_created_at);
        const updated = formatShortDate(ref.external_updated_at);
        // Collapse "created == updated" into a single line; the
        // operator doesn't need to read two identical dates.
        const showUpdated = updated && updated !== created;
        return (
          <li key={ref.id} className="origin-ref-item">
            <div className="origin-ref-chip">
              <Chip
                system={ref.system}
                label={full}
                externalId={ref.external_id}
                externalUrl={ref.external_url}
                size="regular"
              />
            </div>
            {created || showUpdated ? (
              <dl className="origin-ref-dates">
                {created ? (
                  <>
                    <dt>Creado</dt>
                    <dd>{created}</dd>
                  </>
                ) : null}
                {showUpdated ? (
                  <>
                    <dt>Actualizado</dt>
                    <dd>{updated}</dd>
                  </>
                ) : null}
              </dl>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/** Compact origin chips for the contacts list — un chip por par
 *  `(system, account_id)`.
 *
 *  PR-Ficha-Cleanup: pre-cleanup deduplicábamos por system y un
 *  contacto en `artisjet-europe` + `mbolasers` se leía como un solo
 *  "AgileCRM". Pero el filtro de /contacts SÍ permite seleccionar
 *  cuenta concreta, así que la lista necesita reflejarlo.
 *  Ahora: un chip "AgileCRM · artisjet-europe" por cada par. Si la
 *  cuenta está vacía (caso `manual`), solo el system label. */
export function OriginChipsSummary({
  summary,
}: {
  summary: ExternalReferenceSummary[] | undefined;
}) {
  if (!summary || !summary.length) return <>—</>;
  // Dedupea por par completo. Map preserva el orden de inserción.
  const seen = new Set<string>();
  const pairs: Array<{ system: string; account_id: string }> = [];
  for (const ref of summary) {
    const key = `${ref.system}|${ref.account_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    pairs.push({ system: ref.system, account_id: ref.account_id });
  }
  return (
    <ul className="tag-chip-list tag-chip-list--dense origin-chip-list">
      {pairs.map(({ system, account_id }) => {
        const label = systemLabel(system);
        const full = account_id ? `${label} · ${account_id}` : label;
        return (
          <li key={`${system}|${account_id}`}>
            <Chip system={system} label={full} size="dense" />
          </li>
        );
      })}
    </ul>
  );
}
