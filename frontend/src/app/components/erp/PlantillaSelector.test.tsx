import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PlantillaSelector } from "./PlantillaSelector";
import {
  getPlantillaLineas,
  getPlantillasMeta,
  listPlantillaCarpetas,
  listPlantillaEtiquetas,
  listPlantillas,
  type PlantillaCarpetaNode,
  type PlantillaDocumento,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getPlantillasMeta: jest.fn(),
  listPlantillaCarpetas: jest.fn(),
  listPlantillaEtiquetas: jest.fn(),
  listPlantillas: jest.fn(),
  getPlantillaLineas: jest.fn(),
}));

const mockMeta = getPlantillasMeta as jest.Mock;
const mockCarpetas = listPlantillaCarpetas as jest.Mock;
const mockEtiquetas = listPlantillaEtiquetas as jest.Mock;
const mockList = listPlantillas as jest.Mock;
const mockLineas = getPlantillaLineas as jest.Mock;

function plantilla(over: Partial<PlantillaDocumento> = {}): PlantillaDocumento {
  return {
    id: "p1", nombre: "Kit arranque DTF", descripcion: null, activa: true,
    idioma: "es", carpeta_id: "c-esc", carpeta: "DTF / Escuelas", etiquetas: [],
    origen_tipo: "manual", origen_serie: null, origen_codigo: null, origen_order_id: null,
    origen_numero: null, num_lineas: 3, created_at: null, updated_at: null,
    ...over,
  };
}

const ARBOL: PlantillaCarpetaNode[] = [
  {
    id: "c-dtf", nombre: "DTF", parent_id: null, num_plantillas: 4, es_defecto: false,
    children: [
      { id: "c-esc", nombre: "Escuelas", parent_id: "c-dtf", num_plantillas: 2, es_defecto: true, children: [] },
    ],
  },
  { id: "c-uv", nombre: "UV", parent_id: null, num_plantillas: 1, es_defecto: false, children: [] },
];

const META = {
  idiomas: ["es", "en", "de", "fr", "nl"], destinos: ["oferta", "proforma", "pedido"],
  max_profundidad: 3, carpeta_defecto: "c-esc", gestiona_acceso: false, acceso_restringido: false,
};

function listado(items: PlantillaDocumento[], idioma_cliente: string | null = null) {
  return { items, total: items.length, idioma_cliente, idioma_fuente: idioma_cliente ? "empresa" : null };
}

/** Argumentos de la última llamada a `listPlantillas`. */
function ultimaLista(): Record<string, unknown> {
  return mockList.mock.calls[mockList.mock.calls.length - 1][0];
}

