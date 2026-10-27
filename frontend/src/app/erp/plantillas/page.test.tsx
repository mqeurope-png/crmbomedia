import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PlantillasPage from "./page";
import { getCurrentUser } from "../../lib/api";
import {
  createPlantillaCarpeta,
  deletePlantillaCarpeta,
  getPlantillasMeta,
  listPlantillaCarpetas,
  listPlantillaEtiquetas,
  listPlantillas,
  listPlantillaUsuarios,
  setPlantillaCarpetaAcceso,
  setPlantillaCarpetaDefecto,
  updatePlantilla,
  updatePlantillaCarpeta,
  type PlantillaCarpetaNode,
  type PlantillaDocumento,
  type PlantillasMeta,
} from "../../lib/erpApi";

/** Pantalla ERP · Plantillas: árbol de carpetas (se abre en la carpeta por
 *  defecto, ★ para cambiarla, crear / borrar y, para administración, el
 *  acceso), la lista con el buscador (que mira dentro de las líneas) y los
 *  filtros de idioma, estado y etiqueta, y por plantilla Editar, Desactivar /
 *  Activar y el menú «Usar …» (oferta, proforma y, con permiso, pedido). El
 *  editor de plantillas se prueba en su propio test: aquí basta con ver que se
 *  abre con la fila. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title, description, actions }: {
    title: string; description?: string; actions?: React.ReactNode;
  }) => <div><h1>{title}</h1><p>{description}</p>{actions}</div>,
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/erpApi", () => ({
  createPlantillaCarpeta: jest.fn(),
  deletePlantillaCarpeta: jest.fn(),
  getPlantillasMeta: jest.fn(),
  listPlantillaCarpetas: jest.fn(),
  listPlantillaEtiquetas: jest.fn(),
  listPlantillas: jest.fn(),
  listPlantillaUsuarios: jest.fn(),
  setPlantillaCarpetaAcceso: jest.fn(),
  setPlantillaCarpetaDefecto: jest.fn(),
  updatePlantilla: jest.fn(),
  updatePlantillaCarpeta: jest.fn(),
}));
jest.mock("../../components/erp/PlantillaEditorModal", () => ({
  PlantillaEditorModal: ({ plantilla, carpetaInicial, onClose, onSaved }: {
    plantilla?: { nombre: string } | null; carpetaInicial?: string | null;
    onClose: () => void; onSaved: (p: { nombre: string }) => void;
  }) => (
    <div>
      EDITOR {plantilla?.nombre ?? "nueva"} carpeta:{carpetaInicial ?? "—"}
      <button type="button" onClick={() => onSaved(plantilla ?? { nombre: "Recién creada" })}>
        GUARDAR EDITOR
      </button>
      <button type="button" onClick={onClose}>CERRAR EDITOR</button>
    </div>
  ),
}));

const mockMeta = getPlantillasMeta as jest.Mock;
const mockCarpetas = listPlantillaCarpetas as jest.Mock;
const mockEtiquetas = listPlantillaEtiquetas as jest.Mock;
const mockList = listPlantillas as jest.Mock;
const mockUsuarios = listPlantillaUsuarios as jest.Mock;
const mockUpdate = updatePlantilla as jest.Mock;
const mockCreateCarpeta = createPlantillaCarpeta as jest.Mock;
const mockUpdateCarpeta = updatePlantillaCarpeta as jest.Mock;
const mockDeleteCarpeta = deletePlantillaCarpeta as jest.Mock;
const mockAcceso = setPlantillaCarpetaAcceso as jest.Mock;
const mockDefecto = setPlantillaCarpetaDefecto as jest.Mock;

function plantilla(over: Partial<PlantillaDocumento> = {}): PlantillaDocumento {
  return {
    id: "p1", nombre: "Kit arranque DTF", descripcion: "Para escuelas de diseño", activa: true,
    idioma: "es", carpeta_id: "c-esc", carpeta: "DTF / Escuelas", etiquetas: ["Escuelas"],
    origen_tipo: "presupuesto", origen_serie: 2, origen_codigo: 40, origen_order_id: null,
    origen_numero: "2-000040", num_lineas: 3, created_at: null, updated_at: null,
    ...over,
  };
}

const P1 = plantilla();
const P2 = plantilla({
  id: "p2", nombre: "Kit UV Deutsch", descripcion: null, activa: false, idioma: "de",
  carpeta_id: null, carpeta: null, etiquetas: [], origen_tipo: "manual", origen_serie: null,
  origen_codigo: null, origen_numero: null, num_lineas: 1,
});

const ARBOL: PlantillaCarpetaNode[] = [
  {
    id: "c-dtf", nombre: "DTF", parent_id: null, num_plantillas: 4, es_defecto: false,
    children: [
      { id: "c-esc", nombre: "Escuelas", parent_id: "c-dtf", num_plantillas: 2, es_defecto: true,
        children: [], acceso_user_ids: [] },
    ],
  },
  { id: "c-uv", nombre: "UV", parent_id: null, num_plantillas: 1, es_defecto: false, children: [] },
];

const META: PlantillasMeta = {
  idiomas: ["es", "en", "de", "fr", "nl"], destinos: ["oferta", "proforma", "pedido"],
  max_profundidad: 3, carpeta_defecto: "c-esc", gestiona_acceso: false, acceso_restringido: false,
};

function listado(items: PlantillaDocumento[]) {
  return { items, total: items.length, idioma_cliente: null, idioma_fuente: null };
}

/** Argumentos de la última llamada a `listPlantillas`. */
function ultimaLista(): Record<string, unknown> {
  return mockList.mock.calls[mockList.mock.calls.length - 1][0];
}

