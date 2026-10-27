import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { precioDeLaWeb, REFRESCO_MS, WebsIndiceBloque } from "./WebsIndiceBloque";
import { ApiError } from "../../lib/api";
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

type Usuario = ReturnType<typeof userEvent.setup>;

/** Las reglas van plegadas: se abren con su botón, que dice cuántas hay. */
async function abrirReglas(user: Usuario) {
  await user.click(await screen.findByRole("button", { name: `Ver las reglas (${REGLAS.length})` }));
}

/** La fila de botones de arriba: el resultado de «Actualizar el estado» va ahí. */
function acciones(): HTMLElement {
  return screen.getByRole("button", { name: /^Actualiz/ }).parentElement as HTMLElement;
}

/** La tabla del conteo y los recorridos, con su marca de «sin actualizar». */
function datos(): HTMLElement {
  return screen.getByRole("table", { name: "Índice de las webs por web y tipo" })
    .closest(".webs-indice-datos") as HTMLElement;
}

const SESION_CADUCADA = new ApiError("Could not validate credentials", 401, "Could not validate credentials");

const CORRIENDO = pasada({ id: "p2", origen: "manual", estado: "corriendo", fin: null, resumen: {},
  pendientes: ["mbolasers"], inicio: "2026-10-27T11:00:10Z" });

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
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit={false} />);
    await screen.findByRole("table", { name: "Índice de las webs por web y tipo" });
    expect(screen.getByRole("button", { name: "Reindexar ahora" })).toBeDisabled();
    await abrirReglas(user);
    expect(screen.getByRole("group", { name: "Categorías de producto fuera" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Añadir regla" })).not.toBeInTheDocument();
  });
});

