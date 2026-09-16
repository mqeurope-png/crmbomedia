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
 *  empresa. Nada de lo que había se pierde (Editar, Ver empresa).
 *
 *  Lote 2 · PR-2 (revisión de diseño §5): antigüedad en palabras por fila
 *  (ámbar pasado el umbral en pendientes), «pendientes» por antigüedad por
 *  defecto, Duplicar como secundario fijo en todas las filas y la convertida
 *  enlazando a su pedido desde la frase y desde «Ver pedido». */

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
// El modal real se prueba en CreateQuoteModal.test.tsx (allí, sin mock, se
// comprueba que `duplicateSource` arranca en «Duplicar» y precarga la vista
// previa con las líneas de origen y «Ver PDF»). Aquí basta con ver a qué modo
// se abre: `edit:` (edición) y `dup:` (duplicar con la proforma de origen).
jest.mock("../../components/erp/CreateQuoteModal", () => ({
  CreateQuoteModal: ({ companyName, editCodpre, duplicateSource, onCreated, onCancel }: {
    companyName: string; editCodpre?: string | null;
    duplicateSource?: { codpre?: string | null } | null;
    onCreated: (jobId: string) => void; onCancel: () => void;
  }) => (
    <div>
      QUOTE MODAL {companyName} edit:{editCodpre ?? "—"} dup:{duplicateSource?.codpre ?? "—"}
      <button type="button" onClick={() => onCreated("job-9")}>GUARDAR MODAL</button>
      <button type="button" onClick={onCancel}>CANCELAR MODAL</button>
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
  tippre: "5", serie: 5, serie_label: "Streamtec", numero: "5-000039",
  estpre: 1, estado: "aceptada", estado_label: "aceptada", queue: "aceptadas",
  queue_label: "Aceptadas · por convertir",
  company: { id: "be", name: "Ligue Braille", country: "BE", factusol_id: "3392" },
  order: null, country_iso2: "BE", regime: "intracomunitario",
  regime_label: "Intracomunitario (exento)", regime_source: "empresa", exento: true,
};
const CLOSSET = {
  ...BRAILLE, codpre: "37", numero: "5-000037", referencia: "Rótulos", fecha: "2026-08-26", clipre: "9999",
  cliente_nombre: "SPRL Clossetcadeaux", total: 331, company: null, country_iso2: null,
  regime: null, regime_label: "Exento (según la proforma)", regime_source: "cabecera",
};
const DUPLICODER = {
  ...BRAILLE, codpre: "40", tippre: "1", serie: 1, serie_label: "Bomedia", numero: "1-000040",
  referencia: "Tinta", fecha: "2026-09-01", clipre: "2458", cliente_nombre: "DUPLICODER",
  base: 100, iva: 21, total: 121, estpre: 0, estado: "pendiente", estado_label: "pendiente",
  queue: "pendientes", queue_label: "Pendientes de respuesta",
  company: { id: "es", name: "Duplicoder SL", country: "ES", factusol_id: "2458" },
  country_iso2: "ES", regime: "nacional", regime_label: "Nacional (con IVA)", exento: false,
};
const RECHAZADA = {
  ...DUPLICODER, codpre: "41", tippre: "2", serie: 2, serie_label: "MQ Europe", numero: "2-000041",
  fecha: "2026-07-15", estpre: 2, estado: "rechazada", estado_label: "rechazada",
  queue: "rechazadas", queue_label: "Rechazadas",
};
const NUEVE = {
  ...BRAILLE, codpre: "9", numero: "5-000009", referencia: "Muestras", fecha: "2026-09-10",
};
const CONVERTIDA = {
  ...BRAILLE, codpre: "71", tippre: "2", serie: 2, serie_label: "MQ Europe", numero: "2-000071",
  referencia: "Placas", queue: "convertidas", queue_label: "Convertidas",
  order: { id: "o71", order_number: "PRO-000071" },
};
/** Pendiente de hace 41 días: pasa del umbral comercial (30). Solo entra en
 *  los tests de PR-2 (no altera los contadores de los demás). */
const VIEJA = {
  ...DUPLICODER, codpre: "42", numero: "1-000042", referencia: "Vinilos", fecha: "2026-08-05",
};
const LISTING = {
  items: [BRAILLE, CLOSSET, DUPLICODER, RECHAZADA, CONVERTIDA, NUEVE],
  unlinked: false,
  queue_counts: { aceptadas: 3, pendientes: 1, rechazadas: 1, convertidas: 1 },
  estpre_values: { "1": 3, "0": 1, "2": 1 },
};
const LISTING_CON_VIEJA = {
  ...LISTING,
  items: [...LISTING.items, VIEJA],
  queue_counts: { ...LISTING.queue_counts, pendientes: 2 },
};

/** «Hoy» fijo (15 sep 2026) para que la antigüedad en palabras sea estable:
 *  39 y 71 son del 4 sep (11 días), 40 del 1 sep (14), 41 del 15 jul (62),
 *  42 del 5 ago (41). */
let nowSpy: jest.SpyInstance<number, []>;

beforeEach(() => {
  nowSpy = jest.spyOn(Date, "now").mockReturnValue(new Date(2026, 8, 15, 10, 0).getTime());
  mockList.mockReset();
  mockList.mockResolvedValue(LISTING);
  mockConvert.mockReset();
  mockDuplicate.mockReset();
  mockStatus.mockReset();
  mockPdf.mockReset();
  mockCompany.mockReset();
  (saveBlob as jest.Mock).mockReset();
});

afterEach(() => {
  nowSpy.mockRestore();
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
    await waitFor(() => expect(mockList).toHaveBeenCalledWith({ days_back: 365, limit: 500 }));
    const colas = within(screen.getByRole("navigation", { name: "Colas de proformas" }));
    expect(colas.getByRole("button", { name: "Aceptadas · por convertir (3)" })).toHaveAttribute("aria-pressed", "true");
    expect(colas.getByRole("button", { name: "Pendientes de respuesta (1)" })).toBeInTheDocument();
    expect(colas.getByRole("button", { name: "Rechazadas (1)" })).toBeInTheDocument();
    expect(colas.getByRole("button", { name: "Convertidas (1)" })).toBeInTheDocument();

    expect(list.getAllByRole("listitem")).toHaveLength(3);              // solo la cola activa
    const braille = within(row("39"));
    expect(braille.getByText("5-000039")).toBeInTheDocument();          // nº visible serie-código
    expect(braille.getByText("Streamtec")).toBeInTheDocument();         // empresa emisora
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
    expect(conv.getByRole("link", { name: "Ver pedido" })).toHaveAttribute("href", "/erp/orders/o71");
    expect(conv.queryByRole("button", { name: "Convertir en pedido" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Rechazadas (1)" }));
    const rech = within(row("41"));
    expect(rech.getByText("rechazada")).toBeInTheDocument();
    expect(rech.queryByRole("button", { name: "Convertir en pedido" })).toBeNull();
    // Duplicar va fijo en la fila; en «⋯» quedan Convertir de todas formas,
    // Editar y Ver empresa.
    expect(rech.getByRole("button", { name: "Duplicar" })).toBeInTheDocument();
    await user.click(rech.getByRole("button", { name: "Más acciones 41" }));
    expect(rech.getByRole("button", { name: "Convertir de todas formas" })).toBeInTheDocument();
    expect(rech.getByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(rech.getByRole("link", { name: "Ver empresa" })).toHaveAttribute("href", "/companies/es");
    expect(rech.getAllByRole("button", { name: "Duplicar" })).toHaveLength(1);

    // Volver a pulsar la cola activa → todas; el buscador filtra dentro.
    await user.click(screen.getByRole("button", { name: "Rechazadas (1)" }));
    expect(within(screen.getByRole("list", { name: "Proformas" })).getAllByRole("listitem")).toHaveLength(6);
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
      queue_counts: { aceptadas: 2, pendientes: 1, rechazadas: 1, convertidas: 2 },
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

  it("PDF descarga el presupuesto en el idioma del cliente; Duplicar abre la previsualización (modal en modo «Duplicar»), sin duplicado directo", async () => {
    mockPdf.mockResolvedValue(new Blob(["%PDF"]));
    mockStatus.mockResolvedValue({ status: "finished", result: { codpre: "600" } });
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    await user.click(within(row("39")).getByRole("button", { name: "PDF" }));
    await waitFor(() => expect(mockPdf).toHaveBeenCalledWith("presupuestos", 5, "39", "fr", {}));
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), "Proforma_39.pdf");
    // Duplicar está en la fila, sin abrir «⋯»; y NO duplica directamente: abre el
    // modal en modo «Duplicar» con la proforma 39 como origen (previsualización).
    await user.click(within(row("39")).getByRole("button", { name: "Duplicar" }));
    expect(await screen.findByText(/QUOTE MODAL Ligue Braille edit:— dup:39/)).toBeInTheDocument();
    expect(mockDuplicate).not.toHaveBeenCalled();
    // La copia se crea desde el propio modal (tras la vista previa), y la
    // pantalla espera al job, avisa y recarga — como en el alta y la edición.
    await user.click(screen.getByRole("button", { name: "GUARDAR MODAL" }));
    expect(await screen.findByText("Proforma nº 600 creada.")).toBeInTheDocument();
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

  function order(): string[] {
    return screen.getAllByRole("listitem").map((el) => el.getAttribute("aria-label") ?? "");
  }

  it("buscador en vivo por empresa, referencia y nº (con serie o a secas); los contadores siguen al filtro", async () => {
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    const buscar = screen.getByRole("searchbox", { name: "Buscar proforma" });
    // Empresa (CRM vinculada): Ligue Braille → 39 y 9 en aceptadas, 71 en convertidas.
    await user.type(buscar, "ligue");
    expect(order()).toEqual(["Proforma 9", "Proforma 39"]);
    expect(screen.getByRole("button", { name: "Aceptadas · por convertir (2)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Convertidas (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pendientes de respuesta (0)" })).toBeInTheDocument();
    // Referencia de una proforma de OTRA cola: la lista de la activa queda vacía
    // y el contador dice dónde está.
    await user.clear(buscar);
    await user.type(buscar, "tinta");
    expect(screen.getByText("Nada en «Aceptadas · por convertir».")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pendientes de respuesta (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aceptadas · por convertir (0)" })).toBeInTheDocument();
    // Nº con serie y a secas.
    await user.clear(buscar);
    await user.type(buscar, "5-000037");
    expect(order()).toEqual(["Proforma 37"]);
    await user.clear(buscar);
    await user.type(buscar, "37");
    expect(order()).toEqual(["Proforma 37"]);
    // Cliente del documento (sin empresa CRM).
    await user.clear(buscar);
    await user.type(buscar, "closset");
    expect(order()).toEqual(["Proforma 37"]);
    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    expect(order()).toHaveLength(3);
  });

  it("el rango de fechas acota y se combina con el texto y la cola; un «desde» antiguo amplía el periodo pedido", async () => {
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    await user.click(screen.getByRole("button", { name: "Aceptadas · por convertir (3)" }));  // todas
    expect(order()).toHaveLength(6);
    await user.type(screen.getByLabelText("Fecha desde"), "2026-09-01");
    await user.type(screen.getByLabelText("Fecha hasta"), "2026-09-05");
    expect(order()).toEqual(["Proforma 71", "Proforma 39", "Proforma 40"]);   // fecha desc, empate → nº desc
    expect(screen.getByRole("button", { name: "Aceptadas · por convertir (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rechazadas (0)" })).toBeInTheDocument();
    // + texto
    await user.type(screen.getByRole("searchbox", { name: "Buscar proforma" }), "ligue");
    expect(order()).toEqual(["Proforma 71", "Proforma 39"]);
    // + cola
    await user.click(screen.getByRole("button", { name: "Convertidas (1)" }));
    expect(order()).toEqual(["Proforma 71"]);
    // «Desde» más antiguo que el periodo (1 año) → se pide más al backend.
    await user.clear(screen.getByLabelText("Fecha desde"));
    await user.type(screen.getByLabelText("Fecha desde"), "2024-01-01");
    await waitFor(() => expect(mockList).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 500, days_back: expect.any(Number) }),
    ));
    const last = mockList.mock.calls[mockList.mock.calls.length - 1][0] as { days_back: number };
    expect(last.days_back).toBeGreaterThan(365);
    expect(last.days_back).toBeLessThanOrEqual(1825);
  });

  it("orden por fecha (por defecto, desc), serie y nº de FACTUSOL (numérico), asc y desc, dentro de la cola activa", async () => {
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    // En la cola activa (aceptadas): fecha desc por defecto.
    expect(order()).toEqual(["Proforma 9", "Proforma 39", "Proforma 37"]);
    await user.click(screen.getByRole("button", { name: "Aceptadas · por convertir (3)" }));  // todas
    expect(order()).toEqual([
      "Proforma 9", "Proforma 71", "Proforma 39", "Proforma 40", "Proforma 37", "Proforma 41",
    ]);
    // Nº de proforma: numérico de verdad (9 antes que 37), asc y desc.
    await user.selectOptions(screen.getByLabelText("Ordenar por"), "codpre");
    expect(order()).toEqual([
      "Proforma 71", "Proforma 41", "Proforma 40", "Proforma 39", "Proforma 37", "Proforma 9",
    ]);
    await user.click(screen.getByRole("button", { name: "Orden descendente" }));
    expect(order()).toEqual([
      "Proforma 9", "Proforma 37", "Proforma 39", "Proforma 40", "Proforma 41", "Proforma 71",
    ]);
    // Serie (1 Bomedia, 2 MQ Europe, 5 Streamtec) asc; empate → nº.
    await user.selectOptions(screen.getByLabelText("Ordenar por"), "serie");
    expect(order()).toEqual([
      "Proforma 40", "Proforma 41", "Proforma 71", "Proforma 9", "Proforma 37", "Proforma 39",
    ]);
    await user.click(screen.getByRole("button", { name: "Orden ascendente" }));
    expect(order()).toEqual([
      "Proforma 39", "Proforma 37", "Proforma 9", "Proforma 71", "Proforma 41", "Proforma 40",
    ]);
    // Fecha asc.
    await user.selectOptions(screen.getByLabelText("Ordenar por"), "fecha");
    await user.click(screen.getByRole("button", { name: "Orden descendente" }));
    expect(order()).toEqual([
      "Proforma 41", "Proforma 37", "Proforma 40", "Proforma 39", "Proforma 71", "Proforma 9",
    ]);
    // Y dentro de una cola.
    await user.click(screen.getByRole("button", { name: "Aceptadas · por convertir (3)" }));
    expect(order()).toEqual(["Proforma 37", "Proforma 39", "Proforma 9"]);
  });

  // ---- Lote 2 · PR-2 (revisión de diseño §5 «Proformas») ----

  it("PR-2 · antigüedad en palabras en cada fila; pasados 30 días, la pendiente va en ámbar (aviso comercial)", async () => {
    mockList.mockResolvedValue(LISTING_CON_VIEJA);
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    expect(within(row("39")).getByText("Aceptada hace 11 días")).not.toHaveClass("is-late");
    expect(within(row("9")).getByText("Aceptada hace 5 días")).toBeInTheDocument();
    expect(within(row("37")).getByText("Aceptada hace 20 días")).toBeInTheDocument();
    // La fecha absoluta sigue al lado, como dato (mono).
    expect(within(row("39")).getByText("04/09/2026")).toHaveClass("mono");

    await user.click(screen.getByRole("button", { name: "Pendientes de respuesta (2)" }));
    const reciente = within(row("40")).getByText("Enviada hace 14 días · sin respuesta");
    expect(reciente).not.toHaveClass("is-late");
    const tardia = within(row("42")).getByText("Enviada hace 41 días · sin respuesta");
    expect(tardia).toHaveClass("is-late");
    expect(tardia).toHaveAttribute("title", expect.stringContaining("41 días sin respuesta"));

    await user.click(screen.getByRole("button", { name: "Rechazadas (1)" }));
    expect(within(row("41")).getByText("Rechazada hace 9 semanas")).not.toHaveClass("is-late");
  });

  it("PR-2 · «pendientes» va por antigüedad (más antiguas primero) por defecto; las demás por fecha desc; la elección del usuario manda", async () => {
    mockList.mockResolvedValue(LISTING_CON_VIEJA);
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    expect(order()).toEqual(["Proforma 9", "Proforma 39", "Proforma 37"]);      // aceptadas: fecha desc
    await user.click(screen.getByRole("button", { name: "Pendientes de respuesta (2)" }));
    expect(order()).toEqual(["Proforma 42", "Proforma 40"]);                     // la de 41 días primero
    expect(screen.getByRole("button", { name: "Orden ascendente" })).toBeInTheDocument();
    expect(screen.getByText(/más antiguas primero/)).toBeInTheDocument();
    // Otra cola → fecha desc, sin que el usuario toque nada; y al volver, antigüedad.
    await user.click(screen.getByRole("button", { name: "Convertidas (1)" }));
    expect(screen.getByRole("button", { name: "Orden descendente" })).toBeInTheDocument();
    expect(screen.queryByText(/más antiguas primero/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Pendientes de respuesta (2)" }));
    expect(order()).toEqual(["Proforma 42", "Proforma 40"]);
    // El usuario elige desc: se respeta en pendientes, en las demás y al volver.
    await user.click(screen.getByRole("button", { name: "Orden ascendente" }));
    expect(order()).toEqual(["Proforma 40", "Proforma 42"]);
    expect(screen.queryByText(/más antiguas primero/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Aceptadas · por convertir (3)" }));
    expect(order()).toEqual(["Proforma 9", "Proforma 39", "Proforma 37"]);
    await user.click(screen.getByRole("button", { name: "Pendientes de respuesta (2)" }));
    expect(order()).toEqual(["Proforma 40", "Proforma 42"]);
    // Y con «Nº de proforma» tampoco se le cambia la dirección elegida.
    await user.selectOptions(screen.getByLabelText("Ordenar por"), "codpre");
    expect(order()).toEqual(["Proforma 42", "Proforma 40"]);
  });

  it("PR-2 · Duplicar es un secundario fijo en cada fila, también en convertidas, y no se repite en «⋯»", async () => {
    mockStatus.mockResolvedValue({ status: "finished", result: { codpre: "602" } });
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    for (const codpre of ["9", "39", "37"]) {
      const dup = within(row(codpre)).getByRole("button", { name: "Duplicar" });
      expect(dup).toHaveClass("button", "secondary");
      expect(dup.closest(".erp-flow-menu-pop")).toBeNull();                     // fuera de «⋯»
    }
    await user.click(screen.getByRole("button", { name: "Convertidas (1)" }));
    const conv = within(row("71"));
    await user.click(conv.getByRole("button", { name: "Más acciones 71" }));
    expect(conv.getAllByRole("button", { name: "Duplicar" })).toHaveLength(1);
    expect(conv.queryByRole("link", { name: "Abrir pedido" })).toBeNull();      // «Ver pedido» ya está en la fila
    expect(conv.getByRole("button", { name: "Editar" })).toBeInTheDocument();
    // También en convertidas, Duplicar abre la previsualización (modal en modo
    // «Duplicar» con la 71 de origen); nunca duplica directamente.
    await user.click(conv.getByRole("button", { name: "Duplicar" }));
    expect(await screen.findByText(/QUOTE MODAL .* dup:71/)).toBeInTheDocument();
    expect(mockDuplicate).not.toHaveBeenCalled();
  });

  // ---- Lote 3 · Duplicar con previsualización ----

  it("Lote 3 · Duplicar abre el modal en modo «Duplicar» con la proforma de la fila como origen; no existe duplicado directo", async () => {
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    // El modal aún no está montado.
    expect(screen.queryByText(/QUOTE MODAL/)).toBeNull();
    // Duplicar abre el MISMO modal que «Nueva proforma → Duplicar», ya en modo
    // «Duplicar» (dup:39) y con el cliente de la propia proforma como destino.
    await user.click(within(row("39")).getByRole("button", { name: "Duplicar" }));
    expect(await screen.findByText(/QUOTE MODAL Ligue Braille edit:— dup:39/)).toBeInTheDocument();
    // El endpoint de duplicado directo NO se ha llamado: la copia solo puede
    // salir de la vista previa del modal (Usar como plantilla → Crear proforma).
    expect(mockDuplicate).not.toHaveBeenCalled();
    // Cancelar cierra el modal sin crear ni duplicar nada.
    await user.click(screen.getByRole("button", { name: "CANCELAR MODAL" }));
    expect(screen.queryByText(/QUOTE MODAL/)).toBeNull();
    expect(mockDuplicate).not.toHaveBeenCalled();
  });

  it("PR-2 · la convertida enlaza a su pedido desde la frase de estado y desde «Ver pedido» (secundario), y conserva la pastilla", async () => {
    const user = userEvent.setup();
    render(<ProformasPage />);
    await screen.findByRole("list", { name: "Proformas" });
    await user.click(screen.getByRole("button", { name: "Convertidas (1)" }));
    const conv = within(row("71"));
    const frase = conv.getByText(/Convertida en/);
    expect(frase).toHaveTextContent("Convertida en PRO-000071");
    expect(frase).not.toHaveClass("is-late");
    expect(within(frase).getByRole("link", { name: "PRO-000071" })).toHaveAttribute("href", "/erp/orders/o71");
    const ver = conv.getByRole("link", { name: "Ver pedido" });
    expect(ver).toHaveAttribute("href", "/erp/orders/o71");
    expect(ver).toHaveClass("button", "secondary");
    expect(conv.getByText("pedido PRO-000071")).toHaveClass("badge");
    expect(conv.queryByRole("button", { name: "Convertir en pedido" })).toBeNull();
    expect(conv.queryByText(/sin respuesta/)).toBeNull();                       // ya es pedido
  });
});
