import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PlantillaEditorModal } from "./PlantillaEditorModal";
import {
  createPlantilla,
  getPlantilla,
  getPlantillasMeta,
  listPlantillaCarpetas,
  listPlantillaEtiquetas,
  searchFactusolArticles,
  updatePlantilla,
  type PlantillaDocumento,
} from "../../lib/erpApi";

/** Alta y edición de una plantilla de documento: nombre, descripción, idioma,
 *  carpeta (moverla), etiquetas, activa y las líneas (mismo editor que la
 *  oferta). Al editar desde la lista, las líneas se piden con getPlantilla. */

jest.mock("../../lib/erpApi", () => ({
  createPlantilla: jest.fn(),
  updatePlantilla: jest.fn(),
  getPlantilla: jest.fn(),
  getPlantillasMeta: jest.fn(),
  listPlantillaCarpetas: jest.fn(),
  listPlantillaEtiquetas: jest.fn(),
  searchFactusolArticles: jest.fn(),
}));

const mockCreate = createPlantilla as jest.Mock;
const mockUpdate = updatePlantilla as jest.Mock;
const mockGet = getPlantilla as jest.Mock;
const mockMeta = getPlantillasMeta as jest.Mock;
const mockCarpetas = listPlantillaCarpetas as jest.Mock;

function carpeta(id: string, nombre: string, parent_id: string | null, children: unknown[] = []) {
  return { id, nombre, parent_id, num_plantillas: 0, es_defecto: false, children };
}

const ARBOL = {
  items: [
    carpeta("c-dtf", "DTF", null, [carpeta("c-esc", "Escuelas", "c-dtf")]),
    carpeta("c-uv", "UV", null),
  ],
  carpeta_defecto: "c-esc",
};

function meta(over = {}) {
  return {
    idiomas: ["es", "en", "de", "fr", "nl"], destinos: ["oferta", "proforma", "pedido"],
    max_profundidad: 3, carpeta_defecto: "c-esc", gestiona_acceso: false,
    acceso_restringido: false, ...over,
  };
}

const PL: PlantillaDocumento = {
  id: "p1", nombre: "Kit UV", descripcion: "Arranque", activa: true, idioma: "es",
  carpeta_id: "c-dtf", carpeta: "DTF", etiquetas: ["UV"],
  origen_tipo: "presupuesto", origen_serie: 2, origen_codigo: 40, origen_order_id: null,
  origen_numero: "2-000040", num_lineas: 1, created_at: null, updated_at: null,
};
const LINEA = {
  id: "l1", posicion: 1, codart: "A1", sku: "SKU-1", descripcion: "Placa",
  cantidad: 2, precio: 50, descuento_pct: 0, iva_pct: 21, total: 100, text_only: false,
};
const LINEA_IN = {
  codart: "A1", sku: "SKU-1", descripcion: "Placa", cantidad: 2, precio: 50, descuento_pct: 0, iva_pct: 21,
};

beforeEach(() => {
  mockCreate.mockReset();
  mockUpdate.mockReset();
  mockGet.mockReset();
  mockMeta.mockReset();
  mockCarpetas.mockReset();
  (searchFactusolArticles as jest.Mock).mockResolvedValue([]);
  (listPlantillaEtiquetas as jest.Mock).mockResolvedValue([{ etiqueta: "DTF", norm: "dtf", num: 2 }]);
  mockMeta.mockResolvedValue(meta());
  mockCarpetas.mockResolvedValue(ARBOL);
  mockCreate.mockResolvedValue({ ...PL, id: "p2", nombre: "Nueva", origen_tipo: "manual", origen_numero: null });
  mockUpdate.mockResolvedValue(PL);
  mockGet.mockResolvedValue({ ...PL, lineas: [LINEA] });
});

