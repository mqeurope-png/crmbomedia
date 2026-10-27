import { resolveVisibleNav } from "./appNav";
import type { User } from "./api";

function user(role: User["role"]): User {
  return {
    id: "u", email: `${role}@x.com`, full_name: "U", role, is_active: true,
  } as User;
}

const labels = (role: User["role"], mode: "crm" | "erp") =>
  resolveVisibleNav(user(role), mode).map((i) => i.label);

describe("Formularios en el menú", () => {
  it("lo ven admin y manager (los que pueden usar la pantalla), junto a Workflows", () => {
    for (const rol of ["admin", "manager"] as const) {
      const l = labels(rol, "crm");
      expect(l).toContain("Formularios");
      expect(l.indexOf("Formularios")).toBe(l.indexOf("Workflows") + 1);
    }
    expect(resolveVisibleNav(user("admin"), "crm").find((i) => i.label === "Formularios")?.href)
      .toBe("/admin/forms");
  });

  it("un usuario normal no lo ve (la API le daría 403)", () => {
    expect(labels("user", "crm")).not.toContain("Formularios");
  });
});

describe("ERP · Ofertas en el menú", () => {
  it("va justo después de ERP · Proformas y lo ven quienes hacen proformas (admin, comercial)", () => {
    for (const rol of ["admin", "comercial"] as const) {
      const l = labels(rol, "erp");
      expect(l).toContain("ERP · Ofertas");
      expect(l.indexOf("ERP · Ofertas")).toBe(l.indexOf("ERP · Proformas") + 1);
    }
    expect(resolveVisibleNav(user("admin"), "erp").find((i) => i.label === "ERP · Ofertas")?.href)
      .toBe("/erp/ofertas");
  });

  it("el SAT no lo ve (no tiene la capacidad de proformas)", () => {
    expect(labels("sat", "erp")).not.toContain("ERP · Ofertas");
  });
});

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

  it("SAT (solo-ERP) ve su taller y los pedidos, no el CRM", () => {
    const l = labels("sat", "erp");
    expect(l).toContain("ERP · Taller (SAT)");
    expect(l).toContain("ERP · Pedidos");
    expect(l).not.toContain("Contactos");
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

describe("El taller (SAT) — 14/10/2026", () => {
  it("ve solo Cola SAT y Pedidos, en los dos modos", () => {
    expect(labels("sat", "erp").sort()).toEqual(["ERP · Pedidos", "ERP · Taller (SAT)"]);
    // En modo CRM no hay nada más: el CRM es público pero el SAT vive en ERP
    // (appMode lo fuerza); aun así, ninguna entrada de ERP extra.
    expect(labels("sat", "crm").filter((l) => l.startsWith("ERP · ")).sort())
      .toEqual(["ERP · Pedidos", "ERP · Taller (SAT)"]);
  });

  it("dentro de Pedidos, solo la Bandeja (Excepciones es de oficina)", () => {
    const pedidos = resolveVisibleNav(user("sat"), "erp").find((i) => i.label === "ERP · Pedidos");
    expect(pedidos?.children?.map((c) => c.label)).toEqual(["Bandeja"]);
    const oficina = resolveVisibleNav(user("pedidos"), "erp").find((i) => i.label === "ERP · Pedidos");
    expect(oficina?.children?.map((c) => c.label)).toEqual(["Bandeja", "Excepciones"]);
  });

  it("la capacidad sale del rol SAT, sin roles adicionales; y los añadidos suman", () => {
    const solo = { ...user("sat"), erp_roles: [] } as User;
    expect(resolveVisibleNav(solo, "erp").map((i) => i.label)).toContain("ERP · Pedidos");
    const conPedidos = { ...user("sat"), erp_roles: ["pedidos"] } as User;
    expect(resolveVisibleNav(conPedidos, "erp").map((i) => i.label)).toContain("ERP · Documentos");
  });

  it("el viewer (sin ERP) sigue sin ver Pedidos", () => {
    expect(labels("viewer", "crm")).not.toContain("ERP · Pedidos");
  });
});

describe("ERP · Plantillas en el menú", () => {
  it("va justo después de ERP · Ofertas y lo ven quienes hacen proformas", () => {
    for (const rol of ["admin", "comercial"] as const) {
      const l = labels(rol, "erp");
      expect(l.indexOf("ERP · Plantillas")).toBe(l.indexOf("ERP · Ofertas") + 1);
    }
    expect(resolveVisibleNav(user("admin"), "erp").find((i) => i.label === "ERP · Plantillas")?.href)
      .toBe("/erp/plantillas");
    expect(labels("sat", "erp")).not.toContain("ERP · Plantillas");
  });
});
