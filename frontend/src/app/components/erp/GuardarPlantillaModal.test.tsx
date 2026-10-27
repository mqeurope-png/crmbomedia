import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GuardarPlantillaModal } from "./GuardarPlantillaModal";
import {
  createPlantilla,
  getPlantillasMeta,
  listPlantillaCarpetas,
  listPlantillaEtiquetas,
} from "../../lib/erpApi";

/** «Guardar como plantilla» desde una proforma, un albarán o un pedido: el
 *  servidor copia las líneas (solo se LEE el documento) a una plantilla de
 *  documento con nombre, descripción, idioma, carpeta y etiquetas. */

jest.mock("../../lib/erpApi", () => ({
  createPlantilla: jest.fn(),
  getPlantillasMeta: jest.fn(),
  listPlantillaCarpetas: jest.fn(),
  listPlantillaEtiquetas: jest.fn(),
}));

const mockCreate = createPlantilla as jest.Mock;
const mockMeta = getPlantillasMeta as jest.Mock;
const mockCarpetas = listPlantillaCarpetas as jest.Mock;
const mockEtiquetas = listPlantillaEtiquetas as jest.Mock;

function carpeta(id: string, nombre: string, parent_id: string | null, children: unknown[] = []) {
  return { id, nombre, parent_id, num_plantillas: 0, es_defecto: false, children };
}

const ARBOL = {
  items: [
    carpeta("c-dtf", "DTF", null, [carpeta("c-esc", "Escuelas", "c-dtf")]),
    carpeta("c-uv", "UV", null),
  ],
  carpeta_defecto: null,
};

function meta(over = {}) {
  return {
    idiomas: ["es", "en", "de", "fr", "nl"], destinos: ["oferta", "proforma", "pedido"],
    max_profundidad: 3, carpeta_defecto: null, gestiona_acceso: false,
    acceso_restringido: false, ...over,
  };
}

const PLANTILLA = {
  id: "pl1", nombre: "Kit DTF escuelas", descripcion: "Para ferias", activa: true,
  idioma: "de", carpeta_id: "c-esc", carpeta: "DTF / Escuelas", etiquetas: ["DTF", "escuelas"],
  origen_tipo: "presupuesto", origen_serie: 2, origen_codigo: 40, origen_order_id: null,
  origen_numero: "2-000040", num_lineas: 3, created_at: null, updated_at: null,
};

beforeEach(() => {
  mockCreate.mockReset();
  mockCreate.mockResolvedValue(PLANTILLA);
  mockMeta.mockReset();
  mockMeta.mockResolvedValue(meta());
  mockCarpetas.mockReset();
  mockCarpetas.mockResolvedValue(ARBOL);
  mockEtiquetas.mockReset();
  mockEtiquetas.mockResolvedValue([
    { etiqueta: "DTF", norm: "dtf", num: 4 }, { etiqueta: "FESPA", norm: "fespa", num: 1 },
  ]);
});

