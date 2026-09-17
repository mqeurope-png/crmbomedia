import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyFactusolPanel } from "./CompanyFactusolPanel";
import { mergeCompanies } from "../../lib/companiesApi";
import {
  alreadyLinkedHolder,
  createFactusolCustomer,
  fixFactusolCustomerRegime,
  getFactusolPullPreview,
  getFactusolRegimePreview,
  linkFactusolCustomer,
  pullFactusolIntoCompany,
  searchFactusolCustomers,
} from "../../lib/erpApi";

jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/companiesApi", () => ({
  mergeCompanies: jest.fn(),
}));
jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  alreadyLinkedHolder: jest.fn(() => null),
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
  (linkFactusolCustomer as jest.Mock).mockReset();
  (linkFactusolCustomer as jest.Mock).mockResolvedValue({ linked: true });
  (alreadyLinkedHolder as jest.Mock).mockReset();
  (alreadyLinkedHolder as jest.Mock).mockReturnValue(null);
  (mergeCompanies as jest.Mock).mockReset();
  (mergeCompanies as jest.Mock).mockResolvedValue({ id: "holder-1" });
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

describe("CompanyFactusolPanel — «Buscar en FACTUSOL»: listar y elegir CODCLI (Bloque 2)", () => {
  const UNLINKED = { ...COMPANY, factusol_company_id: null };
  const HIT_11 = {
    ...CUSTOMER, codcli: "11", nombre: "BOMEDIA SL", nif: "B63609309",
    nifcli: "B63609309", crm_link: null, factusol_matches_crm_id: null,
  };
  const HIT_89 = {
    ...CUSTOMER, codcli: "89", nombre: "BOMEDIA SL (2)", nif: "B63609309",
    nifcli: "B63609309", crm_link: null, factusol_matches_crm_id: null,
  };

  it("lista TODOS los CODCLI que coinciden y vincula SOLO el que elija el operador", async () => {
    (searchFactusolCustomers as jest.Mock).mockResolvedValue([HIT_11, HIT_89]);
    const onLinked = jest.fn();
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={UNLINKED} onLinked={onLinked} />);
    await user.click(await screen.findByRole("button", { name: "Buscar en FACTUSOL" }));
    // Ambos CODCLI del mismo NIF se listan (el caso BOMEDIA 11 y 89).
    expect(await screen.findByText(/Coinciden 2 clientes de FACTUSOL con ese NIF/)).toBeInTheDocument();
    const row89 = within(screen.getByText("89").closest("tr") as HTMLElement);
    // Nada se ha vinculado todavía (no hay autovínculo).
    expect(linkFactusolCustomer).not.toHaveBeenCalled();
    await user.click(row89.getByRole("button", { name: "Vincular" }));
    await waitFor(() => expect(linkFactusolCustomer).toHaveBeenCalledWith({
      crm_type: "company", crm_id: "acme", factusol_codcli: "89",
    }));
    expect(linkFactusolCustomer).toHaveBeenCalledTimes(1);
    expect(onLinked).toHaveBeenCalledWith("89");
    expect(await screen.findByText(/Vinculado al cliente FACTUSOL nº 89/)).toBeInTheDocument();
  });

  it("con coincidencias NUNCA dice «no está» ni ofrece crear un duplicado", async () => {
    (searchFactusolCustomers as jest.Mock).mockResolvedValue([HIT_11]);
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={UNLINKED} />);
    await user.click(await screen.findByRole("button", { name: "Buscar en FACTUSOL" }));
    expect(await screen.findByText("11")).toBeInTheDocument();
    // Ni mensaje de «no aparece / puedes crearlo» ni botón de crear duplicado.
    expect(screen.queryByText(/No aparece en FACTUSOL|Puedes crearlo/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crear en FACTUSOL" })).not.toBeInTheDocument();
  });

  it("sin coincidencias sí ofrece crear (y solo entonces)", async () => {
    (searchFactusolCustomers as jest.Mock).mockResolvedValue([]);
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={UNLINKED} />);
    await user.click(await screen.findByRole("button", { name: "Buscar en FACTUSOL" }));
    expect(await screen.findByText(/No aparece en FACTUSOL por NIF/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Crear en FACTUSOL" })).toBeInTheDocument();
    expect(linkFactusolCustomer).not.toHaveBeenCalled();
  });

  it("permite buscar por nombre cuando el NIF no encuentra nada", async () => {
    (searchFactusolCustomers as jest.Mock)
      .mockResolvedValueOnce([])          // por NIF: nada
      .mockResolvedValueOnce([HIT_11]);   // por nombre: aparece
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={UNLINKED} />);
    await user.click(await screen.findByRole("button", { name: "Buscar en FACTUSOL" }));
    await screen.findByText(/No aparece en FACTUSOL por NIF/);
    await user.click(screen.getByRole("button", { name: "Buscar por nombre" }));
    expect(await screen.findByText("11")).toBeInTheDocument();
    expect(searchFactusolCustomers).toHaveBeenLastCalledWith(COMPANY.name, "name");
  });
});

