import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VincularDocumentoModal } from "./VincularDocumentoModal";
import {
  createFactusolCustomerAndLink,
  linkOrderDocument,
  listFactusolDocuments,
  previewLinkOrderDocument,
  searchFactusolCustomers,
} from "../../lib/erpApi";

jest.mock("../../lib/api", () => ({
  ApiError: class ApiError extends Error {
    code?: string;
  },
}));
jest.mock("../../lib/companiesApi", () => ({
  listCompanies: jest.fn(() => Promise.resolve({ items: [], total: 0 })),
}));
jest.mock("../../lib/erpApi", () => ({
  listFactusolDocuments: jest.fn(),
  previewLinkOrderDocument: jest.fn(),
  linkOrderDocument: jest.fn(),
  searchFactusolCustomers: jest.fn(),
  createFactusolCustomerAndLink: jest.fn(),
  linkFactusolCustomer: jest.fn(),
}));

const FAC_ROW = {
  doc_type: "facturas", serie: 2, codigo: 526110, numero: "2-526110",
  cliente_codigo: "7001", cliente_nombre: "PREMO B.V.", fecha: "2026-09-20",
  total: 5059, estado: "0", estado_label: "Pendiente", referencia: "PREMO", forma_pago: "011",
};
const PREVIEW = {
  doc_type: "facturas", serie: 2, codigo: 526110, numero: "2-526110",
  fecha: "2026-09-20", total: 5059, referencia: "PREMO", forma_pago: "011",
  forma_pago_nombre: "Recibo domiciliado", cliente_codigo: "7001",
  cliente_nombre: "PREMO B.V.", company_id: "premo", company_name: "PREMO B.V.",
  company_linked: true, is_sample: true, linked_elsewhere: null,
  lines: [
    { codart: "MBO", description: "Impresora UV", quantity: 1, unit_price: 4000,
      discount_pct: 0, iva_pct: 21 },
    { codart: null, description: "Portes", quantity: 1, unit_price: 181,
      discount_pct: 0, iva_pct: 21 },
  ],
};

beforeEach(() => {
  [listFactusolDocuments, previewLinkOrderDocument, linkOrderDocument,
    searchFactusolCustomers, createFactusolCustomerAndLink]
    .forEach((m) => (m as jest.Mock).mockReset());
  (listFactusolDocuments as jest.Mock).mockResolvedValue({ items: [FAC_ROW], total: 1 });
});

describe("VincularDocumentoModal", () => {
  it("busca la factura, enseña lo que se cargará y la vincula con confirmación", async () => {
    (previewLinkOrderDocument as jest.Mock).mockResolvedValue(PREVIEW);
    (linkOrderDocument as jest.Mock).mockResolvedValue({ id: "o-3" });
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<VincularDocumentoModal orderId="o-3" orderNumber="MUESTRA-000003"
                                   onClose={jest.fn()} onDone={onDone} />);
    await user.type(screen.getByLabelText("Buscar documento FACTUSOL"), "526110");
    await waitFor(() => expect(listFactusolDocuments).toHaveBeenLastCalledWith(
      "facturas", { q: "526110", limit: 15 },
    ));
    await user.click(await screen.findByRole("button", { name: "Elegir factura 2-526110" }));
    const datos = await screen.findByLabelText("Datos que se cargarán");
    expect(datos).toHaveTextContent("PREMO B.V.");
    expect(datos).toHaveTextContent("Recibo domiciliado");
    expect(within(datos).getByRole("list", { name: "Líneas del documento" }))
      .toHaveTextContent("Impresora UV");
    expect(datos).toHaveTextContent("Base4181,00 €");    // es-ES: sin separador en 4 cifras
    expect(datos).toHaveTextContent("Total5059,00 €");
    const vincular = screen.getByRole("button", { name: "Vincular y cargar datos" });
    expect(vincular).toBeDisabled();
    await user.click(screen.getByLabelText("Confirmo cargar los datos del documento"));
    await user.click(vincular);
    await waitFor(() => expect(linkOrderDocument).toHaveBeenCalledWith(
      "o-3", { doc_type: "facturas", serie: 2, codigo: 526110 },
    ));
    expect(onDone).toHaveBeenCalledWith({ id: "o-3" });
  });

  it("cliente FACTUSOL sin empresa CRM: ofrece crearla con sus datos y luego vincula", async () => {
    (previewLinkOrderDocument as jest.Mock)
      .mockResolvedValueOnce({ ...PREVIEW, company_id: null, company_name: null, company_linked: false })
      .mockResolvedValueOnce(PREVIEW);
    (searchFactusolCustomers as jest.Mock).mockResolvedValue([{
      codcli: "7001", nombre: "PREMO B.V.", nif: "NL123", domcli: "Keizersgracht 1",
      pobcli: "Amsterdam", cpocli: "1015", procli: "", paicli: "528", emacli: null, telcli: null,
    }]);
    (createFactusolCustomerAndLink as jest.Mock).mockResolvedValue({ company_id: "premo" });
    const user = userEvent.setup();
    render(<VincularDocumentoModal orderId="o-3" orderNumber="MUESTRA-000003"
                                   initial={{ doc_type: "facturas", serie: 2, codigo: 526110 }}
                                   onClose={jest.fn()} />);
    const panel = await screen.findByRole("region", { name: "Vincular empresa CRM" });
    expect(panel).toHaveTextContent("no tiene empresa en el CRM");
    expect(screen.getByRole("button", { name: "Vincular y cargar datos" })).toBeDisabled();
    await user.click(within(panel).getByRole("button", {
      name: "Crear empresa CRM con estos datos y vincular",
    }));
    await waitFor(() => expect(createFactusolCustomerAndLink).toHaveBeenCalledWith({
      factusol_codcli: "7001",
      factusol_customer_data: expect.objectContaining({ nombre: "PREMO B.V.", nif: "NL123", pais: "528" }),
    }));
    expect(await screen.findByRole("status")).toHaveTextContent("creada y vinculada");
    await waitFor(() => expect(previewLinkOrderDocument).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("region", { name: "Vincular empresa CRM" })).toBeNull();
    expect(screen.getByLabelText("Confirmo cargar los datos del documento")).toBeInTheDocument();
  });

  it("documento ya vinculado a otro pedido: lo dice y no deja vincular", async () => {
    (previewLinkOrderDocument as jest.Mock).mockResolvedValue({
      ...PREVIEW, linked_elsewhere: { order_id: "o-9", order_number: "MANUAL-000009" },
    });
    render(<VincularDocumentoModal orderId="o-3" orderNumber="MUESTRA-000003"
                                   initial={{ doc_type: "facturas", serie: 2, codigo: 526110 }}
                                   onClose={jest.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("ya está vinculado al pedido MANUAL-000009");
    expect(screen.getByRole("button", { name: "Vincular y cargar datos" })).toBeDisabled();
    expect(linkOrderDocument).not.toHaveBeenCalled();
  });
});
