import { readFileSync } from "fs";
import { join } from "path";

/** Bug 03/10/2026: en el modal plano del ERP (`.modal-dialog.erp-modal`, que
 *  es una columna flex con alto máximo y scroll propio) un hijo con `overflow`
 *  tiene mínimo flex 0 y, con el modal lleno, el navegador lo encogía hasta
 *  0 px: la lista «Contactos del CRM añadidos» del email de presupuesto
 *  desaparecía. jsdom no calcula alturas (se midió en Chromium); este test
 *  vigila que las reglas que lo impiden sigan en la hoja de estilos. */
const css = readFileSync(join(__dirname, "..", "..", "styles.css"), "utf8").replace(/\s+/g, " ");

function ruleBody(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ /g, "\\s*");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  return match ? match[1] : "";
}

describe("molde de modal plano del ERP", () => {
  it("los hijos del diálogo no se encogen (salvo un .modal-body, que scrollea)", () => {
    expect(ruleBody(".modal-dialog.erp-emit-modal > *:not(.modal-body)")).toMatch(/flex-shrink:\s*0/);
    expect(css).toMatch(/\.modal-dialog\.erp-modal > \*:not\(\.modal-body\),/);
  });

  it("las listas de contactos tienen su alto y scroll propios, sin encogerse", () => {
    const lista = ruleBody(".erp-contacts-list");
    expect(lista).toMatch(/flex:\s*none/);
    expect(lista).toMatch(/max-height:\s*220px/);
    expect(lista).toMatch(/overflow-y:\s*auto/);
  });
});

/** Bug 09/10/2026, el mismo de ancho: el pie del detalle de un presupuesto
 *  mete ocho cosas en una fila (tipo, banco, divisa, idioma, la nota del
 *  idioma y cuatro botones). Sin envolver, el contenido (~1000 px) no cabía
 *  en el diálogo (720 px) y, con el pie alineado a la derecha, se salía por
 *  la IZQUIERDA: el selector de tipo quedaba fuera del modal —se veía el
 *  recuadro con la flechita y nada de texto— y del de banco solo el final.
 *  Medido en Chromium: antes 3 controles fuera de la caja; después 0. */
describe("pie del modal: envuelve en vez de aplastar", () => {
  // `.modal-actions` aparece también como parte de selectores más largos
  // (`.erp-emit-modal .modal-actions`): aquí hace falta la regla del pie
  // GENÉRICO, así que el selector tiene que empezar donde acaba la anterior.
  const limpio = css.replace(/\/\*.*?\*\//g, " ");
  function reglaExacta(selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ /g, "\\s*");
    const match = limpio.match(new RegExp(`(?:^|[}])\\s*${escaped}\\s*\\{([^}]*)\\}`));
    return match ? match[1] : "";
  }

  it("la fila de acciones envuelve", () => {
    const cuerpos = limpio.match(/\.modal-actions\s*\{[^}]*\}/g) ?? [];
    expect(cuerpos.some((c) => /flex-wrap:\s*wrap/.test(c))).toBe(true);
    expect(reglaExacta(".modal-actions")).toBeTruthy();
  });

  it("los controles del pie no se encogen", () => {
    expect(ruleBody(".modal-actions > *, .modal-actions .erp-doc-pdf > *"))
      .toMatch(/flex-shrink:\s*0/);
  });

  it("el grupo del PDF sí cede ancho, para poder envolver por dentro", () => {
    const grupo = ruleBody(".modal-actions .erp-doc-pdf");
    expect(grupo).toMatch(/flex:\s*1 1 auto/);
    expect(grupo).toMatch(/min-width:\s*0/);
    expect(grupo).toMatch(/flex-wrap:\s*wrap/);
    // Los botones siguen agrupados a la derecha cuando caben en su línea.
    expect(grupo).toMatch(/justify-content:\s*flex-end/);
  });

  it("ningún select del pie baja de un ancho legible", () => {
    expect(ruleBody(".modal-actions select")).toMatch(/min-width:\s*7ch/);
  });

  it("la nota del idioma es lo primero que cede y puede saltar de línea", () => {
    const nota = ruleBody(".modal-actions .erp-doc-pdf-langsrc");
    expect(nota).toMatch(/flex-shrink:\s*1/);
    expect(nota).toMatch(/min-width:\s*0/);
    expect(nota).toMatch(/white-space:\s*normal/);
  });
});
