import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { escribirLocales, leerLocales, SoporteFuentesBloque } from "./SoporteFuentesBloque";
import {
  actualizarFuenteSoporte,
  comprobarFuenteSoporte,
  crearFuenteSoporte,
  listarFuentesSoporte,
  type SoporteFuente,
} from "../../lib/soporteApi";

jest.mock("../../lib/soporteApi", () => ({
  listarFuentesSoporte: jest.fn(),
  crearFuenteSoporte: jest.fn(),
  actualizarFuenteSoporte: jest.fn(),
  comprobarFuenteSoporte: jest.fn(),
}));

const mockListar = listarFuentesSoporte as jest.Mock;
const mockCrear = crearFuenteSoporte as jest.Mock;
const mockActualizar = actualizarFuenteSoporte as jest.Mock;
const mockComprobar = comprobarFuenteSoporte as jest.Mock;

function fuente(over: Partial<SoporteFuente> = {}): SoporteFuente {
  return {
    clave: "flux", nombre: "FLUX Support", dominio: "support.flux3dp.com", tipo: "zendesk",
    base_url: "https://support.flux3dp.com", marca: "FLUX", locales: { es: "es-419", en: "en-us" },
    activo: true, posicion: 0, notas: null, comprobado_en: "2026-10-27T09:00:00Z",
    comprobado_estado: "ok", comprobado_detalle: "La búsqueda de prueba devuelve 12 artículos con texto.",
    ...over,
  };
}

const ARTISJET = fuente({
  clave: "artisjet", nombre: "artisJet Help", dominio: "help.artisjet.com", tipo: "html",
  base_url: "https://help.artisjet.com/es", marca: "artisJet", locales: {}, activo: false,
  comprobado_en: null, comprobado_estado: null, comprobado_detalle: null,
});

beforeEach(() => {
  mockListar.mockReset();
  mockCrear.mockReset();
  mockActualizar.mockReset();
  mockComprobar.mockReset();
  mockListar.mockResolvedValue([fuente(), ARTISJET]);
});

