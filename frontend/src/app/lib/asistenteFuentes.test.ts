import { FUENTES_DE_PARTIDA, guardarFuentes, leerFuentes } from "./asistenteFuentes";

beforeEach(() => window.localStorage.clear());

describe("asistenteFuentes — las casillas «Webs» y «Soporte» por usuario", () => {
  it("de partida, las dos marcadas (también sin usuario)", () => {
    expect(FUENTES_DE_PARTIDA).toEqual({ webs: true, soporte: true });
    expect(leerFuentes("u-1")).toEqual({ webs: true, soporte: true });
    expect(leerFuentes(null)).toEqual({ webs: true, soporte: true });
  });

  it("se guardan y se leen por usuario, sin pisarse", () => {
    guardarFuentes("u-1", { webs: false, soporte: true });
    expect(window.localStorage.getItem("bohub.asistente.fuentes.u-1")).toBe('{"webs":false,"soporte":true}');
    expect(leerFuentes("u-1")).toEqual({ webs: false, soporte: true });
    expect(leerFuentes("u-2")).toEqual({ webs: true, soporte: true });
    // Sin usuario no se guarda nada.
    guardarFuentes(null, { webs: false, soporte: false });
    expect(window.localStorage.length).toBe(1);
  });

  it("lo que no es lo que se guardó vuelve a las de partida (campo a campo)", () => {
    const clave = "bohub.asistente.fuentes.u-1";
    window.localStorage.setItem(clave, "no es json");
    expect(leerFuentes("u-1")).toEqual({ webs: true, soporte: true });
    window.localStorage.setItem(clave, "[false, false]");
    expect(leerFuentes("u-1")).toEqual({ webs: true, soporte: true });
    window.localStorage.setItem(clave, '{"webs": "no", "soporte": false}');
    expect(leerFuentes("u-1")).toEqual({ webs: true, soporte: false });
  });

  it("sin almacenamiento local no rompe nada", () => {
    const getItem = jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("bloqueado"); });
    const setItem = jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("bloqueado"); });
    try {
      expect(leerFuentes("u-1")).toEqual({ webs: true, soporte: true });
      expect(() => guardarFuentes("u-1", { webs: false, soporte: false })).not.toThrow();
    } finally {
      getItem.mockRestore();
      setItem.mockRestore();
    }
  });

  it("lo leído es una copia: tocarlo no cambia las de partida", () => {
    const leidas = leerFuentes(null);
    leidas.webs = false;
    expect(FUENTES_DE_PARTIDA.webs).toBe(true);
  });
});
