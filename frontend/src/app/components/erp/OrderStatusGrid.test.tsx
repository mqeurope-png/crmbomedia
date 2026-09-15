import { render, screen, within } from "@testing-library/react";
import { isInvoiced, OrderStatusGrid, orderStatusCells, STATE_WORD } from "./OrderStatusGrid";

/** Lote 2 · E4 — rejilla fija Pago · Factura · Cobro · Envío: misma posición
 *  siempre, verde/ámbar/gris/rojo, cada casilla con su palabra y la del
 *  estado (funciona sin distinguir colores) y nunca pulsable. */

function order(over = {}) {
  return {
    payment_status: "pending", invoice_status: "not_invoiced",
    factusol_invoice_number: null, factusol_cobro_status: null,
    transport_status: "not_shipped",
    ...over,
  };
}

function cells() {
  return within(screen.getByRole("group", { name: "Estado del pedido" })).getAllByRole("img");
}

describe("OrderStatusGrid", () => {
  it("pinta las cuatro casillas siempre en el mismo orden, con su palabra", () => {
    render(<OrderStatusGrid order={order()} />);
    const c = cells();
    expect(c).toHaveLength(4);
    expect(c.map((el) => el.getAttribute("data-cell"))).toEqual(["pago", "factura", "cobro", "envio"]);
    expect(c.map((el) => el.textContent)).toEqual([
      "PagoPendiente", "FacturaPor emitir", "Cobro—", "EnvíoPendiente",
    ]);
  });

  it("pedido recién llegado: pago/factura/envío pendientes y cobro «no aplica»", () => {
    render(<OrderStatusGrid order={order()} />);
    expect(screen.getByRole("img", { name: "Pago: pendiente" })).toHaveClass("erp-status-cell", "is-pending");
    expect(screen.getByRole("img", { name: "Factura: pendiente" })).toHaveClass("is-pending");
    expect(screen.getByRole("img", { name: "Cobro: no aplica" })).toHaveClass("is-na");
    expect(screen.getByRole("img", { name: "Envío: pendiente" })).toHaveClass("is-pending");
  });

  it("todo hecho para un pedido pagado, facturado, cobrado y entregado", () => {
    render(<OrderStatusGrid order={order({
      payment_status: "paid", invoice_status: "invoiced_by_erp",
      factusol_invoice_number: "5-260100", factusol_cobro_status: "cobrada",
      transport_status: "delivered",
    })} />);
    for (const k of ["Pago", "Factura", "Cobro", "Envío"]) {
      expect(screen.getByRole("img", { name: `${k}: hecho` })).toHaveClass("is-done");
    }
    expect(screen.getByRole("img", { name: "Factura: hecho" }))
      .toHaveAttribute("title", "Factura emitida (5-260100).");
    expect(screen.getByRole("img", { name: "Envío: hecho" })).toHaveTextContent("Entregado");
  });

  it("bloqueado en rojo: pago fallido/devuelto, factura con error, envío con incidencia/devuelto", () => {
    const { rerender } = render(<OrderStatusGrid order={order({
      payment_status: "failed", invoice_status: "error", transport_status: "incident",
    })} />);
    expect(screen.getByRole("img", { name: "Pago: bloqueado" })).toHaveClass("is-blocked");
    expect(screen.getByRole("img", { name: "Pago: bloqueado" })).toHaveTextContent("Fallido");
    expect(screen.getByRole("img", { name: "Factura: bloqueado" })).toHaveTextContent("Error");
    expect(screen.getByRole("img", { name: "Envío: bloqueado" })).toHaveTextContent("Incidencia");
    rerender(<OrderStatusGrid order={order({ payment_status: "refunded", transport_status: "returned" })} />);
    expect(screen.getByRole("img", { name: "Pago: bloqueado" })).toHaveTextContent("Devuelto");
    expect(screen.getByRole("img", { name: "Envío: bloqueado" })).toHaveTextContent("Devuelto");
  });

  it("cobro: pendiente solo cuando hay factura; hecho solo con «cobrada»", () => {
    const { rerender } = render(<OrderStatusGrid order={order({
      invoice_status: "generated", factusol_cobro_status: "pendiente",
    })} />);
    expect(screen.getByRole("img", { name: "Cobro: pendiente" })).toHaveClass("is-pending");
    rerender(<OrderStatusGrid order={order({ factusol_invoice_number: "260731", factusol_cobro_status: "cobrada" })} />);
    expect(screen.getByRole("img", { name: "Cobro: hecho" })).toHaveTextContent("Cobrado");
    rerender(<OrderStatusGrid order={order({ factusol_cobro_status: "pendiente" })} />);
    expect(screen.getByRole("img", { name: "Cobro: no aplica" })).toBeInTheDocument();
  });

  it("envío: en camino y «ya enviado fuera» cuentan como hecho; etiqueta creada sigue pendiente", () => {
    const { rerender } = render(<OrderStatusGrid order={order({ transport_status: "in_transit" })} />);
    expect(screen.getByRole("img", { name: "Envío: hecho" })).toHaveTextContent("Enviado");
    rerender(<OrderStatusGrid order={order({ transport_status: "already_shipped_externally" })} />);
    expect(screen.getByRole("img", { name: "Envío: hecho" })).toBeInTheDocument();
    rerender(<OrderStatusGrid order={order({ transport_status: "label_created" })} />);
    expect(screen.getByRole("img", { name: "Envío: pendiente" })).toHaveTextContent("Etiqueta lista");
    // Sin `transport_status` (objetos parciales) también es pendiente.
    rerender(<OrderStatusGrid order={order({ transport_status: undefined })} />);
    expect(screen.getByRole("img", { name: "Envío: pendiente" })).toBeInTheDocument();
  });

  it("pago: parcial y a crédito son pendientes (ámbar), solo `paid` es hecho", () => {
    const { rerender } = render(<OrderStatusGrid order={order({ payment_status: "partial_paid" })} />);
    expect(screen.getByRole("img", { name: "Pago: pendiente" })).toHaveTextContent("Parcial");
    rerender(<OrderStatusGrid order={order({ payment_status: "credit_approved" })} />);
    expect(screen.getByRole("img", { name: "Pago: pendiente" })).toHaveTextContent("A crédito");
    rerender(<OrderStatusGrid order={order({ payment_status: "paid" })} />);
    expect(screen.getByRole("img", { name: "Pago: hecho" })).toHaveTextContent("Pagado");
  });

  it("conserva los aria-label del contrato anterior en el valor («Pagado: sí» con is-on)", () => {
    render(<OrderStatusGrid order={order({
      payment_status: "paid", invoice_status: "generated", factusol_cobro_status: "pendiente",
    })} />);
    expect(screen.getByLabelText("Pagado: sí")).toHaveClass("erp-status-cell-v", "is-on");
    expect(screen.getByLabelText("Facturado: sí")).toHaveClass("is-on");
    expect(screen.getByLabelText("Cobro registrado: no")).toHaveClass("is-off");
    expect(screen.getByLabelText("Enviado: no")).toHaveClass("is-off");
  });

  it("las casillas informan y no se pulsan; la variante compacta lleva is-sm", () => {
    render(<OrderStatusGrid order={order()} size="sm" className="extra" />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByRole("group", { name: "Estado del pedido" })).toHaveClass("erp-status-grid", "is-sm", "extra");
  });

  it("orderStatusCells e isInvoiced comparten el criterio con el backend", () => {
    expect(orderStatusCells(order()).map((c) => c.key)).toEqual(["pago", "factura", "cobro", "envio"]);
    expect(orderStatusCells(order()).map((c) => STATE_WORD[c.state])).toEqual([
      "pendiente", "pendiente", "no aplica", "pendiente",
    ]);
    expect(isInvoiced({ invoice_status: "not_invoiced", factusol_invoice_number: null })).toBe(false);
    expect(isInvoiced({ invoice_status: "generated", factusol_invoice_number: null })).toBe(true);
    expect(isInvoiced({ invoice_status: "not_invoiced", factusol_invoice_number: "1" })).toBe(true);
  });
});
