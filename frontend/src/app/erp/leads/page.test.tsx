import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LeadsPage from "./page";
import {
  corregirLeadClasificacion,
  crearLeadWorkflow,
  getLeadWorkflow,
  listLeadClasificaciones,
  simularLeadsEnSeco,
  type LeadClasificacion,
} from "../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, prefetch, ...rest }: { children: React.ReactNode; href: string;
    prefetch?: boolean } & Record<string, unknown>) => (
    <a href={href} data-prefetch={String(prefetch)} {...rest}>{children}</a>
  ),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <header><h1>{title}</h1>{actions}</header>
  ),
}));
jest.mock("../../lib/erpApi", () => ({
  listLeadClasificaciones: jest.fn(),
  corregirLeadClasificacion: jest.fn(),
  simularLeadsEnSeco: jest.fn(),
  getLeadWorkflow: jest.fn(),
  crearLeadWorkflow: jest.fn(),
}));

const mockList = listLeadClasificaciones as jest.Mock;
const mockCorregir = corregirLeadClasificacion as jest.Mock;
const mockSimular = simularLeadsEnSeco as jest.Mock;
const mockWorkflow = getLeadWorkflow as jest.Mock;
const mockCrear = crearLeadWorkflow as jest.Mock;

const OPCIONES = {
  idiomas: ["es", "en", "fr", "de", "nl", "pt", "ca", "it"],
  intereses: [
    { id: "vending", label: "Vending", comercial: true, activo: true },
    { id: "uv_mediano", label: "UV LED mediano formato", comercial: true, activo: true },
    { id: "dtf", label: "DTF · impresión textil", comercial: true, activo: true },
    { id: "otro", label: "Otro", comercial: false, activo: true },
  ],
};

function lead(over: Partial<LeadClasificacion> = {}): LeadClasificacion {
  return {
    id: "lc-1",
    contacto: { id: "c-isabella", nombre: "Isabella Cedillo", email: "isabella@glowbtl.mx" },
    fuente: "web_form", referencia: "env-1", lead_at: "2026-10-08T10:15:00Z",
    web: "pimpam-vending.com", cuenta_agile: null, productos: ["Vending"],
    texto: "Quiero información sobre máquinas de vending para oficinas en México.",
    idioma: "es", idioma_fuente: "formulario", idioma_formulario: "es", discrepancia_idioma: false,
    interes: "vending", interes_texto: "Vending", interes_fuente: "etiquetas",
    intereses: ["vending"], intereses_texto: "Vending", intereses_etiquetas: ["Vending"],
    es_spam: false, confianza: 0.95, bajo_umbral: false,
    motivo: "Productos marcados: Vending.", proveedor: "palabras_clave", modelo: null,
    estado: "preparado", estado_detalle: null,
    plantilla: "Lead · Vending (ES)", remitente: "info@pimpam-vending.com",
    borrador_id: "d-1", borrador_url: "https://crm.example.com/emails/drafts?id=d-1",
    tarea_id: "t-1", run_id: "run-1",
    efectivo: { idioma: "es", interes: "vending", interes_texto: "Vending",
                intereses: ["vending"], intereses_texto: "Vending",
                intereses_etiquetas: ["Vending"], es_spam: false },
    correccion: { corregida: false, idioma: null, interes: null, intereses: [], es_spam: null,
                  nota: null, por: null, cuando: null },
    creado: "2026-10-08T22:15:00Z",
    ...over,
  };
}

/** Klaus (10/10/2026): placas de metal Y camisetas → UV mediano y DTF. */
const KLAUS_INTERESES = {
  interes: "uv_mediano", interes_texto: "UV LED mediano formato", interes_fuente: "texto",
  intereses: ["uv_mediano", "dtf"],
  intereses_texto: "UV LED mediano formato + DTF · impresión textil",
  intereses_etiquetas: ["UV LED mediano formato", "DTF · impresión textil"],
  efectivo: { idioma: "de", interes: "uv_mediano", interes_texto: "UV LED mediano formato",
              intereses: ["uv_mediano", "dtf"],
              intereses_texto: "UV LED mediano formato + DTF · impresión textil",
              intereses_etiquetas: ["UV LED mediano formato", "DTF · impresión textil"],
              es_spam: false },
};

const SIN_WORKFLOW = {
  existe: false, id: null, status: null, url: null, pipeline_ok: true, pipeline_aviso: null,
};

