import {
  CRM_ROLES,
  ERP_ROLES,
  ROLE_LABELS,
  roleScope,
  roleScopeNote,
  scopeChangeWarning,
} from "./roles";

describe("roles — metadatos", () => {
  it("las etiquetas son legibles (no el valor interno)", () => {
    // test_role_labels_are_human_readable
    expect(ROLE_LABELS.admin).toBe("Administrador");
    expect(ROLE_LABELS.manager).toBe("Responsable");
    expect(ROLE_LABELS.user).toBe("Usuario");
    expect(ROLE_LABELS.viewer).toBe("Solo lectura");
    expect(ROLE_LABELS.pedidos).toBe("ERP · Pedidos");
    expect(ROLE_LABELS.sat).toBe("ERP · Taller (SAT)");
    // ninguna etiqueta es el valor crudo.
    for (const [value, label] of Object.entries(ROLE_LABELS)) {
      expect(label).not.toBe(value);
    }
  });

  it("agrupa CRM y ERP sin solaparse", () => {
    expect(CRM_ROLES).toEqual(["admin", "manager", "user", "viewer"]);
    expect(ERP_ROLES).toEqual(["pedidos", "sat"]);
    expect(CRM_ROLES.some((r) => ERP_ROLES.includes(r))).toBe(false);
  });

  it("roleScope: pedidos/sat son ERP; el resto CRM", () => {
    expect(roleScope("pedidos")).toBe("erp");
    expect(roleScope("sat")).toBe("erp");
    expect(roleScope("admin")).toBe("crm");
    expect(roleScope("viewer")).toBe("crm");
  });

  it("la nota de ámbito distingue ERP de CRM", () => {
    expect(roleScopeNote("pedidos")).toMatch(/solo verá el erp/i);
    expect(roleScopeNote("pedidos")).toMatch(/no tendrá acceso a contactos/i);
    expect(roleScopeNote("user")).toMatch(/crm/i);
  });

  it("scopeChangeWarning solo avisa cuando cambia el ámbito", () => {
    expect(scopeChangeWarning("user", "manager")).toBeNull(); // CRM→CRM
    expect(scopeChangeWarning("pedidos", "sat")).toBeNull(); // ERP→ERP
    expect(scopeChangeWarning("user", "pedidos")).toMatch(/CRM → ERP/);
    expect(scopeChangeWarning("sat", "admin")).toMatch(/ERP → CRM/);
  });
});
