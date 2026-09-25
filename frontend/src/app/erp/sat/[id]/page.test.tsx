import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SatOrderWorkPage from "./page";
import {
  fireTransition, getOrder, setPackages, transitionPacked, type OrderDetail,
} from "../../../lib/erpApi";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "o1" }),
  useRouter: () => ({ push: mockPush, replace: jest.fn() }),
}));

jest.mock("../../../lib/api", () => ({
  getCurrentUser: jest.fn().mockResolvedValue({ role: "sat", roles: ["sat"] }),
}));

jest.mock("../../../lib/erpApi", () => ({
  STATUS_LABELS: jest.requireActual("../../../lib/erpApi").STATUS_LABELS,
  EXCEPTION_CATALOG: jest.requireActual("../../../lib/erpApi").EXCEPTION_CATALOG,
  getOrder: jest.fn(),
  attachDocument: jest.fn(),
  fireTransition: jest.fn(),
  reportException: jest.fn(),
  setPackages: jest.fn(),
  transitionPacked: jest.fn(),
  printShippingFile: jest.fn(),
}));

const mockGet = getOrder as jest.Mock;

function detail(over: Partial<OrderDetail> = {}): OrderDetail {
  return {
    id: "o1", order_number: "BOP-1", preparation_status: "preparing", packing: null,
    notes: null, serial_number: null, whiterip_license: null, shipping_origin: null,
    lines: [{ id: "l1", quantity: 2, description: "Art A", product_sku: "A" }],
    ...over,
  } as unknown as OrderDetail;
}

afterEach(() => {
  Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
});

/** Lote 2 · PR-2: el modo trabajo enseña lo mismo que la card — observaciones
 *  del comercial arriba (solo si hay) y datos técnicos con «copiar». */
describe("SatOrderWorkPage (modo trabajo)", () => {
  it("observaciones en ámbar antes de las líneas y datos técnicos con copiar", async () => {
    const user = userEvent.setup();
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    mockGet.mockResolvedValue(detail({
      notes: "Cliente pide embalaje reforzado.", serial_number: "FLX-7741-2026",
      whiterip_license: "WR-4C-88231", shipping_origin: "SAT",
    }));
    render(<SatOrderWorkPage />);
    expect(await screen.findByRole("heading", { name: "BOP-1" })).toBeInTheDocument();
    const note = screen.getByRole("note", { name: "Observaciones del comercial" });
    expect(note).toHaveTextContent("Cliente pide embalaje reforzado.");
    const lines = screen.getByRole("heading", { name: "Líneas" });
    expect(note.compareDocumentPosition(lines) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("FLX-7741-2026")).toHaveClass("sat-tech-value");
    await user.click(screen.getByRole("button", { name: "Copiar nº de serie" }));
    expect(writeText).toHaveBeenCalledWith("FLX-7741-2026");
    expect(await screen.findByText("Copiado")).toBeInTheDocument();
    expect(screen.getByText("SAT")).toHaveClass("sat-origin-pill");
    // Lo de siempre sigue: líneas y, en preparación, embalar AQUÍ (en línea).
    expect(screen.getByText("Art A")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "📦 Embalar" })).toBeInTheDocument();
  });

  it("sin nota no hay bloque de observaciones; sin datos, cajas con «—»", async () => {
    mockGet.mockResolvedValue(detail());
    render(<SatOrderWorkPage />);
    await screen.findByRole("heading", { name: "BOP-1" });
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.getByText("Nº de serie")).toBeInTheDocument();
    expect(screen.getByText("Licencia WhiteRIP")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByRole("button", { name: /Copiar/ })).not.toBeInTheDocument();
  });

  it("«Empezar preparación» NO saca del pedido: aparecen aquí los bultos y «Embalar», y al embalar sigue abierto", async () => {
    const user = userEvent.setup();
    (fireTransition as jest.Mock).mockResolvedValue({});
    (setPackages as jest.Mock).mockResolvedValue([]);
    (transitionPacked as jest.Mock).mockResolvedValue({});
    mockGet
      .mockResolvedValueOnce(detail({ preparation_status: "in_queue" }))
      .mockResolvedValueOnce(detail({ preparation_status: "preparing" }))
      .mockResolvedValue(detail({ preparation_status: "packed" }));
    render(<SatOrderWorkPage />);
    await user.click(await screen.findByRole("button", { name: /EMPEZAR PREPARACIÓN/ }));
    expect(fireTransition).toHaveBeenCalledWith("o1", { domain: "preparation", to_status: "preparing" });
    // Sigue en el pedido, con peso/medidas y «Embalar» en línea (sin modal).
    expect(await screen.findByRole("region", { name: "Embalar" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("Peso bulto 1"), "2");
    await user.type(screen.getByLabelText("Alto bulto 1"), "10");
    await user.type(screen.getByLabelText("Ancho bulto 1"), "20");
    await user.type(screen.getByLabelText("Fondo bulto 1"), "30");
    // Varios bultos: se añaden aquí mismo.
    await user.click(screen.getByRole("button", { name: "+ Añadir bulto" }));
    expect(screen.getByLabelText("Peso bulto 2")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Eliminar" })[0]);
    await user.click(screen.getByRole("button", { name: "📦 Embalar" }));
    await waitFor(() => expect(transitionPacked).toHaveBeenCalledWith("o1"));
    expect(setPackages).toHaveBeenCalledWith("o1", [
      { weight_kg: 2, height_cm: 10, width_cm: 20, depth_cm: 30 },
    ]);
    // Embalado y SIGUE abierto (sin volver a la cola).
    expect(await screen.findByRole("heading", { name: "✓ Embalado" })).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "← Volver a la Cola SAT" })).toHaveAttribute("href", "/erp/sat");
  });
});
