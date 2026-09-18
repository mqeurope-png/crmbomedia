import { render, screen, within } from "@testing-library/react";
import { isInvoiced, OrderStatusPills } from "./OrderStatusPills";

/** Lote B7 → Lote 2: el estado del pedido en la bandeja es la rejilla fija de
 *  cuatro casillas más la pastilla «Completado». Los aria-label del contrato
 *  anterior («Pagado: sí» + is-on) se mantienen: los usa la bandeja. */

function order(over = {}) {
  return {
    payment_status: "pending", invoice_status: "not_invoiced",
    factusol_invoice_number: null, factusol_cobro_status: null,
    transport_status: "not_shipped",
    completed: false, completed_at: null, completed_by_name: null,
    ...over,
  };
}

describe("OrderStatusPills", () => {
  it("pedido recién llegado: nada hecho (is-off) y la rejilla presente", () => {
    render(<OrderStatusPills order={order()} />);
    for (const label of ["Pagado", "Facturado", "Cobro registrado", "Enviado"]) {
      const v = screen.getByLabelText(`${label}: no`);
      expect(v).toHaveClass("erp-status-cell-v", "is-off");
      expect(v).not.toHaveClass("is-on");
    }
    expect(screen.getByLabelText("Completado: no")).toHaveClass("erp-status-pill", "is-off");
    const grid = screen.getByRole("group", { name: "Estado del pedido" });
    expect(within(grid).getAllByRole("img")).toHaveLength(4);
    expect(screen.getByRole("img", { name: "Cobro: no aplica" })).toBeInTheDocument();
  });

  it("todo hecho para un pedido pagado, facturado, cobrado, enviado y completado", () => {
    render(<OrderStatusPills order={order({
      payment_status: "paid", invoice_status: "invoiced_by_erp",
      factusol_invoice_number: "5-260100", factusol_cobro_status: "cobrada",
      transport_status: "in_transit",
      completed: true, completed_at: "2026-09-10T18:00:00", completed_by_name: "Bart",
    })} />);
    for (const label of ["Pagado", "Facturado", "Cobro registrado", "Enviado"]) {
      expect(screen.getByLabelText(`${label}: sí`)).toHaveClass("is-on");
    }
    for (const k of ["Pago", "Factura", "Cobro", "Envío"]) {
      expect(screen.getByRole("img", { name: `${k}: hecho` })).toHaveClass("is-done");
    }
    // El completado dice cuándo y quién (solo BoHub).
    expect(screen.getByLabelText("Completado: sí"))
      .toHaveAttribute("title", "Completado el 10/9/2026 por Bart (solo BoHub)");
    expect(screen.getByRole("img", { name: "Factura: hecho" }))
      .toHaveAttribute("title", expect.stringMatching(/5-260100/));
  });

  it("«Facturado» sale por el nº de factura O por el estado emitido; el error bloquea la casilla", () => {
    const { rerender } = render(<OrderStatusPills order={order({
      invoice_status: "already_invoiced_externally", factusol_cobro_status: "pendiente",
    })} />);
    expect(screen.getByLabelText("Facturado: sí")).toBeInTheDocument();
    expect(screen.getByLabelText("Cobro registrado: no")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Cobro: pendiente" })).toBeInTheDocument();
    rerender(<OrderStatusPills order={order({ factusol_invoice_number: "260731" })} />);
    expect(screen.getByLabelText("Facturado: sí")).toBeInTheDocument();
    rerender(<OrderStatusPills order={order({ invoice_status: "error" })} />);
    expect(screen.getByLabelText("Facturado: no")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Factura: bloqueado" })).toHaveClass("is-blocked");
  });

  it("«Pagado» es el estado del CRM: solo `paid` cuenta; fallido/devuelto bloquean", () => {
    const { rerender } = render(<OrderStatusPills order={order({ payment_status: "paid" })} />);
    expect(screen.getByLabelText("Pagado: sí")).toBeInTheDocument();
    for (const st of ["partial_paid", "failed", "refunded", "credit_approved"]) {
      rerender(<OrderStatusPills order={order({ payment_status: st })} />);
      expect(screen.getByLabelText("Pagado: no")).toBeInTheDocument();
    }
    expect(screen.getByRole("img", { name: "Pago: pendiente" })).toBeInTheDocument();
    rerender(<OrderStatusPills order={order({ payment_status: "failed" })} />);
    expect(screen.getByRole("img", { name: "Pago: bloqueado" })).toBeInTheDocument();
  });

  it("la variante pequeña (vista lista) lleva is-sm en el envoltorio y en la rejilla", () => {
    const { container } = render(<OrderStatusPills order={order()} size="sm" className="erp-flow-item-pills" />);
    expect(container.firstChild).toHaveClass("erp-status-pills", "is-sm", "erp-flow-item-pills");
    expect(screen.getByRole("group", { name: "Estado del pedido" })).toHaveClass("is-sm");
  });

  it("«Factura enviada»: enviada (verde), sin enviar (ámbar) y nada sin factura", () => {
    // Sin factura: no aplica → no hay pastilla de «Factura enviada».
    const { rerender } = render(<OrderStatusPills order={order()} />);
    expect(screen.queryByLabelText(/^Factura enviada:/)).toBeNull();
    // Con factura pero sin enviar: ámbar.
    rerender(<OrderStatusPills order={order({
      invoice_status: "invoiced_by_erp", factusol_invoice_number: "5-1",
    })} />);
    const sinEnviar = screen.getByLabelText("Factura enviada: no");
    expect(sinEnviar).toHaveClass("erp-status-pill", "is-warn");
    expect(sinEnviar).toHaveTextContent("Factura sin enviar");
    // Con factura enviada: verde, con la fecha en el tooltip.
    rerender(<OrderStatusPills order={order({
      invoice_status: "invoiced_by_erp", factusol_invoice_number: "5-1",
      invoice_emailed_at: "2026-09-12T10:00:00",
    })} />);
    const enviada = screen.getByLabelText("Factura enviada: sí");
    expect(enviada).toHaveClass("is-on");
    expect(enviada).toHaveTextContent("Factura enviada");
    expect(enviada).toHaveAttribute("title", expect.stringContaining("12/9/2026"));
  });

  it("isInvoiced comparte el criterio con el backend", () => {
    expect(isInvoiced({ invoice_status: "not_invoiced", factusol_invoice_number: null })).toBe(false);
    expect(isInvoiced({ invoice_status: "generated", factusol_invoice_number: null })).toBe(true);
    expect(isInvoiced({ invoice_status: "not_invoiced", factusol_invoice_number: "1" })).toBe(true);
  });
});
