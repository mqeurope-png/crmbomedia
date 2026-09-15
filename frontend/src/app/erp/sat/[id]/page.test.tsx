import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SatOrderWorkPage from "./page";
import { getOrder, type OrderDetail } from "../../../lib/erpApi";

jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "o1" }),
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));

jest.mock("../../../lib/erpApi", () => ({
  STATUS_LABELS: jest.requireActual("../../../lib/erpApi").STATUS_LABELS,
  EXCEPTION_CATALOG: jest.requireActual("../../../lib/erpApi").EXCEPTION_CATALOG,
  getOrder: jest.fn(),
  attachDocument: jest.fn(),
  fireTransition: jest.fn(),
  reportException: jest.fn(),
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
    // Lo de siempre sigue: líneas y «Embalado» (estado preparing).
    expect(screen.getByText("Art A")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /EMBALADO/ })).toBeInTheDocument();
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
});
