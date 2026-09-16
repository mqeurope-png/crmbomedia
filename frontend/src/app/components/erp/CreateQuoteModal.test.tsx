import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CreateQuoteModal } from "./CreateQuoteModal";
import { listCompanies } from "../../lib/companiesApi";
import {
  createFactusolQuote,
  downloadFactusolDocumentPdf,
  getFactusolCustomerAddresses,
  getFactusolQuote,
  saveBlob,
  searchFactusolArticles,
  searchFactusolQuotes,
  updateFactusolQuote,
  waitForQuoteJob,
} from "../../lib/erpApi";

jest.mock("../../lib/companiesApi", () => ({ listCompanies: jest.fn() }));
jest.mock("../../lib/erpApi", () => ({
  createFactusolQuote: jest.fn(),
  updateFactusolQuote: jest.fn(),
  getFactusolQuote: jest.fn(),
  getFactusolCustomerAddresses: jest.fn(),
  waitForQuoteJob: jest.fn(),
  searchFactusolArticles: jest.fn(),
  searchFactusolQuotes: jest.fn(),
  downloadFactusolDocumentPdf: jest.fn(),
  saveBlob: jest.fn(),
  // C-4-fix1: el modo duplicar ya NO filtra por cliente; si alguien vuelve a
  // llamar a listFactusolQuotes desde aquí, el test lo detecta.
  listFactusolQuotes: jest.fn(),
}));
const mockCreate = createFactusolQuote as jest.Mock;
const mockGetQuote = getFactusolQuote as jest.Mock;
const mockArticles = searchFactusolArticles as jest.Mock;
const mockSearchQuotes = searchFactusolQuotes as jest.Mock;
const mockCompanies = listCompanies as jest.Mock;
const mockUpdate = updateFactusolQuote as jest.Mock;
const mockAddresses = getFactusolCustomerAddresses as jest.Mock;
const mockWaitJob = waitForQuoteJob as jest.Mock;
const mockPdf = downloadFactusolDocumentPdf as jest.Mock;
const mockSaveBlob = saveBlob as jest.Mock;

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

/** Margen mayor que el debounce del autocomplete (300 ms): sirve para
 *  afirmar que algo NO se ha buscado. */
function afterDebounce() {
  return new Promise((r) => setTimeout(r, 400));
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
  mockUpdate.mockReset();
  mockAddresses.mockReset();
  mockWaitJob.mockReset();
  mockPdf.mockReset();
  mockSaveBlob.mockReset();
  mockUpdate.mockResolvedValue({ job_id: "job-u1", status: "queued", codpre: "574" });
  mockAddresses.mockResolvedValue([]);
  mockWaitJob.mockResolvedValue({ status: "finished", result: { codpre: "51" } });
});

function base(over = {}) {
  return {
    companyId: "c1", companyName: "Acme SL",
    onCreated: jest.fn(), onCancel: jest.fn(), ...over,
  };
}

/** Modo duplicar: busca, carga la plantilla nº `codpre` y la vuelca a la
 *  tabla («Usar como plantilla»). */
