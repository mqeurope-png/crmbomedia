import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewCompanyPage from "./page";
import { createCompany, fiscalCheck } from "../../lib/companiesApi";
import { createFactusolCustomer } from "../../lib/erpApi";
import { getCurrentUser } from "../../lib/api";

/** Pantalla «Crear empresa» (`/companies/new`) · revisión Lote 2 §9: «lo
 *  escrito se arrastra» — el texto tecleado en el buscador llega por
 *  `?name=` y el formulario lo recibe como nombre fiscal; si todo va bien se
 *  va a la ficha nueva, y si FACTUSOL falla la empresa NO se pierde. */

// `?name=` de la URL: cada test lo fija antes de montar.
let search = "";
const push = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => new URLSearchParams(search),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: {
    children: React.ReactNode; href: string; className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
jest.mock("../../lib/api", () => ({ getCurrentUser: jest.fn() }));
jest.mock("../../lib/companiesApi", () => ({
  createCompany: jest.fn(),
  fiscalCheck: jest.fn(),
}));
jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  createFactusolCustomer: jest.fn(),
}));

const mockUser = getCurrentUser as jest.Mock;
const mockCheck = fiscalCheck as jest.Mock;
const mockCreate = createCompany as jest.Mock;
const mockFactusol = createFactusolCustomer as jest.Mock;

beforeEach(() => {
  search = "";
  push.mockReset();
  mockUser.mockReset();
  mockUser.mockResolvedValue({ role: "user" });
  mockCheck.mockReset();
  mockCheck.mockResolvedValue({
    country_iso2: "ES", in_eu: true, regime: "nacional", regime_label: "Nacional (con IVA)",
    regime_reason: "España → nacional", vat_normalized: null,
    duplicates: { crm: [], factusol: null, factusol_checked: true, factusol_error: null },
    vies: { applies: false, vat: null, status: null, valid: null, checked_at: null,
            name: null, address: null, stale: false },
  });
  mockCreate.mockReset();
  mockCreate.mockResolvedValue({ id: "c-new", name: "rotulacion", factusol_company_id: null });
  mockFactusol.mockReset();
});

describe("/companies/new — «Crear empresa» como pantalla", () => {
  it("lo escrito se arrastra: `?name=` llega como nombre fiscal y al crear va a la ficha", async () => {
    search = "name=rotulacion";
    const user = userEvent.setup();
    render(<NewCompanyPage />);
    const nombre = await screen.findByLabelText("Nombre fiscal *");
    expect(nombre).toHaveValue("rotulacion");
    await user.click(screen.getByRole("button", { name: "Crear empresa" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({ name: "rotulacion" }),
    ));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/companies/c-new"));
  });

  it("sin `?name=` el formulario sale vacío y «Cancelar» vuelve al listado", async () => {
    const user = userEvent.setup();
    render(<NewCompanyPage />);
    expect(await screen.findByLabelText("Nombre fiscal *")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Crear empresa" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(push).toHaveBeenCalledWith("/companies");
  });

  it("si el alta en FACTUSOL falla, se queda diciéndolo con enlace a la ficha (la empresa no se pierde)", async () => {
    search = "name=rotulacion";
    mockUser.mockResolvedValue({ role: "admin" });
    mockFactusol.mockRejectedValue(new Error("DELSOL caído"));
    const user = userEvent.setup();
    render(<NewCompanyPage />);
    await screen.findByRole("checkbox", { name: "Crear también en FACTUSOL" });
    await user.click(screen.getByRole("button", { name: "Crear empresa" }));
    expect(await screen.findByRole("heading", { name: "Empresa creada" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("DELSOL caído");
    expect(screen.getByRole("link", { name: "Abrir la ficha de «rotulacion»" }))
      .toHaveAttribute("href", "/companies/c-new");
    expect(push).not.toHaveBeenCalled();
  });
});
