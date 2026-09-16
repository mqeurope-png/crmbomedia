import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { OrderFactusolClientPanel } from "./OrderFactusolClientPanel";
import {
  completeOrderFactusolCustomer,
  getOrderFactusolCustomer,
  linkOrderFactusolCompany,
} from "../../lib/erpApi";
import { getCompany } from "../../lib/companiesApi";

/** ERP · Lote 4 / Lote 6 — panel del cliente FACTUSOL del pedido WEB.
 *
 *  El cliente SIEMPRE existe (lo cargó FesteWeb como F_PCL), así que el panel
 *  NUNCA ofrece «Crear en FACTUSOL». Se resuelve por el CLIPCL del F_PCL
 *  (PREFERENTE), o el CLIFAC de la factura / el CLIALB del albarán; el panel lo
 *  pinta, deja completar lo que falte (NIF) y ofrece «Vincular empresa a este
 *  cliente» (al CODCLI exacto). `found:false` → «sin cliente». Con la empresa YA
 *  vinculada se reutiliza el `CompanyFactusolPanel`. */

jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  getOrderFactusolCustomer: jest.fn(),
  completeOrderFactusolCustomer: jest.fn(),
  linkOrderFactusolCompany: jest.fn(),
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/companiesApi", () => ({ getCompany: jest.fn() }));
// El camino «con empresa YA vinculada» delega en el panel de empresa. Si se
// pintara «Crear en FACTUSOL» sería este mock, que aquí no debe montarse en los
// pedidos web sin vincular.
jest.mock("./CompanyFactusolPanel", () => ({
  CompanyFactusolPanel: () => <div>panel de empresa · Crear en FACTUSOL</div>,
}));

beforeEach(() => {
  (getOrderFactusolCustomer as jest.Mock).mockReset();
  (completeOrderFactusolCustomer as jest.Mock).mockReset();
  (linkOrderFactusolCompany as jest.Mock).mockReset();
  (getCompany as jest.Mock).mockReset();
});

describe("OrderFactusolClientPanel — cliente por CODCLI/CLIFAC (Lote 4)", () => {
  it("pinta el cliente resuelto por la factura y ofrece completar el NIF que falta", async () => {
    (getOrderFactusolCustomer as jest.Mock).mockResolvedValue({
      found: true, codcli: "260090", source: "factura", missing: ["nif"],
      company_id: null,
      cliente: {
        codcli: "260090", nombre: "Escola La Muntanyeta", nofcli: "Escola La Muntanyeta",
        nif: "", regime_label: "Nacional",
      },
    });
    (completeOrderFactusolCustomer as jest.Mock).mockResolvedValue({
      ok: true, changed: true, written: { NIFCLI: "G12345678" }, codcli: "260090",
      source: "factura", cliente: null, missing: [],
    });
    const onChanged = jest.fn();
    render(<OrderFactusolClientPanel orderId="o-9" companyId={null} onChanged={onChanged} />);

    expect(await screen.findByText("Cliente FACTUSOL nº 260090")).toBeInTheDocument();
    expect(screen.getByText("Escola La Muntanyeta")).toBeInTheDocument();
    // Resuelto por el cliente de la factura (CLIFAC), no por empresa.
    expect(screen.getByText(/CLIFAC/)).toBeInTheDocument();
    expect(getCompany).not.toHaveBeenCalled();

    // El botón depende de la carga async del usuario (rol editable).
    fireEvent.click(await screen.findByRole("button", { name: "Completar datos" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "G12345678" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar en FACTUSOL" }));

    await waitFor(() =>
      expect(completeOrderFactusolCustomer).toHaveBeenCalledWith("o-9", { nif: "G12345678" }),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("found:false muestra el aviso «sin cliente» (no revienta)", async () => {
    (getOrderFactusolCustomer as jest.Mock).mockResolvedValue({
      found: false, codcli: null, source: null, cliente: null, missing: [], company_id: null,
    });
    render(<OrderFactusolClientPanel orderId="o-9" companyId={null} />);
    expect(await screen.findByText(/Sin cliente FACTUSOL/)).toBeInTheDocument();
    expect(completeOrderFactusolCustomer).not.toHaveBeenCalled();
  });

  it("con empresa CRM YA vinculada reutiliza el panel de empresa (no resuelve por CODCLI)", async () => {
    (getCompany as jest.Mock).mockResolvedValue({ id: "c-1", name: "X", factusol_company_id: "1043" });
    render(<OrderFactusolClientPanel orderId="o-9" companyId="c-1" />);
    expect(await screen.findByText(/panel de empresa/)).toBeInTheDocument();
    expect(getOrderFactusolCustomer).not.toHaveBeenCalled();
  });

  it("pedido web con F_PCL resuelve por el CLIPCL (pedido_cliente) y NO ofrece crear", async () => {
    (getOrderFactusolCustomer as jest.Mock).mockResolvedValue({
      found: true, codcli: "11", source: "pedido_cliente", missing: [],
      company_id: null,
      cliente: { codcli: "11", nombre: "BOMEDIA", nofcli: "BOMEDIA", nif: "B00000000" },
    });
    render(<OrderFactusolClientPanel orderId="o-9" companyId={null} />);
    expect(await screen.findByText("Cliente FACTUSOL nº 11")).toBeInTheDocument();
    expect(screen.getByText("BOMEDIA")).toBeInTheDocument();
    expect(screen.getByText(/CLIPCL/)).toBeInTheDocument();
    // NUNCA se ofrece crear en FACTUSOL (lo duplicaría): FesteWeb ya lo cargó.
    expect(screen.queryByText(/Crear en FACTUSOL/)).toBeNull();
    // Sin empresa en el pedido no hay a quién vincular.
    expect(screen.queryByRole("button", { name: /Vincular empresa/ })).toBeNull();
  });

  it("pedido web con empresa SIN vincular: resuelve por F_PCL, NO crea y ofrece vincular", async () => {
    // La empresa existe pero NO está vinculada → nada de «Crear en FACTUSOL»:
    // se resuelve por el F_PCL y se ofrece vincular al CODCLI exacto.
    (getCompany as jest.Mock).mockResolvedValue({ id: "c-2", name: "BOMEDIA SL", factusol_company_id: null });
    (getOrderFactusolCustomer as jest.Mock).mockResolvedValue({
      found: true, codcli: "11", source: "pedido_cliente", missing: [],
      company_id: "c-2",
      cliente: { codcli: "11", nombre: "BOMEDIA", nofcli: "BOMEDIA", nif: "B00000000" },
    });
    (linkOrderFactusolCompany as jest.Mock).mockResolvedValue({
      ok: true, order_id: "o-9", company_id: "c-2", codcli: "11",
      source: "pedido_cliente", linked: true,
    });
    const onChanged = jest.fn();
    render(<OrderFactusolClientPanel orderId="o-9" companyId="c-2" onChanged={onChanged} />);

    expect(await screen.findByText("Cliente FACTUSOL nº 11")).toBeInTheDocument();
    // Resuelto por el F_PCL, sin pasar por el panel de empresa (sin «Crear»).
    expect(screen.queryByText(/panel de empresa/)).toBeNull();
    expect(screen.queryByText(/Crear en FACTUSOL/)).toBeNull();
    expect(getOrderFactusolCustomer).toHaveBeenCalledWith("o-9");

    const link = await screen.findByRole("button", { name: /Vincular empresa a este cliente/ });
    fireEvent.click(link);
    await waitFor(() => expect(linkOrderFactusolCompany).toHaveBeenCalledWith("o-9"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });
});
