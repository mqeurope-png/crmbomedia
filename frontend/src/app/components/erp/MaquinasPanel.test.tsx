import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MaquinasPanel } from "./MaquinasPanel";

/** Máquinas artículo a artículo. Figurantes. */

jest.mock("../../lib/erpApi", () => ({
  getHistoricoMaquinas: jest.fn(),
  marcarHistoricoMaquina: jest.fn(),
  aceptarPropuestasMaquinas: jest.fn(),
  crearPistaArticulo: jest.fn(),
  borrarPistaArticulo: jest.fn(),
}));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

const ART = (codart: string, desc: string, over: Record<string, unknown> = {}) => ({
  codart, descripcion: desc, familia: "021", familia_nombre: "Cortadoras figurante",
  gama: "laser", lineas: 10, estado: "sin_revisar", clase_propia: null,
  propuesta: "maquina", pista: null, ...over,
});

const DATOS = {
  items: [
    ART("5001", "FLUX BEAMBOX PRO 50W", { estado: "maquina", lineas: 30 }),
    ART("5002", "FLUX FOCUSING LENS", { propuesta: "no_maquina", pista: "LENS", lineas: 20 }),
    ART("5003", "SPARE PARTS PP100 HOLDER CARTRIDGE", { propuesta: "no_maquina", pista: "SPARE PARTS" }),
  ],
  marcadas: 1, total: 3, propuestas: 2,
  reglas: [{ id: 1, patron: "LENS", orden: 100 }],
};

beforeEach(() => {
  api().getHistoricoMaquinas.mockResolvedValue(DATOS);
});

test("enseña el recuento, la propuesta y marca con la casilla", async () => {
  const user = userEvent.setup();
  api().marcarHistoricoMaquina.mockResolvedValue({ ...DATOS, marcadas: 2 });
  render(<MaquinasPanel puedeEditar />);
  expect(await screen.findByText("1 de 3")).toBeInTheDocument();
  expect(screen.getByText("no · LENS")).toBeInTheDocument();
  await user.click(screen.getByLabelText("Es máquina 5002"));
  expect(api().marcarHistoricoMaquina).toHaveBeenCalledWith("5002", true);
  expect(await screen.findByText("2 de 3")).toBeInTheDocument();
});

test("aceptar todas las propuestas", async () => {
  const user = userEvent.setup();
  api().aceptarPropuestasMaquinas.mockResolvedValue({
    ...DATOS, propuestas: 0, aplicadas: { maquina: 0, no_maquina: 2 } });
  render(<MaquinasPanel puedeEditar />);
  await user.click(await screen.findByRole("button", { name: /Aceptar todas las propuestas \(2\)/ }));
  expect(await screen.findByText(/0 como máquina y 2 como no máquina/)).toBeInTheDocument();
});

test("solo sin revisar filtra y quien no configura no marca", async () => {
  const user = userEvent.setup();
  render(<MaquinasPanel puedeEditar={false} />);
  await screen.findByText("1 de 3");
  expect(screen.getByLabelText("Es máquina 5001")).toBeDisabled();
  expect(screen.queryByRole("button", { name: /Aceptar todas/ })).not.toBeInTheDocument();
  await user.click(screen.getByLabelText("Solo sin revisar"));
  const tabla = screen.getByRole("table");
  await waitFor(() => expect(within(tabla).queryByText("FLUX BEAMBOX PRO 50W")).not.toBeInTheDocument());
});
