"use client";

import { ExternalLink, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  getBrevoTemplateHtml,
  getComposerSourceTemplates,
  getEmailTemplate,
  getEmailTemplatesPicker,
  recordEmailTemplateUse,
  type BrevoPickerItem,
  type ComposerSourceItem,
  type EmailTemplate,
  type EmailTemplateListItem,
  type EmailTemplatesPicker,
} from "../../lib/emailTemplatesApi";

type Tab = "crm" | "brevo" | "composer" | "recent";

export type TemplatePickerSelection = {
  source: Tab;
  subject: string | null;
  body_html: string;
  /** Set when the operator just picked a CRM template. We bump
   *  `usage_count` on the backend so the recent list reflects it. */
  template_id?: string | null;
};

type Props = {
  onSelect: (selection: TemplatePickerSelection) => void;
  onClose: () => void;
};

const TAB_LABELS: Record<Tab, string> = {
  crm: "CRM",
  brevo: "Brevo",
  composer: "Composer ⚡",
  recent: "Recientes",
};

export function TemplatePicker({ onSelect, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("crm");
  const [picker, setPicker] = useState<EmailTemplatesPicker | null>(null);
  const [composer, setComposer] = useState<{
    items: ComposerSourceItem[];
    error: string | null;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [composerNotice, setComposerNotice] = useState<ComposerSourceItem | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([getEmailTemplatesPicker(), getComposerSourceTemplates()])
      .then(([p, c]) => {
        if (cancelled) return;
        setPicker(p);
        setComposer({ items: c.items, error: c.error });
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(
          extractErrorMessage(err, "No se pudieron cargar las plantillas."),
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    function matches(name: string, subject?: string | null) {
      if (!q) return true;
      return (
        name.toLowerCase().includes(q) ||
        (subject ? subject.toLowerCase().includes(q) : false)
      );
    }
    return {
      crm: (picker?.crm ?? []).filter((t) => matches(t.name, t.subject)),
      brevo: (picker?.brevo ?? []).filter((t) => matches(t.name, t.subject)),
      composer: (composer?.items ?? []).filter((t) => matches(t.name)),
      recent: (picker?.recent ?? []).filter((t) => matches(t.name, t.subject)),
    };
  }, [picker, composer, search]);

  async function pickCrm(item: EmailTemplateListItem) {
    try {
      const full: EmailTemplate = await getEmailTemplate(item.id);
      onSelect({
        source: tab === "recent" ? "recent" : "crm",
        subject: full.subject,
        body_html: full.body_html,
        template_id: full.id,
      });
      // Fire-and-forget: bumping the use count shouldn't block the
      // selection if Redis or the API hiccups.
      recordEmailTemplateUse(full.id).catch(() => {
        /* swallow */
      });
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar la plantilla."));
    }
  }

  async function pickBrevo(item: BrevoPickerItem) {
    try {
      // Cache rows store html_content NULL until first detail open;
      // the endpoint lazy-loads via the Brevo API when needed.
      const full = await getBrevoTemplateHtml(item.id);
      onSelect({
        source: "brevo",
        subject: full.subject ?? item.subject,
        body_html: full.body_html,
      });
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo cargar la plantilla Brevo."),
      );
    }
  }

  function openComposer(item: ComposerSourceItem) {
    setComposerNotice(item);
  }

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-dialog tp-dialog">
        <div className="modal-header">
          <h2>Cargar plantilla</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Cerrar"
          >
            ×
          </button>
        </div>
        <div className="modal-body">
          <div className="tp-tabs" role="tablist">
            {(Object.keys(TAB_LABELS) as Tab[]).map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                className={`tp-tab${tab === key ? " is-active" : ""}`}
                onClick={() => setTab(key)}
              >
                {TAB_LABELS[key]}
              </button>
            ))}
          </div>
          <div className="tp-search">
            <Search size={13} aria-hidden />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Buscar plantilla…"
            />
          </div>
          {error ? <p className="modal-error">{error}</p> : null}
          {loading ? (
            <p className="muted">Cargando…</p>
          ) : tab === "crm" ? (
            <CrmList items={filtered.crm} onPick={pickCrm} />
          ) : tab === "brevo" ? (
            <BrevoList items={filtered.brevo} onPick={pickBrevo} />
          ) : tab === "composer" ? (
            <ComposerList
              items={filtered.composer}
              error={composer?.error ?? null}
              onPick={openComposer}
            />
          ) : (
            <CrmList
              items={filtered.recent}
              onPick={pickCrm}
              emptyHint="Aún no has usado ninguna plantilla."
            />
          )}
        </div>
      </div>
      {composerNotice ? (
        <ComposerOpenModal
          item={composerNotice}
          onClose={() => setComposerNotice(null)}
        />
      ) : null}
    </div>
  );
}

