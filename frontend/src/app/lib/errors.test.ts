import { formatFastApiDetail } from "./errors";

describe("formatFastApiDetail", () => {
  it("devuelve el string cuando detail es texto plano", () => {
    expect(formatFastApiDetail("Pedido no encontrado")).toBe("Pedido no encontrado");
  });

  it("une los mensajes de una lista de errores de validación", () => {
    const out = formatFastApiDetail([
      { loc: ["body", "nombre"], msg: "field required" },
    ]);
    expect(out).toContain("field required");
  });

  it("extrae el detail de un objeto {code, detail} (C-3-fix3)", () => {
    // El ERP lanza HTTPException(409, {"code": ..., "detail": "..."}), así que
    // `detail` llega como objeto. Antes caía al fallback «Error de la API (409)»
    // y el usuario no veía el motivo real.
    const out = formatFastApiDetail({
      code: "already_linked",
      detail: "El cliente FACTUSOL 1 ya está vinculado a company «PORTA».",
    });
    expect(out).toBe("El cliente FACTUSOL 1 ya está vinculado a company «PORTA».");
  });

  it("acepta también la forma {message}", () => {
    expect(formatFastApiDetail({ message: "Algo falló" })).toBe("Algo falló");
  });

  it("enseña los bloqueos de un 409 {code: blocked, blockers} (Lote 2 D: «Aprobar» en la bandeja)", () => {
    const out = formatFastApiDetail({
      code: "blocked",
      blockers: [{ code: "open_exceptions", detail: "1 excepción(es) sin resolver" }],
    });
    expect(out).toBe("Bloqueado: 1 excepción(es) sin resolver");
    // Sin motivos legibles, al fallback.
    expect(formatFastApiDetail({ code: "blocked", blockers: [] }, "fallback")).toBe("fallback");
  });

  it("cae al fallback si no hay nada legible", () => {
    expect(formatFastApiDetail({ code: "x" }, "fallback")).toBe("fallback");
    expect(formatFastApiDetail(null, "fallback")).toBe("fallback");
    expect(formatFastApiDetail("   ", "fallback")).toBe("fallback");
  });
});
