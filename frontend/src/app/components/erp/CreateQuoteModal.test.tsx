import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CreateQuoteModal } from "./CreateQuoteModal";
import { listCompanies } from "../../lib/companiesApi";
import {
  createFactusolQuote,
  getFactusolQuote,
  searchFactusolArticles,
  searchFactusolQuotes,
} from "../../lib/erpApi";

jest.mock("../../lib/companiesApi", () => ({ listCompanies: jest.fn() }));
jest.mock("../../lib/erpApi", () => ({
  createFactusolQuote: jest.fn(),
  getFactusolQuote: jest.fn(),
  searchFactusolArticles: jest.fn(),
  searchFactusolQuotes: jest.fn(),
  // C-4-fix1: el modo duplicar ya NO filtra por cliente; si alguien vuelve a
  // llamar a listFactusolQuotes desde aquí, el test lo detecta.
  listFactusolQuotes: jest.fn(),
}));
const mockCreate = createFactusolQuote as jest.Mock;
const mockGetQuote = getFactusolQuote as jest.Mock;
const mockArticles = searchFactusolArticles as jest.Mock;
const mockSearchQuotes = searchFactusolQuotes as jest.Mock;
const mockCompanies = listCompanies as jest.Mock;

function article(over = {}) {
  return {
    codart: "00001", equart: "CDR80WPT", sku: "CDR80WPT",
    descripcion: "CD TQ 700 MB white Thermal WPT",
    desart: "CD TQ 700 MB white Thermal WPT", deeart: null, detart: null,
    eanart: null, famart: null,
    precio_venta: 0.79, precio_venta_columna: "PVPART", precio_coste: 0.25,
    precio: 0.79, stock: 100, iva_pct: 21, ...over,
  };
}

function quote(over = {}) {
  return {
    codpre: "77", referencia: "Rotulación nave Duaner", fecha: "2026-07-01",
    clipre: "55555", cliente_nombre: "Laboratorios Duaner",
    base: 100, iva: 21, total: 121, ...over,
  };
}

beforeEach(() => {
  mockCreate.mockReset();
  mockGetQuote.mockReset();
  mockArticles.mockReset();
  mockSearchQuotes.mockReset();
  mockCompanies.mockReset();
  mockCreate.mockResolvedValue({ job_id: "job-1", status: "queued" });
  mockArticles.mockResolvedValue([]);
  mockSearchQuotes.mockResolvedValue([]);
  mockCompanies.mockResolvedValue({ items: [], total: 0 });
});

function base(over = {}) {
  return {
    companyId: "c1", companyName: "Acme SL",
    onCreated: jest.fn(), onCancel: jest.fn(), ...over,
  };
}

