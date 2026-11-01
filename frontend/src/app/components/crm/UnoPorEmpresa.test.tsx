import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UnoPorEmpresa, textoRecuentos, tieneReglasDeEmpresa } from "./UnoPorEmpresa";

const RECUENTOS = { contactos: 1234, empresas: 980, sin_empresa: 4775 };

test("los recuentos dicen empresas, contactos, seleccionados y los que quedan fuera", () => {
  expect(textoRecuentos(RECUENTOS, 980, 25, true)).toBe(
    "980 empresas · 980 contactos (uno por empresa, de 1234 que cumplen) · 25 seleccionados"
    + " · 4775 sin empresa, fuera");
  expect(textoRecuentos(RECUENTOS, 1234, 0, false)).toBe(
    "980 empresas · 1234 contactos · 0 seleccionados · 4775 sin empresa");
  expect(textoRecuentos({ contactos: 1, empresas: 1, sin_empresa: 0 }, 1, 1, false)).toBe(
    "1 empresa · 1 contacto · 1 seleccionado");
});

test("por defecto, uno por empresa solo si las reglas miran la empresa", () => {
  expect(tieneReglasDeEmpresa({ operator: "AND", children: [
    { type: "rule", field: "tags", comparator: "contains_any", value: ["x"] }] })).toBe(false);
  expect(tieneReglasDeEmpresa({ operator: "AND", children: [{ operator: "OR", children: [
    { type: "rule", field: "parque_gama", comparator: "in", value: ["laser"] }] }] })).toBe(true);
  expect(tieneReglasDeEmpresa({})).toBe(false);
});

function pintar(over: Partial<Parameters<typeof UnoPorEmpresa>[0]> = {}) {
  const props = {
    uno: true, onUnoChange: jest.fn(), orden: null, onOrdenChange: jest.fn(),
    recuentos: RECUENTOS, total: 980, seleccionados: 0, ...over,
  };
  render(<UnoPorEmpresa {...props} />);
  return props;
}

test("cambia entre todos y uno por empresa", async () => {
  const p = pintar({ uno: false });
  expect(screen.queryByLabelText("Orden de preferencia")).toBeNull();
  await userEvent.click(screen.getByLabelText("Un solo contacto por empresa"));
  expect(p.onUnoChange).toHaveBeenCalledWith(true);
});

test("el orden de preferencia: quitar el primer criterio, moverlo y volver a ponerlo", async () => {
  const p = pintar();
  // Quitar «Contacto principal».
  await userEvent.click(screen.getByLabelText("Quitar «Contacto principal (marcado en la ficha de empresa)»"));
  expect(p.onOrdenChange).toHaveBeenLastCalledWith(["email_valido", "interaccion", "antiguedad"]);
  // Bajarlo.
  await userEvent.click(screen.getByLabelText("Bajar «Contacto principal (marcado en la ficha de empresa)»"));
  expect(p.onOrdenChange).toHaveBeenLastCalledWith(
    ["email_valido", "principal", "interaccion", "antiguedad"]);
});

test("un criterio quitado sale aparte y se puede volver a usar", async () => {
  const p = pintar({ orden: ["interaccion", "antiguedad"] });
  expect(screen.getByText(/Sin usar/)).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText("Usar «Con email válido»"));
  expect(p.onOrdenChange).toHaveBeenLastCalledWith(["interaccion", "antiguedad", "email_valido"]);
  expect(screen.getByRole("status")).toHaveTextContent("980 empresas");
});