function CrmList({
  items,
  onPick,
  emptyHint = "No hay plantillas que coincidan.",
}: {
  items: EmailTemplateListItem[];
  onPick: (item: EmailTemplateListItem) => void;
  emptyHint?: string;
}) {
  if (items.length === 0) {
    return <p className="muted">{emptyHint}</p>;
  }
  return (
    <ul className="tp-list">
      {items.map((item) => (
        <li key={item.id}>
          <button
            type="button"
            className="tp-row"
            onClick={() => onPick(item)}
          >
            <span className="tp-row-name">{item.name}</span>
            {item.subject ? (
              <span className="tp-row-subject">{item.subject}</span>
            ) : null}
            <span className="tp-row-meta">{item.usage_count} usos</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function BrevoList({
  items,
  onPick,
}: {
  items: BrevoPickerItem[];
  onPick: (item: BrevoPickerItem) => void;
}) {
  if (items.length === 0) {
    return (
      <p className="muted">
        No hay plantillas Brevo sincronizadas que coincidan.
      </p>
    );
  }
  return (
    <ul className="tp-list">
      {items.map((item) => (
        <li key={item.id}>
          <button
            type="button"
            className="tp-row"
            onClick={() => onPick(item)}
          >
            <span className="tp-row-name">{item.name}</span>
            {item.subject ? (
              <span className="tp-row-subject">{item.subject}</span>
            ) : null}
            <span className="tp-row-meta">Brevo</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function ComposerList({
  items,
  error,
  onPick,
}: {
  items: ComposerSourceItem[];
  error: string | null;
  onPick: (item: ComposerSourceItem) => void;
}) {
  if (error) {
    return (
      <p className="modal-error" style={{ marginTop: 0 }}>
        {error}
      </p>
    );
  }
  if (items.length === 0) {
    return <p className="muted">No hay plantillas en composer.bomedia.net.</p>;
  }
  return (
    <ul className="tp-list">
      {items.map((item) => (
        <li key={item.id}>
          <button
            type="button"
            className="tp-row"
            onClick={() => onPick(item)}
          >
            <span className="tp-row-name">{item.name}</span>
            <span className="tp-row-subject">
              {item.brand ? `${item.brand} · ` : ""}
              {item.blocks_count} bloque{item.blocks_count === 1 ? "" : "s"}
            </span>
            <span className="tp-row-meta">Composer</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function ComposerOpenModal({
  item,
  onClose,
}: {
  item: ComposerSourceItem;
  onClose: () => void;
}) {
  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      style={{ zIndex: 1100 }}
    >
      <div className="modal-dialog small">
        <div className="modal-header">
          <h2>{item.name}</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Cerrar"
          >
            ×
          </button>
        </div>
        <div className="modal-body">
          <p>
            Esta plantilla solo se puede editar en{" "}
            <strong>composer.bomedia.net</strong> porque usa bloques de
            productos visuales que el editor del CRM no soporta.
          </p>
          <p>
            Ábrela en el Composer, edítala y copia el HTML renderizado.
            Luego pégalo en este editor (pestaña HTML).
          </p>
          <div className="modal-footer">
            <button type="button" className="button secondary" onClick={onClose}>
              Cancelar
            </button>
            <a
              href={item.open_url}
              target="_blank"
              rel="noopener noreferrer"
              className="button"
            >
              Abrir en Composer <ExternalLink size={12} aria-hidden />
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
