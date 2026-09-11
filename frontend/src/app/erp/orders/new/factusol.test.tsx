import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewManualOrderPage from "./page";
import { listContacts } from "../../../lib/api";
import { getCompany, listCompanies } from "../../../lib/companiesApi";
import {
  createOrder,
  listFactusolQuotes,
  previewOrderFromFactusol,
  searchFactusolArticles,
  searchFactusolCustomers,
} from "../../../lib/erpApi";

const push = jest.fn();
const refresh = jest.fn();
let search = "";
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
  useSearchParams: () => new URLSearchParams(search),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../../lib/api", () => ({ listContacts: jest.fn() }));
jest.mock("../../../lib/companiesApi", () => ({
  listCompanies: jest.fn(),
  createCompany: jest.fn(),
  getCompany: jest.fn(),
}));
jest.mock("../../../lib/erpApi", () => ({
  createOrder: jest.fn(),
  createFactusolCustomer: jest.fn(),
  createFactusolCustomerAndLink: jest.fn(),
  linkFactusolCustomer: jest.fn(),
  searchFactusolCustomers: jest.fn(),
  listFactusolQuotes: jest.fn(),
  getFactusolQuote: jest.fn(),
  searchFactusolArticles: jest.fn(),
  previewOrderFromFactusol: jest.fn(),
  searchFactusolQuotes: jest.fn(() => Promise.resolve([])),
  listFactusolDocuments: jest.fn(() => Promise.resolve({ items: [], total: 0 })),
  // Fase 2: catálogos del paso de pago (PaymentStep), inertes.
  getContrapartidas: jest.fn(() => Promise.resolve([])),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([])),
}));

const COMPANY = {
  id: "c1", name: "Acme SL", tax_id: "B12345678",
  address_line: "C Mayor 1", city: "Madrid", postal_code: "28001",
  state: "Madrid", country: "España", factusol_company_id: "55555",
};

function preview(over = {}) {
  return {
    doc_type: "pedidos", serie: 5, codigo: 123, numero: "5-000123",
    fecha: "2026-09-02", total: 60.5, referencia: "Pedido tienda", estado: "0",
    estado_label: "Pendiente", forma_pago: "011", forma_pago_nombre: "Recibo domiciliado",
    cliente_codigo: "55555", cliente_nombre: "Acme SL",
    company_id: "c1", company_name: "Acme SL", company_linked: true,
    lines: [
      { position: 1, codart: "CDR80WPT", description: "CD TQ 700 MB", quantity: 100,
        unit_price: 0.5, line_total: 50, discount_pct: 0, iva_pct: null },
      { position: 2, codart: null, description: "Portes", quantity: 1,
        unit_price: 10.5, line_total: 10.5, discount_pct: 0, iva_pct: null },
    ],
    order_number: "PCL-5-000123", external_id: "5-000123", already_imported: null,
    ...over,
  };
}

beforeEach(() => {
  search = "";
  push.mockReset();
  (listCompanies as jest.Mock).mockReset();
  (listCompanies as jest.Mock).mockResolvedValue({ items: [COMPANY], total: 1 });
  (listContacts as jest.Mock).mockReset();
  (listContacts as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  (getCompany as jest.Mock).mockReset();
  (getCompany as jest.Mock).mockResolvedValue(COMPANY);
  (createOrder as jest.Mock).mockReset();
  (createOrder as jest.Mock).mockResolvedValue({ id: "new-order-1" });
  (listFactusolQuotes as jest.Mock).mockReset();
  (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: [], unlinked: false });
  (searchFactusolArticles as jest.Mock).mockReset();
  (searchFactusolArticles as jest.Mock).mockResolvedValue([]);
  (searchFactusolCustomers as jest.Mock).mockReset();
  (searchFactusolCustomers as jest.Mock).mockResolvedValue([]);
  (previewOrderFromFactusol as jest.Mock).mockReset();
  (previewOrderFromFactusol as jest.Mock).mockResolvedValue(preview());
});

