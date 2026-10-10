import {
  formatBackendDateCompact,
  formatBackendDateTimeCompact,
  withYearIfNotCurrent,
} from "./dates";

/** Fechas compactas con el año solo cuando no es el año en curso. En Tareas
 *  había 25 vencidas, varias de hace más de un año, y «30 mar, 09:00» no
 *  decía de qué año era. */
const AHORA = new Date("2026-10-10T12:00:00Z");

describe("withYearIfNotCurrent", () => {
  it("no toca las opciones si la fecha es del año en curso", () => {
    const opciones = { day: "2-digit", month: "short" } as const;
    expect(withYearIfNotCurrent(new Date("2026-03-30T09:00:00Z"), opciones, AHORA))
      .toEqual(opciones);
  });

  it("añade el año cuando la fecha es de otro año (pasado o futuro)", () => {
    const opciones = { day: "2-digit", month: "short" } as const;
    expect(withYearIfNotCurrent(new Date("2025-03-30T09:00:00Z"), opciones, AHORA))
      .toEqual({ ...opciones, year: "numeric" });
    expect(withYearIfNotCurrent(new Date("2027-01-02T09:00:00Z"), opciones, AHORA))
      .toEqual({ ...opciones, year: "numeric" });
  });
});

describe("formatBackendDateTimeCompact", () => {
  it("una fecha del año en curso va sin año", () => {
    const texto = formatBackendDateTimeCompact("2026-03-30T09:00:00Z", AHORA);
    expect(texto).toMatch(/30 mar/);
    expect(texto).not.toContain("2026");
    expect(texto).toMatch(/\d{2}:\d{2}/);
  });

  it("una fecha de otro año lleva el año", () => {
    const texto = formatBackendDateTimeCompact("2025-03-30T09:00:00Z", AHORA);
    expect(texto).toMatch(/30 mar/);
    expect(texto).toContain("2025");
    expect(texto).toMatch(/\d{2}:\d{2}/);
  });

  it("sin fecha, o con una que no se puede leer, pinta una raya", () => {
    expect(formatBackendDateTimeCompact(null, AHORA)).toBe("—");
    expect(formatBackendDateTimeCompact(undefined, AHORA)).toBe("—");
    expect(formatBackendDateTimeCompact("", AHORA)).toBe("—");
    expect(formatBackendDateTimeCompact("ayer por la tarde", AHORA)).toBe("—");
  });

  it("un ISO sin zona se lee como UTC (no como hora local)", () => {
    // Mismo instante con y sin sufijo Z: mismo texto.
    expect(formatBackendDateTimeCompact("2025-03-30T09:00:00", AHORA))
      .toBe(formatBackendDateTimeCompact("2025-03-30T09:00:00Z", AHORA));
  });
});

describe("formatBackendDateCompact", () => {
  it("solo la fecha, con el año si no es el actual", () => {
    expect(formatBackendDateCompact("2026-03-30T09:00:00Z", AHORA)).not.toContain("2026");
    expect(formatBackendDateCompact("2026-03-30T09:00:00Z", AHORA)).not.toMatch(/\d{2}:\d{2}/);
    expect(formatBackendDateCompact("2025-03-30T09:00:00Z", AHORA)).toContain("2025");
    expect(formatBackendDateCompact(null, AHORA)).toBe("—");
  });
});
