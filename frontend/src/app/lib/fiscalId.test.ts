import { looksLikeFiscalId, normalizeFiscalId } from "./fiscalId";

describe("looksLikeFiscalId", () => {
  it("reconoce el NIF/CIF español con y sin letra", () => {
    for (const q of ["B64113590", "12345678Z", "X1234567L", "b-64.113.590"]) {
      expect(looksLikeFiscalId(q)).toBe(true);
    }
  });

  it("reconoce el NIF-IVA con prefijo de país (el bug del alta manual)", () => {
    // `FR91523447399` se buscaba «por nombre» y no encontraba nada.
    for (const q of [
      "FR91523447399", "fr 91.523.447-399", "ESB64113590",
      "BE0812240188", "DE455128445", "NL123456789B01",
    ]) {
      expect(looksLikeFiscalId(q)).toBe(true);
    }
  });

  it("reconoce el NIF-IVA extranjero DESNUDO (sin prefijo de país)", () => {
    // El bug: `FR91523447399` encontraba la empresa y `91523447399` no, porque
    // el desnudo caía al patrón español (7-8 dígitos) y se buscaba «por nombre».
    for (const q of ["91523447399", "0812240188", "455128445", "123456789B01"]) {
      expect(looksLikeFiscalId(q)).toBe(true);
    }
  });

  it("es SIMÉTRICO: con y sin prefijo dan el mismo veredicto", () => {
    const pares: [string, string][] = [
      ["FR91523447399", "91523447399"],
      ["BE0812240188", "0812240188"],
      ["DE455128445", "455128445"],
      ["NL123456789B01", "123456789B01"],
      ["ESB64113590", "B64113590"],
    ];
    for (const [prefijado, desnudo] of pares) {
      expect(looksLikeFiscalId(prefijado)).toBe(true);
      expect(looksLikeFiscalId(desnudo)).toBe(true);
    }
  });

  it("no confunde un nombre de empresa con un NIF", () => {
    for (const q of [
      "", "  ", "Y'A PAS PHOTO", "Bomedia", "EURL", "ES",
      "3M ESPAÑA", "24 HORAS SL", "A1 TELECOM",
    ]) {
      expect(looksLikeFiscalId(q)).toBe(false);
    }
  });

  it("normaliza separadores y mayúsculas sin perder el prefijo", () => {
    expect(normalizeFiscalId("fr 91.523.447-399")).toBe("FR91523447399");
    expect(normalizeFiscalId("b-64.113.590")).toBe("B64113590");
  });
});
