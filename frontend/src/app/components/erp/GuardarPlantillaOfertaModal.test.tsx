import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GuardarPlantillaOfertaModal } from "./GuardarPlantillaOfertaModal";
import { createOfertaPlantilla } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({ createOfertaPlantilla: jest.fn() }));

const mockCreate = createOfertaPlantilla as jest.Mock;

const PLANTILLA = {
  id: "pl1", nombre: "Kit braille", descripcion: null, activa: true,
  origen_serie: 5, origen_codpre: 39, origen_numero: "5-000039", num_lineas: 3,
  created_at: null, updated_at: null,
};

beforeEach(() => mockCreate.mockReset());

describe("GuardarPlantillaOfertaModal", () => {
  it("propone la referencia como nombre y crea la plantilla con la serie y el nº del presupuesto", async () => {
    mockCreate.mockResolvedValue(PLANTILLA);
    const onSaved = jest.fn();
    const user = userEvent.setup();
    render(<GuardarPlantillaOfertaModal serie={5} codpre={39} numero="5-000039"
                                        referencia="Placas braille"
                                        onClose={jest.fn()} onSaved={onSaved} />);
    expect(screen.getByRole("dialog", { name: "Guardar el presupuesto 5-000039 como plantilla de oferta" }))
      .toHaveTextContent("No se toca nada en FACTUSOL");
    const nombre = screen.getByRole("textbox", { name: "Nombre de la plantilla" });
    expect(nombre).toHaveValue("Placas braille");
    await user.clear(nombre);
    await user.type(nombre, "  Kit braille ");
    await user.type(screen.getByRole("textbox", { name: "Descripción de la plantilla" }), "Para ferias");
    await user.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({
      nombre: "Kit braille", descripcion: "Para ferias", serie: 5, codpre: 39,
    }));
    expect(onSaved).toHaveBeenCalledWith(PLANTILLA);
  });

  it("sin nombre no se puede guardar; sin descripción se manda null", async () => {
    mockCreate.mockResolvedValue(PLANTILLA);
    const user = userEvent.setup();
    render(<GuardarPlantillaOfertaModal serie={1} codpre={40} numero={null} referencia={null}
                                        onClose={jest.fn()} onSaved={jest.fn()} />);
    // Sin nº visible, el título usa serie-nº.
    expect(screen.getByRole("dialog", { name: "Guardar el presupuesto 1-40 como plantilla de oferta" }))
      .toBeInTheDocument();
    const guardar = screen.getByRole("button", { name: "Guardar plantilla" });
    expect(guardar).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "Nombre de la plantilla" }), "Tinta");
    expect(guardar).toBeEnabled();
    await user.click(guardar);
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({
      nombre: "Tinta", descripcion: null, serie: 1, codpre: 40,
    }));
  });

  it("un error del servidor se enseña y el modal sigue abierto; Cancelar cierra", async () => {
    mockCreate.mockRejectedValue(new Error("El presupuesto no tiene líneas"));
    const onClose = jest.fn();
    const onSaved = jest.fn();
    const user = userEvent.setup();
    render(<GuardarPlantillaOfertaModal serie={5} codpre={39} referencia="Placas"
                                        onClose={onClose} onSaved={onSaved} />);
    await user.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El presupuesto no tiene líneas");
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Guardar plantilla" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
