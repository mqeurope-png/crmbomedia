import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HistoricoPage from "./page";

/** ERP · Quién compró qué. Clientes, artículos y documentos: FIGURANTES. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, prefetch, ...rest }: { children: React.ReactNode; href: string;
    prefetch?: boolean } & Record<string, unknown>) => (
    <a href={href} data-prefetch={String(prefetch)} {...rest}>{children}</a>
  ),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <header><h1>{title}</h1></header>,
}));
jest.mock("../../lib/erpApi", () => ({
  buscarHistoricoArticulos: jest.fn(),
  cargarHistorico: jest.fn(),
  clasificarHistoricoFamilia: jest.fn(),
  downloadHistoricoCompradoresCsv: jest.fn(),
  getHistoricoCompradores: jest.fn(),
  getHistoricoEstado: jest.fn(),
  getHistoricoFamilias: jest.fn(),
  saveBlob: jest.fn(),
}));
jest.mock("../../lib/api", () => ({ getCurrentUser: jest.fn() }));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

const ej = (ejercicio: string, over: Record<string, unknown> = {}) => ({
  ejercicio, estado: "cargado", cargado_at: "2026-10-30T05:21:00", intento_at: null,
  detalle: null, facturas: 10, albaranes: 5, lineas: 40, lineas_producto: 30, lineas_nota: 10,
  articulos: 100, segundos: 7.5, ...over,
});

const ESTADO = {
  en_curso: "2026",
  ejercicios: [
    ej("2026"),
    ej("2017"),
    ej("2015", { estado: "no_disponible", cargado_at: null, lineas: 0, lineas_nota: 0,
                 detalle: "FACTUSOL no tiene este ejercicio abierto para la API: reábrelo en el "
                   + "escritorio de FACTUSOL y vuelve a cargarlo." }),
  ],
  notas: { lineas: 80, producto: 60, notas: 20 },
};

const COMPRADORES = {
  total: 2,
  sin_empresa: 1,
  items: [
    {
      codcli: "4101", nombre: "Talleres Figurante S.L.",
      empresa: { id: "emp-1", nombre: "Talleres Figurante" },
      ultima_compra: { fecha: "2026-03-10", fecha_original: null, ejercicio: "2026",
                       tipo: "factura", documento: "5-260001" },
      unidades: 2, compras: 2,
      articulos: [{ codart: "1712", descripcion: "Láser Figurante 1390" }],
    },
    {
      codcli: "4102", nombre: "Rótulos de Ejemplo S.C.", empresa: null,
      ultima_compra: { fecha: null, fecha_original: "1900-01-01", ejercicio: "2017",
                       tipo: "factura", documento: "1-170002" },
      unidades: 3, compras: 1,
      articulos: [{ codart: "1712", descripcion: "Láser 1390 (2017)" }],
    },
  ],
};

const FAMILIAS = [
  { codigo: "LAS", descripcion: "Láseres", clase: null, articulos: 12, ventas: 300 },
  { codigo: "TIN", descripcion: "Tintas", clase: "consumible", articulos: 40, ventas: 900 },
];

beforeEach(() => {
  const m = api();
  m.getHistoricoEstado.mockResolvedValue(ESTADO);
  m.getHistoricoFamilias.mockResolvedValue({ items: FAMILIAS, clases: ["maquina", "consumible", "otro"] });
  m.buscarHistoricoArticulos.mockResolvedValue({ items: [
    { codart: "1712", descripcion: "Láser Figurante 1390", familia: "LAS",
      ultimo_ejercicio: "2026", ventas: 42 },
  ] });
  m.getHistoricoCompradores.mockResolvedValue(COMPRADORES);
  m.downloadHistoricoCompradoresCsv.mockResolvedValue(new Blob(["x"]));
  m.cargarHistorico.mockResolvedValue({ encolada: ["2015"] });
  m.clasificarHistoricoFamilia.mockResolvedValue({ codigo: "LAS", clase: "maquina" });
  (jest.requireMock("../../lib/api") as Record<string, jest.Mock>)
    .getCurrentUser.mockResolvedValue({ id: "u1", role: "admin" });
});

afterEach(() => jest.clearAllMocks());

test("buscar un artículo enseña compradores, última compra, unidades y quién no tiene empresa", async () => {
  const user = userEvent.setup();
  render(<HistoricoPage />);
  await user.type(screen.getByRole("searchbox"), "láser");
  await user.click(await screen.findByRole("option", { name: /1712/ }));
  await user.click(screen.getByRole("button", { name: "Buscar" }));

  await waitFor(() => expect(api().getHistoricoCompradores).toHaveBeenCalledWith(
    expect.objectContaining({ codart: "1712", q: undefined })));
  expect(await screen.findByText(/2 clientes · 1 sin empresa vinculada/)).toBeInTheDocument();
  const filas = screen.getAllByRole("row");
  const taller = filas.find((f) => within(f).queryByText(/Talleres Figurante S.L./))!;
  expect(within(taller).getByRole("link", { name: "Talleres Figurante" }))
    .toHaveAttribute("href", "/companies/emp-1");
  expect(within(taller).getByText("10/3/2026")).toBeInTheDocument();
  expect(within(taller).getByText("5-260001")).toBeInTheDocument();
  const rotulos = filas.find((f) => within(f).queryByText(/Rótulos de Ejemplo/))!;
  expect(within(rotulos).getByText("Sin empresa vinculada")).toBeInTheDocument();
  // La fecha imposible (1900) no rompe: sale el ejercicio.
  expect(within(rotulos).getByText(/2017 \(fecha no válida\)/)).toBeInTheDocument();
  expect(within(rotulos).getByText("3")).toBeInTheDocument();

  // El CSV es de lo que se ve, aunque luego se toque el formulario sin buscar.
  await user.selectOptions(screen.getByLabelText("Desde"), "2020");
  await user.click(screen.getByRole("button", { name: "Exportar CSV" }));
  await waitFor(() => expect(api().saveBlob).toHaveBeenCalledWith(expect.any(Blob), "quien_compro.csv"));
  expect(api().downloadHistoricoCompradoresCsv).toHaveBeenCalledWith(
    expect.objectContaining({ codart: "1712", desde: undefined }));
});

test("filtra por familia y por años", async () => {
  const user = userEvent.setup();
  render(<HistoricoPage />);
  await screen.findByRole("option", { name: /LAS · Láseres/ });
  await user.selectOptions(screen.getByLabelText("Familia"), "TIN");
  await user.selectOptions(screen.getByLabelText("Desde"), "2017");
  await user.selectOptions(screen.getByLabelText("Hasta"), "2020");
  await user.click(screen.getByRole("button", { name: "Buscar" }));
  await waitFor(() => expect(api().getHistoricoCompradores).toHaveBeenCalledWith(
    { codart: undefined, q: undefined, familia: "TIN", desde: 2017, hasta: 2020 }));
});

test("la carga dice qué ejercicio no está disponible y deja relanzarlo", async () => {
  const user = userEvent.setup();
  render(<HistoricoPage />);
  expect(await screen.findByText(/Un ejercicio no se ha podido cargar \(2015\)/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Carga" }));
  expect(screen.getByText(/20 son notas/)).toBeInTheDocument();
  const fila = screen.getAllByRole("row").find((f) => within(f).queryByText("2015"))!;
  expect(within(fila).getByText("No disponible en FACTUSOL")).toBeInTheDocument();
  expect(within(fila).getByText(/reábrelo en el escritorio de FACTUSOL/)).toBeInTheDocument();
  await user.click(within(fila).getByRole("button", { name: "Volver a cargar" }));
  expect(api().cargarHistorico).toHaveBeenCalledWith(["2015"]);
  await user.click(screen.getByRole("button", { name: "Cargar los 3 ejercicios" }));
  expect(api().cargarHistorico).toHaveBeenLastCalledWith([]);
});

test("las familias se clasifican desde la pantalla", async () => {
  const user = userEvent.setup();
  render(<HistoricoPage />);
  await user.click(screen.getByRole("button", { name: "Familias" }));
  expect(await screen.findByText(/2 familias de FACTUSOL · 1 sin clasificar/)).toBeInTheDocument();
  await user.selectOptions(await screen.findByLabelText("Clase de LAS"), "maquina");
  expect(api().clasificarHistoricoFamilia).toHaveBeenCalledWith("LAS", "maquina");
  expect(await screen.findByText(/todas clasificadas/)).toBeInTheDocument();
});

test("sin permiso de configuración, la clase se ve pero no se cambia", async () => {
  (jest.requireMock("../../lib/api") as Record<string, jest.Mock>)
    .getCurrentUser.mockResolvedValue({ id: "u2", role: "pedidos", capabilities: ["erp.office.view"] });
  const user = userEvent.setup();
  render(<HistoricoPage />);
  await user.click(screen.getByRole("button", { name: "Familias" }));
  expect(await screen.findByText("Consumible")).toBeInTheDocument();
  expect(screen.queryByLabelText("Clase de LAS")).not.toBeInTheDocument();
});
