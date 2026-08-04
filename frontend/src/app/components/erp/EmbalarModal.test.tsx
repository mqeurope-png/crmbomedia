import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmbalarModal } from "./EmbalarModal";
import { setPackages, transitionPacked } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  setPackages: jest.fn(),
  transitionPacked: jest.fn(),
}));
const mockSet = setPackages as jest.Mock;
const mockPacked = transitionPacked as jest.Mock;

beforeEach(() => {
  mockSet.mockReset();
  mockPacked.mockReset();
  mockSet.mockResolvedValue({ packages: [] });
  mockPacked.mockResolvedValue({ order_id: "o1", preparation_status: "packed" });
});

async function fillBulto(user: ReturnType<typeof userEvent.setup>, i: number,
                         vals: [string, string, string, string]) {
  await user.type(screen.getByLabelText(`Peso bulto ${i}`), vals[0]);
  await user.type(screen.getByLabelText(`Alto bulto ${i}`), vals[1]);
  await user.type(screen.getByLabelText(`Ancho bulto ${i}`), vals[2]);
  await user.type(screen.getByLabelText(`Fondo bulto ${i}`), vals[3]);
}

describe("EmbalarModal", () => {
  it("empieza con 1 bulto y permite añadir y eliminar", async () => {
    const user = userEvent.setup();
    render(<EmbalarModal orderId="o1" onDone={() => {}} onCancel={() => {}} />);
    expect(screen.getByLabelText("Bulto 1")).toBeInTheDocument();
    expect(screen.queryByLabelText("Bulto 2")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "+ Añadir bulto" }));
    expect(screen.getByLabelText("Bulto 2")).toBeInTheDocument();
    // El primer bulto no tiene botón Eliminar; el segundo sí.
    await user.click(screen.getByRole("button", { name: "Eliminar" }));
    expect(screen.queryByLabelText("Bulto 2")).not.toBeInTheDocument();
  });

  it("mantiene «Guardar» deshabilitado hasta rellenar peso + 3 medidas > 0", async () => {
    const user = userEvent.setup();
    render(<EmbalarModal orderId="o1" onDone={() => {}} onCancel={() => {}} />);
    const submit = screen.getByRole("button", { name: "Guardar y embalar" });
    expect(submit).toBeDisabled();
    await fillBulto(user, 1, ["2", "10", "10", "10"]);
    expect(submit).toBeEnabled();
  });

  it("envía los bultos en orden y transiciona a packed", async () => {
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<EmbalarModal orderId="o1" onDone={onDone} onCancel={() => {}} />);
    await fillBulto(user, 1, ["2", "10", "20", "30"]);
    await user.click(screen.getByRole("button", { name: "+ Añadir bulto" }));
    await fillBulto(user, 2, ["1", "5", "5", "5"]);
    await user.click(screen.getByRole("button", { name: "Guardar y embalar" }));

    await waitFor(() => expect(mockSet).toHaveBeenCalled());
    const [oid, pkgs] = mockSet.mock.calls[0];
    expect(oid).toBe("o1");
    expect(pkgs).toHaveLength(2);
    expect(pkgs[0]).toEqual({ weight_kg: 2, height_cm: 10, width_cm: 20, depth_cm: 30 });
    expect(pkgs[1].weight_kg).toBe(1);
    await waitFor(() => expect(mockPacked).toHaveBeenCalledWith("o1"));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("muestra error si la transición falla y no cierra", async () => {
    mockPacked.mockRejectedValue(new Error("boom"));
    const onDone = jest.fn();
    const user = userEvent.setup();
    render(<EmbalarModal orderId="o1" onDone={onDone} onCancel={() => {}} />);
    await fillBulto(user, 1, ["2", "10", "10", "10"]);
    await user.click(screen.getByRole("button", { name: "Guardar y embalar" }));
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });
});
