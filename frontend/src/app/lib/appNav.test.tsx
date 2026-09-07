import { resolveVisibleNav } from "./appNav";
import type { User } from "./api";

function user(role: User["role"]): User {
  return {
    id: "u", email: `${role}@x.com`, full_name: "U", role, is_active: true,
  } as User;
}

const labels = (role: User["role"], mode: "crm" | "erp") =>
  resolveVisibleNav(user(role), mode).map((i) => i.label);

describe("resolveVisibleNav — menú por ámbito de rol", () => {
  it("un usuario solo-ERP (pedidos) ve SOLO secciones de ERP", () => {
    const l = labels("pedidos", "erp");
    // Nada de CRM.
    for (const crm of ["Contactos", "Emails", "Marketing", "Pipelines",
      "Segmentos", "Tags", "Workflows", "Empresas", "Dashboard"]) {
      expect(l).not.toContain(crm);
    }
    // Sí sus secciones de ERP.
    expect(l).toContain("ERP · Pedidos");
    expect(l).toContain("ERP · Documentos");
    // Todas las visibles son de ámbito ERP.
    expect(l.every((label) => label.startsWith("ERP · "))).toBe(true);
  });

  it("SAT (solo-ERP) ve su taller, no el CRM", () => {
    const l = labels("sat", "erp");
    expect(l).toContain("ERP · Taller (SAT)");
    expect(l).not.toContain("Contactos");
    expect(l).not.toContain("ERP · Pedidos"); // su rol no lo permite
  });

  it("el admin en modo CRM ve CRM y ERP; en modo ERP solo ERP", () => {
    const crm = labels("admin", "crm");
    expect(crm).toContain("Contactos");
    expect(crm).toContain("ERP · Pedidos"); // como hoy: ve el ERP también
    const erp = labels("admin", "erp");
    expect(erp).toContain("ERP · Pedidos");
    expect(erp).not.toContain("Contactos"); // en modo ERP, sin rastro del CRM
  });

  it("un comercial (user) en modo CRM ve el CRM y el ERP permitido", () => {
    const l = labels("user", "crm");
    expect(l).toContain("Contactos");
    expect(l).toContain("ERP · Pedidos");
    // pero no lo que su rol no permite.
    expect(l).not.toContain("Usuarios");
  });
});