async function duplicateFrom(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Duplicar" }));
  await user.type(screen.getByLabelText("Buscar plantilla"), "lab");
  await user.click(
    await screen.findByRole("button", { name: "Cargar esta plantilla" }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Usar como plantilla" }),
  );
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

  it("elegir un artículo en SKU no abre el desplegable de Descripción (solo busca el campo con foco)", async () => {
    mockArticles.mockResolvedValue([article()]);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);

    await user.type(screen.getByLabelText("SKU línea 1"), "CDR80");
    await user.click(await screen.findByRole("button", { name: /CDR80WPT/ }));
    expect(screen.getByLabelText("Descripción línea 1"))
      .toHaveValue("CD TQ 700 MB white Thermal WPT");
    const calls = mockArticles.mock.calls.length;
    await afterDebounce();
    // La descripción cambió por programa: no relanza la búsqueda ni abre lista.
    expect(mockArticles).toHaveBeenCalledTimes(calls);
    expect(screen.queryByRole("listbox", { name: /Descripción línea 1/ }))
      .not.toBeInTheDocument();
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
    await duplicateFrom(user);

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
    await duplicateFrom(user);

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
    await duplicateFrom(user);

    expect(await screen.findByText(/no tiene líneas en FACTUSOL/))
      .toBeInTheDocument();
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("");
  });

  // --- Lote B4: duplicar sin remapear + vista previa + PDF -----------------

  it("Lote B4: la plantilla precarga el SKU COMERCIAL (EQUART), no el CODART interno", async () => {
    mockSearchQuotes.mockResolvedValue([quote()]);
    mockGetQuote.mockResolvedValue({
      ...quote(), line_source: "F_LPS",
      lines: [
        { position: 1, codart: "00001", sku: "CDR80WPT",
          description: "CD TQ 700 MB white Thermal WPT", quantity: 10,
          unit_price: 0.79, discount_pct: 0, line_total: 7.9, iva_pct: 21 },
        // Sin EQUART: el backend devuelve el interno como sku.
        { position: 2, codart: "1712", sku: "1712", description: "Cable HDMI",
          quantity: 1, unit_price: 10, discount_pct: 0, line_total: 10,
          iva_pct: 21 },
        // Texto libre: SKU vacío, descripción intacta.
        { position: 3, codart: null, sku: null, description: "Hora SAT",
          quantity: 1, unit_price: 60, discount_pct: 0, line_total: 60,
          iva_pct: 21 },
        // Respuesta antigua sin `sku`: cae al codart.
        { position: 4, codart: "MBO", description: "Cabezal MBO",
          quantity: 1, unit_price: 250, discount_pct: 0, line_total: 250,
          iva_pct: 21 },
      ],
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await duplicateFrom(user);

    expect(await screen.findByLabelText("SKU línea 1")).toHaveValue("CDR80WPT");
    expect(screen.getByLabelText("SKU línea 2")).toHaveValue("1712");
    expect(screen.getByLabelText("SKU línea 3")).toHaveValue("");
    expect(screen.getByLabelText("Descripción línea 3")).toHaveValue("Hora SAT");
    expect(screen.getByLabelText("SKU línea 4")).toHaveValue("MBO");
    expect(screen.getByText(/plantilla nº 77/)).toBeInTheDocument();

    // Y el payload manda el SKU tal cual (el backend acepta EQUART o CODART).
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].lines.map((l: { codart?: string }) => l.codart))
      .toEqual(["CDR80WPT", "1712", undefined, "MBO"]);
  });

  it("Lote B4: cargar una plantilla enseña la vista previa (SKU · descripción · cant · precio · total) sin tocar la tabla", async () => {
    mockSearchQuotes.mockResolvedValue([quote()]);
    mockGetQuote.mockResolvedValue({
      ...quote(), line_source: "F_LPS", portes: 19,
      lines: [
        { position: 1, codart: "00001", sku: "CDR80WPT",
          description: "CD TQ 700 MB white Thermal WPT", quantity: 10,
          unit_price: 0.79, discount_pct: 0, line_total: 7.9, iva_pct: 21 },
        { position: 2, codart: null, sku: null, description: "Hora SAT",
          quantity: 2, unit_price: 60, discount_pct: 0, line_total: 120,
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

    const preview = await screen.findByRole("region", { name: "Plantilla nº 77" });
    expect(within(preview).getByText(/Proforma nº 77/)).toBeInTheDocument();
    expect(within(preview).getByText(/Laboratorios Duaner/)).toBeInTheDocument();
    const rows = within(within(preview).getByRole("table", { name: "Líneas de la plantilla" }))
      .getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("CDR80WPT");
    expect(rows[0]).toHaveTextContent("CD TQ 700 MB white Thermal WPT");
    expect(rows[0]).toHaveTextContent("10");
    expect(rows[0]).toHaveTextContent("0.79");
    expect(rows[0]).toHaveTextContent("7.90");
    expect(rows[1]).toHaveTextContent("—");         // texto libre: sin SKU
    expect(rows[1]).toHaveTextContent("120.00");
    expect(within(preview).getByText(/Portes:/)).toHaveTextContent("19.00 €");
    // Sigue en «Duplicar»: la tabla editable todavía no existe.
    expect(screen.queryByLabelText("SKU línea 1")).not.toBeInTheDocument();
    expect(within(preview).getByRole("button", { name: "Ver PDF" })).toBeInTheDocument();

    // «Usar como plantilla» → tabla editable con las líneas Y los portes.
    await user.click(within(preview).getByRole("button", { name: "Usar como plantilla" }));
    expect(await screen.findByLabelText("SKU línea 1")).toHaveValue("CDR80WPT");
    expect(screen.getByLabelText("Portes")).toHaveValue(19);
  });

  it("Lote B4: «Ver PDF» pide el PDF del presupuesto en variante proforma y lo abre en pestaña nueva", async () => {
    mockSearchQuotes.mockResolvedValue([quote({ codpre: "39" })]);
    mockGetQuote.mockResolvedValue({
      ...quote({ codpre: "39" }), tippre: "5", line_source: "F_LPS",
      lines: [{ position: 1, codart: null, sku: null, description: "Placas",
                quantity: 1, unit_price: 100, discount_pct: 0, line_total: 100,
                iva_pct: 21 }],
    });
    mockPdf.mockResolvedValue(new Blob(["%PDF"]));
    const open = jest.fn(() => ({} as Window));
    window.open = open;
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));
    await user.type(screen.getByLabelText("Buscar plantilla"), "lab");
    await user.click(
      await screen.findByRole("button", { name: "Cargar esta plantilla" }),
    );
    await user.click(await screen.findByRole("button", { name: "Ver PDF" }));

    await waitFor(() => expect(mockPdf).toHaveBeenCalledWith(
      "presupuestos", 5, "39", "es", { variant: "proforma" },
    ));
    await waitFor(() => expect(open).toHaveBeenCalledWith("blob:jest", "_blank"));
    expect(mockSaveBlob).not.toHaveBeenCalled();
  });

  it("Lote B4: si el navegador bloquea la pestaña, «Ver PDF» cae a la descarga", async () => {
    mockSearchQuotes.mockResolvedValue([quote()]);
    mockGetQuote.mockResolvedValue({ ...quote(), line_source: "F_LPS", lines: [] });
    mockPdf.mockResolvedValue(new Blob(["%PDF"]));
    window.open = jest.fn(() => null);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));
    await user.type(screen.getByLabelText("Buscar plantilla"), "lab");
    await user.click(
      await screen.findByRole("button", { name: "Cargar esta plantilla" }),
    );
    await user.click(await screen.findByRole("button", { name: "Ver PDF" }));

    await waitFor(() => expect(mockSaveBlob)
      .toHaveBeenCalledWith(expect.any(Blob), "Proforma_77.pdf"));
    // Serie por defecto (1) si la cabecera no trae TIPPRE.
    expect(mockPdf).toHaveBeenCalledWith("presupuestos", 1, "77", "es", { variant: "proforma" });
  });

  it("Lote B4 (bug «se buguea»): cargar una plantilla NO lanza el autocomplete de cada línea", async () => {
    // Antes, cada ArticleAutocompleteInput buscaba en cuanto cambiaba su
    // valor — también por programa. Cargar una plantilla de N líneas
    // disparaba 2N búsquedas y abría 2N desplegables sin que nadie tecleara.
    mockSearchQuotes.mockResolvedValue([quote()]);
    mockArticles.mockResolvedValue([article()]);
    mockGetQuote.mockResolvedValue({
      ...quote(), line_source: "F_LPS",
      lines: [
        { position: 1, codart: "00001", sku: "CDR80WPT",
          description: "CD TQ 700 MB white Thermal WPT", quantity: 1,
          unit_price: 0.79, discount_pct: 0, line_total: 0.79, iva_pct: 21 },
        { position: 2, codart: "1712", sku: "CAB-HDMI", description: "Cable HDMI",
          quantity: 1, unit_price: 10, discount_pct: 0, line_total: 10,
          iva_pct: 21 },
      ],
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await duplicateFrom(user);
    expect(await screen.findByLabelText("SKU línea 2")).toHaveValue("CAB-HDMI");

    await afterDebounce();
    expect(mockArticles).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    // Teclear en un campo SÍ busca, y solo ese campo.
    await user.type(screen.getByLabelText("SKU línea 2"), "X");
    await waitFor(() => expect(mockArticles).toHaveBeenCalledWith("CAB-HDMIX"));
    expect(await screen.findByRole("listbox", { name: /SKU línea 2/ })).toBeInTheDocument();
    expect(screen.queryByRole("listbox", { name: /SKU línea 1/ })).not.toBeInTheDocument();
  });

  it("Lote B4: cargar dos plantillas seguidas sustituye la vista previa y las líneas", async () => {
    mockSearchQuotes.mockResolvedValue([quote({ codpre: "1" }), quote({ codpre: "2" })]);
    mockGetQuote
      .mockResolvedValueOnce({
        ...quote({ codpre: "1" }), line_source: "F_LPS",
        lines: [{ position: 1, codart: null, sku: null, description: "Primera",
                  quantity: 1, unit_price: 1, discount_pct: 0, line_total: 1,
                  iva_pct: 21 },
                { position: 2, codart: null, sku: null, description: "Segunda",
                  quantity: 1, unit_price: 1, discount_pct: 0, line_total: 1,
                  iva_pct: 21 }],
      })
      .mockResolvedValueOnce({
        ...quote({ codpre: "2" }), line_source: "F_LPS",
        lines: [{ position: 1, codart: null, sku: null, description: "Única",
                  quantity: 1, unit_price: 1, discount_pct: 0, line_total: 1,
                  iva_pct: 21 }],
      });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByRole("button", { name: "Duplicar" }));
    await user.type(screen.getByLabelText("Buscar plantilla"), "lab");
    const buttons = await screen.findAllByRole("button", { name: "Cargar esta plantilla" });
    await user.click(buttons[0]);
    await screen.findByRole("region", { name: "Plantilla nº 1" });
    await user.click(buttons[1]);
    const preview = await screen.findByRole("region", { name: "Plantilla nº 2" });
    expect(screen.queryByRole("region", { name: "Plantilla nº 1" })).not.toBeInTheDocument();
    await user.click(within(preview).getByRole("button", { name: "Usar como plantilla" }));
    expect(await screen.findByLabelText("Descripción línea 1")).toHaveValue("Única");
    expect(screen.queryByLabelText("Descripción línea 2")).not.toBeInTheDocument();
    expect(screen.getByText(/plantilla nº 2/)).toBeInTheDocument();
  });

  // --- Lote 3: Duplicar desde una fila (arranca en «Duplicar» con la
  //     proforma de origen ya en la vista previa) ---------------------------

  it("Lote 3: con `duplicateSource` arranca en «Duplicar» y precarga la vista previa (líneas de origen con SKU comercial + «Ver PDF») sin buscar", async () => {
    mockGetQuote.mockResolvedValue({
      ...quote({ codpre: "39" }), tippre: "5", line_source: "F_LPS", portes: 19,
      lines: [
        { position: 1, codart: "00001", sku: "CDR80WPT",
          description: "CD TQ 700 MB white Thermal WPT", quantity: 10,
          unit_price: 0.79, discount_pct: 0, line_total: 7.9, iva_pct: 21 },
        { position: 2, codart: null, sku: null, description: "Hora SAT",
          quantity: 2, unit_price: 60, discount_pct: 0, line_total: 120,
          iva_pct: 21 },
      ],
    });
    render(<CreateQuoteModal {...base({ duplicateSource: quote({ codpre: "39" }) })} />);

    // Carga la proforma de la fila (líneas reales de F_LPS) sin pasar por el
    // buscador: es el mismo `getFactusolQuote` que usa «Cargar esta plantilla».
    await waitFor(() => expect(mockGetQuote).toHaveBeenCalledWith("39"));
    const preview = await screen.findByRole("region", { name: "Plantilla nº 39" });
    // Líneas de origen visibles, ya mapeadas (SKU comercial, no el CODART).
    expect(within(preview).getByText("CDR80WPT")).toBeInTheDocument();
    expect(within(preview).getByText("CD TQ 700 MB white Thermal WPT")).toBeInTheDocument();
    expect(within(preview).getByText("Hora SAT")).toBeInTheDocument();
    // «Ver PDF» presente; la tabla editable aún no existe (falta «Usar como
    // plantilla»): la duplicación real todavía no ha ocurrido.
    expect(within(preview).getByRole("button", { name: "Ver PDF" })).toBeInTheDocument();
    expect(screen.queryByLabelText("SKU línea 1")).not.toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("Lote 3: la copia solo se crea tras la vista previa (Usar como plantilla → Crear proforma), con el cliente destino y sin remapear artículos", async () => {
    mockArticles.mockResolvedValue([article()]);
    mockGetQuote.mockResolvedValue({
      ...quote({ codpre: "39" }), line_source: "F_LPS",
      lines: [
        { position: 1, codart: "00001", sku: "CDR80WPT",
          description: "CD TQ 700 MB white Thermal WPT", quantity: 10,
          unit_price: 0.79, discount_pct: 0, line_total: 7.9, iva_pct: 21 },
        { position: 2, codart: null, sku: null, description: "Hora SAT",
          quantity: 2, unit_price: 60, discount_pct: 0, line_total: 120,
          iva_pct: 21 },
      ],
    });
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ duplicateSource: quote({ codpre: "39" }), onCreated })} />);

    const preview = await screen.findByRole("region", { name: "Plantilla nº 39" });
    expect(mockCreate).not.toHaveBeenCalled();               // solo abrir no crea nada
    await user.click(within(preview).getByRole("button", { name: "Usar como plantilla" }));
    expect(await screen.findByLabelText("SKU línea 1")).toHaveValue("CDR80WPT");
    expect(screen.getByLabelText("Descripción línea 2")).toHaveValue("Hora SAT");
    // Volcar la plantilla no dispara el autocomplete (no se remapea el artículo).
    await afterDebounce();
    expect(mockArticles).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Crear proforma" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    // Cliente DESTINO (el de la fila, pasado como companyId), y el SKU tal cual.
    expect(mockCreate.mock.calls[0][0].company_id).toBe("c1");
    expect(mockCreate.mock.calls[0][0].lines[0].codart).toBe("CDR80WPT");
    expect(onCreated).toHaveBeenCalledWith("job-1");
  });

  // --- C-4-fix6: referencia, descuento, direcciones y edición -------------

  it("renderiza el campo Referencia y lo envía en el payload", async () => {
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);

    await user.type(screen.getByLabelText("Referencia (opcional)"), "PROY-2026-42");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Vinilo");
    await user.type(screen.getByLabelText("Precio línea 1"), "100");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].referencia).toBe("PROY-2026-42");
  });

  it("la columna DTO recalcula el total de la línea y viaja al payload", async () => {
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);

    await user.type(screen.getByLabelText("Descripción línea 1"), "Vinilo");
    await user.clear(screen.getByLabelText("Cantidad línea 1"));
    await user.type(screen.getByLabelText("Cantidad línea 1"), "2");
    await user.type(screen.getByLabelText("Precio línea 1"), "100");
    // Sin descuento: 2 × 100 = 200.
    expect(await screen.findByText("200.00")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Descuento línea 1"));
    await user.type(screen.getByLabelText("Descuento línea 1"), "10");
    // Con 10 %: 180.
    expect(await screen.findByText("180.00")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Crear proforma" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].lines[0].discount_pct).toBe(10);
  });

  it("con una sola dirección no aparece el selector", async () => {
    mockAddresses.mockResolvedValue([
      { codigo: 0, nombre: "(principal)", direccion: "C/ Mayor 1",
        ciudad: "Madrid", cp: "28001", provincia: "Madrid", pais: "724" },
    ]);
    render(<CreateQuoteModal {...base({ factusolCodcli: "55555" })} />);
    await waitFor(() => expect(mockAddresses).toHaveBeenCalledWith("55555"));
    expect(screen.queryByLabelText("Dirección de envío")).not.toBeInTheDocument();
  });

  it("con varias direcciones se puede elegir una alternativa y viaja al payload", async () => {
    mockAddresses.mockResolvedValue([
      { codigo: 0, nombre: "(principal)", direccion: "C/ Mayor 1",
        ciudad: "Madrid", cp: "28001", provincia: "Madrid", pais: "724" },
      { codigo: 1, nombre: "Delegación Norte", direccion: "Pol. Ind. 4",
        ciudad: "Bilbao", cp: "48001", provincia: "Bizkaia", pais: "724" },
    ]);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ factusolCodcli: "55555" })} />);

    const select = await screen.findByLabelText("Dirección de envío");
    await user.selectOptions(select, "1");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Vinilo");
    await user.type(screen.getByLabelText("Precio línea 1"), "100");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].address).toEqual(expect.objectContaining({
      direccion: "Pol. Ind. 4", ciudad: "Bilbao", cp: "48001",
    }));
  });

  it("la dirección principal NO se envía: el backend ya la toma de la empresa", async () => {
    mockAddresses.mockResolvedValue([
      { codigo: 0, nombre: "(principal)", direccion: "C/ Mayor 1",
        ciudad: "Madrid", cp: "28001", provincia: "Madrid", pais: "724" },
      { codigo: 1, nombre: "Delegación Norte", direccion: "Pol. Ind. 4",
        ciudad: "Bilbao", cp: "48001", provincia: "Bizkaia", pais: "724" },
    ]);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ factusolCodcli: "55555" })} />);
    await screen.findByLabelText("Dirección de envío");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Vinilo");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].address).toBeNull();
  });

  // --- Lote B3b: portes + destinatario libre (dropshipping) ----------------

  it("Lote B3b: «Portes (€)» suma a la base mostrada y viaja aparte de las líneas", async () => {
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);

    await user.type(screen.getByLabelText("Descripción línea 1"), "Vinilo");
    await user.type(screen.getByLabelText("Precio línea 1"), "100");
    await user.type(screen.getByLabelText("Portes"), "19.5");
    expect(screen.getByText(/Base:/)).toHaveTextContent("119.50 EUR");
    expect(screen.getByText(/líneas 100.00 \+ portes 19.50/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Crear proforma" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.portes).toBe(19.5);
    // Ninguna línea de portes: van en la banda de la cabecera.
    expect(payload.lines).toHaveLength(1);
    expect(payload.shipping).toBeNull();
  });

  it("Lote B3b: sin portes el payload manda 0 y no hay desglose", async () => {
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.type(screen.getByLabelText("Descripción línea 1"), "Vinilo");
    await user.type(screen.getByLabelText("Precio línea 1"), "100");
    expect(screen.queryByText(/líneas 100.00/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].portes).toBe(0);
  });

  it("Lote B3b: el bloque dropshipping está plegado; al abrirlo, nombre + dirección viajan en `shipping`", async () => {
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    expect(screen.queryByLabelText("Nombre de envío")).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Enviar a otro nombre / dirección (dropshipping)"));
    await user.type(screen.getByLabelText("Nombre de envío"), "Ligue Braille");
    await user.type(screen.getByLabelText("Dirección de entrega"), "Rue d'Angleterre 57");
    await user.type(screen.getByLabelText("Ciudad de entrega"), "Bruxelles");
    await user.type(screen.getByLabelText("Código postal de entrega"), "1060");
    await user.type(screen.getByLabelText("Provincia de entrega"), "Bruxelles-Capitale");
    await user.type(screen.getByLabelText("País de entrega"), "BE");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Placas");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].shipping).toEqual({
      name: "Ligue Braille", address_line: "Rue d'Angleterre 57",
      city: "Bruxelles", postal_code: "1060", state: "Bruxelles-Capitale",
      country: "BE",
    });
  });

  it("Lote B3b: dropshipping abierto pero vacío no envía `shipping`", async () => {
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.click(screen.getByLabelText("Enviar a otro nombre / dirección (dropshipping)"));
    expect(screen.getByLabelText("Nombre de envío")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Descripción línea 1"), "Placas");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].shipping).toBeNull();
  });

  it("Lote B3b: plegar el bloque tras escribir descarta el destinatario (no se manda)", async () => {
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    const toggle = screen.getByLabelText("Enviar a otro nombre / dirección (dropshipping)");
    await user.click(toggle);
    await user.type(screen.getByLabelText("Nombre de envío"), "Alguien");
    await user.click(toggle);
    expect(screen.queryByLabelText("Nombre de envío")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Descripción línea 1"), "Placas");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].shipping).toBeNull();
  });

  it("Lote B3b: dropshipping convive con el selector de direcciones de F_CLI (viajan los dos; manda el libre)", async () => {
    mockAddresses.mockResolvedValue([
      { codigo: 0, nombre: "(principal)", direccion: "C/ Mayor 1",
        ciudad: "Madrid", cp: "28001", provincia: "Madrid", pais: "724" },
      { codigo: 1, nombre: "Delegación Norte", direccion: "Pol. Ind. 4",
        ciudad: "Bilbao", cp: "48001", provincia: "Bizkaia", pais: "724" },
    ]);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ factusolCodcli: "55555" })} />);
    await user.selectOptions(await screen.findByLabelText("Dirección de envío"), "1");
    await user.click(screen.getByLabelText("Enviar a otro nombre / dirección (dropshipping)"));
    await user.type(screen.getByLabelText("Nombre de envío"), "Obra Valencia");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Placas");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.address).toEqual(expect.objectContaining({ ciudad: "Bilbao" }));
    expect(payload.shipping).toEqual(expect.objectContaining({ name: "Obra Valencia" }));
  });

  it("modo edición: título, precarga y PATCH en vez de POST", async () => {
    mockGetQuote.mockResolvedValue({
      ...quote({ codpre: "574" }), referencia: "REF ORIGINAL",
      line_source: "F_LPS",
      lines: [{ position: 1, codart: "MBO", description: "Cabezal MBO",
                quantity: 1, unit_price: 250, discount_pct: 5,
                line_total: 237.5, iva_pct: 21 }],
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ editCodpre: "574" })} />);

    expect(await screen.findByText("Editar proforma nº 574")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText("Referencia (opcional)")).toHaveValue("REF ORIGINAL"));
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("Cabezal MBO");
    expect(screen.getByLabelText("Descuento línea 1")).toHaveValue(5);

    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0]).toBe("574");
    expect(mockUpdate.mock.calls[0][1].force).toBe(false);
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("modo edición (Lote B3b/B4): precarga los portes y el SKU comercial, y los devuelve en el PATCH", async () => {
    mockArticles.mockResolvedValue([article()]);
    mockGetQuote.mockResolvedValue({
      ...quote({ codpre: "574" }), referencia: "REF", line_source: "F_LPS",
      portes: 12,
      lines: [{ position: 1, codart: "00001", sku: "CDR80WPT",
                description: "CD TQ 700 MB white Thermal WPT", quantity: 1,
                unit_price: 0.79, discount_pct: 0, line_total: 0.79,
                iva_pct: 21 }],
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ editCodpre: "574" })} />);
    expect(await screen.findByLabelText("SKU línea 1")).toHaveValue("CDR80WPT");
    expect(screen.getByLabelText("Portes")).toHaveValue(12);
    expect(screen.getByText(/Base:/)).toHaveTextContent("12.79 EUR");
    // La precarga tampoco dispara el autocomplete.
    await afterDebounce();
    expect(mockArticles).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][1].portes).toBe(12);
    expect(mockUpdate.mock.calls[0][1].lines[0].codart).toBe("CDR80WPT");
  });

  it("proforma aceptada: ofrece «Guardar de todos modos» sin perder los cambios", async () => {
    mockGetQuote.mockResolvedValue({
      ...quote({ codpre: "574" }), referencia: "REF", line_source: "F_LPS",
      lines: [{ position: 1, codart: "MBO", description: "Cabezal MBO",
                quantity: 1, unit_price: 250, discount_pct: 0,
                line_total: 250, iva_pct: 21 }],
    });
    mockWaitJob.mockResolvedValueOnce({
      status: "failed", code: "quote_not_editable",
      error: "La proforma 574 está aceptada en FACTUSOL.",
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ editCodpre: "574" })} />);
    await screen.findByText("Editar proforma nº 574");
    await user.click(await screen.findByRole("button", { name: "Guardar cambios" }));

    expect(await screen.findByText(/está aceptada en FACTUSOL/)).toBeInTheDocument();
    // Lo escrito sigue ahí: el modal no se cerró.
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("Cabezal MBO");

    mockWaitJob.mockResolvedValue({ status: "finished", result: { codpre: "574" } });
    await user.click(screen.getByRole("button", { name: "Guardar de todos modos" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(2));
    expect(mockUpdate.mock.calls[1][1].force).toBe(true);
  });

  it("un fallo que NO es de estado no ofrece forzar", async () => {
    mockWaitJob.mockResolvedValue({
      status: "failed", error: "BDEscribirRegistroError",
    });
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);
    await user.type(screen.getByLabelText("Descripción línea 1"), "Vinilo");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    expect(await screen.findByText("BDEscribirRegistroError")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Guardar de todos modos" }),
    ).not.toBeInTheDocument();
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

// Lote 7 · P3 — la proforma de cobro de un pedido manual abre este modal con
// las líneas del pedido ya sembradas (para revisar y crear).
describe("CreateQuoteModal · prefillLines (P3)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAddresses.mockResolvedValue([]);
  });

  it("siembra las líneas iniciales y las envía al crear la proforma", async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({ job_id: "job-1" });
    mockWaitJob.mockResolvedValue({ status: "done", codpre: "900" });
    render(
      <CreateQuoteModal
        companyId="co-1"
        companyName="Duplicoder SL"
        prefillLines={[
          {
            sku: "CDR80WPT", description: "Placa base", quantity: "2",
            unit_price: "150", discount_pct: "0", iva_pct: "21",
          },
        ]}
        prefillReferencia="MANUAL-000006"
        onCreated={jest.fn()}
        onCancel={jest.fn()}
      />,
    );
    // La descripción del pedido aparece precargada en el formulario.
    expect(await screen.findByDisplayValue("Placa base")).toBeInTheDocument();
    expect(screen.getByDisplayValue("MANUAL-000006")).toBeInTheDocument();
    // Al crear, la línea sembrada viaja en el payload (sin volver a teclearla).
    await user.click(screen.getByRole("button", { name: /crear proforma/i }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.referencia).toBe("MANUAL-000006");
    expect(payload.lines.some((l: { description: string }) => l.description === "Placa base")).toBe(true);
  });
});
