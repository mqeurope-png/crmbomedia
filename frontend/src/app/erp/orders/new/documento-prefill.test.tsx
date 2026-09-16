import { render, waitFor } from "@testing-library/react";
import NewManualOrderPage from "./page";
import { listContacts } from "../../../lib/api";
import { listCompanies } from "../../../lib/companiesApi";
import { previewOrderFromFactusol } from "../../../lib/erpApi";

/** Fase 5 — «Crear pedido» desde el explorador de documentos: la URL trae
 *  ?doc_type=&serie=&codigo= y la pantalla de alta precarga ESE documento con
 *  el flujo de importación de siempre (Fase 1/2), sin teclear serie ni número. */

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), refresh: jest.fn() }),
  useSearchParams: () =>
    new URLSearchParams("doc_type=presupuestos&serie=5&codigo=27"),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) =>
    <a href={href}>{children}</a>,
}));
jest.mock("../../../lib/api", () => ({ listContacts: jest.fn() }));
jest.mock("../../../lib/companiesApi", () => ({
  listCompanies: jest.fn(),
  createCompany: jest.fn(),
  getCompany: jest.fn(),
}));
jest.mock("../../../lib/erpApi", () => ({
  // Lote 7 · P1: el alta manual usa FACTUSOL_SERIES para el selector de serie.
  FACTUSOL_SERIES: [
    { value: 1, label: "Bomedia" }, { value: 2, label: "MQ Europe" },
    { value: 4, label: "Lambert" }, { value: 5, label: "Streamtec" },
  ],
  createOrder: jest.fn(),
  createFactusolCustomer: jest.fn(),
  createFactusolCustomerAndLink: jest.fn(),
  linkFactusolCustomer: jest.fn(),
  searchFactusolCustomers: jest.fn(() => Promise.resolve([])),
  listFactusolQuotes: jest.fn(() => Promise.resolve({ items: [], unlinked: false })),
  getFactusolQuote: jest.fn(),
  searchFactusolArticles: jest.fn(() => Promise.resolve([])),
  previewOrderFromFactusol: jest.fn(),
  searchFactusolQuotes: jest.fn(() => Promise.resolve([])),
  listFactusolDocuments: jest.fn(() => Promise.resolve({ items: [], total: 0 })),
  getContrapartidas: jest.fn(() => Promise.resolve([])),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([])),
}));

const PREVIEW = {
  doc_type: "presupuestos", serie: 5, codigo: 27, numero: "5-000027",
  fecha: "2026-08-01", total: 100, referencia: "REF-27", estado: "1",
  estado_label: "Aceptado", forma_pago: "002", forma_pago_nombre: "Transferencia",
  cliente_codigo: "2458", cliente_nombre: "DUPLICODER", company_id: "es",
  company_name: "Duplicoder SL", company_linked: true,
  lines: [{
    position: 1, codart: "art1", description: "Servicio", quantity: 1,
    unit_price: 100, line_total: 100, discount_pct: 0, iva_pct: 21,
  }],
  order_number: "PRO-000027", external_id: "27", already_imported: null,
};

beforeEach(() => {
  (listCompanies as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  (listContacts as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  (previewOrderFromFactusol as jest.Mock).mockReset();
  (previewOrderFromFactusol as jest.Mock).mockResolvedValue(PREVIEW);
});

it("precarga el presupuesto de la URL en el alta (reutiliza la conversión)", async () => {
  render(<NewManualOrderPage />);
  // El alta importa ESE documento con el flujo de Fase 1 sin teclear nada.
  await waitFor(() =>
    expect(previewOrderFromFactusol).toHaveBeenCalledWith("presupuestos", 5, 27),
  );
});
