"use client";

import { useState } from "react";
import { SAT_QUEUE } from "../mocks";

/** Pantalla 3 — Cola SAT táctil: lista priorizada, botones grandes. */
export default function SatQueuePage() {
  const [queue, setQueue] = useState(SAT_QUEUE);

  function advance(id: string) {
    setQueue((q) => q.map((item) => {
      if (item.id !== id) return item;
      if (item.status === "queued") return { ...item, status: "preparing" };
      if (item.status === "preparing") return { ...item, status: "packed" };
      return item;
    }).filter((item) => item.status !== "packed"));
  }
  function block(id: string) {
    setQueue((q) => q.map((item) => (item.id === id ? { ...item, status: "blocked" } : item)));
  }

  return (
    <main>
      <h1 className="erp-h1">Cola SAT</h1>
      <p className="erp-sub">
        Pantalla táctil del taller. Al marcar «Embalado» el pedido sale de la cola
        (y en el real pediría la foto del bulto si Bart la hace obligatoria).
      </p>
      <div className="erp-sat">
        {queue.length === 0 ? <p>🎉 Cola vacía.</p> : null}
        {queue.map((item) => (
          <div key={item.id} className={`erp-sat-card${item.status === "blocked" ? " blocked" : ""}`}>
            <h3>{item.number}</h3>
            <div>{item.customer}{!item.paid ? <span className="erp-unpaid"> · ⚠ SIN COBRAR</span> : null}</div>
            <ul className="erp-sat-lines">
              {item.lines.map((l) => <li key={l}>{l}</li>)}
            </ul>
            {item.status === "queued" ? (
              <button className="erp-sat-btn start" type="button" onClick={() => advance(item.id)}>▶ EMPEZAR</button>
            ) : null}
            {item.status === "preparing" ? (
              <button className="erp-sat-btn pack" type="button" onClick={() => advance(item.id)}>📦 EMBALADO</button>
            ) : null}
            {item.status === "blocked" ? (
              <div><span className="erp-chip bad">Bloqueado</span> — resolver desde oficina</div>
            ) : (
              <button className="erp-sat-btn issue" type="button" onClick={() => block(item.id)}>⚠ Reportar problema</button>
            )}
          </div>
        ))}
      </div>
    </main>
  );
}
