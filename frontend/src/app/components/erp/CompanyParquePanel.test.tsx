import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyParquePanel } from "./CompanyParquePanel";

/** Parque y compras de una empresa en su ficha. Figurantes. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));
jest.mock("../../lib/erpApi", () => ({
  fichaDocumentoHref: jest.requireActual("../../lib/erpApi").fichaDocumentoHref,
  getParqueEmpresa: jest.fn(),
  getComprasEmpresa: jest.fn(),
  getHistoricoFamilias: jest.fn(),
  anadirMaquinaParque: jest.fn(),
  borrarMaquinaParque: jest.fn(),
  darBajaMaquina: jest.fn(),
  deshacerBajaMaquina: jest.fn(),
}));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

const MAQ = (over: Record<string, unknown>) => ({
  origen: "factura", clave: "2026|factura|5|260001|1|1712", id: null, codart: "1712",
  articulo: "Láser Figurante 1390", familia: "LAS", gama: "laser", gama_nombre: "Láser",
  fecha: "2026-03-10", fecha_original: null, ejercicio: "2026", cantidad: 1,
  documento: { tipo: "factura", serie: "5", codigo: 260001, numero: "5-260001" }, baja: null,
  ...over,
});

const PARQUE = {
  codcli: "4101", sin_cliente_factusol: false, activas: 2,
  maquinas: [
    MAQ({}),
    MAQ({ origen: "manual", clave: null, id: "m-1", codart: null, articulo: "Láser de 2012",
          fecha: "2012-06-01", ejercicio: "2012", documento: null }),
    MAQ({ clave: "2017|factura|1|170001|2|0917", articulo: "Plotter Antiguo X-70", codart: "0917",
          ejercicio: "2017", fecha: "2017-04-20",
          documento: { tipo: "factura", serie: "1", codigo: 170001, numero: "1-170001" },
          baja: { at: "2026-10-30T10:00:00Z", motivo: "Desguazada", por: "x" } }),
  ],
};

const COMPRAS = {
  codcli: "4101", sin_cliente_factusol: false, ejercicio: "2026", en_curso: "2026",
  ejercicios: ["2026", "2017"],
  documentos: [{ tipo: "factura", tipo_label: "Factura", ejercicio: "2026", serie: "5", codigo: 260001,
                 numero: "5-260001", fecha: "2026-03-10", fecha_original: null, total: 1089,
                 estado_label: "Cobrada" }],
};

beforeEach(() => {
  api().getParqueEmpresa.mockResolvedValue(PARQUE);
  api().getComprasEmpresa.mockResolvedValue(COMPRAS);
  api().getHistoricoFamilias.mockResolvedValue({ items: [], clases: [], gamas: [
    { codigo: "laser", nombre: "Láser" }] });
  api().darBajaMaquina.mockResolvedValue({ ...PARQUE, activas: 1 });
  api().anadirMaquinaParque.mockResolvedValue({ id: "m-2" });
});

afterEach(() => jest.clearAllMocks());

test("enseña sus máquinas con gama, fecha, de dónde salen y las de baja", async () => {
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  const maquinas = await screen.findByRole("region", { name: "Máquinas" });
  expect(await within(maquinas).findByText(/2 en servicio/)).toBeInTheDocument();
  const fila = within(maquinas).getAllByRole("row").find((r) => within(r).queryByText("Láser Figurante 1390"))!;
  expect(within(fila).getByRole("link", { name: /Factura 5-260001/ }))
    .toHaveAttribute("href", "/erp/documentos/ficha/factura/2026/5/260001");
  expect(within(maquinas).getByText("Añadida a mano")).toBeInTheDocument();
  expect(within(maquinas).getByText(/Dada de baja: Desguazada/)).toBeInTheDocument();
});

test("dar de baja con motivo y añadir una a mano", async () => {
  const user = userEvent.setup();
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  const maquinas = await screen.findByRole("region", { name: "Máquinas" });
  await within(maquinas).findByText(/2 en servicio/);
  const fila = within(maquinas).getAllByRole("row").find((r) => within(r).queryByText("Láser Figurante 1390"))!;
  await user.click(within(fila).getByRole("button", { name: "Dar de baja" }));
  await user.type(within(fila).getByLabelText("Motivo de la baja"), "Vendida");
  await user.click(within(fila).getByRole("button", { name: "Confirmar baja" }));
  expect(api().darBajaMaquina).toHaveBeenCalledWith("emp-1", { clave: "2026|factura|5|260001|1|1712" }, "Vendida");
  await user.click(screen.getByRole("button", { name: "Añadir una máquina a mano" }));
  await user.type(screen.getByLabelText("Máquina"), "Láser de otro proveedor");
  await user.selectOptions(screen.getByLabelText("Gama"), "laser");
  await user.click(screen.getByRole("button", { name: "Añadir" }));
  expect(api().anadirMaquinaParque).toHaveBeenCalledWith("emp-1", expect.objectContaining({
    descripcion: "Láser de otro proveedor", gama: "laser" }));
});

test("compras: arranca en el ejercicio en curso y «ver todo el histórico» trae lo anterior", async () => {
  const user = userEvent.setup();
  render(<CompanyParquePanel companyId="emp-1" puedeEditar={false} />);
  const compras = await screen.findByRole("region", { name: "Qué ha comprado" });
  expect(await within(compras).findByRole("link", { name: /Factura 5-260001/ })).toBeInTheDocument();
  expect(api().getComprasEmpresa).toHaveBeenCalledWith("emp-1", undefined);
  await user.click(within(compras).getByRole("button", { name: "Ver todo el histórico" }));
  await waitFor(() => expect(api().getComprasEmpresa).toHaveBeenLastCalledWith("emp-1", "todos"));
  // Sin permiso de pedidos: ni dar de baja ni añadir.
  expect(screen.queryByRole("button", { name: "Dar de baja" })).not.toBeInTheDocument();
});

test("una empresa sin cliente de FACTUSOL lo dice", async () => {
  api().getParqueEmpresa.mockResolvedValue({ codcli: null, sin_cliente_factusol: true,
    activas: 0, maquinas: [] });
  api().getComprasEmpresa.mockResolvedValue({ ...COMPRAS, codcli: null, sin_cliente_factusol: true,
    documentos: [], ejercicios: [] });
  render(<CompanyParquePanel companyId="emp-1" puedeEditar={false} />);
  expect(await screen.findByText(/no tiene cliente de FACTUSOL vinculado/)).toBeInTheDocument();
});

test("si no se puede añadir, el formulario sigue abierto con lo escrito", async () => {
  const user = userEvent.setup();
  api().anadirMaquinaParque.mockRejectedValueOnce(new Error("Esa gama no existe."));
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  await screen.findByText(/2 en servicio/);
  await user.click(screen.getByRole("button", { name: "Añadir una máquina a mano" }));
  await user.type(screen.getByLabelText("Máquina"), "Láser de otro proveedor");
  await user.click(screen.getByRole("button", { name: "Añadir" }));
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(screen.getByLabelText("Máquina")).toHaveValue("Láser de otro proveedor");
});

test("si fallan las compras de un año no se quedan las del anterior a la vista", async () => {
  const user = userEvent.setup();
  render(<CompanyParquePanel companyId="emp-1" puedeEditar={false} />);
  const compras = await screen.findByRole("region", { name: "Qué ha comprado" });
  await within(compras).findByText("5-260001");
  api().getComprasEmpresa.mockRejectedValueOnce(new Error("Caído"));
  await user.selectOptions(within(compras).getByLabelText("Ejercicio"), "2017");
  expect(await within(compras).findByRole("alert")).toBeInTheDocument();
  expect(within(compras).queryByText("5-260001")).not.toBeInTheDocument();
  await user.click(within(compras).getByRole("button", { name: "Volver al ejercicio en curso" }));
  await waitFor(() => expect(within(compras).getByText("5-260001")).toBeInTheDocument());
});
