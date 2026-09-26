import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OrderShipmentSections } from "./ExternalShipmentSection";
import { getOrderShipment, setOrderTracking, type OrderShipmentInfo } from "../../lib/erpApi";
import { getCustomerEmailPreview, sendCustomerEmail } from "../../lib/geneiApi";

jest.mock("../../lib/erpApi", () => ({
  getOrderShipment: jest.fn(),
  setOrderTracking: jest.fn(),
}));

jest.mock("../../lib/geneiApi", () => ({
  ...jest.requireActual("../../lib/geneiApi"),
  getCustomerEmailPreview: jest.fn(),
  sendCustomerEmail: jest.fn(),
}));

// La sección de Genei tiene sus propios tests: aquí solo importa si se pinta.
jest.mock("./GeneiShipmentSection", () => ({
  ...jest.requireActual("./GeneiShipmentSection"),
  GeneiShipmentSection: () => <section aria-label="Envío con Genei">GENEI</section>,
}));

const mockInfo = getOrderShipment as jest.Mock;
const mockSetTracking = setOrderTracking as jest.Mock;
const mockPreview = getCustomerEmailPreview as jest.Mock;
const mockSend = sendCustomerEmail as jest.Mock;

const UPS_TRACK = "1ZFV12345678901234";
const UPS_URL = `https://www.ups.com/track?tracknum=${UPS_TRACK}`;

function info(over: Partial<OrderShipmentInfo> = {}): OrderShipmentInfo {
  return {
    order_id: "o1", kind: "externo", courier: "UPS", tracking: UPS_TRACK,
    tracking_url: UPS_URL, transport_status: "in_transit",
    picked_up_at: "2026-09-25T09:30:00+00:00",
    customer_email: { status: "sent", to: "envios@cliente.de", lang: "de",
                      from: "info@artisjet-printers.eu", sent_at: "2026-09-25T09:31:00+00:00" },
    suggested_courier: "UPS", couriers: ["UPS", "MRW"], ...over,
  };
}

beforeEach(() => {
  [mockInfo, mockSetTracking, mockPreview, mockSend].forEach((m) => m.mockReset());
});

