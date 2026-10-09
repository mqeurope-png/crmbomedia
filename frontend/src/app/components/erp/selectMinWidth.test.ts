import { SELECT_CHROME, selectMinWidth } from "./selectMinWidth";

/** El suelo de ancho de un `<select>` del pie del modal sale de su opción más
 *  larga, que es lo que el usuario tiene que poder leer sin desplegar. */

test("manda la opción más larga, no la primera ni la elegida", () => {
  expect(selectMinWidth(["Presupuesto", "Factura proforma"]))
    .toBe(`calc(16ch + ${SELECT_CHROME})`);
  expect(selectMinWidth(["Albarán (sin importes)", "Albarán valorado"]))
    .toBe(`calc(22ch + ${SELECT_CHROME})`);
});

test("los espacios de los extremos no cuentan", () => {
  expect(selectMinWidth(["  EUR  "])).toBe(`calc(3ch + ${SELECT_CHROME})`);
});

test("sin opciones (o todas vacías) no fuerza ningún ancho", () => {
  expect(selectMinWidth([])).toBeUndefined();
  expect(selectMinWidth(["", "   "])).toBeUndefined();
});
