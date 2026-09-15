import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Lote 2 · sistema de diseño ERP — guardas sobre `styles.css` como texto:
 *  los tokens transversales existen en `:root`, la escala tipográfica es
 *  cerrada en los bloques ERP (nada por debajo de 12 px) y los moldes
 *  compartidos (rejilla de estado, modal, tabla responsive) están definidos. */

const css = readFileSync(join(__dirname, "styles.css"), "utf8");
const rootStart = css.indexOf(":root {");
const root = css.slice(rootStart, css.indexOf("\n}\n", rootStart));
const erp = css.slice(css.indexOf("/* ===== BoHub ERP (Fase A)"));

describe("styles.css · tokens del sistema de diseño ERP", () => {
  it.each([
    "--focus", "--ink-on-color", "--danger-ink", "--faint", "--line2", "--brand-ink",
    "--st-green-bg", "--st-amber-ink", "--st-red-ink", "--st-neutral-bg",
    "--fs-display", "--fs-title", "--fs-subtitle", "--fs-body", "--fs-label", "--font-mono",
    "--sp-1", "--sp-4", "--sp-12", "--r-control", "--r-panel", "--r-pill",
    "--control-h", "--control-h-touch", "--modal-w-form", "--modal-w-wide",
  ])("define %s en :root", (token) => {
    expect(root).toMatch(new RegExp(`${token}:\\s*[^;]+;`));
  });

  it("el ámbar de estado usa la tinta oscurecida (#8a5c00) y el foco va al 40 %", () => {
    expect(root).toMatch(/--st-amber-ink:\s*#8a5c00;/);
    expect(root).toMatch(/--focus:\s*rgba\(47, 107, 255, 0\.4\);/);
    expect(root).toMatch(/--danger-ink:\s*#b42318;/);
  });

  it("los bloques ERP no bajan de 12 px ni usan tamaños fuera de la escala", () => {
    const raw = erp.match(/font-size:\s*\d+(\.\d+)?px/g) ?? [];
    expect(raw).toEqual([]);
  });

  it("los bloques ERP no pintan datos con --faint ni con los grises claros antiguos", () => {
    expect(erp).not.toMatch(/#9aa2ad|#94a3b8|#7a8290/i);
    // --faint solo en el icono del paso (sin dato), nunca en texto.
    const faintUses = erp.match(/var\(--faint\)/g) ?? [];
    expect(faintUses.length).toBeLessThanOrEqual(1);
  });

  it("define la rejilla de estado, el molde de modal y la tabla responsive", () => {
    for (const sel of [
      ".erp-status-grid {", ".erp-status-cell.is-done {", ".erp-status-cell.is-pending {",
      ".erp-status-cell.is-na {", ".erp-status-cell.is-blocked {",
      ".modal-dialog.wide {", ".modal-dialog.erp-modal,", ".button.tertiary,", ".button.danger {",
      "table.data-table--responsive td::before {", ".erp-primary-sticky {",
    ]) {
      expect(css).toContain(sel);
    }
    expect(css).toMatch(/content:\s*attr\(data-label\)/);
  });
});
