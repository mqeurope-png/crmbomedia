import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewManualOrderPage from "./page";
import { listContacts } from "../../../lib/api";
import { getCompany, listCompanies } from "../../../lib/companiesApi";
import {
  createOrder,
  getContrapartidas,
  listFactusolQuotes,
  previewOrderFromFactusol,
  searchFactusolArticles,
  searchFactusolCustomers,
} from "../../../lib/erpApi";

const push = jest.fn();
const refresh = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
  useSearchParams: () => new URLSearchParams(""),
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
  getContrapartidas: jest.fn(),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([
    { codigo: "002", nombre: "Transferencia" }, { codigo: "011", nombre: "Recibo domiciliado" },
  ])),
}));

const COMPANY = {
  id: "c1", name: "Acme SL", tax_id: "B12345678",
  address_line: "C Mayor 1", city: "Madrid", postal_code: "28001",
  state: "Madrid", country: "España", factusol_company_id: "55555",
};

function preview(over = {}) {
  return {
    doc_type: "pedidos", serie: 5, codigo: 123, numero: "5-000123",
    fecha: "2026-09-02", total: 60.5, referencia: "Encargo taller", estado: "0",
    estado_label: "Pendiente", forma_pago: "011", forma_pago_nombre: "Recibo domiciliado",
    cliente_codigo: "55555", cliente_nombre: "Acme SL",
    company_id: "c1", company_name: "Acme SL", company_linked: true,
    lines: [
      { position: 1, codart: "CDR80WPT", description: "CD TQ 700 MB", quantity: 100,
        unit_price: 0.5, line_total: 50, discount_pct: 0, iva_pct: null },
    ],
    order_number: "PCL-5-000123", external_id: "5-000123", already_imported: null,
    ...over,
  };
}

beforeEach(() => {
  push.mockReset();
  (listCompanies as jest.Mock).mockReset();
  (listCompanies as jest.Mock).mockResolvedValue({ items: [COMPANY], total: 1 });
  (listContacts as jest.Mock).mockReset();
  (listContacts as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  (getCompany as jest.Mock).mockReset();
  (getCompany as jest.Mock).mockResolvedValue(COMPANY);
  (createOrder as jest.Mock).mockReset();
  (createOrder as jest.Mock).mockResolvedValue({ id: "new-order-1", albaran_job_id: "job-7" });
  (listFactusolQuotes as jest.Mock).mockReset();
  (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: [], unlinked: false });
  (searchFactusolArticles as jest.Mock).mockReset();
  (searchFactusolArticles as jest.Mock).mockResolvedValue([]);
  (searchFactusolCustomers as jest.Mock).mockReset();
  (searchFactusolCustomers as jest.Mock).mockResolvedValue([]);
  (previewOrderFromFactusol as jest.Mock).mockReset();
  (previewOrderFromFactusol as jest.Mock).mockResolvedValue(preview());
  (getContrapartidas as jest.Mock).mockReset();
  (getContrapartidas as jest.Mock).mockResolvedValue([
    { codigo: "6", nombre: "Bomedia Sabadell" }, { codigo: "8", nombre: "Streamtec Sabadell" },
  ]);
});

async function loadPedido(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(screen.getByLabelText("Tipo de documento FACTUSOL"), "pedidos");
  await user.clear(screen.getByLabelText("Serie del documento FACTUSOL"));
  await user.type(screen.getByLabelText("Serie del documento FACTUSOL"), "5");
  await user.type(screen.getByLabelText("Número del documento FACTUSOL"), "123");
  await user.click(screen.getByRole("button", { name: "Cargar documento" }));
  await waitFor(() => expect(previewOrderFromFactusol).toHaveBeenCalledWith("pedidos", 5, 123));
}

describe("Fase 2 · alta desde FACTUSOL: paso de pago y albarán", () => {
  it("sin documento no hay paso de pago; al cargarlo aparece con la forma de pago del documento", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    expect(screen.queryByLabelText("Sin pago")).not.toBeInTheDocument();
    await loadPedido(user);
    expect(await screen.findByText(/se creará su/)).toHaveTextContent("albarán en FACTUSOL");
    expect(screen.getByLabelText("Sin pago")).toBeChecked();
    expect(screen.getByLabelText("Forma de pago")).toHaveValue("011");
  });

  it("test_convertir_pagado_B: «Pagado» con cuenta viaja en el alta y la ficha recibe el job del albarán", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await loadPedido(user);
    await user.click(screen.getByLabelText("Pagado"));
    // Sin cuenta el formulario no se puede enviar.
    await user.click(screen.getByLabelText("Recogida en tienda"));
    expect(screen.getByRole("button", { name: "Crear pedido" })).toBeDisabled();
    await screen.findByRole("option", { name: "8 · Streamtec Sabadell" });
    await user.selectOptions(screen.getByLabelText("Cuenta del cobro"), "8");
    await user.click(screen.getByRole("button", { name: "Crear pedido" }));
    await waitFor(() => expect(createOrder).toHaveBeenCalled());
    const payload = (createOrder as jest.Mock).mock.calls[0][0];
    expect(payload.create_albaran).toBe(true);
    expect(payload.payment).toEqual(expect.objectContaining({
      paid: true, contrapartida: "8", forma_pago: "011", forma_pago_nombre: "Recibo domiciliado",
    }));
    expect(payload.payment.fecha).toBe(new Date().toISOString().slice(0, 10));
    expect(payload.factusol_source.doc_type).toBe("pedidos");
    expect(push).toHaveBeenCalledWith("/erp/orders/new-order-1?albaran_job=job-7");
  });

  it("test_convertir_sin_pago: por defecto viaja «sin pago» con la forma del documento", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await loadPedido(user);
    await user.click(screen.getByLabelText("Recogida en tienda"));
    await user.click(screen.getByRole("button", { name: "Crear pedido" }));
    await waitFor(() => expect(createOrder).toHaveBeenCalled());
    const payload = (createOrder as jest.Mock).mock.calls[0][0];
    expect(payload.payment).toEqual(expect.objectContaining({
      paid: false, contrapartida: null, forma_pago: "011",
    }));
    expect(payload.create_albaran).toBe(true);
  });

  it("un pedido manual sin documento FACTUSOL no lleva pago ni albarán", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(listCompanies).toHaveBeenCalled());
    // Datalist: la empresa se elige tecleando su nombre exacto.
    await user.type(screen.getByLabelText("Empresa"), "Acme SL");
    await user.type(screen.getByLabelText("SKU línea 1"), "REP-1");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Reparación");
    await user.type(screen.getByLabelText("Precio línea 1"), "40");
    await user.click(screen.getByLabelText("Recogida en tienda"));
    const submit = screen.getByRole("button", { name: "Crear pedido" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(createOrder).toHaveBeenCalled());
    const payload = (createOrder as jest.Mock).mock.calls[0][0];
    expect(payload.payment).toBeUndefined();
    expect(payload.create_albaran).toBeUndefined();
    expect(payload.factusol_source).toBeUndefined();
  });
});
