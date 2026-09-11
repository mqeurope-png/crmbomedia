import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyFactusolPanel } from "./CompanyFactusolPanel";
import {
  getFactusolPullPreview,
  pullFactusolIntoCompany,
  searchFactusolCustomers,
} from "../../lib/erpApi";

jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  createFactusolCustomer: jest.fn(),
  linkFactusolCustomer: jest.fn(),
  searchFactusolCustomers: jest.fn(),
  getFactusolPullPreview: jest.fn(),
  pullFactusolIntoCompany: jest.fn(),
}));

const COMPANY = {
  id: "acme", name: "Acme Viejo SL", website: null, domain: null, tax_id: "B00000000",
  vat: null, country: "FR", region: null, state: "Lleida", city: "Lleida",
  address_line: "Calle Vieja 9", postal_code: "25001", sector: null, size_category: null,
  notes: null, source: "manual", is_active: true, factusol_company_id: "55555",
  external_references: {}, custom_fields: {}, contacts_count: 0,
  created_at: "2026-01-01T00:00:00", updated_at: "2026-01-01T00:00:00",
};

const CUSTOMER = {
  codcli: "55555", nombre: "Acme", nif: "B12345678", nofcli: "ACME SL", noccli: "Acme",
  nifcli: "B12345678", domcli: "C/ Mayor 1", pobcli: "Madrid", cpocli: "28001",
  procli: "Madrid", paicli: "724", pais_iso2: "ES", emacli: null, telcli: null,
  crm_link: { type: "company" as const, id: "acme", name: "Acme Viejo SL" },
  factusol_matches_crm_id: "acme",
};

const CHANGES = [
  { field: "nombre", label: "Nombre", crm: "Acme Viejo SL", factusol: "ACME SL" },
  { field: "nif", label: "NIF", crm: "B00000000", factusol: "B12345678" },
  { field: "pais", label: "País", crm: "FR", factusol: "ES" },
];

beforeEach(() => {
  (searchFactusolCustomers as jest.Mock).mockReset();
  (searchFactusolCustomers as jest.Mock).mockResolvedValue([CUSTOMER]);
  (getFactusolPullPreview as jest.Mock).mockReset();
  (getFactusolPullPreview as jest.Mock).mockResolvedValue({
    company_id: "acme", codcli: "55555", customer: CUSTOMER, changes: CHANGES,
  });
  (pullFactusolIntoCompany as jest.Mock).mockReset();
  (pullFactusolIntoCompany as jest.Mock).mockResolvedValue({
    ok: true, company_id: "acme", codcli: "55555", changes: CHANGES, applied: 3,
  });
});

describe("CompanyFactusolPanel — «Traer datos de FACTUSOL»", () => {
  it("lee el cliente por CÓDIGO, enseña las diferencias y el botón junto a «Ver diferencias»", async () => {
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={COMPANY} />);
    await waitFor(() =>
      expect(searchFactusolCustomers).toHaveBeenCalledWith("55555", "codcli"),
    );
    expect(await screen.findByText(/Los datos difieren de FACTUSOL/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ver diferencias" }));
    expect(screen.getByText("C/ Mayor 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Traer datos de FACTUSOL" })).toBeInTheDocument();
  });

  it("pide confirmación mostrando qué cambia y solo entonces sobrescribe (con historial en el backend)", async () => {
    const onPulled = jest.fn();
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={COMPANY} onPulled={onPulled} />);
    await user.click(await screen.findByRole("button", { name: "Traer datos de FACTUSOL" }));
    await waitFor(() => expect(getFactusolPullPreview).toHaveBeenCalledWith("acme"));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByRole("alert")).toHaveTextContent(/sobrescribirá los datos de la empresa con los de FACTUSOL \(cliente nº 55555\)/);
    expect(dialog.getByText("ACME SL")).toBeInTheDocument();
    expect(dialog.getByText("B12345678")).toBeInTheDocument();
    // Hasta confirmar no se escribe nada.
    expect(pullFactusolIntoCompany).not.toHaveBeenCalled();
    await user.click(dialog.getByRole("button", { name: "Sobrescribir con FACTUSOL (3)" }));
    await waitFor(() => expect(pullFactusolIntoCompany).toHaveBeenCalledWith("acme"));
    expect(await screen.findByText(
      /Datos traídos de FACTUSOL cliente nº 55555: 3 campo\(s\) actualizado\(s\)/,
    )).toBeInTheDocument();
    expect(onPulled).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancelar no escribe nada", async () => {
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={COMPANY} />);
    await user.click(await screen.findByRole("button", { name: "Traer datos de FACTUSOL" }));
    const dialog = within(await screen.findByRole("dialog"));
    await user.click(dialog.getByRole("button", { name: "Cancelar" }));
    expect(pullFactusolIntoCompany).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sin permiso de edición no hay botón «Traer datos»", async () => {
    const { getCurrentUser } = jest.requireMock("../../lib/api");
    (getCurrentUser as jest.Mock).mockResolvedValueOnce({ role: "user" });
    render(<CompanyFactusolPanel company={COMPANY} />);
    expect(await screen.findByText(/Los datos difieren de FACTUSOL/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Traer datos de FACTUSOL" })).not.toBeInTheDocument();
  });
});
