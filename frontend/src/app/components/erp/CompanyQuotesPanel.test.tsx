import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyQuotesPanel } from "./CompanyQuotesPanel";
import {
  convertFactusolQuoteToOrder,
  getQuoteConvertPreview,
  getQuoteJobStatus,
  listFactusolQuotes,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  FACTUSOL_SERIES: jest.requireActual("../../lib/erpApi").FACTUSOL_SERIES,
  convertFactusolQuoteToOrder: jest.fn(),
  getQuoteJobStatus: jest.fn(),
  listFactusolQuotes: jest.fn(),
  // El modal hijo usa estos; se dejan inertes.
  createFactusolQuote: jest.fn(),
  updateFactusolQuote: jest.fn(),
  getFactusolQuote: jest.fn(),
  getFactusolCustomerAddresses: jest.fn(() => Promise.resolve([])),
  getFactusolCustomerPaymentMethod: jest.fn(() => Promise.resolve({ codigo: null, nombre: null })),
  waitForQuoteJob: jest.fn(),
  duplicateFactusolQuote: jest.fn(),
  searchFactusolArticles: jest.fn(),
  // Fase 2: catálogos del paso de pago.
  getContrapartidas: jest.fn(() => Promise.resolve([
    { codigo: "6", nombre: "Bomedia Sabadell" }, { codigo: "8", nombre: "Streamtec Sabadell" },
  ])),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([
    { codigo: "002", nombre: "Transferencia" },
  ])),
  // «Convertir en pedido» enseña antes el resumen de lo que se va a crear.
  getQuoteConvertPreview: jest.fn(),
}));
const mockList = listFactusolQuotes as jest.Mock;
const mockConvert = convertFactusolQuoteToOrder as jest.Mock;
const mockStatus = getQuoteJobStatus as jest.Mock;
const mockPreview = getQuoteConvertPreview as jest.Mock;

/** Resumen de la conversión (solo LEE; aún no convertida). */
function convertPreview(over = {}) {
  return {
    codpre: "77", serie: 1, company_linked: true, cliente_codigo: "55555",
    already_existed: null,
    company_id: "c1", company_name: "Acme SL", fecha: "2026-08-01",
    lineas: [{ product_sku: "", description: "Instalación sala 3", quantity: 1,
               unit_price: 100, discount_pct: 0, line_total: 100, tax_rate: 21 }],
    total: 121,
    origen: { doc_type: "presupuestos", label: "proforma", serie: 1, codigo: 77,
              numero: "1-000077", referencia: "Instalación sala 3" },
    ...over,
  };
}

function quote(over = {}) {
  return {
    codpre: "77", referencia: "Instalación sala 3", fecha: "2026-08-01",
    clipre: "55555", cliente_nombre: "Acme SL", base: 100, iva: 21, total: 121,
    ...over,
  };
}

beforeEach(() => {
  mockList.mockReset();
  mockConvert.mockReset();
  mockStatus.mockReset();
  mockPreview.mockReset();
  mockList.mockResolvedValue({ items: [], unlinked: false });
  mockPreview.mockResolvedValue(convertPreview());
});

/** Abre «Convertir en pedido» y espera al resumen: hasta entonces el botón
 *  «Crear pedido y albarán» está deshabilitado. */
async function abrirConversion(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Convertir en pedido" }));
  const dialog = await screen.findByRole("dialog", { name: "Convertir proforma en pedido" });
  await within(dialog).findByRole("region", { name: "Resumen del pedido" });
  expect(mockPreview).toHaveBeenCalledWith("77", null);
  return dialog;
}

function base(over = {}) {
  return {
    companyId: "c1", companyName: "Acme SL", factusolCodcli: "55555", ...over,
  };
}

