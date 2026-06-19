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
  AlertTriangle,
  Calculator,
  CirclePlay,
  FlaskConical,
  Pause,
  Plus,
  Save,
  Search,
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
import { CustomFieldSelector } from "../../../components/workflows/CustomFieldSelector";
import { PipelineStageSelector } from "../../../components/workflows/PipelineStageSelector";
import { TagPicker, TriggerConfigPanel } from "../../../components/workflows/TriggerConfigPanel";
import { WorkflowConditionBuilder } from "../../../components/workflows/WorkflowConditionBuilder";
import { WorkflowDryRunModal } from "../../../components/workflows/WorkflowDryRunModal";
import { WorkflowNarrativeSummary } from "../../../components/workflows/WorkflowNarrativeSummary";
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
import { listEmailTemplates } from "../../../lib/emailTemplatesApi";
import {
  humanizeStepLabel,
  humanizeValidationMessages,
  stepIcon,
  stepSummary,
  validateStepConfig,
} from "../../../lib/workflowsHumanize";

type StepNodeData = {
  stepType: string;
  config: Record<string, unknown>;
  isEntry: boolean;
  displayName?: string | null;
  triggerType?: string;
  templateLookup?: Record<string, string>;
  [key: string]: unknown;
};

type WorkflowFlowNode = Node<StepNodeData, "workflowStep">;

/** PR-Fixes-Pase-3 Bug 1. Computa los IDs y labels de los handles de
 *  salida según el tipo + config del step. Cuando el step tiene
 *  múltiples ramas (condition / switch / wait_for_event) devolvemos
 *  varios; en otro caso, un único `"default"`. El `branch_label` de
 *  la edge es siempre el `id` del handle origen.
 */
function outgoingHandles(stepType: string, config: Record<string, unknown>): {
  id: string;
  label: string;
  variant: "default" | "yes" | "no";
}[] {
  if (stepType === "condition") {
    return [
      { id: "true", label: "Sí", variant: "yes" },
      { id: "false", label: "No", variant: "no" },
    ];
  }
  if (stepType === "wait_for_event") {
    return [
      { id: "matched", label: "Ocurrió", variant: "yes" },
      { id: "timeout", label: "Timeout", variant: "no" },
    ];
  }
  if (stepType === "switch") {
    const cases = (config.cases as string[] | undefined) ?? [];
    const out = cases.map((c, idx) => ({
      id: `case_${idx}`,
      label: c || `Caso ${idx + 1}`,
      variant: "default" as const,
    }));
    out.push({ id: "default", label: "Otros", variant: "default" });
    return out;
  }
  if (stepType.startsWith("exit_")) {
    return [];
  }
  return [{ id: "default", label: "", variant: "default" }];
}

/** Renderer del nodo en el canvas. Recalcula label humano + summary +
 *  validación en cada render para que cambios de config en el side
 *  panel se reflejen inmediatamente.
 *
 *  PR-Fixes: el label ya no se cachea en `data.label` porque eso
 *  causaba que el trigger node mostrara "Inicio del workflow"
 *  ignorando el `triggerType`.
 *
 *  PR-Fixes-Pase-3 Bug 1: handles separados por rama para condition /
 *  switch / wait_for_event. Cada handle muestra su etiqueta debajo.
 */
