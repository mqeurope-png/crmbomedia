import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewManualOrderPage from "./page";
import { listContacts } from "../../../lib/api";
import { listCompanies } from "../../../lib/companiesApi";
import { createOrder } from "../../../lib/erpApi";

const push = jest.fn();
const refresh = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../../lib/api", () => ({ listContacts: jest.fn() }));
jest.mock("../../../lib/companiesApi", () => ({ listCompanies: jest.fn() }));
jest.mock("../../../lib/erpApi", () => ({ createOrder: jest.fn() }));

const mockCompanies = listCompanies as jest.Mock;
const mockContacts = listContacts as jest.Mock;
const mockCreate = createOrder as jest.Mock;

const COMPANY = {
  id: "c1", name: "Duplicoder SL", tax_id: "B12345678",
  address_line: "C Aribau 171", city: "Barcelona", postal_code: "08036",
  state: "Barcelona", country: "España",
};

beforeEach(() => {
  push.mockReset();
  refresh.mockReset();
  mockCompanies.mockReset();
  mockContacts.mockReset();
  mockCreate.mockReset();
  mockCompanies.mockResolvedValue({ items: [COMPANY], total: 1 });
  mockContacts.mockResolvedValue({ items: [], total: 0 });
  mockCreate.mockResolvedValue({ id: "new-order-1" });
});

describe("NewManualOrderPage", () => {
  it("renderiza el formulario con 1 línea y el origen manual", async () => {
    render(<NewManualOrderPage />);
    expect(screen.getByText("Nuevo pedido manual")).toBeInTheDocument();
    expect(screen.getByText(/Origen:/)).toHaveTextContent("manual");
    expect(screen.getByLabelText("SKU línea 1")).toBeInTheDocument();
    expect(screen.queryByLabelText("SKU línea 2")).not.toBeInTheDocument();
    // Sin cliente ni dirección el submit está deshabilitado.
    expect(screen.getByRole("button", { name: "Crear pedido" })).toBeDisabled();
  });

  it("añade y elimina líneas", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await user.click(screen.getByRole("button", { name: "+ Añadir línea" }));
    expect(screen.getByLabelText("SKU línea 2")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Eliminar línea 2" }));
    expect(screen.queryByLabelText("SKU línea 2")).not.toBeInTheDocument();
  });

  it("al elegir empresa autocompleta NIF y dirección de envío", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(mockCompanies).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    await waitFor(() =>
      expect(screen.getByLabelText("NIF / CIF")).toHaveValue("B12345678"));
    expect(screen.getByLabelText("Dirección de envío")).toHaveValue("C Aribau 171");
    expect(screen.getByLabelText("Ciudad de envío")).toHaveValue("Barcelona");
  });

  it("envía el pedido y redirige a la ficha nueva", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(mockCompanies).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    await user.type(screen.getByLabelText("SKU línea 1"), "SKU-1");
    await user.type(screen.getByLabelText("Precio línea 1"), "100");

    const submit = screen.getByRole("button", { name: "Crear pedido" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.company_id).toBe("c1");
    expect(payload.lines).toHaveLength(1);
    expect(payload.lines[0]).toMatchObject({ product_sku: "SKU-1", unit_price: 100 });
    expect(payload.shipping_address.city).toBe("Barcelona");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/erp/orders/new-order-1"));
  });

  it("con «Recogida en tienda» no exige dirección de envío", async () => {
    const user = userEvent.setup();
    render(<NewManualOrderPage />);
    await waitFor(() => expect(mockContacts).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Empresa"), "Duplicoder SL");
    await user.click(screen.getByLabelText("Recogida en tienda"));
    expect(screen.queryByLabelText("Dirección de envío")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("SKU línea 1"), "SKU-1");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Crear pedido" })).toBeEnabled());
  });
});
