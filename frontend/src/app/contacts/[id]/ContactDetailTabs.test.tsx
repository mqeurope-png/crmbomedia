import { CONTACT_DETAIL_TABS } from "./tabs";

/** CRM-2 — la pestaña «Actividad» se retiró (redundante con «Historial») y
 *  «Oportunidades» pasó a llamarse «Pipelines». Se testea la fuente de verdad
 *  de las pestañas directamente: la página es un client component muy pesado
 *  (muchas fetches de hijos), así que un render completo sería frágil. */
describe("CONTACT_DETAIL_TABS (CRM-2)", () => {
  const labels = CONTACT_DETAIL_TABS.map((t) => t.label);
  const ids = CONTACT_DETAIL_TABS.map((t) => t.id);

  it("no incluye la pestaña «Actividad»", () => {
    expect(labels).not.toContain("Actividad");
    expect(ids).not.toContain("activity");
  });

  it("«Historial» sigue presente (cubre lo que salía en Actividad)", () => {
    expect(labels).toContain("Historial");
  });

  it("la pestaña de pipelines se llama «Pipelines», no «Oportunidades»", () => {
    expect(labels).toContain("Pipelines");
    expect(labels).not.toContain("Oportunidades");
    // El id interno NO cambia — la entidad sigue siendo Opportunity.
    expect(ids).toContain("opportunities");
  });

  it("la primera pestaña (default) es «Resumen»", () => {
    expect(CONTACT_DETAIL_TABS[0].label).toBe("Resumen");
  });
});
