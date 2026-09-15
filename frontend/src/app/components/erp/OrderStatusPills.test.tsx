import { render, screen } from "@testing-library/react";
import { isInvoiced, OrderStatusPills } from "./OrderStatusPills";

/** Lote B7 — las cuatro pastillas de estado: verde cuando se cumple, gris
 *  cuando no, y el aria-label lo dice sin depender del color. */

function order(over = {}) {
  return {
    payment_status: "pending", invoice_status: "not_invoiced",
    factusol_invoice_number: null, factusol_cobro_status: null,
    completed: false, completed_at: null, completed_by_name: null,
    ...over,
  };
}

describe("OrderStatusPills", () => {
  it("todo en gris para un pedido recién llegado", () => {
    render(<OrderStatusPills order={order()} />);
    for (const label of ["Pagado", "Facturado", "Cobro registrado", "Completado"]) {
      const pill = screen.getByLabelText(`${label}: no`);
      expect(pill).toHaveClass("erp-status-pill", "is-off");
      expect(pill).not.toHaveClass("is-on");
    }
    expect(screen.getByRole("group", { name: "Estado del pedido" })).toBeInTheDocument();
  });

  it("todo en verde para un pedido pagado, facturado, cobrado y completado", () => {
    render(<OrderStatusPills order={order({
      payment_status: "paid", invoice_status: "invoiced_by_erp",
      factusol_invoice_number: "5-260100", factusol_cobro_status: "cobrada",
      completed: true, completed_at: "2026-09-10T18:00:00", completed_by_name: "Bart",
    })} />);
    for (const label of ["Pagado", "Facturado", "Cobro registrado", "Completado"]) {
      expect(screen.getByLabelText(`${label}: sí`)).toHaveClass("is-on");
    }
    // El completado dice cuándo y quién (solo BoHub).
    expect(screen.getByLabelText("Completado: sí"))
      .toHaveAttribute("title", "Completado el 10/9/2026 por Bart (solo BoHub)");
    expect(screen.getByLabelText("Facturado: sí"))
      .toHaveAttribute("title", expect.stringMatching(/5-260100/));
  });

  it("«Facturado» sale verde por el nº de factura O por el estado emitido; «Cobro» solo con cobrada", () => {
    const { rerender } = render(<OrderStatusPills order={order({
      invoice_status: "already_invoiced_externally", factusol_cobro_status: "pendiente",
    })} />);
    expect(screen.getByLabelText("Facturado: sí")).toBeInTheDocument();
    expect(screen.getByLabelText("Cobro registrado: no")).toBeInTheDocument();
    rerender(<OrderStatusPills order={order({ factusol_invoice_number: "260731" })} />);
    expect(screen.getByLabelText("Facturado: sí")).toBeInTheDocument();
    rerender(<OrderStatusPills order={order({ invoice_status: "error" })} />);
    expect(screen.getByLabelText("Facturado: no")).toBeInTheDocument();
  });

  it("«Pagado» es el estado del CRM: solo `paid` cuenta", () => {
    const { rerender } = render(<OrderStatusPills order={order({ payment_status: "paid" })} />);
    expect(screen.getByLabelText("Pagado: sí")).toBeInTheDocument();
    for (const st of ["partial_paid", "failed", "refunded", "credit_approved"]) {
      rerender(<OrderStatusPills order={order({ payment_status: st })} />);
      expect(screen.getByLabelText("Pagado: no")).toBeInTheDocument();
    }
  });

  it("la variante pequeña (vista lista) lleva la clase is-sm", () => {
    render(<OrderStatusPills order={order()} size="sm" />);
    expect(screen.getByRole("group", { name: "Estado del pedido" })).toHaveClass("is-sm");
  });

  it("isInvoiced comparte el criterio con el backend", () => {
    expect(isInvoiced({ invoice_status: "not_invoiced", factusol_invoice_number: null })).toBe(false);
    expect(isInvoiced({ invoice_status: "generated", factusol_invoice_number: null })).toBe(true);
    expect(isInvoiced({ invoice_status: "not_invoiced", factusol_invoice_number: "1" })).toBe(true);
  });
});
