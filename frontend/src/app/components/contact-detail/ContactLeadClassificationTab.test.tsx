import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContactLeadClassificationTab } from "./ContactLeadClassificationTab";
import { leadClasificacion as lead } from "./leadClasificacionFixture";
import {
  corregirLeadClasificacion,
  listContactLeadClasificaciones,
  type LeadClasificacion,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  listContactLeadClasificaciones: jest.fn(),
  corregirLeadClasificacion: jest.fn(),
}));

const mockList = listContactLeadClasificaciones as jest.Mock;
const mockCorregir = corregirLeadClasificacion as jest.Mock;

const OPCIONES = {
  idiomas: ["es", "en", "fr", "de"],
  intereses: [{ id: "vending", label: "Vending" }, { id: "distribucion", label: "Distribución" },
              { id: "otro", label: "Otro" }],
};

function respuesta(items: LeadClasificacion[]) {
  return { umbral_confianza: 0.7, total: items.length, items, opciones: OPCIONES };
}

const VIEJA = lead({
  id: "vieja", lead_at: "2025-02-01T10:00:00Z", fuente: "agilecrm", web: null,
  cuenta_agile: "agile-bomedia", texto_completo: "Wir suchen einen UV-Drucker.", texto: "Wir suchen…",
  idioma: "de", idioma_formulario: null, discrepancia_idioma: false, idioma_discrepancia_texto: null,
  interes: "otro", interes_texto: "Otro", confianza: 0.9, bajo_umbral: false, estado: "preparado",
  plantilla: "Lead · Otro (DE)", remitente: "info@bomedia.de", borrador_id: "d-2",
  borrador_url: "/emails/drafts?id=d-2", contexto: { fuente: "agilecrm", referencia: "nota-1",
  sitio: null, formulario: null, idioma_formulario: null, productos: [], pais: null,
  cuenta_agile: "agile-bomedia", dominio_email: "druck.de" },
  efectivo: { idioma: "de", interes: "otro", interes_texto: "Otro", es_spam: false },
  correccion: { corregida: true, idioma: null, interes: "otro", es_spam: null,
                nota: "era otra cosa", por: "Bart", cuando: "2025-02-02T09:00:00Z" },
});

beforeEach(() => {
  mockList.mockReset();
  mockCorregir.mockReset();
});

