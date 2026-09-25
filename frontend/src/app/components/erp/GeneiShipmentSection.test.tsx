import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GeneiShipmentSection } from "./GeneiShipmentSection";
import { printShippingFile } from "../../lib/erpApi";
import {
  geneiCreateShipment,
  geneiDeleteShipment,
  geneiFetchLabel,
  geneiPay,
  geneiPrefill,
  geneiPrices,
  geneiRefresh,
  getCustomerEmailPreview,
  sendCustomerEmail,
} from "../../lib/geneiApi";

jest.mock("../../lib/erpApi", () => ({
  printShippingFile: jest.fn().mockResolvedValue(undefined),
}));

jest.mock("../../lib/geneiApi", () => ({
  ...jest.requireActual("../../lib/geneiApi"),
  geneiPrefill: jest.fn(),
  geneiPrices: jest.fn(),
  geneiCreateShipment: jest.fn(),
  geneiFetchLabel: jest.fn(),
  geneiRefresh: jest.fn(),
  geneiPay: jest.fn(),
  geneiDeleteShipment: jest.fn(),
  getCustomerEmailPreview: jest.fn(),
  sendCustomerEmail: jest.fn(),
}));

const mockPrefill = geneiPrefill as jest.Mock;
const mockPrices = geneiPrices as jest.Mock;
const mockCreate = geneiCreateShipment as jest.Mock;
const mockLabel = geneiFetchLabel as jest.Mock;
const mockRefresh = geneiRefresh as jest.Mock;
const mockPay = geneiPay as jest.Mock;
const mockDelete = geneiDeleteShipment as jest.Mock;

const DEST = {
  name: "Alexandre", contact: "Alexandre", dni: "B1", email: "a@x.fr", phone: "+33",
  address: "12 Rue", postalCode: "75001", town: "Paris", isoCountry: "FR", observations: "",
};
const PKG = { weight: 1, height: 20, width: 20, length: 20 };

function prefill(over = {}) {
  return {
    order_id: "o-1", configured: true, destination: DEST, missing: [],
    packages: [], default_package: PKG, preferred_couriers: ["GLS"],
    origin_address_id: "1304422", is_packed: true, state: {}, ...over,
  };
}

