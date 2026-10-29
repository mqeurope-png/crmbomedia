import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MencionFacturaModal } from "./MencionFacturaModal";

/** Mención legal en una factura ya emitida: vista previa con el texto exacto
 *  y la observación donde va, confirmación explícita y aviso si ya se envió. */

const getMencionesLegales = jest.fn();
const previewMencion = jest.fn();
const aplicarMencion = jest.fn();
jest.mock("../../lib/erpApi", () => ({
  getMencionesLegales: (...a: unknown[]) => getMencionesLegales(...a),
  previewMencion: (...a: unknown[]) => previewMencion(...a),
  aplicarMencion: (...a: unknown[]) => aplicarMencion(...a),
}));

const TEXTO = "Operación exenta de IVA por exportación, artículo 21.1º de la Ley 37/1992.";

function plan(over: Record<string, unknown> = {}) {
  return {
    factura: "2-526090", texto: TEXTO, mencion_id: "fuera-ue", campo: "OB2FAC", ya_esta: false,
    actuales: { OB1FAC: "Pedido web de prueba", OB2FAC: "" }, enviada: null, avisos: [],
    ...over,
  };
}

beforeEach(() => {
  getMencionesLegales.mockReset();
  previewMencion.mockReset();
  aplicarMencion.mockReset();
  getMencionesLegales.mockResolvedValue({
    max: 255, verifactu_activo: false,
    items: [
      { id: "ceuta-melilla", nombre: "Ceuta y Melilla", texto: "Texto de referencia", revisado: false },
      { id: "fuera-ue", nombre: "Fuera de la UE", texto: TEXTO, revisado: true },
    ],
  });
});

function abrir() {
  return render(
    <MencionFacturaModal orderId="o-1" orderNumber="BOPRIN-9" factura="2-526090" onClose={jest.fn()} />,
  );
}

describe("MencionFacturaModal", () => {
  it("las menciones sin revisar no se pueden elegir", async () => {
    abrir();
    const select = await screen.findByLabelText("Mención legal");
    const opciones = within(select).getAllByRole("option");
    const ceuta = opciones.find((o) => o.textContent?.startsWith("Ceuta y Melilla"));
    expect(ceuta).toBeDisabled();
    expect(ceuta).toHaveTextContent("(sin revisar)");
    expect(opciones.find((o) => o.textContent === "Fuera de la UE")).toBeEnabled();
  });

  it("exige confirmación y escribe exactamente lo de la vista previa", async () => {
    previewMencion.mockResolvedValue(plan());
    aplicarMencion.mockResolvedValue({
      ok: true, factura: "2-526090", campo: "OB2FAC", texto: TEXTO, ya_enviada: false,
    });
    const user = userEvent.setup();
    abrir();
    await user.selectOptions(await screen.findByLabelText("Mención legal"), "fuera-ue");
    await user.click(screen.getByRole("button", { name: "Ver cómo queda" }));
    await waitFor(() => expect(previewMencion).toHaveBeenCalledWith("o-1", { mencion_id: "fuera-ue" }));
    const previa = within(screen.getByLabelText("Vista previa"));
    expect(previa.getByText(/Se escribirá en/)).toHaveTextContent("Observaciones 2 (OB2FAC)");
    expect(previa.getByText(`«${TEXTO}»`)).toBeInTheDocument();
    expect(previa.getByText(/Ya tiene/)).toHaveTextContent("«Pedido web de prueba» (no se toca)");
    const anadir = screen.getByRole("button", { name: "Añadir a la factura" });
    expect(anadir).toBeDisabled();
    await user.click(screen.getByLabelText("Confirmo la mención"));
    await user.click(anadir);
    await waitFor(() => expect(aplicarMencion).toHaveBeenCalledWith("o-1", {
      mencion_id: "fuera-ue", campo: "OB2FAC",
    }));
    expect(await screen.findByRole("status")).toHaveTextContent("Mención añadida a la factura 2-526090");
  });

  it("si la factura ya se envió lo avisa (el PDF del cliente no la lleva)", async () => {
    previewMencion.mockResolvedValue(plan({
      enviada: { at: "2026-10-01", via: "email" },
      avisos: ["La factura ya se envió al cliente: el PDF que tiene NO lleva la mención."],
    }));
    const user = userEvent.setup();
    abrir();
    await user.selectOptions(await screen.findByLabelText("Mención legal"), "fuera-ue");
    await user.click(screen.getByRole("button", { name: "Ver cómo queda" }));
    expect(await screen.findByText(/el PDF que tiene NO lleva la mención/)).toBeInTheDocument();
  });

  it("texto libre: se manda el texto, y sin hueco en las observaciones no se ofrece escribir", async () => {
    previewMencion.mockResolvedValue(plan({
      texto: "Mención libre de prueba", mencion_id: null, campo: null,
      actuales: { OB1FAC: "uno", OB2FAC: "dos" },
      avisos: ["Las dos observaciones de la factura ya tienen texto y BoHub no pisa ninguna."],
    }));
    const user = userEvent.setup();
    abrir();
    await user.selectOptions(await screen.findByLabelText("Mención legal"), "__libre__");
    await user.type(screen.getByLabelText("Texto de la mención"), "Mención libre de prueba");
    await user.click(screen.getByRole("button", { name: "Ver cómo queda" }));
    await waitFor(() => expect(previewMencion).toHaveBeenCalledWith("o-1", {
      texto: "Mención libre de prueba",
    }));
    expect(await screen.findByText(/BoHub no pisa ninguna/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Añadir a la factura" })).toBeNull();
  });

  it("en un pedido en inglés sale el texto en inglés; sin él, el castellano y un aviso", async () => {
    const EN = "VAT exempt export (texto de prueba en inglés).";
    getMencionesLegales.mockResolvedValue({
      max: 255, verifactu_activo: false,
      items: [{ id: "fuera-ue", nombre: "Fuera de la UE", texto: TEXTO, revisado: true,
        textos: { es: { texto: TEXTO, revisado: true }, en: { texto: EN, revisado: true } } }],
    });
    const user = userEvent.setup();
    const { unmount } = render(<MencionFacturaModal orderId="o-1" orderNumber="BOPRIN-9"
      factura="2-526090" idioma="en" onClose={jest.fn()} />);
    await user.selectOptions(await screen.findByLabelText("Mención legal"), "fuera-ue");
    expect(screen.getByText(`«${EN}»`)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    unmount();

    render(<MencionFacturaModal orderId="o-1" orderNumber="BOPRIN-9"
      factura="2-526090" idioma="nl" onClose={jest.fn()} />);
    await user.selectOptions(await screen.findByLabelText("Mención legal"), "fuera-ue");
    expect(screen.getByText(`«${TEXTO}»`)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "El pedido está en neerlandés y la mención «Fuera de la UE» no lo tiene en ese idioma: va en castellano.");
  });
});
