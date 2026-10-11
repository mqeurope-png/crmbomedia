import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConocimientoPanel } from "./ConocimientoPanel";
import {
  comprobarKbRutas,
  createKbRuta,
  getConocimiento,
  resolverConocimiento,
  sincronizarFlux,
  updateKbRuta,
  updateSitioWeb,
  type Conocimiento,
  type KbRuta,
  type SitioWeb,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getConocimiento: jest.fn(),
  createSitioWeb: jest.fn(),
  updateSitioWeb: jest.fn(),
  createKbRuta: jest.fn(),
  updateKbRuta: jest.fn(),
  comprobarKbRutas: jest.fn(),
  createFluxCategoria: jest.fn(),
  updateFluxCategoria: jest.fn(),
  sincronizarFlux: jest.fn(),
  resolverConocimiento: jest.fn(),
}));

const mockGet = getConocimiento as jest.Mock;
const mockCreateRuta = createKbRuta as jest.Mock;
const mockUpdateRuta = updateKbRuta as jest.Mock;
const mockUpdateSitio = updateSitioWeb as jest.Mock;
const mockComprobar = comprobarKbRutas as jest.Mock;
const mockSincronizar = sincronizarFlux as jest.Mock;
const mockResolver = resolverConocimiento as jest.Mock;

function sitio(over: Partial<SitioWeb> & { clave: string; dominio: string }): SitioWeb {
  return {
    host: over.dominio, dominios_extra: [], marca: over.clave, empresa: "MQ Europe", tipo: "web",
    idioma_defecto: "es", idiomas: ["es"], plugin_traduccion: null, prefijo_idioma_defecto: false,
    slugs_traducidos: false, soporte_propio: false, remitente_defecto: null, posicion: 0,
    activo: true, ...over,
  };
}

function ruta(over: Partial<KbRuta> & { id: string; sitio: string; url: string }): KbRuta {
  return {
    tipo: "soporte", idioma: "es", interes: "", nota: null, activo: true, posicion: 0,
    comprobado_en: null, comprobado_http: null, comprobado_error: null, ...over,
  };
}

const MQ_EN = "https://artisjet-printers.eu/en/technical-support/";
const DATOS: Conocimiento = {
  sitios: [
    sitio({ clave: "mqeurope", dominio: "mqeurope.com", host: "www.mqeurope.com", marca: "MQ Europe",
      idioma_defecto: "en", idiomas: ["en", "fr", "nl"], plugin_traduccion: "qtranslate",
      remitente_defecto: "sales@mqeurope.com" }),
    sitio({ clave: "fluxlasers", dominio: "fluxlasers.es", dominios_extra: ["fluxlasers.eu"],
      marca: "FLUX", idiomas: ["es", "en", "de"], plugin_traduccion: "wpml", soporte_propio: true }),
    sitio({ clave: "bomedia", dominio: "bomedia.net", tipo: "correo", marca: "Bomedia" }),
  ],
  sitios_de_la_base: true,
  rutas: [
    ruta({ id: "r1", sitio: "mqeurope", idioma: "en", url: MQ_EN,
      comprobado_en: "2026-10-11T10:00:00Z", comprobado_http: 200 }),
    ruta({ id: "r2", sitio: "fluxlasers", idioma: "de", url: "https://fluxlasers.eu/de/support/",
      comprobado_en: "2026-10-11T10:00:00Z", comprobado_http: 404 }),
    ruta({ id: "p1", tipo: "producto", sitio: "mqeurope", idioma: "", interes: "dtf",
      url: "https://www.mqeurope.com/dtf-textile-printing/" }),
  ],
  flux: {
    categorias: [
      { categoria_id: "10647778378639", modelo: "Beambox II", palabras: ["beambox ii", "beambox 2"],
        posicion: 3, activo: true,
        enlace_es: "https://support.flux3dp.com/hc/es-419/categories/10647778378639" },
    ],
    locales: { es: "es-419", en: "en-us" },
    articulos_por_idioma: { es: 120, en: 300 },
    idos: 2,
    ultima_sincronizacion: {
      id: "s1", origen: "programada", estado: "ok", inicio: "2026-10-11T04:30:00Z",
      fin: "2026-10-11T04:31:00Z", articulos: 420, nuevos: 0, actualizados: 3, idos: 0,
      locales: {}, detalle: null,
    },
  },
  idiomas: ["de", "en", "es", "fr", "nl"],
};

