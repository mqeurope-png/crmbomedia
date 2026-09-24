import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GeneiShipmentSection } from "./GeneiShipmentSection";
import {
  geneiCreateShipment,
  geneiDeleteShipment,
  geneiFetchLabel,
  geneiPrefill,
  geneiPrices,
  geneiRefresh,
} from "../../lib/geneiApi";

jest.mock("../../lib/geneiApi", () => ({
  ...jest.requireActual("../../lib/geneiApi"),
  geneiPrefill: jest.fn(),
  geneiPrices: jest.fn(),
  geneiCreateShipment: jest.fn(),
  geneiFetchLabel: jest.fn(),
  geneiRefresh: jest.fn(),
  geneiDeleteShipment: jest.fn(),
}));

const mockPrefill = geneiPrefill as jest.Mock;
const mockPrices = geneiPrices as jest.Mock;
const mockCreate = geneiCreateShipment as jest.Mock;
const mockLabel = geneiFetchLabel as jest.Mock;
const mockRefresh = geneiRefresh as jest.Mock;
const mockDelete = geneiDeleteShipment as jest.Mock;

const DEST = {
  name: "Alexandre", contact: "Alexandre", dni: "B1", email: "a@x.fr", phone: "+33",
  address: "12 Rue", postalCode: "75001", town: "Paris", isoCountry: "FR", observations: "",
};
const PKG = { weight: 1, height: 20, width: 20, length: 20 };

function prefill(over = {}) {
  return {
    order_id: "o-1", configured: true, destination: DEST, missing: [],
    default_package: PKG, preferred_couriers: ["GLS"], origin_address_id: "1304422",
    state: {}, ...over,
  };
}

beforeEach(() => {
  [mockPrefill, mockPrices, mockCreate, mockLabel, mockRefresh, mockDelete].forEach((m) => m.mockReset());
});

it("sin configurar: invita a configurar Genei en Ajustes", async () => {
  mockPrefill.mockResolvedValue(prefill({ configured: false }));
  render(<GeneiShipmentSection orderId="o-1" canManage />);
  expect(await screen.findByText(/Genei no está configurado/)).toBeInTheDocument();
});

it("sin envío: crea uno con el comparador (propone el preferido más barato) y muestra el estado", async () => {
  mockPrefill.mockResolvedValue(prefill());
  mockPrices.mockResolvedValue({
    order_id: "o-1",
    default: { agency_id: "2", name: "GLS Domicilio", price: 4.5, home_delivery: true },
    home_options: [
      { agency_id: "1", name: "Correos Domicilio", price: 6, home_delivery: true },
      { agency_id: "2", name: "GLS Domicilio", price: 4.5, home_delivery: true },
    ],
    all_options: [
      { agency_id: "1", name: "Correos Domicilio", price: 6, home_delivery: true },
      { agency_id: "2", name: "GLS Domicilio", price: 4.5, home_delivery: true },
      { agency_id: "4", name: "GLS Oficina", price: 2, home_delivery: false },
    ],
    preferred_couriers: ["GLS"],
  });
  mockCreate.mockResolvedValue({
    order_id: "o-1",
    summary: { shipment_code: "GEN9", state_bucket: "created", state_label: "Recogida pendiente de pago",
               tracking: null, courier: "GLS Domicilio", payment_url: "https://pay/x", state_code: 7 },
    state: { shipment_code: "GEN9", state_bucket: "created", state_label: "Recogida pendiente de pago",
             courier: "GLS Domicilio", payment_url: "https://pay/x" },
  });
  const onChanged = jest.fn();
  const user = userEvent.setup();
  render(<GeneiShipmentSection orderId="o-1" canManage onChanged={onChanged} />);

  await user.click(await screen.findByRole("button", { name: "Crear envío con Genei" }));
  const dialog = await screen.findByRole("dialog", { name: "Crear envío con Genei" });
  // Autocompara al abrir (destino completo) y preselecciona la preferida (GLS, 2).
  await waitFor(() => expect(mockPrices).toHaveBeenCalled());
  const gls = await within(dialog).findByRole("radio", { name: /GLS Domicilio · 4.50/ });
  expect(gls).toBeChecked();
  // «Solo a domicilio» por defecto: la de oficina no se lista.
  expect(within(dialog).queryByText(/GLS Oficina/)).toBeNull();

  await user.click(within(dialog).getByRole("button", { name: "Crear envío" }));
  await waitFor(() => expect(mockCreate).toHaveBeenCalledWith("o-1", expect.objectContaining({
    agency_id: "2", destination: expect.objectContaining({ town: "Paris" }),
  })));
  // Estado tras crear: pastilla + enlace de pago (pago manual en Genei).
  expect(await screen.findByText("Recogida pendiente de pago")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Pagar y tramitar en Genei/ }))
    .toHaveAttribute("href", "https://pay/x");
  expect(onChanged).toHaveBeenCalled();
});

it("con envío: etiqueta, actualizar estado y eliminar", async () => {
  mockPrefill.mockResolvedValue(prefill({
    state: { shipment_code: "GEN9", state_bucket: "ready", state_label: "Tramitado",
             courier: "GLS", tracking: null },
  }));
  mockLabel.mockResolvedValue({
    order_id: "o-1", file: { id: "f1" }, transition_applied: true, transition_reason: null,
    state: { shipment_code: "GEN9", state_bucket: "ready", state_label: "Tramitado", courier: "GLS" },
  });
  mockRefresh.mockResolvedValue({
    order_id: "o-1",
    summary: { shipment_code: "GEN9", state_bucket: "in_transit", state_label: "En tránsito",
               tracking: "TRK9", courier: "GLS", payment_url: null, state_code: 5 },
    state: { shipment_code: "GEN9", state_bucket: "in_transit", state_label: "En tránsito",
             tracking: "TRK9", courier: "GLS" },
  });
  mockDelete.mockResolvedValue({ order_id: "o-1", deleted: true });
  const user = userEvent.setup();
  render(<GeneiShipmentSection orderId="o-1" canManage />);

  expect(await screen.findByText("GEN9")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Descargar etiqueta" }));
  await waitFor(() => expect(mockLabel).toHaveBeenCalledWith("o-1"));
  expect(await screen.findByText(/Etiqueta descargada/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Actualizar estado" }));
  await waitFor(() => expect(mockRefresh).toHaveBeenCalledWith("o-1"));
  expect(await screen.findByText("TRK9")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Eliminar envío" }));
  await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("o-1"));
  expect(await screen.findByText(/Aún no hay envío en Genei/)).toBeInTheDocument();
});

it("sin permiso no ofrece crear/gestionar", async () => {
  mockPrefill.mockResolvedValue(prefill());
  render(<GeneiShipmentSection orderId="o-1" canManage={false} />);
  await screen.findByText(/Aún no hay envío en Genei/);
  expect(screen.queryByRole("button", { name: "Crear envío con Genei" })).toBeNull();
});