describe("CompanyQuotesPanel", () => {
  it("empresa sin vínculo FACTUSOL: pide vincular en vez de listar", async () => {
    render(<CompanyQuotesPanel {...base({ factusolCodcli: null })} />);
    expect(
      await screen.findByText(/no está vinculada a un cliente de FACTUSOL/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nueva proforma" })).not.toBeInTheDocument();
  });

  it("lista las proformas del cliente", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    render(<CompanyQuotesPanel {...base()} />);
    expect(await screen.findByText("Instalación sala 3")).toBeInTheDocument();
    expect(screen.getByText("121.00 €")).toBeInTheDocument();
  });

  it("cada proforma ofrece Editar, Duplicar y Enviar por email además de Convertir en pedido", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    render(<CompanyQuotesPanel {...base()} />);
    expect(await screen.findByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Duplicar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enviar por email" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Convertir en pedido" }))
      .toBeInTheDocument();
  });

  it("Remates · punto 6: sin permiso, ni «Duplicar» ni «Enviar por email»", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    render(<CompanyQuotesPanel {...base()} canDuplicate={false} canEmail={false} />);
    expect(await screen.findByRole("button", { name: "Editar" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Duplicar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enviar por email" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Convertir en pedido" })).toBeInTheDocument();
  });

  it("Punto A · una proforma ya enviada enseña «Enviada dd/mm» y «Reenviar»", async () => {
    mockList.mockResolvedValue({
      items: [quote({ emailed_at: "2026-10-02T09:00:00", emailed_to: ["x@acme.es"] })],
      unlinked: false,
    });
    render(<CompanyQuotesPanel {...base()} />);
    expect(await screen.findByText("Enviada 02/10")).toHaveAttribute("title", expect.stringContaining("x@acme.es"));
    expect(screen.getByRole("button", { name: "Reenviar" })).toBeInTheDocument();
  });

  it("Punto B · «Duplicar» abre la copia directa de esa proforma con la empresa de la ficha como destino", async () => {
    const { getFactusolQuote } = jest.requireMock("../../lib/erpApi");
    (getFactusolQuote as jest.Mock).mockResolvedValue({
      ...quote({ codpre: "77", numero: "1-000077", tippre: "1", serie: 1 }),
      lines: [{ position: 1, codart: null, sku: null, description: "Instalación",
                quantity: 1, unit_price: 100, discount_pct: 0, line_total: 100, iva_pct: 21 }],
    });
    mockList.mockResolvedValue({ items: [quote({ numero: "1-000077", tippre: "1", serie: 1 })], unlinked: false });
    const user = userEvent.setup();
    render(<CompanyQuotesPanel {...base()} />);
    await user.click(await screen.findByRole("button", { name: "Duplicar" }));
    const modal = await screen.findByRole("dialog", { name: "Duplicar proforma FACTUSOL" });
    expect(within(modal).getByRole("heading", { name: "Duplicar proforma nº 1-000077" })).toBeInTheDocument();
    expect(within(modal).getByText("Acme SL")).toBeInTheDocument();          // cliente destino = la ficha
    await waitFor(() => expect(getFactusolQuote).toHaveBeenCalledWith("77", 1));
    await waitFor(() =>
      expect(within(modal).getByLabelText("Descripción línea 1")).toHaveValue("Instalación"));
    expect(within(modal).getByRole("button", { name: "Crear proforma" })).toBeInTheDocument();
  });

  it("convertir en pedido pasa por el paso de pago, encola, espera al job y avisa del pedido y del albarán", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    mockConvert.mockResolvedValue({ job_id: "job-c1", status: "queued", codpre: "77" });
    mockStatus.mockResolvedValue({
      status: "finished",
      result: {
        order_id: "o-1", order_number: "PRO-000077",
        albaran: { numero: "5-500008", status: "created" },
      },
    });
    const onOrderCreated = jest.fn();
    const user = userEvent.setup();
    render(<CompanyQuotesPanel {...base({ onOrderCreated })} />);

    // Primero el resumen (solo lee); luego el paso de pago. Por defecto «sin pago».
    const dialog = await abrirConversion(user);
    expect(mockConvert).not.toHaveBeenCalled();
    expect(within(dialog).getByLabelText("Sin pago")).toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "Crear pedido y albarán" }));

    await waitFor(() => expect(mockConvert).toHaveBeenCalledWith("77", expect.objectContaining({
      create_albaran: true,
      payment: expect.objectContaining({ paid: false, contrapartida: null }),
    }), undefined));
    const notice = await screen.findByText(/PRO-000077/);
    expect(notice).toHaveTextContent("Albarán FACTUSOL 5-500008 creado");
    expect(notice).toHaveTextContent("Sin pago: pendiente");
    expect(onOrderCreated).toHaveBeenCalledWith("o-1");
  });

  it("«Crear pedido y albarán» está deshabilitado hasta que llega el resumen", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    let llega!: (v: unknown) => void;
    mockPreview.mockReturnValue(new Promise((r) => { llega = r; }));
    const user = userEvent.setup();
    render(<CompanyQuotesPanel {...base()} />);
    await user.click(await screen.findByRole("button", { name: "Convertir en pedido" }));
    const dialog = await screen.findByRole("dialog", { name: "Convertir proforma en pedido" });
    expect(within(dialog).getByText("Leyendo la proforma…")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Crear pedido y albarán" })).toBeDisabled();
    await act(async () => { llega(convertPreview()); });
    await within(dialog).findByRole("region", { name: "Resumen del pedido" });
    expect(within(dialog).getByRole("button", { name: "Crear pedido y albarán" })).toBeEnabled();
    expect(mockConvert).not.toHaveBeenCalled();
  });

  it("«Pagado» con cuenta opcional viaja al job (opción B: sin factura)", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    mockConvert.mockResolvedValue({ job_id: "job-c2", status: "queued", codpre: "77" });
    mockStatus.mockResolvedValue({
      status: "finished",
      result: { order_id: "o-2", order_number: "PRO-000077", albaran_error: "no cuadra" },
    });
    const user = userEvent.setup();
    render(<CompanyQuotesPanel {...base()} />);
    const dialog = await abrirConversion(user);
    await user.click(within(dialog).getByLabelText("Pagado"));
    const confirm = within(dialog).getByRole("button", { name: "Crear pedido y albarán" });
    // Fix C: la cuenta es OPCIONAL (apunte del pedido, no cobro en FACTUSOL);
    // se puede confirmar sin ella. Si se indica, viaja en el job.
    expect(confirm).toBeEnabled();
    // El catálogo de cuentas llega de forma asíncrona.
    await within(dialog).findByRole("option", { name: "8 · Streamtec Sabadell" });
    await user.selectOptions(within(dialog).getByLabelText("Cuenta del pago"), "8");
    expect(within(dialog).getByText(/No se emite ninguna factura/)).toBeInTheDocument();
    await user.click(confirm);
    await waitFor(() => expect(mockConvert).toHaveBeenCalledWith("77", expect.objectContaining({
      payment: expect.objectContaining({ paid: true, contrapartida: "8" }),
    }), undefined));
    const notice = await screen.findByText(/PRO-000077/);
    expect(notice).toHaveTextContent("El albarán NO se creó: no cuadra");
    expect(notice).toHaveTextContent("Pago apuntado");
  });

  it("un job fallido muestra el error de FACTUSOL", async () => {
    mockList.mockResolvedValue({ items: [quote()], unlinked: false });
    mockConvert.mockResolvedValue({ job_id: "job-c1", status: "queued", codpre: "77" });
    mockStatus.mockResolvedValue({ status: "failed", error: "CODPRE duplicado" });
    const user = userEvent.setup();
    render(<CompanyQuotesPanel {...base()} />);

    const dialog = await abrirConversion(user);
    await user.click(within(dialog).getByRole("button", { name: "Crear pedido y albarán" }));
    expect(await screen.findByText("CODPRE duplicado")).toBeInTheDocument();
  });
});