function StepNode({
  data,
  selected,
  id,
}: {
  data: StepNodeData;
  selected?: boolean;
  id: string;
}) {
  const stepLike = {
    type: data.stepType,
    config: data.config,
    display_name: data.displayName,
  };
  const label = humanizeStepLabel(stepLike, {
    triggerType: data.triggerType,
    templates: data.templateLookup,
  });
  const summary = stepSummary(stepLike);
  const validation = validateStepConfig(stepLike);
  const handles = outgoingHandles(data.stepType, data.config);
  // Distribución horizontal de los handles en el borde inferior: si hay
  // N handles, los espaciamos uniformemente.
  return (
    <div
      className={[
        "workflow-node",
        selected ? "is-selected" : "",
        data.isEntry ? "is-entry" : "",
        !validation.valid ? "is-invalid" : "",
        handles.length > 1 ? "is-multi-out" : "",
      ].join(" ")}
      data-node-id={id}
    >
      <Handle type="target" position={Position.Top} />
      <div className="workflow-node-row">
        <span className="workflow-node-icon" aria-hidden>
          {stepIcon(data.stepType)}
        </span>
        <div className="workflow-node-title">{label}</div>
        {!validation.valid ? (
          <span
            className="workflow-node-warning"
            title={humanizeValidationMessages(validation.missing).join(
              " · ",
            )}
            aria-label="Configuración incompleta"
          >
            <AlertTriangle size={11} aria-hidden />
          </span>
        ) : null}
      </div>
      {summary ? <div className="workflow-node-summary">{summary}</div> : null}
      {handles.map((h, idx) => {
        const offset =
          handles.length === 1
            ? 50
            : ((idx + 1) / (handles.length + 1)) * 100;
        return (
          <Handle
            key={h.id}
            id={h.id}
            type="source"
            position={Position.Bottom}
            className={`workflow-handle-${h.variant}`}
            style={{ left: `${offset}%` }}
          />
        );
      })}
      {handles.length > 1 ? (
        <div className="workflow-node-handle-labels">
          {handles.map((h, idx) => {
            const offset =
              ((idx + 1) / (handles.length + 1)) * 100;
            return (
              <span
                key={h.id}
                className={`workflow-handle-label workflow-handle-label-${h.variant}`}
                style={{ left: `${offset}%` }}
              >
                {h.label}
              </span>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const nodeTypes = { workflowStep: StepNode as any };

/** PR-Fixes #4: triggers que NO admiten retroactivo. Mantener
 *  sincronizado con el backend `cost_estimate`. */
const EVENT_TRIGGERS = new Set([
  "contact.created",
  "contact.updated",
  "contact.lifecycle_changed",
  "contact.unsubscribed",
  "email.crm.opened",
  "email.crm.clicked",
  "email.crm.replied",
  "email.brevo.opened",
  "email.brevo.clicked",
  "task.created",
  "task.completed",
  "task.overdue",
  "opportunity.created",
  "opportunity.stage_changed",
  "opportunity.won",
  "opportunity.lost",
]);

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
  const [searchTerm, setSearchTerm] = useState("");
  const [dryRunOpen, setDryRunOpen] = useState(false);
  const [templateLookup, setTemplateLookup] = useState<Record<string, string>>(
    {},
  );
  // PR-Fixes-Pase-2 Bug E. La sub-config del trigger vive en
  // `workflow.trigger_config_json` (no en `WorkflowStep.config_json`).
  // El editor mantiene su propia copia editable y la sincroniza al
  // guardar.
  const [triggerConfig, setTriggerConfig] = useState<Record<string, unknown>>(
    {},
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      const w = await getWorkflow(workflowId);
      const c = await getWorkflowCatalog();
      // PR-Fixes #8: cargamos el catálogo de plantillas email para que
      // el step `action_send_email` pueda elegir una y resolver el
      // nombre humano en el label del nodo.
      let templates: Awaited<ReturnType<typeof listEmailTemplates>> = [];
      try {
        templates = await listEmailTemplates();
      } catch {
        templates = [];
      }
      const tplLookup: Record<string, string> = {};
      for (const t of templates) tplLookup[t.id] = t.name;
      setWorkflow(w);
      setCatalog(c);
      setTemplateLookup(tplLookup);
      setTriggerConfig(w.trigger_config ?? {});
      setNodes(
        w.steps.map((s) => ({
          id: s.id,
          type: "workflowStep",
          position: { x: s.position_x, y: s.position_y },
          data: {
            stepType: s.type,
            config: s.config,
            isEntry: s.is_entry,
            displayName: s.display_name ?? null,
            triggerType: w.trigger_type,
            templateLookup: tplLookup,
          },
        })),
      );
      setEdges(
        w.edges.map((e) => ({
          id: e.id,
          source: e.from_step_id,
          target: e.to_step_id,
          // PR-Fixes-Pase-3 Bug 1: persistimos el branch_label COMO
          // sourceHandle para que React Flow ancle la flecha al
          // handle correcto en el render. La etiqueta visible se
          // sigue mostrando como `label` cuando no es default.
          sourceHandle: e.branch_label || "default",
          label: e.branch_label !== "default" ? e.branch_label : undefined,
          data: { branchLabel: e.branch_label || "default" },
        })),
      );
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el workflow."));
    }
  }, [workflowId, setNodes, setEdges]);

  useEffect(() => {
    void load();
  }, [load]);

  /** PR-Fixes-Pase-3 Bug 1: cada conexión persiste el `sourceHandle`
   *  como `data.branchLabel` en la edge. Eso es lo que el backend
   *  recibe como `branch_label`, y el engine usa para decidir qué
   *  rama tomar en `condition` / `switch` / `wait_for_event`. */
  const onConnect = useCallback(
    (connection: {
      source: string | null;
      target: string | null;
      sourceHandle?: string | null;
    }) => {
      if (!connection.source || !connection.target) return;
      const branch = connection.sourceHandle || "default";
      setEdges((eds) =>
        addEdge(
          {
            id: `e-${connection.source}-${connection.target}-${branch}`,
            source: connection.source!,
            target: connection.target!,
            sourceHandle: connection.sourceHandle ?? undefined,
            label: branch !== "default" ? branch : undefined,
            data: { branchLabel: branch },
          },
          eds,
        ),
      );
    },
    [setEdges],
  );

  const addStep = (stepType: string, _label: string) => {
    void _label;
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
          stepType,
          config: {},
          isEntry: false,
          displayName: null,
          triggerType: workflow?.trigger_type,
          templateLookup,
        },
      },
    ]);
  };

  /** Doble click sobre un nodo abre prompt para renombrar. Guarda
   *  `displayName` en data — se persiste al pulsar Guardar. */
  const onNodeDoubleClick = useCallback(
    (
      _e: React.MouseEvent,
      node: { id: string; data: StepNodeData },
    ) => {
      const current = node.data.displayName ?? "";
      const next = window.prompt(
        "Renombrar este paso (deja vacío para usar el nombre por defecto):",
        current,
      );
      if (next === null) return;
      const trimmed = next.trim();
      setNodes((nds) =>
        nds.map((n) =>
          n.id === node.id
            ? {
                ...n,
                data: {
                  ...n.data,
                  displayName: trimmed || null,
                },
              }
            : n,
        ),
      );
    },
    [setNodes],
  );

  /** Filtro de búsqueda en panel "Añadir paso". Match case-insensitive
   *  sobre label + tipo. */
  const filteredSteps = useMemo(() => {
    if (!catalog) return [];
    const term = searchTerm.trim().toLowerCase();
    if (!term) return catalog.steps;
    return catalog.steps.filter(
      (s) =>
        s.label.toLowerCase().includes(term) ||
        s.type.toLowerCase().includes(term),
    );
  }, [catalog, searchTerm]);

  /** Validación global. Activate se deshabilita si hay ≥1 nodo
   *  inválido. */
  const invalidNodes = useMemo(
    () =>
      nodes.filter(
        (n) =>
          !validateStepConfig({
            type: n.data.stepType,
            config: n.data.config,
          }).valid,
      ),
    [nodes],
  );

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
        display_name: n.data.displayName ?? null,
      }));
      const edgesPayload: WorkflowEdgeWrite[] = edges.map((e) => ({
        from_client_id: e.source,
        to_client_id: e.target,
        // PR-Fixes-Pase-3 Bug 1: el branch_label real vive en el
        // sourceHandle / data.branchLabel; `label` es solo el texto
        // visible y no siempre está poblado.
        branch_label:
          ((e.data as { branchLabel?: string } | undefined)?.branchLabel) ||
          e.sourceHandle ||
          (e.label as string) ||
          "default",
      }));
      const updated = await updateWorkflow(workflowId, {
        steps: stepsPayload,
        edges: edgesPayload,
        trigger_config: triggerConfig,
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
    if (invalidNodes.length > 0) {
      setError(
        `Hay ${invalidNodes.length} paso${invalidNodes.length === 1 ? "" : "s"} con configuración incompleta. Revisa los nodos en rojo.`,
      );
      return;
    }
    // Aviso por "similar" — exact ya lo rechaza el backend.
    const similar = workflow?.duplicate_warnings?.filter(
      (w) => w.kind === "similar",
    );
    if (similar && similar.length > 0) {
      if (
        !confirm(
          `Este workflow se parece a "${similar[0].workflow_name}". ¿Crear de todas formas?`,
        )
      ) {
        return;
      }
    }
    const isEvent = EVENT_TRIGGERS.has(workflow?.trigger_type ?? "");
    const confirmMsg = isEvent
      ? `Vas a activar este workflow. Como el trigger es un evento (${workflow?.trigger_type}), solo se procesarán contactos cuando ocurra en el futuro. Estimación: ${estimate.estimated_runs_30d} runs en 30d. ¿Confirmar?`
      : `Vas a activar este workflow. Aproximadamente ${estimate.matching_contacts_now} contactos cumplen el criterio ahora. Solo se procesarán los que cumplan a partir de ahora. ¿Confirmar?`;
    if (!confirm(confirmMsg)) return;
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
            {workflow.status === "draft" ? (
              <button
                type="button"
                className="button secondary"
                onClick={() => setDryRunOpen(true)}
                disabled={busy}
              >
                <FlaskConical size={12} aria-hidden /> Probar
              </button>
            ) : null}
            {workflow.status !== "active" ? (
              <button
                type="button"
                className="button"
                onClick={onActivate}
                disabled={busy || invalidNodes.length > 0}
                title={
                  invalidNodes.length > 0
                    ? `Hay ${invalidNodes.length} paso${invalidNodes.length === 1 ? "" : "s"} con configuración incompleta`
                    : ""
                }
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
              Contactos que cumplen ahora:{" "}
              <strong>{estimate.matching_contacts_now}</strong>
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
          {/* PR-Fixes #4. Explicación contextual del 0 cuando el
              trigger es de evento puntual — no es un bug, es lo
              correcto: el workflow solo aplica a futuros. */}
          {EVENT_TRIGGERS.has(workflow.trigger_type) ? (
            <p className="muted small">
              Este trigger dispara cuando ocurre el evento. La cifra
              «cumplen ahora» es 0 porque solo se aplica a futuros.
              «Runs estimados 30d» proyecta basándose en el histórico
              de los últimos 30 días.
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="workflow-editor-layout">
        <aside className="workflow-editor-toolbar">
          <h3>Añadir paso</h3>
          <div className="workflow-editor-search">
            <Search size={11} aria-hidden />
            <input
              type="search"
              placeholder="Buscar paso..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              aria-label="Buscar paso por nombre o tipo"
            />
          </div>
          {Object.entries(
            filteredSteps.reduce(
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
                  <span aria-hidden>{stepIcon(s.type)}</span>{" "}
                  <Plus size={10} aria-hidden /> {s.label}
                </button>
              ))}
            </div>
          ))}
          {filteredSteps.length === 0 ? (
            <p className="muted small">Sin resultados.</p>
          ) : null}
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
              onNodeDoubleClick={(e, node) =>
                onNodeDoubleClick(
                  e,
                  node as unknown as { id: string; data: StepNodeData },
                )
              }
              nodeTypes={nodeTypes}
              fitView
              /* PR-Fixes #5: borrar flechas. React Flow llama a
                 `onEdgesChange` con el delete event cuando el edge
                 está seleccionado y el user pulsa Delete/Backspace. */
              deleteKeyCode={["Backspace", "Delete"]}
              edgesFocusable
              elementsSelectable
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
              triggerConfig={triggerConfig}
              onTriggerConfigChange={setTriggerConfig}
              triggerType={workflow.trigger_type}
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

      <WorkflowNarrativeSummary
        triggerType={workflow.trigger_type}
        templates={templateLookup}
        steps={nodes.map((n) => ({
          id: n.id,
          type: n.data.stepType,
          config: n.data.config,
          display_name: n.data.displayName ?? null,
        }))}
        edges={edges.map((e) => ({
          from_step_id: e.source,
          to_step_id: e.target,
          branch_label: (e.label as string) ?? "default",
        }))}
      />

      {dryRunOpen ? (
        <WorkflowDryRunModal
          workflowId={workflowId}
          onClose={() => setDryRunOpen(false)}
        />
      ) : null}
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
  triggerConfig: Record<string, unknown>;
  onTriggerConfigChange: (next: Record<string, unknown>) => void;
  triggerType: string;
  onChange: (next: Record<string, unknown>) => void;
  onDelete: () => void;
};

function StepConfigPanel({
  node,
  catalog,
  triggerConfig,
  onTriggerConfigChange,
  triggerType,
  onChange,
  onDelete,
}: StepConfigPanelProps) {
  const cfg = node.data.config;
  const setField = (key: string, value: unknown) => {
    onChange({ ...cfg, [key]: value });
  };

  const panelLabel = humanizeStepLabel(
    {
      type: node.data.stepType,
      config: node.data.config,
      display_name: node.data.displayName,
    },
    {
      triggerType: node.data.triggerType,
      templates: node.data.templateLookup,
    },
  );
  return (
    <div className="workflow-step-config">
      <h3>{panelLabel}</h3>
      <p className="muted small">
        <code>{node.data.stepType}</code>
      </p>

      {/* PR-Fixes-Pase-2 Bug E: trigger node muestra sub-config +
          filtro adicional usando el FilterBuilder de Contactos. */}
      {node.data.stepType === "trigger" ? (
        <>
          <h4 className="workflow-section-h">Configuración del trigger</h4>
          <TriggerConfigPanel
            triggerType={triggerType}
            config={triggerConfig}
            onChange={onTriggerConfigChange}
          />
          <h4 className="workflow-section-h">Filtro adicional (opcional)</h4>
          <p className="muted small">
            Solo dispara cuando el contacto cumple estas condiciones.
          </p>
          <WorkflowConditionBuilder
            condition={
              (triggerConfig.filter as Record<string, unknown>) ?? {}
            }
            onChange={(next) =>
              onTriggerConfigChange({ ...triggerConfig, filter: next })
            }
          />
        </>
      ) : null}

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
        <SendEmailConfig
          cfg={cfg}
          setField={setField}
          templates={node.data.templateLookup}
          variables={catalog.variables}
        />
      ) : null}

      {node.data.stepType === "action_add_tag" ||
      node.data.stepType === "action_remove_tag" ? (
        <label>
          Tag
          <TagPicker
            value={(cfg.tag as string) ?? ""}
            onChange={(next) => setField("tag", next)}
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

      {/* PR-Fixes-Pase-3 Bug 6: dropdown de custom fields (no input
          texto). */}
      {node.data.stepType === "action_set_custom_field" ? (
        <CustomFieldSelector
          value={(cfg.field as string) ?? ""}
          valueValue={
            cfg.value === undefined || cfg.value === null
              ? ""
              : String(cfg.value)
          }
          onChange={(next) =>
            onChange({
              ...cfg,
              field: next.field,
              value: next.value,
              field_type: next.type,
            })
          }
        />
      ) : null}

      {/* Panel para action_move_opportunity_stage.
          PR-Fixes-Pase-3 Bug 5: la versión previa llamaba a
          `setField` dos veces seguidas; cada una capturaba `cfg` por
          closure → la segunda machacaba la primera y el pipeline se
          perdía al pestañear. Ahora actualizamos ambos en una sola
          llamada a `onChange`. */}
      {node.data.stepType === "action_move_opportunity_stage" ? (
        <>
          <PipelineStageSelector
            pipelineId={cfg.pipeline_id as string | undefined}
            stageId={cfg.stage_id as string | undefined}
            onChange={(pid, sid) => {
              onChange({ ...cfg, pipeline_id: pid, stage_id: sid });
            }}
          />
          <p className="muted small">
            El contacto debe tener una oportunidad activa en este
            pipeline para que la acción funcione.
          </p>
        </>
      ) : null}

      {/* Panel para action_assign_owner */}
      {node.data.stepType === "action_assign_owner" ? (
        <label>
          ID del usuario propietario
          <input
            type="text"
            value={(cfg.user_id as string) ?? ""}
            onChange={(e) => setField("user_id", e.target.value)}
            placeholder="UUID del user activo"
          />
          <span className="muted small">
            Cambia el owner del contacto al user indicado. Cópialo de
            <code> /admin/users</code>.
          </span>
        </label>
      ) : null}

      {/* Panel para action_notify_owner / action_notify_manager */}
      {node.data.stepType === "action_notify_owner" ||
      node.data.stepType === "action_notify_manager" ? (
        <>
          <label>
            Mensaje
            <textarea
              rows={3}
              value={(cfg.message as string) ?? ""}
              onChange={(e) => setField("message", e.target.value)}
              placeholder="Lead frío reactivado: {{ contact.first_name }}"
            />
            <span className="muted small">
              Soporta variables{" "}
              <code>{`{{ contact.first_name }}`}</code>. Se entrega como
              notificación in-app.
            </span>
          </label>
          <VariablesHelp variables={catalog.variables} />
        </>
      ) : null}

      {/* Panel para action_push_to_brevo / action_force_agilecrm_resync */}
      {node.data.stepType === "action_push_to_brevo" ||
      node.data.stepType === "action_force_agilecrm_resync" ? (
        <p className="muted small">
          Este paso no necesita configuración adicional. Se ejecuta
          automáticamente sobre el contacto del workflow.
        </p>
      ) : null}

      {/* Panel para exit_* */}
      {node.data.stepType.startsWith("exit_") ? (
        <p className="muted small">
          El workflow terminará con estado{" "}
          <code>{node.data.stepType.replace("exit_", "")}</code>. No
          requiere configuración.
        </p>
      ) : null}

      {/* Panel para switch */}
      {node.data.stepType === "switch" ? (
        <>
          <label>
            Campo a evaluar
            <select
              value={(cfg.field as string) ?? catalog.fields[0]}
              onChange={(e) => setField("field", e.target.value)}
            >
              {catalog.fields.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <label>
            Valores (uno por línea — cada uno genera una rama)
            <textarea
              rows={3}
              value={
                Array.isArray(cfg.cases) ? (cfg.cases as string[]).join("\n") : ""
              }
              onChange={(e) =>
                setField(
                  "cases",
                  e.target.value
                    .split("\n")
                    .map((v) => v.trim())
                    .filter(Boolean),
                )
              }
              placeholder={"new\nqualified\ncustomer"}
            />
          </label>
        </>
      ) : null}

      {/* Panel para wait_until */}
      {node.data.stepType === "wait_until" ? (
        <>
          <label>
            Fecha absoluta (ISO 8601)
            <input
              type="datetime-local"
              value={(cfg.absolute_at as string) ?? ""}
              onChange={(e) => setField("absolute_at", e.target.value)}
            />
          </label>
          <p className="muted small">
            O usa una fecha relativa al contacto (avanzado):
          </p>
          <label>
            Campo de fecha
            <select
              value={(cfg.field as string) ?? "contact.created_at"}
              onChange={(e) => setField("field", e.target.value)}
            >
              <option value="contact.created_at">contact.created_at</option>
            </select>
          </label>
          <label>
            Días de offset
            <input
              type="number"
              value={(cfg.offset_days as number) ?? 0}
              onChange={(e) =>
                setField("offset_days", Number(e.target.value))
              }
            />
          </label>
          <label>
            Hora local
            <input
              type="number"
              min={0}
              max={23}
              value={(cfg.hour_local as number) ?? 9}
              onChange={(e) =>
                setField("hour_local", Number(e.target.value))
              }
            />
          </label>
        </>
      ) : null}

      {node.data.stepType === "condition" ? (
        <WorkflowConditionBuilder
          condition={(cfg.condition as Record<string, unknown> | undefined) ?? {}}
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

/**
 * PR-Fixes Improvement #8: configuración del paso "Enviar email" con
 * dos modos (radio):
 *
 *  - "Usar plantilla del CRM" (default): dropdown con todas las
 *    plantillas + búsqueda. Persiste `template_id`; el motor resuelve
 *    al ejecutar (las ediciones de la plantilla se reflejan
 *    automáticamente).
 *  - "Contenido personalizado": campos subject + body_html como
 *    antes. Útil para mensajes ad-hoc que no merecen ser plantilla
 *    reutilizable.
 */
function SendEmailConfig({
  cfg,
  setField,
  templates,
  variables,
}: {
  cfg: Record<string, unknown>;
  setField: (key: string, value: unknown) => void;
  templates?: Record<string, string>;
  variables: string[];
}) {
  const tplId = cfg.template_id as string | undefined;
  // PR-Fixes-Pase-2 Bug A. La versión previa derivaba `mode` de
  // (template_id ? template : (body_html ? custom : template)). Ese
  // toggle quedaba muerto: al pasar a "custom" se eliminaba
  // template_id y body_html seguía vacío → la siguiente renderización
  // volvía a "template" y el radio "Contenido personalizado" no
  // se podía mantener marcado.
  //
  // Persistimos el modo explícitamente en `cfg.mode` para que el
  // toggle responda al click. Si la fila legacy no tiene `mode`,
  // lo derivamos del template_id (compat para drafts viejos).
  const persistedMode = cfg.mode as "template" | "custom" | undefined;
  const mode: "template" | "custom" = persistedMode
    ? persistedMode
    : tplId
      ? "template"
      : "template";
  const [filter, setFilter] = useState("");
  const tplEntries = templates ? Object.entries(templates) : [];
  const filtered = filter
    ? tplEntries.filter(([, name]) =>
        name.toLowerCase().includes(filter.toLowerCase()),
      )
    : tplEntries;

  const switchTo = (next: "template" | "custom") => {
    setField("mode", next);
    if (next === "template") {
      setField("body_html", "");
      setField("subject", "");
    } else {
      setField("template_id", undefined);
    }
  };

  return (
    <>
      <fieldset className="workflow-radio-group">
        <legend className="muted small">Origen del contenido</legend>
        <label className="workflow-radio">
          <input
            type="radio"
            name="email-mode"
            checked={mode === "template"}
            onChange={() => switchTo("template")}
          />
          Plantilla del CRM
        </label>
        <label className="workflow-radio">
          <input
            type="radio"
            name="email-mode"
            checked={mode === "custom"}
            onChange={() => switchTo("custom")}
          />
          Contenido personalizado
        </label>
      </fieldset>

      {mode === "template" ? (
        <>
          <label>
            Buscar plantilla
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Empieza a escribir el nombre..."
            />
          </label>
          <label>
            Plantilla
            <select
              value={tplId ?? ""}
              onChange={(e) => setField("template_id", e.target.value || undefined)}
            >
              <option value="">— Selecciona una —</option>
              {filtered.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
            {tplId && templates?.[tplId] ? (
              <span className="muted small">
                Plantilla seleccionada: «{templates[tplId]}». Las
                ediciones futuras de esta plantilla se reflejan al
                siguiente envío.
              </span>
            ) : (
              <span className="muted small">
                Sin plantilla seleccionada. El paso no enviará nada.
              </span>
            )}
          </label>
        </>
      ) : (
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
        </>
      )}

      <label>
        From alias
        <input
          type="email"
          value={(cfg.from_alias as string) ?? ""}
          onChange={(e) => setField("from_alias", e.target.value)}
          placeholder="info@bomedia.net"
        />
      </label>
      <VariablesHelp variables={variables} />
    </>
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

// PR-Fixes-Pase-2 Bug B: el `ConditionBuilder` legacy fue reemplazado
// por `WorkflowConditionBuilder` (envuelve EntityFilterBuilder con el
// schema de Contactos). Eliminado para no confundir.
