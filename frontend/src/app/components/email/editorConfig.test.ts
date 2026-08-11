import { EDITOR_INVALID_ELEMENTS } from "./editorConfig";

/** CRM-COMPOSITOR-V2.2 — el editor rechaza elementos ejecutables al
 *  pegar (la barrera real es el sanitizador del backend). */
describe("editorConfig", () => {
  it("bloquea script/iframe y demás elementos ejecutables", () => {
    const blocked = EDITOR_INVALID_ELEMENTS.split(",");
    for (const tag of ["script", "iframe", "object", "embed", "form"]) {
      expect(blocked).toContain(tag);
    }
  });
});
