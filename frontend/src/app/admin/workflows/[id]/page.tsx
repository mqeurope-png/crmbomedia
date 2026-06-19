"use client";

import "@xyflow/react/dist/style.css";
import {
  Background,
  Controls,
  type Edge,
  Handle,
  MiniMap,
  type Node,
  Position,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import {
  Calculator,
  CirclePlay,
  Pause,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { PageHeader } from "../../../components/PageHeader";
import { extractErrorMessage } from "../../../lib/errors";
import {
  activateWorkflow,
  getWorkflow,
  getWorkflowCatalog,
  getWorkflowCostEstimate,
  pauseWorkflow,
  updateWorkflow,
  type WorkflowCatalog,
  type WorkflowCostEstimate,
  type WorkflowDetail,
  type WorkflowEdgeWrite,
  type WorkflowStepWrite,
} from "../../../lib/workflowsApi";

type StepNodeData = {
  label: string;
  stepType: string;
  config: Record<string, unknown>;
  isEntry: boolean;
  [key: string]: unknown;
};

type WorkflowFlowNode = Node<StepNodeData, "workflowStep">;

// React Flow node renderer — minimal but legible.
function StepNode({
  data,
  selected,
}: {
  data: StepNodeData;
  selected?: boolean;
}) {
  const summary = (() => {
    if (data.stepType === "wait_time") {
      const m = (data.config?.duration_minutes as number | undefined) ?? 0;
      return `${m} min`;
    }
    if (data.stepType === "action_send_email") {
      const subj = (data.config?.subject as string | undefined) ?? "";
      return subj.slice(0, 40);
    }
    if (data.stepType === "action_add_tag") {
      return `+${(data.config?.tag as string) ?? ""}`;
    }
    if (data.stepType === "condition") {
      const c = data.config?.condition as Record<string, unknown> | undefined;
      const f = (c?.field as string) ?? "";
      const o = (c?.op as string) ?? "";
      return `${f} ${o}`;
    }
    return "";
  })();
  return (
    <div
      className={`workflow-node ${selected ? "is-selected" : ""} ${data.isEntry ? "is-entry" : ""}`}
    >
      <Handle type="target" position={Position.Top} />
      <div className="workflow-node-title">{data.label}</div>
      {summary ? <div className="workflow-node-summary">{summary}</div> : null}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const nodeTypes = { workflowStep: StepNode as any };

export default function WorkflowEditorPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const workflowId = params.id;
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null);
  const [catalog, setCatalog] = useState<WorkflowCatalog | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [estimate, setEstimate] = useState<WorkflowCostEstimate | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const w = await getWorkflow(workflowId);
      const c = await getWorkflowCatalog();
      setWorkflow(w);
      setCatalog(c);
      setNodes(
        w.steps.map((s) => ({
          id: s.id,
          type: "workflowStep",
          position: { x: s.position_x, y: s.position_y },
          data: {
            label:
              c.steps.find((x) => x.type === s.type)?.label ?? s.type,
            stepType: s.type,
            config: s.config,
            isEntry: s.is_entry,
          },
        })),
      );
      setEdges(
        w.edges.map((e) => ({
          id: e.id,
          source: e.from_step_id,
          target: e.to_step_id,
          label: e.branch_label !== "default" ? e.branch_label : undefined,
        })),
      );
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el workflow."));
    }
  }, [workflowId, setNodes, setEdges]);

  useEffect(() => {
    void load();
  }, [load]);

  const onConnect = useCallback(
    (connection: { source: string | null; target: string | null }) => {
      if (!connection.source || !connection.target) return;
      setEdges((eds) =>
        addEdge(
          {
            id: `e-${connection.source}-${connection.target}`,
            source: connection.source!,
            target: connection.target!,
          },
          eds,
        ),
      );
    },
    [setEdges],
  );

  const addStep = (stepType: string, label: string) => {
    const id = `step-${Date.now()}`;
    const lastNode = nodes[nodes.length - 1];
    const x = lastNode ? lastNode.position.x : 200;
    const y = lastNode ? lastNode.position.y + 120 : 200;
    setNodes((nds) => [
      ...nds,
      {
        id,
        type: "workflowStep",
        position: { x, y },
        data: {
          label,
          stepType,
          config: {},
          isEntry: false,
        },
      },
    ]);
  };

  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );

  const updateSelectedConfig = (next: Record<string, unknown>) => {
    if (!selectedNodeId) return;
    setNodes((nds) =>
      nds.map((n) =>
        n.id === selectedNodeId
          ? { ...n, data: { ...n.data, config: next } }
          : n,
      ),
    );
  };

  const onSave = async () => {
    if (!workflow) return;
    setBusy(true);
    setError(null);
    try {
      const stepsPayload: WorkflowStepWrite[] = nodes.map((n, idx) => ({
        client_id: n.id,
        type: n.data.stepType,
        config: n.data.config,
        position_x: n.position.x,
        position_y: n.position.y,
        is_entry: n.data.isEntry || idx === 0,
      }));
      const edgesPayload: WorkflowEdgeWrite[] = edges.map((e) => ({
        from_client_id: e.source,
        to_client_id: e.target,
        branch_label: (e.label as string) || "default",
      }));
      const updated = await updateWorkflow(workflowId, {
        steps: stepsPayload,
        edges: edgesPayload,
      });
      setWorkflow(updated);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar."));
    } finally {
      setBusy(false);
    }
  };

  const onEstimate = async () => {
    try {
      setEstimate(await getWorkflowCostEstimate(workflowId));
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo calcular la estimación."));
    }
  };

  const onActivate = async () => {
    if (!estimate) {
      await onEstimate();
      return;
    }
    if (estimate.validation_errors.length > 0) {
      setError(`Errores: ${estimate.validation_errors.join(" · ")}`);
      return;
    }
    if (
      !confirm(
        `Vas a activar este workflow. Aproximadamente ${estimate.matching_contacts_now} contactos cumplirían el trigger ahora mismo. Solo se procesarán contactos que cumplan a partir de ahora. ¿Confirmar?`,
      )
    )
      return;
    setBusy(true);
    try {
      await activateWorkflow(workflowId, true);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo activar."));
    } finally {
      setBusy(false);
    }
  };

  if (!workflow || !catalog) {
    return <div className="page"><p className="muted">Cargando editor…</p></div>;
  }

  return (
    <div className="page workflow-editor-page">
      <PageHeader
        title={workflow.name}
        description={`${workflow.trigger_type} · ${workflow.status}`}
        actions={
          <>
            <button
              type="button"
              className="button secondary"
              onClick={() => router.push("/admin/workflows")}
            >
              Volver
            </button>
            <button
              type="button"
              className="button secondary"
              onClick={onEstimate}
              disabled={busy}
            >
              <Calculator size={12} aria-hidden /> Estimar
            </button>
            {workflow.status !== "active" ? (
              <button
                type="button"
                className="button"
                onClick={onActivate}
                disabled={busy}
              >
                <CirclePlay size={12} aria-hidden /> Activar
              </button>
            ) : (
              <button
                type="button"
                className="button secondary"
                onClick={async () => {
                  await pauseWorkflow(workflowId);
                  await load();
                }}
                disabled={busy}
              >
                <Pause size={12} aria-hidden /> Pausar
              </button>
            )}
            <button
              type="button"
              className="button"
              onClick={onSave}
              disabled={busy}
            >
              <Save size={12} aria-hidden /> Guardar
            </button>
          </>
        }
      />

      {error ? <p className="form-error">{error}</p> : null}
      {estimate ? (
        <div className="form-card embedded">
          <h3>
            <Calculator size={14} aria-hidden /> Estimación
          </h3>
          <ul>
            <li>
              Contactos que cumplen ahora: <strong>{estimate.matching_contacts_now}</strong>
            </li>
            <li>Runs estimados 30d: {estimate.estimated_runs_30d}</li>
            <li>Emails estimados 30d: {estimate.estimated_emails_30d}</li>
            <li>Tareas estimadas 30d: {estimate.estimated_tasks_30d}</li>
            {estimate.validation_errors.length > 0 ? (
              <li className="form-error">
                Errores estructurales: {estimate.validation_errors.join(" · ")}
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}

      <div className="workflow-editor-layout">
        <aside className="workflow-editor-toolbar">
          <h3>Añadir paso</h3>
          {Object.entries(
            catalog.steps.reduce(
              (acc, s) => {
                if (s.type === "trigger") return acc;
                (acc[s.category] = acc[s.category] || []).push(s);
                return acc;
              },
              {} as Record<string, typeof catalog.steps>,
            ),
          ).map(([category, items]) => (
            <div key={category} className="workflow-editor-toolbar-group">
              <strong>{categoryLabel(category)}</strong>
              {items.map((s) => (
                <button
                  key={s.type}
                  type="button"
                  className="workflow-editor-toolbar-item"
                  onClick={() => addStep(s.type, s.label)}
                >
                  <Plus size={10} aria-hidden /> {s.label}
                </button>
              ))}
            </div>
          ))}
        </aside>

        <div className="workflow-editor-canvas">
          <ReactFlowProvider>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_e, node) => setSelectedNodeId(node.id)}
              nodeTypes={nodeTypes}
              fitView
            >
              <Background />
              <Controls />
              <MiniMap />
            </ReactFlow>
          </ReactFlowProvider>
        </div>

        <aside className="workflow-editor-side">
          {selectedNode ? (
            <StepConfigPanel
              node={selectedNode}
              catalog={catalog}
              onChange={updateSelectedConfig}
              onDelete={() => {
                setNodes((nds) => nds.filter((n) => n.id !== selectedNode.id));
                setEdges((eds) =>
                  eds.filter(
                    (e) =>
                      e.source !== selectedNode.id && e.target !== selectedNode.id,
                  ),
                );
                setSelectedNodeId(null);
              }}
            />
          ) : (
            <p className="muted small">
              Selecciona un paso del canvas para editar su configuración.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}

function categoryLabel(c: string): string {
  return (
    {
      wait: "Esperas",
      logic: "Lógica",
      contact: "Contacto",
      task: "Tareas",
      email: "Email",
      opportunity: "Oportunidades",
      notify: "Notificaciones",
      sync: "Sincronización",
      exit: "Salidas",
    }[c] ?? c
  );
}

// ---------------------------------------------------------------------
// Side panel — config form por step type
// ---------------------------------------------------------------------

type StepConfigPanelProps = {
  node: WorkflowFlowNode;
  catalog: WorkflowCatalog;
  onChange: (next: Record<string, unknown>) => void;
  onDelete: () => void;
};

function StepConfigPanel({
  node,
  catalog,
  onChange,
  onDelete,
}: StepConfigPanelProps) {
  const cfg = node.data.config;
  const setField = (key: string, value: unknown) => {
    onChange({ ...cfg, [key]: value });
  };

  return (
    <div className="workflow-step-config">
      <h3>{node.data.label}</h3>
      <p className="muted small">
        <code>{node.data.stepType}</code>
      </p>

      {node.data.stepType === "wait_time" ? (
        <label>
          Duración (minutos)
          <input
            type="number"
            min={1}
            value={(cfg.duration_minutes as number) ?? 60}
            onChange={(e) => setField("duration_minutes", Number(e.target.value))}
          />
          <span className="muted small">
            60 min = 1 h · 1440 = 1 día · 10080 = 7 días.
          </span>
        </label>
      ) : null}

      {node.data.stepType === "action_send_email" ? (
        <>
          <label>
            Asunto
            <input
              type="text"
              value={(cfg.subject as string) ?? ""}
              onChange={(e) => setField("subject", e.target.value)}
              placeholder="Hola {{ contact.first_name }}"
            />
          </label>
          <label>
            Cuerpo HTML
            <textarea
              rows={6}
              value={(cfg.body_html as string) ?? ""}
              onChange={(e) => setField("body_html", e.target.value)}
              placeholder="<p>Hola {{ contact.first_name }}, ...</p>"
            />
          </label>
          <label>
            From alias
            <input
              type="email"
              value={(cfg.from_alias as string) ?? ""}
              onChange={(e) => setField("from_alias", e.target.value)}
              placeholder="info@bomedia.net"
            />
          </label>
          <VariablesHelp variables={catalog.variables} />
        </>
      ) : null}

      {node.data.stepType === "action_add_tag" ||
      node.data.stepType === "action_remove_tag" ? (
        <label>
          Tag
          <input
            type="text"
            value={(cfg.tag as string) ?? ""}
            onChange={(e) => setField("tag", e.target.value)}
            placeholder="ej. fespa-onboarding"
          />
        </label>
      ) : null}

      {node.data.stepType === "action_change_lifecycle_status" ? (
        <label>
          Nuevo estado
          <select
            value={(cfg.status as string) ?? "new"}
            onChange={(e) => setField("status", e.target.value)}
          >
            {[
              "new",
              "qualified",
              "customer",
              "lost",
              "lead",
              "prospect",
            ].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {node.data.stepType === "action_change_lead_score" ? (
        <label>
          Delta (positivo suma, negativo resta)
          <input
            type="number"
            value={(cfg.delta as number) ?? 0}
            onChange={(e) => setField("delta", Number(e.target.value))}
          />
        </label>
      ) : null}

      {node.data.stepType === "action_create_task" ? (
        <>
          <label>
            Título
            <input
              type="text"
              value={(cfg.title as string) ?? ""}
              onChange={(e) => setField("title", e.target.value)}
              placeholder="Llamar a {{ contact.first_name }}"
            />
          </label>
          <label>
            Descripción
            <textarea
              rows={3}
              value={(cfg.description as string) ?? ""}
              onChange={(e) => setField("description", e.target.value)}
            />
          </label>
          <label>
            Vencimiento (días desde ahora)
            <input
              type="number"
              min={0}
              value={(cfg.due_in_days as number) ?? 1}
              onChange={(e) => setField("due_in_days", Number(e.target.value))}
            />
          </label>
          <label>
            Prioridad
            <select
              value={(cfg.priority as string) ?? "medium"}
              onChange={(e) => setField("priority", e.target.value)}
            >
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="urgent">urgent</option>
            </select>
          </label>
        </>
      ) : null}

      {node.data.stepType === "condition" ? (
        <ConditionBuilder
          fields={catalog.fields}
          condition={
            (cfg.condition as Record<string, unknown> | undefined) ?? {}
          }
          onChange={(next) => setField("condition", next)}
        />
      ) : null}

      {node.data.stepType === "wait_for_event" ? (
        <>
          <label>
            Evento a esperar
            <select
              value={(cfg.event_type as string) ?? "email.crm.opened"}
              onChange={(e) => setField("event_type", e.target.value)}
            >
              {[
                "email.crm.opened",
                "email.crm.clicked",
                "email.crm.replied",
                "contact.updated",
                "opportunity.stage_changed",
                "task.completed",
              ].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label>
            Timeout (minutos)
            <input
              type="number"
              min={1}
              value={(cfg.timeout_minutes as number) ?? 10080}
              onChange={(e) =>
                setField("timeout_minutes", Number(e.target.value))
              }
            />
          </label>
        </>
      ) : null}

      <button
        type="button"
        className="button secondary small"
        onClick={() => {
          if (!confirm("¿Borrar este paso?")) return;
          onDelete();
        }}
      >
        <Trash2 size={11} aria-hidden /> Borrar paso
      </button>
    </div>
  );
}

function VariablesHelp({ variables }: { variables: string[] }) {
  return (
    <details className="workflow-variables-help">
      <summary>Variables disponibles</summary>
      <ul>
        {variables.map((v) => (
          <li key={v}>
            <code>{`{{ ${v} }}`}</code>
          </li>
        ))}
      </ul>
    </details>
  );
}

type ConditionBuilderProps = {
  fields: string[];
  condition: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
};

function ConditionBuilder({
  fields,
  condition,
  onChange,
}: ConditionBuilderProps) {
  // Minimal: una sola hoja por ahora. Para AND/OR + N hojas el
  // siguiente sprint lo ampliará si Bart lo pide.
  return (
    <div className="workflow-condition-builder">
      <label>
        Campo
        <select
          value={(condition.field as string) ?? fields[0]}
          onChange={(e) => onChange({ ...condition, field: e.target.value })}
        >
          {fields.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </label>
      <label>
        Operador
        <select
          value={(condition.op as string) ?? "eq"}
          onChange={(e) => onChange({ ...condition, op: e.target.value })}
        >
          {[
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "contains",
            "not_contains",
            "starts_with",
            "ends_with",
            "empty",
            "not_empty",
          ].map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
      {!["empty", "not_empty"].includes(
        (condition.op as string) ?? "eq",
      ) ? (
        <label>
          Valor
          <input
            type="text"
            value={(condition.value as string) ?? ""}
            onChange={(e) => onChange({ ...condition, value: e.target.value })}
          />
        </label>
      ) : null}
    </div>
  );
}
