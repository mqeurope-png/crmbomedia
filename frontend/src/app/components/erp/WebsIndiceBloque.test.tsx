import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { precioDeLaWeb, REFRESCO_MS, WebsIndiceBloque } from "./WebsIndiceBloque";
import {
  actualizarReglaWebs,
  borrarReglaWebs,
  buscarEnWebs,
  crearReglaWebs,
  estadoWebs,
  listarReglasWebs,
  reindexarWebs,
  type WebFragmento,
  type WebRegla,
  type WebsEstado,
  type WebsPasada,
} from "../../lib/websApi";

jest.mock("../../lib/websApi", () => ({
  estadoWebs: jest.fn(),
  reindexarWebs: jest.fn(),
  listarReglasWebs: jest.fn(),
  crearReglaWebs: jest.fn(),
  actualizarReglaWebs: jest.fn(),
  borrarReglaWebs: jest.fn(),
  buscarEnWebs: jest.fn(),
}));

const mockEstado = estadoWebs as jest.Mock;
const mockReindexar = reindexarWebs as jest.Mock;
const mockReglas = listarReglasWebs as jest.Mock;
const mockCrear = crearReglaWebs as jest.Mock;
const mockActualizar = actualizarReglaWebs as jest.Mock;
const mockBorrar = borrarReglaWebs as jest.Mock;
const mockBuscar = buscarEnWebs as jest.Mock;

function pasada(over: Partial<WebsPasada> = {}): WebsPasada {
  return {
    id: "p1", origen: "programada", estado: "parcial", inicio: "2026-10-01T03:10:00Z",
    fin: "2026-10-01T03:17:00Z", pendientes: [], error: null, creado_en: "2026-10-01T03:09:00Z",
    resumen: {
      boprint: { estado: "ok", paginas: 10, entradas: 6, productos: 36, nuevos: 2, actualizados: 3, idos: 0 },
      mbolasers: { estado: "error", error: "La REST devuelve 500 al pedir el contenido." },
    },
    ...over,
  };
}

const ESTADO: WebsEstado = {
  filas: [
    { sitio: "mbolasers", dominio: "mbolasers.com", pagina: 51, entrada: 25, producto: 106, idos: 3,
      idiomas: ["de", "en", "es", "fr", "nl"], ultima: "2026-10-01T03:16:00Z" },
    { sitio: "boprint", dominio: "boprint.net", pagina: 10, entrada: 6, producto: 36, idos: 0,
      idiomas: ["es"], ultima: "2026-10-01T03:11:00Z" },
  ],
  total: { pagina: 61, entrada: 31, producto: 142 },
  ultima_pasada: pasada(),
  en_curso: null,
};

function regla(over: Partial<WebRegla> & { id: number; patron: string }): WebRegla {
  return { tipo: "pagina_excluir_slug", sitio: null, activo: true, nota: null, posicion: 0, ...over };
}

const REGLAS: WebRegla[] = [
  regla({ id: 1, patron: "cooki", nota: "cookies" }),
  regla({ id: 2, tipo: "categoria_fuera", patron: "Repuestos" }),
  regla({ id: 3, tipo: "producto_incluir_id", patron: "4320", sitio: "boprint", nota: "kit de limpieza" }),
];

const BEAMBOX: WebFragmento = {
  id: "w1", sitio: "boprint", dominio: "boprint.net", tipo: "producto", idioma: "es",
  titulo: "FLUX Beambox II", url: "https://boprint.net/producto/flux-beambox-ii",
  fragmento: "Cortadora láser CO2 de 55 W con cámara para alinear.", imagen: "https://boprint.net/img/beambox.jpg",
  galeria: ["https://boprint.net/img/b2.jpg", "https://boprint.net/img/b3.jpg"],
  categorias: ["Corte láser", "Fluxlasers"], precio_web: 4490, precio_web_texto: null,
  precio_iva: "sin_iva", moneda: "EUR",
};

beforeEach(() => {
  for (const m of [mockEstado, mockReindexar, mockReglas, mockCrear, mockActualizar, mockBorrar, mockBuscar]) {
    m.mockReset();
  }
  mockEstado.mockResolvedValue(ESTADO);
  mockReglas.mockResolvedValue(REGLAS);
});

afterEach(() => {
  jest.useRealTimers();
});

