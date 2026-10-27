import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { WebFragmento } from "../../lib/websApi";
import { WebsBusqueda, precioWeb } from "./WebsBusqueda";

jest.mock("../../lib/websApi", () => ({ buscarEnWebs: jest.fn() }));

const api = jest.requireMock("../../lib/websApi") as { buscarEnWebs: jest.Mock };

function fragmento(over: Partial<WebFragmento> = {}): WebFragmento {
  return {
    id: "w-1", sitio: "fluxlasers", dominio: "fluxlasers.eu", tipo: "producto", idioma: "de",
    titulo: "beamo", url: "https://www.fluxlasers.eu/de/produkt/beamo/",
    fragmento: "Der kompakte Desktop-Laser mit 30 W CO2-Röhre.",
    imagen: "https://www.fluxlasers.eu/wp-content/uploads/beamo.jpg", galeria: [],
    categorias: ["Lasermaschinen"], precio_web: 1990, precio_web_texto: "1.990,00 €",
    precio_iva: "sin_iva", moneda: "EUR", ...over,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("WebsBusqueda", () => {
  it("busca en el idioma del correo y pinta título, web, tipo, idioma, fragmento, imagen y enlace", async () => {
    api.buscarEnWebs.mockResolvedValueOnce([fragmento()]);
    render(<WebsBusqueda idioma="de" />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Qué buscar en nuestras webs"), "beamo");
    await user.click(screen.getByRole("button", { name: /Buscar/ }));
    await waitFor(() => expect(api.buscarEnWebs).toHaveBeenCalledWith("beamo", { idioma: "de", tipo: null }));
    const item = (await screen.findAllByRole("listitem"))[0];
    expect(within(item).getByRole("link", { name: "beamo" })).toHaveAttribute(
      "href", "https://www.fluxlasers.eu/de/produkt/beamo/");
    expect(item).toHaveTextContent("fluxlasers.eu · Producto · DE");
    expect(item).toHaveTextContent("Der kompakte Desktop-Laser");
    expect(item.querySelector("img")).toHaveAttribute("src", "https://www.fluxlasers.eu/wp-content/uploads/beamo.jpg");
    // Sin `onAnadir` no hay nada que añadir.
    expect(within(item).queryByRole("button", { name: "Añadir al borrador" })).not.toBeInTheDocument();
  });

  it("el precio es el de la web, dicho así (sin IVA) y nunca como precio de oferta", async () => {
    api.buscarEnWebs.mockResolvedValueOnce([fragmento()]);
    render(<WebsBusqueda />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Qué buscar en nuestras webs"), "beamo{Enter}");
    const item = (await screen.findAllByRole("listitem"))[0];
    expect(item).toHaveTextContent("precio en la web (sin IVA): 1.990,00 €");
    expect(item).toHaveTextContent("no es precio de oferta");
    expect(precioWeb({ precio_web: 1200, precio_web_texto: null, precio_iva: "con_iva", moneda: "EUR" }))
      .toEqual({ importe: expect.stringContaining("1200,00"), etiqueta: "precio en la web (con IVA)" });
    expect(precioWeb({ precio_web: null, precio_web_texto: null, precio_iva: null, moneda: null })).toBeNull();
  });

  it("«Añadir al borrador» pasa el resultado; lo ya elegido sale como añadido y con el máximo no se añade más", async () => {
    api.buscarEnWebs.mockResolvedValue([
      fragmento(), fragmento({ id: "w-2", titulo: "HEXA", url: "https://www.fluxlasers.eu/de/produkt/hexa/" }),
    ]);
    const onAnadir = jest.fn();
    const { rerender } = render(<WebsBusqueda onAnadir={onAnadir} elegidos={["w-2"]} maximo={4} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Qué buscar en nuestras webs"), "laser{Enter}");
    const [primero, segundo] = await screen.findAllByRole("listitem");
    await user.click(within(primero).getByRole("button", { name: "Añadir al borrador" }));
    expect(onAnadir).toHaveBeenCalledWith(expect.objectContaining({ id: "w-1", url: "https://www.fluxlasers.eu/de/produkt/beamo/" }));
    expect(within(segundo).getByRole("button", { name: "Añadida al borrador" })).toBeDisabled();
    rerender(<WebsBusqueda onAnadir={onAnadir} elegidos={["a", "b", "c", "d"]} maximo={4} />);
    expect(screen.getByText(/Ya hay 4 páginas elegidas/)).toBeInTheDocument();
    for (const b of screen.getAllByRole("button", { name: "Añadir al borrador" })) expect(b).toBeDisabled();
  });

  it("sin resultados lo dice; vacío no busca", async () => {
    api.buscarEnWebs.mockResolvedValueOnce([]);
    render(<WebsBusqueda />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Buscar/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("Escribe qué buscar");
    expect(api.buscarEnWebs).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("Qué buscar en nuestras webs"), "zzz{Enter}");
    expect(await screen.findByRole("status")).toHaveTextContent("No hay nada en las webs con «zzz»");
  });

  it("dentro del compositor, el envío del buscador no sube al formulario del correo", async () => {
    api.buscarEnWebs.mockResolvedValueOnce([]);
    // El envío sube por el árbol de React aunque el buscador vaya en un portal:
    // un ancestro con onSubmit lo vería (como el <form> del compositor).
    const enviarCorreo = jest.fn((e: React.FormEvent) => e.preventDefault());
    render(<div onSubmit={enviarCorreo}><WebsBusqueda /></div>);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Qué buscar en nuestras webs"), "hexa{Enter}");
    await waitFor(() => expect(api.buscarEnWebs).toHaveBeenCalledTimes(1));
    expect(enviarCorreo).not.toHaveBeenCalled();
  });
});
