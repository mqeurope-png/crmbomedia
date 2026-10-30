import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VincularAlbaranPanel } from "./VincularAlbaranPanel";

/** Vincular un albarán desde la ficha del pedido. Figurantes. */

jest.mock("../../lib/erpApi", () => ({
  getAlbaranesCandidatos: jest.fn(),
  vincularAlbaranPedido: jest.fn(),
}));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

const ALB = (codigo: number, extra: Record<string, unknown> = {}) => ({
  serie: "5", codigo, numero: `5-${String(codigo).padStart(6, "0")}`, ejercicio: "2026",
  fecha: "2026-09-20", cliente_nombre: "Talleres Figurante", codcli: "4101", total: 121,
  viene_de: "Proforma 1-000533", de_la_factura: false, ...extra,
});

beforeEach(() => {
  api().getAlbaranesCandidatos.mockResolvedValue({
    codcli: "4101", de_la_factura: ["5-000031"],
    items: [ALB(31, { de_la_factura: true }), ALB(33), ALB(32)],
  });
  api().vincularAlbaranPedido.mockResolvedValue({ id: "o1", factusol_albaran_number: "5-000033" });
});

test("propone los del cliente con su origen y vincula uno", async () => {
  const user = userEvent.setup();
  const onLinked = jest.fn();
  render(<VincularAlbaranPanel orderId="o1" aviso="Viene de una factura y no tiene albarán"
                               onLinked={onLinked} />);
  expect(screen.getByText(/no tiene albarán/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Vincular un albarán que ya existe" }));
  expect(await screen.findByText("5-000033")).toBeInTheDocument();
  expect(screen.getByText(/el de su factura/)).toBeInTheDocument();
  expect(screen.getAllByText("Proforma 1-000533")).toHaveLength(3);
  const fila = screen.getByText("5-000033").closest("tr")!;
  await user.click(fila.querySelector("button")!);
  expect(api().vincularAlbaranPedido).toHaveBeenCalledWith("o1", "5", 33);
  await waitFor(() => expect(onLinked).toHaveBeenCalled());
});

test("busca por número", async () => {
  const user = userEvent.setup();
  render(<VincularAlbaranPanel orderId="o1" onLinked={jest.fn()} />);
  await user.click(screen.getByRole("button", { name: "Vincular un albarán que ya existe" }));
  await screen.findByText("5-000033");
  api().getAlbaranesCandidatos.mockResolvedValueOnce({ codcli: "4101", de_la_factura: [], items: [] });
  await user.type(screen.getByLabelText("Buscar por número"), "5-40");
  await user.click(screen.getByRole("button", { name: "Buscar" }));
  expect(api().getAlbaranesCandidatos).toHaveBeenLastCalledWith("o1", "5-40");
  expect(await screen.findByText(/Ningún albarán libre con el número 5-40/)).toBeInTheDocument();
});
