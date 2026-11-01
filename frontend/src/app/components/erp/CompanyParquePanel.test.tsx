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
  buscarMaquinasCatalogo: jest.fn(),
  contestarMismaVenta: jest.fn(),
  darBajaMaquina: jest.fn(),
  deshacerBajaMaquina: jest.fn(),
  deshacerDosCompras: jest.fn(),
  setClaseArticulo: jest.fn(),
  HISTORICO_CLASE_LABEL: jest.requireActual("../../lib/erpApi").HISTORICO_CLASE_LABEL,
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
  api().buscarMaquinasCatalogo.mockResolvedValue([]);
});

afterEach(() => jest.clearAllMocks());

test("enseña sus máquinas con gama, fecha, de dónde salen y las de baja", async () => {
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  const maquinas = await screen.findByRole("region", { name: "Máquinas" });
  expect(await within(maquinas).findByText(/2 en servicio/)).toBeInTheDocument();
  const fila = within(maquinas).getAllByRole("row").find((r) => within(r).queryByText("Láser Figurante 1390"))!;
  expect(within(fila).getByRole("link", { name: /Factura 5-260001/ }))
    .toHaveAttribute("href", "/erp/documentos/ficha/factura/2026/5/260001");
  // La escrita a mano se distingue de la del catálogo.
  expect(within(maquinas).getByText("Escrita a mano")).toBeInTheDocument();
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

const CATALOGO = [
  { codart: "A3000U", descripcion: "Impresora UV Figurante A3000U", familia: "UVM", gama: "uv-led",
    gama_nombre: "UV LED", equivalentes: [] },
  { codart: "ART-3000UP", descripcion: "Artis Figurante 3000U Pro", familia: "UVM", gama: "uv-led",
    gama_nombre: "UV LED", equivalentes: [] },
];

test("buscar «3000» ofrece las del catálogo y elegir una pone la gama de su familia", async () => {
  const user = userEvent.setup();
  api().buscarMaquinasCatalogo.mockResolvedValue(CATALOGO);
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  await screen.findByText(/2 en servicio/);
  await user.click(screen.getByRole("button", { name: "Añadir una máquina a mano" }));
  await user.type(screen.getByLabelText("Máquina"), "3000");
  const lista = await screen.findByRole("list", { name: "Máquinas del catálogo" });
  expect(api().buscarMaquinasCatalogo).toHaveBeenLastCalledWith("3000");
  expect(within(lista).getAllByRole("button")).toHaveLength(2);
  await user.click(within(lista).getByRole("button", { name: /A3000U/ }));
  expect(screen.getByRole("status", { name: "Máquina elegida" })).toHaveTextContent("Del catálogo");
  const gama = screen.getByLabelText("Gama");
  expect(gama).toHaveValue("uv-led");
  expect(gama).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Añadir" }));
  expect(api().anadirMaquinaParque).toHaveBeenCalledWith("emp-1", expect.objectContaining({
    codart: "A3000U", descripcion: "Impresora UV Figurante A3000U" }));
  // La gama no se manda: es la de su familia.
  expect(api().anadirMaquinaParque.mock.calls[0][1].gama).toBeUndefined();
});

test("del catálogo sin gama en su familia: se manda la que se elige", async () => {
  const user = userEvent.setup();
  api().buscarMaquinasCatalogo.mockResolvedValue([{ ...CATALOGO[0], gama: null, gama_nombre: null }]);
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  await screen.findByText(/2 en servicio/);
  await user.click(screen.getByRole("button", { name: "Añadir una máquina a mano" }));
  await user.type(screen.getByLabelText("Máquina"), "3000");
  const lista = await screen.findByRole("list", { name: "Máquinas del catálogo" });
  await user.click(within(lista).getByRole("button", { name: /A3000U/ }));
  expect(screen.getByText(/Su familia no tiene gama/)).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText(/Gama/), "laser");
  await user.click(screen.getByRole("button", { name: "Añadir" }));
  expect(api().anadirMaquinaParque.mock.calls[0][1]).toEqual(expect.objectContaining({
    codart: "A3000U", gama: "laser" }));
});

test("escribir el código exacto y añadir sin pinchar la sugerencia la vincula", async () => {
  const user = userEvent.setup();
  api().buscarMaquinasCatalogo.mockResolvedValue(CATALOGO);
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  await screen.findByText(/2 en servicio/);
  await user.click(screen.getByRole("button", { name: "Añadir una máquina a mano" }));
  await user.type(screen.getByLabelText("Máquina"), "a3000u");
  expect(await screen.findByText(/Es el código de una del catálogo/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Añadir" }));
  expect(api().anadirMaquinaParque.mock.calls[0][1]).toEqual(expect.objectContaining({
    codart: "A3000U" }));
});

test("si falla la búsqueda no dice que no está en el catálogo", async () => {
  const user = userEvent.setup();
  api().buscarMaquinasCatalogo.mockRejectedValue(new Error("500"));
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  await screen.findByText(/2 en servicio/);
  await user.click(screen.getByRole("button", { name: "Añadir una máquina a mano" }));
  await user.type(screen.getByLabelText("Máquina"), "3000");
  expect(await screen.findByText(/No se ha podido buscar en el catálogo/)).toBeInTheDocument();
  expect(screen.queryByText(/Ninguna máquina del catálogo casa/)).not.toBeInTheDocument();
});

test("si el catálogo no la tiene, se añade escrita a mano y se avisa", async () => {
  const user = userEvent.setup();
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  await screen.findByText(/2 en servicio/);
  await user.click(screen.getByRole("button", { name: "Añadir una máquina a mano" }));
  await user.type(screen.getByLabelText("Máquina"), "Plotter de otra marca");
  expect(await screen.findByText(/Ninguna máquina del catálogo casa/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Añadir" }));
  expect(api().anadirMaquinaParque.mock.calls[0][1]).toEqual(expect.objectContaining({
    descripcion: "Plotter de otra marca" }));
  expect(api().anadirMaquinaParque.mock.calls[0][1]).not.toHaveProperty("codart");
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


test("«No es una máquina» pone la clase del artículo y vuelve a leer el parque", async () => {
  const user = userEvent.setup();
  api().setClaseArticulo.mockResolvedValue({ items: [] });
  render(<CompanyParquePanel companyId="emp-1" puedeEditar={false} puedeClasificar />);
  const maquinas = await screen.findByRole("region", { name: "Máquinas" });
  await within(maquinas).findByText(/2 en servicio/);
  const fila = within(maquinas).getAllByRole("row").find((r) => within(r).queryByText("Láser Figurante 1390"))!;
  // Sin permiso de pedidos no se da de baja, pero sí se reclasifica.
  expect(within(fila).queryByRole("button", { name: "Dar de baja" })).not.toBeInTheDocument();
  await user.click(within(fila).getByRole("button", { name: "No es una máquina" }));
  expect(within(fila).getByText(/todos los clientes que lo tengan/)).toBeInTheDocument();
  await user.selectOptions(within(fila).getByLabelText("Es un"), "recambio");
  const lecturas = api().getParqueEmpresa.mock.calls.length;
  await user.click(within(fila).getByRole("button", { name: "Confirmar" }));
  expect(api().setClaseArticulo).toHaveBeenCalledWith("1712", "recambio");
  await waitFor(() => expect(api().getParqueEmpresa.mock.calls.length).toBeGreaterThan(lecturas));
});

test("la factura enseña su albarán y un albarán suelto avisa de la factura cercana", async () => {
  api().getParqueEmpresa.mockResolvedValue({
    ...PARQUE, maquinas: [
      MAQ({ albaranes: [{ tipo: "albaran", serie: "3", codigo: 230001, ejercicio: "2023", numero: "3-230001" }] }),
      MAQ({ origen: "albaran", clave: "2023|albaran|3|230002|1|1712", ejercicio: "2023",
            documento: { tipo: "albaran", serie: "3", codigo: 230002, numero: "3-230002" },
            posible_misma_venta: AVISO }),
    ],
  });
  render(<CompanyParquePanel companyId="emp-1" puedeEditar={false} />);
  expect(await screen.findByText("3-230001")).toBeInTheDocument();
  const aviso = screen.getByRole("group", { name: "¿Misma venta?" });
  expect(within(aviso).getByText("1-123021").closest("a")).toHaveAttribute(
    "href", expect.stringContaining("/factura/2022/1/123021"));
  // Lo que hace falta para decidir, sin abrir nada; sin permiso, sin botones.
  expect(aviso).toHaveTextContent("Este albarán: 0,00 € · «ENVÍO MÁQUINA SUBSTITUCIÓN»");
  expect(aviso).toHaveTextContent("La factura: 9000,00 € · «Pedido figurante 123021»");
  expect(within(aviso).queryByRole("button")).toBeNull();
});

const ALB = "2023|albaran|3|230002|1|1712";
const FAC = "2022|factura|1|123021|1|1712";
const AVISO = { tipo: "factura", serie: "1", codigo: 123021, ejercicio: "2022", numero: "1-123021",
                clave: FAC, importe: 9000, referencia: "Pedido figurante 123021",
                albaran: { importe: 0, referencia: "ENVÍO MÁQUINA SUBSTITUCIÓN" }, propuesta: null };
const SUELTO = (over: Record<string, unknown> = {}) => MAQ({
  origen: "albaran", clave: ALB, ejercicio: "2023",
  documento: { tipo: "albaran", serie: "3", codigo: 230002, numero: "3-230002" },
  posible_misma_venta: AVISO, ...over });

test("el aviso se contesta: sí, es la misma venta; no, son dos compras", async () => {
  const user = userEvent.setup();
  api().getParqueEmpresa.mockResolvedValue({ ...PARQUE, maquinas: [SUELTO()] });
  api().contestarMismaVenta.mockResolvedValue({ ...PARQUE, activas: 0, maquinas: [SUELTO({
    baja: { at: "2026-11-01T10:00:00Z", motivo: null, por: "bart@example.com", tipo: "misma_venta",
            duplica: { tipo: "factura", serie: "1", codigo: 123021, ejercicio: "2022", numero: "1-123021" },
            propuesta: false } })] });
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  const aviso = await screen.findByRole("group", { name: "¿Misma venta?" });
  await user.click(within(aviso).getByRole("button", { name: "Sí, es la misma venta" }));
  expect(api().contestarMismaVenta).toHaveBeenCalledWith("emp-1", { clave: ALB, factura: FAC }, true);
  // Sale como duplicado, no como retirada; y se puede deshacer.
  expect(await screen.findByText(/No cuenta: misma venta que la factura/)).toBeInTheDocument();
  expect(screen.queryByText(/Dada de baja/)).toBeNull();
  expect(screen.queryByRole("group", { name: "¿Misma venta?" })).toBeNull();
  expect(screen.getByRole("button", { name: "Deshacer «misma venta»" })).toBeInTheDocument();
});

test("«no, son dos compras» deja una nota que se puede deshacer", async () => {
  const user = userEvent.setup();
  api().getParqueEmpresa.mockResolvedValue({ ...PARQUE, maquinas: [SUELTO()] });
  api().contestarMismaVenta.mockResolvedValue({ ...PARQUE, maquinas: [SUELTO({
    posible_misma_venta: null,
    dos_compras: [{ tipo: "factura", serie: "1", codigo: 123021, ejercicio: "2022", numero: "1-123021",
                    clave: FAC, por: "bart@example.com", at: "2026-11-01T10:00:00Z" }] })] });
  api().deshacerDosCompras.mockResolvedValue({ ...PARQUE, maquinas: [SUELTO()] });
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  const aviso = await screen.findByRole("group", { name: "¿Misma venta?" });
  await user.click(within(aviso).getByRole("button", { name: "No, son dos compras" }));
  expect(api().contestarMismaVenta).toHaveBeenCalledWith("emp-1", { clave: ALB, factura: FAC }, false);
  expect(await screen.findByText(/Revisado: no es la misma venta que la factura/)).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "¿Misma venta?" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Deshacer" }));
  expect(api().deshacerDosCompras).toHaveBeenCalledWith("emp-1", { clave: ALB, factura: FAC });
  expect(await screen.findByRole("group", { name: "¿Misma venta?" })).toBeInTheDocument();
});

test("un envío de sustitución a 0 € sale ya propuesto, y se puede decir que no", async () => {
  api().getParqueEmpresa.mockResolvedValue({ ...PARQUE, activas: 0, maquinas: [SUELTO({
    posible_misma_venta: { ...AVISO, propuesta: "sustitucion" },
    baja: { at: null, motivo: "Envío de sustitución a 0 €", por: null, tipo: "misma_venta",
            duplica: { tipo: "factura", serie: "1", codigo: 123021, ejercicio: "2022", numero: "1-123021" },
            propuesta: true } })] });
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  const aviso = await screen.findByRole("group", { name: "¿Misma venta?" });
  expect(aviso).toHaveTextContent("Propuesto: la misma venta");
  expect(screen.getByText("No cuenta: sustitución (propuesto)")).toBeInTheDocument();
  expect(within(aviso).getByRole("button", { name: "No, son dos compras" })).toBeInTheDocument();
  // Lo propuesto no se escribió: nada que «volver a poner en servicio».
  expect(screen.queryByRole("button", { name: /Volver a poner en servicio|Deshacer/ })).toBeNull();
});

test("una baja de antes con el aviso pregunta si era la misma venta", async () => {
  api().getParqueEmpresa.mockResolvedValue({ ...PARQUE, maquinas: [SUELTO({
    baja: { at: "2026-10-30T10:00:00Z", motivo: "Repetido", por: "x", tipo: null, duplica: null,
            propuesta: false } })] });
  render(<CompanyParquePanel companyId="emp-1" puedeEditar />);
  const aviso = await screen.findByRole("group", { name: "¿Misma venta?" });
  expect(aviso).toHaveTextContent("¿Era la misma venta?");
  expect(within(aviso).getByRole("button", { name: "Sí, es la misma venta" })).toBeInTheDocument();
});

test("la misma máquina con el código de FACTUSOL y el de la web sale avisada", async () => {
  api().getParqueEmpresa.mockResolvedValue({
    ...PARQUE, maquinas: [
      MAQ({ codart: "34", articulo: "Beambox Figurante (40W)",
            mismo_modelo: [{ codart: "60995", articulo: "FLUX BEAMBOX 40W", ejercicio: "2025",
                             documento: { tipo: "factura", serie: "5", codigo: 250009, numero: "5-250009" } }] }),
      MAQ({ codart: "60995", articulo: "FLUX BEAMBOX 40W", clave: "2025|factura|5|250009|1|60995",
            mismo_modelo: [{ codart: "34", articulo: "Beambox Figurante (40W)", ejercicio: "2026",
                             documento: { tipo: "factura", serie: "5", codigo: 260001, numero: "5-260001" } }] }),
    ],
  });
  render(<CompanyParquePanel companyId="emp-1" puedeEditar={false} />);
  const avisos = await screen.findAllByText(/¿La misma máquina que/);
  expect(avisos).toHaveLength(2);
  expect(within(avisos[0]).getByText("60995").closest("a")).toHaveAttribute(
    "href", expect.stringContaining("/factura/2025/5/250009"));
});


test("marcada «no es cliente final»: se ven sus máquinas y se dice que no cuentan", async () => {
  api().getParqueEmpresa.mockResolvedValue({ ...PARQUE, no_cliente_final: true });
  render(<CompanyParquePanel companyId="emp-1" puedeEditar={false} />);
  expect(await screen.findByText(/Marcada «no es cliente final»/)).toBeInTheDocument();
  expect(screen.getByText("Láser Figurante 1390")).toBeInTheDocument();
});
