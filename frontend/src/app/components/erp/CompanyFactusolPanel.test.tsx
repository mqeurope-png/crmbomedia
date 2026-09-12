import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyFactusolPanel } from "./CompanyFactusolPanel";
import {
  createFactusolCustomer,
  fixFactusolCustomerRegime,
  getFactusolPullPreview,
  getFactusolRegimePreview,
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
  getFactusolRegimePreview: jest.fn(),
  fixFactusolCustomerRegime: jest.fn(),
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

/** Tarea C: ficha belga con NIF-IVA que FACTUSOL tiene como nacional (0/0/1). */
const REGIME_PREVIEW = {
  company_id: "acme", codcli: "55555", company_country: "BE", company_vat: "BE0812240188",
  country_iso2: "BE", regime: "intracomunitario" as const,
  regime_label: "Intracomunitario (exento)",
  reason: "BE (UE) con NIF-IVA BE0812240188 → intracomunitario",
  current: { IFICLI: 0, IVACLI: 0, TIVCLI: 1, PAICLI: "056", regime: "nacional" as const,
             regime_label: "Nacional (con IVA)" },
  proposed: { IFICLI: 2, IVACLI: 2, TIVCLI: 4, PAICLI: "056" },
  changes: [
    { column: "IFICLI", label: "Tipo de documento", current: 0, current_label: "0 · N.I.F.",
      proposed: 2, proposed_label: "2 · NIF/IVA operador intracomunitario" },
    { column: "IVACLI", label: "Aplicar IVA", current: 0, current_label: "0 · Sí (nacional)",
      proposed: 2, proposed_label: "2 · Intracomunitario" },
    { column: "TIVCLI", label: "Tipo impositivo", current: 1, current_label: "1 · 21 %",
      proposed: 4, proposed_label: "4 · Exento" },
  ],
  coherent: false,
};

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
  (getFactusolRegimePreview as jest.Mock).mockReset();
  (getFactusolRegimePreview as jest.Mock).mockResolvedValue(REGIME_PREVIEW);
  (fixFactusolCustomerRegime as jest.Mock).mockReset();
  (fixFactusolCustomerRegime as jest.Mock).mockResolvedValue({
    ...REGIME_PREVIEW, ok: true, changed: true,
    written: { IFICLI: 2, IVACLI: 2, TIVCLI: 4 },
  });
  (createFactusolCustomer as jest.Mock).mockReset();
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
    expect(screen.queryByRole("button", { name: "Comprobar régimen de IVA" })).not.toBeInTheDocument();
  });
});

describe("CompanyFactusolPanel — «Régimen de IVA en FACTUSOL» (Tarea C)", () => {
  it("enseña el régimen por país + NIF-IVA frente a la ficha F_CLI y solo corrige tras confirmar", async () => {
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={{ ...COMPANY, country: "BE", vat: "BE0812240188" }} />);
    await user.click(await screen.findByRole("button", { name: "Comprobar régimen de IVA" }));
    await waitFor(() => expect(getFactusolRegimePreview).toHaveBeenCalledWith("acme"));
    const dialog = within(await screen.findByRole("dialog", { name: "Régimen de IVA en FACTUSOL" }));
    expect(dialog.getByText("Intracomunitario (exento)")).toBeInTheDocument();
    expect(dialog.getByText(/BE \(UE\) con NIF-IVA BE0812240188/)).toBeInTheDocument();
    expect(dialog.getByText(/Ficha F_CLI nº 55555 ahora: Nacional \(con IVA\)/)).toBeInTheDocument();
    expect(dialog.getByRole("alert")).toHaveTextContent(/SOLO las columnas de abajo/);
    expect(dialog.getByText("2 · NIF/IVA operador intracomunitario")).toBeInTheDocument();
    expect(dialog.getByText("4 · Exento")).toBeInTheDocument();
    // Hasta confirmar no se escribe nada.
    expect(fixFactusolCustomerRegime).not.toHaveBeenCalled();
    await user.click(dialog.getByRole("button", { name: "Corregir en FACTUSOL (3)" }));
    await waitFor(() => expect(fixFactusolCustomerRegime).toHaveBeenCalledWith("acme"));
    expect(await screen.findByText(
      /Régimen corregido en FACTUSOL cliente nº 55555: Intracomunitario \(exento\) \(IFICLI, IVACLI, TIVCLI\)/,
    )).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancelar no escribe; una ficha coherente no ofrece corregir", async () => {
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={COMPANY} />);
    await user.click(await screen.findByRole("button", { name: "Comprobar régimen de IVA" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Régimen de IVA en FACTUSOL" }));
    await user.click(dialog.getByRole("button", { name: "Cancelar" }));
    expect(fixFactusolCustomerRegime).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    (getFactusolRegimePreview as jest.Mock).mockResolvedValueOnce({
      ...REGIME_PREVIEW, changes: [], coherent: true,
      current: { ...REGIME_PREVIEW.current, IFICLI: 2, IVACLI: 2, TIVCLI: 4,
                 regime: "intracomunitario", regime_label: "Intracomunitario (exento)" },
    });
    await user.click(screen.getByRole("button", { name: "Comprobar régimen de IVA" }));
    const ok = within(await screen.findByRole("dialog", { name: "Régimen de IVA en FACTUSOL" }));
    expect(ok.getByRole("status")).toHaveTextContent(/ya está bien: nada que corregir/);
    expect(ok.queryByRole("button", { name: /Corregir en FACTUSOL/ })).not.toBeInTheDocument();
    await user.click(ok.getByRole("button", { name: "Cerrar" }));
    expect(fixFactusolCustomerRegime).not.toHaveBeenCalled();
  });

  it("«Crear en FACTUSOL» manda el país y el NIF-IVA de la empresa y enseña el régimen", async () => {
    (searchFactusolCustomers as jest.Mock).mockResolvedValue([]);
    (createFactusolCustomer as jest.Mock).mockResolvedValue({
      factusol_codcli: "4600", created: true, regime: "intracomunitario",
      regime_label: "Intracomunitario (exento)",
    });
    const user = userEvent.setup();
    render(<CompanyFactusolPanel
      company={{ ...COMPANY, factusol_company_id: null, country: "BE", vat: "BE0812240188" }}
    />);
    await user.click(await screen.findByRole("button", { name: "Crear en FACTUSOL" }));
    await waitFor(() => expect(createFactusolCustomer).toHaveBeenCalledWith(
      expect.objectContaining({ crm_id: "acme", pais: "BE", vat: "BE0812240188" }),
    ));
    expect(await screen.findByText(
      /Creado en FACTUSOL con el nº 4600 \(régimen de IVA: Intracomunitario \(exento\)\)/,
    )).toBeInTheDocument();
  });
});
