import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProformasPage from "./page";
import { getCompany } from "../../lib/companiesApi";
import {
  convertFactusolQuoteToOrder,
  downloadFactusolDocumentPdf,
  duplicateFactusolQuote,
  getQuoteJobStatus,
  listFactusolQuotes,
  saveBlob,
} from "../../lib/erpApi";

/** Pantalla Proformas (rediseño de flujo, Fase 4): colas arriba con contador
 *  (aceptadas · por convertir / pendientes / rechazadas + convertidas), lista
 *  de la cola elegida con nº, cliente (país · régimen), importe, estado y la
 *  acción principal «Convertir en pedido» (la conversión de siempre: paso de
 *  pago + albarán), PDF y Duplicar; «+ Nueva proforma» con el buscador de
 *  empresa. Nada de lo que había se pierde (Editar, Ver empresa). */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title, description, actions }: {
    title: string; description?: string; actions?: React.ReactNode;
  }) => <div><h1>{title}</h1><p>{description}</p>{actions}</div>,
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/companiesApi", () => ({ getCompany: jest.fn() }));
jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  listFactusolQuotes: jest.fn(),
  convertFactusolQuoteToOrder: jest.fn(),
  duplicateFactusolQuote: jest.fn(),
  getQuoteJobStatus: jest.fn(),
  downloadFactusolDocumentPdf: jest.fn(),
  saveBlob: jest.fn(),
  // Paso de pago (Fase 2): catálogos.
  getContrapartidas: jest.fn(() => Promise.resolve([{ codigo: "6", nombre: "Bomedia Sabadell" }])),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([{ codigo: "002", nombre: "Transferencia" }])),
}));
jest.mock("../../components/CompanyPickerModal", () => ({
  CompanyPickerModal: ({ open, onPick }: { open: boolean; onPick: (id: string | null, label: string) => void }) =>
    open ? <button type="button" onClick={() => onPick("c1", "Acme SL")}>PICK Acme SL</button> : null,
}));
jest.mock("../../components/erp/CreateQuoteModal", () => ({
  CreateQuoteModal: ({ companyName, editCodpre, onCreated }: {
    companyName: string; editCodpre?: string | null; onCreated: (jobId: string) => void;
  }) => (
    <div>
      QUOTE MODAL {companyName} edit:{editCodpre ?? "—"}
      <button type="button" onClick={() => onCreated("job-9")}>GUARDAR MODAL</button>
    </div>
  ),
}));

const mockList = listFactusolQuotes as jest.Mock;
const mockConvert = convertFactusolQuoteToOrder as jest.Mock;
const mockDuplicate = duplicateFactusolQuote as jest.Mock;
const mockStatus = getQuoteJobStatus as jest.Mock;
const mockPdf = downloadFactusolDocumentPdf as jest.Mock;
const mockCompany = getCompany as jest.Mock;

const BRAILLE = {
  codpre: "39", referencia: "Placas braille", fecha: "2026-09-04", clipre: "3392",
  cliente_nombre: "LIGUE BRAILLE", base: 4770, iva: 0, total: 4770,
  estpre: 1, estado: "aceptada", estado_label: "aceptada", queue: "aceptadas",
  queue_label: "Aceptadas · por convertir",
  company: { id: "be", name: "Ligue Braille", country: "BE", factusol_id: "3392" },
  order: null, country_iso2: "BE", regime: "intracomunitario",
  regime_label: "Intracomunitario (exento)", regime_source: "empresa", exento: true,
};
const CLOSSET = {
  ...BRAILLE, codpre: "37", referencia: "Rótulos", fecha: "2026-08-26", clipre: "9999",
  cliente_nombre: "SPRL Clossetcadeaux", total: 331, company: null, country_iso2: null,
  regime: null, regime_label: "Exento (según la proforma)", regime_source: "cabecera",
};
const DUPLICODER = {
  ...BRAILLE, codpre: "40", referencia: "Tinta", clipre: "2458", cliente_nombre: "DUPLICODER",
  base: 100, iva: 21, total: 121, estpre: 0, estado: "pendiente", estado_label: "pendiente",
  queue: "pendientes", queue_label: "Pendientes de respuesta",
  company: { id: "es", name: "Duplicoder SL", country: "ES", factusol_id: "2458" },
  country_iso2: "ES", regime: "nacional", regime_label: "Nacional (con IVA)", exento: false,
};
const RECHAZADA = {
  ...DUPLICODER, codpre: "41", estpre: 2, estado: "rechazada", estado_label: "rechazada",
  queue: "rechazadas", queue_label: "Rechazadas",
};
const CONVERTIDA = {
  ...BRAILLE, codpre: "71", referencia: "Placas", queue: "convertidas", queue_label: "Convertidas",
  order: { id: "o71", order_number: "PRO-000071" },
};
const LISTING = {
  items: [BRAILLE, CLOSSET, DUPLICODER, RECHAZADA, CONVERTIDA],
  unlinked: false,
  queue_counts: { aceptadas: 2, pendientes: 1, rechazadas: 1, convertidas: 1 },
  estpre_values: { "1": 3, "0": 1, "2": 1 },
};

