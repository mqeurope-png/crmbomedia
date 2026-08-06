import { render, screen } from "@testing-library/react";
import {
  ContactSummaryTab,
  ContactSummaryPlaceholderCards,
} from "./ContactSummaryTab";
import type { ActivityEvent } from "../../lib/api";

function event(over: Partial<ActivityEvent> = {}): ActivityEvent {
  return {
    id: "e1", contact_id: "c1", system: "crm", account_id: "calls",
    external_id: "call_log:1", event_type: "CALL_LOG",
    subject: "Llamada: Interesado", body: "pidió catálogo",
    metadata: null, occurred_at: "2026-08-06T10:00:00Z",
    synced_at: "2026-08-06T10:00:00Z", created_at: "2026-08-06T10:00:00Z",
    updated_at: "2026-08-06T10:00:00Z", ...over,
  };
}

describe("ContactSummaryTab · CRM-1 llamadas en actividad reciente", () => {
  it("la llamada registrada aparece en «Actividad reciente»", () => {
    render(<ContactSummaryTab contactId="c1" events={[event()]} />);
    expect(screen.getByText("Actividad reciente")).toBeInTheDocument();
    // El subject del evento CALL_LOG que emite el backend.
    expect(screen.getByText("Llamada: Interesado")).toBeInTheDocument();
  });

  it("un CALL_LOG sin subject cae a la etiqueta «Llamada registrada»", () => {
    render(
      <ContactSummaryTab contactId="c1"
                         events={[event({ subject: null })]} />,
    );
    expect(screen.getByText("Llamada registrada")).toBeInTheDocument();
  });

  it("sin actividad lo dice", () => {
    render(<ContactSummaryTab contactId="c1" events={[]} />);
    expect(screen.getByText("Sin actividad reciente.")).toBeInTheDocument();
  });
});

describe("ContactSummaryPlaceholderCards · CRM-2 rename Pipelines", () => {
  it("el placeholder se llama «Pipelines vinculados», no «Oportunidades»", () => {
    render(<ContactSummaryPlaceholderCards />);
    expect(screen.getByText("Pipelines vinculados")).toBeInTheDocument();
    expect(screen.queryByText(/Oportunidades/)).not.toBeInTheDocument();
  });
});