beforeEach(() => {
  mockList.mockReset();
  mockCorregir.mockReset();
  mockSimular.mockReset();
  mockWorkflow.mockReset();
  mockCrear.mockReset();
  mockList.mockResolvedValue({
    dias: 15, umbral_confianza: 0.7, total: 2, corregidas: 0, opciones: OPCIONES,
    items: [
      lead(),
      lead({
        id: "lc-2", contacto: { id: "c-klaus", nombre: "Klaus Druck", email: "klaus@druck.de" },
        fuente: "agilecrm", referencia: "nota-1", web: null, cuenta_agile: "agile-bomedia",
        productos: [], texto: "Ich möchte auf Metallplatten sowie auf T-Shirts drucken.",
        idioma: "de", idioma_fuente: "texto", idioma_formulario: null,
        ...KLAUS_INTERESES,
        confianza: 0.55, bajo_umbral: true, estado: "sin_plantilla",
        estado_detalle: "sin plantilla para UV LED mediano formato + DTF · impresión textil en de",
        plantilla: null, remitente: null, borrador_id: null, borrador_url: null,
      }),
    ],
  });
  mockWorkflow.mockResolvedValue(SIN_WORKFLOW);
  mockCrear.mockResolvedValue({
    id: "wf-1", name: "Respuesta a leads (Fase 1)", status: "draft", url: "/admin/workflows/wf-1",
  });
  mockSimular.mockResolvedValue({
    dias: 15, limite: 200, nada_escrito: true,
    resumen: { total: 2, spam: 1, sin_plantilla: 0, con_discrepancia_idioma: 1, ya_clasificados: 1,
               por_interes: { vending: 1, otro: 1 }, por_idioma: { es: 1, de: 1 },
               proveedor: "palabras_clave" },
    items: [
      { contacto_id: "c-isabella", nombre: "Isabella Cedillo", email: "isabella@glowbtl.mx",
        fuente: "web_form", referencia: "env-1", lead_at: "2026-10-08T10:15:00Z",
        web: "pimpam-vending.com", idioma_formulario: "es", productos: ["Vending"],
        texto: "Quiero información sobre máquinas de vending.",
        clasificacion: { idioma: "es", interes: "vending", intereses: ["vending"],
                         intereses_texto: "Vending", es_spam: false, confianza: 0.95,
                         motivo: "Productos marcados: Vending.", discrepancia_idioma: false },
        ya_clasificado: true,
        haria: { etapa: "Nuevo lead", plantilla: "Lead · Vending (ES)",
                 remitente: "info@pimpam-vending.com", tarea: true, aviso: null } },
      { contacto_id: "c-spam", nombre: "SEO Agency", email: "x@leads.com",
        fuente: "web_form", referencia: "env-2", lead_at: "2026-10-07T10:15:00Z",
        web: "artisjet.es", idioma_formulario: "es", productos: [],
        texto: "We generate leads for your business.",
        clasificacion: { idioma: "en", interes: "otro", es_spam: true, confianza: 0.9,
                         motivo: "Oferta de generación de leads.", discrepancia_idioma: true },
        ya_clasificado: false,
        haria: { etapa: "Descartado / spam", plantilla: null, remitente: null, tarea: false,
                 aviso: null } },
    ],
  });
});

