import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { OrderDetail } from "../../lib/erpApi";
import { OrderStatusMachine } from "./OrderStatusMachine";

function detail(over: Partial<OrderDetail> = {}): OrderDetail {
  return {
    id: "o1", order_number: "MAN-0001", external_source: "manual",
    store_id: null, contact_id: null, company_id: null,
    total_amount: 100, currency: "EUR",
    payment_status: "paid", preparation_status: "preparing",
    transport_status: "not_shipped", invoice_status: "not_invoiced",
    tracking_number: null, approved_at: null, placed_at: null,
    created_at: "2026-07-31T09:00:00Z",
    externally_processed_at: null, externally_processed_note: null,
    factusol_invoice_number: null,
    externally_processed_by_user_id: null,
    notes: null, packing: null, lines: [], status_history: [], exceptions: [],
    blockers: [], warnings: [],
    available_transitions: {
      payment: [],
      preparation: [
        { to_status: "packed", label: "Embalado", required_evidence: [] },
        { to_status: "blocked", label: "Bloquear (problema)", required_evidence: ["reason"] },
      ],
      transport: [],
      invoice: [],
    },
    ...over,
  };
}

describe("OrderStatusMachine", () => {
  it("pinta los 4 dominios con su estado", () => {
    render(<OrderStatusMachine order={detail()} onFire={() => {}} />);
    expect(screen.getByText("Pago")).toBeInTheDocument();
    expect(screen.getByText("Preparación")).toBeInTheDocument();
    expect(screen.getByText("Transporte")).toBeInTheDocument();
    expect(screen.getByText("Facturación")).toBeInTheDocument();
    expect(screen.getByText("Preparando")).toBeInTheDocument();
  });

  it("renderiza un botón por transición disponible y dispara onFire", async () => {
    const onFire = jest.fn();
    const user = userEvent.setup();
    render(<OrderStatusMachine order={detail()} onFire={onFire} />);
    await user.click(screen.getByRole("button", { name: "Embalado" }));
    expect(onFire).toHaveBeenCalledWith(
      "preparation",
      expect.objectContaining({ to_status: "packed" }),
    );
  });

  it("no muestra botones en dominios sin transiciones disponibles", () => {
    render(<OrderStatusMachine order={detail()} onFire={() => {}} />);
    // payment/transport/invoice están vacíos → solo 2 botones (preparación).
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });
});