describe("WebsIndiceBloque · conteo por web y tipo", () => {
  it("una fila por web (página / entrada / producto, total, ya no están, idiomas) y el total", async () => {
    render(<WebsIndiceBloque canEdit />);
    const tabla = await screen.findByRole("table", { name: "Índice de las webs por web y tipo" });
    const filas = within(tabla).getAllByRole("row");
    expect(within(filas[0]).getAllByRole("columnheader").map((c) => c.textContent)).toEqual([
      "Web", "Páginas", "Entradas", "Productos", "Total", "Ya no están", "Idiomas", "Última lectura",
    ]);
    // Ordenadas por dominio: boprint.net antes que mbolasers.com.
    const celdas = (fila: HTMLElement) => within(fila).getAllByRole("cell").map((c) => c.textContent);
    expect(within(filas[1]).getByRole("rowheader")).toHaveTextContent("boprint.net");
    expect(celdas(filas[1]).slice(0, 6)).toEqual(["10", "6", "36", "52", "0", "ES"]);
    expect(within(filas[2]).getByRole("rowheader")).toHaveTextContent("mbolasers.com");
    expect(celdas(filas[2]).slice(0, 6)).toEqual(["51", "25", "106", "182", "3", "DEENESFRNL"]);
    expect(within(filas[3]).getByRole("rowheader")).toHaveTextContent("Total (2 webs)");
    expect(celdas(filas[3]).slice(0, 5)).toEqual(["61", "31", "142", "234", "3"]);
    // Los ceros se apagan, sin dejar de verse.
    expect(within(filas[1]).getAllByRole("cell")[4]).toHaveClass("is-cero");
  });

  it("el último recorrido: estado, origen y el resumen por web con su error", async () => {
    render(<WebsIndiceBloque canEdit />);
    const ultimo = await screen.findByLabelText("Último recorrido terminado");
    expect(within(ultimo).getByText("terminado con errores en alguna web")).toHaveClass("is-error");
    expect(within(ultimo).getByText(/el mensual/)).toBeInTheDocument();
    expect(within(ultimo).getByText(/10 páginas · 6 entradas · 36 productos · 2 nuevos · 3 cambiados/)).toBeInTheDocument();
    expect(within(ultimo).getByText("La REST devuelve 500 al pedir el contenido.")).toHaveClass("is-error");
    expect(screen.queryByLabelText("Recorrido en curso")).not.toBeInTheDocument();
  });

  it("con el índice vacío lo dice y ofrece llenarlo", async () => {
    mockEstado.mockResolvedValue({ ...ESTADO, filas: [], total: { pagina: 0, entrada: 0, producto: 0 }, ultima_pasada: null });
    render(<WebsIndiceBloque canEdit />);
    expect(await screen.findByText(/El índice está vacío/)).toBeInTheDocument();
    expect(within(screen.getByLabelText("Último recorrido terminado")).getByText("Ninguno todavía.")).toBeInTheDocument();
  });
});

