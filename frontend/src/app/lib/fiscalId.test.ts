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

  it("no confunde un nombre de empresa con un NIF", () => {
    for (const q of ["", "  ", "Y'A PAS PHOTO", "Bomedia", "EURL", "ES"]) {
      expect(looksLikeFiscalId(q)).toBe(false);
    }
  });

  it("normaliza separadores y mayúsculas sin perder el prefijo", () => {
    expect(normalizeFiscalId("fr 91.523.447-399")).toBe("FR91523447399");
    expect(normalizeFiscalId("b-64.113.590")).toBe("B64113590");
  });
});
