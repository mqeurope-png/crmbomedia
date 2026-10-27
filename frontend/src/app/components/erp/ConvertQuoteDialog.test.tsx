import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConvertQuoteDialog } from "./ConvertQuoteDialog";
import { getQuoteConvertPreview } from "../../lib/erpApi";

/** «Convertir en pedido» una proforma: ANTES del paso de pago enseña el
 *  resumen de lo que se va a crear (empresa, fecha, conceptos, importe y la
 *  proforma de origen), leído del servidor. Nada se confirma hasta pulsar
 *  «Crear pedido y albarán»; si ya se convirtió, enlaza el pedido y no ofrece
 *  crear otro; sin resumen, no se puede confirmar. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../lib/erpApi", () => ({
  getQuoteConvertPreview: jest.fn(),
  // Catálogos del paso de pago (PaymentStep).
  getContrapartidas: jest.fn(() => Promise.resolve([{ codigo: "6", nombre: "Bomedia Sabadell" }])),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([{ codigo: "002", nombre: "Transferencia" }])),
}));

const mockPreview = getQuoteConvertPreview as jest.Mock;

const QUOTE = {
  codpre: "40", referencia: "Kit DTF escuelas", fecha: "2026-10-01", clipre: "55555",
  cliente_nombre: "Acme SL", base: 127, iva: 26.67, total: 153.67,
  tippre: "1", serie: 1, numero: "1-000040",
};

function preview(over = {}) {
  return {
    codpre: "40", serie: 1, company_linked: true, cliente_codigo: "55555",
    already_existed: null,
    company_id: "acme", company_name: "Acme SL", fecha: "2026-10-27",
    lineas: [
      { product_sku: "DTF-60", description: "Impresora DTF 60 cm", quantity: 2, unit_price: 50,
        discount_pct: 0, line_total: 100, tax_rate: 21 },
      { product_sku: "", description: "Instalación y formación", quantity: 1, unit_price: 30,
        discount_pct: 10, line_total: 27, tax_rate: 21 },
    ],
    total: 153.67,
    origen: { doc_type: "presupuestos", label: "proforma", serie: 1, codigo: 40,
              numero: "1-000040", referencia: "Kit DTF escuelas" },
    ...over,
  };
}

function props(over = {}) {
  return { quote: QUOTE, onCancel: jest.fn(), onConfirm: jest.fn(), ...over };
}

beforeEach(() => {
  mockPreview.mockReset();
  mockPreview.mockResolvedValue(preview());
});

describe("ConvertQuoteDialog", () => {
  it("enseña el resumen (empresa, origen, conceptos e importe) antes de confirmar", async () => {
    const p = props();
    render(<ConvertQuoteDialog {...p} />);
    const dialog = screen.getByRole("dialog", { name: "Convertir proforma en pedido" });
    const resumen = await within(dialog).findByRole("region", { name: "Resumen del pedido" });
    expect(mockPreview).toHaveBeenCalledTimes(1);
    expect(mockPreview).toHaveBeenCalledWith("40", 1);

    expect(within(resumen).getByText("Acme SL")).toBeInTheDocument();
    expect(within(resumen).getByText("27/10/2026")).toBeInTheDocument();
    expect(within(resumen).getByText("1-000040").closest("dd")).toHaveTextContent(
      "Proforma 1-000040 · Kit DTF escuelas",
    );
    const filas = within(within(resumen).getByRole("table", { name: "Conceptos del pedido" }))
      .getAllByRole("row").slice(1);
    const celdas = (fila: HTMLElement) =>
      within(fila).getAllByRole("cell").map((td) => td.textContent);
    expect(celdas(filas[0])).toEqual(["DTF-60 · Impresora DTF 60 cm", "2", "50.00 €", "", "100.00 €"]);
    expect(celdas(filas[1])).toEqual(["Instalación y formación", "1", "30.00 €", "10 %", "27.00 €"]);
    expect(within(resumen).getByText(/Importe \(IVA incl\.\)/)).toHaveTextContent(
      "Importe (IVA incl.): 153.67 €",
    );
    // El paso de pago sigue ahí; nada se confirma sin pulsar.
    expect(within(dialog).getByLabelText("Sin pago")).toBeChecked();
    expect(p.onConfirm).not.toHaveBeenCalled();
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("solo «Crear pedido y albarán» confirma, con el pago elegido", async () => {
    const user = userEvent.setup();
    const p = props();
    render(<ConvertQuoteDialog {...p} />);
    await screen.findByRole("region", { name: "Resumen del pedido" });
    await user.click(screen.getByLabelText("Pagado"));
    expect(p.onConfirm).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Crear pedido y albarán" }));
    expect(p.onConfirm).toHaveBeenCalledTimes(1);
    expect(p.onConfirm).toHaveBeenCalledWith(expect.objectContaining({ paid: true }));
    expect(p.onCancel).not.toHaveBeenCalled();
  });

  it("«Cancelar» cierra sin confirmar", async () => {
    const user = userEvent.setup();
    const p = props();
    render(<ConvertQuoteDialog {...p} />);
    await screen.findByRole("region", { name: "Resumen del pedido" });
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(p.onCancel).toHaveBeenCalledTimes(1);
    expect(p.onConfirm).not.toHaveBeenCalled();
  });

  it("si la proforma ya se convirtió, enlaza el pedido y no ofrece «Crear pedido y albarán»", async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValue(preview({
      already_existed: { order_id: "o-40", order_number: "PRO-000040" },
    }));
    const p = props();
    render(<ConvertQuoteDialog {...p} />);
    const aviso = await screen.findByRole("status");
    expect(aviso).toHaveTextContent("Esta proforma ya se convirtió en el pedido");
    expect(within(aviso).getByRole("link", { name: "PRO-000040" }))
      .toHaveAttribute("href", "/erp/orders/o-40");
    expect(screen.queryByRole("button", { name: "Crear pedido y albarán" })).not.toBeInTheDocument();
    // Sin el resumen recalculado: no sería el del pedido que existe.
    expect(screen.queryByRole("region", { name: "Resumen del pedido" })).not.toBeInTheDocument();
    // Sin paso de pago: no hay nada que crear.
    expect(screen.queryByLabelText("Sin pago")).not.toBeInTheDocument();
    // «Cancelar» pasa a «Cerrar» (el ✕ también se llama «Cerrar»: se busca por texto).
    await user.click(screen.getByText("Cerrar", { selector: "button" }));
    expect(p.onCancel).toHaveBeenCalled();
    expect(p.onConfirm).not.toHaveBeenCalled();
  });

  it("si el resumen no se puede leer, se ve el error y no se puede confirmar", async () => {
    mockPreview.mockRejectedValue(new Error("FACTUSOL no responde"));
    const p = props();
    render(<ConvertQuoteDialog {...p} />);
    expect(screen.getByRole("button", { name: "Crear pedido y albarán" })).toBeDisabled();
    expect(await screen.findByRole("alert")).toHaveTextContent("FACTUSOL no responde");
    await waitFor(() => expect(screen.queryByText("Leyendo la proforma…")).not.toBeInTheDocument());
    expect(screen.queryByRole("region", { name: "Resumen del pedido" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Crear pedido y albarán" })).toBeDisabled();
    expect(p.onConfirm).not.toHaveBeenCalled();
  });

  it("cliente FACTUSOL sin empresa en el CRM: lo avisa en el resumen", async () => {
    mockPreview.mockResolvedValue(preview({
      company_linked: false, company_id: null, company_name: null, cliente_codigo: "385",
    }));
    render(<ConvertQuoteDialog {...props()} />);
    const resumen = await screen.findByRole("region", { name: "Resumen del pedido" });
    expect(within(resumen).getByText("Sin empresa del CRM vinculada")).toBeInTheDocument();
    expect(screen.getByText(/El cliente FACTUSOL 385 no tiene empresa en el CRM/)).toBeInTheDocument();
  });

  it("sin serie, la toma de TIPPRE para pedir el resumen (los CODPRE se repiten entre series)", async () => {
    render(<ConvertQuoteDialog {...props({ quote: { ...QUOTE, serie: null, tippre: "2" } })} />);
    await screen.findByRole("region", { name: "Resumen del pedido" });
    expect(mockPreview).toHaveBeenCalledWith("40", 2);
  });
});
