import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OfertaPlantillaModal } from "./OfertaPlantillaModal";
import {
  createOfertaPlantilla,
  getOfertaPlantilla,
  searchFactusolArticles,
  updateOfertaPlantilla,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  createOfertaPlantilla: jest.fn(),
  updateOfertaPlantilla: jest.fn(),
  getOfertaPlantilla: jest.fn(),
  searchFactusolArticles: jest.fn(),
}));

const mockCreate = createOfertaPlantilla as jest.Mock;
const mockUpdate = updateOfertaPlantilla as jest.Mock;
const mockGet = getOfertaPlantilla as jest.Mock;

const PL = {
  id: "p1", nombre: "Kit UV", descripcion: "Arranque", activa: true,
  origen_serie: 2, origen_codpre: 500, origen_numero: "2-000500", num_lineas: 1,
  created_at: null, updated_at: null,
};
const LINEA = {
  id: "l1", posicion: 1, codart: "A1", sku: "SKU-1", descripcion: "Placa",
  cantidad: 2, precio: 50, descuento_pct: 0, iva_pct: 21, total: 100, text_only: false,
};

beforeEach(() => {
  mockCreate.mockReset();
  mockUpdate.mockReset();
  mockGet.mockReset();
  (searchFactusolArticles as jest.Mock).mockResolvedValue([]);
  mockCreate.mockResolvedValue({ ...PL, id: "p2", nombre: "Nueva", origen_numero: null });
  mockUpdate.mockResolvedValue(PL);
  mockGet.mockResolvedValue({ ...PL, lineas: [LINEA] });
});

describe("OfertaPlantillaModal", () => {
  it("nueva plantilla con líneas a mano → createOfertaPlantilla (nombre obligatorio)", async () => {
    const onSaved = jest.fn();
    const user = userEvent.setup();
    render(<OfertaPlantillaModal onClose={jest.fn()} onSaved={onSaved} />);
    expect(screen.getByRole("button", { name: "Crear plantilla" })).toBeDisabled();
    await user.type(screen.getByLabelText("Nombre de la plantilla"), "Nueva");
    await user.type(screen.getByLabelText("Descripción línea 1"), "Montaje");
    await user.type(screen.getByLabelText("Precio línea 1"), "80");
    await user.click(screen.getByLabelText("Plantilla activa"));                 // la deja desactivada
    await user.click(screen.getByRole("button", { name: "Crear plantilla" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({
      nombre: "Nueva", descripcion: null, activa: false,
      lineas: [{ codart: null, sku: null, descripcion: "Montaje", cantidad: 1, precio: 80, descuento_pct: 0, iva_pct: 21 }],
    }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "p2" })));
  });

  it("editar: carga las líneas con getOfertaPlantilla y guarda con updateOfertaPlantilla", async () => {
    const user = userEvent.setup();
    render(<OfertaPlantillaModal plantilla={PL} onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(screen.getByRole("dialog", { name: "Editar plantilla Kit UV" })).toHaveTextContent("Copiada del presupuesto 2-000500");
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("p1"));
    expect(await screen.findByLabelText("SKU línea 1")).toHaveValue("SKU-1");
    await user.clear(screen.getByLabelText("Nombre de la plantilla"));
    await user.type(screen.getByLabelText("Nombre de la plantilla"), "Kit UV 2026");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("p1", {
      nombre: "Kit UV 2026", descripcion: "Arranque", activa: true,
      lineas: [{ codart: "A1", sku: "SKU-1", descripcion: "Placa", cantidad: 2, precio: 50, descuento_pct: 0, iva_pct: 21 }],
    }));
  });
});
