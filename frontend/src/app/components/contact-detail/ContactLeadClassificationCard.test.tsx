import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContactLeadClassificationCard } from "./ContactLeadClassificationCard";
import { nivelConfianza } from "./LeadClasificacionResumen";
import { leadClasificacion as lead } from "./leadClasificacionFixture";
import {
  listContactLeadClasificaciones,
  type LeadClasificacion,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  listContactLeadClasificaciones: jest.fn(),
}));

const mockList = listContactLeadClasificaciones as jest.Mock;

const OPCIONES = {
  idiomas: ["es", "en", "fr", "de"],
  intereses: [{ id: "vending", label: "Vending" }, { id: "otro", label: "Otro" }],
};

function respuesta(items: LeadClasificacion[]) {
  return { umbral_confianza: 0.7, total: items.length, items, opciones: OPCIONES };
}

beforeEach(() => mockList.mockReset());

describe("ContactLeadClassificationCard (recuadro del Resumen)", () => {
  it("sin clasificación no pinta nada", async () => {
    mockList.mockResolvedValue(respuesta([]));
    const { container } = render(<ContactLeadClassificationCard contactId="c-torra" />);
    // Espera a que la petición vuelva: sigue vacío.
    await waitFor(() => expect(mockList).toHaveBeenCalledWith("c-torra"));
    await act(async () => undefined);
    expect(container.querySelector(".contact-lead-ia-card")).toBeNull();
    expect(screen.queryByText("Análisis de la IA")).not.toBeInTheDocument();
  });

  it("enseña la última clasificación: interés, idioma con su origen, confianza en rojo, "
     + "aviso de idioma, motivo entero y borrador pendiente", async () => {
    mockList.mockResolvedValue(respuesta([lead()]));
    const onSeeAll = jest.fn();
    const user = userEvent.setup();
    render(<ContactLeadClassificationCard contactId="c-torra" onSeeAll={onSeeAll} />);
    expect(await screen.findByText("Análisis de la IA")).toBeInTheDocument();
    // El interés manda: grande y con color.
    expect(screen.getByText("Vending")).toHaveClass("lead-ia-interes-valor");
    expect(screen.getByText("por los productos marcados")).toBeInTheDocument();
    expect(screen.getByText("FR")).toBeInTheDocument();
    expect(screen.getByText("por el texto")).toBeInTheDocument();
    expect(screen.getByText("el formulario era DE pero el texto está en FR")).toHaveClass("warn");
    // Semáforo: por debajo del umbral, en rojo.
    expect(screen.getByText("55%")).toHaveClass("bad");
    expect(screen.getByText("55%")).toHaveClass("is-baja");
    expect(screen.getByText("por debajo del umbral (70%)")).toBeInTheDocument();
    // Spam solo cuando lo es: nada de «SPAM · No».
    expect(screen.queryByText(/SPAM/)).not.toBeInTheDocument();
    // El motivo entero está (el recorte a dos líneas es visual) y, como es
    // largo, el Resumen ofrece «más».
    expect(screen.getByText(/la plantilla alemana no encaja\.$/)).toBeInTheDocument();
    const mas = screen.getByRole("button", { name: "más" });
    expect(mas).toHaveAttribute("aria-expanded", "false");
    await user.click(mas);
    expect(screen.getByRole("button", { name: "menos" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Clasificado")).toBeInTheDocument();
    expect(screen.getByText("Borrador pendiente de prepararse.")).toBeInTheDocument();
    expect(screen.getByText("anthropic · claude-haiku")).toBeInTheDocument();
    expect(screen.getByText(/Formulario web · pimpam-vending\.com/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Abrir el borrador" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Ver el análisis completo/ }));
    expect(onSeeAll).toHaveBeenCalledTimes(1);
  });

  it("con borrador preparado enlaza al borrador y no habla de pendiente", async () => {
    mockList.mockResolvedValue(respuesta([
      lead({ estado: "preparado", plantilla: "Lead · Vending (FR)", remitente: "info@pimpam-vending.com",
             borrador_id: "d-1", borrador_url: "/emails/drafts?id=d-1", confianza: 0.92,
             bajo_umbral: false, discrepancia_idioma: false, idioma_discrepancia_texto: null }),
      lead({ id: "vieja", lead_at: "2025-02-01T10:00:00Z" }),
    ]));
    render(<ContactLeadClassificationCard contactId="c-torra" />);
    expect(await screen.findByRole("link", { name: "Abrir el borrador" }))
      .toHaveAttribute("href", "/emails/drafts?id=d-1");
    expect(screen.getByText("92%")).toHaveClass("active");
    expect(screen.getByText("92%")).toHaveClass("is-alta");
    expect(screen.getByText("Borrador preparado")).toBeInTheDocument();
    expect(screen.getByText("Plantilla: Lead · Vending (FR)")).toBeInTheDocument();
    expect(screen.getByText("Desde: info@pimpam-vending.com")).toBeInTheDocument();
    expect(screen.queryByText("Borrador pendiente de prepararse.")).not.toBeInTheDocument();
    expect(screen.queryByText(/formulario era DE/)).not.toBeInTheDocument();
    // Solo la última; se avisa de que hay más en la pestaña.
    expect(screen.getByText(/2 análisis, este es el último/)).toBeInTheDocument();
  });

  it("con varios intereses el principal va grande y los demás en chips pequeños", async () => {
    mockList.mockResolvedValue(respuesta([
      lead({
        interes: "uv_mediano", interes_texto: "UV LED mediano formato", interes_fuente: "ia",
        intereses: ["uv_mediano", "dtf"],
        intereses_texto: "UV LED mediano formato + DTF · impresión textil",
        intereses_etiquetas: ["UV LED mediano formato", "DTF · impresión textil"],
        efectivo: { idioma: "de", interes: "uv_mediano", interes_texto: "UV LED mediano formato",
                    intereses: ["uv_mediano", "dtf"],
                    intereses_texto: "UV LED mediano formato + DTF · impresión textil",
                    intereses_etiquetas: ["UV LED mediano formato", "DTF · impresión textil"],
                    es_spam: false },
      }),
    ]));
    render(<ContactLeadClassificationCard contactId="c-torra" />);
    expect(await screen.findByText("UV LED mediano formato")).toHaveClass("lead-ia-interes-valor");
    expect(screen.getByText("+ DTF · impresión textil")).toHaveClass("lead-ia-secundario");
    expect(screen.getByText("por la IA")).toBeInTheDocument();
  });

  it("corregido a mano a otra lista, se dice lo que había dicho la IA (todos sus intereses)", async () => {
    mockList.mockResolvedValue(respuesta([
      lead({
        intereses: ["uv_mediano", "dtf"], interes: "uv_mediano", interes_texto: "UV LED mediano formato",
        intereses_texto: "UV LED mediano formato + DTF · impresión textil",
        intereses_etiquetas: ["UV LED mediano formato", "DTF · impresión textil"],
        efectivo: { idioma: "fr", interes: "dtf", interes_texto: "DTF · impresión textil",
                    intereses: ["dtf", "uv_mediano"],
                    intereses_texto: "DTF · impresión textil + UV LED mediano formato",
                    intereses_etiquetas: ["DTF · impresión textil", "UV LED mediano formato"],
                    es_spam: false },
        correccion: { corregida: true, idioma: null, interes: "dtf", intereses: ["dtf", "uv_mediano"],
                      es_spam: null, nota: null, por: "Bart", cuando: "2026-10-10T09:00:00Z" },
      }),
    ]));
    render(<ContactLeadClassificationCard contactId="c-torra" />);
    expect(await screen.findByText("DTF · impresión textil")).toHaveClass("lead-ia-interes-valor");
    expect(screen.getByText("+ UV LED mediano formato")).toHaveClass("lead-ia-secundario");
    expect(screen.getByText(
      "corregido a mano (la IA dijo UV LED mediano formato + DTF · impresión textil)",
    )).toBeInTheDocument();
  });

  it("si la carga falla el recuadro dice el error (no se esconde)", async () => {
    mockList.mockRejectedValue(new Error("Sin red"));
    render(<ContactLeadClassificationCard contactId="c-torra" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Sin red");
  });

  it("un motivo corto no ofrece «más», y el spam se ve desde lejos", async () => {
    mockList.mockResolvedValue(respuesta([
      lead({ motivo: "Oferta de generación de leads.", es_spam: true, confianza: 0.75,
             efectivo: { idioma: "fr", interes: "otro", interes_texto: "Otro", intereses: ["otro"],
                         intereses_texto: "Otro", es_spam: true } }),
    ]));
    render(<ContactLeadClassificationCard contactId="c-torra" />);
    expect(await screen.findByText("SPAM")).toHaveClass("lead-ia-spam");
    expect(screen.queryByRole("button", { name: "más" })).not.toBeInTheDocument();
    // 0,75 con umbral 0,7: justo por encima, en ámbar.
    expect(screen.getByText("75%")).toHaveClass("is-justa");
    expect(screen.getByText("justo por encima del umbral (70%)")).toBeInTheDocument();
  });
});

describe("nivelConfianza (semáforo frente al umbral)", () => {
  it("rojo por debajo, aviso hasta una décima por encima, verde después", () => {
    expect(nivelConfianza(0.55, 0.7)).toBe("baja");
    expect(nivelConfianza(0.7, 0.7)).toBe("justa");
    expect(nivelConfianza(0.79, 0.7)).toBe("justa");
    expect(nivelConfianza(0.85, 0.7)).toBe("alta");
    // El umbral es el configurado, no un 0,7 fijo.
    expect(nivelConfianza(0.85, 0.9)).toBe("baja");
  });
});
