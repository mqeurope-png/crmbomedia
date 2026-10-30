import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DeshacerSat } from "./DeshacerSat";

/** «Deshacer envío al SAT» en la ficha. Figurantes. */

jest.mock("../../lib/erpApi", () => ({ deshacerEnvioSat: jest.fn() }));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

const ORDER = (prep: string, over: Record<string, unknown> = {}) => ({
  id: "o1", preparation_status: prep, transport_status: "not_shipped", completed: false, ...over,
}) as never;

test("en la cola se deshace con confirmación y motivo", async () => {
  const user = userEvent.setup();
  const onDone = jest.fn();
  api().deshacerEnvioSat.mockResolvedValue({ aviso_correo: null, order: ORDER("pending_review") });
  render(<DeshacerSat order={ORDER("in_queue")} onDone={onDone} />);
  await user.click(screen.getByRole("button", { name: "Deshacer envío al SAT" }));
  expect(screen.getByText(/vuelve a «Por revisar»/)).toBeInTheDocument();
  await user.type(screen.getByLabelText("Motivo"), "Aprobado sin querer");
  await user.click(screen.getByRole("button", { name: "Confirmar" }));
  expect(api().deshacerEnvioSat).toHaveBeenCalledWith("o1", "Aprobado sin querer");
  await waitFor(() => expect(onDone).toHaveBeenCalled());
});

test("si el taller ya lo prepara o lo embaló, lo dice y no se puede", () => {
  const { rerender } = render(<DeshacerSat order={ORDER("preparing")} onDone={jest.fn()} />);
  expect(screen.getByText(/ya está preparándolo/)).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  rerender(<DeshacerSat order={ORDER("packed")} onDone={jest.fn()} />);
  expect(screen.getByText(/ya lo ha embalado/)).toBeInTheDocument();
});

test("fuera de la cola no aparece", () => {
  const { container } = render(<DeshacerSat order={ORDER("pending_review")} onDone={jest.fn()} />);
  expect(container).toBeEmptyDOMElement();
});

test("un pedido ya enviado o completado no enseña nada", () => {
  const { container, rerender } = render(
    <DeshacerSat order={ORDER("packed", { transport_status: "in_transit" })} onDone={jest.fn()} />);
  expect(container).toBeEmptyDOMElement();
  rerender(<DeshacerSat order={ORDER("packed", { completed: true })} onDone={jest.fn()} />);
  expect(container).toBeEmptyDOMElement();
});