describe("WebsIndiceBloque · «Actualizar el estado» y datos sin actualizar", () => {
  it("dice «Actualizando…» mientras pide y, al terminar, la hora junto al botón", async () => {
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit />);
    await screen.findByRole("table", { name: "Índice de las webs por web y tipo" });
    expect(acciones()).toHaveTextContent(/Actualizado a las \d{2}:\d{2}/);

    let soltar: (e: WebsEstado) => void = () => {};
    mockEstado.mockImplementation(() => new Promise<WebsEstado>((r) => { soltar = r; }));
    await user.click(screen.getByRole("button", { name: "Actualizar el estado" }));
    expect(screen.getByRole("button", { name: "Actualizando…" })).toBeDisabled();
    expect(acciones()).not.toHaveTextContent(/Actualizado a las/);
    await act(async () => { soltar(ESTADO); });
    expect(await screen.findByRole("button", { name: "Actualizar el estado" })).toBeEnabled();
    expect(acciones()).toHaveTextContent(/Actualizado a las \d{2}:\d{2}/);
    expect(mockEstado).toHaveBeenCalledTimes(2);
  });

  it("con la sesión caducada (401) tras una primera carga: lo dice junto al botón y marca los números como de antes", async () => {
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit />);
    await screen.findByRole("table", { name: "Índice de las webs por web y tipo" });
    expect(datos()).not.toHaveClass("is-desfasado");

    mockEstado.mockRejectedValue(SESION_CADUCADA);
    await user.click(screen.getByRole("button", { name: "Actualizar el estado" }));
    await waitFor(() => expect(acciones()).toHaveTextContent(
      /No se ha podido actualizar: tu sesión ha caducado \(vuelve a entrar\)\. Lo que ves es de las \d{2}:\d{2}\./));
    // Los números se quedan (son lo último que se sabe), pero marcados.
    expect(within(datos()).getByText(/sin actualizar:/).closest("p"))
      .toHaveTextContent(/Datos de las \d{2}:\d{2}, sin actualizar: tu sesión ha caducado/);
    expect(datos()).toHaveClass("is-desfasado");
    expect(within(datos()).getByLabelText("Último recorrido terminado")).toBeInTheDocument();
    // El botón no se queda en «Actualizando…».
    expect(screen.getByRole("button", { name: "Actualizar el estado" })).toBeEnabled();
  });

  it("si el sondeo de un recorrido en marcha recibe un 401, deja de preguntar y lo dice", async () => {
    jest.useFakeTimers();
    mockEstado.mockResolvedValueOnce({ ...ESTADO, en_curso: CORRIENDO }).mockRejectedValue(SESION_CADUCADA);
    render(<WebsIndiceBloque canEdit />);
    await screen.findByLabelText("Recorrido en curso");
    expect(screen.getByText(/el estado se refresca solo/)).toBeInTheDocument();

    await act(async () => { jest.advanceTimersByTime(REFRESCO_MS); });
    await waitFor(() => expect(mockEstado).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(acciones()).toHaveTextContent(/No se ha podido actualizar: tu sesión ha caducado/));
    expect(datos()).toHaveClass("is-desfasado");
    expect(within(datos()).getByText(/vuelve a entrar para verlos al día/)).toBeInTheDocument();
    expect(screen.queryByText(/el estado se refresca solo/)).not.toBeInTheDocument();
    await act(async () => { jest.advanceTimersByTime(REFRESCO_MS * 3); });
    expect(mockEstado).toHaveBeenCalledTimes(2);
  });

  it("un fallo que no es de sesión dice «No se ha podido actualizar» y el sondeo sigue hasta que vuelve a ir", async () => {
    jest.useFakeTimers();
    const terminado = pasada({ id: "p2", origen: "manual", estado: "ok" });
    mockEstado
      .mockResolvedValueOnce({ ...ESTADO, en_curso: CORRIENDO })
      .mockRejectedValueOnce(new ApiError("Error de la API (502)", 502, null))
      .mockResolvedValueOnce({ ...ESTADO, en_curso: CORRIENDO })
      .mockResolvedValue({ ...ESTADO, en_curso: null, ultima_pasada: terminado });
    render(<WebsIndiceBloque canEdit />);
    await screen.findByLabelText("Recorrido en curso");

    await act(async () => { jest.advanceTimersByTime(REFRESCO_MS); });
    await waitFor(() => expect(mockEstado).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(acciones()).toHaveTextContent(
      /No se ha podido actualizar: Error de la API \(502\)\. Lo que ves es de las \d{2}:\d{2}\./));
    expect(datos()).toHaveClass("is-desfasado");
    expect(within(datos()).getByText(/se vuelve a intentar cada 5 s/)).toBeInTheDocument();

    // No se para: a los pocos segundos se vuelve a pedir y ya va.
    await act(async () => { jest.advanceTimersByTime(REFRESCO_MS); });
    await waitFor(() => expect(mockEstado).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(acciones()).toHaveTextContent(/Actualizado a las \d{2}:\d{2}/));
    expect(datos()).not.toHaveClass("is-desfasado");
    expect(screen.queryByText(/sin actualizar/)).not.toBeInTheDocument();

    // Y sigue mientras dura el recorrido: termina y se deja de preguntar.
    await act(async () => { jest.advanceTimersByTime(REFRESCO_MS); });
    await waitFor(() => expect(mockEstado).toHaveBeenCalledTimes(4));
    await waitFor(() => expect(screen.queryByLabelText("Recorrido en curso")).not.toBeInTheDocument());
    await act(async () => { jest.advanceTimersByTime(REFRESCO_MS * 3); });
    expect(mockEstado).toHaveBeenCalledTimes(4);
  });

  it("si la primera carga falla no hay números que marcar: lo dice junto al botón", async () => {
    mockEstado.mockRejectedValue(new ApiError("Error de la API (500)", 500, null));
    render(<WebsIndiceBloque canEdit />);
    await waitFor(() => expect(acciones()).toHaveTextContent(
      "No se ha podido cargar el estado del índice: Error de la API (500)."));
    expect(screen.queryByRole("table", { name: "Índice de las webs por web y tipo" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Cargando el estado del índice/)).not.toBeInTheDocument();
  });
});

