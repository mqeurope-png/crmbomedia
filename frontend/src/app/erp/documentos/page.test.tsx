import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FactusolDocumentosPage from "./page";
import {
  getFactusolDocument,
  getFactusolSeries,
  listFactusolDocuments,
  searchFactusolCustomers,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  listFactusolDocuments: jest.fn(),
  getFactusolDocument: jest.fn(),
  getFactusolSeries: jest.fn(),
  searchFactusolCustomers: jest.fn(),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const mockList = listFactusolDocuments as jest.Mock;
const mockDetail = getFactusolDocument as jest.Mock;
const mockSeries = getFactusolSeries as jest.Mock;
const mockCustomers = searchFactusolCustomers as jest.Mock;

function doc(over = {}) {
  return {
    doc_type: "facturas", codigo: 260066, serie: 5, numero: "5-260066",
    cliente_codigo: "99", cliente_nombre: "MOVIATICOS",
    fecha: "2026-08-21", total: 186.34, estado: "0",
    estado_label: "Estado 0", referencia: "BOP-099917", ...over,
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
  mockCustomers.mockReset();
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

  it("buscar cliente resuelve a CODCLI y filtra por él", async () => {
    mockCustomers.mockResolvedValue([
      { codcli: "99", nombre: "MOVIATICOS" },
    ]);
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    await user.type(screen.getByLabelText("Buscar cliente"), "movia{Enter}");
    await user.click(await screen.findByRole("button", { name: /MOVIATICOS/ }));
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas", expect.objectContaining({ codcli: "99" }),
      ),
    );
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
