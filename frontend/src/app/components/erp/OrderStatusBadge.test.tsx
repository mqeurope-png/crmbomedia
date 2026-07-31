import { render, screen } from "@testing-library/react";
import { OrderStatusBadge } from "./OrderStatusBadge";

describe("OrderStatusBadge", () => {
  it("mapea el estado a su label y tono", () => {
    const { container } = render(<OrderStatusBadge status="paid" />);
    expect(screen.getByText("Pagado")).toBeInTheDocument();
    expect(container.querySelector(".badge.ok")).toBeInTheDocument();
  });

  it("usa tono bad para bloqueado y error", () => {
    const { container, rerender } = render(<OrderStatusBadge status="blocked" />);
    expect(container.querySelector(".badge.bad")).toBeInTheDocument();
    rerender(<OrderStatusBadge status="error" />);
    expect(screen.getByText("Error factura")).toBeInTheDocument();
  });

  it("cae a muted con el valor crudo si el estado es desconocido", () => {
    const { container } = render(<OrderStatusBadge status="xyz" />);
    expect(screen.getByText("xyz")).toBeInTheDocument();
    expect(container.querySelector(".badge.muted")).toBeInTheDocument();
  });
});
