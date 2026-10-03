import { render, screen, waitFor } from "@testing-library/react";
import ErpHome from "./page";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
jest.mock("../lib/api", () => ({
  getCurrentUser: jest.fn().mockResolvedValue({
    id: "p", role: "pedidos", full_name: "Bart Uno", email: "b@x", is_active: true,
  }),
}));
jest.mock("../lib/erpApi", () => ({
  listOrders: jest.fn().mockResolvedValue({
    items: [], queue: null, queue_counts: { por_revisar: 3, por_facturar: 7 },
  }),
  getSatQueue: jest.fn().mockResolvedValue({ preparing: [{}], ready_for_pickup: [{}] }),
  getCuadreResumen: jest.fn().mockResolvedValue({
    contadores: { alta: 0, media: 0, baja: 4, total: 4 },
  }),
}));

describe("ErpHome", () => {
  it("muestra accesos de ERP y contadores para un usuario de ERP", async () => {
    window.history.replaceState({}, "", "/erp");
    render(<ErpHome />);
    expect(await screen.findByText("ERP · Pedidos")).toBeInTheDocument();
    expect(screen.queryByText("Contactos")).not.toBeInTheDocument();
    // Contador de pedidos pendientes (best-effort).
    expect(
      await screen.findByText("Pedidos pendientes de aprobación"),
    ).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    // Cuenta lo mismo que la cola «Por revisar» de la bandeja por defecto:
    // sin completar y con todos los pagos (también los pendientes de pago).
    const { listOrders } = jest.requireMock("../lib/erpApi");
    expect(listOrders).toHaveBeenCalledWith({ completed: false, limit: 1 });
    // Lote 2 D: lleva a la bandeja filtrada por «Por revisar» (la Cola
    // PEDIDOS ya no es pantalla aparte).
    expect(screen.getByRole("link", { name: /Pedidos pendientes de aprobación/ }))
      .toHaveAttribute("href", "/erp/orders?queue=por_revisar");
  });

  it("sin descuadres de severidad alta o media no enseña la tarjeta del Cuadre", async () => {
    window.history.replaceState({}, "", "/erp");
    render(<ErpHome />);
    expect(await screen.findByText("Pedidos pendientes de aprobación")).toBeInTheDocument();
    const { getCuadreResumen } = jest.requireMock("../lib/erpApi");
    await waitFor(() => expect(getCuadreResumen).toHaveBeenCalled());
    expect(screen.queryByText(/en el Cuadre/)).not.toBeInTheDocument();
  });

  it("con descuadres de severidad alta o media enseña «N descuadres» y lleva al Cuadre", async () => {
    window.history.replaceState({}, "", "/erp");
    const { getCuadreResumen } = jest.requireMock("../lib/erpApi");
    getCuadreResumen.mockResolvedValueOnce({ contadores: { alta: 2, media: 5, baja: 9, total: 16 } });
    render(<ErpHome />);
    const card = await screen.findByRole("link", { name: /Descuadres en el Cuadre/ });
    expect(card).toHaveAttribute("href", "/erp/cuadre");
    expect(card).toHaveTextContent("7");
  });

  it("enseña el aviso cuando se llega desde una URL del CRM (?desde)", async () => {
    window.history.replaceState({}, "", "/erp?desde=%2Fcontacts");
    render(<ErpHome />);
    expect(await screen.findByText(/es del CRM/i)).toBeInTheDocument();
    // y limpia la query de la barra.
    await waitFor(() => expect(window.location.search).toBe(""));
  });
});