describe("ficha · envío con OTRO courier", () => {
  it("courier, tracking enlazado, fecha de recogida y aviso al cliente; sin «Envío con Genei»", async () => {
    mockInfo.mockResolvedValue(info());
    render(<OrderShipmentSections orderId="o1" canManage />);
    const sec = await screen.findByRole("region", { name: "Envío con otro courier" });
    expect(within(sec).getByText("Enviado · UPS")).toHaveClass("badge", "courier-ext");
    expect(within(sec).getByText("UPS")).toBeInTheDocument();
    expect(within(sec).getByRole("link", { name: UPS_TRACK })).toHaveAttribute("href", UPS_URL);
    expect(within(sec).getByText("Recogido")).toBeInTheDocument();
    expect(within(sec).getByLabelText("Aviso de envío al cliente"))
      .toHaveTextContent(/enviado .*a envios@cliente\.de en alemán desde info@artisjet-printers\.eu/);
    expect(within(sec).getByRole("button", { name: "Reenviar aviso al cliente" })).toBeEnabled();
    expect(screen.queryByRole("region", { name: "Envío con Genei" })).not.toBeInTheDocument();
  });

  it("sin courier ni tracking: «otro courier», sin enlace, y el aviso espera al nº", async () => {
    mockInfo.mockResolvedValue(info({ courier: null, tracking: null, tracking_url: null,
                                      customer_email: { status: "pending" } }));
    render(<OrderShipmentSections orderId="o1" canManage />);
    const sec = await screen.findByRole("region", { name: "Envío con otro courier" });
    expect(within(sec).getByText("Enviado · otro courier")).toBeInTheDocument();
    expect(within(sec).getByText("Sin indicar (otro courier)")).toBeInTheDocument();
    expect(within(sec).queryByRole("link")).not.toBeInTheDocument();
    expect(within(sec).getByLabelText("Aviso de envío al cliente"))
      .toHaveTextContent("en cuanto el envío tenga nº de seguimiento");
    expect(within(sec).getByRole("button", { name: "Enviar aviso al cliente" })).toBeDisabled();
  });

  it("✎ corrige courier y tracking desde la ficha y recarga", async () => {
    mockInfo.mockResolvedValueOnce(info({ courier: null, tracking: null, tracking_url: null }))
      .mockResolvedValue(info());
    mockSetTracking.mockResolvedValue({ id: "o1", tracking_number: UPS_TRACK, courier: "UPS" });
    const onChanged = jest.fn();
    const user = userEvent.setup();
    render(<OrderShipmentSections orderId="o1" canManage onChanged={onChanged} />);
    await user.click(await screen.findByRole("button", { name: /Editar courier \/ seguimiento/ }));
    const editor = screen.getByRole("group", { name: "Courier y seguimiento" });
    await user.type(within(editor).getByLabelText("Nº de seguimiento"), UPS_TRACK);
    expect(within(editor).getByLabelText("Courier")).toHaveValue("UPS");
    await user.click(within(editor).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockSetTracking).toHaveBeenCalledWith("o1", UPS_TRACK, "UPS"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(await screen.findByRole("link", { name: UPS_TRACK })).toHaveAttribute("href", UPS_URL);
  });

  it("reenviar el aviso a mano usa la vista previa (con el enlace de UPS) y recarga", async () => {
    mockInfo.mockResolvedValue(info());
    mockPreview.mockResolvedValue({
      to: "envios@cliente.de", from_alias: "info@artisjet-printers.eu", from_alias_source: "idioma",
      store: null, lang: "de", lang_source: "pais_destino", subject: "Ihre Sendung",
      body_text: `Verfolgen: ${UPS_URL}`, tracking: UPS_TRACK, tracking_url: UPS_URL,
      courier: "UPS", order_ref: "BOP-1", missing: [], status: { status: "sent" },
    });
    mockSend.mockResolvedValue({ order_id: "o1", state: {} });
    const user = userEvent.setup();
    render(<OrderShipmentSections orderId="o1" canManage />);
    await user.click(await screen.findByRole("button", { name: "Reenviar aviso al cliente" }));
    const dialog = await screen.findByRole("dialog", { name: "Aviso de envío al cliente" });
    expect(await within(dialog).findByText(`Verfolgen: ${UPS_URL}`)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Enviar aviso" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledWith(
      "o1", { to: "envios@cliente.de", lang: undefined },
    ));
    await waitFor(() => expect(mockInfo).toHaveBeenCalledTimes(2));
  });

  it("sin permiso: se ve el envío, pero no se edita ni se reenvía", async () => {
    mockInfo.mockResolvedValue(info());
    render(<OrderShipmentSections orderId="o1" canManage={false} />);
    await screen.findByRole("region", { name: "Envío con otro courier" });
    expect(screen.queryByRole("button", { name: /Editar courier/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /aviso al cliente/ })).not.toBeInTheDocument();
  });
});

describe("ficha · envío de Genei o sin envío: como siempre", () => {
  it("envío de Genei → solo la sección de Genei", async () => {
    mockInfo.mockResolvedValue(info({ kind: "genei", courier: "GLS" }));
    render(<OrderShipmentSections orderId="o1" canManage />);
    expect(await screen.findByRole("region", { name: "Envío con Genei" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Envío con otro courier" })).not.toBeInTheDocument();
  });

  it("aún sin envío → la sección de Genei (para crearlo)", async () => {
    mockInfo.mockResolvedValue(info({ kind: null, courier: null, tracking: null,
                                      tracking_url: null, transport_status: "not_shipped",
                                      picked_up_at: null, customer_email: null }));
    render(<OrderShipmentSections orderId="o1" canManage />);
    expect(await screen.findByRole("region", { name: "Envío con Genei" })).toBeInTheDocument();
  });

  it("si no se puede saber el envío, se enseña la sección de Genei de siempre", async () => {
    mockInfo.mockRejectedValue(new Error("boom"));
    render(<OrderShipmentSections orderId="o1" canManage />);
    expect(await screen.findByRole("region", { name: "Envío con Genei" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