describe("SoporteFuentesBloque (lista blanca del soporte de terceros)", () => {
  it("lista las fuentes con dominio, tipo, marca, idiomas, activa y la última comprobación", async () => {
    render(<SoporteFuentesBloque canEdit />);
    const tabla = await screen.findByRole("table", { name: "Centros de soporte de la lista blanca" });
    const [, flux, artis] = within(tabla).getAllByRole("row");
    expect(within(flux).getByText("FLUX Support")).toBeInTheDocument();
    expect(within(flux).getByText("support.flux3dp.com")).toBeInTheDocument();
    expect(within(flux).getByText("Zendesk")).toBeInTheDocument();
    expect(within(flux).getByText("es → es-419")).toBeInTheDocument();
    expect(within(flux).getByText(/^sirve contenido · /)).toHaveClass("is-ok");
    expect(within(flux).getByLabelText("Activa FLUX Support")).toBeChecked();
    expect(within(artis).getByText("sin comprobar")).toBeInTheDocument();
    expect(within(artis).getByLabelText("Activa artisJet Help")).not.toBeChecked();
    expect(artis).toHaveClass("is-inactivo");
    // Lo que explica la sección: solo de estos dominios, y la cáscara vacía no sirve.
    expect(screen.getByText(/Solo se descarga de estos dominios/)).toBeInTheDocument();
    expect(screen.getByText(/cáscara vacía y no sirve/)).toBeInTheDocument();
  });

  it("«Comprobar» enseña que una fuente es una cáscara vacía (se pinta con JavaScript)", async () => {
    mockComprobar.mockResolvedValue({
      ...ARTISJET, comprobado_en: "2026-10-27T10:00:00Z", comprobado_estado: "vacio",
      comprobado_detalle: "La página llega con 37 caracteres de texto: se pinta con JavaScript.",
    });
    const user = userEvent.setup();
    render(<SoporteFuentesBloque canEdit />);
    await user.click(await screen.findByRole("button", { name: "Comprobar artisJet Help" }));
    await waitFor(() => expect(mockComprobar).toHaveBeenCalledWith("artisjet"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      /artisJet Help: cáscara vacía\. La página se pinta con JavaScript/);
    const fila = screen.getByLabelText("Activa artisJet Help").closest("tr") as HTMLElement;
    expect(within(fila).getByText(/^cáscara vacía · /)).toHaveClass("is-error");
    expect(within(fila).getByText(/37 caracteres de texto/)).toBeInTheDocument();
  });

  it("avisa de una fuente activa cuya comprobación dio vacío", async () => {
    mockListar.mockResolvedValue([fuente({ comprobado_estado: "vacio", comprobado_detalle: "sin texto" })]);
    render(<SoporteFuentesBloque canEdit />);
    expect(await screen.findByRole("note")).toHaveTextContent("FLUX Support (cáscara vacía)");
  });

  it("da de alta una fuente (desactivada de entrada) con los idiomas como idioma=locale", async () => {
    mockCrear.mockImplementation(async (p) => fuente({
      ...p, base_url: "https://support.mbo.com", comprobado_en: null, comprobado_estado: null,
      comprobado_detalle: null,
    }));
    const user = userEvent.setup();
    render(<SoporteFuentesBloque canEdit />);
    await screen.findByRole("table", { name: "Centros de soporte de la lista blanca" });
    const alta = screen.getByRole("group", { name: "Añadir un centro de soporte" });
    const boton = within(alta).getByRole("button", { name: "Añadir fuente" });
    expect(boton).toBeDisabled();
    await user.type(within(alta).getByLabelText("Clave (fuente nueva)"), "mbo");
    await user.type(within(alta).getByLabelText("Nombre (fuente nueva)"), "Soporte MBO");
    await user.type(within(alta).getByLabelText("Dominio (fuente nueva)"), "support.mbo.com");
    await user.selectOptions(within(alta).getByLabelText("Tipo (fuente nueva)"), "wordpress");
    await user.type(within(alta).getByLabelText("Marca (fuente nueva)"), "MBO");
    await user.type(within(alta).getByLabelText("Idiomas (fuente nueva)"), "es=es, en=en-gb");
    expect(within(alta).getByLabelText("Activa (fuente nueva)")).not.toBeChecked();
    await user.click(boton);
    await waitFor(() => expect(mockCrear).toHaveBeenCalledWith({
      clave: "mbo", nombre: "Soporte MBO", tipo: "wordpress", locales: { es: "es", en: "en-gb" },
      activo: false, marca: "MBO", notas: null, dominio: "support.mbo.com",
    }));
    expect(await screen.findByLabelText("Activa Soporte MBO")).not.toBeChecked();
    expect(screen.getByRole("status")).toHaveTextContent("Soporte MBO añadida a la lista (desactivada");
    // El formulario queda vacío para la siguiente.
    expect(screen.getByLabelText("Clave (fuente nueva)")).toHaveValue("");
  });

  it("unos idiomas mal escritos no se mandan", async () => {
    const user = userEvent.setup();
    render(<SoporteFuentesBloque canEdit />);
    const alta = await screen.findByRole("group", { name: "Añadir un centro de soporte" });
    await user.type(within(alta).getByLabelText("Clave (fuente nueva)"), "mbo");
    await user.type(within(alta).getByLabelText("Nombre (fuente nueva)"), "Soporte MBO");
    await user.type(within(alta).getByLabelText("Dominio (fuente nueva)"), "support.mbo.com");
    await user.type(within(alta).getByLabelText("Idiomas (fuente nueva)"), "español");
    await user.click(within(alta).getByRole("button", { name: "Añadir fuente" }));
    expect(await within(alta).findByRole("alert")).toHaveTextContent("«español» no es idioma=locale");
    expect(mockCrear).not.toHaveBeenCalled();
  });

  it("edita una fuente mandando solo lo que cambia, y la activa o desactiva al momento", async () => {
    mockActualizar.mockImplementation(async (clave, cambios) => (clave === "flux"
      ? fuente({ ...cambios }) : { ...ARTISJET, ...cambios }));
    const user = userEvent.setup();
    render(<SoporteFuentesBloque canEdit />);
    await user.click(await screen.findByRole("button", { name: "Editar FLUX Support" }));
    const form = screen.getByRole("group", { name: "Editar FLUX Support" });
    expect(within(form).getByLabelText("Clave (flux)")).toBeDisabled();
    expect(within(form).getByLabelText("Idiomas (flux)")).toHaveValue("es=es-419, en=en-us");
    await user.type(within(form).getByLabelText("Idiomas (flux)"), ", de=de");
    await user.type(within(form).getByLabelText("Notas (flux)"), "Centro oficial de FLUX");
    await user.click(within(form).getByRole("button", { name: "Guardar cambios de la fuente" }));
    await waitFor(() => expect(mockActualizar).toHaveBeenCalledWith("flux", {
      locales: { es: "es-419", en: "en-us", de: "de" }, notas: "Centro oficial de FLUX",
    }));
    expect(await screen.findByText("Centro oficial de FLUX")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Editar FLUX Support" })).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Activa artisJet Help"));
    await waitFor(() => expect(mockActualizar).toHaveBeenCalledWith("artisjet", { activo: true }));
    expect(await screen.findByLabelText("Activa artisJet Help")).toBeChecked();
  });

  it("desactivarla en la fila con el formulario abierto: guardar el formulario no la vuelve a activar", async () => {
    mockActualizar.mockImplementation(async (_clave, cambios) => fuente({ activo: false, ...cambios }));
    const user = userEvent.setup();
    render(<SoporteFuentesBloque canEdit />);
    await user.click(await screen.findByRole("button", { name: "Editar FLUX Support" }));
    const form = screen.getByRole("group", { name: "Editar FLUX Support" });
    // «Comprobar» dio cáscara vacía: se desactiva en la fila, con el formulario abierto.
    await user.click(screen.getByLabelText("Activa FLUX Support"));
    await waitFor(() => expect(mockActualizar).toHaveBeenCalledWith("flux", { activo: false }));
    expect(await screen.findByLabelText("Activa FLUX Support")).not.toBeChecked();
    // Y luego se corrige el nombre en el formulario y se guarda: solo va el nombre.
    await user.type(within(form).getByLabelText("Nombre (flux)"), " (oficial)");
    await user.click(within(form).getByRole("button", { name: "Guardar cambios de la fuente" }));
    await waitFor(() => expect(mockActualizar).toHaveBeenCalledTimes(2));
    expect(mockActualizar).toHaveBeenLastCalledWith("flux", { nombre: "FLUX Support (oficial)" });
    expect(await screen.findByLabelText("Activa FLUX Support (oficial)")).not.toBeChecked();
  });

  it("sin permiso de edición: la tabla se ve, sin alta, sin editar y sin comprobar", async () => {
    render(<SoporteFuentesBloque canEdit={false} />);
    await screen.findByRole("table", { name: "Centros de soporte de la lista blanca" });
    expect(screen.queryByRole("group", { name: "Añadir un centro de soporte" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Editar FLUX Support" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Comprobar FLUX Support" })).toBeDisabled();
    expect(screen.getByLabelText("Activa FLUX Support")).toBeDisabled();
  });

  it("idioma=locale se lee y se escribe igual", () => {
    expect(leerLocales("es=es-419, EN=en-us;de=de")).toEqual({
      locales: { es: "es-419", en: "en-us", de: "de" }, malos: [],
    });
    expect(leerLocales("es=es-419 fr")).toEqual({ locales: { es: "es-419" }, malos: ["fr"] });
    expect(escribirLocales({ es: "es-419", en: "en-us" })).toBe("es=es-419, en=en-us");
  });
});