describe("WebsIndiceBloque · reglas", () => {
  it("van plegadas: el botón dice cuántas hay y la lista solo se pinta al abrirla", async () => {
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit />);
    const ver = await screen.findByRole("button", { name: "Ver las reglas (3)" });
    expect(ver).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Patrón de la regla «cooki»")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Añadir regla" })).not.toBeInTheDocument();

    await user.click(ver);
    expect(screen.getByRole("button", { name: "Ocultar las reglas" })).toHaveAttribute("aria-expanded", "true");
    const patron = screen.getByLabelText("Patrón de la regla «cooki»");
    await user.type(patron, "|cookie");

    // Ocultarlas no pierde lo escrito sin guardar.
    await user.click(screen.getByRole("button", { name: "Ocultar las reglas" }));
    expect(screen.queryByRole("group", { name: "Categorías de producto fuera" })).not.toBeInTheDocument();
    await abrirReglas(user);
    expect(screen.getByLabelText("Patrón de la regla «cooki»")).toHaveValue("cooki|cookie");
    expect(mockReglas).toHaveBeenCalledTimes(1);
  });

  it("agrupadas por tipo, cada grupo con su explicación", async () => {
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit />);
    await abrirReglas(user);
    const slug = screen.getByRole("group", { name: "Páginas y entradas fuera, por slug" });
    expect(within(slug).getByText(/expresión regular sobre el slug/)).toBeInTheDocument();
    expect(within(slug).getByLabelText("Patrón de la regla «cooki»")).toHaveValue("cooki");
    const categorias = screen.getByRole("group", { name: "Categorías de producto fuera" });
    expect(within(categorias).getByText(/Cualquier categoría de fuera manda/)).toBeInTheDocument();
    // Por el nombre en el idioma por defecto; luego por id a todos los idiomas y subcategorías.
    expect(within(categorias).getByText(/idioma POR DEFECTO de la web/)).toHaveTextContent(
      /todas sus subcategorías y la misma categoría en todos los idiomas de la web/);
    expect(within(categorias).getByLabelText("Patrón de la regla «Repuestos»")).toHaveValue("Repuestos");
    const incluir = screen.getByRole("group", { name: "Productos dentro, por id (excepción)" });
    expect(within(incluir).getByLabelText("Web de la regla «4320» (boprint)")).toHaveValue("boprint");
    expect(within(screen.getByRole("group", { name: "Productos fuera, por id" })).getByText("Ninguna.")).toBeInTheDocument();
  });

  it("alta de una regla con su web y nota; un id que no es número, o sin web, no se manda", async () => {
    mockCrear.mockImplementation(async (p) => regla({ id: 9, posicion: 5, ...p }));
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit />);
    await abrirReglas(user);
    await user.selectOptions(screen.getByLabelText("Tipo de la regla nueva"), "producto_excluir_id");
    expect(screen.getByText("Web (obligatoria)")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Patrón de la regla nueva"), "abc");
    await user.click(screen.getByRole("button", { name: "Añadir regla" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El id es un número");
    expect(mockCrear).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText("Patrón de la regla nueva"));
    await user.type(screen.getByLabelText("Patrón de la regla nueva"), "3201");
    await user.click(screen.getByRole("button", { name: "Añadir regla" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Una regla por id necesita su web"));
    expect(mockCrear).not.toHaveBeenCalled();

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
    await abrirReglas(user);
    const patron = screen.getByLabelText("Patrón de la regla «Repuestos»");
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

  it("(des)activar una regla no se lleva lo que se está escribiendo en su fila sin guardar", async () => {
    mockActualizar.mockImplementation(async (id, cambios) => ({ ...REGLAS.find((r) => r.id === id), ...cambios }));
    const user = userEvent.setup();
    render(<WebsIndiceBloque canEdit />);
    await abrirReglas(user);
    const patron = screen.getByLabelText("Patrón de la regla «cooki»");
    const fila = patron.closest("tr") as HTMLElement;
    await user.clear(patron);
    await user.type(patron, "cooki|checkout");
    await user.type(within(fila).getByLabelText("Web de la regla «cooki»"), "boprint");
    await user.type(within(fila).getByLabelText("Nota de la regla «cooki»"), " y pago");
    // Se desactiva y se vuelve a activar sin haber pulsado «Guardar».
    await user.click(within(fila).getByLabelText("Activa la regla «cooki»"));
    await waitFor(() => expect(mockActualizar).toHaveBeenCalledWith(1, { activo: false }));
    expect(await within(fila).findByLabelText("Activa la regla «cooki»")).not.toBeChecked();
    await user.click(within(fila).getByLabelText("Activa la regla «cooki»"));
    await waitFor(() => expect(mockActualizar).toHaveBeenCalledWith(1, { activo: true }));
    expect(await within(fila).findByLabelText("Activa la regla «cooki»")).toBeChecked();
    // Lo escrito sigue ahí, y «Guardar» también.
    expect(patron).toHaveValue("cooki|checkout");
    expect(within(fila).getByLabelText("Web de la regla «cooki»")).toHaveValue("boprint");
    expect(within(fila).getByLabelText("Nota de la regla «cooki»")).toHaveValue("cookies y pago");
    await user.click(within(fila).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockActualizar).toHaveBeenLastCalledWith(1, {
      patron: "cooki|checkout", sitio: "boprint", nota: "cookies y pago",
    }));
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
