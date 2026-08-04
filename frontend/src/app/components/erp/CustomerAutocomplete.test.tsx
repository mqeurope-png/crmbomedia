import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CustomerAutocomplete } from "./CustomerAutocomplete";
import { listCompanies } from "../../lib/companiesApi";
import { searchFactusolCustomers, type FactusolCustomer } from "../../lib/erpApi";

jest.mock("../../lib/companiesApi", () => ({ listCompanies: jest.fn() }));
jest.mock("../../lib/erpApi", () => ({ searchFactusolCustomers: jest.fn() }));

const mockCompanies = listCompanies as jest.Mock;
const mockFactusol = searchFactusolCustomers as jest.Mock;

function customer(over: Partial<FactusolCustomer> = {}): FactusolCustomer {
  return {
    codcli: "2458", nomcli: "Laboratorios Porta", cifcli: "B64113590",
    dircli: "C Aribau 171", pobcli: "Barcelona", cpocli: "08036",
    procli: "Barcelona", naccli: "ES", emacli: null, telcli: null,
    crm_link: null, factusol_matches_crm_id: null, ...over,
  };
}

const CRM_COMPANY = {
  id: "c1", name: "Solo CRM SL", tax_id: "B99999999",
  factusol_company_id: null, address_line: null, city: null,
  postal_code: null, state: null, country: null,
};

beforeEach(() => {
  mockCompanies.mockReset();
  mockFactusol.mockReset();
  mockFactusol.mockResolvedValue([]);
  mockCompanies.mockResolvedValue({ items: [], total: 0 });
});

describe("CustomerAutocomplete", () => {
  it("consulta FACTUSOL y CRM y pinta las 2 secciones", async () => {
    mockFactusol.mockResolvedValue([customer()]);
    mockCompanies.mockResolvedValue({ items: [CRM_COMPANY], total: 1 });
    const user = userEvent.setup();
    render(<CustomerAutocomplete onPick={() => {}} />);
    await user.type(screen.getByLabelText("Buscar cliente"), "porta");

    await waitFor(() => expect(mockFactusol).toHaveBeenCalled());
    await waitFor(() => expect(mockCompanies).toHaveBeenCalled());
    expect(await screen.findByText("En FACTUSOL")).toBeInTheDocument();
    expect(screen.getByText("Laboratorios Porta")).toBeInTheDocument();
    expect(screen.getByText(/Solo en CRM/)).toBeInTheDocument();
    expect(screen.getByText("Solo CRM SL")).toBeInTheDocument();
  });

  it("un NIF se busca por «nif» exacto y un nombre por «name»", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<CustomerAutocomplete onPick={() => {}} />);
    await user.type(screen.getByLabelText("Buscar cliente"), "B64113590");
    await waitFor(() => expect(mockFactusol).toHaveBeenCalledWith("B64113590", "nif"));
    unmount();

    mockFactusol.mockClear();
    render(<CustomerAutocomplete onPick={() => {}} />);
    await user.type(screen.getByLabelText("Buscar cliente"), "Porta");
    await waitFor(() => expect(mockFactusol).toHaveBeenCalledWith("Porta", "name"));
  });

  it("marca «✓ En CRM» si el cliente FACTUSOL ya está vinculado", async () => {
    mockFactusol.mockResolvedValue([customer({
      crm_link: { type: "company", id: "c9", name: "Porta CRM" },
      factusol_matches_crm_id: "c9",
    })]);
    const user = userEvent.setup();
    render(<CustomerAutocomplete onPick={() => {}} />);
    await user.type(screen.getByLabelText("Buscar cliente"), "porta");
    expect(await screen.findByText("✓ En CRM")).toBeInTheDocument();
  });

  it("marca «Vincular a CRM» si no está vinculado", async () => {
    mockFactusol.mockResolvedValue([customer()]);
    const user = userEvent.setup();
    render(<CustomerAutocomplete onPick={() => {}} />);
    await user.type(screen.getByLabelText("Buscar cliente"), "porta");
    expect(await screen.findByText("Vincular a CRM")).toBeInTheDocument();
  });

  it("elegir un cliente FACTUSOL emite la elección", async () => {
    mockFactusol.mockResolvedValue([customer()]);
    const onPick = jest.fn();
    const user = userEvent.setup();
    render(<CustomerAutocomplete onPick={onPick} />);
    await user.type(screen.getByLabelText("Buscar cliente"), "porta");
    await user.click(await screen.findByText("Laboratorios Porta"));
    expect(onPick).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "factusol" }),
    );
  });

  it("elegir una empresa solo-CRM la marca para crear en FACTUSOL", async () => {
    mockCompanies.mockResolvedValue({ items: [CRM_COMPANY], total: 1 });
    const onPick = jest.fn();
    const user = userEvent.setup();
    render(<CustomerAutocomplete onPick={onPick} />);
    await user.type(screen.getByLabelText("Buscar cliente"), "solo");
    expect(await screen.findByText("Crear en FACTUSOL")).toBeInTheDocument();
    await user.click(screen.getByText("Solo CRM SL"));
    expect(onPick).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "crm" }),
    );
  });

  it("las empresas CRM que ya tienen código FACTUSOL no se duplican abajo", async () => {
    mockCompanies.mockResolvedValue({
      items: [{ ...CRM_COMPANY, factusol_company_id: "2458" }], total: 1,
    });
    const user = userEvent.setup();
    render(<CustomerAutocomplete onPick={() => {}} />);
    await user.type(screen.getByLabelText("Buscar cliente"), "solo");
    await waitFor(() => expect(mockCompanies).toHaveBeenCalled());
    expect(screen.queryByText(/Solo en CRM/)).not.toBeInTheDocument();
  });

  it("sin resultados ofrece crear el contacto", async () => {
    const user = userEvent.setup();
    render(<CustomerAutocomplete onPick={() => {}} />);
    await user.type(screen.getByLabelText("Buscar cliente"), "zzz");
    expect(await screen.findByText(/Créalo primero en Contactos/)).toBeInTheDocument();
  });
});
