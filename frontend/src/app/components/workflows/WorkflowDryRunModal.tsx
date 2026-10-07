"use client";

import { CheckCircle, FlaskConical } from "lucide-react";
import { useState } from "react";
import {
  dryRunWorkflow,
  type WorkflowDryRunResponse,
} from "../../lib/workflowsApi";
import { extractErrorMessage } from "../../lib/errors";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";

type Props = {
  workflowId: string;
  onClose: () => void;
};

/** Modal de simulación. Pide contact_id y dispara el dry-run.
 *  Renderiza una timeline con cada paso simulado + descripción. */
export function WorkflowDryRunModal({ workflowId, onClose }: Props) {
  const [contactId, setContactId] = useState("");
  const [result, setResult] = useState<WorkflowDryRunResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose });

  const onRun = async () => {
    if (!contactId.trim()) {
      setError("Pega el ID del contacto a probar.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await dryRunWorkflow(workflowId, contactId.trim());
      setResult(res);
      if (res.error) {
        setError(`Error: ${res.error}`);
      }
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo simular."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="email-compose-overlay"
      role="presentation"
      {...overlayProps}
    >
      <div className="form-card workflow-dryrun-modal">
        <header className="workflow-dryrun-header">
          <h3>
            <FlaskConical size={14} aria-hidden /> Probar workflow
          </h3>
          <ModalCloseButton onClose={requestClose} placement="header" />
        </header>
        <p className="muted small">
          Simula el workflow sobre un contacto real sin commitear ni
          enviar nada. Ideal para verificar plantillas y condiciones
          antes de activar.
        </p>
        <label>
          ID del contacto
          <input
            type="text"
            value={contactId}
            onChange={(e) => setContactId(e.target.value)}
            placeholder="ej. 8f2c9a1d-..."
            disabled={loading}
          />
          <span className="muted small">
            Cópialo de la URL de la ficha del contacto.
          </span>
        </label>
        <div className="actions">
          <button
            type="button"
            className="button"
            onClick={onRun}
            disabled={loading || !contactId.trim()}
          >
            <FlaskConical size={12} aria-hidden /> Simular ejecución
          </button>
        </div>

        {error ? <p className="form-error">{error}</p> : null}

        {result && result.steps.length > 0 ? (
          <div className="workflow-dryrun-timeline">
            <p className="muted small">
              Simulando sobre <code>{result.contact_email ?? result.contact_id}</code>
            </p>
            <ol>
              {result.steps.map((s, idx) => (
                <li key={`${s.step_id}-${idx}`}>
                  <strong>{s.label}</strong>
                  {s.branch_taken ? (
                    <span className="badge muted small">
                      rama: {s.branch_taken}
                    </span>
                  ) : null}
                  <p className="muted small">{s.description}</p>
                </li>
              ))}
            </ol>
            {result.truncated ? (
              <p className="muted small">
                ⚠ Simulación truncada tras 50 pasos (posible loop).
              </p>
            ) : (
              <p className="muted small">
                <CheckCircle size={11} aria-hidden /> Fin de la simulación.
              </p>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
