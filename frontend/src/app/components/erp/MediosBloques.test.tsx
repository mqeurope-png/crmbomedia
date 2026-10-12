import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ImagenesBloque, VideosBloque } from "./MediosBloques";
import {
  createKbVideo,
  getCatalogoProductos,
  getKbImagenes,
  getKbVideos,
  sincronizarKbImagenes,
  updateKbImagen,
  updateKbVideo,
  type Conocimiento,
  type KbImagen,
  type KbVideo,
  type MediosEstado,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getKbImagenes: jest.fn(),
  updateKbImagen: jest.fn(),
  sincronizarKbImagenes: jest.fn(),
  getKbVideos: jest.fn(),
  createKbVideo: jest.fn(),
  updateKbVideo: jest.fn(),
  sincronizarKbVideos: jest.fn(),
  getCatalogoProductos: jest.fn(),
}));

const mockImagenes = getKbImagenes as jest.Mock;
const mockMarcar = updateKbImagen as jest.Mock;
const mockSincronizarImagenes = sincronizarKbImagenes as jest.Mock;
const mockVideos = getKbVideos as jest.Mock;
const mockCrearVideo = createKbVideo as jest.Mock;
const mockGuardarVideo = updateKbVideo as jest.Mock;
const mockProductos = getCatalogoProductos as jest.Mock;

const MEDIOS: MediosEstado = {
  imagenes: { total: 331, por_sitio: { boprint: 64, fluxlasers: 109 }, favoritas: 2, ocultas: 1,
    ultima_sincronizacion: null },
  videos: { total: 537, por_idioma: { en: 479, es: 34 }, por_canal: {}, temas: 4, ocultos: 0,
    cortos: 48, clave_configurada: true, ultima_sincronizacion: null },
  canales: [{ handle: "@artisjeteurope", marca: "artisjet" }, { handle: "@fluxinc", marca: "flux" }],
  sincronizacion_diaria: true,
};

const DATOS = {
  sitios: [{ clave: "boprint", dominio: "boprint.net", host: "boprint.net", tipo: "web" }],
  medios: MEDIOS,
} as unknown as Conocimiento;

function imagen(over: Partial<KbImagen> & { id: string; titulo: string }): KbImagen {
  return {
    sitio: "boprint", media_id: 1, url: `https://boprint.net/${over.id}.jpg`,
    miniatura: `https://boprint.net/${over.id}-768x429.jpg`, ancho: 1920, alto: 1072,
    mime: "image/jpeg", pagina: null, publicado_en: null, favorita: false, oculta: false,
    ido_en: null, ...over,
  };
}

function video(over: Partial<KbVideo> & { id: string; video_id: string; titulo: string }): KbVideo {
  return {
    plataforma: "youtube", url: `https://www.youtube.com/watch?v=${over.video_id}`,
    descripcion: null, idioma: "es", idioma_fuente: "titulo", idioma_metadatos: "es",
    idioma_audio: "en", tema: null, marca: "artisjet", productos: [], manual: [],
    miniatura: `https://img.youtube.com/vi/${over.video_id}/maxresdefault.jpg`,
    miniatura_calidad: "maxresdefault", duracion: 156, corto: false, canal: "MBO - Artisjet Europe",
    origen: "canal", webs: [], visible: true, publicado_en: null, ido_en: null, ...over,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockProductos.mockResolvedValue({ productos: [
    { origen_id: "artisjet-3000pro", nombre: "artisJet 3000Pro", marca: "artisjet" },
    { origen_id: "artisjet-young", nombre: "artisJet Young", marca: "artisjet" },
  ], total: 2, limite: 500 });
});

