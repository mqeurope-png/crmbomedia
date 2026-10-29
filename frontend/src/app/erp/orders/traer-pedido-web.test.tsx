import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrdersPage from "./page";
import { getCurrentUser } from "../../lib/api";
import { listOrders } from "../../lib/erpApi";

jest.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));
// La cabecera lleva los botones de alta: el mock los pinta.
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <><h1>{title}</h1>{actions}</>
  ),
}));
jest.mock("../../components/erp/OrderStatusBadge", () => ({
  OrderStatusBadge: ({ status }: { status: string }) => <span>{status}</span>,
}));
// El modal tiene sus propios tests; aquí solo importa que se abra y se cierre.
jest.mock("../../components/erp/TraerPedidoWebModal", () => ({
  TraerPedidoWebModal: ({ onClose }: { onClose: () => void }) => (
    <div role="dialog" aria-label="Traer un pedido de la web">
      <button type="button" onClick={onClose}>Cerrar</button>
    </div>
  ),
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(),
}));
jest.mock("../../lib/erpApi", () => ({
  WORKFLOW_QUEUES: [
    "por_revisar", "por_facturar", "factura_por_enviar", "por_cobrar", "incidencias", "listo",
  ],
  customerHeadline: () => "",
  customerLabel: () => "",
  listOrders: jest.fn(),
  getErpSettings: jest.fn(() => Promise.resolve({})),
}));

const BOTON = "Traer un pedido de la web";

beforeEach(() => {
  window.localStorage.clear();
  (listOrders as jest.Mock).mockResolvedValue({ items: [], queue_counts: {}, queue: null });
});

async function pintarCon(user: object) {
  (getCurrentUser as jest.Mock).mockResolvedValue(user);
  render(<ErpOrdersPage />);
  // La bandeja ya ha cargado (con o sin el botón).
  await screen.findByText(/No hay pedidos/);
}

describe("ERP · Pedidos — «Traer un pedido de la web»", () => {
  it("sale con crear pedidos Y ver los web (pedidos, admin) y abre el modal", async () => {
    const user = userEvent.setup();
    await pintarCon({
      role: "user", capabilities: ["erp.access", "erp.orders.create", "erp.orders.view_web"],
    });
    const boton = await screen.findByRole("button", { name: BOTON });
    await user.click(boton);
    expect(screen.getByRole("dialog", { name: BOTON })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByRole("dialog", { name: BOTON })).not.toBeInTheDocument();
  });

  it.each(["pedidos", "admin"])("sale para el rol %s", async (role) => {
    await pintarCon({ role });
    expect(await screen.findByRole("button", { name: BOTON })).toBeInTheDocument();
  });

  it("no sale sin ver los pedidos web (el Comercial crea pedidos pero no ve los web)", async () => {
    await pintarCon({ role: "comercial" });
    expect(await screen.findByText("+ Nuevo pedido manual")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: BOTON })).not.toBeInTheDocument();
  });

  it("no sale sin crear pedidos (el taller ve los web pero no crea pedidos)", async () => {
    await pintarCon({ role: "sat" });
    // El usuario ya está cargado: el taller sí da de alta muestras.
    expect(await screen.findByText("+ Nuevo envío / muestra")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: BOTON })).not.toBeInTheDocument();
  });
});
