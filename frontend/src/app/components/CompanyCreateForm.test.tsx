import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyCreateForm } from "./CompanyCreateForm";
import { createCompany, fiscalCheck } from "../lib/companiesApi";
import { createFactusolCustomer } from "../lib/erpApi";
import { getCurrentUser } from "../lib/api";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: {
    children: React.ReactNode; href: string; className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));
jest.mock("../lib/api", () => ({ getCurrentUser: jest.fn() }));
jest.mock("../lib/companiesApi", () => ({
  createCompany: jest.fn(),
  fiscalCheck: jest.fn(),
}));
jest.mock("../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  createFactusolCustomer: jest.fn(),
}));

const mockUser = getCurrentUser as jest.Mock;
const mockCheck = fiscalCheck as jest.Mock;
const mockCreate = createCompany as jest.Mock;
const mockFactusol = createFactusolCustomer as jest.Mock;

function check(over: Record<string, unknown> = {}) {
  return {
    country_iso2: "FR", in_eu: true, regime: "intracomunitario",
    regime_label: "Intracomunitario (exento)",
    regime_reason: "FR (UE) con NIF-IVA FR16339753527 → intracomunitario",
    vat_normalized: "FR16339753527",
    duplicates: { crm: [], factusol: null, factusol_checked: true, factusol_error: null },
    vies: {
      applies: true, vat: "FR16339753527", status: "pendiente", valid: null,
      checked_at: null, name: null, address: null, stale: false,
    },
    ...over,
  };
}

function vies(over: Record<string, unknown>) {
  return { ...check().vies, ...over };
}

beforeEach(() => {
  mockUser.mockReset();
  mockUser.mockResolvedValue({ role: "admin" });
  mockCheck.mockReset();
  mockCheck.mockResolvedValue(check());
  mockCreate.mockReset();
  mockCreate.mockResolvedValue({
    id: "c-new", name: "SAS La Maison de la Plaque", factusol_company_id: null,
  });
  mockFactusol.mockReset();
  mockFactusol.mockResolvedValue({
    factusol_codcli: "4471", created: true, regime_label: "Intracomunitario (exento)",
  });
});

