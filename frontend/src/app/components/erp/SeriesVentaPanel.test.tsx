import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SeriesVentaPanel } from "./SeriesVentaPanel";

/** Series que cuentan como venta. Figurantes. */

jest.mock("../../lib/erpApi", () => ({
  getHistoricoSeries: jest.fn(),
  marcarHistoricoSerie: jest.fn(),
}));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

test("dice cuánto se queda fuera y se marca por serie", async () => {
  const user = userEvent.setup();
  api().getHistoricoSeries.mockResolvedValue({
    items: [{ serie: "1", nombre: "Empresa Figurante", documentos: 900, lineas: 2400, cuenta: true },
            { serie: "3", nombre: null, documentos: 40, lineas: 95, cuenta: true }],
    fuera: { documentos: 0, lineas: 0 },
  });
  api().marcarHistoricoSerie.mockResolvedValue({
    items: [{ serie: "1", nombre: "Empresa Figurante", documentos: 900, lineas: 2400, cuenta: true },
            { serie: "3", nombre: null, documentos: 40, lineas: 95, cuenta: false }],
    fuera: { documentos: 40, lineas: 95 },
  });
  render(<SeriesVentaPanel puedeEditar />);
  expect(await screen.findByText("Todas las series cuentan.")).toBeInTheDocument();
  expect(screen.getByText("sin nombre en Ajustes ERP")).toBeInTheDocument();
  await user.click(screen.getByLabelText("Cuenta la serie 3"));
  expect(api().marcarHistoricoSerie).toHaveBeenCalledWith("3", false);
  expect(await screen.findByText("40 documentos")).toBeInTheDocument();
  expect(screen.getByText("95 líneas")).toBeInTheDocument();
});
