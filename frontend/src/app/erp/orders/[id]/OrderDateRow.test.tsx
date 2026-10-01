import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OrderDateRow } from "./OrderDateRow";
import { changeOrderDate } from "../../../lib/erpApi";

/** «Fecha del pedido» en la ficha: se cambia en los pedidos que no vienen de la
 *  tienda; en uno web el botón está bloqueado y el porqué va en el tooltip. */

jest.mock("../../../lib/erpApi", () => ({ changeOrderDate: jest.fn() }));

beforeEach(() => jest.clearAllMocks());

it("un pedido manual cambia la fecha y recarga la ficha", async () => {
  const user = userEvent.setup();
  const onSaved = jest.fn();
  (changeOrderDate as jest.Mock).mockResolvedValue({ id: "o1", changed: true, placed_at: "2026-09-24" });
  render(<OrderDateRow orderId="o1" placedAt="2026-10-01T00:00:00+00:00" isWeb={false}
                       canEdit onSaved={onSaved} />);
  expect(screen.getByText("01/10/2026")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Cambiar fecha" }));
  fireEvent.change(screen.getByLabelText("Fecha del pedido"), { target: { value: "2026-09-24" } });
  await user.click(screen.getByRole("button", { name: "Guardar" }));
  await waitFor(() => expect(changeOrderDate).toHaveBeenCalledWith("o1", "2026-09-24"));
  expect(onSaved).toHaveBeenCalled();
});

it("en un pedido web no se puede cambiar (botón bloqueado con el porqué)", () => {
  render(<OrderDateRow orderId="w1" placedAt="2026-09-30T10:00:00+00:00" isWeb
                       canEdit onSaved={jest.fn()} />);
  const btn = screen.getByRole("button", { name: "Cambiar fecha" });
  expect(btn).toBeDisabled();
  expect(btn).toHaveAttribute("title", expect.stringMatching(/la de la tienda/));
});

it("sin permiso de edición solo se ve la fecha", () => {
  render(<OrderDateRow orderId="o2" placedAt={null} createdAt="2026-09-02T08:00:00Z"
                       isWeb={false} canEdit={false} onSaved={jest.fn()} />);
  expect(screen.getByText("02/09/2026")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cambiar fecha" })).not.toBeInTheDocument();
});