describe("ERP · Leads (respuesta a leads · Fase 1)", () => {
  it("lista los leads procesados: clasificación, resultado, borrador y la confianza baja en rojo", async () => {
    render(<LeadsPage />);
    const isabella = (await screen.findByRole("link", { name: "Isabella Cedillo" })).closest("tr")!;
    expect(mockList).toHaveBeenCalledWith(15);
    expect(within(isabella).getByLabelText("Interés de Isabella Cedillo")).toHaveValue("vending");
    expect(within(isabella).getByLabelText("Idioma de Isabella Cedillo")).toHaveValue("es");
    expect(within(isabella).getByLabelText("Spam de Isabella Cedillo")).not.toBeChecked();
    expect(within(isabella).getByText("95%")).toHaveClass("active");
    expect(within(isabella).getByText("Borrador preparado")).toBeInTheDocument();
    expect(within(isabella).getByRole("link", { name: "Abrir el borrador" }))
      .toHaveAttribute("href", "https://crm.example.com/emails/drafts?id=d-1");
    expect(within(isabella).getByText("Formulario web · pimpam-vending.com", { exact: false }))
      .toBeInTheDocument();
    // Klaus: por debajo del umbral y sin plantilla; dos intereses, el principal
    // en el desplegable y el segundo como chip.
    const klaus = screen.getByRole("link", { name: "Klaus Druck" }).closest("tr")!;
    expect(within(klaus).getByText("55%")).toHaveClass("bad");
    expect(within(klaus).getByText("Sin plantilla")).toBeInTheDocument();
    expect(within(klaus).getByText("AgileCRM · agile-bomedia", { exact: false })).toBeInTheDocument();
    expect(within(klaus).getByLabelText("Interés de Klaus Druck")).toHaveValue("uv_mediano");
    expect(within(klaus).getByText("DTF · impresión textil")).toBeInTheDocument();
    expect(within(klaus).getByRole("button", { name: "Quitar DTF · impresión textil de los intereses de Klaus Druck" }))
      .toBeInTheDocument();
    expect(screen.getByText("2 procesados · 0 corregidos a mano · umbral de confianza 70%"))
      .toBeInTheDocument();
    // Sin workflow todavía: se ofrece crearlo.
    expect(screen.getByRole("button", { name: "Crear el workflow" })).toBeEnabled();
  });

  it("corregir manda solo lo que cambia y la fila pasa a enseñar la corrección", async () => {
    mockCorregir.mockImplementation((id: string, payload: { intereses: string[]; nota?: string }) =>
      Promise.resolve(lead({
        efectivo: { idioma: "es", interes: payload.intereses[0], interes_texto: "Otro",
                    intereses: payload.intereses, intereses_texto: "Otro",
                    intereses_etiquetas: ["Otro"], es_spam: false },
        correccion: { corregida: true, idioma: null, interes: payload.intereses[0],
                      intereses: payload.intereses, es_spam: null,
                      nota: String(payload.nota ?? ""), por: "Bart", cuando: "2026-10-09T09:00:00Z" },
      })));
    const user = userEvent.setup();
    render(<LeadsPage />);
    const fila = (await screen.findByRole("link", { name: "Isabella Cedillo" })).closest("tr")!;
    // Sin cambios no hay botón.
    expect(within(fila).queryByRole("button", { name: "Guardar corrección" })).not.toBeInTheDocument();
    await user.selectOptions(within(fila).getByLabelText("Interés de Isabella Cedillo"), "otro");
    await user.type(within(fila).getByLabelText("Nota de la corrección de Isabella Cedillo"),
      "era una consulta de distribución");
    await user.click(within(fila).getByRole("button", { name: "Guardar corrección" }));
    // Los intereses viajan en lista, en el orden elegido.
    await waitFor(() => expect(mockCorregir).toHaveBeenCalledWith("lc-1", {
      intereses: ["otro"], nota: "era una consulta de distribución",
    }));
    expect(await screen.findByText("Corregido por Bart el", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("2 procesados · 1 corregidos a mano · umbral de confianza 70%"))
      .toBeInTheDocument();
    // Tras la corrección la fila arranca de lo corregido: sin cambios, sin botón.
    const filaNueva = screen.getByRole("link", { name: "Isabella Cedillo" }).closest("tr")!;
    expect(within(filaNueva).getByLabelText("Interés de Isabella Cedillo")).toHaveValue("otro");
    expect(within(filaNueva).queryByRole("button", { name: "Guardar corrección" }))
      .not.toBeInTheDocument();
  });

  it("varios intereses: añadir uno, subirlo o quitarlo viaja como la lista entera en su orden", async () => {
    mockCorregir.mockImplementation((id: string, payload: { intereses: string[] }) =>
      Promise.resolve(lead({ id, efectivo: { ...lead().efectivo, intereses: payload.intereses,
                                             interes: payload.intereses[0] } })));
    const user = userEvent.setup();
    render(<LeadsPage />);
    const isabella = (await screen.findByRole("link", { name: "Isabella Cedillo" })).closest("tr")!;
    await user.selectOptions(within(isabella).getByLabelText("Añadir interés a Isabella Cedillo"), "dtf");
    await user.click(within(isabella).getByRole("button", { name: "Guardar corrección" }));
    await waitFor(() => expect(mockCorregir).toHaveBeenCalledWith("lc-1", {
      intereses: ["vending", "dtf"],
    }));
    // Klaus: DTF pasa a principal con «subir».
    const klaus = screen.getByRole("link", { name: "Klaus Druck" }).closest("tr")!;
    await user.click(within(klaus).getByRole("button", { name: "Subir DTF · impresión textil en los intereses de Klaus Druck" }));
    expect(within(klaus).getByLabelText("Interés de Klaus Druck")).toHaveValue("dtf");
    await user.click(within(klaus).getByRole("button", { name: "Guardar corrección" }));
    await waitFor(() => expect(mockCorregir).toHaveBeenLastCalledWith("lc-2", {
      intereses: ["dtf", "uv_mediano"],
    }));
  });

  it("marcar como spam viaja como es_spam y el error del servidor se enseña en la fila", async () => {
    mockCorregir.mockRejectedValue(new Error("Clasificación no encontrada."));
    const user = userEvent.setup();
    render(<LeadsPage />);
    const fila = (await screen.findByRole("link", { name: "Klaus Druck" })).closest("tr")!;
    await user.click(within(fila).getByLabelText("Spam de Klaus Druck"));
    await user.click(within(fila).getByRole("button", { name: "Guardar corrección" }));
    await waitFor(() => expect(mockCorregir).toHaveBeenCalledWith("lc-2", { es_spam: true }));
    expect(await within(fila).findByRole("alert")).toHaveTextContent("Clasificación no encontrada.");
  });

  it("el modo en seco pide los días elegidos y enseña el resumen sin escribir nada", async () => {
    const user = userEvent.setup();
    render(<LeadsPage />);
    await screen.findByRole("link", { name: "Isabella Cedillo" });
    await user.selectOptions(screen.getByLabelText("Días del modo en seco"), "30");
    await user.click(screen.getByRole("button", { name: "Simular en seco" }));
    await waitFor(() => expect(mockSimular).toHaveBeenCalledWith(30));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "2 leads en 15 días (proveedor: palabras_clave): 1 spam, 0 sin plantilla, "
      + "1 con el idioma distinto del formulario, 1 ya procesados de verdad. Nada escrito.",
    );
    expect(screen.getByText("Por interés: Vending 1 · Otro 1. Por idioma: ES 1 · DE 1."))
      .toBeInTheDocument();
    const spam = screen.getByRole("link", { name: "SEO Agency" }).closest("tr")!;
    expect(within(spam).getByText("Spam")).toBeInTheDocument();
    expect(within(spam).getByText("Etapa: Descartado / spam")).toBeInTheDocument();
    expect(within(spam).getByText("≠ formulario ES")).toBeInTheDocument();
    const ok = screen.getAllByRole("link", { name: "Isabella Cedillo" })
      .map((l) => l.closest("tr")!)
      .find((tr) => within(tr).queryByText("Etapa: Nuevo lead"))!;
    expect(within(ok).getByText("Plantilla: Lead · Vending (ES) · desde info@pimpam-vending.com"))
      .toBeInTheDocument();
    expect(within(ok).getByText("ya procesado", { exact: false })).toBeInTheDocument();
    // Nada de escribir: solo la simulación.
    expect(mockCorregir).not.toHaveBeenCalled();
  });

  it("crea el workflow en borrador y luego enseña su estado con el enlace al editor", async () => {
    mockWorkflow
      .mockResolvedValueOnce(SIN_WORKFLOW)
      .mockResolvedValue({
        existe: true, id: "wf-1", status: "draft", url: "/admin/workflows/wf-1",
        pipeline_ok: true, pipeline_aviso: null,
      });
    const user = userEvent.setup();
    render(<LeadsPage />);
    await user.click(await screen.findByRole("button", { name: "Crear el workflow" }));
    await waitFor(() => expect(mockCrear).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Workflow «Respuesta a leads (Fase 1)» creado en borrador.", { exact: false }))
      .toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Abrir en Workflows" }))
      .toHaveAttribute("href", "/admin/workflows/wf-1");
    expect(screen.getByText("en borrador")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crear el workflow" })).not.toBeInTheDocument();
  });

  it("sin el pipeline «Ventas B2B» no deja crear el workflow y dice por qué", async () => {
    mockWorkflow.mockResolvedValue({
      ...SIN_WORKFLOW, pipeline_ok: false,
      pipeline_aviso: "No existe el pipeline «Ventas B2B»: créalo en Pipelines con las etapas Nuevo lead y Descartado / spam.",
    });
    render(<LeadsPage />);
    expect(await screen.findByRole("button", { name: "Crear el workflow" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("No existe el pipeline «Ventas B2B»");
  });
});
