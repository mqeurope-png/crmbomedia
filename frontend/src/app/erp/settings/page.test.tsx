import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpSettingsPage from "./page";
import {
  getErpNextReferences,
  getErpSettings,
  previewInvoiceEmailTemplate,
  sendInvoiceEmailTemplateTest,
  updateErpSettings,
  type ErpSettings,
} from "../../lib/erpApi";

// La tarjeta de Genei tiene su propio endpoint; aquí se prueba aparte.
jest.mock("../../components/erp/GeneiSettingsCard", () => ({
  GeneiSettingsCard: () => null,
}));
jest.mock("../../lib/erpApi", () => ({
  getErpSettings: jest.fn(),
  updateErpSettings: jest.fn(),
  getErpNextReferences: jest.fn(),
  previewInvoiceEmailTemplate: jest.fn(),
  sendInvoiceEmailTemplateTest: jest.fn(),
  previewShipmentEmailTemplate: jest.fn(() => Promise.resolve({
    lang: "es", subject: "Tu pedido 9553 ya tiene envío", body_text: "Hola",
    body_html: "<p>Hola</p>", from_alias_example: "pedidos@streamtec.es",
    from_alias_source: "idioma", from_alias_scope: null, sample: {},
  })),
  sendShipmentEmailTemplateTest: jest.fn(),
  deleteFactusolCompanyLogo: jest.fn(),
  uploadFactusolCompanyLogo: jest.fn(),
}));
// Sugerencias de remitente (datalist): los «enviar como» del usuario.
jest.mock("../../lib/emailsApi", () => ({
  // Todos los «enviar como» de la cuenta (incluye los de tienda, que no son
  // de ningún usuario) + los propios del usuario.
  getEmailAliases: jest.fn(() => Promise.resolve([
    { send_as_email: "pedidos@streamtec.es", display_name: "Streamtec",
      is_primary: false, is_default: false, verification_status: "accepted",
      user_pref_allowed: false, user_pref_default: false,
      gmail_display_name: "Streamtec", display_name_override: null,
      resolved_display_name: "Streamtec" },
  ])),
  getMyEmailAliases: jest.fn(() => Promise.resolve([
    { send_as_email: "ventas@bomedia.net", display_name: "Ventas",
      is_default: true, resolved_display_name: "Ventas" },
  ])),
}));
const mockGet = getErpSettings as jest.Mock;
const mockUpdate = updateErpSettings as jest.Mock;
const mockNextRefs = getErpNextReferences as jest.Mock;
const mockPreview = previewInvoiceEmailTemplate as jest.Mock;
const mockTest = sendInvoiceEmailTemplateTest as jest.Mock;

const SAVE_SERIES = { name: "Guardar cambios · Series FACTUSOL" };
const SAVE_EMPRESAS = { name: "Guardar cambios · Empresas emisoras" };

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
    can_edit: true,
    ...over,
  };
}

const STORES = [
  { slug: "boprint", label: "boprint", ref_prefix_metadata: null, derived_ref_prefix: "BOP" },
  { slug: "fluxlasers", label: "fluxlasers", ref_prefix_metadata: null, derived_ref_prefix: "FLU" },
];

const DEFAULT_SUBJECT: Record<string, string> = {
  es: "Factura {numero}{pedido}", en: "Invoice {numero}{pedido}",
  de: "Rechnung {numero}{pedido}", fr: "Facture {numero}{pedido}",
  nl: "Factuur {numero}{pedido}",
};

/** Simula el backend: rellena los marcadores con los datos de muestra
 *  (usa lo escrito si no está vacío; si no, la plantilla por defecto). */
function fakePreview(lang: string, draft?: { subject?: string; body?: string }) {
  const fill = (t: string) => t
    .replace("{cliente}", "Rotulación Levante S.L.")
    .replace("{numero}", "5-000118")
    .replace("{pedido}", " · pedido BP-2479")
    .replace("{referencia}", " (su ref. BOP-002479)");
  const subject = fill(draft?.subject?.trim() ? draft.subject : DEFAULT_SUBJECT[lang]);
  const body_text = fill(draft?.body?.trim()
    ? draft.body
    : "Estimado/a {cliente}:\n\nAdjuntamos la factura {numero}{pedido}{referencia}.\n\nUn saludo.");
  return Promise.resolve({
    lang, subject, body_text, body_html: `<p>${body_text}</p>`,
    from_alias_example: "pedidos@streamtec.es", from_alias_source: "serie",
    from_alias_scope: "5",
    sample: { cliente: "Rotulación Levante S.L.", numero: "5-000118",
              pedido: "BP-2479", referencia: "BOP-002479" },
  });
}