describe("CompanyCreateForm — «Crear empresa»", () => {
  it("detecta el régimen al vuelo por país + NIF-IVA y deja el gancho VIES", async () => {
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={() => {}} />);
    await user.type(screen.getByLabelText("NIF-IVA (VAT intracomunitario)"), "FR16339753527");
    await user.type(screen.getByLabelText("País"), "FR");
    await waitFor(() => expect(mockCheck).toHaveBeenLastCalledWith(
      { tax_id: "", vat: "FR16339753527", country: "FR" },
    ));
    const detect = await screen.findByRole("status");
    expect(detect).toHaveTextContent("Régimen detectado");
    expect(detect).toHaveTextContent("Intracomunitario · sin IVA");
    expect(detect).toHaveTextContent("VIES: pendiente de validar");
    expect(screen.getByText(/No existe en FACTUSOL con ese NIF/)).toBeInTheDocument();
  });

  it("VIES: «✓ verificado» con el nombre; «VAT no válido» deja el régimen nacional con IVA; VIES caído → «no disponible»", async () => {
    const user = userEvent.setup();
    async function detectar() {
      await user.type(screen.getByLabelText("NIF-IVA (VAT intracomunitario)"), "FR16339753527");
      await user.type(screen.getByLabelText("País"), "FR");
      return screen.findByRole("status");
    }
    mockCheck.mockResolvedValue(check({
      regime_reason: "FR (UE) con NIF-IVA FR16339753527 verificado en VIES → intracomunitario",
      vies: vies({ status: "valido", valid: true, checked_at: "2026-09-14T10:00:00",
                   name: "SAS LA MAISON DE LA PLAQUE" }),
    }));
    let view = render(<CompanyCreateForm onCreated={() => {}} />);
    let detect = await detectar();
    expect(detect).toHaveTextContent("Intracomunitario · sin IVA");
    expect(detect).toHaveTextContent("✓ verificado en VIES (SAS LA MAISON DE LA PLAQUE)");
    view.unmount();

    mockCheck.mockResolvedValue(check({
      regime: "nacional", regime_label: "Nacional (con IVA)",
      regime_reason: "FR (UE) con NIF-IVA FR16339753527 NO válido en VIES → nacional (no se puede eximir)",
      vies: vies({ status: "no_valido", valid: false, checked_at: "2026-09-14T10:00:00" }),
    }));
    view = render(<CompanyCreateForm onCreated={() => {}} />);
    detect = await detectar();
    expect(detect).toHaveTextContent("Nacional · con IVA");
    expect(detect).toHaveTextContent("VAT no válido en VIES: no se puede eximir de IVA");
    view.unmount();

    mockCheck.mockResolvedValue(check({
      vies: vies({ status: "desconocido", error: "VIES HTTP 500" }),
    }));
    render(<CompanyCreateForm onCreated={() => {}} />);
    detect = await detectar();
    expect(detect).toHaveTextContent("Intracomunitario · sin IVA");   // no bloquea
    expect(detect).toHaveTextContent("VIES no disponible: pendiente de validar");
  });

  it("crea la empresa con sus datos fiscales y, con la casilla marcada, también el cliente en FACTUSOL", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={onCreated} />);
    await user.type(screen.getByLabelText("Nombre fiscal *"), "SAS La Maison de la Plaque");
    await user.type(screen.getByLabelText("NIF-IVA (VAT intracomunitario)"), "FR16339753527");
    await user.type(screen.getByLabelText("País"), "FR");
    await user.type(screen.getByLabelText("Domicilio"), "Rue du Chemin Noir");
    await user.type(screen.getByLabelText("CP"), "21120");
    await user.type(screen.getByLabelText("Población"), "Is-sur-Tille");
    const casilla = await screen.findByRole("checkbox", { name: "Crear también en FACTUSOL" });
    expect(casilla).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Crear empresa" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "SAS La Maison de la Plaque", vat: "FR16339753527", country: "FR",
      address_line: "Rue du Chemin Noir", postal_code: "21120", city: "Is-sur-Tille",
      tax_id: null,
    })));
    await waitFor(() => expect(mockFactusol).toHaveBeenCalledWith(expect.objectContaining({
      crm_type: "company", crm_id: "c-new", nombre: "SAS La Maison de la Plaque",
      nif: "FR16339753527", vat: "FR16339753527", pais: "FR", cp: "21120",
    })));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({
      company: expect.objectContaining({ id: "c-new" }),
      factusol: { codcli: "4471", created: true, regime_label: "Intracomunitario (exento)" },
      factusolError: null,
    }));
  });

  it("sin la casilla (o sin rol ERP) no toca FACTUSOL", async () => {
    mockUser.mockResolvedValue({ role: "user" });
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={onCreated} />);
    await user.type(screen.getByLabelText("Nombre fiscal *"), "Solo CRM SL");
    expect(screen.queryByRole("checkbox", { name: "Crear también en FACTUSOL" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Crear empresa" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(mockFactusol).not.toHaveBeenCalled();
  });

  it("no duplicar por NIF: avisa con la existente y ofrece usarla; la existente en FACTUSOL se vincula", async () => {
    mockCheck.mockResolvedValue(check({
      duplicates: {
        crm: [{ id: "maison", name: "SAS La Maison de la Plaque", tax_id: null,
                vat: "FR16339753527", country: "FR", factusol_company_id: "2760" }],
        factusol: { codcli: "2760", nombre: "LA MAISON DE LA PLAQUE", nif: "FR16339753527" },
        factusol_checked: true, factusol_error: null,
      },
    }));
    const onUseExisting = jest.fn();
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={() => {}} onUseExisting={onUseExisting} />);
    await user.type(screen.getByLabelText("NIF-IVA (VAT intracomunitario)"), "FR16339753527");
    const aviso = await screen.findByRole("alert");
    expect(aviso).toHaveTextContent("Ya existe en el CRM con ese NIF");
    expect(aviso).toHaveTextContent("SAS La Maison de la Plaque (FACTUSOL nº 2760)");
    expect(screen.getByText(/Ya existe en FACTUSOL con ese NIF: cliente nº 2760/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Usar «SAS La Maison de la Plaque»" }));
    expect(onUseExisting).toHaveBeenCalledWith({ id: "maison", name: "SAS La Maison de la Plaque" });
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("si el alta en FACTUSOL falla, la empresa queda creada y se informa (no se pierde)", async () => {
    mockFactusol.mockRejectedValue(new Error("DELSOL caído"));
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={onCreated} />);
    await user.type(screen.getByLabelText("Nombre fiscal *"), "Nueva SL");
    await screen.findByRole("checkbox", { name: "Crear también en FACTUSOL" });
    await user.click(screen.getByRole("button", { name: "Crear empresa" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({
      company: expect.objectContaining({ id: "c-new" }),
      factusol: null,
      factusolError: expect.stringMatching(/DELSOL caído/),
    })));
  });

  it("el 409 del backend por NIF duplicado se enseña como error, sin crear", async () => {
    mockCreate.mockRejectedValue(new Error("Ya existe una empresa con ese NIF: «Duplicoder SL»"));
    mockUser.mockResolvedValue({ role: "user" });
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={() => {}} />);
    await user.type(screen.getByLabelText("Nombre fiscal *"), "Duplicoder bis");
    await user.click(screen.getByRole("button", { name: "Crear empresa" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Ya existe una empresa con ese NIF/);
  });
});
