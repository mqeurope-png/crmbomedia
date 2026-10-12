import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OfertaEditorModal } from "./OfertaEditorModal";
import {
  createOferta,
  getOferta,
  getOfertaPlantilla,
  getOfertasMeta,
  listOfertaPlantillas,
  searchCrmContacts,
  searchFactusolArticles,
  updateOferta,
  type Oferta,
} from "../../lib/erpApi";

/** Editor de ofertas: totales en vivo con el criterio del backend (una sola
 *  banda de IVA, la de la primera línea con importe; sin IVA → 0), alta con
 *  plantilla (vuelca sus líneas) → `createOferta`, edición que carga las
 *  líneas con `getOferta` y solo manda `fecha` si se cambió a mano, y el
 *  contacto elegido con el buscador del CRM cuando no viene fijo. */

jest.mock("../../lib/erpApi", () => ({
  FACTUSOL_SERIES: jest.requireActual("../../lib/erpApi").FACTUSOL_SERIES,
  createOferta: jest.fn(),
  updateOferta: jest.fn(),
  getOferta: jest.fn(),
  getOfertaPlantilla: jest.fn(),
  getOfertasMeta: jest.fn(),
  listOfertaPlantillas: jest.fn(),
  searchCrmContacts: jest.fn(),
  searchFactusolArticles: jest.fn(),
}));

const mockCreate = createOferta as jest.Mock;
const mockUpdate = updateOferta as jest.Mock;
const mockGet = getOferta as jest.Mock;
const mockPlantilla = getOfertaPlantilla as jest.Mock;
const mockMeta = getOfertasMeta as jest.Mock;
const mockPlantillas = listOfertaPlantillas as jest.Mock;
const mockContacts = searchCrmContacts as jest.Mock;
const mockArticles = searchFactusolArticles as jest.Mock;

const META = {
  prefijo: "OF", validez_dias: 45,
  series: [{ serie: 1, nombre: "Bomedia" }, { serie: 2, nombre: "MQ Europe" }],
  idiomas: ["es", "en", "de", "fr", "nl"], estados: ["borrador", "enviada", "caducada", "convertida"],
};
const LINEA = {
  id: "l1", posicion: 1, codart: "A1", sku: "SKU-1", descripcion: "Placa braille",
  cantidad: 2, precio: 50, descuento_pct: 0, iva_pct: 21, total: 100, text_only: false,
};
const OFERTA: Oferta = {
  id: "of1", numero: "OF-2026-0001", anio: 2026, secuencia: 1,
  estado: "borrador", estado_guardado: "borrador", vencida: false,
  contact_id: "c1",
  contacto: { id: "c1", nombre: "Ana Pérez", email: "ana@acme.com", company_id: "e1" },
  company_id: "e1", empresa: { id: "e1", nombre: "Acme SL", factusol_company_id: null },
  plantilla_id: null, serie: 2, emisora: "MQ Europe", idioma: "en", con_iva: true,
  fecha: "2026-10-01", validez_dias: 30, valida_hasta: "2026-10-31",
  referencia: "Rótulos", notas: "Entrega en 2 semanas",
  base: 100, iva_pct: 21, iva: 21, total: 121, version: 1,
  factusol: null, convertida_en: null, enviada_en: null, caducada_en: null,
  num_envios: 0, ultimo_envio: null, pendiente_de_envio: false,
  created_by_user_id: "u1", created_at: null, updated_at: null,
};

beforeEach(() => {
  mockCreate.mockReset();
  mockUpdate.mockReset();
  mockGet.mockReset();
  mockPlantilla.mockReset();
  mockMeta.mockReset();
  mockPlantillas.mockReset();
  mockContacts.mockReset();
  mockArticles.mockReset();
  mockMeta.mockResolvedValue(META);
  mockPlantillas.mockResolvedValue([
    { id: "p1", nombre: "Kit UV", descripcion: null, activa: true, origen_serie: null,
      origen_codpre: null, origen_numero: null, num_lineas: 1, created_at: null, updated_at: null },
  ]);
  mockPlantilla.mockResolvedValue({
    id: "p1", nombre: "Kit UV", descripcion: null, activa: true, origen_serie: null,
    origen_codpre: null, origen_numero: null, num_lineas: 1, created_at: null, updated_at: null,
    lineas: [LINEA],
  });
  mockArticles.mockResolvedValue([]);
  mockContacts.mockResolvedValue([]);
  mockCreate.mockResolvedValue({ ...OFERTA, id: "of9", numero: "OF-2026-0009" });
  mockUpdate.mockResolvedValue(OFERTA);
  mockGet.mockResolvedValue({ ...OFERTA, lineas: [LINEA] });
});