describe("WebsIndiceBloque · «Reindexar ahora»", () => {
  it("encola, avisa y refresca el estado cada pocos segundos mientras está en cola o corriendo", async () => {
    jest.useFakeTimers();
    const enCola = pasada({ id: "p2", origen: "manual", estado: "en_cola", inicio: null, fin: null,
      resumen: {}, pendientes: ["boprint", "mbolasers"], creado_en: "2026-10-27T11:00:00Z" });
    const corriendo = { ...enCola, estado: "corriendo" as const, inicio: "2026-10-27T11:00:10Z",
      pendientes: ["mbolasers"], resumen: { boprint: { estado: "ok", paginas: 10, entradas: 6, productos: 37 } } };
    const terminado = pasada({ id: "p2", origen: "manual", estado: "ok", resumen: corriendo.resumen });
    mockReindexar.mockResolvedValue(enCola);
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<WebsIndiceBloque canEdit />);
    await screen.findByRole("table", { name: "Índice de las webs por web y tipo" });
    expect(mockEstado).toHaveBeenCalledTimes(1);

    mockEstado.mockResolvedValue({ ...ESTADO, en_curso: enCola });
    await user.click(screen.getByRole("button", { name: "Reindexar ahora" }));
    expect(mockReindexar).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("status")).toHaveTextContent(/Recorrido en cola/);
    await waitFor(() => expect(mockEstado).toHaveBeenCalledTimes(2));
    const enCurso = await screen.findByLabelText("Recorrido en curso");
    expect(within(enCurso).getByText(/en cola/)).toBeInTheDocument();
    expect(within(enCurso).getByText("Faltan: boprint, mbolasers")).toBeInTheDocument();
    // Mientras hay uno en marcha, no se encola otro.
    expect(screen.getByRole("button", { name: "Reindexar ahora" })).toBeDisabled();

    // A los pocos segundos se vuelve a pedir: ya corre.
    mockEstado.mockResolvedValue({ ...ESTADO, en_curso: corriendo });
    await act(async () => { jest.advanceTimersByTime(REFRESCO_MS); });
    await waitFor(() => expect(mockEstado).toHaveBeenCalledTimes(3));
    expect(await within(screen.getByLabelText("Recorrido en curso")).findByText("en marcha")).toBeInTheDocument();

    // Y otra vez: ha terminado; se deja de preguntar.
    mockEstado.mockResolvedValue({ ...ESTADO, en_curso: null, ultima_pasada: terminado });
    await act(async () => { jest.advanceTimersByTime(REFRESCO_MS); });
    await waitFor(() => expect(mockEstado).toHaveBeenCalledTimes(4));
    await waitFor(() => expect(screen.queryByLabelText("Recorrido en curso")).not.toBeInTheDocument());
    expect(within(screen.getByLabelText("Último recorrido terminado")).getByText("terminado sin errores")).toBeInTheDocument();
    await act(async () => { jest.advanceTimersByTime(REFRESCO_MS * 3); });
    expect(mockEstado).toHaveBeenCalledTimes(4);
    expect(screen.getByRole("button", { name: "Reindexar ahora" })).toBeEnabled();
  });

  it("sin permiso de edición no se puede reindexar", async () => {
    render(<WebsIndiceBloque canEdit={false} />);
    await screen.findByRole("table", { name: "Índice de las webs por web y tipo" });
    expect(screen.getByRole("button", { name: "Reindexar ahora" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Añadir regla" })).not.toBeInTheDocument();
  });
});

describe("WebsIndiceBloque · reglas", () => {
  it("agrupadas por tipo, cada grupo con su explicación", async () => {
    render(<WebsIndiceBloque canEdit />);
    const slug = await screen.findByRole("group", { name: "Páginas y entradas fuera, por slug" });
    expect(within(slug).getByText(/expresión regular sobre el slug/)).toBeInTheDocument();
    expect(within(slug).getByLabelText("Patrón de la regla «cooki»")).toHaveValue("cooki");
    const categorias = screen.getByRole("group", { name: "Categorías de producto fuera" });
    expect(within(categorias).getByText(/Cualquier categoría de fuera manda/)).toBeInTheDocument();
    expect(within(categorias).getByLabelText("Patrón de la regla «Repuestos»")).toHaveValue("Repuestos");
    const incluir = screen.getByRole("group", { name: "Productos dentro, por id (excepción)" });
    expect(within(incluir).getByLabelText("Web de la regla «4320» (boprint)")).toHaveValue("boprint");
    expect(within(screen.getByRole("group", { name: "Productos fuera, por id" })).getByText("Ninguna.")).toBeInTheDocument();
  });

  it("alta de una regla con su web y nota; un id que no es número no se manda", async () => {
    mockCrear.mockImplementation(async (p) => regla({ id: 9, posicion: 5, ...p }));
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit />);
    await screen.findByRole("group", { name: "Categorías de producto fuera" });
    await user.selectOptions(screen.getByLabelText("Tipo de la regla nueva"), "producto_excluir_id");
    await user.type(screen.getByLabelText("Patrón de la regla nueva"), "abc");
    await user.click(screen.getByRole("button", { name: "Añadir regla" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El id es un número");
    expect(mockCrear).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText("Patrón de la regla nueva"));
    await user.type(screen.getByLabelText("Patrón de la regla nueva"), "3201");
    await user.type(screen.getByLabelText("Web de la regla nueva"), "mbolasers");
    await user.type(screen.getByLabelText("Nota de la regla nueva"), "producto español repetido");
    await user.click(screen.getByRole("button", { name: "Añadir regla" }));
    await waitFor(() => expect(mockCrear).toHaveBeenCalledWith({
      tipo: "producto_excluir_id", patron: "3201", sitio: "mbolasers", nota: "producto español repetido",
      activo: true,
    }));
    const grupo = screen.getByRole("group", { name: "Productos fuera, por id" });
    expect(await within(grupo).findByLabelText("Patrón de la regla «3201» (mbolasers)")).toHaveValue("3201");
  });

  it("edita el patrón (Guardar solo si cambia), desactiva y borra con confirmación", async () => {
    mockActualizar.mockImplementation(async (id, cambios) => ({ ...REGLAS.find((r) => r.id === id), ...cambios }));
    mockBorrar.mockResolvedValue({ ok: true });
    const confirmar = jest.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit />);
    const patron = await screen.findByLabelText("Patrón de la regla «Repuestos»");
    const fila = patron.closest("tr") as HTMLElement;
    expect(within(fila).queryByRole("button", { name: "Guardar" })).not.toBeInTheDocument();
    await user.clear(patron);
    await user.type(patron, "Recambios");
    await user.click(within(fila).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockActualizar).toHaveBeenCalledWith(2, { patron: "Recambios", sitio: null, nota: null }));
    expect(await screen.findByLabelText("Patrón de la regla «Recambios»")).toHaveValue("Recambios");

    await user.click(screen.getByLabelText("Activa la regla «cooki»"));
    await waitFor(() => expect(mockActualizar).toHaveBeenCalledWith(1, { activo: false }));
    expect(await screen.findByLabelText("Activa la regla «cooki»")).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Borrar la regla «4320» (boprint)" }));
    expect(confirmar).toHaveBeenCalled();
    await waitFor(() => expect(mockBorrar).toHaveBeenCalledWith(3));
    await waitFor(() => expect(screen.queryByLabelText("Patrón de la regla «4320» (boprint)")).not.toBeInTheDocument());
    confirmar.mockRestore();
  });
});

