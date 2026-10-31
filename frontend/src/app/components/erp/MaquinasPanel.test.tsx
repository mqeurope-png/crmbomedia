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
  cambiarUmbralMaquinas: jest.fn(),
  cambiarPalabraEnteraPista: jest.fn(),
  darPorRevisadasFamilia: jest.fn(),
  deshacerRevisadasFamilia: jest.fn(),
}));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

const ART = (codart: string, desc: string, over: Record<string, unknown> = {}) => ({
  codart, descripcion: desc, familia: "021", familia_nombre: "Cortadoras figurante",
  gama: "laser", lineas: 10, estado: "sin_revisar", clase_propia: null,
  propuesta: "maquina", pista_si: "BEAMBOX", pista_no: null, por_precio: false,
  precio_max: 100, propondria: null, en_lote: false, ...over,
});

const DATOS = {
  items: [
    ART("5001", "FLUX BEAMBOX PRO 50W", { estado: "maquina", lineas: 30, precio_max: 4200,
                                          propuesta: null }),
    ART("5002", "FLUX FOCUSING LENS", { propuesta: "no_maquina", pista_si: null, pista_no: "LENS",
                                        precio_max: 45 }),
    ART("5003", "A5000 UV FIGURANTE", { propuesta: "dudoso", pista_si: null, por_precio: true,
                                        precio_max: 12000 }),
    ART("5004", "LICENCIA FIGURANTE", { estado: "no_maquina", propuesta: null, precio_max: 3000,
                                        propondria: "dudoso" }),
    ART("5005", "EMBUDO FIGURANTE", { propuesta: null, pista_si: null, precio_max: null }),
  ],
  marcadas: 1, total: 5, propuestas: 1, sin_revisar: 3, umbral: 2000, colisiones: 1,
  reglas: [{ id: 1, patron: "LENS", orden: 100, tipo: "no_maquina", palabra_entera: false },
           { id: 2, patron: "HEXA", orden: 100, tipo: "maquina", palabra_entera: true }],
};

beforeEach(() => {
  api().getHistoricoMaquinas.mockResolvedValue(DATOS);
});

function codigos(): string[] {
  return within(screen.getByRole("table")).getAllByRole("row").slice(1)
    .map((r) => r.querySelector(".mono")?.textContent ?? "");
}

test("propuestas: lo revisado no lleva, el precio da dudosa y se ordena por precio", async () => {
  const user = userEvent.setup();
  render(<MaquinasPanel puedeEditar />);
  expect(await screen.findByText("1 de 5")).toBeInTheDocument();
  expect(screen.getByText("dudosa · precio")).toBeInTheDocument();
  expect(screen.getAllByText("revisada")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: /Precio máx\./ }));
  expect(codigos()).toEqual(["5003", "5001", "5004", "5002", "5005"]);     // desc, sin precio al final
  await user.click(screen.getByRole("button", { name: /Precio máx\./ }));
  expect(codigos()).toEqual(["5002", "5004", "5001", "5003", "5005"]);     // asc
});

test("«No es máquina» por fila y el filtro de las revisadas que hoy se propondrían", async () => {
  const user = userEvent.setup();
  api().marcarHistoricoMaquina.mockResolvedValue(DATOS);
  render(<MaquinasPanel puedeEditar />);
  await screen.findByText("1 de 5");
  const fila = screen.getByText("EMBUDO FIGURANTE").closest("tr")!;
  await user.click(within(fila).getByRole("button", { name: "No es máquina" }));
  expect(api().marcarHistoricoMaquina).toHaveBeenCalledWith("5005", false, true);
  await user.selectOptions(screen.getByLabelText("Ver"), "colisiones");
  await waitFor(() => expect(codigos()).toEqual(["5004"]));
  expect(screen.getByText(/hoy se propondría: dudosa/)).toBeInTheDocument();
});

test("dar por revisadas la familia dice cuántas y pide confirmación; y se deshace", async () => {
  const user = userEvent.setup();
  api().darPorRevisadasFamilia.mockResolvedValue({
    ...DATOS, revisadas: 3,
    items: DATOS.items.map((i) => (i.estado === "sin_revisar" ? { ...i, estado: "no_maquina", en_lote: true } : i)),
  });
  api().deshacerRevisadasFamilia.mockResolvedValue({ ...DATOS, deshechas: 3 });
  render(<MaquinasPanel puedeEditar />);
  await screen.findByText("1 de 5");
  await user.selectOptions(screen.getByLabelText("Familia"), "021");
  await user.click(screen.getByRole("button", { name: "Dar por revisadas las que quedan (3)" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Se darán por revisadas 3 filas de la familia 021");
  expect(api().darPorRevisadasFamilia).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Confirmar (3)" }));
  expect(api().darPorRevisadasFamilia).toHaveBeenCalledWith("021");
  expect(await screen.findByText(/3 dadas por revisadas/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Deshacer «dar por revisadas» \(3\)/ }));
  expect(api().deshacerRevisadasFamilia).toHaveBeenCalledWith("021");
});

test("el umbral se cambia sin tocar código", async () => {
  const user = userEvent.setup();
  api().cambiarUmbralMaquinas.mockResolvedValue({ ...DATOS, umbral: 1500 });
  render(<MaquinasPanel puedeEditar />);
  await screen.findByText("1 de 5");
  await user.type(screen.getByLabelText("Umbral de precio"), "1500");
  await user.click(screen.getByRole("button", { name: "Cambiar umbral" }));
  expect(api().cambiarUmbralMaquinas).toHaveBeenCalledWith(1500);
});

test("quien no configura no marca ni revisa", async () => {
  render(<MaquinasPanel puedeEditar={false} />);
  await screen.findByText("1 de 5");
  expect(screen.getByLabelText("Es máquina 5001")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "No es máquina" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Umbral de precio")).not.toBeInTheDocument();
});

test("la casilla «palabra entera» de una pista", async () => {
  const user = userEvent.setup();
  api().cambiarPalabraEnteraPista.mockResolvedValue(DATOS);
  render(<MaquinasPanel puedeEditar />);
  await screen.findByText("1 de 5");
  await user.click(screen.getByText(/Pistas de la propuesta/));
  expect(screen.getByLabelText("Palabra entera HEXA")).toBeChecked();
  await user.click(screen.getByLabelText("Palabra entera LENS"));
  expect(api().cambiarPalabraEnteraPista).toHaveBeenCalledWith(1, true);
});