beforeEach(() => {
  mockGet.mockReset();
  mockCreateRuta.mockReset();
  mockUpdateRuta.mockReset();
  mockUpdateSitio.mockReset();
  mockComprobar.mockReset();
  mockSincronizar.mockReset();
  mockResolver.mockReset();
  mockGet.mockResolvedValue(DATOS);
});

describe("ConocimientoPanel (Configuración ERP → Base de conocimiento)", () => {
  it("enseña sitios, rutas con su comprobación, páginas por interés y el estado del índice de FLUX", async () => {
    render(<ConocimientoPanel canEdit />);
    expect(await screen.findByRole("cell", { name: "www.mqeurope.com" })).toBeInTheDocument();
    expect(screen.getByText("+ fluxlasers.eu")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "solo correo" })).toBeInTheDocument();
    expect(screen.getByLabelText("URL de mqeurope en")).toHaveValue(MQ_EN);
    expect(screen.getByText(/^200 · /)).toBeInTheDocument();
    expect(screen.getByText(/^404 · /)).toBeInTheDocument();
    expect(screen.getByLabelText("URL de mqeurope dtf")).toHaveValue(
      "https://www.mqeurope.com/dtf-textile-printing/");
    expect(screen.getByText("castellano 120 · inglés 300")).toBeInTheDocument();
    expect(screen.getByText(/2 desaparecidos/)).toBeInTheDocument();
    expect(screen.getByLabelText("Palabras de Beambox II")).toHaveValue("beambox ii, beambox 2");
    expect(screen.getByLabelText("Soporte propio fluxlasers")).toBeChecked();
  });

  it("añade una ruta de soporte y pinta lo que devuelve el servidor", async () => {
    const nueva = ruta({ id: "r3", sitio: "mqeurope", idioma: "de",
      url: "https://www.mqeurope.com/de/technischer-support/" });
    mockCreateRuta.mockResolvedValue({ ...DATOS, rutas: [...DATOS.rutas, nueva] });
    const user = userEvent.setup();
    render(<ConocimientoPanel canEdit />);
    await screen.findByRole("cell", { name: "www.mqeurope.com" });
    await user.selectOptions(screen.getByLabelText("Web de la ruta nueva (soporte)"), "mqeurope");
    await user.clear(screen.getByLabelText("Idioma de la ruta nueva"));
    await user.type(screen.getByLabelText("Idioma de la ruta nueva"), "de");
    await user.type(screen.getByLabelText("URL de la ruta nueva (soporte)"), nueva.url);
    await user.click(screen.getByRole("button", { name: "Añadir ruta" }));
    await waitFor(() => expect(mockCreateRuta).toHaveBeenCalledWith({
      tipo: "soporte", sitio: "mqeurope", url: nueva.url, nota: undefined, idioma: "de",
    }));
    expect(await screen.findByLabelText("URL de mqeurope de")).toHaveValue(nueva.url);
  });

  it("desactiva una ruta y guarda una URL cambiada por su API, sin «Guardar cambios»", async () => {
    mockUpdateRuta.mockResolvedValue({
      ...DATOS, rutas: DATOS.rutas.map((r) => (r.id === "r2" ? { ...r, activo: false } : r)),
    });
    const user = userEvent.setup();
    render(<ConocimientoPanel canEdit />);
    await screen.findByRole("cell", { name: "www.mqeurope.com" });
    await user.click(screen.getByLabelText("Activa fluxlasers de"));
    await waitFor(() => expect(mockUpdateRuta).toHaveBeenCalledWith("r2", { activo: false }));
    expect(await screen.findByLabelText("Activa fluxlasers de")).not.toBeChecked();
    // La URL editada enseña «Guardar» solo cuando cambia.
    const url = screen.getByLabelText("URL de mqeurope en");
    await user.clear(url);
    await user.type(url, "https://artisjet-printers.eu/en/support/");
    const fila = url.closest("tr") as HTMLElement;
    await user.click(within(fila).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockUpdateRuta).toHaveBeenCalledWith("r1", {
      url: "https://artisjet-printers.eu/en/support/",
    }));
  });

  it("«Comprobar que las URL devuelven 200» resume lo que falla y actualiza la tabla", async () => {
    mockComprobar.mockResolvedValue({
      resultados: [
        { id: "r1", url: MQ_EN, http: 200, ok: true, error: null },
        { id: "r2", url: "https://fluxlasers.eu/de/support/", http: 404, ok: false, error: null },
      ],
      rutas: DATOS.rutas.map((r) => (r.id === "r1"
        ? { ...r, comprobado_en: "2026-10-12T09:00:00Z", comprobado_http: 200 } : r)),
    });
    const user = userEvent.setup();
    render(<ConocimientoPanel canEdit />);
    await screen.findByRole("cell", { name: "www.mqeurope.com" });
    await user.click(screen.getByRole("button", { name: "Comprobar que las URL devuelven 200" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "2 URL comprobadas, 1 con problema: https://fluxlasers.eu/de/support/.");
  });

  it("«Sincronizar ahora» y «Probar» hablan con sus endpoints y enseñan el resultado", async () => {
    mockSincronizar.mockResolvedValue({
      sincronizacion: { id: "s2", origen: "manual", estado: "ok", inicio: "2026-10-12T09:00:00Z",
        fin: null, articulos: 425, nuevos: 5, actualizados: 0, idos: 0, locales: {}, detalle: null },
      articulos_por_idioma: { es: 125, en: 300 }, idos: 2,
      ultima_sincronizacion: null,
    });
    mockResolver.mockResolvedValue({
      soporte: { url: "https://fluxlasers.eu/de/support/", origen: "ruta", sitio: "fluxlasers",
        idioma: "de", etiqueta: null },
      producto: null,
      flux: {
        modelo: "Beambox II", categoria_id: "10647778378639", intencion: "kb",
        enlace: { url: "https://support.flux3dp.com/hc/de-de/categories/10647778378639",
          origen: "flux_kb", sitio: null, idioma: "de", etiqueta: "Beambox II" },
        kb: { url: "https://support.flux3dp.com/hc/de-de/categories/10647778378639",
          origen: "flux_kb", sitio: null, idioma: "de", etiqueta: "Beambox II" },
        soporte: null,
        articulos: [{ titulo: "Kamera kalibrieren", url: "https://support.flux3dp.com/hc/de-de/articles/1",
          outdated: false }],
      },
    });
    const user = userEvent.setup();
    render(<ConocimientoPanel canEdit />);
    await screen.findByRole("cell", { name: "www.mqeurope.com" });
    await user.click(screen.getByRole("button", { name: "Sincronizar ahora" }));
    expect(await screen.findByText(
      "Sincronizado: 425 artículos (5 nuevos, 0 cambiados, 0 desaparecidos).")).toBeInTheDocument();
    expect(screen.getByText("castellano 125 · inglés 300")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Web para probar"), "fluxlasers");
    await user.type(screen.getByLabelText("Texto para probar"), "Wie kalibriere ich die Kamera der Beambox II?");
    await user.click(screen.getByRole("button", { name: "Probar" }));
    await waitFor(() => expect(mockResolver).toHaveBeenCalledWith({
      sitio: "fluxlasers", idioma: "de", interes: null,
      texto: "Wie kalibriere ich die Kamera der Beambox II?",
    }));
    const resultado = await screen.findByLabelText("Enlaces que saldrían");
    expect(within(resultado).getByText(/Beambox II · cómo se hace → KB de FLUX/)).toBeInTheDocument();
    expect(within(resultado).getByRole("link", { name: "Kamera kalibrieren" })).toHaveAttribute(
      "href", "https://support.flux3dp.com/hc/de-de/articles/1");
  });

  it("sin permiso de edición enseña las tablas sin altas y con los enlaces como texto", async () => {
    render(<ConocimientoPanel canEdit={false} />);
    expect(await screen.findByRole("cell", { name: "www.mqeurope.com" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Añadir ruta" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Añadir sitio" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: MQ_EN })).toHaveAttribute("href", MQ_EN);
    expect(screen.getByRole("button", { name: "Sincronizar ahora" })).toBeDisabled();
  });
});
