import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PendingOrder } from "../../lib/erpApi";
import { OrderApprovalCard } from "./OrderApprovalCard";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

function order(over: Partial<PendingOrder> = {}): PendingOrder {
  return {
    id: "o1", order_number: "MAN-0001", external_source: "manual",
    store_id: null, contact_id: null, company_id: null,
    total_amount: 4890, currency: "EUR",
    payment_status: "paid", preparation_status: "pending_review",
    transport_status: "not_shipped", invoice_status: "not_invoiced",
    tracking_number: null, approved_at: null, placed_at: null,
    created_at: "2026-07-31T09:00:00Z", externally_processed_at: null,
    blockers: [], warnings: [], ...over,
  };
}

describe("OrderApprovalCard", () => {
  it("muestra «Aprobar» cuando no hay bloqueos y el rol puede aprobar", () => {
    render(<OrderApprovalCard order={order()} canApprove onApprove={() => {}} />);
    expect(screen.getByRole("button", { name: /Aprobar/ })).toBeInTheDocument();
  });

  it("OCULTA «Aprobar» si hay bloqueos activos", () => {
    render(
      <OrderApprovalCard
        order={order({
          blockers: [{ code: "open_exceptions", detail: "1 excepción sin resolver" }],
        })}
        canApprove
        onApprove={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /Aprobar/ })).not.toBeInTheDocument();
    expect(screen.getByText("open_exceptions")).toBeInTheDocument();
  });

  it("OCULTA «Aprobar» si el rol no puede aprobar (aunque no haya bloqueos)", () => {
    render(<OrderApprovalCard order={order()} canApprove={false} onApprove={() => {}} />);
    expect(screen.queryByRole("button", { name: /Aprobar/ })).not.toBeInTheDocument();
  });

  it("llama onApprove con el id al pulsar", async () => {
    const onApprove = jest.fn();
    const user = userEvent.setup();
    render(<OrderApprovalCard order={order()} canApprove onApprove={onApprove} />);
    await user.click(screen.getByRole("button", { name: /Aprobar/ }));
    expect(onApprove).toHaveBeenCalledWith("o1");
  });

  // --- externalizar (B-2-fix4/fix5) -----------------------------------------

  it("renderiza los warnings si se pasan, SIN ocultar «Aprobar» (contrato del componente)", () => {
    // El backend ya no envía warnings (B-2-fix5: array vacío), pero el
    // componente sigue soportando renderizarlos si algún día los recibe.
    render(
      <OrderApprovalCard
        order={order({ warnings: [{ code: "info", detail: "Aviso de ejemplo" }] })}
        canApprove
        onApprove={() => {}}
      />,
    );
    expect(screen.getByText("info")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Aprobar/ })).toBeInTheDocument();
  });

  it("con onMarkExternal muestra el botón (variante secondary) y lo dispara con el id", async () => {
    const onMarkExternal = jest.fn();
    const user = userEvent.setup();
    render(
      <OrderApprovalCard
        order={order()}
        canApprove
        onApprove={() => {}}
        onMarkExternal={onMarkExternal}
      />,
    );
    const btn = screen.getByRole("button", { name: /Procesado externamente/ });
    // B-2-fix5: variante con contraste visible (no «ghost», que era invisible).
    expect(btn).toHaveClass("button", "small", "secondary");
    await user.click(btn);
    expect(onMarkExternal).toHaveBeenCalledWith("o1");
  });

  it("con onToggleSelect muestra el checkbox de selección múltiple", async () => {
    const onToggleSelect = jest.fn();
    const user = userEvent.setup();
    render(
      <OrderApprovalCard
        order={order()}
        canApprove
        onApprove={() => {}}
        onToggleSelect={onToggleSelect}
      />,
    );
    await user.click(screen.getByLabelText(/Seleccionar MAN-0001/));
    expect(onToggleSelect).toHaveBeenCalledWith("o1");
  });
});