describe("WebsIndiceBloque · búsqueda de prueba", () => {
  it("enseña el trozo con su URL, la imagen y el precio como precio de la web (no de oferta)", async () => {
    mockBuscar.mockResolvedValue([BEAMBOX]);
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit={false} />);
    await screen.findByRole("table", { name: "Índice de las webs por web y tipo" });
    await user.type(screen.getByLabelText("Buscar en el índice de las webs"), "beambox");
    await user.type(screen.getByLabelText("Idioma de la búsqueda de prueba"), "es");
    await user.selectOptions(screen.getByLabelText("Web de la búsqueda de prueba"), "boprint");
    await user.selectOptions(screen.getByLabelText("Tipo de la búsqueda de prueba"), "producto");
    await user.click(screen.getByRole("button", { name: "Buscar en el índice" }));
    await waitFor(() => expect(mockBuscar).toHaveBeenCalledWith("beambox", {
      idioma: "es", sitio: "boprint", tipo: "producto",
    }));
    const lista = await screen.findByRole("list", { name: "Resultados de la prueba" });
    const item = within(lista).getByRole("listitem");
    expect(within(item).getByRole("link", { name: "FLUX Beambox II" })).toHaveAttribute("href", BEAMBOX.url);
    expect(within(item).getByText(BEAMBOX.url)).toBeInTheDocument();
    expect(item.querySelector("img")).toHaveAttribute("src", BEAMBOX.imagen);
    expect(within(item).getByText(/boprint\.net · Producto · ES · Corte láser, Fluxlasers · 2 imágenes más/)).toBeInTheDocument();
    expect(within(item).getByText(BEAMBOX.fragmento)).toBeInTheDocument();
    // es-ES no separa los miles con cuatro cifras (4490,00 €).
    const precio = within(item).getByText(/Precio de la web:/);
    expect(precio).toHaveTextContent(/Precio de la web: 4490,00\s€ \(sin IVA\)/);
    expect(precio).toHaveTextContent("lo que dice la web, no es precio de oferta");
  });

  it("sin nada en el índice lo dice", async () => {
    mockBuscar.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit />);
    await screen.findByRole("table", { name: "Índice de las webs por web y tipo" });
    await user.type(screen.getByLabelText("Buscar en el índice de las webs"), "plancha de vapor");
    await user.click(screen.getByRole("button", { name: "Buscar en el índice" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Nada en el índice para «plancha de vapor».");
  });

  it("precio de la web: el texto tal cual manda; sin precio no hay línea; el IVA dicho o «no lo dice»", () => {
    expect(precioDeLaWeb({ precio_web: 12, precio_web_texto: "12,00 € (+IVA)", precio_iva: "sin_iva", moneda: "EUR" }))
      .toEqual({ importe: "12,00 € (+IVA)", iva: "sin IVA" });
    expect(precioDeLaWeb({ precio_web: null, precio_web_texto: null, precio_iva: null, moneda: null })).toBeNull();
    expect(precioDeLaWeb({ precio_web: 99, precio_web_texto: null, precio_iva: "desconocido", moneda: "EUR" })?.iva)
      .toBe("la web no dice si lleva IVA");
  });
});
