import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Lote 4 · Cola SAT — guardas sobre `styles.css` como texto: el bloque nuevo
 *  existe una sola vez y trae (#1) el modelo de scroll de las vistas (cola
 *  acotada al viewport + contenedor `.sat-scroll` con overflow propio, columnas
 *  de la global independientes) y (#2) la tabla compacta con ancho mínimo
 *  reducido, todo con tokens del sistema. */

const css = readFileSync(join(__dirname, "..", "..", "styles.css"), "utf8");
const lote4 = css.slice(css.indexOf("Lote 4 · Cola SAT"));

describe("styles.css · Lote 4 Cola SAT", () => {
  it("añade el bloque de Lote 4 al final, una sola vez", () => {
    expect(lote4.length).toBeGreaterThan(0);
    expect((css.match(/Lote 4 · Cola SAT/g) ?? [])).toHaveLength(1);
  });

  it("#1 · acota la cola al viewport y da scroll vertical propio a las vistas", () => {
    expect(lote4).toMatch(/\.sat-shell:has\(\.sat-queue-wrap\)\s*\{[^}]*height:\s*100dvh/);
    expect(lote4).toMatch(/\.sat-scroll\s*\{[^}]*overflow-y:\s*auto/);
  });

  it("#1 · en la vista global cada columna scrollea por su cuenta", () => {
    expect(lote4).toMatch(/\.sat-global\s+\.sat-global-col\s*\{[^}]*min-height:\s*0/);
  });

  it("#2 · la lista compacta reduce el ancho mínimo de la tabla del taller", () => {
    expect(lote4).toMatch(/\.sat-table\s*\{\s*min-width:/);
  });

  it("no usa tamaños de fuente en px, grises antiguos ni --faint sobre datos", () => {
    expect(lote4.match(/font-size:\s*\d+(\.\d+)?px/g) ?? []).toEqual([]);
    expect(lote4).not.toMatch(/#9aa2ad|#94a3b8|#7a8290/i);
    expect(lote4).not.toMatch(/var\(--faint\)/);
  });
});