describe("CreateQuoteModal", () => {
  // --- C-4-fix2: solo 2 pestañas -------------------------------------------

  it("no renderiza la pestaña «Rápida» (la duplicaba «Con artículos»)", () => {
    render(<CreateQuoteModal {...base()} />);
    expect(screen.queryByRole("button", { name: "Rápida" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Con artículos" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Duplicar" })).toBeInTheDocument();
  });

  it("una proforma simple se hace con una línea escrita a mano, sin catálogo", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ onCreated })} />);

    await user.type(screen.getByLabelText("Descripción línea 1"), "Mano de obra");
    await user.type(screen.getByLabelText("Precio línea 1"), "500");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.company_id).toBe("c1");
    expect(payload.lines).toEqual([
      expect.objectContaining({
        description: "Mano de obra", quantity: 1, unit_price: 500,
      }),
    ]);
    expect(onCreated).toHaveBeenCalledWith("job-1");
  });

  it("no deja crear una proforma sin ninguna línea con descripción", () => {
    render(<CreateQuoteModal {...base()} />);
    expect(screen.getByRole("button", { name: "Crear proforma" })).toBeDisabled();
  });

  // --- C-4-fix2: el autocomplete carga el precio ---------------------------

  it("elegir un artículo rellena SKU, descripción y PRECIO de venta", async () => {
    mockArticles.mockResolvedValue([article()]);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);

    await user.type(screen.getByLabelText("SKU línea 1"), "CDR80");
    await user.click(await screen.findByRole("button", { name: /CDR80WPT/ }));

    expect(screen.getByLabelText("SKU línea 1")).toHaveValue("CDR80WPT");
    expect(screen.getByLabelText("Descripción línea 1"))
      .toHaveValue("CD TQ 700 MB white Thermal WPT");
    // El precio de VENTA, no el coste (0.25).
    expect(screen.getByLabelText("Precio línea 1")).toHaveValue(0.79);
  });

  it("sin precio de venta deja el campo en blanco, no lo fuerza a 0", async () => {
    mockArticles.mockResolvedValue([article({ precio_venta: null, precio: 0 })]);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);

    await user.type(screen.getByLabelText("Descripción línea 1"), "CDR80");
    await user.click(await screen.findByRole("button", { name: /CDR80WPT/ }));

    expect(screen.getByLabelText("Precio línea 1")).toHaveValue(null);
  });

  // --- C-4-fix1/fix2: duplicar --------------------------------------------

  it("modo duplicar: input de búsqueda libre, no la lista filtrada por cliente", async () => {
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));

    expect(screen.getByLabelText("Buscar plantilla")).toBeInTheDocument();
    expect(screen.getByText(/Puedes duplicar/)).toBeInTheDocument();
    const { listFactusolQuotes } = jest.requireMock("../../lib/erpApi");
    expect(listFactusolQuotes).not.toHaveBeenCalled();
  });

  it("escribir en el buscador llama a la búsqueda global, sin company_id", async () => {
    mockSearchQuotes.mockResolvedValue([quote()]);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));
    await user.type(screen.getByLabelText("Buscar plantilla"), "lab");

    await waitFor(() => expect(mockSearchQuotes).toHaveBeenCalledWith(
      "lab", expect.objectContaining({ days_back: 365 }),
    ));
    expect(await screen.findByText(/Laboratorios Duaner/)).toBeInTheDocument();
  });

  it("plantilla CON cache: carga sus N líneas y queda en «Con artículos»", async () => {
    mockSearchQuotes.mockResolvedValue([quote()]);
    mockGetQuote.mockResolvedValue({
      ...quote(), line_source: "cache",
      lines: [
        { position: 1, codart: "ART-1", description: "Vinilo impreso",
          quantity: 3, unit_price: 40, discount_pct: 0, line_total: 120,
          iva_pct: 21 },
        { position: 2, codart: "", description: "Montaje",
          quantity: 1, unit_price: 60, discount_pct: 0, line_total: 60,
          iva_pct: 21 },
      ],
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));
    await user.type(screen.getByLabelText("Buscar plantilla"), "lab");
    await user.click(
      await screen.findByRole("button", { name: "Cargar esta plantilla" }),
    );

    expect(await screen.findByLabelText("Descripción línea 1"))
      .toHaveValue("Vinilo impreso");
    expect(screen.getByLabelText("Descripción línea 2")).toHaveValue("Montaje");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    // Cliente DESTINO, no el de origen de la plantilla.
    expect(mockCreate.mock.calls[0][0].company_id).toBe("c1");
  });

  it("plantilla antigua del escritorio: carga sus líneas reales de F_LPS", async () => {
    // C-4-fix3: F_PRE SÍ tiene líneas (en F_LPS), así que las proformas del
    // escritorio ya no degradan a «sin desglose».
    mockSearchQuotes.mockResolvedValue([quote({ codpre: "574" })]);
    mockGetQuote.mockResolvedValue({
      ...quote({ codpre: "574" }), line_source: "F_LPS",
      lines: [
        { position: 1, codart: "MBO", description: "Cabezal MBO", quantity: 1,
          unit_price: 250, discount_pct: 0, line_total: 250, iva_pct: 21 },
        { position: 2, codart: "CAP", description: "Capping", quantity: 1,
          unit_price: 25, discount_pct: 0, line_total: 25, iva_pct: 21 },
        { position: 3, codart: "WIP", description: "Wiper", quantity: 1,
          unit_price: 20, discount_pct: 0, line_total: 20, iva_pct: 21 },
        { position: 4, codart: null, description: "Hora SAT", quantity: 1,
          unit_price: 60, discount_pct: 0, line_total: 60, iva_pct: 21 },
      ],
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));
    await user.type(screen.getByLabelText("Buscar plantilla"), "lab");
    await user.click(
      await screen.findByRole("button", { name: "Cargar esta plantilla" }),
    );

    expect(await screen.findByLabelText("Descripción línea 1"))
      .toHaveValue("Cabezal MBO");
    expect(screen.getByLabelText("Descripción línea 4")).toHaveValue("Hora SAT");
    expect(screen.getByLabelText("Precio línea 1")).toHaveValue(250);
    // Ya no hay banner de «esta proforma no tiene desglose».
    expect(screen.queryByText(/no tiene líneas/)).not.toBeInTheDocument();
  });

  it("proforma sin líneas en F_LPS: avisa pero deja la tabla editable", async () => {
    mockSearchQuotes.mockResolvedValue([quote({ codpre: "88" })]);
    mockGetQuote.mockResolvedValue({
      ...quote({ codpre: "88" }), line_source: "F_LPS", lines: [],
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));
    await user.type(screen.getByLabelText("Buscar plantilla"), "lab");
    await user.click(
      await screen.findByRole("button", { name: "Cargar esta plantilla" }),
    );

    expect(await screen.findByText(/no tiene líneas en FACTUSOL/))
      .toBeInTheDocument();
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("");
  });

  it("permite cambiar el cliente destino a otra empresa vinculada", async () => {
    mockCompanies.mockResolvedValue({
      items: [
        { id: "c2", name: "Laboratorios Porta", factusol_company_id: "66666" },
        { id: "c3", name: "Sin FACTUSOL", factusol_company_id: null },
      ],
      total: 2,
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);

    await user.click(screen.getByRole("button", { name: "Cambiar" }));
    const input = await screen.findByLabelText("Empresa destino");
    // Solo empresas ya vinculadas: las demás las rechaza el backend con 409.
    // (Las <option> de un <datalist> no exponen rol ARIA.)
    await waitFor(() => {
      const values = Array.from(
        document.querySelectorAll("#erp-quote-target-companies option"),
      ).map((o) => (o as HTMLOptionElement).value);
      expect(values).toEqual(["Laboratorios Porta"]);
    });

    await user.type(input, "Laboratorios Porta");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Trabajo nuevo");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].company_id).toBe("c2");
  });
});