describe("PlantillaEditorModal", () => {
  it("nueva con líneas a mano → createPlantilla con idioma, carpeta, etiquetas, activa y líneas", async () => {
    const onSaved = jest.fn();
    const user = userEvent.setup();
    render(<PlantillaEditorModal onClose={jest.fn()} onSaved={onSaved} />);
    expect(screen.getByRole("dialog", { name: "Nueva plantilla" })).toBeInTheDocument();
    expect(screen.queryByText(/Guardada desde/)).toBeNull();
    const crear = screen.getByRole("button", { name: "Crear plantilla" });
    expect(crear).toBeDisabled();

    // Nueva sin carpeta abierta: se propone la carpeta por defecto del usuario.
    await screen.findByRole("option", { name: "DTF / Escuelas" });
    await waitFor(() => expect(screen.getByLabelText("Carpeta de la plantilla")).toHaveValue("c-esc"));

    await user.type(screen.getByLabelText("Nombre de la plantilla"), "Nueva");
    // Con nombre pero sin líneas, sigue sin poder crearse.
    expect(crear).toBeDisabled();
    await user.type(screen.getByLabelText("Descripción línea 1"), "Montaje");
    await user.type(screen.getByLabelText("Precio línea 1"), "80");
    await user.selectOptions(screen.getByLabelText("Idioma de la plantilla"), "fr");
    await user.selectOptions(screen.getByLabelText("Carpeta de la plantilla"), "c-uv");
    await user.type(screen.getByLabelText("Añadir etiqueta"), "UV{Enter}");
    await user.click(screen.getByLabelText("Plantilla activa"));                 // la deja desactivada
    expect(crear).toBeEnabled();
    await user.click(crear);
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({
      nombre: "Nueva",
      descripcion: null,
      idioma: "fr",
      carpeta_id: "c-uv",
      etiquetas: ["UV"],
      activa: false,
      lineas: [{ codart: null, sku: null, descripcion: "Montaje", cantidad: 1, precio: 80, descuento_pct: 0, iva_pct: 21 }],
    }));
    expect(mockUpdate).not.toHaveBeenCalled();
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "p2" })));
  });

  it("nueva desde una carpeta abierta: se queda en esa carpeta, no en la por defecto", async () => {
    const user = userEvent.setup();
    render(<PlantillaEditorModal carpetaInicial="c-uv" onClose={jest.fn()} onSaved={jest.fn()} />);
    await screen.findByRole("option", { name: "DTF / Escuelas" });
    expect(screen.getByLabelText("Carpeta de la plantilla")).toHaveValue("c-uv");
    await user.type(screen.getByLabelText("Nombre de la plantilla"), "Tinta");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Tinta blanca");
    await user.click(screen.getByRole("button", { name: "Crear plantilla" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({ carpeta_id: "c-uv", idioma: "es", etiquetas: [], activa: true }),
    ));
  });

  it("editar: vaciar la descripción la borra (se manda vacía, no «sin cambios»)", async () => {
    const user = userEvent.setup();
    render(<PlantillaEditorModal plantilla={PL} onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(await screen.findByLabelText("SKU línea 1")).toHaveValue("SKU-1");
    await user.clear(screen.getByLabelText("Descripción de la plantilla"));
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("p1",
      expect.objectContaining({ descripcion: "" })));
  });

  it("editar: carga las líneas con getPlantilla y guarda con updatePlantilla moviéndola de carpeta, con otro idioma y otras etiquetas", async () => {
    const onSaved = jest.fn();
    const user = userEvent.setup();
    render(<PlantillaEditorModal plantilla={PL} onClose={jest.fn()} onSaved={onSaved} />);
    expect(screen.getByRole("dialog", { name: "Editar plantilla Kit UV" }))
      .toHaveTextContent("Guardada desde la proforma 2-000040");
    expect(screen.getByText("Cargando líneas…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeDisabled();
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("p1"));
    expect(await screen.findByLabelText("SKU línea 1")).toHaveValue("SKU-1");
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("Placa");

    // Al editar se respeta su carpeta (no la por defecto del usuario).
    await screen.findByRole("option", { name: "DTF / Escuelas" });
    expect(screen.getByLabelText("Carpeta de la plantilla")).toHaveValue("c-dtf");
    expect(screen.getByLabelText("Idioma de la plantilla")).toHaveValue("es");
    expect(within(screen.getByRole("list", { name: "Etiquetas" })).getByText("UV")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Nombre de la plantilla"));
    await user.type(screen.getByLabelText("Nombre de la plantilla"), "Kit UV 2026");
    await user.selectOptions(screen.getByLabelText("Idioma de la plantilla"), "de");
    await user.selectOptions(screen.getByLabelText("Carpeta de la plantilla"), "c-esc");
    await user.click(screen.getByRole("button", { name: "Quitar la etiqueta UV" }));
    await user.type(screen.getByLabelText("Añadir etiqueta"), "Escuelas{Enter}");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("p1", {
      nombre: "Kit UV 2026",
      descripcion: "Arranque",
      idioma: "de",
      carpeta_id: "c-esc",
      etiquetas: ["Escuelas"],
      activa: true,
      lineas: [LINEA_IN],
    }));
    expect(mockCreate).not.toHaveBeenCalled();
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(PL));
  });

  it("una plantilla que ya trae sus líneas no las vuelve a pedir y se puede sacar a la raíz", async () => {
    const user = userEvent.setup();
    const albaran: PlantillaDocumento = {
      ...PL, id: "p3", nombre: "Kit albarán", origen_tipo: "albaran", origen_serie: 5,
      origen_codigo: 500010, origen_numero: "5-500010", carpeta_id: "c-uv", carpeta: "UV",
      etiquetas: [], lineas: [LINEA],
    };
    render(<PlantillaEditorModal plantilla={albaran} onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(screen.getByRole("dialog", { name: "Editar plantilla Kit albarán" }))
      .toHaveTextContent("Guardada desde el albarán 5-500010");
    expect(screen.getByLabelText("SKU línea 1")).toHaveValue("SKU-1");
    await screen.findByRole("option", { name: "UV" });
    expect(screen.getByLabelText("Carpeta de la plantilla")).toHaveValue("c-uv");
    await user.selectOptions(screen.getByLabelText("Carpeta de la plantilla"), "");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("p3", expect.objectContaining({
      carpeta_id: null, etiquetas: [], lineas: [LINEA_IN],
    })));
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("guardada desde un pedido enseña su nº; hecha a mano no enseña origen", async () => {
    const pedido: PlantillaDocumento = {
      ...PL, id: "p4", nombre: "Del pedido", origen_tipo: "pedido", origen_serie: null,
      origen_codigo: null, origen_order_id: "o-1", origen_numero: "MANUAL-000777", lineas: [LINEA],
    };
    const { unmount } = render(
      <PlantillaEditorModal plantilla={pedido} onClose={jest.fn()} onSaved={jest.fn()} />,
    );
    expect(screen.getByRole("dialog", { name: "Editar plantilla Del pedido" }))
      .toHaveTextContent("Guardada desde el pedido MANUAL-000777");
    await screen.findByRole("option", { name: "UV" });
    unmount();

    const manual: PlantillaDocumento = { ...PL, id: "p5", nombre: "A mano", origen_tipo: "manual", origen_numero: null, lineas: [LINEA] };
    render(<PlantillaEditorModal plantilla={manual} onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(screen.getByRole("dialog", { name: "Editar plantilla A mano" })).toBeInTheDocument();
    expect(screen.queryByText(/Guardada desde/)).toBeNull();
    await screen.findByRole("option", { name: "UV" });
  });

  it("si no se pueden cargar las líneas se avisa y no deja guardar (no se pisan con vacías)", async () => {
    mockGet.mockRejectedValue(new Error("Plantilla no encontrada"));
    render(<PlantillaEditorModal plantilla={PL} onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Plantilla no encontrada");
    await screen.findByRole("option", { name: "UV" });
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeDisabled();
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("acceso restringido: sin «Sin carpeta» y la nueva va a su primera carpeta", async () => {
    mockMeta.mockResolvedValue(meta({ acceso_restringido: true, carpeta_defecto: null }));
    mockCarpetas.mockResolvedValue({ items: [carpeta("c-uv", "UV", null)], carpeta_defecto: null });
    const user = userEvent.setup();
    render(<PlantillaEditorModal onClose={jest.fn()} onSaved={jest.fn()} />);
    await screen.findByRole("option", { name: "UV" });
    await waitFor(() => expect(screen.getByLabelText("Carpeta de la plantilla")).toHaveValue("c-uv"));
    expect(screen.queryByRole("option", { name: "— Sin carpeta —" })).toBeNull();
    await user.type(screen.getByLabelText("Nombre de la plantilla"), "Kit");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Placa");
    await user.click(screen.getByRole("button", { name: "Crear plantilla" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({ carpeta_id: "c-uv" }),
    ));
  });

  it("un error del servidor al guardar se enseña y el modal sigue abierto; Cancelar cierra", async () => {
    mockUpdate.mockRejectedValue(new Error("Ya hay una plantilla con ese nombre"));
    const onClose = jest.fn();
    const onSaved = jest.fn();
    const user = userEvent.setup();
    render(<PlantillaEditorModal plantilla={{ ...PL, lineas: [LINEA] }} onClose={onClose} onSaved={onSaved} />);
    await screen.findByRole("option", { name: "UV" });
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ya hay una plantilla con ese nombre");
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