beforeEach(() => {
  mockList.mockReset();
  mockList.mockResolvedValue(LISTING);
  mockConvert.mockReset();
  mockDuplicate.mockReset();
  mockStatus.mockReset();
  mockPdf.mockReset();
  mockCompany.mockReset();
  (saveBlob as jest.Mock).mockReset();
});

function row(codpre: string) {
  return screen.getByRole("listitem", { name: `Proforma ${codpre}` });
}

describe("Pantalla Proformas (rediseño de flujo, Fase 4)", () => {
  it("colas con contador y la lista de «Aceptadas · por convertir»: nº, cliente con país · régimen, importe y acciones", async () => {
    render(<ProformasPage />);
    expect(await screen.findByRole("heading", { name: "Proformas" })).toBeInTheDocument();
    expect(screen.getByText("Presupuestos enviados y su estado.")).toBeInTheDocument();
    const list = within(await screen.findByRole("list", { name: "Proformas" }));
    await waitFor(() => expect(mockList).toHaveBeenCalledWith({ days_back: 365 }));
    const colas = within(screen.getByRole("navigation", { name: "Colas de proformas" }));
    expect(colas.getByRole("button", { name: "Aceptadas · por convertir (2)" })).toHaveAttribute("aria-pressed", "true");
    expect(colas.getByRole("button", { name: "Pendientes de respuesta (1)" })).toBeInTheDocument();
    expect(colas.getByRole("button", { name: "Rechazadas (1)" })).toBeInTheDocument();
    expect(colas.getByRole("button", { name: "Convertidas (1)" })).toBeInTheDocument();

    expect(list.getAllByRole("listitem")).toHaveLength(2);              // solo la cola activa
    const braille = within(row("39"));
    expect(braille.getByText("aceptada")).toBeInTheDocument();
    expect(braille.getByRole("link", { name: "Ligue Braille" })).toHaveAttribute("href", "/companies/be");
    expect(braille.getByText("BE · intracomunitario · exento")).toBeInTheDocument();
    expect(braille.getByText(/4770\.00 €/)).toHaveTextContent("exento · intracomunitario");
    expect(braille.getByText("04/09/2026")).toBeInTheDocument();
    expect(braille.getByRole("button", { name: "Convertir en pedido" })).toBeInTheDocument();
    expect(braille.getByRole("button", { name: "PDF" })).toBeInTheDocument();
    // Sin empresa vinculada: nombre del documento y «exento según la proforma».
    const closset = within(row("37"));
    expect(closset.getByText("SPRL Clossetcadeaux")).toBeInTheDocument();
    expect(closset.getByText("exento · según la proforma")).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "Proforma 40" })).toBeNull();
  });

  it("cambiar de cola filtra; pendientes con IVA nacional; convertidas abre el pedido; rechazadas sin convertir como principal", async () => {
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    await user.click(screen.getByRole("button", { name: "Pendientes de respuesta (1)" }));
    const dupli = within(row("40"));
    expect(dupli.getByText("pendiente")).toBeInTheDocument();
    expect(dupli.getByText("ES · nacional · con IVA")).toBeInTheDocument();
    expect(dupli.getByText(/121\.00 €/)).toHaveTextContent("IVA 21 % · nacional");
    expect(dupli.getByRole("button", { name: "Convertir en pedido" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Convertidas (1)" }));
    const conv = within(row("71"));
    expect(conv.getByRole("link", { name: "Abrir pedido PRO-000071" })).toHaveAttribute("href", "/erp/orders/o71");
    expect(conv.queryByRole("button", { name: "Convertir en pedido" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Rechazadas (1)" }));
    const rech = within(row("41"));
    expect(rech.getByText("rechazada")).toBeInTheDocument();
    expect(rech.queryByRole("button", { name: "Convertir en pedido" })).toBeNull();
    await user.click(rech.getByRole("button", { name: "Más acciones 41" }));
    expect(rech.getByRole("button", { name: "Convertir de todas formas" })).toBeInTheDocument();
    expect(rech.getByRole("button", { name: "Duplicar" })).toBeInTheDocument();
    expect(rech.getByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(rech.getByRole("link", { name: "Ver empresa" })).toHaveAttribute("href", "/companies/es");

    // Volver a pulsar la cola activa → todas; el buscador filtra dentro.
    await user.click(screen.getByRole("button", { name: "Rechazadas (1)" }));
    expect(within(screen.getByRole("list", { name: "Proformas" })).getAllByRole("listitem")).toHaveLength(5);
    await user.type(screen.getByRole("searchbox", { name: "Buscar proforma" }), "closset");
    expect(within(screen.getByRole("list", { name: "Proformas" })).getAllByRole("listitem")).toHaveLength(1);
  });

  it("«Convertir en pedido» es la conversión de siempre: paso de pago, encola, espera al job, avisa y recarga (pasa a convertidas)", async () => {
    mockConvert.mockResolvedValue({ job_id: "job-c1", status: "queued", codpre: "39" });
    mockStatus.mockResolvedValue({
      status: "finished",
      result: { order_id: "o39", order_number: "PRO-000039", albaran: { numero: "5-500009", status: "created" } },
    });
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    await user.click(within(row("39")).getByRole("button", { name: "Convertir en pedido" }));
    const dialog = await screen.findByRole("dialog", { name: "Convertir proforma en pedido" });
    expect(dialog).toHaveTextContent("Convertir la proforma 39 en pedido");
    expect(within(dialog).getByLabelText("Sin pago")).toBeChecked();
    mockList.mockResolvedValue({
      ...LISTING,
      items: LISTING.items.map((q) => (q.codpre === "39"
        ? { ...q, queue: "convertidas", order: { id: "o39", order_number: "PRO-000039" } } : q)),
      queue_counts: { aceptadas: 1, pendientes: 1, rechazadas: 1, convertidas: 2 },
    });
    await user.click(within(dialog).getByRole("button", { name: "Crear pedido y albarán" }));
    await waitFor(() => expect(mockConvert).toHaveBeenCalledWith("39", expect.objectContaining({
      create_albaran: true, payment: expect.objectContaining({ paid: false }),
    })));
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("Pedido PRO-000039 creado desde la proforma 39");
    expect(notice).toHaveTextContent("Albarán FACTUSOL 5-500009 creado");
    // Recarga: ya no está en «aceptadas» y las colas se actualizan.
    expect(await screen.findByRole("button", { name: "Convertidas (2)" })).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "Proforma 39" })).toBeNull();
  });

  it("PDF descarga el presupuesto en el idioma del cliente; Duplicar encola, espera y avisa", async () => {
    mockPdf.mockResolvedValue(new Blob(["%PDF"]));
    mockDuplicate.mockResolvedValue({ job_id: "job-d1", status: "queued", source_codpre: "39" });
    mockStatus.mockResolvedValue({ status: "finished", result: { codpre: "600" } });
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    await user.click(within(row("39")).getByRole("button", { name: "PDF" }));
    await waitFor(() => expect(mockPdf).toHaveBeenCalledWith("presupuestos", 1, "39", "fr", {}));
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), "Proforma_39.pdf");
    await user.click(within(row("39")).getByRole("button", { name: "Más acciones 39" }));
    await user.click(within(row("39")).getByRole("button", { name: "Duplicar" }));
    await waitFor(() => expect(mockDuplicate).toHaveBeenCalledWith("39"));
    expect(await screen.findByText("Proforma nº 600 creada (duplicado de 39).")).toBeInTheDocument();
    expect(mockList).toHaveBeenCalledTimes(2);                        // recarga
  });

  it("«+ Nueva proforma» elige la empresa con el buscador y abre el alta; sin vínculo FACTUSOL avisa", async () => {
    mockCompany.mockResolvedValueOnce({ id: "c1", name: "Acme SL", factusol_company_id: "55555" });
    mockStatus.mockResolvedValue({ status: "finished", result: { codpre: "601" } });
    const user = userEvent.setup();
    render(<ProformasPage />);
    await user.click(await screen.findByRole("button", { name: "+ Nueva proforma" }));
    await user.click(screen.getByRole("button", { name: "PICK Acme SL" }));
    expect(await screen.findByText(/QUOTE MODAL Acme SL edit:—/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "GUARDAR MODAL" }));
    expect(await screen.findByText("Proforma nº 601 creada.")).toBeInTheDocument();
    // Sin vínculo: no se abre el alta, se avisa.
    mockCompany.mockResolvedValueOnce({ id: "c2", name: "Sin Vínculo SL", factusol_company_id: null });
    await user.click(screen.getByRole("button", { name: "+ Nueva proforma" }));
    await user.click(screen.getByRole("button", { name: "PICK Acme SL" }));
    expect(await screen.findByText(/«Sin Vínculo SL» no está vinculada/)).toBeInTheDocument();
    expect(screen.queryByText(/QUOTE MODAL Sin Vínculo/)).toBeNull();
    // Editar desde «⋯» abre el mismo alta en modo edición.
    await user.click(within(row("39")).getByRole("button", { name: "Más acciones 39" }));
    await user.click(within(row("39")).getByRole("button", { name: "Editar" }));
    expect(await screen.findByText(/QUOTE MODAL Ligue Braille edit:39/)).toBeInTheDocument();
  });
});
