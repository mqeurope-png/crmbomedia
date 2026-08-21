import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FactusolDocumentosPage from "./page";
import {
  getFactusolDocument,
  getFactusolSeries,
  listFactusolDocuments,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  listFactusolDocuments: jest.fn(),
  getFactusolDocument: jest.fn(),
  getFactusolSeries: jest.fn(),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const mockList = listFactusolDocuments as jest.Mock;
const mockDetail = getFactusolDocument as jest.Mock;
const mockSeries = getFactusolSeries as jest.Mock;

function doc(over = {}) {
  return {
    doc_type: "facturas", codigo: 260066, serie: 5, numero: "5-260066",
    cliente_codigo: "99", cliente_nombre: "MOVIATICOS",
    fecha: "2026-08-21", total: 186.34, estado: "0",
    estado_label: "Estado 0", referencia: "BOP-099917",
    forma_pago: "002", ...over,
  };
}

beforeEach(() => {
  mockList.mockReset();
  mockList.mockResolvedValue({ items: [doc()], total: 1 });
  mockDetail.mockReset();
  mockSeries.mockReset();
  mockSeries.mockResolvedValue({
    items: [
      { serie: 5, nombre: "Streamtec", is_default: true, is_known: true },
      { serie: 2, nombre: "MQ Europe", is_default: false, is_known: true },
      { serie: 7, nombre: "Serie 7", is_default: false, is_known: false },
    ],
    default: 5,
  });
});

describe("ERP · Documentos (E3-A)", () => {
  it("muestra las 4 pestañas y lista facturas por defecto", async () => {
    render(<FactusolDocumentosPage />);
    for (const label of ["Pedidos", "Presupuestos", "Albaranes", "Facturas"]) {
      expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
    }
    expect(await screen.findByText("5-260066")).toBeInTheDocument();
    expect(screen.getByText("MOVIATICOS")).toBeInTheDocument();
    expect(mockList).toHaveBeenCalledWith(
      "facturas", expect.objectContaining({ limit: 100, offset: 0 }),
    );
  });

  it("cambiar de pestaña re-consulta el tipo elegido", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    await user.click(screen.getByRole("tab", { name: "Albaranes" }));
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith("albaranes", expect.anything()),
    );
  });

  it("el filtro de serie viaja al backend; las series sin nombre no salen", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    // Esperar a que carguen las opciones de series antes de seleccionar.
    await screen.findByRole("option", { name: "5 · Streamtec" });
    expect(
      screen.queryByRole("option", { name: "7 · Serie 7" }),
    ).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Serie / empresa"), "5");
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas", expect.objectContaining({ serie: 5 }),
      ),
    );
  });

  it("el campo cliente acepta CIF/email y viaja como cliente_q", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    const input = screen.getByLabelText("Cliente, CIF o email");
    expect(input).toHaveAttribute(
      "placeholder", expect.stringContaining("B12345678"),
    );
    await user.type(input, "admin@moviaticos.com{Enter}");
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas",
        expect.objectContaining({ cliente_q: "admin@moviaticos.com" }),
      ),
    );
  });

  it("las cabeceras ordenan asc/desc sobre el conjunto (toggle)", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    // Default: numero desc.
    expect(mockList).toHaveBeenCalledWith(
      "facturas", expect.objectContaining({ sort: "numero", dir: "desc" }),
    );
    await user.click(screen.getByRole("button", { name: /^Total/ }));
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas", expect.objectContaining({ sort: "total", dir: "desc" }),
      ),
    );
    // Segundo click en la misma columna → asc, con indicador.
    await user.click(screen.getByRole("button", { name: /^Total/ }));
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas", expect.objectContaining({ sort: "total", dir: "asc" }),
      ),
    );
    expect(screen.getByRole("button", { name: /Total ▲/ })).toBeInTheDocument();
  });

  it("el detalle muestra la forma de pago con nombre", async () => {
    mockDetail.mockResolvedValue({
      ...doc(),
      forma_pago_nombre: "Transferencia 30 días",
      lines: [],
    });
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByText("5-260066"));
    expect(await screen.findByText("Transferencia 30 días")).toBeInTheDocument();
    expect(screen.getByText("Forma de pago")).toBeInTheDocument();
  });

  it("abrir una fila carga el detalle con líneas", async () => {
    mockDetail.mockResolvedValue({
      ...doc(),
      lines: [{
        position: 1, codart: "99cy", description: "Tinta cyan",
        quantity: 2, unit_price: 40, line_total: 80,
      }],
    });
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByText("5-260066"));
    expect(await screen.findByText("Tinta cyan")).toBeInTheDocument();
    expect(mockDetail).toHaveBeenCalledWith("facturas", 5, 260066);
  });

  it("«Limpiar filtros» resetea y re-consulta sin filtros", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByRole("option", { name: "2 · MQ Europe" });
    await user.selectOptions(screen.getByLabelText("Serie / empresa"), "2");
    await user.click(
      await screen.findByRole("button", { name: "Limpiar filtros" }),
    );
    await waitFor(() => {
      const last = mockList.mock.calls.at(-1);
      expect(last?.[1].serie).toBeUndefined();
    });
  });
});
