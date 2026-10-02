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
