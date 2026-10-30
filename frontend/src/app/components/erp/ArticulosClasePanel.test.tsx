import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ArticulosClasePanel } from "./ArticulosClasePanel";

/** Los artículos con clase propia en Histórico → Familias. Figurantes. */

jest.mock("../../lib/erpApi", () => ({
  HISTORICO_CLASE_LABEL: jest.requireActual("../../lib/erpApi").HISTORICO_CLASE_LABEL,
  getArticulosClase: jest.fn(),
  setClaseArticulo: jest.fn(),
}));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

const SENSOR = {
  codart: "3302", clase: "accesorio", por: "x", at: null, nombre: "Sensor Figurante 3D",
  familia: "LAS", familia_nombre: "Láseres figurante", clase_familia: "maquina",
};

test("enseña cada artículo con su familia y se cambia o se quita", async () => {
  const user = userEvent.setup();
  api().getArticulosClase.mockResolvedValue({ items: [SENSOR] });
  api().setClaseArticulo.mockResolvedValueOnce({ items: [{ ...SENSOR, clase: "recambio" }] })
    .mockResolvedValueOnce({ items: [] });
  render(<ArticulosClasePanel puedeEditar />);
  expect(await screen.findByText("Sensor Figurante 3D")).toBeInTheDocument();
  expect(screen.getByText(/· Máquina/)).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Clase de 3302"), "recambio");
  expect(api().setClaseArticulo).toHaveBeenCalledWith("3302", "recambio");
  await user.click(screen.getByRole("button", { name: "Volver a la de su familia" }));
  expect(api().setClaseArticulo).toHaveBeenLastCalledWith("3302", null);
  expect(await screen.findByText(/Ninguno/)).toBeInTheDocument();
});

test("quien no configura solo lo ve", async () => {
  api().getArticulosClase.mockResolvedValue({ items: [SENSOR] });
  render(<ArticulosClasePanel puedeEditar={false} />);
  expect(await screen.findByText("Accesorio")).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