test("las imágenes: se cargan, se marcan favoritas y se ocultan, y se sincronizan", async () => {
  const onDatos = jest.fn();
  const coasters = imagen({ id: "i1", titulo: "custom coasters" });
  mockImagenes.mockResolvedValue({ imagenes: [coasters, imagen({ id: "i2", titulo: "dtf hoodies",
    oculta: true })], total: 2, limite: 600 });
  mockMarcar.mockResolvedValue({ imagen: { ...coasters, favorita: true },
    estado: { ...MEDIOS.imagenes, favoritas: 3 } });
  render(<ImagenesBloque datos={DATOS} canEdit onDatos={onDatos} />);
  expect(screen.getByLabelText("Estado del índice de imágenes")).toHaveTextContent("331 imágenes");
  expect(screen.getByLabelText("Estado del índice de imágenes")).toHaveTextContent("boprint 64");
  await userEvent.type(screen.getByLabelText("Buscar imágenes"), "coast");
  await userEvent.click(screen.getByRole("button", { name: "Ver las imágenes" }));
  expect(mockImagenes).toHaveBeenCalledWith({ sitio: undefined, q: "coast", favoritas: undefined });
  const lista = await screen.findByRole("list", { name: "Imágenes indexadas" });
  expect(within(lista).getByAltText("custom coasters")).toHaveAttribute(
    "src", "https://boprint.net/i1-768x429.jpg");
  expect(within(lista).getAllByRole("listitem")[1]).toHaveClass("is-inactivo");
  await userEvent.click(within(within(lista).getAllByRole("listitem")[0]).getByRole("button",
    { name: "☆ Favorita" }));
  expect(mockMarcar).toHaveBeenCalledWith("i1", { favorita: true });
  await waitFor(() => expect(within(lista).getByRole("button", { name: "★ Favorita" }))
    .toHaveAttribute("aria-pressed", "true"));
  expect(onDatos).toHaveBeenCalledWith(expect.objectContaining({
    medios: expect.objectContaining({ imagenes: expect.objectContaining({ favoritas: 3 }) }) }));
  mockSincronizarImagenes.mockResolvedValue({ sincronizacion: { estado: "parcial", productos: 330,
    nuevos: 2, actualizados: 1, idos: 3, detalle: "fluxlasers: HTTP 503" }, estado: MEDIOS.imagenes });
  await userEvent.click(screen.getByRole("button", { name: "Sincronizar imágenes" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Parcial: fluxlasers: HTTP 503");
});

test("sin permiso de edición no se marca ni se sincroniza", async () => {
  mockImagenes.mockResolvedValue({ imagenes: [imagen({ id: "i1", titulo: "custom coasters" })],
    total: 1, limite: 600 });
  render(<ImagenesBloque datos={DATOS} canEdit={false} onDatos={jest.fn()} />);
  expect(screen.getByRole("button", { name: "Sincronizar imágenes" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Ver las imágenes" }));
  expect(await screen.findByRole("button", { name: "☆ Favorita" })).toBeDisabled();
});

test("los vídeos: pegar una URL, corregir el idioma, el tema y las máquinas", async () => {
  const freebird = video({ id: "v1", video_id: "keIJei91HnU", titulo: "FREEBIRD: Menos clics, más resultados",
    tema: "freebird-menos-clics" });
  mockVideos.mockResolvedValue({ videos: [freebird], total: 1, limite: 1000 });
  render(<VideosBloque datos={DATOS} canEdit onDatos={jest.fn()} />);
  expect(screen.getByText(/@artisjeteurope, @fluxinc/)).toBeInTheDocument();
  expect(screen.getByLabelText("Estado de la tabla de vídeos")).toHaveTextContent("4 temas · 48 shorts");
  await userEvent.click(screen.getByRole("button", { name: "Ver los vídeos" }));
  const tabla = await screen.findByRole("table", { name: "Vídeos" });
  expect(within(tabla).getByText("FREEBIRD: Menos clics, más resultados")).toBeInTheDocument();
  expect(within(tabla).getByText(/miniatura HD/)).toBeInTheDocument();
  expect(within(tabla).getByText("por el título")).toBeInTheDocument();
  // El idioma, a mano: queda fijado y se puede volver a lo automático.
  mockGuardarVideo.mockResolvedValueOnce({ video: { ...freebird, idioma: "pt", idioma_fuente: "manual",
    manual: ["idioma"] }, estado: MEDIOS.videos });
  await userEvent.selectOptions(within(tabla).getByLabelText("Idioma de keIJei91HnU"), "pt");
  expect(mockGuardarVideo).toHaveBeenCalledWith("v1", { idioma: "pt" });
  const automatico = await within(tabla).findByRole("button", { name: "↺ automático" });
  mockGuardarVideo.mockResolvedValueOnce({ video: freebird, estado: MEDIOS.videos });
  await userEvent.click(automatico);
  expect(mockGuardarVideo).toHaveBeenLastCalledWith("v1", { automatico: ["idioma"] });
  // El tema se guarda con su botón; una máquina del catálogo se añade de la lista.
  const tema = within(tabla).getByLabelText("Tema de keIJei91HnU");
  await userEvent.clear(tema);
  await userEvent.type(tema, "freebird-clics");
  mockGuardarVideo.mockResolvedValueOnce({ video: { ...freebird, tema: "freebird-clics", manual: ["tema"] },
    estado: MEDIOS.videos });
  await userEvent.click(within(tabla).getAllByRole("button", { name: "Guardar" })[0]);
  expect(mockGuardarVideo).toHaveBeenLastCalledWith("v1", { tema: "freebird-clics" });
  mockGuardarVideo.mockResolvedValueOnce({ video: { ...freebird, productos: [
    { fuente: "compositor", origen_id: "artisjet-3000pro", nombre: "artisJet 3000Pro" }],
    manual: ["productos"] }, estado: MEDIOS.videos });
  await userEvent.selectOptions(await within(tabla).findByLabelText("Añadir máquina a keIJei91HnU"),
    "artisjet-3000pro");
  expect(mockGuardarVideo).toHaveBeenLastCalledWith("v1", { productos: [{ origen_id: "artisjet-3000pro" }] });
  expect(await within(tabla).findByText("artisJet 3000Pro")).toBeInTheDocument();
  // Pegar una URL: el vídeo entra arriba.
  mockCrearVideo.mockResolvedValue({ video: video({ id: "v2", video_id: "dQw4w9WgXcQ", titulo: "Pegado",
    origen: "manual" }), estado: { ...MEDIOS.videos, total: 538 } });
  await userEvent.type(screen.getByLabelText("URL del vídeo"), "https://youtu.be/dQw4w9WgXcQ");
  await userEvent.click(screen.getByRole("button", { name: "Añadir" }));
  expect(mockCrearVideo).toHaveBeenCalledWith("https://youtu.be/dQw4w9WgXcQ");
  expect(await within(tabla).findByText("Pegado")).toBeInTheDocument();
  expect(within(tabla).getByText(/pegado a mano/)).toBeInTheDocument();
  expect(screen.getByLabelText("URL del vídeo")).toHaveValue("");
});

test("sin clave de YouTube en el servidor lo dice", () => {
  const sinClave = { ...DATOS, medios: { ...MEDIOS, videos: { ...MEDIOS.videos, clave_configurada: false } } };
  render(<VideosBloque datos={sinClave as Conocimiento} canEdit onDatos={jest.fn()} />);
  expect(screen.getByText(/Sin clave de YouTube en el servidor/)).toBeInTheDocument();
});

test("una API anterior a la Fase 3 (sin medios) no pinta nada", () => {
  const { container } = render(<><ImagenesBloque datos={{ ...DATOS, medios: undefined }} canEdit onDatos={jest.fn()} />
    <VideosBloque datos={{ ...DATOS, medios: undefined }} canEdit onDatos={jest.fn()} /></>);
  expect(container).toBeEmptyDOMElement();
});