beforeEach(() => {
  mockGet.mockReset();
  mockUpdate.mockReset();
  mockNextRefs.mockReset();
  mockPreview.mockReset();
  mockTest.mockReset();
  mockGet.mockResolvedValue(settings());
  mockUpdate.mockImplementation((patch) => Promise.resolve(settings(patch)));
  mockNextRefs.mockResolvedValue({
    manual_next: "MANUAL-000124",
    stores: [
      { slug: "boprint", label: "boprint", prefix: "BP", prefix_source: "ajustes",
        next_number: 2480, next_number_known: true, example_ref: "BP-002480" },
      { slug: "fluxlasers", label: "fluxlasers", prefix: "FLU", prefix_source: "derivado",
        next_number: 1, next_number_known: false, example_ref: "FLU-000001" },
    ],
  });
  mockPreview.mockImplementation(fakePreview);
  mockTest.mockResolvedValue({
    sent: true, to: "admin@example.com", lang: "es",
    subject: "[Prueba] Factura 5-000118 · pedido BP-2479",
    from_alias: "pedidos@streamtec.es", from_alias_source: "serie",
    from_alias_scope: "5", message_id: "m-1",
  });
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

  it("guarda la serie por defecto y el override por origen (solo los campos de la sección)", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    await user.type(await screen.findByLabelText("Serie por defecto"), "A");
    await user.type(screen.getByLabelText("Serie Manual"), "M");
    await user.click(screen.getByRole("button", SAVE_SERIES));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const patch = mockUpdate.mock.calls[0][0];
    expect(patch.factusol_series_default).toBe("A");
    expect(patch.factusol_series_by_source.manual).toBe("M");
    // Lote 2 · PR-2: el PATCH lleva SOLO los campos de «Series FACTUSOL».
    expect(patch).not.toHaveProperty("factusol_companies");
    expect(patch).not.toHaveProperty("factusol_pickup_warehouses");
    expect(patch).not.toHaveProperty("default_invoice_mode");
  });

  it("espejo: el reconcile automático viene apagado y se enciende con su intervalo", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const toggle = await screen.findByLabelText("Sincronizar la hoja automáticamente");
    expect(toggle).not.toBeChecked();                 // apagado por defecto
    const minutos = screen.getByLabelText("Minutos entre sincronizaciones automáticas");
    await user.click(toggle);
    await user.clear(minutos);
    await user.type(minutos, "15");
    await user.click(screen.getByRole("button", { name: "Guardar cambios · Hoja de seguimiento en Drive" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const patch = mockUpdate.mock.calls[0][0];
    expect(patch.seguimiento_reconcile_enabled).toBe(true);
    expect(patch.seguimiento_reconcile_interval_minutes).toBe(15);
  });

  it("espejo: el intervalo nunca queda por debajo del mínimo (5)", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const minutos = await screen.findByLabelText("Minutos entre sincronizaciones automáticas");
    await user.clear(minutos);
    await user.type(minutos, "2");
    await user.tab();                                   // al salir se ajusta
    expect(minutos).toHaveValue(5);
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
    await user.click(screen.getByRole("button", SAVE_SERIES));
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
    await user.click(screen.getByRole("button", SAVE_EMPRESAS));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const patch = mockUpdate.mock.calls[0][0];
    expect(patch.factusol_companies["5"].nif).toBe("CIF B00000000");
    expect(Object.keys(patch)).toEqual(["factusol_companies"]);
  });

  it("edita las cuentas bancarias de la empresa (E4-fix1)", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    // Dos cuentas ya cargadas; edito el IBAN de la segunda (Open Bank).
    const iban2 = await screen.findByLabelText("Banco 2 iban (serie 5)");
    expect(iban2).toHaveValue("ES23 0073 0100 5404 4814 5865");
    await user.clear(iban2);
    await user.type(iban2, "ES99 NUEVO");
    await user.click(screen.getByRole("button", SAVE_EMPRESAS));
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
    await user.click(screen.getByRole("button", { name: "Guardar cambios · Almacenes de recogida" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const patch = mockUpdate.mock.calls[0][0];
    expect(patch.factusol_pickup_warehouses[0].direccion).toBe("C/ Nueva 1");
    expect(Object.keys(patch)).toEqual(["factusol_pickup_warehouses"]);
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
    await user.click(screen.getByRole("button", SAVE_SERIES));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0].factusol_estalb_invoiced).toBe("");
    expect(mockUpdate.mock.calls[0][0].factusol_estpre_accepted).toBe("1");
  });

  it("la serie por defecto enseña al lado a qué empresa irán las facturas", async () => {
    mockGet.mockResolvedValue(settings({ factusol_series_default: "5" }));
    render(<ErpSettingsPage />);
    expect(await screen.findByLabelText("Serie por defecto")).toHaveValue("5");
    const region = screen.getByRole("region", { name: "Series FACTUSOL" });
    expect(region).toHaveTextContent("Las facturas nuevas irán a la serie 5 (Streamtec SL).");
    // Y el origen manual enseña el siguiente número que se dará.
    expect(region).toHaveTextContent("siguiente nº MANUAL-000124");
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
    await user.click(screen.getByRole("button", { name: "Guardar cambios · Contrapartidas de cobro" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const sent = mockUpdate.mock.calls[0][0];
    expect(sent.contrapartidas[0]).toEqual({ codigo: "6", nombre: "Bomedia Sabadell (ES33…1918)" });
    expect(sent.contrapartidas).toHaveLength(3);
    expect(sent.paypal_contrapartidas_by_store.boprint).toBe("6");
    expect(sent.paypal_contrapartidas_by_store.artisjet).toBe("12");
    expect(Object.keys(sent).sort()).toEqual(["contrapartidas", "paypal_contrapartidas_by_store"]);
  });
});

describe("ErpSettingsPage — «Enviar factura al cliente»: remitente por tienda y plantillas", () => {
  it("una fila por tienda Woo; el remitente de la tienda viaja al guardar y se ve el resultado", async () => {
    mockGet.mockResolvedValue(settings({
      woocommerce_stores: STORES,
      factusol_store_email_from: { boprint: "pedidos@streamtec.es" },
      factusol_series_default: "5",
      factusol_series_email_from: { "5": "pedidos@streamtec.es" },
    }));
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const boprint = await screen.findByLabelText("Remitente tienda boprint");
    expect(boprint).toHaveValue("pedidos@streamtec.es");
    expect(screen.getByLabelText("Remitente tienda fluxlasers")).toHaveValue("");
    const region = screen.getByRole("region", { name: "Remitentes del email de factura" });
    expect(region).toHaveTextContent("Las facturas de boprint saldrán de pedidos@streamtec.es.");
    // Sin remitente propio, la tienda cae al de su serie prevista.
    expect(region).toHaveTextContent(
      "Las facturas de fluxlasers saldrán de pedidos@streamtec.es (remitente de la serie 5 (Streamtec SL)).",
    );
    await user.clear(boprint);
    await user.type(boprint, "tienda@boprint.es");
    expect(region).toHaveTextContent("Las facturas de boprint saldrán de tienda@boprint.es.");
    await user.click(screen.getByRole("button", { name: "Guardar cambios · Remitentes del email de factura" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0].factusol_store_email_from).toMatchObject({
      boprint: "tienda@boprint.es",
    });
    expect(Object.keys(mockUpdate.mock.calls[0][0]).sort()).toEqual([
      "factusol_series_email_from", "factusol_store_email_from",
    ]);
  });

  it("plantillas por idioma: asunto y cuerpo editables viajan al guardar", async () => {
    mockGet.mockResolvedValue(settings({
      factusol_invoice_email_templates: {
        es: { subject: "Factura {numero}{pedido}", body: "Adjuntamos la factura {numero}{pedido}." },
        fr: { subject: "Facture {numero}{pedido}", body: "Ci-joint la facture {numero}{pedido}." },
      },
    }));
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const asuntoFr = await screen.findByLabelText("Asunto factura fr");
    expect(asuntoFr).toHaveValue("Facture {numero}{pedido}");
    await user.clear(asuntoFr);
    // user-event trata «{» como tecla especial: «{{» escribe la llave literal.
    await user.type(asuntoFr, "Votre facture {{numero}{{pedido}");
    await user.click(screen.getByRole("button", { name: "Guardar cambios · Plantillas del email de factura" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const sent = mockUpdate.mock.calls[0][0].factusol_invoice_email_templates;
    expect(sent.fr.subject).toBe("Votre facture {numero}{pedido}");
    expect(sent.es.subject).toBe("Factura {numero}{pedido}");  // el resto se conserva
  });
});

describe("ErpSettingsPage — aviso de envío al cliente", () => {
  it("remitentes de los pedidos manuales y plantillas por idioma viajan al guardar", async () => {
    mockGet.mockResolvedValue(settings({
      shipment_email_from: { es: "pedidos@streamtec.es", otros: "info@artisjet-printers.eu" },
      shipment_email_templates: {
        es: { subject: "Tu pedido {pedido} ya tiene envío", body: "Hola {cliente}" },
        de: { subject: "Ihre Bestellung {pedido}", body: "Hallo {cliente}" },
      },
    }));
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const region = await screen.findByRole("region", { name: "Aviso de envío al cliente" });
    const otros = within(region).getByLabelText("Remitente aviso manual en otros idiomas");
    expect(otros).toHaveValue("info@artisjet-printers.eu");
    await user.clear(otros);
    await user.type(otros, "export@artisjet-printers.eu");
    const asuntoDe = within(region).getByLabelText("Asunto aviso de envío de");
    await user.clear(asuntoDe);
    await user.type(asuntoDe, "Versand {{pedido}");
    // Las etiquetas del aviso no chocan con las de la factura.
    expect(screen.getByLabelText("Asunto factura de")).toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Ver ejemplo aviso es" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Guardar cambios · Aviso de envío al cliente" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const body = mockUpdate.mock.calls[0][0];
    expect(body.shipment_email_from).toEqual({
      es: "pedidos@streamtec.es", otros: "export@artisjet-printers.eu",
    });
    expect(body.shipment_email_templates.de.subject).toBe("Versand {pedido}");
    expect(body.shipment_email_templates.es.subject).toBe("Tu pedido {pedido} ya tiene envío");
    expect(body.factusol_invoice_email_templates).toBeUndefined();   // solo su sección
  });
});

// Lote 2 · PR-2 — cada ajuste con su resultado al lado, un «Guardar cambios»
// por sección con indicador de cambios sin guardar, plantillas con «Ver
// ejemplo» y «Enviarme una prueba».
describe("ErpSettingsPage — Lote 2 · PR-2", () => {
  it("cada sección tiene su «Guardar cambios», su indicador y guarda solo sus campos", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const sat = await screen.findByLabelText("Email del SAT");
    const region = screen.getByRole("region", { name: "Email del SAT / taller" });
    const save = within(region).getByRole("button", { name: "Guardar cambios · Email del SAT / taller" });
    // Sin cambios: nada que guardar y sin indicador.
    expect(save).toBeDisabled();
    expect(within(region).queryByText("Cambios sin guardar")).not.toBeInTheDocument();
    await user.type(sat, "taller@bomedia.net");
    expect(within(region).getByText("Cambios sin guardar")).toBeInTheDocument();
    expect(region).toHaveTextContent("«Enviar por email» saldrá precargado a taller@bomedia.net.");
    expect(save).toBeEnabled();
    // Las demás secciones siguen sin cambios.
    expect(screen.getByRole("button", SAVE_SERIES)).toBeDisabled();
    await user.click(save);
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate.mock.calls[0][0]).toEqual({ sat_email: "taller@bomedia.net" });
    // Éxito dentro de la sección y el indicador desaparece.
    expect(await within(region).findByText("Guardado.")).toBeInTheDocument();
    expect(within(region).queryByText("Cambios sin guardar")).not.toBeInTheDocument();
  });

  it("avisa al salir de la página con cambios sin guardar (beforeunload)", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const sat = await screen.findByLabelText("Email del SAT");
    const before = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(before);
    expect(before.defaultPrevented).toBe(false);
    await user.type(sat, "taller@bomedia.net");
    const after = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(true);
  });

  it("el prefijo enseña la siguiente referencia y se recompone en vivo al escribir", async () => {
    mockGet.mockResolvedValue(settings({
      woocommerce_stores: STORES,
      factusol_ref_prefix_by_store: { boprint: "BP" },
    }));
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const prefix = await screen.findByLabelText("Prefijo referencia FACTUSOL tienda boprint");
    expect(prefix).toHaveValue("BP");
    const region = screen.getByRole("region", { name: "Tiendas y referencias" });
    await waitFor(() => expect(region).toHaveTextContent("Siguiente referencia: BP-002480"));
    // Sin prefijo configurado: el derivado del nº de pedido y, si nunca hubo
    // pedidos de esa tienda, el 000001.
    expect(region).toHaveTextContent("Siguiente referencia: FLU-000001 · derivado del nº de pedido · aún sin pedidos de esta tienda");
    await user.type(prefix, "x");
    expect(prefix).toHaveValue("BPX");  // se guarda en mayúsculas
    expect(region).toHaveTextContent("Siguiente referencia: BPX-002480");
    expect(mockNextRefs).toHaveBeenCalledTimes(1);  // se recompone en cliente
    await user.click(within(region).getByRole("button", { name: "Guardar cambios · Tiendas y referencias" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0]).toEqual({ factusol_ref_prefix_by_store: { boprint: "BPX" } });
  });

  it("el ejemplo de la plantilla se actualiza al escribir (previsualización con datos de muestra)", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    const asunto = await screen.findByLabelText("Asunto factura es");
    const ejemplo = screen.getByTestId("ejemplo-es");
    await waitFor(() => expect(ejemplo).toHaveTextContent("Ejemplo: Factura 5-000118 · pedido BP-2479 — Estimado/a Rotulación Levante S.L.:"));
    await user.clear(asunto);
    await user.type(asunto, "Su factura {{numero}");
    await waitFor(() => expect(ejemplo).toHaveTextContent("Ejemplo: Su factura 5-000118 —"));
    const last = mockPreview.mock.calls[mockPreview.mock.calls.length - 1];
    expect(last[0]).toBe("es");
    expect(last[1]).toMatchObject({ subject: "Su factura {numero}" });
  });

  it("«Ver ejemplo» abre el modal con el correo completo", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    await user.click(await screen.findByRole("button", { name: "Ver ejemplo fr" }));
    const dialog = await screen.findByRole("dialog", { name: "Ejemplo del email de factura en Français" });
    // (el mock rellena los marcadores sin traducir el separador de {pedido})
    await waitFor(() => expect(dialog).toHaveTextContent("Facture 5-000118 · pedido BP-2479"));
    expect(within(dialog).getByTestId("ejemplo-cuerpo")).toHaveTextContent("Rotulación Levante S.L.");
    expect(dialog).toHaveTextContent("pedidos@streamtec.es (remitente de la serie por defecto)");
    expect(dialog).toHaveTextContent("Datos de muestra");
    await user.click(within(dialog).getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("«Enviarme una prueba» llama a la API con lo escrito y enseña el resultado", async () => {
    mockGet.mockResolvedValue(settings({
      factusol_invoice_email_templates: {
        es: { subject: "Su factura {numero}", body: "Hola {cliente}." },
      },
    }));
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    await user.click(await screen.findByRole("button", { name: "Enviarme una prueba es" }));
    await waitFor(() => expect(mockTest).toHaveBeenCalledWith("es", {
      subject: "Su factura {numero}", body: "Hola {cliente}.",
    }));
    expect(await screen.findByText(
      "Prueba enviada a admin@example.com desde pedidos@streamtec.es con el asunto «[Prueba] Factura 5-000118 · pedido BP-2479».",
    )).toBeInTheDocument();
  });

  it("«Enviarme una prueba» enseña el motivo si el remitente no se puede usar", async () => {
    mockTest.mockRejectedValue(new Error(
      "No se puede enviar desde pedidos@streamtec.es: El remitente no es un «enviar como» verificado de la cuenta de Gmail.",
    ));
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    await user.click(await screen.findByRole("button", { name: "Enviarme una prueba es" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No se puede enviar desde pedidos@streamtec.es");
  });

  it("sin permiso de guardar: botones desactivados con el motivo", async () => {
    mockGet.mockResolvedValue(settings({ can_edit: false }));
    render(<ErpSettingsPage />);
    const save = await screen.findByRole("button", SAVE_SERIES);
    expect(save).toBeDisabled();
    expect(save).toHaveAttribute("title", "Solo un administrador puede guardar.");
    expect(screen.getAllByText("Solo un administrador puede guardar.").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Enviarme una prueba es" })).toBeDisabled();
    // Ver el ejemplo sí se puede.
    expect(screen.getByRole("button", { name: "Ver ejemplo es" })).toBeEnabled();
  });
});
