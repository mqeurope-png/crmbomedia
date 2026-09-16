import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { OrderFactusolClientPanel } from "./OrderFactusolClientPanel";
import {
  completeOrderFactusolCustomer,
  getOrderFactusolCustomer,
} from "../../lib/erpApi";
import { getCompany } from "../../lib/companiesApi";

/** ERP · Lote 4 — panel del cliente FACTUSOL del pedido, también sin empresa CRM.
 *
 *  Sin empresa vinculada el cliente EXISTE igual: el backend lo resuelve por el
 *  CLIFAC de la factura / el CLIALB del albarán y el panel lo pinta y deja
 *  completar lo que falte (NIF) con confirmación. `found:false` → «sin cliente».
 *  Con empresa CRM se reutiliza el `CompanyFactusolPanel` (no se regresa). */

jest.mock("../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  getOrderFactusolCustomer: jest.fn(),
  completeOrderFactusolCustomer: jest.fn(),
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/companiesApi", () => ({ getCompany: jest.fn() }));
// El camino «con empresa» solo tiene que delegar en el panel de empresa.
jest.mock("./CompanyFactusolPanel", () => ({
  CompanyFactusolPanel: () => <div>panel de empresa</div>,
}));

beforeEach(() => {
  (getOrderFactusolCustomer as jest.Mock).mockReset();
  (completeOrderFactusolCustomer as jest.Mock).mockReset();
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

  it("con empresa CRM vinculada reutiliza el panel de empresa (no resuelve por CODCLI)", async () => {
    (getCompany as jest.Mock).mockResolvedValue({ id: "c-1", name: "X", factusol_company_id: "1043" });
    render(<OrderFactusolClientPanel orderId="o-9" companyId="c-1" />);
    expect(await screen.findByText("panel de empresa")).toBeInTheDocument();
    expect(getOrderFactusolCustomer).not.toHaveBeenCalled();
  });
});