describe("CompanyFactusolPanel — «Fusionar con esa empresa» (Lote 8 · B2)", () => {
  const UNLINKED = { ...COMPANY, factusol_company_id: null };
  const HIT = {
    ...CUSTOMER, codcli: "11", nombre: "BOMEDIA SL", nif: "B63609309",
    nifcli: "B63609309", crm_link: null, factusol_matches_crm_id: null,
  };

  it("ofrece fusionar cuando el CODCLI ya lo tiene otra empresa, y fusiona ESTA en aquélla", async () => {
    (searchFactusolCustomers as jest.Mock).mockResolvedValue([HIT]);
    // El vínculo se rechaza con «ya vinculado a otra empresa»; el extractor
    // devuelve quién lo tiene (una empresa) para ofrecer fusionar.
    (linkFactusolCustomer as jest.Mock).mockRejectedValue(new Error("ya vinculado"));
    (alreadyLinkedHolder as jest.Mock).mockReturnValue({
      type: "company", company_id: "holder-1", company_name: "Empresa Titular SL",
    });
    const onMerged = jest.fn();
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={UNLINKED} onMerged={onMerged} />);
    await user.click(await screen.findByRole("button", { name: "Buscar en FACTUSOL" }));
    const row = within(screen.getByText("11").closest("tr") as HTMLElement);
    await user.click(row.getByRole("button", { name: "Vincular" }));
    // No se ha fusionado todavía: primero se ofrece.
    expect(mergeCompanies).not.toHaveBeenCalled();
    const dialog = within(await screen.findByRole("dialog", {
      name: "Fusionar con la empresa vinculada",
    }));
    expect(dialog.getAllByText(/Empresa Titular SL/).length).toBeGreaterThan(0);
    await user.click(dialog.getByRole("button", { name: "Fusionar con esa empresa" }));
    // ESTA empresa (origen) se fusiona EN la titular (superviviente).
    await waitFor(() => expect(mergeCompanies).toHaveBeenCalledWith("acme", "holder-1"));
    expect(onMerged).toHaveBeenCalledWith("holder-1");
  });

  it("cancelar la oferta no fusiona nada", async () => {
    (searchFactusolCustomers as jest.Mock).mockResolvedValue([HIT]);
    (linkFactusolCustomer as jest.Mock).mockRejectedValue(new Error("ya vinculado"));
    (alreadyLinkedHolder as jest.Mock).mockReturnValue({
      type: "company", company_id: "holder-1", company_name: "Empresa Titular SL",
    });
    const user = userEvent.setup();
    render(<CompanyFactusolPanel company={UNLINKED} />);
    await user.click(await screen.findByRole("button", { name: "Buscar en FACTUSOL" }));
    const row = within(screen.getByText("11").closest("tr") as HTMLElement);
    await user.click(row.getByRole("button", { name: "Vincular" }));
    const dialog = within(await screen.findByRole("dialog", {
      name: "Fusionar con la empresa vinculada",
    }));
    await user.click(dialog.getByRole("button", { name: "Cancelar" }));
    expect(mergeCompanies).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
