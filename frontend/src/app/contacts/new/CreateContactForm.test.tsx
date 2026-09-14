import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CreateContactForm } from "./CreateContactForm";
import { createContact } from "../../lib/api";

const push = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: jest.fn() }),
}));
jest.mock("../../lib/api", () => ({ createContact: jest.fn() }));
jest.mock("../../components/CompanySearch", () => ({
  CompanySearch: ({ onPick, onCreate }: {
    onPick: (c: { id: string; name: string; factusol_company_id: string | null }) => void;
    onCreate: (n: string) => void;
  }) => (
    <div>
      <input aria-label="Empresa" />
      <button type="button" onClick={() => onPick({
        id: "maison", name: "La Maison", factusol_company_id: "2760",
      })}>elegir maison</button>
      <button type="button" onClick={() => onCreate("Nueva SL")}>crear nueva</button>
    </div>
  ),
}));
jest.mock("../../components/CompanyCreateForm", () => ({
  CompanyCreateForm: ({ initialName, onCreated }: {
    initialName: string;
    onCreated: (r: { company: { id: string; name: string; factusol_company_id: null }; factusol: { codcli: string; created: boolean; regime_label: null } | null; factusolError: string | null }) => void;
  }) => (
    <button type="button" onClick={() => onCreated({
      company: { id: "c-new", name: initialName, factusol_company_id: null },
      factusol: { codcli: "4471", created: true, regime_label: null }, factusolError: null,
    })}>guardar {initialName}</button>
  ),
}));

beforeEach(() => {
  push.mockReset();
  (createContact as jest.Mock).mockReset();
  (createContact as jest.Mock).mockResolvedValue({ id: "ct-1" });
});

describe("CreateContactForm — alta de contacto con el buscador unificado", () => {
  it("ya no hay desplegable de empresas: se busca, se elige y viaja company_id", async () => {
    const user = userEvent.setup();
    render(<CreateContactForm />);
    expect(screen.queryByRole("combobox", { name: /Empresa/ })).toBeNull();   // el <select> viejo
    expect(screen.getByLabelText("Empresa")).toBeInTheDocument();             // el buscador
    await user.click(screen.getByRole("button", { name: "elegir maison" }));
    expect(screen.getByText("La Maison")).toBeInTheDocument();
    expect(screen.getByText("en FACTUSOL nº 2760")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Nombre"), "Alexandre");
    await user.type(screen.getByLabelText("Email"), "alex@maison.fr");
    await user.click(screen.getByRole("button", { name: "Crear contacto" }));
    await waitFor(() => expect(createContact).toHaveBeenCalledWith(expect.objectContaining({
      first_name: "Alexandre", email: "alex@maison.fr", company_id: "maison",
      marketing_consent: "unknown",
    })));
    expect(push).toHaveBeenCalledWith("/contacts/ct-1");
  });

  it("«Sin empresa» y «Cambiar» siguen existiendo; sin empresa viaja null", async () => {
    const user = userEvent.setup();
    render(<CreateContactForm />);
    await user.click(screen.getByRole("button", { name: "elegir maison" }));
    await user.click(screen.getByRole("button", { name: "Sin empresa" }));
    expect(screen.getByLabelText("Empresa")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Nombre"), "Ana");
    await user.type(screen.getByLabelText("Email"), "ana@x.com");
    await user.click(screen.getByRole("button", { name: "Crear contacto" }));
    await waitFor(() => expect(createContact).toHaveBeenCalledWith(
      expect.objectContaining({ company_id: null }),
    ));
  });

  it("«Crear empresa nueva» crea sin salir del alta y la deja elegida", async () => {
    const user = userEvent.setup();
    render(<CreateContactForm />);
    await user.click(screen.getByRole("button", { name: "crear nueva" }));
    // Mientras se crea la empresa, el alta del contacto espera.
    expect(screen.getByRole("button", { name: "Crear contacto" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "guardar Nueva SL" }));
    expect(screen.getByText("Nueva SL")).toBeInTheDocument();
    expect(screen.getByText("en FACTUSOL nº 4471")).toBeInTheDocument();
    expect(await screen.findByRole("status")).toHaveTextContent("creada y elegida");
    expect(screen.getByRole("button", { name: "Crear contacto" })).toBeEnabled();
  });
});
