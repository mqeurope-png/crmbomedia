"use client";

import { ExternalLink, Plus, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { TemplateEditor } from "../../components/BrevoTemplateEditor";
import {
  listBrevoTemplates,
  resolvePrimaryBrevoAccount,
  type BrevoTemplate,
} from "../../lib/brevoApi";
import { extractErrorMessage } from "../../lib/errors";

type View =
  | { kind: "list" }
  | { kind: "create" }
  | { kind: "edit"; templateId: string };

export default function MarketingTemplatesPage() {
  const [accountId, setAccountId] = useState<string | null>(null);
  const [accountResolved, setAccountResolved] = useState(false);
  const [templates, setTemplates] = useState<BrevoTemplate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [view, setView] = useState<View>({ kind: "list" });
  const [filterTag, setFilterTag] = useState("");
  const [filterActive, setFilterActive] = useState("");
  const [query, setQuery] = useState("");

  const load = useCallback(
    async (account: string, refresh = false) => {
      try {
        const rows = await listBrevoTemplates(account, { refresh });
        setTemplates(rows);
        setError(null);
      } catch (err) {
        setError(
          extractErrorMessage(err, "No se pudieron cargar las plantillas."),
        );
      }
    },
    [],
  );

  useEffect(() => {
    resolvePrimaryBrevoAccount()
      .then(async (account) => {
        setAccountId(account);
        if (account) await load(account);
      })
      .catch(() =>
        setError("No se pudo resolver la cuenta Brevo configurada."),
      )
      .finally(() => {
        setAccountResolved(true);
        setIsLoading(false);
      });
  }, [load]);

  const tags = useMemo(
    () =>
      Array.from(
        new Set(templates.map((t) => t.tag).filter(Boolean) as string[]),
      ).sort(),
    [templates],
  );

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return templates
      .filter((t) => (filterTag ? t.tag === filterTag : true))
      .filter((t) =>
        filterActive ? String(t.is_active) === filterActive : true,
      )
      .filter((t) =>
        normalized ? t.name.toLowerCase().includes(normalized) : true,
      );
  }, [templates, filterTag, filterActive, query]);

  if (isLoading) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Plantillas de email" eyebrow="Marketing" />
        <p className="muted">Cargando…</p>
      </main>
    );
  }

  if (accountResolved && !accountId) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Plantillas de email" eyebrow="Marketing" />
        <ErrorState
          title="Brevo no configurado"
          message="Configura una cuenta Brevo en /admin/integrations para usar el módulo de marketing."
        />
      </main>
    );
  }

  if (view.kind !== "list" && accountId) {
    return (
      <main className="shell shell-wide">
        <PageHeader
          title={view.kind === "create" ? "Nueva plantilla" : "Editar plantilla"}
          eyebrow="Marketing"
          crumbs={[
            { label: "Plantillas", href: "/marketing/templates" },
            { label: view.kind === "create" ? "Nueva" : "Editar" },
          ]}
        />
        <TemplateEditor
          accountId={accountId}
          templateId={view.kind === "edit" ? view.templateId : null}
          onDone={async () => {
            setView({ kind: "list" });
            await load(accountId);
          }}
          onCancel={() => setView({ kind: "list" })}
        />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Plantillas de email"
        eyebrow="Marketing"
        description="Plantillas de Brevo gestionadas desde el CRM. El HTML se edita en texto plano con vista previa; para edición visual usa Brevo nativo."
        actions={
          <>
            <button
              type="button"
              className="button secondary small"
              disabled={refreshing || !accountId}
              onClick={async () => {
                if (!accountId) return;
                setRefreshing(true);
                await load(accountId, true);
                setRefreshing(false);
              }}
            >
              <RefreshCw size={12} aria-hidden />{" "}
              {refreshing ? "Refrescando…" : "Refrescar"}
            </button>
            <button
              type="button"
              className="button small"
              onClick={() => setView({ kind: "create" })}
            >
              <Plus size={12} aria-hidden /> Nueva plantilla
            </button>
          </>
        }
      />

      {error ? <ErrorState title="Error" message={error} /> : null}

      <div className="marketing-filters">
        <input
          type="search"
          placeholder="Buscar por nombre…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select
          value={filterTag}
          onChange={(event) => setFilterTag(event.target.value)}
        >
          <option value="">Todas las etiquetas</option>
          {tags.map((tag) => (
            <option key={tag} value={tag}>
              {tag}
            </option>
          ))}
        </select>
        <select
          value={filterActive}
          onChange={(event) => setFilterActive(event.target.value)}
        >
          <option value="">Cualquier estado</option>
          <option value="true">Activas</option>
          <option value="false">Inactivas</option>
        </select>
      </div>

      {filtered.length === 0 ? (
        <p className="muted">
          {templates.length === 0
            ? "No hay plantillas todavía. Crea la primera o pulsa Refrescar para traerlas de Brevo."
            : "Ninguna plantilla coincide con los filtros."}
        </p>
      ) : (
        <div className="template-cards-grid">
          {filtered.map((template) => (
            <button
              key={template.id}
              type="button"
              className="marketing-template-card"
              onClick={() => setView({ kind: "edit", templateId: template.id })}
            >
              <div className="marketing-template-card-head">
                <strong>{template.name}</strong>
                <span
                  className={`status-pill ${template.is_active ? "is-on" : "is-off"}`}
                >
                  {template.is_active ? "Activa" : "Inactiva"}
                </span>
              </div>
              <span className="muted small">{template.subject ?? "—"}</span>
              <span className="muted small">
                {template.sender_name
                  ? `${template.sender_name} <${template.sender_email}>`
                  : template.sender_email ?? ""}
              </span>
              {template.tag ? (
                <span className="marketing-tag-chip">{template.tag}</span>
              ) : null}
            </button>
          ))}
        </div>
      )}

      <p className="muted small">
        <ExternalLink size={12} aria-hidden /> ¿Necesitas el editor visual?{" "}
        <a
          href="https://app.brevo.com/templates/listing"
          target="_blank"
          rel="noreferrer"
        >
          Abrir plantillas en Brevo
        </a>
      </p>
    </main>
  );
}
