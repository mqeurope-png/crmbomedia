import { render, screen } from "@testing-library/react";
import CompanyDetailPage from "./page";
import { getCompany, listCompanyContacts } from "../../lib/companiesApi";

jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "c1" }),
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div><h1>{title}</h1>{actions}</div>
  ),
}));
jest.mock("../../lib/companiesApi", () => ({
  getCompany: jest.fn(),
  listCompanyContacts: jest.fn(),
  listCompanies: jest.fn(() => Promise.resolve({ items: [], total: 0 })),
  mergeCompanies: jest.fn(),
  updateCompany: jest.fn(),
  deleteCompany: jest.fn(),
}));
jest.mock("../../lib/dates", () => ({ formatBackendDateTime: () => "—" }));
jest.mock("../../components/erp/CompanyFactusolPanel", () => ({
  CompanyFactusolPanel: () => null,
}));
jest.mock("../../components/erp/CompanyQuotesPanel", () => ({
  CompanyQuotesPanel: () => null,
}));

const COMPANY = {
  id: "c1", name: "Acme SL", website: null, domain: null, tax_id: "B12345678",
  vat: null, country: "España", region: null, state: "Madrid", city: "Madrid",
  address_line: "C Mayor 1", postal_code: "28001", sector: null, size_category: null,
  notes: null, source: null, factusol_company_id: "55555",
  created_at: "2026-01-01T00:00:00", updated_at: "2026-01-01T00:00:00",
};

beforeEach(() => {
  (getCompany as jest.Mock).mockReset();
  (getCompany as jest.Mock).mockResolvedValue(COMPANY);
  (listCompanyContacts as jest.Mock).mockReset();
  (listCompanyContacts as jest.Mock).mockResolvedValue([]);
});

describe("Ficha de empresa — «+ Nuevo pedido» (Fase 1)", () => {
  it("enlaza al alta de pedido con la empresa precargada", async () => {
    render(<CompanyDetailPage />);
    await screen.findByRole("heading", { name: "Acme SL" });
    const link = screen.getByRole("link", { name: "+ Nuevo pedido" });
    expect(link).toHaveAttribute("href", "/erp/orders/new?company_id=c1");
  });
});
