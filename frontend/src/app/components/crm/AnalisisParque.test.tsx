import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnalisisParquePanel, AnalisisResumenContacto } from "./AnalisisParque";

/** El análisis del parque (ficha de empresa y de contacto). Figurantes. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));
jest.mock("../../lib/analisisParqueApi", () => ({
  getPermisoAnalisis: jest.fn(),
  getAnalisisParque: jest.fn(),
  generarAnalisisParque: jest.fn(),
}));

function api() {
  return jest.requireMock("../../lib/analisisParqueApi") as Record<string, jest.Mock>;
}

const HECHOS = {
  estado: "con_datos",
  hechos: [
    { id: "D1", bloque: "tiene", texto: "Tiene LASER FIGURANTE 40W (LAS100), de la gama Láser.",
      gama: "laser", fuente: { tipo: "documento", doc: { tipo: "factura", serie: "1",
        codigo: 210001, ejercicio: "2021", numero: "1-210001" } } },
    { id: "D2", bloque: "cambio", texto: "Compraba filtros cada 4 meses y lleva 56 sin pedir.",
      gama: "laser", fuente: { tipo: "documento", doc: { tipo: "factura", serie: "1",
        codigo: 220001, ejercicio: "2022", numero: "1-220001" } } },
    { id: "D3", bloque: "no_compra", texto: "Nunca ha comprado Filtro figurante B (FIL300).",
      gama: "laser", fuente: { tipo: "fecha", fecha: "2026-05-01" } },
    { id: "D4", bloque: "no_se_sabe", texto: "No se sabe si la máquina sigue en servicio.",
      gama: "laser", fuente: { tipo: "documento", doc: { tipo: "factura", serie: "1",
        codigo: 210001, ejercicio: "2021", numero: "1-210001" } } },
  ],
  productos: [],
};

const GENERADO = {
  company_id: "emp-1", datos: "con_datos", estado: "al_dia", hechos_actuales: HECHOS,
  generado_at: "2026-11-01T09:30:00Z", modelo: "claude-haiku-5-5", coste_usd: 0.0012,
  caducidad_meses: 3,
  analisis: {
    hechos: HECHOS,
    lectura: [{ texto: "Dejó de comprar filtros hace tiempo.", citas: ["D2"] }],
    entrada: { texto: "Preguntarle si sigue usando el láser.", citas: ["D2"],
               productos: [{ id: "P1", codart: "FIL300", descripcion: "Filtro figurante B" }] },
    descartado: ["Una frase de la lectura no citaba ningún dato."],
  },
};

beforeEach(() => {
  api().getPermisoAnalisis.mockResolvedValue({ puede: true, solo_admin: true });
  api().getAnalisisParque.mockResolvedValue({ ...GENERADO, estado: "sin_generar", analisis: null,
                                              generado_at: null });
  api().generarAnalisisParque.mockResolvedValue(GENERADO);
});

afterEach(() => jest.clearAllMocks());

test("al abrir la ficha se genera, y cada dato lleva su documento o su fecha", async () => {
  render(<AnalisisParquePanel companyId="emp-1" />);
  const panel = await screen.findByRole("region", { name: "Análisis del parque" });
  await waitFor(() => expect(api().generarAnalisisParque).toHaveBeenCalledWith("emp-1", false));
  expect(await within(panel).findByText(/Calculado el .* con claude-haiku-5-5/)).toBeInTheDocument();
  expect(within(panel).getByText(/En prueba · solo administradores/)).toBeInTheDocument();
  const cambio = within(panel).getByRole("generic", { name: "Qué ha cambiado" });
  expect(within(cambio).getByRole("link", { name: /Factura 1-220001/ }))
    .toHaveAttribute("href", "/erp/documentos/ficha/factura/2022/1/220001");
  const entrada = within(panel).getByRole("generic", { name: "Por dónde entrar" });
  expect(entrada).toHaveTextContent("Preguntarle si sigue usando el láser.");
  expect(within(entrada).getByRole("link", { name: "D2" })).toHaveAttribute(
    "href", "#analisis-emp-1-D2");
  expect(entrada).toHaveTextContent("Filtro figurante B (FIL300)");
  expect(within(panel).getByRole("generic", { name: "Lo que no se sabe" }))
    .toHaveTextContent("No se sabe si la máquina sigue en servicio.");
  expect(within(panel).getByText(/Se descartó 1 frase/)).toBeInTheDocument();
});

test("al día no se regenera; «Volver a generar» sí", async () => {
  api().getAnalisisParque.mockResolvedValue(GENERADO);
  const user = userEvent.setup();
  render(<AnalisisParquePanel companyId="emp-1" />);
  await screen.findByText(/Calculado el/);
  expect(api().generarAnalisisParque).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Volver a generar" }));
  expect(api().generarAnalisisParque).toHaveBeenCalledWith("emp-1", true);
});

test("sin cliente de FACTUSOL lo dice y no genera nada", async () => {
  api().getAnalisisParque.mockResolvedValue({ ...GENERADO, datos: "sin_cliente", analisis: null,
                                              hechos_actuales: { ...HECHOS, hechos: [] } });
  render(<AnalisisParquePanel companyId="emp-1" />);
  expect(await screen.findByText(/no tiene cliente de FACTUSOL vinculado/)).toBeInTheDocument();
  expect(api().generarAnalisisParque).not.toHaveBeenCalled();
});

test("si no puede verlo, no sale", async () => {
  api().getPermisoAnalisis.mockResolvedValue({ puede: false, solo_admin: true });
  const { container } = render(<AnalisisParquePanel companyId="emp-1" />);
  await waitFor(() => expect(api().getPermisoAnalisis).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
  expect(api().getAnalisisParque).not.toHaveBeenCalled();
});

test("en la ficha de contacto: el de su empresa, dicho, y con enlace; sin empresa, nada", async () => {
  api().getAnalisisParque.mockResolvedValue(GENERADO);
  const { rerender, container } = render(
    <AnalisisResumenContacto companyId="emp-1" companyName="Taller figurante" />);
  const resumen = await screen.findByRole("region", { name: "Análisis de su empresa" });
  expect(resumen).toHaveTextContent("Es de Taller figurante, no de este contacto");
  expect(await within(resumen).findByText(/Preguntarle si sigue usando el láser/)).toBeInTheDocument();
  expect(within(resumen).getByRole("link", { name: "Ver el análisis completo" }))
    .toHaveAttribute("href", "/companies/emp-1#analisis-parque");
  expect(api().generarAnalisisParque).not.toHaveBeenCalled();     // desde el contacto no genera
  rerender(<AnalisisResumenContacto companyId={null} companyName={null} />);
  expect(container).toBeEmptyDOMElement();
});


test("una empresa que no es cliente final no se analiza", async () => {
  api().getAnalisisParque.mockResolvedValue({ ...GENERADO, datos: "no_cliente_final", analisis: null,
                                              hechos_actuales: { ...HECHOS, hechos: [] } });
  render(<AnalisisParquePanel companyId="emp-1" />);
  expect(await screen.findByText(/no es cliente final/)).toBeInTheDocument();
  expect(api().generarAnalisisParque).not.toHaveBeenCalled();
});

test("las citas salen separadas (D2 · D1), no pegadas", async () => {
  const conVarias = { ...GENERADO, analisis: { ...GENERADO.analisis,
    lectura: [{ texto: "Dejó de comprar filtros hace tiempo.", citas: ["D2", "D1", "D4"] }] } };
  api().generarAnalisisParque.mockResolvedValue(conVarias);
  render(<AnalisisParquePanel companyId="emp-1" />);
  const frase = (await screen.findByText(/Dejó de comprar filtros hace tiempo\./)).closest("li") as HTMLElement;
  expect(frase).toHaveTextContent("D2 · D1 · D4");
  expect(frase).not.toHaveTextContent("D2D1");
  expect(within(frase).getAllByRole("link").map((a) => a.textContent)).toEqual(["D2", "D1", "D4"]);
});
