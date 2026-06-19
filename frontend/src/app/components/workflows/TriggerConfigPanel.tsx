"use client";

import { useEffect, useState } from "react";
import {
  listIntegrationAccounts,
  type IntegrationAccount,
} from "../../lib/integrationSettings";
import {
  listBrevoCampaigns,
  type BrevoCampaign,
} from "../../lib/brevoApi";
import { getUsers, listTags, type Tag, type User } from "../../lib/api";
import { listEmailTemplates } from "../../lib/emailTemplatesApi";
import { PipelineStageSelector } from "./PipelineStageSelector";

type Props = {
  triggerType: string;
  config: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
};

/**
 * PR-Fixes-Pase-2 Bug E.
 *
 * Panel de sub-configuración del trigger. Cambia según el tipo
 * elegido para que el operador pueda acotar el disparo a una
 * campaña Brevo concreta, un stage de pipeline concreto, un tag
 * concreto, etc.
 *
 * El backend dispatcher ya respeta `trigger_config.filter` para
 * filtros generales sobre el contacto; los campos específicos por
 * tipo (campaign_id, template_id, stage_id, etc.) se interpretan
 * en cada handler de trigger en el dispatcher.
 *
 * Si el trigger no tiene sub-config relevante, el panel renderiza
 * un placeholder neutro.
 */
export function TriggerConfigPanel({
  triggerType,
  config,
  onChange,
}: Props) {
  const set = (key: string, value: unknown) => {
    const next = { ...config };
    if (
      value === undefined ||
      value === null ||
      value === ""
    ) {
      delete next[key];
    } else {
      next[key] = value;
    }
    onChange(next);
  };

  if (
    triggerType === "email.brevo.opened" ||
    triggerType === "email.brevo.clicked"
  ) {
    return <BrevoSubConfig config={config} set={set} clicked={triggerType === "email.brevo.clicked"} />;
  }

  if (
    triggerType === "email.crm.opened" ||
    triggerType === "email.crm.clicked" ||
    triggerType === "email.crm.replied"
  ) {
    return <CrmEmailSubConfig config={config} set={set} clicked={triggerType === "email.crm.clicked"} />;
  }

  if (
    triggerType === "opportunity.created" ||
    triggerType === "opportunity.stage_changed" ||
    triggerType === "opportunity.won" ||
    triggerType === "opportunity.lost"
  ) {
    return (
      <OpportunitySubConfig
        config={config}
        set={set}
        wantsStage={triggerType === "opportunity.stage_changed"}
        wantsValue={triggerType === "opportunity.won"}
      />
    );
  }

  if (triggerType === "task.created" || triggerType === "task.completed" || triggerType === "task.overdue") {
    return <TaskSubConfig config={config} set={set} />;
  }

  if (triggerType === "contact.updated") {
    return <ContactUpdatedSubConfig config={config} set={set} />;
  }

  if (triggerType === "contact.lifecycle_changed") {
    return <LifecycleSubConfig config={config} set={set} />;
  }

  if (triggerType === "engagement.brevo.composed") {
    return <EngagementSubConfig config={config} set={set} />;
  }

  if (triggerType === "contact.date_field") {
    return <DateFieldSubConfig config={config} set={set} />;
  }

  if (triggerType === "cron.recurring") {
    return <CronSubConfig config={config} set={set} />;
  }

  return (
    <p className="muted small">
      Este trigger no requiere configuración adicional. Aplica a
      cualquier ocurrencia del evento.
    </p>
  );
}

// ---------------------------------------------------------------------
// Brevo opened / clicked
// ---------------------------------------------------------------------

