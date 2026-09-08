import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpSettingsPage from "./page";
import { getErpSettings, updateErpSettings, type ErpSettings } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getErpSettings: jest.fn(),
  updateErpSettings: jest.fn(),
}));
const mockGet = getErpSettings as jest.Mock;
const mockUpdate = updateErpSettings as jest.Mock;

function settings(over: Partial<ErpSettings> = {}): ErpSettings {
  return {
    default_invoice_mode: "manual",
    auto_invoice_max_amount_eur: null,
    default_carrier_id: null,
    factusol_default_ejercicio: "2026",
    factusol_live: false,
    factusol_series_default: "",
    factusol_series_by_source: {},
    factusol_estpcl_invoiced: "",
    factusol_estpre_accepted: "1",
    factusol_estalb_invoiced: "1",
    factusol_companies: {
      "5": {
        nombre: "Streamtec SL", direccion: "C. Corsega 232, 5",
        cp_poblacion: "08036 Barcelona", pais: "España",
        telefono: "Tel. 932022530", email: "", nif: "CIF B64154263",
        idioma_defecto: "es",
        bancos: [
          { nombre: "Banco de Sabadell", domicilio: "Alicante",
            iban: "ES11 0081 0202 1700 0125 9030", bic: "BSABESBB",
            defecto: true },
          { nombre: "Open Bank", domicilio: "Madrid",
            iban: "ES23 0073 0100 5404 4814 5865", bic: "OPENESMM",
            defecto: false },
        ],
        legal: {}, pie: {}, intracom: {}, titulo_albaran_valorado: {},
        logo: false,
      },
    },
    factusol_pickup_warehouses: [
      { nombre: "Almacén TERLO 2000", direccion: "Castellbisbal" },
    ],
    ...over,
  };
}

beforeEach(() => {
  mockGet.mockReset();
  mockUpdate.mockReset();
  mockGet.mockResolvedValue(settings());
  mockUpdate.mockImplementation((patch) => Promise.resolve(settings(patch)));
});

