import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanySearch } from "./CompanySearch";
import { listCompanies } from "../lib/companiesApi";

jest.mock("../lib/companiesApi", () => ({
  listCompanies: jest.fn(),
}));
const mockList = listCompanies as jest.Mock;

function company(over: Record<string, unknown> = {}) {
  return {
    id: "maison", name: "SAS La Maison de la Plaque", tax_id: "FR16339753527",
    vat: "FR16339753527", country: "FR", domain: "maisonplaque.fr",
    factusol_company_id: "2760", ...over,
  };
}

beforeEach(() => {
  mockList.mockReset();
  mockList.mockResolvedValue({
    items: [company(), company({
      id: "cadeau", name: "La Maison du Cadeau", tax_id: "B98765432", vat: null,
      country: "ES", domain: null, factusol_company_id: null,
    })],
    total: 2,
  });
});

describe("CompanySearch — buscador de empresa unificado", () => {
  it("filtra en vivo y cada resultado dice si está en FACTUSOL (con nº) o es solo CRM", async () => {
    const user = userEvent.setup();
    render(<CompanySearch onPick={() => {}} onCreate={() => {}} />);
    await user.type(screen.getByRole("combobox", { name: "Empresa" }), "la mai");
    await waitFor(() => expect(mockList).toHaveBeenCalledWith({ q: "la mai", limit: 8 }));
    const opciones = await screen.findAllByRole("option");
    // 2 empresas + «Crear empresa «…»».
    expect(opciones).toHaveLength(3);
    expect(opciones[0]).toHaveTextContent("SAS La Maison de la Plaque");
    expect(opciones[0]).toHaveTextContent("En FACTUSOL · nº 2760");
    // El NIF va en monoespaciada (dato numérico) y cada fila enseña su acción;
    // «En FACTUSOL» da el primario, «Solo CRM» el secundario.
    expect(opciones[0].querySelector(".mono")).toHaveTextContent("FR16339753527");
    expect(opciones[0].querySelector(".company-search-use")).toHaveTextContent("Usar esta");
    expect(opciones[0].querySelector(".company-search-use")?.className).not.toContain("secondary");
    expect(opciones[1]).toHaveTextContent("La Maison du Cadeau");
    expect(opciones[1]).toHaveTextContent("Solo CRM");
    expect(opciones[1].querySelector(".company-search-use")?.className).toContain("secondary");
    expect(opciones[2]).toHaveTextContent("Crear empresa «la mai»");
    expect(opciones[2].className).toContain("is-create");
  });

  it("elegir una empresa llama a onPick y cierra la lista", async () => {
    const onPick = jest.fn();
    const user = userEvent.setup();
    render(<CompanySearch onPick={onPick} />);
    await user.type(screen.getByRole("combobox", { name: "Empresa" }), "mai");
    await user.click(await screen.findByRole("option", { name: /Maison du Cadeau/ }));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: "cadeau" }));
    expect(screen.queryByRole("option")).toBeNull();
  });

  it("la opción «Crear empresa «…»» es persistente (última, también con resultados) y lleva el texto escrito", async () => {
    mockList.mockResolvedValue({ items: [], total: 0 });
    const onCreate = jest.fn();
    const user = userEvent.setup();
    const view = render(<CompanySearch onPick={() => {}} onCreate={onCreate} />);
    await user.type(screen.getByRole("combobox", { name: "Empresa" }), "Nueva SL");
    expect(await screen.findByText("Ninguna empresa coincide.")).toBeInTheDocument();
    const [crear] = screen.getAllByRole("option");
    expect(crear).toHaveTextContent("¿No está en la lista?");
    expect(crear).toHaveTextContent("Crear empresa «Nueva SL»");
    await user.click(crear);
    expect(onCreate).toHaveBeenCalledWith("Nueva SL");
    view.unmount();

    // Con resultados sigue ahí, la última, y arrastra lo escrito tal cual.
    mockList.mockResolvedValue({ items: [company()], total: 1 });
    render(<CompanySearch onPick={() => {}} onCreate={onCreate} />);
    await user.type(screen.getByRole("combobox", { name: "Empresa" }), "rotulacion");
    await screen.findByRole("option", { name: /SAS La Maison de la Plaque/ });
    const opciones = screen.getAllByRole("option");
    expect(opciones).toHaveLength(2);
    expect(opciones[1]).toHaveTextContent("Crear empresa «rotulacion»");
    await user.click(opciones[1]);
    expect(onCreate).toHaveBeenLastCalledWith("rotulacion");
  });

  it("teclado: ↓ ↓ ⏎ elige la segunda; sin onCreate no hay opción de crear", async () => {
    const onPick = jest.fn();
    const user = userEvent.setup();
    render(<CompanySearch onPick={onPick} />);
    const input = screen.getByRole("combobox", { name: "Empresa" });
    await user.type(input, "mai");
    await screen.findAllByRole("option");
    expect(screen.getAllByRole("option")).toHaveLength(2);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: "cadeau" }));
  });
});