describe("ContactLeadClassificationTab (pestaña «Análisis IA»)", () => {
  it("lista los análisis, el último arriba, con la consulta entera y el contexto", async () => {
    mockList.mockResolvedValue(respuesta([lead(), VIEJA]));
    render(<ContactLeadClassificationTab contactId="c-torra" canCorrect />);
    const items = await screen.findAllByRole("article", { name: /Análisis del lead del/ });
    expect(items).toHaveLength(2);
    expect(mockList).toHaveBeenCalledWith("c-torra");
    expect(screen.getByText("2 análisis · umbral de confianza 70%")).toBeInTheDocument();

    const ultimo = items[0];
    expect(within(ultimo).getByText("Último")).toBeInTheDocument();
    // La consulta entera, con sus saltos de línea, no los 160 caracteres de la lista.
    expect(within(ultimo).getByText(/bureau de 40 personnes\./)).toBeInTheDocument();
    expect(within(ultimo).getByText(/Torra$/)).toBeInTheDocument();
    // El contexto que entró con la consulta.
    expect(within(ultimo).getByText("Web")).toBeInTheDocument();
    expect(within(ultimo).getByText("pimpam-vending.com")).toBeInTheDocument();
    expect(within(ultimo).getByText("Idioma del formulario")).toBeInTheDocument();
    expect(within(ultimo).getByText("de")).toBeInTheDocument();
    expect(within(ultimo).getByText("Productos marcados")).toBeInTheDocument();
    expect(within(ultimo).getByText("País")).toBeInTheDocument();
    expect(within(ultimo).getByText("elbarquito.net")).toBeInTheDocument();
    expect(within(ultimo).queryByText("fuente")).not.toBeInTheDocument();
    // La clasificación, igual que en el recuadro.
    expect(within(ultimo).getByText("55%")).toHaveClass("bad");
    expect(within(ultimo).getByText("el formulario era DE pero el texto está en FR")).toBeInTheDocument();

    const viejo = items[1];
    expect(within(viejo).queryByText("Último")).not.toBeInTheDocument();
    expect(within(viejo).getByText("Wir suchen einen UV-Drucker.")).toBeInTheDocument();
    expect(within(viejo).getByText(/AgileCRM · agile-bomedia/)).toBeInTheDocument();
    expect(within(viejo).getByText(/Corregido por Bart el/)).toHaveTextContent("era otra cosa");
    expect(within(viejo).getByRole("link", { name: "Abrir el borrador" }))
      .toHaveAttribute("href", "/emails/drafts?id=d-2");
    // La fecha de 2025 lleva el año.
    expect(within(viejo).getByRole("heading", { level: 3 })).toHaveTextContent("2025");
  });

  it("corregir manda solo lo que cambia (la corrección de ERP · Leads) y el análisis se actualiza",
     async () => {
    mockList.mockResolvedValue(respuesta([lead()]));
    mockCorregir.mockImplementation((id: string, payload: Record<string, unknown>) =>
      Promise.resolve(lead({
        efectivo: { idioma: "fr", interes: String(payload.interes), interes_texto: "Distribución",
                    es_spam: false },
        correccion: { corregida: true, idioma: null, interes: String(payload.interes), es_spam: null,
                      nota: String(payload.nota ?? ""), por: "Bart", cuando: "2026-10-10T09:00:00Z" },
      })));
    const user = userEvent.setup();
    render(<ContactLeadClassificationTab contactId="c-torra" canCorrect />);
    const item = (await screen.findAllByRole("article", { name: /Análisis del lead del/ }))[0];
    expect(within(item).queryByRole("button", { name: "Guardar corrección" })).not.toBeInTheDocument();
    await user.selectOptions(within(item).getByLabelText(/^Interés del lead del/), "distribucion");
    await user.type(within(item).getByLabelText(/^Nota de la corrección del lead del/),
      "quiere distribuir, no comprar");
    await user.click(within(item).getByRole("button", { name: "Guardar corrección" }));
    await waitFor(() => expect(mockCorregir).toHaveBeenCalledWith(
      "48cd85c8-9ae8-45df-a645-17d62bbb11bd",
      { interes: "distribucion", nota: "quiere distribuir, no comprar" },
    ));
    expect(await screen.findByText(/Corregido por Bart el/)).toHaveTextContent("quiere distribuir, no comprar");
    // Lo que manda ahora es lo corregido (y se dice lo que había dicho la IA).
    expect(screen.getByText("Distribución", { selector: ".lead-ia-interes-valor" }))
      .toBeInTheDocument();
    expect(screen.getByText(/corregido a mano \(la IA dijo Vending\)/)).toBeInTheDocument();
    // Tras guardar se arranca de lo corregido: sin cambios, sin botón.
    expect(screen.queryByRole("button", { name: "Guardar corrección" })).not.toBeInTheDocument();
    expect(screen.getByText("Corrección guardada.")).toBeInTheDocument();
    // Una segunda corrección sin tocar la nota la conserva (el servidor
    // sustituye la nota entera en cada corrección).
    await user.click(screen.getByLabelText(/^Spam del lead del/));
    expect(screen.getByLabelText(/^Nota de la corrección del lead del/))
      .toHaveValue("quiere distribuir, no comprar");
    await user.click(screen.getByRole("button", { name: "Guardar corrección" }));
    await waitFor(() => expect(mockCorregir).toHaveBeenLastCalledWith(
      "48cd85c8-9ae8-45df-a645-17d62bbb11bd",
      { es_spam: true, nota: "quiere distribuir, no comprar" },
    ));
  });

  it("el error del servidor al corregir se enseña en el análisis", async () => {
    mockList.mockResolvedValue(respuesta([lead()]));
    mockCorregir.mockRejectedValue(new Error("No tienes la capacidad erp.config."));
    const user = userEvent.setup();
    render(<ContactLeadClassificationTab contactId="c-torra" canCorrect />);
    const item = (await screen.findAllByRole("article", { name: /Análisis del lead del/ }))[0];
    await user.click(within(item).getByLabelText(/^Spam del lead del/));
    await user.click(within(item).getByRole("button", { name: "Guardar corrección" }));
    await waitFor(() => expect(mockCorregir).toHaveBeenCalledWith(
      "48cd85c8-9ae8-45df-a645-17d62bbb11bd", { es_spam: true }));
    expect(await within(item).findByRole("alert")).toHaveTextContent("No tienes la capacidad erp.config.");
  });

  it("sin la capacidad de configuración no hay formulario y se dice por qué", async () => {
    mockList.mockResolvedValue(respuesta([lead()]));
    render(<ContactLeadClassificationTab contactId="c-torra" canCorrect={false} />);
    await screen.findAllByRole("article", { name: /Análisis del lead del/ });
    expect(screen.queryByLabelText(/^Interés del lead del/)).not.toBeInTheDocument();
    expect(screen.getByText(/requiere el permiso de configuración del ERP/)).toBeInTheDocument();
  });

  it("sin análisis lo dice", async () => {
    mockList.mockResolvedValue(respuesta([]));
    render(<ContactLeadClassificationTab contactId="c-torra" canCorrect />);
    expect(await screen.findByText(/no tiene ningún análisis de la IA/)).toBeInTheDocument();
  });
});
