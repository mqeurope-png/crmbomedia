"use client";

import type { ReactNode } from "react";
import type { WorkflowAlert } from "../../../lib/erpApi";

/** Alertas accionables del pedido (las calcula el backend). En la ficha van
 *  como barra arriba del todo (`variant="bar"`); en la bandeja, como línea
 *  compacta dentro de la tarjeta del pedido (`variant="inline"`).
 *
 *  `renderAction` deja a cada pantalla poner el botón que resuelve la alerta
 *  («Ver ficha cliente», «Mapear líneas»…) sin duplicar el texto. */
export function WorkflowAlerts({
  alerts, variant = "bar", renderAction, max,
}: {
  alerts: WorkflowAlert[];
  variant?: "bar" | "inline";
  renderAction?: (alert: WorkflowAlert) => ReactNode;
  /** Máximo de alertas a pintar (la bandeja enseña solo la primera). */
  max?: number;
}) {
  if (alerts.length === 0) return null;
  const shown = max ? alerts.slice(0, max) : alerts;
  const restantes = alerts.length - shown.length;
  if (variant === "inline") {
    return (
      <div className="erp-flow-why-list">
        {shown.map((a) => (
          <p key={a.code} className={`erp-flow-why${a.blocking ? " is-blocking" : ""}`}>
            <span aria-hidden>!</span> {a.text}
          </p>
        ))}
        {restantes > 0 ? (
          <p className="erp-flow-why muted">+{restantes} aviso(s) más</p>
        ) : null}
      </div>
    );
  }
  return (
    <div
      className={`erp-flow-alertbar${alerts.some((a) => a.blocking) ? " is-blocking" : ""}`}
      role="alert"
      aria-label="Alertas del pedido"
    >
      {shown.map((a) => (
        <div key={a.code} className="erp-flow-alert">
          <span aria-hidden>!</span>
          <span>{a.text}</span>
          {renderAction ? <span className="erp-flow-alert-fix">{renderAction(a)}</span> : null}
        </div>
      ))}
    </div>
  );
}
