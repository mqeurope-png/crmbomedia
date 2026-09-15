import { ageInDays, relativeAge } from "./relativeAge";

/** Antigüedad en palabras (Lote 2 · PR-2, proformas): días naturales sobre
 *  fechas `YYYY-MM-DD`, tramos días → semanas → meses → años, y tolerante a
 *  fechas vacías, inválidas o futuras. `now` se fija en local para que el
 *  resultado no dependa de la zona horaria del runner. */

const NOW = new Date(2026, 8, 15, 10, 30); // 15 sep 2026, 10:30 local

describe("relativeAge", () => {
  it.each([
    ["2026-09-15", "hoy"],
    ["2026-09-14", "ayer"],
    ["2026-09-13", "hace 2 días"],
    ["2026-09-12", "hace 3 días"],
    ["2026-08-05", "hace 41 días"],           // el umbral comercial se lee en días exactos
    ["2026-07-18", "hace 59 días"],
    ["2026-07-17", "hace 9 semanas"],         // 60 días → semanas
    ["2026-07-15", "hace 9 semanas"],
    ["2026-06-18", "hace 13 semanas"],        // 89 días
    ["2026-06-17", "hace 3 meses"],           // 90 días → meses
    ["2026-03-15", "hace 6 meses"],
    ["2025-09-16", "hace 12 meses"],          // 364 días
    ["2025-09-15", "hace 1 año"],             // 365 días → años
    ["2024-09-15", "hace 2 años"],
  ])("%s → «%s»", (iso, expected) => {
    expect(relativeAge(iso, NOW)).toBe(expected);
  });

  it("una fecha futura (reloj desfasado) cuenta como hoy", () => {
    expect(relativeAge("2026-09-20", NOW)).toBe("hoy");
    expect(ageInDays("2026-09-20", NOW)).toBe(0);
  });

  it("acepta ISO con hora (se queda con el día) y un Date", () => {
    expect(relativeAge("2026-09-12T08:00:00Z", NOW)).toBe("hace 3 días");
    expect(relativeAge(new Date(2026, 8, 14, 23, 59), NOW)).toBe("ayer");
    // `now` también como milisegundos (Date.now()).
    expect(relativeAge("2026-09-12", NOW.getTime())).toBe("hace 3 días");
  });

  it("sin fecha válida devuelve null (quien pinta decide qué poner)", () => {
    expect(relativeAge(null, NOW)).toBeNull();
    expect(relativeAge(undefined, NOW)).toBeNull();
    expect(relativeAge("", NOW)).toBeNull();
    expect(relativeAge("ayer", NOW)).toBeNull();
    expect(relativeAge("12/09/2026", NOW)).toBeNull();
    expect(ageInDays("—", NOW)).toBeNull();
  });

  it("ageInDays cuenta días naturales, no horas: la medianoche no cambia el resultado", () => {
    expect(ageInDays("2026-09-14", new Date(2026, 8, 15, 0, 5))).toBe(1);
    expect(ageInDays("2026-09-14", new Date(2026, 8, 15, 23, 55))).toBe(1);
    expect(ageInDays("2026-09-01", NOW)).toBe(14);
  });
});
