import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EquivalenciasPanel } from "./EquivalenciasPanel";

/** Códigos equivalentes. Artículos de catálogo, sin clientes. */

jest.mock("../../lib/erpApi", () => ({
  getEquivalencias: jest.fn(),
  declararEquivalencia: jest.fn(),
  quitarEquivalencia: jest.fn(),
}));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

const PAR = { codart: "34", descripcion: "Beambox (40W)", canonico: "60995",
              canonico_descripcion: "FLUX BEAMBOX 40W", por: "x", at: null };

beforeEach(() => {
  api().getEquivalencias.mockResolvedValue({ items: [PAR] });
  api().declararEquivalencia.mockResolvedValue({ items: [PAR] });
  api().quitarEquivalencia.mockResolvedValue({ items: [] });
});

test("enseña los pares, se declara uno nuevo y se quita", async () => {
  const user = userEvent.setup();
  render(<EquivalenciasPanel puedeEditar />);
  expect(await screen.findByText("FLUX BEAMBOX 40W", { exact: false })).toBeInTheDocument();
  await user.type(screen.getByLabelText("Código"), "4174");
  await user.type(screen.getByLabelText("Es el mismo modelo que"), "61403");
  await user.click(screen.getByRole("button", { name: "Declarar" }));
  expect(api().declararEquivalencia).toHaveBeenCalledWith("61403", "4174");
  await user.click(screen.getByRole("button", { name: "Quitar la equivalencia de 34" }));
  expect(await screen.findByText("No hay ninguna declarada.")).toBeInTheDocument();
});

test("quien no configura solo mira", async () => {
  render(<EquivalenciasPanel puedeEditar={false} />);
  expect(await screen.findByText("60995")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Declarar" })).not.toBeInTheDocument();
});