describe("ErpSettingsPage — serie de facturación (C-2)", () => {
  it("renderiza la serie por defecto y una fila por origen", async () => {
    render(<ErpSettingsPage />);
    expect(await screen.findByLabelText("Serie por defecto")).toBeInTheDocument();
    expect(screen.getByLabelText("Serie WooCommerce (las 3 tiendas)")).toBeInTheDocument();
    expect(screen.getByLabelText("Serie Manual")).toBeInTheDocument();
    expect(screen.getByLabelText("Serie Proforma FACTUSOL")).toBeInTheDocument();
  });

  it("precarga los valores guardados", async () => {
    mockGet.mockResolvedValue(settings({
      factusol_series_default: "A",
      factusol_series_by_source: { manual: "M" },
    }));
    render(<ErpSettingsPage />);
    expect(await screen.findByLabelText("Serie por defecto")).toHaveValue("A");
    expect(screen.getByLabelText("Serie Manual")).toHaveValue("M");
    expect(screen.getByLabelText("Serie WooCommerce (las 3 tiendas)")).toHaveValue("");
  });

  it("guarda la serie por defecto y el override por origen", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    await user.type(await screen.findByLabelText("Serie por defecto"), "A");
    await user.type(screen.getByLabelText("Serie Manual"), "M");
    await user.click(screen.getByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const patch = mockUpdate.mock.calls[0][0];
    expect(patch.factusol_series_default).toBe("A");
    expect(patch.factusol_series_by_source.manual).toBe("M");
  });

  it("expone y guarda el ESTPCL del pedido facturado (ERP-E2-fix2)", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    // Antes solo se podía tocar en la BD a mano; sin él el pedido no se marca.
    const field = await screen.findByLabelText(
      "Estado ESTPCL del pedido facturado",
    );
    expect(field).toHaveValue("");
    await user.type(field, "2");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0].factusol_estpcl_invoiced).toBe("2");
  });

  it("precarga el ESTPCL guardado", async () => {
    mockGet.mockResolvedValue(settings({ factusol_estpcl_invoiced: "2" }));
    render(<ErpSettingsPage />);
    expect(
      await screen.findByLabelText("Estado ESTPCL del pedido facturado"),
    ).toHaveValue("2");
  });

  it("edita la identidad fiscal de las empresas emisoras (E4)", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const nif = await screen.findByLabelText(
      "NIF / VAT (tal como debe imprimirse) (serie 5)",
    );
    expect(nif).toHaveValue("CIF B64154263");
    await user.clear(nif);
    await user.type(nif, "CIF B00000000");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(
      mockUpdate.mock.calls[0][0].factusol_companies["5"].nif,
    ).toBe("CIF B00000000");
  });

  it("edita las cuentas bancarias de la empresa (E4-fix1)", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    // Dos cuentas ya cargadas; edito el IBAN de la segunda (Open Bank).
    const iban2 = await screen.findByLabelText("Banco 2 iban (serie 5)");
    expect(iban2).toHaveValue("ES23 0073 0100 5404 4814 5865");
    await user.clear(iban2);
    await user.type(iban2, "ES99 NUEVO");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const bancos = mockUpdate.mock.calls[0][0].factusol_companies["5"].bancos;
    expect(bancos[1].iban).toBe("ES99 NUEVO");
    expect(bancos[0].defecto).toBe(true);
  });

  it("edita los almacenes de recogida (E4-fix1)", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const dir = await screen.findByLabelText("Almacén 1 dirección");
    expect(dir).toHaveValue("Castellbisbal");
    await user.clear(dir);
    await user.type(dir, "C/ Nueva 1");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(
      mockUpdate.mock.calls[0][0].factusol_pickup_warehouses[0].direccion,
    ).toBe("C/ Nueva 1");
  });

  it("muestra y guarda los estados de conversión ESTPRE/ESTALB (E3-B-fix3)", async () => {
    // test_settings_has_estalb_and_estpre_fields
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const estpre = await screen.findByLabelText(
      "Estado ESTPRE del presupuesto convertido",
    );
    const estalb = screen.getByLabelText("Estado ESTALB del albarán facturado");
    // Defaults confirmados en el escritorio: 1 y 1.
    expect(estpre).toHaveValue("1");
    expect(estalb).toHaveValue("1");
    // Vaciar uno es válido (desactiva el marcado) y viaja en el PATCH.
    await user.clear(estalb);
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0].factusol_estalb_invoiced).toBe("");
    expect(mockUpdate.mock.calls[0][0].factusol_estpre_accepted).toBe("1");
  });
});

// ERP-F5 — contrapartidas de cobro (catálogo configurable) + PayPal por tienda.
describe("ErpSettingsPage — contrapartidas de cobro (F5)", () => {
  it("lista las contrapartidas, permite editarlas y guarda el PayPal por tienda", async () => {
    mockGet.mockResolvedValue(settings({
      contrapartidas: [
        { codigo: "6", nombre: "Bomedia Sabadell" },
        { codigo: "14", nombre: "Paypal Streamtec" },
      ],
      paypal_contrapartidas_by_store: { artisjet: "12", boprint: "14", fluxlasers: "14" },
    }));
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const desc = await screen.findByLabelText("Contrapartida 1 descripción");
    expect(desc).toHaveValue("Bomedia Sabadell");
    expect(screen.getByLabelText("Contrapartida 1 código")).toHaveValue("6");
    // El selector PayPal de boprint apunta a la 14 y ofrece las del catálogo.
    const boprint = screen.getByLabelText("Contrapartida PayPal boprint") as HTMLSelectElement;
    expect(boprint.value).toBe("14");
    expect(screen.getAllByRole("option", { name: "6 · Bomedia Sabadell" }).length).toBe(3);
    await user.clear(desc);
    await user.type(desc, "Bomedia Sabadell (ES33…1918)");
    await user.selectOptions(boprint, "6");
    await user.click(screen.getByRole("button", { name: "+ Añadir contrapartida" }));
    expect(screen.getByLabelText("Contrapartida 3 código")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const sent = mockUpdate.mock.calls[0][0];
    expect(sent.contrapartidas[0]).toEqual({ codigo: "6", nombre: "Bomedia Sabadell (ES33…1918)" });
    expect(sent.contrapartidas).toHaveLength(3);
    expect(sent.paypal_contrapartidas_by_store.boprint).toBe("6");
    expect(sent.paypal_contrapartidas_by_store.artisjet).toBe("12");
  });
});