function totales() {
  return screen.getByRole("group", { name: "Totales" });
}

describe("OfertaEditorModal", () => {
  it("totales en vivo: una sola banda de IVA (la de la primera línea con importe); sin IVA → 0; líneas sin artículo avisan suave", async () => {
    const user = userEvent.setup();
    render(<OfertaEditorModal contactId="c1" contactName="Ana Pérez" onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(screen.getByRole("dialog", { name: "Nueva oferta" })).toHaveTextContent("Contacto: Ana Pérez");
    // La validez por defecto llega de Configuración ERP (meta).
    await waitFor(() => expect(screen.getByLabelText("Validez en días")).toHaveValue(45));
    expect(totales()).toHaveTextContent("Base: 0.00 €");

    await user.type(screen.getByLabelText("Descripción línea 1"), "Placa");
    await user.clear(screen.getByLabelText("Cantidad línea 1"));
    await user.type(screen.getByLabelText("Cantidad línea 1"), "2");
    await user.type(screen.getByLabelText("Precio línea 1"), "50");
    expect(totales()).toHaveTextContent("Base: 100.00 €");
    expect(totales()).toHaveTextContent("IVA 21 %: 21.00 €");
    expect(totales()).toHaveTextContent("Total: 121.00 €");

    // Segunda línea al 10 %: manda el tipo de la PRIMERA (21 %) sobre toda la base.
    await user.click(screen.getByRole("button", { name: "+ Añadir línea" }));
    await user.type(screen.getByLabelText("Descripción línea 2"), "Montaje");
    await user.type(screen.getByLabelText("Precio línea 2"), "10");
    await user.clear(screen.getByLabelText("IVA línea 2"));
    await user.type(screen.getByLabelText("IVA línea 2"), "10");
    expect(totales()).toHaveTextContent("Base: 110.00 €");
    expect(totales()).toHaveTextContent("IVA 21 %: 23.10 €");
    expect(totales()).toHaveTextContent("Total: 133.10 €");
    // Sin artículo de FACTUSOL: válidas, con aviso suave.
    expect(screen.getByRole("note")).toHaveTextContent("2 líneas sin artículo de FACTUSOL");
    expect(screen.getAllByText("Sin artículo: se resolverá al convertir.")).toHaveLength(2);

    await user.click(screen.getByLabelText("Con IVA"));
    expect(totales()).toHaveTextContent("IVA 0 %: 0.00 €");
    expect(totales()).toHaveTextContent("Total: 110.00 €");
    expect(screen.getByRole("button", { name: "Crear oferta" })).toBeEnabled();
  });

  it("alta: la plantilla vuelca sus líneas (artículo incluido) y «Crear oferta» llama a createOferta con contacto, serie, líneas y validez", async () => {
    const onSaved = jest.fn();
    const user = userEvent.setup();
    render(<OfertaEditorModal contactId="c1" contactName="Ana Pérez" onClose={jest.fn()} onSaved={onSaved} />);
    // Sin líneas no se puede crear.
    expect(screen.getByRole("button", { name: "Crear oferta" })).toBeDisabled();
    await user.selectOptions(await screen.findByLabelText("Plantilla"), "p1");
    await waitFor(() => expect(mockPlantilla).toHaveBeenCalledWith("p1"));
    expect(await screen.findByLabelText("SKU línea 1")).toHaveValue("SKU-1");
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("Placa braille");
    expect(screen.getByRole("status")).toHaveTextContent("Líneas cargadas de la plantilla «Kit UV» (1).");
    expect(totales()).toHaveTextContent("Total: 121.00 €");
    expect(screen.queryByRole("note")).toBeNull();                       // con artículo: sin aviso

    await user.selectOptions(screen.getByLabelText("Empresa emisora (serie)"), "2");
    await user.type(screen.getByLabelText("Referencia"), "Obra Sants");
    await user.click(screen.getByRole("button", { name: "Crear oferta" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({
      contact_id: "c1",
      serie: 2,
      lineas: [{
        codart: "A1", sku: "SKU-1", descripcion: "Placa braille",
        cantidad: 2, precio: 50, descuento_pct: 0, iva_pct: 21,
      }],
      con_iva: true,
      validez_dias: 45,
      referencia: "Obra Sants",
      notas: null,
      plantilla_id: "p1",
    }));
    // Idioma «automático»: no viaja (lo decide el backend por el contacto).
    expect(mockCreate.mock.calls[0][0]).not.toHaveProperty("idioma");
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ numero: "OF-2026-0009" })));
  });

  it("edición: carga las líneas con getOferta si no vienen y updateOferta solo manda «fecha» si se cambió a mano", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<OfertaEditorModal oferta={OFERTA} onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(screen.getByRole("dialog", { name: "Editar oferta OF-2026-0001" })).toHaveTextContent("Contacto: Ana Pérez · Acme SL");
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("of1"));
    expect(await screen.findByLabelText("SKU línea 1")).toHaveValue("SKU-1");
    expect(screen.getByLabelText("Idioma")).toHaveValue("en");
    expect(screen.getByLabelText("Validez en días")).toHaveValue(30);
    expect(screen.getByLabelText("Notas")).toHaveValue("Entrega en 2 semanas");
    expect(screen.getByText(/Si la cambias a mano, manda/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("of1", expect.objectContaining({
      serie: 2, idioma: "en", con_iva: true, validez_dias: 30, referencia: "Rótulos",
      notas: "Entrega en 2 semanas",
      lineas: [expect.objectContaining({ codart: "A1", cantidad: 2, precio: 50 })],
    })));
    expect(mockUpdate.mock.calls[0][1]).not.toHaveProperty("fecha");
    unmount();

    // Con la fecha puesta a mano, viaja.
    render(<OfertaEditorModal oferta={{ ...OFERTA, lineas: [LINEA] }} onClose={jest.fn()} onSaved={jest.fn()} />);
    await user.type(screen.getByLabelText("Fecha de la oferta"), "2026-10-05");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenLastCalledWith("of1", expect.objectContaining({ fecha: "2026-10-05" })));
  });

  it("sin contacto fijo se elige con el buscador del CRM («Elegir»); una convertida solo se ve", async () => {
    const user = userEvent.setup();
    mockContacts.mockResolvedValue([{ id: "c9", name: "Luis Gómez", email: "luis@x.com", company_name: "X SL" }]);
    const { unmount } = render(<OfertaEditorModal onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(screen.getByRole("dialog", { name: "Nueva oferta" })).toHaveTextContent("— sin contacto —");
    await user.type(screen.getByLabelText("Buscar contacto del CRM"), "lu");
    await user.click(await screen.findByRole("button", { name: "Elegir a Luis Gómez" }));
    // También los contactos sin email: la oferta no lo necesita para existir.
    expect(mockContacts).toHaveBeenCalledWith("lu", 8, { conEmail: false });
    expect(screen.getByRole("dialog")).toHaveTextContent("Contacto: Luis Gómez");
    expect(screen.queryByLabelText("Buscar contacto del CRM")).toBeNull();
    await user.type(screen.getByLabelText("Descripción línea 1"), "Consultoría");
    await user.type(screen.getByLabelText("Precio línea 1"), "100");
    await user.click(screen.getByRole("button", { name: "Crear oferta" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(expect.objectContaining({ contact_id: "c9" })));
    unmount();

    render(<OfertaEditorModal
      oferta={{ ...OFERTA, estado: "convertida", lineas: [LINEA],
        factusol: { serie: 2, codpre: 575, numero: "2-000575", ejercicio: "2026" } }}
      onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("ya se convirtió en el presupuesto 2-000575");
    expect(screen.queryByRole("button", { name: "Guardar cambios" })).toBeNull();
    const dialogo = within(screen.getByRole("dialog"));
    expect(dialogo.getByLabelText("Precio línea 1")).toBeDisabled();
    expect(dialogo.getByLabelText("SKU línea 1")).toBeDisabled();
    expect(dialogo.getByLabelText("Descripción línea 1")).toBeDisabled();
    expect(dialogo.queryByRole("button", { name: "Cancelar" })).toBeNull();   // «Cerrar»
  });

  it("readOnly (sin permiso de proformas): se ve, pero ni se edita ni se guarda", () => {
    render(<OfertaEditorModal oferta={{ ...OFERTA, lineas: [LINEA] }} readOnly
                              onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Solo lectura");
    expect(screen.queryByRole("button", { name: "Guardar cambios" })).toBeNull();
    const dialogo = within(screen.getByRole("dialog"));
    expect(dialogo.getByLabelText("SKU línea 1")).toBeDisabled();
    expect(dialogo.getByLabelText("Idioma")).toBeDisabled();
    expect(dialogo.queryByRole("button", { name: "Cancelar" })).toBeNull();   // «Cerrar»
  });
});
