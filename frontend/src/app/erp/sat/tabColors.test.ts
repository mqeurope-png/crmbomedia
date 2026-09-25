import { contrastRatio, SAT_TAB_COLORS } from "./tabColors";

/** El texto de cada pestaña tiene que leerse sobre su color: contraste ≥ 7:1
 *  (WCAG AAA) con el fondo normal, el de la pestaña activa y la pastilla del
 *  contador (blanco al 75 % sobre el fondo activo ≈ más claro aún). */
describe("Cola SAT · colores de las pestañas", () => {
  it.each(Object.entries(SAT_TAB_COLORS))("%s: texto legible sobre su fondo", (_k, c) => {
    expect(contrastRatio(c.fg, c.bg)).toBeGreaterThanOrEqual(7);
    expect(contrastRatio(c.fg, c.bgActive)).toBeGreaterThanOrEqual(7);
  });

  it("cada pestaña tiene un color distinto", () => {
    const fondos = Object.values(SAT_TAB_COLORS).map((c) => c.bg);
    expect(new Set(fondos).size).toBe(fondos.length);
  });

  it("la fórmula de contraste es la de WCAG (negro/blanco = 21)", () => {
    expect(contrastRatio("#000000", "#FFFFFF")).toBeCloseTo(21, 5);
    expect(contrastRatio("#FFFFFF", "#FFFFFF")).toBeCloseTo(1, 5);
  });
});
