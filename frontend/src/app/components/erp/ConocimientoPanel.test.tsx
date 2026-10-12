import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConocimientoPanel } from "./ConocimientoPanel";
import {
  comprobarKbRutas,
  createInteresProducto,
  createKbRuta,
  deleteInteresProducto,
  getCatalogoProductos,
  getConocimiento,
  resolverConocimiento,
  sincronizarCatalogo,
  sincronizarFlux,
  updateInteresProducto,
  updatePalabrasInteres,
  updateKbRuta,
  updateSitioWeb,
  type CatalogoProducto,
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
  getCatalogoProductos: jest.fn(),
  sincronizarCatalogo: jest.fn(),
  updateCatalogoMarca: jest.fn(),
  createInteresProducto: jest.fn(),
  updateInteresProducto: jest.fn(),
  deleteInteresProducto: jest.fn(),
  updatePalabrasInteres: jest.fn(),
}));

const mockGet = getConocimiento as jest.Mock;
const mockCreateRuta = createKbRuta as jest.Mock;
const mockUpdateRuta = updateKbRuta as jest.Mock;
const mockUpdateSitio = updateSitioWeb as jest.Mock;
const mockComprobar = comprobarKbRutas as jest.Mock;
const mockSincronizar = sincronizarFlux as jest.Mock;
const mockResolver = resolverConocimiento as jest.Mock;
const mockProductos = getCatalogoProductos as jest.Mock;
const mockSincronizarCatalogo = sincronizarCatalogo as jest.Mock;
const mockCreateInteres = createInteresProducto as jest.Mock;
const mockUpdateInteres = updateInteresProducto as jest.Mock;
const mockDeleteInteres = deleteInteresProducto as jest.Mock;
const mockPalabras = updatePalabrasInteres as jest.Mock;

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
  catalogo: {
    fuentes: {
      compositor: {
        productos: 90, por_tipo: { maquina: 90 }, por_idioma: { es: 18, fr: 18, de: 18, en: 18, nl: 18 },
        clave_configurada: true,
        ultima_sincronizacion: {
          id: "c1", fuente: "compositor", origen: "programada", estado: "ok",
          inicio: "2026-10-11T04:45:00Z", fin: "2026-10-11T04:45:02Z", productos: 90, nuevos: 0,
          actualizados: 2, idos: 0, origen_actualizado_en: "2026-09-20T08:00:00Z", resumen: {},
          detalle: null,
        },
      },
      woocommerce: {
        productos: 1544, por_tipo: { maquina: 84, consumible: 1300, repuesto: 160 },
        por_idioma: { es: 386, en: 386, de: 386, fr: 386 }, por_tienda: { fluxlasers: 1544 },
        ultima_sincronizacion: {
          id: "w1", fuente: "woocommerce", origen: "programada", estado: "parcial",
          inicio: "2026-10-11T04:46:00Z", fin: null, productos: 1544, nuevos: 0, actualizados: 0,
          idos: 0, origen_actualizado_en: null, resumen: {}, detalle: "mboprinters:es: ConnectError",
        },
      },
    },
    idos: 0,
    con_precio: 82 + 1544,
    descartados: 2,
    sin_precio_publico: 1,
    marcas: [
      { nombre: "MBO", logo: null, color: null, urls: { es: "https://mboprinters.com/" }, idioma_defecto: "es", ida: false },
      { nombre: "artisJet", logo: null, color: null, urls: {}, idioma_defecto: "es", ida: false },
    ],
    iva_por_fuente: { compositor: null, woocommerce: "con" },
    precio_meses_orientativo: 6,
    tipos: ["maquina", "consumible", "repuesto"],
  },
  interes_productos: {
    filas: [
      { id: "m1", interes: "uv_mediano", etiqueta: "UV LED mediano formato", marca: "artisJet", patron: "5000U",
        nota: null, posicion: 0, activo: true,
        coincide: [{ origen_id: "artisjet-5000u", nombre: "artisJet 5000U", marca: "artisJet" }] },
      { id: "m2", interes: "dtf", etiqueta: "DTF · impresión textil", marca: "SmartJet FLEX", patron: "",
        nota: "Las cuatro SmartJet FLEX; confirmar cuáles van a DTF.", posicion: 1, activo: true,
        coincide: [] },
    ],
    de_la_base: false,
    huecos: [{ interes: "corte_laser", etiqueta: "Corte láser",
      motivo: "sin máquina en el compositor: se enlaza la página de producto" }],
    revisado: false,
    hallazgo: "Las máquinas láser (FLUX, MBO Lasers), el packaging y el CNC no están en el compositor de correos.",
    catalogo_vacio: false,
    palabras: [
      { interes: "uv", etiqueta: "UV de cualquier tamaño", palabras: ["uv", "uv-drucker"], propias: false },
      { interes: "dtf", etiqueta: "DTF · impresión textil", palabras: ["textil*", "t-shirt*"], propias: false },
    ],
  },
  idiomas: ["de", "en", "es", "fr", "nl"],
};