beforeEach(() => {
  mockMeta.mockReset().mockResolvedValue(META);
  mockCarpetas.mockReset().mockResolvedValue({ items: ARBOL, carpeta_defecto: "c-esc" });
  mockEtiquetas.mockReset().mockResolvedValue([
    { etiqueta: "Escuelas", norm: "escuelas", num: 4 },
    { etiqueta: "Arranque", norm: "arranque", num: 2 },
  ]);
  mockList.mockReset().mockResolvedValue(listado([plantilla()]));
  mockLineas.mockReset();
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe("PlantillaSelector", () => {
  it("se abre en la carpeta por defecto del usuario (activas) y «Todas» quita el filtro de carpeta", async () => {
    const user = userEvent.setup();
    render(<PlantillaSelector destino="oferta" onClose={jest.fn()} onPick={jest.fn()} />);
    expect(screen.getByRole("dialog", { name: "Elegir plantilla para la oferta" })).toBeInTheDocument();

    expect(await screen.findByRole("button", { name: "Usar la plantilla Kit arranque DTF" })).toBeInTheDocument();
    // No lista nada hasta saber la carpeta por defecto: la primera (y única) ya va filtrada.
    expect(mockList).toHaveBeenCalledTimes(1);
    expect(mockList).toHaveBeenCalledWith(expect.objectContaining({
      carpeta_id: "c-esc", activas: true, q: "", etiquetas: [],
    }));
    // La carpeta por defecto (anidada en DTF) sale marcada en el árbol.
    expect(screen.getByRole("button", { name: "Carpeta Escuelas" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Todas" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Solo en esta carpeta y sus subcarpetas.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Todas" }));
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
    expect(ultimaLista()).not.toHaveProperty("carpeta_id");
    expect(ultimaLista()).not.toHaveProperty("sin_carpeta");
    expect(ultimaLista()).toMatchObject({ activas: true });
    expect(screen.getByRole("button", { name: "Todas" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("Solo en esta carpeta y sus subcarpetas.")).not.toBeInTheDocument();
  });

  it("si la carpeta por defecto ya no está en el árbol, se abre en «Todas»", async () => {
    mockCarpetas.mockResolvedValue({ items: ARBOL, carpeta_defecto: "c-borrada" });
    render(<PlantillaSelector destino="pedido" onClose={jest.fn()} onPick={jest.fn()} />);
    await screen.findByRole("button", { name: "Usar la plantilla Kit arranque DTF" });
    expect(mockList).toHaveBeenCalledTimes(1);
    expect(ultimaLista()).not.toHaveProperty("carpeta_id");
    expect(screen.getByRole("button", { name: "Todas" })).toHaveAttribute("aria-pressed", "true");
  });

  it("sin plantillas en la carpeta ofrece buscar en todas las carpetas", async () => {
    mockList.mockResolvedValueOnce(listado([])).mockResolvedValue(listado([plantilla()]));
    const user = userEvent.setup();
    render(<PlantillaSelector destino="proforma" onClose={jest.fn()} onPick={jest.fn()} />);
    expect(await screen.findByText(/No hay plantillas con estos filtros/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Buscar en todas las carpetas" }));
    expect(await screen.findByRole("button", { name: "Usar la plantilla Kit arranque DTF" })).toBeInTheDocument();
    expect(ultimaLista()).not.toHaveProperty("carpeta_id");
  });

  it("el buscador espera 300 ms, manda q a listPlantillas y enseña la línea que coincide", async () => {
    jest.useFakeTimers();
    try {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
      render(<PlantillaSelector destino="oferta" onClose={jest.fn()} onPick={jest.fn()} />);
      await screen.findByRole("button", { name: "Usar la plantilla Kit arranque DTF" });
      expect(mockList).toHaveBeenCalledTimes(1);

      mockList.mockResolvedValue(listado([plantilla({
        id: "p9", nombre: "Kit cocina escuela",
        coincidencias: [{ posicion: 3, sku: "DM3H", descripcion: "Horno de curado 230 V" }],
      })]));
      await user.type(screen.getByRole("searchbox", { name: "Buscar plantilla" }), "DM3H");
      // Escribir no lanza búsquedas letra a letra: hasta los 300 ms, nada.
      expect(mockList).toHaveBeenCalledTimes(1);
      await act(async () => { jest.advanceTimersByTime(299); });
      expect(mockList).toHaveBeenCalledTimes(1);
      await act(async () => { jest.advanceTimersByTime(1); });
      expect(mockList).toHaveBeenCalledTimes(2);
      expect(ultimaLista()).toMatchObject({ q: "DM3H", carpeta_id: "c-esc", activas: true });

      const item = await screen.findByRole("button", { name: "Usar la plantilla Kit cocina escuela" });
      expect(item).toHaveTextContent("Línea 3");
      expect(item).toHaveTextContent("DM3H");
      expect(item).toHaveTextContent("Horno de curado 230 V");
    } finally {
      jest.useRealTimers();
    }
  });

  it("el chip de etiqueta filtra por su forma normalizada y se puede quitar", async () => {
    const user = userEvent.setup();
    render(<PlantillaSelector destino="oferta" onClose={jest.fn()} onPick={jest.fn()} />);
    await screen.findByRole("button", { name: "Usar la plantilla Kit arranque DTF" });
    const grupo = screen.getByRole("group", { name: "Filtrar por etiqueta" });
    const chip = within(grupo).getByRole("button", { name: /Escuelas/ });
    expect(chip).toHaveAttribute("aria-pressed", "false");

    await user.click(chip);
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
    expect(ultimaLista()).toMatchObject({ etiquetas: ["escuelas"], carpeta_id: "c-esc" });
    expect(chip).toHaveAttribute("aria-pressed", "true");

    await user.click(chip);
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(3));
    expect(ultimaLista()).toMatchObject({ etiquetas: [] });
  });

  describe("con un cliente", () => {
    const DE = plantilla({ id: "p-de", nombre: "Kit DTF Deutsch", idioma: "de", idioma_coincide: true });
    const ES = plantilla({ id: "p-es", nombre: "Kit DTF Español", idioma: "es", idioma_coincide: false });

    beforeEach(() => {
      // El backend las devuelve mezcladas: el selector las agrupa.
      mockList.mockResolvedValue(listado([ES, DE], "de"));
    });

    it("primero las del idioma del cliente y luego «Otros idiomas» con su aviso", async () => {
      render(
        <PlantillaSelector destino="oferta" cliente={{ company_id: "c1" }}
                           onClose={jest.fn()} onPick={jest.fn()} />,
      );
      const suyas = await screen.findByRole("region", { name: "En Deutsch (el idioma del cliente)" });
      const otras = screen.getByRole("region", { name: "Otros idiomas" });
      expect(mockList).toHaveBeenCalledWith(expect.objectContaining({ company_id: "c1", carpeta_id: "c-esc" }));
      expect(screen.getByRole("note")).toHaveTextContent("Cliente en Deutsch: primero sus plantillas.");

      // El grupo del cliente va antes que el de otros idiomas.
      expect(suyas.compareDocumentPosition(otras) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(within(suyas).getByRole("button", { name: "Usar la plantilla Kit DTF Deutsch" })).toBeInTheDocument();
      expect(within(suyas).queryByRole("button", { name: "Usar la plantilla Kit DTF Español" })).not.toBeInTheDocument();

      // Las otras siguen accesibles, con el aviso.
      expect(otras).toHaveTextContent("No están en Deutsch: revisa los textos antes de enviarlos.");
      expect(within(otras).getByRole("button", { name: "Usar la plantilla Kit DTF Español" })).toBeEnabled();
    });

    it("elegir una de otro idioma pide confirmación; si se cancela no carga las líneas", async () => {
      const confirmar = jest.spyOn(window, "confirm").mockReturnValue(false);
      const onPick = jest.fn();
      const user = userEvent.setup();
      render(
        <PlantillaSelector destino="oferta" cliente={{ company_id: "c1" }}
                           onClose={jest.fn()} onPick={onPick} />,
      );
      await user.click(await screen.findByRole("button", { name: "Usar la plantilla Kit DTF Español" }));
      expect(confirmar).toHaveBeenCalledTimes(1);
      expect(confirmar).toHaveBeenCalledWith(
        "«Kit DTF Español» está en Español y el cliente en Deutsch. ¿Usarla igualmente?",
      );
      expect(mockLineas).not.toHaveBeenCalled();
      expect(onPick).not.toHaveBeenCalled();
      // Sigue abierto y se puede elegir otra.
      expect(screen.getByRole("button", { name: "Usar la plantilla Kit DTF Deutsch" })).toBeEnabled();
    });

    it("si se acepta, pide las líneas para el destino y onPick recibe plantilla, líneas y avisos", async () => {
      const confirmar = jest.spyOn(window, "confirm").mockReturnValue(true);
      const lineas = [{
        product_sku: "DM3H", product_codart: "A-DM3H", description: "Horno de curado",
        quantity: 1, unit_price: 1200, tax_rate: 21,
      }];
      mockLineas.mockResolvedValue({
        plantilla: ES, destino: "pedido", lineas, avisos: ["El pedido no lleva descuento por línea."],
      });
      const onPick = jest.fn();
      const user = userEvent.setup();
      render(
        <PlantillaSelector destino="pedido" cliente={{ company_id: "c1" }}
                           onClose={jest.fn()} onPick={onPick} />,
      );
      await user.click(await screen.findByRole("button", { name: "Usar la plantilla Kit DTF Español" }));
      expect(confirmar).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(onPick).toHaveBeenCalledWith({
        plantilla: ES, lineas, avisos: ["El pedido no lleva descuento por línea."],
      }));
      expect(mockLineas).toHaveBeenCalledWith("p-es", "pedido");
    });

    it("elegir una del idioma del cliente no pide confirmación", async () => {
      const confirmar = jest.spyOn(window, "confirm").mockReturnValue(false);
      mockLineas.mockResolvedValue({ plantilla: DE, destino: "proforma", lineas: [], avisos: [] });
      const onPick = jest.fn();
      const user = userEvent.setup();
      render(
        <PlantillaSelector destino="proforma" cliente={{ company_id: "c1" }}
                           onClose={jest.fn()} onPick={onPick} />,
      );
      await user.click(await screen.findByRole("button", { name: "Usar la plantilla Kit DTF Deutsch" }));
      await waitFor(() => expect(onPick).toHaveBeenCalledWith({ plantilla: DE, lineas: [], avisos: [] }));
      expect(mockLineas).toHaveBeenCalledWith("p-de", "proforma");
      expect(confirmar).not.toHaveBeenCalled();
    });
  });

  it("sin cliente no agrupa por idioma ni pide confirmación", async () => {
    const confirmar = jest.spyOn(window, "confirm").mockReturnValue(false);
    const P = plantilla({ idioma: "de" });
    mockLineas.mockResolvedValue({ plantilla: P, destino: "oferta", lineas: [], avisos: [] });
    const onPick = jest.fn();
    const user = userEvent.setup();
    render(<PlantillaSelector destino="oferta" onClose={jest.fn()} onPick={onPick} />);
    await user.click(await screen.findByRole("button", { name: "Usar la plantilla Kit arranque DTF" }));
    await waitFor(() => expect(onPick).toHaveBeenCalledTimes(1));
    expect(confirmar).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "Otros idiomas" })).not.toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("si fallan las líneas enseña el error y deja volver a elegir", async () => {
    mockLineas.mockRejectedValue(new Error("boom"));
    const onPick = jest.fn();
    const user = userEvent.setup();
    render(<PlantillaSelector destino="oferta" onClose={jest.fn()} onPick={onPick} />);
    const item = await screen.findByRole("button", { name: "Usar la plantilla Kit arranque DTF" });
    await user.click(item);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(onPick).not.toHaveBeenCalled();
    expect(item).toBeEnabled();
  });
});
