import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ErpExceptionRow } from "../../lib/erpApi";
import { ExceptionsTable } from "./ExceptionsTable";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

function row(over: Partial<ErpExceptionRow> = {}): ErpExceptionRow {
  return {
    id: "e1", type: "stock_shortage", subtype: "eta_set", status: "open",
    order_id: "o1", metadata: {}, eta_date: "2026-08-01", eta_overdue: false,
    assigned_to_user_id: null, reported_by_user_id: null,
    resolution_note: null, resolved_at: null, created_at: "2026-07-31T09:00:00Z",
    ...over,
  };
}

const noop = () => {};

describe("ExceptionsTable", () => {
  it("muestra el chip de alerta cuando eta_overdue", () => {
    render(<ExceptionsTable rows={[row({ eta_overdue: true })]}
      onAssignMe={noop} onMarkSeen={noop} onResolve={noop} />);
    expect(screen.getByText(/vencida/)).toBeInTheDocument();
    expect(screen.getByTitle("ETA vencida")).toBeInTheDocument();
  });

  it("NO muestra el chip cuando eta_overdue es false", () => {
    render(<ExceptionsTable rows={[row({ eta_overdue: false })]}
      onAssignMe={noop} onMarkSeen={noop} onResolve={noop} />);
    expect(screen.queryByText(/vencida/)).not.toBeInTheDocument();
  });

  it("traduce el tipo a etiqueta legible", () => {
    render(<ExceptionsTable rows={[row({ type: "carrier_incident" })]}
      onAssignMe={noop} onMarkSeen={noop} onResolve={noop} />);
    expect(screen.getByText("Incidencia transporte")).toBeInTheDocument();
  });

  it("muestra acciones en abiertas y las oculta en resueltas", () => {
    const { rerender } = render(
      <ExceptionsTable rows={[row({ status: "open" })]}
        onAssignMe={noop} onMarkSeen={noop} onResolve={noop} />,
    );
    expect(screen.getByRole("button", { name: "Resolver" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Marcar vista" })).toBeInTheDocument();
    rerender(
      <ExceptionsTable rows={[row({ status: "resolved", resolution_note: "hecho" })]}
        onAssignMe={noop} onMarkSeen={noop} onResolve={noop} />,
    );
    expect(screen.queryByRole("button", { name: "Resolver" })).not.toBeInTheDocument();
    expect(screen.getByText(/hecho/)).toBeInTheDocument();
  });

  it("dispara las acciones con el id de la fila", async () => {
    const onResolve = jest.fn();
    const onAssignMe = jest.fn();
    const user = userEvent.setup();
    render(<ExceptionsTable rows={[row({ id: "e42" })]}
      onAssignMe={onAssignMe} onMarkSeen={noop} onResolve={onResolve} />);
    const rowEl = screen.getByRole("row", { name: /Falta de stock/ });
    await user.click(within(rowEl).getByRole("button", { name: "Resolver" }));
    expect(onResolve).toHaveBeenCalledWith("e42");
    await user.click(within(rowEl).getByRole("button", { name: "Asignarme" }));
    expect(onAssignMe).toHaveBeenCalledWith("e42");
  });
});
