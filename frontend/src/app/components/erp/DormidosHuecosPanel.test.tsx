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

const base = { maquinas: 1, ultima_maquina: "2022-05-01", meses_con_maquina: 54 };
const DATOS = {
  ajustes: { meses_dormido: 12, meses_gracia_hueco: 3 },
  hoy: "2026-11-01",
  resumen: {
    clientes_con_maquina: 3, maquinas_sin_gama: 2,
    sin_gama: [
      { codcli: "4709", nombre: null, codart: "M9", articulo: "MAQUINA FIGURANTE", familia: "FX", origen: "factura" },
      { codcli: "4709", nombre: null, codart: "M8", articulo: "OTRA FIGURANTE", familia: "FX", origen: "factura" },
    ],
    sectores: [{ sector: "Rotulación", clientes: 1 }, { sector: null, clientes: 1 }],
    por_gama: [
      { gama: "laser", gama_nombre: "Láser", clientes: 2, con_consumible: 1, dormidos: 1, huecos: 0 },
      { gama: "uv-led", gama_nombre: "UV LED", clientes: 1, con_consumible: 0, dormidos: 0, huecos: 1 },
    ],
  },
  dormidos: [{ ...base, codcli: "4701", nombre: "Cliente Figurante A", sector: "Rotulación",
               empresa: { id: "e1", nombre: "Figurante A" }, gama: "laser", gama_nombre: "Láser",
               articulos: ["BEAMBOX FIGURANTE"], ultima_compra: "2022-09-15", meses_sin_comprar: 49,
               compraba_12_meses: 450, gasto_total: 4450 }],
  huecos: [{ ...base, codcli: "4703", nombre: "Cliente Figurante C", sector: null, empresa: null,
             gama: "uv-led", gama_nombre: "UV LED", articulos: ["IMPRESORA UV FIGURANTE"],
             solo_recambios: false, gasto_total: 15000 }],
};

beforeEach(() => {
  api().getDormidosHuecos.mockResolvedValue(DATOS);
  api().guardarAjustesDormidos.mockResolvedValue(DATOS);
  api().downloadDormidosHuecosCsv.mockResolvedValue(new Blob(["x"]));
});

afterEach(() => jest.clearAllMocks());

test("enseña el resumen por gama, las máquinas sin gama, los dormidos y los huecos", async () => {
  render(<DormidosHuecosPanel puedeEditar />);
  const dormidos = await screen.findByRole("table", { name: "Dormidos" });
  expect(within(dormidos).getByText("Figurante A")).toHaveAttribute("href", "/companies/e1");
  expect(within(dormidos).getByText("49")).toBeInTheDocument();
  const huecos = screen.getByRole("table", { name: "Huecos" });
  expect(within(huecos).getByText("Cliente Figurante C")).toBeInTheDocument();
  expect(within(huecos).getByText("54")).toBeInTheDocument();
  expect(within(screen.getByRole("list", { name: "Máquinas sin gama" })).getByText("M9"))
    .toBeInTheDocument();
});

test("se parte por gama, sector, antigüedad y gasto, y el CSV exporta lo filtrado", async () => {
  const user = userEvent.setup();
  render(<DormidosHuecosPanel puedeEditar />);
  await screen.findByRole("table", { name: "Dormidos" });
  await user.selectOptions(screen.getByLabelText("Gama"), "laser");
  await user.selectOptions(screen.getByLabelText("Sector"), "-");
  await user.type(screen.getByLabelText("Meses con la máquina, mínimo"), "24");
  await user.type(screen.getByLabelText("Gasto total mínimo"), "3000");
  await user.click(screen.getByRole("button", { name: "Filtrar" }));
  const f = { gama: "laser", sector: "-", meses_min: 24, meses_max: undefined, gasto_min: 3000 };
  expect(api().getDormidosHuecos).toHaveBeenLastCalledWith(f);
  await user.click(screen.getAllByRole("button", { name: "Exportar CSV" })[1]);
  expect(api().downloadDormidosHuecosCsv).toHaveBeenCalledWith("huecos", f);
  await user.click(screen.getByRole("button", { name: "Quitar filtros" }));
  expect(api().getDormidosHuecos).toHaveBeenLastCalledWith({});
});

test("el umbral se cambia desde la pantalla; quien no configura no lo ve", async () => {
  const user = userEvent.setup();
  const { unmount } = render(<DormidosHuecosPanel puedeEditar />);
  await user.type(await screen.findByLabelText("Meses sin comprar para ser dormido"), "24");
  await user.click(screen.getByRole("button", { name: "Cambiar" }));
  expect(api().guardarAjustesDormidos).toHaveBeenCalledWith({ meses_dormido: 24 });
  unmount();
  render(<DormidosHuecosPanel puedeEditar={false} />);
  await screen.findByRole("table", { name: "Dormidos" });
  expect(screen.queryByRole("button", { name: "Cambiar" })).not.toBeInTheDocument();
});
