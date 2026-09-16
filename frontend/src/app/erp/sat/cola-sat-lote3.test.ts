import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Lote 3 · Cola SAT — guardas sobre `styles.css` como texto: el bloque nuevo
 *  existe y trae (a) márgenes/gutter de las vistas, (b) la rejilla 50/50 de la
 *  vista global y (c) los controles de edición inline, todo con tokens. */

const css = readFileSync(join(__dirname, "..", "..", "styles.css"), "utf8");
const lote3 = css.slice(css.indexOf("ERP · Lote 3 · Cola SAT"));

describe("styles.css · Lote 3 Cola SAT", () => {
  it("el bloque de Lote 3 se añade al final, una sola vez", () => {
    expect(lote3.length).toBeGreaterThan(0);
    const marks = css.match(/ERP · Lote 3 · Cola SAT/g) ?? [];
    expect(marks).toHaveLength(1);
  });

  it("#3b · da gutter a la cola respetando los 16 px de móvil (padding lateral desde 768)", () => {
    expect(lote3).toMatch(/@media \(min-width: 768px\)/);
    expect(lote3).toMatch(/\.sat-main:has\(\.sat-queue-wrap\)\s*\{\s*padding-inline:/);
  });

  it("#3c · la vista global es una rejilla de dos columnas 50/50 que se apila en móvil", () => {
    expect(lote3).toMatch(/\.sat-global-cols\s*\{[^}]*grid-template-columns:\s*1fr 1fr/);
    expect(lote3).toMatch(/@media \(max-width: 767px\)[^}]*\{[\s\S]*\.sat-global-cols\s*\{[^}]*grid-template-columns:\s*1fr/);
  });

  it("#2 · define los controles de edición inline con tokens del sistema", () => {
    expect(lote3).toContain(".sat-tech-edit-btn");
    expect(lote3).toContain(".sat-tech-input");
    expect(lote3).toMatch(/width:\s*var\(--control-h-sm\)/);
  });

  it("no introduce tamaños de fuente en px ni grises antiguos sobre datos", () => {
    expect(lote3.match(/font-size:\s*\d+(\.\d+)?px/g) ?? []).toEqual([]);
    expect(lote3).not.toMatch(/#9aa2ad|#94a3b8|#7a8290/i);
    expect(lote3).not.toMatch(/var\(--faint\)/);
  });
});
