import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SoporteBusqueda } from "./SoporteBusqueda";
import {
  buscarSoporte,
  leerArticuloSoporte,
  type SoporteArticulo,
  type SoporteResultado,
} from "../../lib/soporteApi";

jest.mock("../../lib/soporteApi", () => ({
  buscarSoporte: jest.fn(),
  leerArticuloSoporte: jest.fn(),
}));

const mockBuscar = buscarSoporte as jest.Mock;
const mockLeer = leerArticuloSoporte as jest.Mock;

const CAMARA: SoporteResultado = {
  fuente: "flux", fuente_nombre: "FLUX Support", articulo_id: "360001",
  titulo: "Calibrar la cámara de la Beambox", extracto: "Abre Beam Studio y elige Máquina → Calibrar cámara.",
  url: "https://support.flux3dp.com/hc/es-419/articles/360001", idioma: "es",
};
const ENFOQUE: SoporteResultado = {
  ...CAMARA, articulo_id: "360002", titulo: "Ajustar el enfoque", extracto: "Con la herramienta de enfoque…",
  url: "https://support.flux3dp.com/hc/es-419/articles/360002",
};

function respuesta(resultados: SoporteResultado[], extra: { avisos?: string[]; fuentes?: string[] } = {}) {
  return { consulta: "beambox camara", resultados, avisos: extra.avisos ?? [], fuentes: extra.fuentes ?? ["flux"] };
}

const ARTICULO: SoporteArticulo = {
  ...CAMARA, texto: "Paso 1. Abre Beam Studio.\n\nPaso 2. Pieza FLUX-CAM-01.",
  html: "<h2>Calibrar</h2><p>Paso 1. Abre Beam Studio.</p>", actualizado: "2026-09-30T10:00:00Z",
};

async function buscar(user: ReturnType<typeof userEvent.setup>, texto = "beambox camara") {
  await user.type(screen.getByLabelText("Qué buscar en el soporte"), texto);
  await user.click(screen.getByRole("button", { name: "Buscar" }));
}

beforeEach(() => {
  mockBuscar.mockReset();
  mockLeer.mockReset();
});

