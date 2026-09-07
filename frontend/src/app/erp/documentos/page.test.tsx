import { render, screen, waitFor, within } from "@testing-library/react";
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
  convertFactusolDocument: jest.fn(),
  getFactusolConvertStatus: jest.fn(),
  downloadFactusolDocumentPdf: jest.fn(),
  getErpSettings: jest.fn(() => Promise.resolve({ factusol_companies: {} })),
  saveBlob: jest.fn(),
  ERP_EDIT_ROLES: ["admin", "pedidos"],
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
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
    expect(mockDetail).toHaveBeenCalledWith("facturas", 5, 260066, undefined);
  });

  it("pinta el badge del ciclo y filtra por él (E3-B)", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({
      items: [doc({
        doc_type: "presupuestos", codigo: 27, numero: "5-000027",
        ciclo: {
          albaranes: [{ doc_type: "albaranes", serie: 5, codigo: 500004,
                        numero: "5-500004" }],
          facturas: [{ doc_type: "facturas", serie: 5, codigo: 260063,
                       numero: "5-260063" }],
          origen: [], estado: "facturado",
        },
      })],
      total: 1,
    });
    render(<FactusolDocumentosPage />);
    await user.click(screen.getByRole("tab", { name: "Presupuestos" }));
    // «Facturado» aparece también como opción del filtro: se aserta el BADGE.
    const facturado = await screen.findAllByText("Facturado");
    expect(facturado.some((el) => el.className.includes("badge"))).toBe(true);
    // El filtro de ciclo existe en presupuestos y viaja al backend.
    await user.selectOptions(
      screen.getByLabelText("Estado del ciclo"), "facturado",
    );
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "presupuestos", expect.objectContaining({ ciclo: "facturado" }),
      ),
    );
  });

  it("la pestaña facturas no ofrece filtro de ciclo y enseña el origen", async () => {
    mockList.mockResolvedValue({
      items: [doc({
        ciclo: {
          albaranes: [], facturas: [],
          origen: [{ doc_type: "albaranes", serie: 5, codigo: 500004,
                     numero: "5-500004" }],
          estado: null,
        },
      })],
      total: 1,
    });
    render(<FactusolDocumentosPage />);
    expect(await screen.findByText(/de 5-500004/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Estado del ciclo")).not.toBeInTheDocument();
  });

  it("las opciones del filtro Ciclo dependen de la pestaña (E3-B-fix1)", async () => {
    // test_cycle_filter_options_depend_on_tab
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    // Facturas (pestaña por defecto): sin filtro de ciclo.
    expect(screen.queryByLabelText("Estado del ciclo")).not.toBeInTheDocument();
    // Albaranes: Sin facturar / Facturado — nunca «Sin albarán…».
    await user.click(screen.getByRole("tab", { name: "Albaranes" }));
    const filtroAlb = await screen.findByLabelText("Estado del ciclo");
    expect(
      within(filtroAlb).getByRole("option", { name: "Sin facturar" }),
    ).toBeInTheDocument();
    expect(
      within(filtroAlb).getByRole("option", { name: "Facturado" }),
    ).toBeInTheDocument();
    expect(
      within(filtroAlb).queryByRole("option", { name: /Sin albarán/ }),
    ).not.toBeInTheDocument();
    // Presupuestos: las tres fases del ciclo.
    await user.click(screen.getByRole("tab", { name: "Presupuestos" }));
    const filtroPre = await screen.findByLabelText("Estado del ciclo");
    for (const name of ["Sin albarán ni factura", "Con albarán", "Facturado"]) {
      expect(
        within(filtroPre).getByRole("option", { name }),
      ).toBeInTheDocument();
    }
  });

  it("el badge del listado de albaranes dice «Sin facturar» (E3-B-fix1)", async () => {
    mockList.mockResolvedValue({
      items: [doc({
        doc_type: "albaranes", codigo: 500005, numero: "5-500005",
        ciclo: {
          albaranes: [], facturas: [], origen: [],
          estado: "pendiente", estado_label: "Sin facturar",
        },
      })],
      total: 1,
    });
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await user.click(screen.getByRole("tab", { name: "Albaranes" }));
    // «Sin facturar» está también en el filtro: se aserta el BADGE de la fila.
    const sinFacturar = await screen.findAllByText("Sin facturar");
    expect(sinFacturar.some((el) => el.className.includes("badge"))).toBe(true);
    expect(screen.queryByText(/Sin albarán ni factura/)).not.toBeInTheDocument();
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