function fila(nombre: string) {
  return screen.getByRole("row", { name: `Plantilla ${nombre}` });
}

beforeEach(() => {
  (getCurrentUser as jest.Mock).mockImplementation(() => Promise.resolve({ role: "admin" }));
  mockMeta.mockReset().mockResolvedValue(META);
  mockCarpetas.mockReset().mockResolvedValue({ items: ARBOL, carpeta_defecto: "c-esc" });
  mockEtiquetas.mockReset().mockResolvedValue([
    { etiqueta: "Escuelas", norm: "escuelas", num: 4 },
    { etiqueta: "Arranque", norm: "arranque", num: 2 },
  ]);
  mockList.mockReset().mockResolvedValue(listado([P1, P2]));
  mockUsuarios.mockReset().mockResolvedValue([
    { id: "u1", nombre: "Ana Pérez", email: "ana@bomedia.net", rol: "comercial", restringido: false },
    { id: "u2", nombre: "Luis Gómez", email: "luis@bomedia.net", rol: "comercial", restringido: true },
  ]);
  mockUpdate.mockReset().mockImplementation((id: string, patch: { activa?: boolean }) => (
    Promise.resolve({ ...(id === "p1" ? P1 : P2), ...patch })
  ));
  mockCreateCarpeta.mockReset().mockResolvedValue({ id: "c-new", nombre: "Hornos", parent_id: "c-esc" });
  mockUpdateCarpeta.mockReset();
  mockDeleteCarpeta.mockReset().mockResolvedValue({ ok: true });
  mockAcceso.mockReset().mockImplementation((id: string, ids: string[]) => (
    Promise.resolve({ id, acceso_user_ids: ids })
  ));
  mockDefecto.mockReset().mockImplementation((id: string | null) => (
    Promise.resolve({ carpeta_defecto: id })
  ));
});

afterEach(() => {
  jest.restoreAllMocks();
});

/** Espera a la lista ya cargada (y con el usuario: los botones de editar). */
async function cargada() {
  await screen.findByRole("table", { name: "Plantillas" });
  await within(fila("Kit arranque DTF")).findByRole("button", { name: "Editar" });
}

