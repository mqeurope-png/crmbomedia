import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SatIncidenciasTab } from "./SatIncidenciasTab";
import type { SatIncidenciaRow } from "../../lib/erpApi";
import {
  getSatIncidencias,
  resolveException,
  resolveShippingIncidencia,
} from "../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

jest.mock("../../lib/erpApi", () => ({
  customerLabel: jest.requireActual("../../lib/erpApi").customerLabel,
  getSatIncidencias: jest.fn(),
  resolveException: jest.fn(),
  resolveShippingIncidencia: jest.fn(),
  STATUS_LABELS: {
    incident: { label: "Incidencia", tone: "bad" },
    in_transit: { label: "En tránsito", tone: "ok" },
  },
}));

const mockGet = getSatIncidencias as jest.Mock;
const mockResolveEnvio = resolveShippingIncidencia as jest.Mock;
const mockResolveExc = resolveException as jest.Mock;

function envio(over: Partial<SatIncidenciaRow> = {}): SatIncidenciaRow {
  return {
    order_id: "o-envio", order_number: "BOP-10",
    contact_name: "Ana Pi", company_name: null,
    tipo: "envio", motivo: "Dirección incorrecta",
    exception_id: null, exception_type: null,
    transport_status: "incident", tracking_number: "TRK-1",
    store_slug: null, placed_at: "2026-09-01T10:00:00+00:00", ...over,
  };
}

function pedido(over: Partial<SatIncidenciaRow> = {}): SatIncidenciaRow {
  return {
    order_id: "o-pedido", order_number: "BOP-20",
    contact_name: null, company_name: "Duplicoder SL",
    tipo: "pedido", motivo: "No quedan unidades",
    exception_id: "exc-1", exception_type: "Falta de stock",
    transport_status: "not_shipped", tracking_number: null,
    store_slug: null, placed_at: "2026-08-01T10:00:00+00:00", ...over,
  };
}

beforeEach(() => {
  mockGet.mockReset();
  mockResolveEnvio.mockReset();
  mockResolveExc.mockReset();
});

describe("SatIncidenciasTab", () => {
  it("lista incidencias de envío y de pedido con su tipo, motivo y estado; informa del contador", async () => {
    mockGet.mockResolvedValue({ items: [envio(), pedido()] });
    const onCount = jest.fn();
    render(
      <SatIncidenciasTab
        filters={{}} canResolveEnvio canResolvePedido onCount={onCount}
      />,
    );
    // Ambas filas, con su badge de tipo.
    expect(await screen.findByText("BOP-10")).toBeInTheDocument();
    expect(screen.getByText("BOP-20")).toBeInTheDocument();
    expect(screen.getByText("Envío")).toBeInTheDocument();
    expect(screen.getByText("Pedido")).toBeInTheDocument();
    expect(screen.getByText("Dirección incorrecta")).toBeInTheDocument();
    expect(screen.getByText("No quedan unidades")).toBeInTheDocument();
    // La etiqueta del tipo de excepción se ve junto al badge «Pedido».
    expect(screen.getByText("Falta de stock")).toBeInTheDocument();
    // El chip de estado de envío usa STATUS_LABELS.
    expect(screen.getByText("Incidencia")).toBeInTheDocument();
    await waitFor(() => expect(onCount).toHaveBeenCalledWith(2));
  });

  it("resolver una incidencia de ENVÍO llama resolveShippingIncidencia y saca la fila", async () => {
    mockGet.mockResolvedValue({ items: [envio()] });
    mockResolveEnvio.mockResolvedValue({ order_id: "o-envio", transport_status: "in_transit" });
    const onResolved = jest.fn();
    const onCount = jest.fn();
    const user = userEvent.setup();
    render(
      <SatIncidenciasTab
        filters={{}} canResolveEnvio canResolvePedido
        onResolved={onResolved} onCount={onCount}
      />,
    );
    await user.click(await screen.findByRole("button", { name: "Resolver" }));
    await waitFor(() => expect(mockResolveEnvio).toHaveBeenCalledWith("o-envio"));
    expect(mockResolveExc).not.toHaveBeenCalled();
    await waitFor(() => expect(onResolved).toHaveBeenCalled());
    // La fila desaparece y el contador baja a 0.
    await waitFor(() => expect(screen.queryByText("BOP-10")).not.toBeInTheDocument());
    expect(onCount).toHaveBeenLastCalledWith(0);
  });

  it("resolver una incidencia de PEDIDO cierra la excepción (resolveException con su id)", async () => {
    mockGet.mockResolvedValue({ items: [pedido()] });
    mockResolveExc.mockResolvedValue({});
    const user = userEvent.setup();
    render(<SatIncidenciasTab filters={{}} canResolveEnvio canResolvePedido />);
    await user.click(await screen.findByRole("button", { name: "Resolver" }));
    await waitFor(() => expect(mockResolveExc).toHaveBeenCalledWith("exc-1"));
    expect(mockResolveEnvio).not.toHaveBeenCalled();
  });

  it("sin el permiso correspondiente muestra «Sin permiso» en vez del botón", async () => {
    mockGet.mockResolvedValue({ items: [envio(), pedido()] });
    render(
      <SatIncidenciasTab filters={{}} canResolveEnvio={false} canResolvePedido={false} />,
    );
    await screen.findByText("BOP-10");
    expect(screen.queryByRole("button", { name: "Resolver" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Sin permiso")).toHaveLength(2);
  });

  it("un error al resolver se muestra en la propia fila y no saca la incidencia", async () => {
    mockGet.mockResolvedValue({ items: [envio()] });
    mockResolveEnvio.mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    render(<SatIncidenciasTab filters={{}} canResolveEnvio canResolvePedido />);
    await user.click(await screen.findByRole("button", { name: "Resolver" }));
    expect(await screen.findByText("boom")).toBeInTheDocument();
    // La fila sigue ahí.
    expect(screen.getByText("BOP-10")).toBeInTheDocument();
  });

  it("sin incidencias enseña el mensaje vacío", async () => {
    mockGet.mockResolvedValue({ items: [] });
    render(<SatIncidenciasTab filters={{}} canResolveEnvio canResolvePedido />);
    expect(await screen.findByText(/Sin incidencias/)).toBeInTheDocument();
  });
});
