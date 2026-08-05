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
  it("modo rápido: envía una línea única con el concepto y el importe", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ onCreated })} />);

    await user.type(screen.getByLabelText("Concepto"), "Instalación sala 3");
    await user.type(screen.getByLabelText("Importe (base)"), "500");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.company_id).toBe("c1");
    expect(payload.referencia).toBe("Instalación sala 3");
    expect(payload.lines).toEqual([
      expect.objectContaining({
        description: "Instalación sala 3", quantity: 1, unit_price: 500,
      }),
    ]);
    expect(onCreated).toHaveBeenCalledWith("job-1");
  });

  it("no deja crear una proforma sin concepto ni líneas", async () => {
    render(<CreateQuoteModal {...base()} />);
    expect(screen.getByRole("button", { name: "Crear proforma" })).toBeDisabled();
  });

  // --- C-4-fix1: artículos por SKU comercial -------------------------------

  it("el autocomplete de artículos muestra SKU comercial, descripción y precio", async () => {
    mockArticles.mockResolvedValue([{
      codart: "00001", equart: "CDR80WPT", sku: "CDR80WPT",
      descripcion: "CD TQ 700 MB white Thermal WPT",
      desart: "CD TQ 700 MB white Thermal WPT", deeart: null, detart: null,
      eanart: null, famart: null, precio: 0.25, stock: 100, iva_pct: 21,
    }]);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);

    await user.click(screen.getByRole("button", { name: "Con artículos" }));
    await user.type(screen.getByLabelText("Buscar artículo"), "CDR80");

    // El SKU comercial es lo que el operativo reconoce, no el CODART interno.
    expect(await screen.findByText("CDR80WPT")).toBeInTheDocument();
    expect(screen.getByText("CD TQ 700 MB white Thermal WPT")).toBeInTheDocument();
    expect(screen.getByText("0.25 €")).toBeInTheDocument();
  });

  it("al elegir el artículo rellena la línea con EQUART y su descripción", async () => {
    mockArticles.mockResolvedValue([{
      codart: "00001", equart: "CDR80WPT", sku: "CDR80WPT",
      descripcion: "CD TQ 700 MB white Thermal WPT",
      desart: "CD TQ 700 MB white Thermal WPT", deeart: null, detart: null,
      eanart: null, famart: null, precio: 0.25, stock: 100, iva_pct: 21,
    }]);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);

    await user.click(screen.getByRole("button", { name: "Con artículos" }));
    await user.type(screen.getByLabelText("Buscar artículo"), "CDR80");
    await user.click(await screen.findByRole("button", { name: /CDR80WPT/ }));

    expect(screen.getByLabelText("Artículo línea 1")).toHaveValue("CDR80WPT");
    expect(screen.getByLabelText("Descripción línea 1"))
      .toHaveValue("CD TQ 700 MB white Thermal WPT");
    expect(screen.getByLabelText("Precio línea 1")).toHaveValue(0.25);
  });

  // --- C-4-fix1: duplicar de cualquier cliente -----------------------------

  it("modo duplicar: input de búsqueda libre, no la lista filtrada por cliente", async () => {
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));

    expect(screen.getByLabelText("Buscar plantilla")).toBeInTheDocument();
    expect(
      screen.getByText(/Puedes duplicar/),
    ).toBeInTheDocument();
    // Nunca se pide la lista restringida al cliente actual.
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
    // Muestra el cliente ORIGEN, que es como se reconoce la plantilla.
    expect(await screen.findByText(/Laboratorios Duaner/)).toBeInTheDocument();
  });

  it("cargar una plantilla de otro cliente la crea para el cliente destino", async () => {
    mockSearchQuotes.mockResolvedValue([quote()]);
    mockGetQuote.mockResolvedValue({
      ...quote(), line_source: "cache",
      lines: [{
        position: 1, codart: "ART-1", description: "Vinilo impreso",
        quantity: 3, unit_price: 40, discount_pct: 0, line_total: 120,
        iva_pct: 21,
      }],
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));
    await user.type(screen.getByLabelText("Buscar plantilla"), "lab");
    await user.click(
      await screen.findByRole("button", { name: "Cargar esta plantilla" }),
    );

    // La plantilla cae en el modo artículos con sus líneas reales.
    expect(await screen.findByLabelText("Descripción línea 1"))
      .toHaveValue("Vinilo impreso");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    // Cliente DESTINO (el del modal), no el de origen de la plantilla.
    expect(payload.company_id).toBe("c1");
    expect(payload.lines[0]).toEqual(expect.objectContaining({
      description: "Vinilo impreso", quantity: 3, unit_price: 40,
    }));
  });

  it("una plantilla sin desglose cae al modo rápido con su referencia", async () => {
    mockSearchQuotes.mockResolvedValue([quote({ codpre: "88" })]);
    mockGetQuote.mockResolvedValue({
      ...quote({ codpre: "88" }), line_source: "ref_text", lines: [],
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));
    await user.type(screen.getByLabelText("Buscar plantilla"), "lab");
    await user.click(
      await screen.findByRole("button", { name: "Cargar esta plantilla" }),
    );

    expect(await screen.findByLabelText("Concepto"))
      .toHaveValue("Rotulación nave Duaner");
    expect(screen.getByLabelText("Importe (base)")).toHaveValue(100);
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
    // Solo se ofrecen empresas ya vinculadas: las demás las rechaza el backend
    // con 409 company_not_linked. (Las <option> de un <datalist> no exponen
    // rol ARIA, así que se comprueban en el DOM.)
    await waitFor(() => {
      const values = Array.from(
        document.querySelectorAll("#erp-quote-target-companies option"),
      ).map((o) => (o as HTMLOptionElement).value);
      expect(values).toEqual(["Laboratorios Porta"]);
    });

    await user.type(input, "Laboratorios Porta");
    await user.type(screen.getByLabelText("Concepto"), "Trabajo nuevo");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].company_id).toBe("c2");
  });
});
