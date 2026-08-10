import { parseState } from "./EmailThreadList";

/** CRM-BANDEJA-FIX-ENVIADOS — "sent" faltaba en parseState: el sidebar
 *  ponía `state=sent` en la URL pero el fetch salía con state=inbox y
 *  «Enviados» mostraba exactamente lo mismo que «Bandeja». */
describe("parseState", () => {
  it("reconoce 'sent' (el param que envía el sidebar de Enviados)", () => {
    expect(parseState("sent")).toBe("sent");
  });

  it("mantiene el resto de estados válidos", () => {
    expect(parseState("inbox")).toBe("inbox");
    expect(parseState("archived")).toBe("archived");
    expect(parseState("trashed")).toBe("trashed");
    expect(parseState("spam")).toBe("spam");
  });

  it("colapsa valores desconocidos o nulos a inbox", () => {
    expect(parseState(null)).toBe("inbox");
    expect(parseState("cualquier-cosa")).toBe("inbox");
  });
});