beforeEach(() => {
  [mockPrefill, mockPrices, mockCreate, mockLabel, mockRefresh, mockPay, mockDelete]
    .forEach((m) => m.mockReset());
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
  // Estado tras crear: pastilla + botón «Pagar y tramitar» (pago por API, sin popup).
  expect(await screen.findByText("Recogida pendiente de pago")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Pagar y tramitar" })).toBeInTheDocument();
  expect(onChanged).toHaveBeenCalled();
});

it("«Pagar y tramitar» paga por API (sin popup) y refresca el estado", async () => {
  mockPrefill.mockResolvedValue(prefill({
    state: { shipment_code: "GEN9", state_bucket: "created",
             state_label: "Recogida pendiente de pago", transaction_id: "555" },
  }));
  mockPay.mockResolvedValue({
    order_id: "o-1",
    summary: { shipment_code: "GEN9", state_bucket: "ready", state_label: "Tramitado",
               tracking: "TRK9", courier: "GLS", payment_url: null, state_code: 1 },
    state: { shipment_code: "GEN9", state_bucket: "ready", state_label: "Tramitado",
             tracking: "TRK9", courier: "GLS" },
  });
  const user = userEvent.setup();
  render(<GeneiShipmentSection orderId="o-1" canManage />);
  await user.click(await screen.findByRole("button", { name: "Pagar y tramitar" }));
  await waitFor(() => expect(mockPay).toHaveBeenCalledWith("o-1"));
  // Tras pagar: estado tramitado y ya no ofrece pagar.
  expect(await screen.findByText("Tramitado")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Pagar y tramitar" })).toBeNull();
});

it("con envío: etiqueta (descarga e imprime en un clic), actualizar estado y eliminar", async () => {
  mockPrefill.mockResolvedValue(prefill({
    state: { shipment_code: "GEN9", state_bucket: "ready", state_label: "Tramitado",
             courier: "GLS", tracking: null, label_available: true },
  }));
  mockLabel.mockResolvedValue({
    order_id: "o-1", file: { id: "f1" }, transition_applied: true, transition_reason: null,
    state: { shipment_code: "GEN9", state_bucket: "ready", state_label: "Tramitado", courier: "GLS",
             label_available: true },
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
  // Con envío no se vuelve a ofrecer «Crear».
  expect(screen.queryByRole("button", { name: /Crear envío con Genei/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "🖨 Imprimir etiqueta" }));
  await waitFor(() => expect(mockLabel).toHaveBeenCalledWith("o-1"));
  // UNA acción: la trae y lanza la impresión del mismo fichero.
  await waitFor(() => expect(printShippingFile).toHaveBeenCalledWith({ id: "f1" }));
  expect(await screen.findByText(/Etiqueta enviada a imprimir/)).toBeInTheDocument();

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

it("pedido no embalado: no deja crear y avisa de empaquetar primero", async () => {
  mockPrefill.mockResolvedValue(prefill({ is_packed: false }));
  render(<GeneiShipmentSection orderId="o-1" canManage />);
  expect(await screen.findByText(/Embala el pedido primero/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Crear envío con Genei" })).toBeNull();
});

it("prellena el modal con las medidas reales del embalaje (multi-bulto)", async () => {
  mockPrefill.mockResolvedValue(prefill({
    packages: [
      { weight: 2.5, height: 30, width: 20, length: 15 },
      { weight: 1, height: 10, width: 10, length: 10 },
    ],
  }));
  mockPrices.mockResolvedValue({
    order_id: "o-1", default: null, home_options: [], all_options: [], preferred_couriers: [],
  });
  const user = userEvent.setup();
  render(<GeneiShipmentSection orderId="o-1" canManage />);
  await user.click(await screen.findByRole("button", { name: "Crear envío con Genei" }));
  const dialog = await screen.findByRole("dialog", { name: "Crear envío con Genei" });
  // Dos bultos reales (no el genérico 1/20/20/20): el primero pesa 2.5 kg.
  const pesos = within(dialog).getAllByLabelText("Peso (kg)") as HTMLInputElement[];
  expect(pesos).toHaveLength(2);
  expect(pesos[0].value).toBe("2.5");
  expect(within(dialog).getByText(/medidas reales del embalaje/)).toBeInTheDocument();
});


it("antes de tramitar (pendiente de pago) no ofrece la etiqueta: botón deshabilitado y aviso", async () => {
  mockPrefill.mockResolvedValue(prefill({
    state: { shipment_code: "GEN9", state_bucket: "created", state_label: "Recogida pendiente de pago",
             label_available: false },
  }));
  render(<GeneiShipmentSection orderId="o-1" canManage />);
  expect(await screen.findByText(
    "La etiqueta estará disponible tras pagar y tramitar el envío.",
  )).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "🖨 Imprimir etiqueta" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Pagar y tramitar" })).toBeInTheDocument();
  expect(mockLabel).not.toHaveBeenCalled();
});

it("un fallo de la etiqueta enseña el mensaje del backend, nunca el código HTTP crudo", async () => {
  mockPrefill.mockResolvedValue(prefill({
    state: { shipment_code: "GEN9", state_bucket: "ready", state_label: "Tramitado",
             label_available: true },
  }));
  mockLabel.mockRejectedValue(new Error(
    "La etiqueta estará disponible tras pagar y tramitar el envío.",
  ));
  const user = userEvent.setup();
  render(<GeneiShipmentSection orderId="o-1" canManage />);
  await user.click(await screen.findByRole("button", { name: "🖨 Imprimir etiqueta" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("tras pagar y tramitar");
  expect(alert.textContent).not.toMatch(/GET \/shipments|→ \d{3}/);
});

it("«Crear envío» completa el destino antes de abrir el modal (teléfono y email incluidos)", async () => {
  mockPrefill
    .mockResolvedValueOnce(prefill({ destination: { ...DEST, phone: "", email: "" } }))
    .mockResolvedValueOnce(prefill({ destination: { ...DEST, phone: "934000000",
                                                    email: "compras@cliente.es" } }));
  mockPrices.mockResolvedValue({
    order_id: "o-1", default: null, home_options: [], all_options: [], preferred_couriers: [],
  });
  const user = userEvent.setup();
  render(<GeneiShipmentSection orderId="o-1" canManage />);
  await user.click(await screen.findByRole("button", { name: "Crear envío con Genei" }));
  await waitFor(() => expect(mockPrefill).toHaveBeenLastCalledWith("o-1", { completar: true }));
  const dialog = await screen.findByRole("dialog", { name: "Crear envío con Genei" });
  expect(within(dialog).getByLabelText("Teléfono")).toHaveValue("934000000");
  expect(within(dialog).getByLabelText("Email")).toHaveValue("compras@cliente.es");
});

describe("ficha: estado REAL del transportista (Genei /tracking)", () => {
  const STATE = {
    shipment_code: "G2", courier: "Ctt Premium", state_bucket: "in_transit",
    state_label: "Recogida efectuada / en tránsito", tracking: "0033260080539700026674",
    label_available: true,
    carrier_status: "EN REPARTO", carrier_status_at: "2026-09-26T07:30:00+00:00",
    carrier_step: "out_for_delivery",
    tracking_url: "https://www.cttexpress.com/localizador/",
    carrier_events: [
      { fecha: "2026-09-24T18:00:00+00:00", codigo: "0", descripcion: "PENDIENTE DE ENTRADA EN RED", step: "pre_transit" },
      { fecha: "2026-09-25T09:10:00+00:00", codigo: "1", descripcion: "EN TRANSITO", step: "in_transit" },
      { fecha: "2026-09-26T07:30:00+00:00", codigo: "2", descripcion: "EN REPARTO", step: "out_for_delivery" },
    ],
  };

  it("enseña el último escaneo, el tracking enlazado y el historial (lo más reciente arriba)", async () => {
    mockPrefill.mockResolvedValue(prefill({ state: STATE }));
    render(<GeneiShipmentSection orderId="o-1" canManage />);
    const kv = (await screen.findByText("Transportista")).parentElement as HTMLElement;
    expect(within(kv).getByText("EN REPARTO")).toHaveClass("badge", "info");
    expect(screen.getByRole("link", { name: "0033260080539700026674" }))
      .toHaveAttribute("href", "https://www.cttexpress.com/localizador/");
    expect(screen.getByText("Historial del transportista (3)")).toBeInTheDocument();
    const lista = screen.getByRole("list", { name: "Historial del transportista" });
    const items = within(lista).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("EN REPARTO");
    expect(items[2]).toHaveTextContent("PENDIENTE DE ENTRADA EN RED");
  });

  it("«Actualizar estado» avisa con el escaneo real del transportista", async () => {
    const user = userEvent.setup();
    mockPrefill.mockResolvedValue(prefill({ state: { ...STATE, carrier_status: null,
                                                     carrier_events: [] } }));
    mockRefresh.mockResolvedValue({
      order_id: "o-1", summary: { state_label: "Recogida efectuada / en tránsito" },
      state: { ...STATE, carrier_status: "PENDIENTE DE ENTRADA EN RED", carrier_step: "pre_transit" },
    });
    render(<GeneiShipmentSection orderId="o-1" canManage />);
    await user.click(await screen.findByRole("button", { name: "Actualizar estado" }));
    expect(await screen.findByText("Estado actualizado: PENDIENTE DE ENTRADA EN RED."))
      .toBeInTheDocument();
  });
});

describe("ficha: aviso de envío al cliente (BoHub, en su idioma)", () => {
  const mockPreview = getCustomerEmailPreview as jest.Mock;
  const mockSendEmail = sendCustomerEmail as jest.Mock;
  const BASE = {
    shipment_code: "G2", courier: "Ctt Premium", state_bucket: "ready",
    tracking: "0033260080539700026674", label_available: true,
  };
  beforeEach(() => { mockPreview.mockReset(); mockSendEmail.mockReset(); });

  it("enseña que se envió (a quién, idioma y remitente)", async () => {
    mockPrefill.mockResolvedValue(prefill({ state: { ...BASE, customer_email: {
      status: "sent", sent_at: "2026-09-25T10:00:00+00:00", to: "compras@lamaison.fr",
      lang: "fr", from: "info@artisjet-printers.eu", sends: 1 } } }));
    render(<GeneiShipmentSection orderId="o-1" canManage />);
    const line = await screen.findByLabelText("Aviso de envío al cliente");
    expect(line).toHaveTextContent("enviado");
    expect(line).toHaveTextContent("compras@lamaison.fr");
    expect(line).toHaveTextContent("francés");
    expect(line).toHaveTextContent("info@artisjet-printers.eu");
    expect(screen.getByRole("button", { name: "Reenviar aviso al cliente" })).toBeEnabled();
  });

  it("pendiente sin tracking: lo dice y no deja enviar aún", async () => {
    mockPrefill.mockResolvedValue(prefill({ state: { ...BASE, tracking: null,
      customer_email: { status: "pending" } } }));
    render(<GeneiShipmentSection orderId="o-1" canManage />);
    expect(await screen.findByText(/en cuanto el envío tenga nº de seguimiento/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enviar aviso al cliente" })).toBeDisabled();
  });

  it("reenviar: vista previa en el idioma del cliente, se puede cambiar el destinatario", async () => {
    const user = userEvent.setup();
    mockPrefill.mockResolvedValue(prefill({ state: { ...BASE,
      customer_email: { status: "sent", to: "compras@lamaison.fr", lang: "fr" } } }));
    mockPreview.mockResolvedValue({
      to: "compras@lamaison.fr", from_alias: "info@artisjet-printers.eu",
      from_alias_source: "idioma", store: null, lang: "fr", lang_source: "pais_destino",
      subject: "Votre commande MAN-1 a été expédiée — suivi 0033260080539700026674",
      body_text: "Bonjour…", tracking: "0033260080539700026674", tracking_url: null,
      courier: "Ctt Premium", order_ref: "MAN-1", missing: [], status: { status: "sent" },
    });
    mockSendEmail.mockResolvedValue({ order_id: "o-1", state: { ...BASE,
      customer_email: { status: "sent", to: "otra@lamaison.fr", lang: "fr", sends: 2 } } });
    render(<GeneiShipmentSection orderId="o-1" canManage />);
    await user.click(await screen.findByRole("button", { name: "Reenviar aviso al cliente" }));
    const dialog = await screen.findByRole("dialog", { name: "Aviso de envío al cliente" });
    expect(await within(dialog).findByText(/Votre commande MAN-1/)).toBeInTheDocument();
    expect(within(dialog).getByText("info@artisjet-printers.eu")).toBeInTheDocument();
    const para = within(dialog).getByLabelText("Para");
    await user.clear(para);
    await user.type(para, "otra@lamaison.fr");
    await user.click(within(dialog).getByRole("button", { name: "Enviar aviso" }));
    await waitFor(() => expect(mockSendEmail).toHaveBeenCalledWith("o-1", {
      to: "otra@lamaison.fr", lang: undefined,
    }));
    expect(await screen.findByLabelText("Aviso de envío al cliente"))
      .toHaveTextContent("otra@lamaison.fr");
  });
});

