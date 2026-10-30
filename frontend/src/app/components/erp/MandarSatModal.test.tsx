import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MandarSatModal } from "./MandarSatModal";

/** «Mandar a SAT» con la casilla del correo al taller. Figurantes. */

jest.mock("../../lib/erpApi", () => ({
  getOrderEmailPreview: jest.fn(),
  mandarASat: jest.fn(),
}));
jest.mock("./SenderSelect", () => ({
  SenderSelect: ({ value }: { value: string }) => <span>Remitente {value}</span>,
}));
jest.mock("../useModalBehaviour", () => ({
  useModalBehaviour: () => ({ overlayProps: {}, requestClose: jest.fn() }),
}));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

const PREVIEW = {
  order_id: "o1", order_number: "FAC-5-000008", cliente: "Talleres Figurante", lang: "es",
  to: ["taller@example.com"], sat_email: "taller@example.com", sat_configured: true,
  subject: "Pedido FAC-5-000008", body_text: "Va para preparar.", from_alias: "ventas@example.com",
  attachments: {
    albaran: { available: true, numero: "5-000008", reason: null },
    pedido: { available: true, numero: "FAC-5-000008", reason: null },
    factura: { available: true, numero: "5-000008", reason: null },
  },
  defaults: { albaran: true, pedido: false, factura: false },
};

const ORDER = {
  id: "o1", order_number: "FAC-5-000008", company_name: "Talleres Figurante",
  payment_status: "pending", factusol_albaran_number: "5-000008",
  lines: [{ id: "l1", quantity: 1, description: "Láser Figurante 1390", product_sku: "1712",
            is_shipping: false }],
  mandar_sat: { desde_documento: true, albaran_lo_crea_bohub: false, factura_sin_albaran: false,
                correo: null },
} as never;

const RESULT = { cola: "entrado", albaran: { estado: "ya_tiene", crear: false }, correo: null,
                 order: ORDER };

beforeEach(() => {
  api().getOrderEmailPreview.mockResolvedValue(PREVIEW);
  api().mandarASat.mockResolvedValue(RESULT);
});

test("con la casilla marcada manda al SAT y el correo al taller en una sola acción", async () => {
  const user = userEvent.setup();
  const onDone = jest.fn();
  render(<MandarSatModal order={ORDER} puedeEscribirAlTaller onClose={jest.fn()} onDone={onDone} />);
  expect(screen.getByText(/Láser Figurante 1390/)).toBeInTheDocument();
  expect(screen.getByText(/se prepara y se envía igual/)).toBeInTheDocument();
  expect(await screen.findByDisplayValue("taller@example.com")).toBeInTheDocument();
  expect(screen.getByLabelText("Avisar al taller por correo")).toBeChecked();
  // Sin factura: no hay ni casilla para adjuntarla.
  expect(screen.queryByLabelText("Adjuntar factura")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Mandar a SAT y avisar al taller" }));
  expect(api().mandarASat).toHaveBeenCalledWith("o1", expect.objectContaining({
    to: ["taller@example.com"], include_albaran: true, include_factura: false,
  }));
  await waitFor(() => expect(onDone).toHaveBeenCalledWith(RESULT));
});

test("sin marcarla no se manda ningún correo", async () => {
  const user = userEvent.setup();
  render(<MandarSatModal order={ORDER} puedeEscribirAlTaller onClose={jest.fn()} onDone={jest.fn()} />);
  await screen.findByDisplayValue("taller@example.com");
  await user.click(screen.getByLabelText("Avisar al taller por correo"));
  expect(screen.getByText("No se manda ningún correo.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Mandar a SAT" }));
  expect(api().mandarASat).toHaveBeenCalledWith("o1", null);
});

test("sin dirección del taller la casilla viene sin marcar", async () => {
  api().getOrderEmailPreview.mockResolvedValue({ ...PREVIEW, to: [], sat_configured: false });
  render(<MandarSatModal order={ORDER} puedeEscribirAlTaller onClose={jest.fn()} onDone={jest.fn()} />);
  await waitFor(() => expect(screen.getByLabelText("Avisar al taller por correo")).not.toBeChecked());
});

test("desde una factura sin albarán dice que no se crea ninguno", () => {
  const order = { ...(ORDER as object), factusol_albaran_number: null,
                  mandar_sat: { desde_documento: true, albaran_lo_crea_bohub: false,
                                factura_sin_albaran: true, correo: null } } as never;
  render(<MandarSatModal order={order} puedeEscribirAlTaller={false} onClose={jest.fn()} onDone={jest.fn()} />);
  expect(screen.getByText(/no se crea ninguno/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Avisar al taller por correo")).not.toBeInTheDocument();
});