describe("ERP · Plantillas", () => {
  it("carga el árbol y la lista: se abre en la carpeta por defecto y enseña nombre, idioma, carpeta, líneas, estado y origen", async () => {
    render(<PlantillasPage />);
    expect(screen.getByRole("heading", { name: "Plantillas" })).toBeInTheDocument();
    await cargada();

    // No lista nada hasta saber la carpeta por defecto: la primera (y única) ya va filtrada.
    expect(mockList).toHaveBeenCalledTimes(1);
    expect(mockList).toHaveBeenCalledWith({
      carpeta_id: "c-esc", q: "", etiquetas: [], idioma: "", activas: false,
    });
    expect(mockMeta).toHaveBeenCalledTimes(1);
    expect(mockEtiquetas).toHaveBeenCalledTimes(1);

    // El árbol: Todas, Sin carpeta y las carpetas, con la por defecto marcada.
    const arbol = screen.getByRole("navigation", { name: "Carpetas de plantillas" });
    expect(within(arbol).getByRole("button", { name: "Todas" })).toHaveAttribute("aria-pressed", "false");
    expect(within(arbol).getByRole("button", { name: "Sin carpeta" })).toBeInTheDocument();
    expect(within(arbol).getByRole("button", { name: "Carpeta DTF" })).toHaveAttribute("aria-pressed", "false");
    expect(within(arbol).getByRole("button", { name: "Carpeta Escuelas" })).toHaveAttribute("aria-pressed", "true");
    expect(within(arbol).getByRole("button", { name: "Quitar Escuelas como carpeta por defecto" }))
      .toHaveAttribute("aria-pressed", "true");
    expect(within(arbol).getByRole("button", { name: "Marcar UV como carpeta por defecto" }))
      .toHaveAttribute("aria-pressed", "false");

    const f1 = within(fila("Kit arranque DTF"));
    expect(f1.getByText("Para escuelas de diseño")).toBeInTheDocument();
    expect(f1.getByText("Escuelas")).toHaveClass("tag-chip");
    expect(f1.getByText("Guardada desde la proforma 2-000040")).toBeInTheDocument();
    expect(f1.getByText("ES")).toBeInTheDocument();
    expect(f1.getByText("DTF / Escuelas")).toBeInTheDocument();
    expect(f1.getByText("3")).toBeInTheDocument();
    expect(f1.getByText("Activa")).toHaveClass("badge", "ok");

    const f2 = within(fila("Kit UV Deutsch"));
    expect(f2.getByText("DE")).toBeInTheDocument();
    expect(f2.getByText("Desactivada")).toHaveClass("badge", "muted");
    expect(f2.queryByText(/Guardada desde/)).toBeNull();          // hecha a mano
    expect(f2.getByRole("button", { name: "Activar" })).toBeInTheDocument();
  });

  it("si la carpeta por defecto ya no está en el árbol, se abre en «Todas»", async () => {
    mockCarpetas.mockResolvedValue({ items: ARBOL, carpeta_defecto: "c-borrada" });
    render(<PlantillasPage />);
    await cargada();
    expect(mockList).toHaveBeenCalledTimes(1);
    expect(ultimaLista()).not.toHaveProperty("carpeta_id");
    expect(screen.getByRole("button", { name: "Todas" })).toHaveAttribute("aria-pressed", "true");
  });

  it("elegir otra carpeta en el árbol filtra por ella («Sin carpeta» manda sin_carpeta)", async () => {
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();
    await user.click(screen.getByRole("button", { name: "Carpeta UV" }));
    await waitFor(() => expect(ultimaLista()).toMatchObject({ carpeta_id: "c-uv" }));
    await user.click(screen.getByRole("button", { name: "Sin carpeta" }));
    await waitFor(() => expect(ultimaLista()).toMatchObject({ sin_carpeta: true }));
    expect(ultimaLista()).not.toHaveProperty("carpeta_id");
  });

  it("el buscador manda q a listPlantillas (con retardo) y enseña la línea que coincide", async () => {
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();
    mockList.mockResolvedValue(listado([plantilla({
      coincidencias: [{ posicion: 3, sku: "DM3H", descripcion: "Horno de curado 230 V" }],
    })]));
    await user.type(screen.getByRole("searchbox", { name: "Buscar plantilla" }), "DM3H");
    await waitFor(() => expect(ultimaLista()).toMatchObject({ q: "DM3H", carpeta_id: "c-esc" }));
    // Cuatro teclas → una sola petición con el texto.
    expect(mockList.mock.calls.filter((c) => c[0].q !== "")).toHaveLength(1);
    const f1 = within(fila("Kit arranque DTF"));
    expect(await f1.findByText("DM3H")).toBeInTheDocument();
    expect(f1.getByText(/Línea 3/)).toHaveTextContent("Horno de curado 230 V");
  });

  it("filtros de idioma, estado y etiqueta van a listPlantillas; «Limpiar filtros» los quita", async () => {
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();

    await user.selectOptions(screen.getByLabelText("Idioma"), "de");
    await waitFor(() => expect(ultimaLista()).toMatchObject({ idioma: "de", carpeta_id: "c-esc" }));

    const grupo = screen.getByRole("group", { name: "Filtrar por etiqueta" });
    const chip = within(grupo).getByRole("button", { name: /Escuelas/ });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    await user.click(chip);
    await waitFor(() => expect(ultimaLista()).toMatchObject({ etiquetas: ["escuelas"], idioma: "de" }));
    expect(chip).toHaveAttribute("aria-pressed", "true");

    await user.selectOptions(screen.getByLabelText("Estado"), "activas");
    await waitFor(() => expect(ultimaLista()).toMatchObject({ activas: true }));

    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => expect(ultimaLista()).toEqual({
      carpeta_id: "c-esc", q: "", etiquetas: [], idioma: "", activas: false,
    }));
    expect(screen.getByLabelText("Idioma")).toHaveValue("");
    expect(chip).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: "Limpiar filtros" })).toBeNull();
  });

  it("«Solo desactivadas» pide todas y deja solo las desactivadas", async () => {
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();
    await user.selectOptions(screen.getByLabelText("Estado"), "desactivadas");
    await waitFor(() => expect(screen.queryByRole("row", { name: "Plantilla Kit arranque DTF" })).toBeNull());
    expect(ultimaLista()).toMatchObject({ activas: false });
    expect(fila("Kit UV Deutsch")).toBeInTheDocument();
  });

  it("«Editar» abre el editor con la plantilla; «+ Nueva plantilla», con la carpeta abierta; guardar avisa y refresca", async () => {
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();

    await user.click(within(fila("Kit arranque DTF")).getByRole("button", { name: "Editar" }));
    expect(await screen.findByText(/EDITOR Kit arranque DTF/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "GUARDAR EDITOR" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Plantilla «Kit arranque DTF» guardada.");
    expect(screen.queryByText(/EDITOR/)).toBeNull();
    // Refresca la lista, las carpetas (los contadores) y las etiquetas.
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
    expect(mockCarpetas).toHaveBeenCalledTimes(2);
    expect(mockEtiquetas).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "+ Nueva plantilla" }));
    expect(await screen.findByText("EDITOR nueva carpeta:c-esc")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "GUARDAR EDITOR" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Plantilla «Recién creada» creada.");
  });

  it("«Desactivar» llama a updatePlantilla(id, {activa: false}), avisa y recarga; «Activar», al revés", async () => {
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();

    await user.click(within(fila("Kit arranque DTF")).getByRole("button", { name: "Desactivar" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("p1", { activa: false }));
    expect(await screen.findByRole("status")).toHaveTextContent("Plantilla «Kit arranque DTF» desactivada.");
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));

    await user.click(within(fila("Kit UV Deutsch")).getByRole("button", { name: "Activar" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("p2", { activa: true }));
    expect(await screen.findByRole("status")).toHaveTextContent("Plantilla «Kit UV Deutsch» activada.");
  });

  it("el menú «Usar …» enlaza a la oferta, la proforma y el pedido nuevos con ?plantilla=ID; la desactivada no tiene menú", async () => {
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();
    expect(within(fila("Kit UV Deutsch")).queryByRole("button", { name: "Usar Kit UV Deutsch" })).toBeNull();

    await user.click(within(fila("Kit arranque DTF")).getByRole("button", { name: "Usar Kit arranque DTF" }));
    expect(screen.getByRole("link", { name: "En una oferta" })).toHaveAttribute("href", "/erp/ofertas?plantilla=p1");
    expect(screen.getByRole("link", { name: "En una proforma" })).toHaveAttribute("href", "/erp/proformas?plantilla=p1");
    expect(screen.getByRole("link", { name: "En un pedido" })).toHaveAttribute("href", "/erp/orders/new?plantilla=p1");
  });

  it("sin erp.orders.create el menú «Usar …» no ofrece el pedido", async () => {
    (getCurrentUser as jest.Mock).mockImplementation(() => Promise.resolve({
      role: "comercial", capabilities: ["erp.access", "erp.proformas"],
    }));
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();
    await user.click(within(fila("Kit arranque DTF")).getByRole("button", { name: "Usar Kit arranque DTF" }));
    expect(screen.getByRole("link", { name: "En una oferta" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "En una proforma" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "En un pedido" })).toBeNull();
  });

  it("sin permiso de proformas: solo ver (ni editar, ni desactivar, ni usar, ni carpetas nuevas)", async () => {
    (getCurrentUser as jest.Mock).mockImplementation(() => Promise.resolve({ role: "sat" }));
    render(<PlantillasPage />);
    await screen.findByRole("table", { name: "Plantillas" });
    await waitFor(() => expect(getCurrentUser).toHaveBeenCalled());
    const f1 = within(fila("Kit arranque DTF"));
    expect(f1.queryByRole("button", { name: "Editar" })).toBeNull();
    expect(f1.queryByRole("button", { name: "Desactivar" })).toBeNull();
    expect(f1.queryByRole("button", { name: "Usar Kit arranque DTF" })).toBeNull();
    expect(screen.queryByRole("button", { name: "+ Nueva plantilla" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Nueva$/ })).toBeNull();
    expect(screen.queryByRole("group", { name: "Acciones de la carpeta Escuelas" })).toBeNull();
  });

  it("la ★ marca o quita la carpeta por defecto con setPlantillaCarpetaDefecto y recarga el árbol", async () => {
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();

    await user.click(screen.getByRole("button", { name: "Marcar UV como carpeta por defecto" }));
    await waitFor(() => expect(mockDefecto).toHaveBeenCalledWith("c-uv"));
    await waitFor(() => expect(mockCarpetas).toHaveBeenCalledTimes(2));
    // La ★ no cambia la carpeta abierta.
    expect(screen.getByRole("button", { name: "Carpeta Escuelas" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "Quitar Escuelas como carpeta por defecto" }));
    await waitFor(() => expect(mockDefecto).toHaveBeenLastCalledWith(null));
  });

  it("«Nueva» carpeta se crea dentro de la seleccionada (parent_id) y se abre", async () => {
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();

    await user.click(screen.getByRole("button", { name: "Nueva" }));
    const dialogo = within(await screen.findByRole("dialog", { name: "Nueva carpeta" }));
    expect(dialogo.getByLabelText("Carpeta madre")).toHaveValue("c-esc");
    expect(dialogo.getByRole("button", { name: "Crear carpeta" })).toBeDisabled();
    await user.type(dialogo.getByLabelText("Nombre de la carpeta"), "  Hornos ");
    await user.click(dialogo.getByRole("button", { name: "Crear carpeta" }));
    await waitFor(() => expect(mockCreateCarpeta).toHaveBeenCalledWith({ nombre: "Hornos", parent_id: "c-esc" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Carpeta «Hornos» creada.");
    expect(screen.queryByRole("dialog", { name: "Nueva carpeta" })).toBeNull();
    // Se abre la nueva y se refresca el árbol.
    await waitFor(() => expect(ultimaLista()).toMatchObject({ carpeta_id: "c-new" }));
    expect(mockCarpetas).toHaveBeenCalledTimes(2);
  });

  it("con «Todas» seleccionada la carpeta nueva va a la raíz (parent_id null)", async () => {
    mockCreateCarpeta.mockResolvedValue({ id: "c-new", nombre: "Bordado", parent_id: null });
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();
    await user.click(screen.getByRole("button", { name: "Todas" }));
    // Sin carpeta seleccionada no hay acciones de carpeta.
    expect(screen.queryByRole("group", { name: /Acciones de la carpeta/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Nueva" }));
    const dialogo = within(await screen.findByRole("dialog", { name: "Nueva carpeta" }));
    expect(dialogo.getByLabelText("Carpeta madre")).toHaveValue("");
    await user.type(dialogo.getByLabelText("Nombre de la carpeta"), "Bordado");
    await user.click(dialogo.getByRole("button", { name: "Crear carpeta" }));
    await waitFor(() => expect(mockCreateCarpeta).toHaveBeenCalledWith({ nombre: "Bordado", parent_id: null }));
  });

  it("borrar la carpeta pide confirmación; si se acepta llama a deletePlantillaCarpeta y vuelve a «Todas»", async () => {
    const confirmar = jest.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();
    const acciones = within(screen.getByRole("group", { name: "Acciones de la carpeta Escuelas" }));

    await user.click(acciones.getByRole("button", { name: "Borrar" }));
    expect(confirmar).toHaveBeenCalledWith(
      "¿Borrar la carpeta «Escuelas»? Sus plantillas y subcarpetas no se borran: pasan a la raíz.",
    );
    expect(mockDeleteCarpeta).not.toHaveBeenCalled();

    await user.click(acciones.getByRole("button", { name: "Borrar" }));
    await waitFor(() => expect(mockDeleteCarpeta).toHaveBeenCalledWith("c-esc"));
    expect(await screen.findByRole("status"))
      .toHaveTextContent("Carpeta «Escuelas» borrada; su contenido está ahora en la raíz.");
    await waitFor(() => expect(screen.getByRole("button", { name: "Todas" })).toHaveAttribute("aria-pressed", "true"));
    await waitFor(() => expect(ultimaLista()).not.toHaveProperty("carpeta_id"));
  });

  it("«Acceso» solo aparece si meta.gestiona_acceso", async () => {
    render(<PlantillasPage />);
    await cargada();
    const acciones = within(screen.getByRole("group", { name: "Acciones de la carpeta Escuelas" }));
    expect(acciones.getByRole("button", { name: "Borrar" })).toBeInTheDocument();
    expect(acciones.queryByRole("button", { name: "Acceso" })).toBeNull();
  });

  it("con meta.gestiona_acceso, «Acceso» abre el modal de la carpeta y guarda con setPlantillaCarpetaAcceso", async () => {
    mockMeta.mockResolvedValue({ ...META, gestiona_acceso: true });
    const user = userEvent.setup();
    render(<PlantillasPage />);
    await cargada();

    await user.click(await screen.findByRole("button", { name: "Acceso" }));
    const dialogo = within(await screen.findByRole("dialog", { name: "Acceso a la carpeta Escuelas" }));
    const luis = await dialogo.findByLabelText("Acceso de Luis Gómez");
    expect(luis).not.toBeChecked();
    expect(dialogo.getByLabelText("Acceso de Ana Pérez")).not.toBeChecked();
    await user.click(luis);
    await user.click(dialogo.getByRole("button", { name: "Guardar acceso" }));
    await waitFor(() => expect(mockAcceso).toHaveBeenCalledWith("c-esc", ["u2"]));
    expect(await screen.findByRole("status")).toHaveTextContent("«Escuelas»: acceso acotado a 1 usuario(s).");
    expect(screen.queryByRole("dialog", { name: "Acceso a la carpeta Escuelas" })).toBeNull();
    await waitFor(() => expect(mockCarpetas).toHaveBeenCalledTimes(2));
  });
});