describe("Fase 1 · alta de pedido desde FACTUSOL y desde la ficha de empresa", () => {
  it("test_nuevo_pedido_desde_ficha_empresa_precarga_empresa: ?company_id= precarga la empresa", async () => {
    search = "company_id=c1";
    render(<NewManualOrderPage />);
    await waitFor(() => expect(getCompany).toHaveBeenCalledWith("c1"));
    expect(await screen.findByDisplayValue("Acme SL")).toBeInTheDocument();
    // NIF y dirección de envío heredados de la empresa.
    expect(screen.getByDisplayValue("B12345678")).toBeInTheDocument();
    expect(screen.getByLabelText("Dirección de envío")).toHaveValue("C Mayor 1");
  });

  it("importa un pedido de cliente de FACTUSOL: cliente, líneas, fecha y origen en el alta", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await user.selectOptions(screen.getByLabelText("Tipo de documento FACTUSOL"), "pedidos");
    await user.clear(screen.getByLabelText("Serie del documento FACTUSOL"));
    await user.type(screen.getByLabelText("Serie del documento FACTUSOL"), "5");
    await user.type(screen.getByLabelText("Número del documento FACTUSOL"), "123");
    await user.click(screen.getByRole("button", { name: "Cargar documento" }));
    await waitFor(() =>
      expect(previewOrderFromFactusol).toHaveBeenCalledWith("pedidos", 5, 123),
    );
    // Aviso con lo cargado (líneas, importe, forma de pago y cliente).
    const status = await screen.findByText(/pedido de cliente 5-000123 cargado/);
    expect(status).toHaveTextContent("2 línea(s)");
    expect(status).toHaveTextContent("Recibo domiciliado");
    expect(status).toHaveTextContent("cliente «Acme SL»");
    // Líneas volcadas (sustituyen a la fila vacía), fecha y empresa.
    expect(screen.getByLabelText("SKU línea 1")).toHaveValue("CDR80WPT");
    expect(screen.getByLabelText("Descripción línea 2")).toHaveValue("Portes");
    expect(screen.queryByLabelText("SKU línea 3")).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("2026-09-02")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Acme SL")).toBeInTheDocument();
    expect(screen.getByText(/Origen:/)).toHaveTextContent("pedido de cliente FACTUSOL 5-000123");
    expect(screen.getByText(/Origen:/)).toHaveTextContent("PCL-5-000123");
    // Recogida en tienda → el formulario es válido; al crear viaja el origen.
    await user.click(screen.getByLabelText("Recogida en tienda"));
    await user.click(screen.getByRole("button", { name: "Crear pedido" }));
    await waitFor(() => expect(createOrder).toHaveBeenCalled());
    const payload = (createOrder as jest.Mock).mock.calls[0][0];
    expect(payload.company_id).toBe("c1");
    expect(payload.factusol_source).toEqual({
      doc_type: "pedidos", serie: 5, codigo: 123, referencia: "Pedido tienda",
      forma_pago: "011", forma_pago_nombre: "Recibo domiciliado",
    });
    expect(payload.lines).toEqual([
      { product_sku: "CDR80WPT", description: "CD TQ 700 MB", quantity: 100, unit_price: 0.5 },
      { product_sku: "", description: "Portes", quantity: 1, unit_price: 10.5 },
    ]);
    expect(push).toHaveBeenCalledWith("/erp/orders/new-order-1");
  });

  it("cliente FACTUSOL sin vincular: avisa y deja elegir la empresa a mano", async () => {
    (previewOrderFromFactusol as jest.Mock).mockResolvedValue(preview({
      doc_type: "presupuestos", serie: 1, codigo: 575, numero: "1-000575",
      cliente_codigo: "99999", cliente_nombre: "Nuevo Cliente",
      company_id: null, company_name: null, company_linked: false,
      order_number: "PRO-000575",
    }));
    const { searchFactusolQuotes } = jest.requireMock("../../../lib/erpApi");
    (searchFactusolQuotes as jest.Mock).mockResolvedValue([{
      codpre: "575", referencia: "Tinta", fecha: "2026-09-01", clipre: "99999",
      cliente_nombre: "Nuevo Cliente", base: 100, iva: 21, total: 121,
    }]);
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    // Proformas: se busca (nº, referencia o cliente) y se elige, sin serie+número.
    await user.type(screen.getByLabelText("Buscar proforma"), "575");
    await user.click(await screen.findByRole("button", { name: "Cargar en el pedido" }));
    await waitFor(() =>
      expect(previewOrderFromFactusol).toHaveBeenCalledWith("presupuestos", 1, 575),
    );
    const status = await screen.findByText(/presupuesto 1-000575 cargado/);
    expect(status).toHaveTextContent("«Nuevo Cliente» (nº 99999) sin vincular");
    // Sin empresa el alta sigue deshabilitada hasta que Bart la elija.
    expect(screen.getByRole("button", { name: "Crear pedido" })).toBeDisabled();
  });

  it("documento ya importado: lo avisa, enlaza al pedido y no deja crear otro", async () => {
    (previewOrderFromFactusol as jest.Mock).mockResolvedValue(preview({
      already_imported: { order_id: "o-9", order_number: "PCL-5-000123" },
    }));
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await user.selectOptions(screen.getByLabelText("Tipo de documento FACTUSOL"), "pedidos");
    await user.type(screen.getByLabelText("Número del documento FACTUSOL"), "123");
    await user.click(screen.getByRole("button", { name: "Cargar documento" }));
    expect(await screen.findByText(/ya se importó como el pedido PCL-5-000123/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Abrir el pedido PCL-5-000123/ }))
      .toHaveAttribute("href", "/erp/orders/o-9");
    await user.click(screen.getByLabelText("Recogida en tienda"));
    expect(screen.getByRole("button", { name: "Crear pedido" })).toBeDisabled();
  });
});