function BrevoSubConfig({
  config,
  set,
  clicked,
}: {
  config: Record<string, unknown>;
  set: (k: string, v: unknown) => void;
  clicked: boolean;
}) {
  const [accounts, setAccounts] = useState<IntegrationAccount[]>([]);
  const [campaigns, setCampaigns] = useState<BrevoCampaign[]>([]);
  const [loading, setLoading] = useState(true);
  const accountId = (config.account_id as string) ?? "";
  const campaignId = (config.campaign_id as string) ?? "";

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listIntegrationAccounts({ system: "brevo" })
      .then((rows) => {
        if (cancelled) return;
        setAccounts(rows);
        if (!accountId && rows.length === 1) {
          set("account_id", rows[0].account_id);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!accountId) {
      setCampaigns([]);
      return;
    }
    let cancelled = false;
    listBrevoCampaigns(accountId, { status: "sent" })
      .then((rows) => {
        if (!cancelled) setCampaigns(rows);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [accountId]);

  if (loading) return <p className="muted small">Cargando cuentas Brevo…</p>;
  if (accounts.length === 0) {
    return (
      <p className="muted small">
        No hay cuentas Brevo configuradas. Conecta una desde{" "}
        <code>/admin/integrations</code>.
      </p>
    );
  }
  return (
    <>
      <label>
        Cuenta Brevo
        <select
          value={accountId}
          onChange={(e) => {
            set("account_id", e.target.value || undefined);
            set("campaign_id", undefined);
          }}
        >
          <option value="">— Selecciona —</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.account_id}>
              {a.display_name}
            </option>
          ))}
        </select>
      </label>
      {accountId ? (
        <label>
          Campaña específica (opcional)
          <select
            value={campaignId}
            onChange={(e) =>
              set("campaign_id", e.target.value || undefined)
            }
          >
            <option value="">— Cualquier campaña —</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.brevo_campaign_id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {clicked ? (
        <label>
          Link específico (opcional)
          <input
            type="text"
            value={(config.link_url as string) ?? ""}
            onChange={(e) => set("link_url", e.target.value || undefined)}
            placeholder="https://ejemplo.com/landing"
          />
        </label>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------
// CRM email opened / clicked / replied
// ---------------------------------------------------------------------

function CrmEmailSubConfig({
  config,
  set,
  clicked,
}: {
  config: Record<string, unknown>;
  set: (k: string, v: unknown) => void;
  clicked: boolean;
}) {
  const [templates, setTemplates] = useState<{ id: string; name: string }[]>(
    [],
  );
  const [users, setUsers] = useState<User[]>([]);
  useEffect(() => {
    let cancelled = false;
    listEmailTemplates()
      .then((rows) => {
        if (!cancelled)
          setTemplates(rows.map((t) => ({ id: t.id, name: t.name })));
      })
      .catch(() => undefined);
    getUsers({ limit: 100 })
      .then((rows) => {
        if (!cancelled) setUsers(rows.filter((u) => u.is_active));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <>
      <label>
        Plantilla específica (opcional)
        <select
          value={(config.template_id as string) ?? ""}
          onChange={(e) => set("template_id", e.target.value || undefined)}
        >
          <option value="">— Cualquier plantilla —</option>
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Owner del email (opcional)
        <select
          value={(config.owner_user_id as string) ?? ""}
          onChange={(e) =>
            set("owner_user_id", e.target.value || undefined)
          }
        >
          <option value="">— Cualquier owner —</option>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.full_name || u.email}
            </option>
          ))}
        </select>
      </label>
      {clicked ? (
        <label>
          Link específico (opcional)
          <input
            type="text"
            value={(config.link_url as string) ?? ""}
            onChange={(e) => set("link_url", e.target.value || undefined)}
            placeholder="https://ejemplo.com/landing"
          />
        </label>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------
// Opportunity-related triggers
// ---------------------------------------------------------------------

function OpportunitySubConfig({
  config,
  set,
  wantsStage,
  wantsValue,
}: {
  config: Record<string, unknown>;
  set: (k: string, v: unknown) => void;
  wantsStage: boolean;
  wantsValue: boolean;
}) {
  return (
    <>
      <PipelineStageSelector
        pipelineId={config.pipeline_id as string | undefined}
        stageId={wantsStage ? (config.stage_id as string | undefined) : undefined}
        onChange={(pid, sid) => {
          set("pipeline_id", pid);
          if (wantsStage) set("stage_id", sid);
        }}
        allowEmpty
        pipelineLabel="Pipeline (opcional)"
        stageLabel={wantsStage ? "Stage destino" : "Stage (opcional)"}
      />
      {wantsValue ? (
        <label>
          Valor mayor que (opcional)
          <input
            type="number"
            value={(config.min_value as number) ?? ""}
            onChange={(e) =>
              set(
                "min_value",
                e.target.value ? Number(e.target.value) : undefined,
              )
            }
          />
        </label>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------

function TaskSubConfig({
  config,
  set,
}: {
  config: Record<string, unknown>;
  set: (k: string, v: unknown) => void;
}) {
  return (
    <label>
      Prioridad mínima (opcional)
      <select
        value={(config.priority as string) ?? ""}
        onChange={(e) => set("priority", e.target.value || undefined)}
      >
        <option value="">— Cualquier prioridad —</option>
        <option value="low">low</option>
        <option value="medium">medium</option>
        <option value="high">high</option>
        <option value="urgent">urgent</option>
      </select>
    </label>
  );
}

// ---------------------------------------------------------------------
// Contact updated
// ---------------------------------------------------------------------

const CONTACT_FIELDS = [
  "email",
  "phone",
  "first_name",
  "last_name",
  "tags",
  "owner_user_id",
  "commercial_status",
  "lead_score",
  "address_country",
  "address_city",
  "job_title",
];

function ContactUpdatedSubConfig({
  config,
  set,
}: {
  config: Record<string, unknown>;
  set: (k: string, v: unknown) => void;
}) {
  return (
    <>
      <label>
        Campo que cambió (opcional)
        <select
          value={(config.field as string) ?? ""}
          onChange={(e) => set("field", e.target.value || undefined)}
        >
          <option value="">— Cualquier campo —</option>
          {CONTACT_FIELDS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </label>
      <label>
        Nuevo valor (opcional)
        <input
          type="text"
          value={(config.new_value as string) ?? ""}
          onChange={(e) => set("new_value", e.target.value || undefined)}
        />
      </label>
    </>
  );
}

// ---------------------------------------------------------------------
// Lifecycle changed
// ---------------------------------------------------------------------

const LIFECYCLE_STATUSES = ["new", "qualified", "customer", "lost", "lead", "prospect"];

function LifecycleSubConfig({
  config,
  set,
}: {
  config: Record<string, unknown>;
  set: (k: string, v: unknown) => void;
}) {
  return (
    <>
      <label>
        Estado origen (opcional)
        <select
          value={(config.from_status as string) ?? ""}
          onChange={(e) => set("from_status", e.target.value || undefined)}
        >
          <option value="">— Cualquier estado origen —</option>
          {LIFECYCLE_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label>
        Estado destino (opcional)
        <select
          value={(config.to_status as string) ?? ""}
          onChange={(e) => set("to_status", e.target.value || undefined)}
        >
          <option value="">— Cualquier estado destino —</option>
          {LIFECYCLE_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

// ---------------------------------------------------------------------
// Engagement compuesto Brevo
// ---------------------------------------------------------------------

function EngagementSubConfig({
  config,
  set,
}: {
  config: Record<string, unknown>;
  set: (k: string, v: unknown) => void;
}) {
  return (
    <>
      <label>
        Aperturas mínimas
        <input
          type="number"
          min={0}
          value={(config.min_opens as number) ?? 3}
          onChange={(e) => set("min_opens", Number(e.target.value))}
        />
      </label>
      <label>
        Clicks mínimos
        <input
          type="number"
          min={0}
          value={(config.min_clicks as number) ?? 0}
          onChange={(e) => set("min_clicks", Number(e.target.value))}
        />
      </label>
      <label>
        Ventana (días)
        <input
          type="number"
          min={1}
          value={(config.window_days as number) ?? 7}
          onChange={(e) => set("window_days", Number(e.target.value))}
        />
      </label>
    </>
  );
}

// ---------------------------------------------------------------------
// Fecha del contacto (cumpleaños / aniversario / custom)
// ---------------------------------------------------------------------

function DateFieldSubConfig({
  config,
  set,
}: {
  config: Record<string, unknown>;
  set: (k: string, v: unknown) => void;
}) {
  return (
    <>
      <label>
        Campo de fecha
        <select
          value={(config.field as string) ?? "birthday"}
          onChange={(e) => set("field", e.target.value)}
        >
          <option value="birthday">Cumpleaños</option>
          <option value="anniversary">Aniversario</option>
        </select>
      </label>
      <label>
        Coincide con
        <select
          value={(config.match as string) ?? "today"}
          onChange={(e) => set("match", e.target.value)}
        >
          <option value="today">Hoy</option>
          <option value="in_7_days">En 7 días</option>
          <option value="in_30_days">En 30 días</option>
        </select>
      </label>
    </>
  );
}

// ---------------------------------------------------------------------
// Cron — preset visual
// ---------------------------------------------------------------------

function CronSubConfig({
  config,
  set,
}: {
  config: Record<string, unknown>;
  set: (k: string, v: unknown) => void;
}) {
  return (
    <>
      <label>
        Frecuencia
        <select
          value={(config.preset as string) ?? "daily"}
          onChange={(e) => set("preset", e.target.value)}
        >
          <option value="hourly">Cada hora</option>
          <option value="daily">Cada día</option>
          <option value="weekly_monday">Cada lunes</option>
          <option value="weekly_friday">Cada viernes</option>
          <option value="monthly_first_day">El día 1 de cada mes</option>
        </select>
      </label>
      <label>
        A la hora (0-23)
        <input
          type="number"
          min={0}
          max={23}
          value={(config.hour as number) ?? 9}
          onChange={(e) => set("hour", Number(e.target.value))}
        />
      </label>
    </>
  );
}

// ---------------------------------------------------------------------
// Tag picker para steps add/remove (reusable)
// ---------------------------------------------------------------------

export function TagPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const [tags, setTags] = useState<Tag[]>([]);
  useEffect(() => {
    let cancelled = false;
    listTags()
      .then((page) => {
        if (!cancelled) setTags(page.items);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">— Selecciona —</option>
      {tags.map((t) => (
        <option key={t.id} value={t.name}>
          {t.name}
        </option>
      ))}
    </select>
  );
}
