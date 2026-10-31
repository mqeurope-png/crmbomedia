import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DormidosHuecosPanel } from "./DormidosHuecosPanel";

/** Dormidos y huecos. Clientes FIGURANTES. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));
jest.mock("../../lib/erpApi", () => ({
  getDormidosHuecos: jest.fn(),
  guardarAjustesDormidos: jest.fn(),
  downloadDormidosHuecosCsv: jest.fn(),
  saveBlob: jest.fn(),
}));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

const base = { maquinas: 1, ultima_maquina: "2022-05-01" };
const DATOS = {
  ajustes: { meses_dormido: 12, meses_gracia_hueco: 3 },
  hoy: "2026-11-01",
  resumen: {
    clientes_con_maquina: 3, maquinas_sin_gama: 2,
    por_gama: [
      { gama: "laser", gama_nombre: "Láser", clientes: 2, con_consumible: 1, dormidos: 1, huecos: 0 },
      { gama: "uv-led", gama_nombre: "UV LED", clientes: 1, con_consumible: 0, dormidos: 0, huecos: 1 },
    ],
  },
  dormidos: [{ ...base, codcli: "4701", nombre: "Cliente Figurante A", empresa: { id: "e1", nombre: "Figurante A" },
               gama: "laser", gama_nombre: "Láser", articulos: ["BEAMBOX FIGURANTE"],
               ultima_compra: "2022-09-15", meses_sin_comprar: 49, compraba_12_meses: 450 }],
  huecos: [{ ...base, codcli: "4703", nombre: "Cliente Figurante C", empresa: null, gama: "uv-led",
             gama_nombre: "UV LED", articulos: ["IMPRESORA UV FIGURANTE"], meses_con_maquina: 54,
             solo_recambios: false }],
};

beforeEach(() => {
  api().getDormidosHuecos.mockResolvedValue(DATOS);
  api().guardarAjustesDormidos.mockResolvedValue({ ...DATOS, dormidos: [] });
  api().downloadDormidosHuecosCsv.mockResolvedValue(new Blob(["x"]));
});

test("enseña el resumen por gama, los dormidos y los huecos, y filtra por gama", async () => {
  const user = userEvent.setup();
  render(<DormidosHuecosPanel puedeEditar />);
  const dormidos = await screen.findByRole("table", { name: "Dormidos" });
  expect(within(dormidos).getByText("Figurante A")).toHaveAttribute("href", "/companies/e1");
  expect(within(dormidos).getByText("49")).toBeInTheDocument();
  expect(within(screen.getByRole("table", { name: "Huecos" })).getByText("Cliente Figurante C"))
    .toBeInTheDocument();
  expect(screen.getByText(/2 máquinas en servicio no tienen gama/)).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Gama"), "uv-led");
  expect(screen.queryByRole("table", { name: "Dormidos" })).not.toBeInTheDocument();
});

test("el umbral se cambia desde la pantalla y se exporta", async () => {
  const user = userEvent.setup();
  render(<DormidosHuecosPanel puedeEditar />);
  await user.type(await screen.findByLabelText("Meses sin comprar para ser dormido"), "24");
  await user.click(screen.getByRole("button", { name: "Cambiar" }));
  expect(api().guardarAjustesDormidos).toHaveBeenCalledWith({ meses_dormido: 24 });
  await user.click(screen.getAllByRole("button", { name: "Exportar CSV" })[1]);
  expect(api().downloadDormidosHuecosCsv).toHaveBeenCalledWith("huecos");
  expect(api().saveBlob).toHaveBeenCalled();
});

test("quien no configura no cambia el umbral", async () => {
  render(<DormidosHuecosPanel puedeEditar={false} />);
  await screen.findByRole("table", { name: "Dormidos" });
  expect(screen.queryByRole("button", { name: "Cambiar" })).not.toBeInTheDocument();
});