describe("GuardarPlantillaModal", () => {
  it("desde una proforma: propone el nombre, preselecciona la carpeta por defecto y manda el payload exacto", async () => {
    mockMeta.mockResolvedValue(meta({ carpeta_defecto: "c-esc" }));
    const onSaved = jest.fn();
    const user = userEvent.setup();
    render(<GuardarPlantillaModal origen={{ tipo: "presupuesto", serie: 2, codigo: 40 }}
                                  numero="2-000040" nombreSugerido="  Kit DTF escuelas "
                                  onClose={jest.fn()} onSaved={onSaved} />);
    expect(screen.getByRole("dialog", { name: "Guardar la proforma 2-000040 como plantilla" }))
      .toHaveTextContent("No se toca nada en FACTUSOL");
    expect(screen.getByLabelText("Nombre de la plantilla")).toHaveValue("Kit DTF escuelas");
    // Sin idioma sugerido, español.
    expect(screen.getByLabelText("Idioma de la plantilla")).toHaveValue("es");

    // La carpeta por defecto del usuario (una subcarpeta) viene elegida.
    await screen.findByRole("option", { name: "DTF / Escuelas" });
    await waitFor(() => expect(screen.getByLabelText("Carpeta de la plantilla")).toHaveValue("c-esc"));
    expect(screen.getByRole("option", { name: "— Sin carpeta —" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Descripción de la plantilla"), "Para ferias");
    await user.selectOptions(screen.getByLabelText("Idioma de la plantilla"), "de");
    await user.type(screen.getByLabelText("Añadir etiqueta"), "DTF{Enter}");
    await user.type(screen.getByLabelText("Añadir etiqueta"), "escuelas{Enter}");
    // Intro en las etiquetas añade la etiqueta, no envía el formulario.
    expect(mockCreate).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({
      nombre: "Kit DTF escuelas",
      descripcion: "Para ferias",
      idioma: "de",
      carpeta_id: "c-esc",
      etiquetas: ["DTF", "escuelas"],
      origen: { tipo: "presupuesto", serie: 2, codigo: 40 },
    }));
    expect(mockCreate).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(PLANTILLA));
  });

  it("desde un albarán: sin nombre no deja guardar; idioma sugerido y otra carpeta elegida a mano", async () => {
    const user = userEvent.setup();
    render(<GuardarPlantillaModal origen={{ tipo: "albaran", serie: 5, codigo: 500010 }}
                                  numero="5-500010" idiomaSugerido="en"
                                  onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(screen.getByRole("dialog", { name: "Guardar el albarán 5-500010 como plantilla" }))
      .toBeInTheDocument();
    expect(screen.getByLabelText("Idioma de la plantilla")).toHaveValue("en");
    await screen.findByRole("option", { name: "UV" });
    // Sin carpeta por defecto: «Sin carpeta».
    expect(screen.getByLabelText("Carpeta de la plantilla")).toHaveValue("");

    const guardar = screen.getByRole("button", { name: "Guardar plantilla" });
    expect(guardar).toBeDisabled();
    await user.type(screen.getByLabelText("Nombre de la plantilla"), "   ");
    expect(guardar).toBeDisabled();
    await user.type(screen.getByLabelText("Nombre de la plantilla"), "Tinta UV");
    expect(guardar).toBeEnabled();
    await user.selectOptions(screen.getByLabelText("Carpeta de la plantilla"), "c-uv");
    await user.click(guardar);
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({
      nombre: "Tinta UV",
      descripcion: null,
      idioma: "en",
      carpeta_id: "c-uv",
      etiquetas: [],
      origen: { tipo: "albaran", serie: 5, codigo: 500010 },
    }));
  });

  it("desde un pedido: origen con el order_id y, sin carpeta, carpeta_id null", async () => {
    const onSaved = jest.fn();
    const user = userEvent.setup();
    render(<GuardarPlantillaModal origen={{ tipo: "pedido", order_id: "o-1" }}
                                  numero="MANUAL-000777" nombreSugerido="Pedido feria"
                                  onClose={jest.fn()} onSaved={onSaved} />);
    expect(screen.getByRole("dialog", { name: "Guardar el pedido MANUAL-000777 como plantilla" }))
      .toHaveTextContent("MANUAL-000777");
    await screen.findByRole("option", { name: "DTF" });
    await user.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({
      nombre: "Pedido feria",
      descripcion: null,
      idioma: "es",
      carpeta_id: null,
      etiquetas: [],
      origen: { tipo: "pedido", order_id: "o-1" },
    }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  });

  // «de» + «el pedido» / «el albarán» se contrae: «del pedido…».
  it("el texto explicativo usa la contracción «del» con el pedido y el albarán", async () => {
    const { unmount } = render(
      <GuardarPlantillaModal origen={{ tipo: "pedido", order_id: "o-1" }} numero="MANUAL-000777"
                             onClose={jest.fn()} onSaved={jest.fn()} />,
    );
    await screen.findByRole("option", { name: "DTF" });
    expect(screen.getByRole("dialog")).toHaveTextContent("Se copian las líneas del pedido MANUAL-000777");
    unmount();
    render(<GuardarPlantillaModal origen={{ tipo: "albaran", serie: 5, codigo: 500010 }} numero="5-500010"
                                  onClose={jest.fn()} onSaved={jest.fn()} />);
    await screen.findByRole("option", { name: "DTF" });
    expect(screen.getByRole("dialog")).toHaveTextContent("Se copian las líneas del albarán 5-500010");
  });

  it("una carpeta por defecto que el usuario ya no ve no se preselecciona", async () => {
    mockMeta.mockResolvedValue(meta({ carpeta_defecto: "c-borrada" }));
    render(<GuardarPlantillaModal origen={{ tipo: "pedido", order_id: "o-1" }}
                                  onClose={jest.fn()} onSaved={jest.fn()} />);
    // Sin nº visible, el título solo dice el tipo de documento.
    expect(screen.getByRole("dialog", { name: "Guardar el pedido como plantilla" })).toBeInTheDocument();
    await screen.findByRole("option", { name: "DTF" });
    expect(screen.getByLabelText("Carpeta de la plantilla")).toHaveValue("");
  });

  it("un usuario con acceso restringido no tiene «Sin carpeta»: se propone su primera carpeta", async () => {
    mockMeta.mockResolvedValue(meta({ acceso_restringido: true }));
    mockCarpetas.mockResolvedValue({ items: [carpeta("c-uv", "UV", null), carpeta("c-dtf", "DTF", null)], carpeta_defecto: null });
    const user = userEvent.setup();
    render(<GuardarPlantillaModal origen={{ tipo: "albaran", serie: 1, codigo: 7 }}
                                  nombreSugerido="Kit UV"
                                  onClose={jest.fn()} onSaved={jest.fn()} />);
    await screen.findByRole("option", { name: "UV" });
    await waitFor(() => expect(screen.getByLabelText("Carpeta de la plantilla")).toHaveValue("c-uv"));
    expect(screen.queryByRole("option", { name: "— Sin carpeta —" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({ carpeta_id: "c-uv" }),
    ));
  });

  it("acceso restringido y ninguna carpeta visible: no deja guardar", async () => {
    mockMeta.mockResolvedValue(meta({ acceso_restringido: true }));
    mockCarpetas.mockResolvedValue({ items: [], carpeta_defecto: null });
    render(<GuardarPlantillaModal origen={{ tipo: "albaran", serie: 1, codigo: 7 }}
                                  nombreSugerido="Kit UV"
                                  onClose={jest.fn()} onSaved={jest.fn()} />);
    await waitFor(() => expect(screen.queryByRole("option", { name: "— Sin carpeta —" })).toBeNull());
    expect(screen.getByRole("button", { name: "Guardar plantilla" })).toBeDisabled();
  });

  it("etiquetas: se añaden con Intro (sin repetir mayúsculas ni acentos) y se quitan con ×", async () => {
    const user = userEvent.setup();
    render(<GuardarPlantillaModal origen={{ tipo: "presupuesto", serie: 2, codigo: 40 }}
                                  nombreSugerido="Kit"
                                  onClose={jest.fn()} onSaved={jest.fn()} />);
    await screen.findByRole("option", { name: "DTF" });
    const input = screen.getByLabelText("Añadir etiqueta");
    await user.type(input, "DTF{Enter}");
    await user.type(input, "Escuelas{Enter}");
    await user.type(input, "ESCUÉLAS{Enter}"); // repetida: no se añade
    await user.type(input, "FESPA{Enter}");
    const lista = screen.getByRole("list", { name: "Etiquetas" });
    expect(lista).toHaveTextContent("DTF");
    expect(lista).toHaveTextContent("Escuelas");
    expect(lista).toHaveTextContent("FESPA");
    expect(screen.getAllByRole("button", { name: /^Quitar la etiqueta / })).toHaveLength(3);

    await user.click(screen.getByRole("button", { name: "Quitar la etiqueta DTF" }));
    expect(screen.queryByRole("button", { name: "Quitar la etiqueta DTF" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({ etiquetas: ["Escuelas", "FESPA"] }),
    ));
  });

  it("un error del servidor se enseña y el modal sigue abierto; Cancelar cierra", async () => {
    mockCreate.mockRejectedValue(new Error("El albarán no tiene líneas"));
    const onClose = jest.fn();
    const onSaved = jest.fn();
    const user = userEvent.setup();
    render(<GuardarPlantillaModal origen={{ tipo: "albaran", serie: 5, codigo: 500010 }}
                                  numero="5-500010" nombreSugerido="Placas"
                                  onClose={onClose} onSaved={onSaved} />);
    await screen.findByRole("option", { name: "DTF" });
    await user.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El albarán no tiene líneas");
    expect(onSaved).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Guardar el albarán 5-500010 como plantilla" }))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Guardar plantilla" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("si no se pueden leer las carpetas, se guarda igual (sin carpeta)", async () => {
    mockCarpetas.mockRejectedValue(new Error("caído"));
    const user = userEvent.setup();
    render(<GuardarPlantillaModal origen={{ tipo: "pedido", order_id: "o-9" }}
                                  nombreSugerido="Kit"
                                  onClose={jest.fn()} onSaved={jest.fn()} />);
    await waitFor(() => expect(mockCarpetas).toHaveBeenCalled());
    expect(screen.getByRole("option", { name: "— Sin carpeta —" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({ carpeta_id: null, origen: { tipo: "pedido", order_id: "o-9" } }),
    ));
  });
});