function producto(over: Partial<CatalogoProducto> & { id: string; nombre: string }): CatalogoProducto {
  return {
    fuente: "compositor", tipo: "maquina", marca: "artisJet", origen_id: over.id, descripcion: null,
    caracteristicas: [], categoria: null, imagen: null, precio_texto: "9.300 €", precio_num: 9300,
    moneda: "EUR", precio_publico: true, descartado: false, motivo_descarte: null,
    enlace: "https://artisjet-printers.eu/x/", idioma: "es", sitio: "artisjet-eu",
    actualizado_origen: "2026-09-20T08:00:00Z", visible: true, datos: {}, visto_en: null, ido_en: null,
    ...over,
  };
}

beforeEach(() => {
  mockGet.mockReset();
  mockCreateRuta.mockReset();
  mockUpdateRuta.mockReset();
  mockUpdateSitio.mockReset();
  mockComprobar.mockReset();
  mockSincronizar.mockReset();
  mockResolver.mockReset();
  mockProductos.mockReset();
  mockSincronizarCatalogo.mockReset();
  mockCreateInteres.mockReset();
  mockUpdateInteres.mockReset();
  mockDeleteInteres.mockReset();
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
    expect(screen.getByRole("button", { name: "Sincronizar compositor" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Añadir fila" })).not.toBeInTheDocument();
  });

  it("el catálogo enseña el estado por fuente, el aviso del IVA sin declarar y la tabla al pedirla", async () => {
    mockProductos.mockResolvedValue({
      productos: [
        producto({ id: "artisjet-5000u", nombre: "artisJet 5000U", precio_texto: "9.300 €" }),
        producto({ id: "artisjet-5000u:fr", nombre: "artisJet 5000U", idioma: "fr", precio_texto: "9 300 €" }),
        producto({ id: "fluxlasers:1000", nombre: "Lente Beambox II", fuente: "woocommerce", tipo: "consumible",
          marca: "Fluxlasers", precio_texto: "12,50 €", precio_num: 12.5, sitio: "fluxlasers",
          categoria: "Lentes", enlace: "https://fluxlasers.es/producto/lente/" }),
      ],
      total: 3, limite: 500,
    });
    const user = userEvent.setup();
    render(<ConocimientoPanel canEdit />);
    await screen.findByRole("cell", { name: "www.mqeurope.com" });
    const compositor = screen.getByLabelText("Estado del compositor");
    expect(within(compositor).getByText("90")).toBeInTheDocument();
    expect(within(compositor).getByText(/castellano 18 · francés 18 · alemán 18 · inglés 18 · neerlandés 18/)).toBeInTheDocument();
    expect(within(compositor).getByText(/fila del compositor del/)).toBeInTheDocument();
    expect(within(compositor).getByText(/IVA: sin declarar/)).toBeInTheDocument();
    const tiendas = screen.getByLabelText("Estado de las tiendas");
    expect(within(tiendas).getByText(/fluxlasers 1544/)).toBeInTheDocument();
    expect(within(tiendas).getByText(/1300 consumibles · 160 repuestos/)).toBeInTheDocument();
    expect(within(tiendas).getByText(/IVA: con IVA incluido/)).toBeInTheDocument();
    expect(screen.getByText(/IVA sin declarar/, { selector: "strong" })).toBeInTheDocument();
    // La tabla se pide a demanda y enseña el precio tal cual, por idioma.
    await user.selectOptions(screen.getByLabelText("Fuente del catálogo"), "compositor");
    await user.type(screen.getByLabelText("Buscar en el catálogo"), "5000");
    await user.click(screen.getByRole("button", { name: "Ver el catálogo" }));
    await waitFor(() => expect(mockProductos).toHaveBeenCalledWith({
      fuente: "compositor", tipo: undefined, idioma: undefined, marca: undefined, q: "5000",
      con_descartados: undefined,
    }));
    const tabla = await screen.findByLabelText("Productos del catálogo");
    expect(within(tabla).getByText("9.300 €")).toBeInTheDocument();
    expect(within(tabla).getByText("9 300 €")).toBeInTheDocument();
    expect(within(tabla).getByText("12,50 €")).toBeInTheDocument();
    expect(within(tabla).getAllByRole("link", { name: "abrir" })[2]).toHaveAttribute(
      "href", "https://fluxlasers.es/producto/lente/");
    expect(screen.getByText("3 filas.")).toBeInTheDocument();
  });

  it("los descartados se ven si se piden, con su motivo; «Consultar» es «sin precio público»", async () => {
    mockProductos.mockResolvedValue({
      productos: [
        producto({ id: "edison", nombre: "Edison Gold Award", precio_texto: null, precio_num: null,
          descartado: true, motivo_descarte: "sin precio ni área de impresión: no es una máquina" }),
        producto({ id: "mbo-uv2513", nombre: "MBO UV2513", marca: "mbo", precio_texto: null, precio_num: null,
          precio_publico: false }),
        producto({ id: "mbo-uv1812", nombre: "MBO UV1812", marca: "mbo", precio_texto: "desde 21.500 €",
          precio_num: 21500 }),
      ],
      total: 3, limite: 500,
    });
    const user = userEvent.setup();
    render(<ConocimientoPanel canEdit />);
    await screen.findByRole("cell", { name: "www.mqeurope.com" });
    expect(screen.getByTestId("kb-descartes")).toHaveTextContent("2 entradas descartadas");
    expect(screen.getByTestId("kb-descartes")).toHaveTextContent("1 producto sin precio público");
    await user.click(screen.getByLabelText("Ver descartados"));
    await user.click(screen.getByRole("button", { name: "Ver el catálogo" }));
    await waitFor(() => expect(mockProductos).toHaveBeenCalledWith(expect.objectContaining({ con_descartados: true })));
    const tabla = await screen.findByLabelText("Productos del catálogo");
    expect(within(tabla).getByText(/descartado: sin precio ni área de impresión/)).toBeInTheDocument();
    expect(within(tabla).getByText("sin precio público (el origen dice «Consultar»)")).toBeInTheDocument();
    expect(within(tabla).getByText("desde 21.500 €")).toBeInTheDocument();
    expect(within(tabla).queryByText("Consultar")).not.toBeInTheDocument();
  });

  it("«Sincronizar compositor» resume la pasada y refresca el estado y las coincidencias", async () => {
    mockSincronizarCatalogo.mockResolvedValue({
      sincronizaciones: [{
        id: "c2", fuente: "compositor", origen: "manual", estado: "ok", inicio: "2026-10-12T09:00:00Z",
        fin: null, productos: 90, nuevos: 5, actualizados: 1, idos: 0, origen_actualizado_en: null,
        resumen: {}, detalle: null,
      }],
      catalogo: { ...DATOS.catalogo, fuentes: { ...DATOS.catalogo.fuentes,
        compositor: { ...DATOS.catalogo.fuentes.compositor, productos: 95 } } },
      interes_productos: { ...DATOS.interes_productos, filas: DATOS.interes_productos.filas.map((f) => (
        f.id === "m2" ? { ...f, coincide: [{ origen_id: "flex-30", nombre: "SmartJet FLEX 30", marca: "SmartJet FLEX" }] } : f)) },
    });
    const user = userEvent.setup();
    render(<ConocimientoPanel canEdit />);
    await screen.findByRole("cell", { name: "www.mqeurope.com" });
    expect(screen.getByText("sin coincidencia")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sincronizar compositor" }));
    await waitFor(() => expect(mockSincronizarCatalogo).toHaveBeenCalledWith("compositor"));
    expect(await screen.findByText(
      "Sincronizado Compositor de correos (máquinas): 90 filas (5 nuevas, 1 cambiadas, 0 desaparecidas).")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Estado del compositor")).getByText("95")).toBeInTheDocument();
    expect(screen.getByText("SmartJet FLEX 30")).toBeInTheDocument();
    expect(screen.queryByText("sin coincidencia")).not.toBeInTheDocument();
  });

  it("las palabras de cada interés se cambian y se vuelven a las de partida", async () => {
    const conPropias = { ...DATOS, interes_productos: { ...DATOS.interes_productos, palabras: [
      DATOS.interes_productos.palabras![0],
      { interes: "dtf", etiqueta: "DTF · impresión textil", palabras: ["textil*", "t-shirt*", "trikot*"],
        propias: true },
    ] } };
    mockPalabras.mockResolvedValueOnce(conPropias).mockResolvedValueOnce(DATOS);
    const user = userEvent.setup();
    render(<ConocimientoPanel canEdit />);
    const tabla = await screen.findByLabelText("Palabras por interés");
    expect(within(tabla).getByText("UV de cualquier tamaño")).toBeInTheDocument();
    const dtf = within(tabla).getByLabelText("Palabras de dtf");
    expect(dtf).toHaveValue("textil*, t-shirt*");
    await user.type(dtf, ", Trikot*");
    await user.click(within(tabla).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockPalabras).toHaveBeenCalledWith("dtf", ["textil*", "t-shirt*", "Trikot*"]));
    expect(await within(tabla).findByText("cambiadas")).toBeInTheDocument();
    await user.click(within(tabla).getByRole("button", { name: "↺ las de partida" }));
    await waitFor(() => expect(mockPalabras).toHaveBeenLastCalledWith("dtf", null));
    await waitFor(() => expect(within(tabla).queryByText("cambiadas")).not.toBeInTheDocument());
  });

  it("interés → máquinas: propuesta sin revisar, huecos, alta de una fila, desactivar y borrar", async () => {
    const nueva = { id: "m3", interes: "uv_mediano", etiqueta: "UV LED mediano formato", marca: "MBO", patron: "6090",
      nota: null, posicion: 2, activo: true,
      coincide: [{ origen_id: "mbo-6090", nombre: "MBO 6090", marca: "MBO" }] };
    mockCreateInteres.mockResolvedValue({ ...DATOS, interes_productos: {
      ...DATOS.interes_productos, filas: [...DATOS.interes_productos.filas, nueva], de_la_base: true } });
    mockUpdateInteres.mockResolvedValue({ ...DATOS, interes_productos: {
      ...DATOS.interes_productos, filas: DATOS.interes_productos.filas.map((f) => (f.id === "m1" ? { ...f, activo: false } : f)) } });
    mockDeleteInteres.mockResolvedValue({ ...DATOS, interes_productos: {
      ...DATOS.interes_productos, filas: DATOS.interes_productos.filas.filter((f) => f.id !== "m2") } });
    jest.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<ConocimientoPanel canEdit />);
    await screen.findByRole("cell", { name: "www.mqeurope.com" });
    expect(screen.getByText("Propuesta sin revisar")).toBeInTheDocument();
    expect(screen.getByText(/no están en el compositor de correos/)).toBeInTheDocument();
    expect(screen.getByLabelText("Intereses sin máquina")).toHaveTextContent(
      "Corte láser (corte_laser): sin máquina en el compositor: se enlaza la página de producto");
    const tabla = screen.getByLabelText("Interés a máquinas");
    expect(within(tabla).getByText("artisJet 5000U")).toBeInTheDocument();
    expect(screen.getByLabelText("Patrón de dtf SmartJet FLEX")).toHaveValue("");   // toda la marca
    await user.type(screen.getByLabelText("Interés de la fila nueva"), "uv_mediano");
    await user.type(screen.getByLabelText("Marca de la fila nueva"), "MBO");
    await user.type(screen.getByLabelText("Patrón de la fila nueva"), "6090");
    await user.click(screen.getByRole("button", { name: "Añadir fila" }));
    await waitFor(() => expect(mockCreateInteres).toHaveBeenCalledWith({
      interes: "uv_mediano", marca: "MBO", patron: "6090", nota: null }));
    expect(await screen.findByText("MBO 6090")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Activa uv_mediano artisJet 5000U"));
    await waitFor(() => expect(mockUpdateInteres).toHaveBeenCalledWith("m1", { activo: false }));
    expect(await screen.findByLabelText("Activa uv_mediano artisJet 5000U")).not.toBeChecked();
    await user.click(screen.getByLabelText("Borrar dtf SmartJet FLEX"));
    await waitFor(() => expect(mockDeleteInteres).toHaveBeenCalledWith("m2"));
    await waitFor(() => expect(screen.queryByLabelText("Borrar dtf SmartJet FLEX")).not.toBeInTheDocument());
  });

  it("«Probar» enseña las máquinas y consumibles del catálogo que saldrían, con su precio tal cual", async () => {
    mockResolver.mockResolvedValue({
      soporte: null, producto: { url: "https://www.mqeurope.com/flatbed-printers/", origen: "producto",
        sitio: "mqeurope", idioma: "en", etiqueta: "uv_mediano" }, flux: null,
      catalogo: {
        maquinas: [
          { origen_id: "artisjet-5000u", fuente: "compositor", tipo: "maquina", nombre: "artisJet 5000U",
            marca: "artisJet", categoria: "A2", caracteristicas: [], precio_texto: "9.300 €", moneda: "EUR",
            precio_idioma: "es", idioma_respaldo: true, orientativo: true, actualizado_origen: null,
            enlace: "https://artisjet-printers.eu/de/5000u/", idioma_enlace: "de", interes: "uv_mediano" },
        ],
        consumibles: [],
        sin_maquina: [],
        avisos: { con: "Preise inkl. MwSt.", sin: "Preise zzgl. MwSt.", sin_declarar: "x", vies: "Für EU-Unternehmen…",
          no_factura: "Diese E-Mail…", orientativo: "Richtpreis…" },
        iva_por_fuente: { compositor: null, woocommerce: "con" },
        precio_meses_orientativo: 6,
      },
    });
    const user = userEvent.setup();
    render(<ConocimientoPanel canEdit />);
    await screen.findByRole("cell", { name: "www.mqeurope.com" });
    await user.type(screen.getByLabelText("Interés para probar"), "uv_mediano");
    await user.type(screen.getByLabelText("Máquina para probar"), "FLUX Beambox II");
    await user.click(screen.getByRole("button", { name: "Probar" }));
    await waitFor(() => expect(mockResolver).toHaveBeenCalledWith({
      sitio: "mqeurope", idioma: "de", interes: "uv_mediano", texto: null, maquina: "FLUX Beambox II",
    }));
    const maquinas = await screen.findByLabelText("Máquinas que saldrían");
    expect(within(maquinas).getByText("9.300 €")).toBeInTheDocument();
    expect(within(maquinas).getByText(/precio del idioma «es»/)).toBeInTheDocument();
    expect(within(maquinas).getByText(/orientativo/)).toBeInTheDocument();
    expect(within(maquinas).getByRole("link", { name: "enlace (de)" })).toHaveAttribute(
      "href", "https://artisjet-printers.eu/de/5000u/");
    expect(screen.getByText(/ninguno \(sin máquina o sin productos que casen\)/)).toBeInTheDocument();
    expect(screen.getByText(/«Für EU-Unternehmen…»/)).toBeInTheDocument();
  });
});