describe("SoporteBusqueda", () => {
  it("busca en el idioma pedido y pinta título (enlace en pestaña nueva), extracto y fuente", async () => {
    mockBuscar.mockResolvedValue(respuesta([CAMARA, ENFOQUE]));
    const user = userEvent.setup();
    render(<SoporteBusqueda idioma="de-DE" />);
    // El idioma del correo («de-DE») se busca por sus dos letras.
    expect(screen.getByLabelText("Idioma de los artículos")).toHaveValue("de");
    await buscar(user);
    await waitFor(() => expect(mockBuscar).toHaveBeenCalledWith("beambox camara", { idioma: "de" }));
    const lista = await screen.findByRole("list", { name: "Artículos de soporte" });
    const items = within(lista).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    const enlace = within(items[0]).getByRole("link", { name: "Calibrar la cámara de la Beambox" });
    expect(enlace).toHaveAttribute("href", CAMARA.url);
    expect(enlace).toHaveAttribute("target", "_blank");
    expect(within(items[0]).getByText(CAMARA.extracto)).toBeInTheDocument();
    expect(within(items[0]).getByText("FLUX Support · ES")).toBeInTheDocument();
  });

  it("sin onUsar es solo consulta: no hay «Usar en el borrador» ni nada que enviar", async () => {
    mockBuscar.mockResolvedValue(respuesta([CAMARA]));
    const user = userEvent.setup();
    render(<SoporteBusqueda />);
    await buscar(user);
    await screen.findByRole("list", { name: "Artículos de soporte" });
    expect(screen.queryByRole("button", { name: /usar en el borrador/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /enviar/i })).not.toBeInTheDocument();
  });

  it("«Leer» trae el artículo y lo enseña en un iframe sin scripts, con su enlace", async () => {
    mockBuscar.mockResolvedValue(respuesta([CAMARA]));
    mockLeer.mockResolvedValue(ARTICULO);
    const user = userEvent.setup();
    render(<SoporteBusqueda />);
    await buscar(user);
    const item = (await screen.findAllByRole("listitem"))[0];
    await user.click(within(item).getByRole("button", { name: "Leer" }));
    await waitFor(() => expect(mockLeer).toHaveBeenCalledWith("flux", "360001", "es"));
    const articulo = await screen.findByRole("region", { name: "Artículo: Calibrar la cámara de la Beambox" });
    const marco = within(articulo).getByTitle("Artículo: Calibrar la cámara de la Beambox");
    expect(marco.tagName).toBe("IFRAME");
    expect(marco).toHaveAttribute("sandbox", "");
    expect(marco.getAttribute("srcdoc")).toContain("<h2>Calibrar</h2>");
    expect(within(articulo).getByRole("link", { name: "Abrir en su web" })).toHaveAttribute("href", CAMARA.url);
    expect(within(articulo).getByText(/actualizado el 30\/09\/2026/)).toBeInTheDocument();
    // Un segundo clic lo cierra.
    await user.click(within(item).getByRole("button", { name: "Cerrar el artículo" }));
    expect(screen.queryByRole("region", { name: /Artículo:/ })).not.toBeInTheDocument();
  });

  it("sin HTML, el artículo se lee como texto en párrafos", async () => {
    mockBuscar.mockResolvedValue(respuesta([CAMARA]));
    mockLeer.mockResolvedValue({ ...ARTICULO, html: "" });
    const user = userEvent.setup();
    render(<SoporteBusqueda />);
    await buscar(user);
    await user.click(await screen.findByRole("button", { name: "Leer" }));
    expect(await screen.findByText("Paso 2. Pieza FLUX-CAM-01.")).toBeInTheDocument();
    expect(screen.queryByTitle(/Artículo:/)).not.toBeInTheDocument();
  });

  it("un artículo que no se puede leer lo dice sin tirar los resultados", async () => {
    mockBuscar.mockResolvedValue(respuesta([CAMARA]));
    mockLeer.mockRejectedValue(new Error("FLUX no responde"));
    const user = userEvent.setup();
    render(<SoporteBusqueda />);
    await buscar(user);
    await user.click(await screen.findByRole("button", { name: "Leer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("FLUX no responde");
    expect(screen.getByRole("list", { name: "Artículos de soporte" })).toBeInTheDocument();
  });

  it("sin resultados lo dice claramente y no ofrece «no hay procedimiento» sin onSinResultados", async () => {
    mockBuscar.mockResolvedValue({
      ...respuesta([], { avisos: ["No se ha encontrado nada para «beambox camara»."] }),
    });
    const user = userEvent.setup();
    render(<SoporteBusqueda />);
    await buscar(user);
    expect(await screen.findByRole("status")).toHaveTextContent("No hay ningún artículo para «beambox camara».");
    // El aviso del servidor que dice lo mismo no se repite.
    expect(screen.queryByRole("list", { name: "Avisos de la búsqueda" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /no hay procedimiento/ })).not.toBeInTheDocument();
  });

  it("con onSinResultados, cero resultados ofrecen escribir diciendo que no hay procedimiento", async () => {
    mockBuscar.mockResolvedValue(respuesta([]));
    const onSinResultados = jest.fn();
    const user = userEvent.setup();
    render(<SoporteBusqueda onSinResultados={onSinResultados} />);
    await buscar(user);
    await user.click(await screen.findByRole("button", { name: "Escribir diciendo que no hay procedimiento" }));
    expect(onSinResultados).toHaveBeenCalledWith("beambox camara");
  });

  it("si no se ha podido mirar en ninguna fuente, no se ofrece decir que no hay procedimiento", async () => {
    mockBuscar.mockResolvedValue(respuesta([], {
      fuentes: [], avisos: ["No hay ningún centro de soporte activo en la lista."],
    }));
    const user = userEvent.setup();
    render(<SoporteBusqueda onSinResultados={jest.fn()} />);
    await buscar(user);
    expect(await screen.findByRole("status")).toHaveTextContent("No hay ningún artículo");
    expect(screen.getByRole("list", { name: "Avisos de la búsqueda" })).toHaveTextContent(
      "No hay ningún centro de soporte activo en la lista.");
    expect(screen.queryByRole("button", { name: /no hay procedimiento/ })).not.toBeInTheDocument();
  });

  it("con onUsar, cada resultado lleva «Usar en el borrador» y pasa el resultado entero", async () => {
    mockBuscar.mockResolvedValue(respuesta([CAMARA, ENFOQUE], { avisos: ["artisJet: no responde."] }));
    const onUsar = jest.fn();
    const user = userEvent.setup();
    render(<SoporteBusqueda onUsar={onUsar} onSinResultados={jest.fn()} />);
    await buscar(user);
    // Los avisos del backend se ven.
    expect(await screen.findByRole("list", { name: "Avisos de la búsqueda" })).toHaveTextContent("artisJet: no responde.");
    const items = within(screen.getByRole("list", { name: "Artículos de soporte" })).getAllByRole("listitem");
    expect(within(items[0]).getByRole("button", { name: "Usar en el borrador" })).toBeInTheDocument();
    await user.click(within(items[1]).getByRole("button", { name: "Usar en el borrador" }));
    expect(onUsar).toHaveBeenCalledWith(ENFOQUE);
    expect(screen.queryByRole("button", { name: /no hay procedimiento/ })).not.toBeInTheDocument();
  });

  it("con consulta inicial busca al abrir; un error de red se enseña y no es «sin resultados»", async () => {
    mockBuscar.mockRejectedValue(new Error("Ningún centro de soporte ha respondido"));
    render(<SoporteBusqueda consultaInicial="hexa láser no enciende" onSinResultados={jest.fn()} />);
    await waitFor(() => expect(mockBuscar).toHaveBeenCalledWith("hexa láser no enciende", { idioma: "es" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ningún centro de soporte ha respondido");
    expect(screen.queryByText(/No hay ningún artículo/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /no hay procedimiento/ })).not.toBeInTheDocument();
  });

  it("una búsqueda vacía no llama al servidor; el envío no sube a un formulario de fuera", async () => {
    // Como en el compositor: lo de fuera escucha los envíos que suben.
    const fuera = jest.fn();
    const user = userEvent.setup();
    render(<div onSubmit={fuera}><SoporteBusqueda /></div>);
    await user.click(screen.getByRole("button", { name: "Buscar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Escribe qué buscar");
    expect(mockBuscar).not.toHaveBeenCalled();
    expect(fuera).not.toHaveBeenCalled();
  });
});
