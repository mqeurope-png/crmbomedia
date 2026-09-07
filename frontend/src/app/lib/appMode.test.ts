import {
  canSwitchMode,
  forcedMode,
  homeForMode,
  isErpOnlyRole,
  pathAllowedInErpMode,
  readStoredMode,
  resolveMode,
  storeMode,
} from "./appMode";

beforeEach(() => {
  window.localStorage.clear();
});

describe("appMode — ámbito por rol", () => {
  it("pedidos/sat están atados a ERP; comercial a CRM; admin elige", () => {
    expect(forcedMode("pedidos")).toBe("erp");
    expect(forcedMode("sat")).toBe("erp");
    expect(forcedMode("user")).toBe("crm");
    expect(forcedMode("manager")).toBe("crm");
    expect(forcedMode("viewer")).toBe("crm");
    expect(forcedMode("admin")).toBeNull();
  });

  it("isErpOnlyRole solo para pedidos/sat", () => {
    expect(isErpOnlyRole("pedidos")).toBe(true);
    expect(isErpOnlyRole("sat")).toBe(true);
    expect(isErpOnlyRole("admin")).toBe(false);
    expect(isErpOnlyRole("user")).toBe(false);
  });

  it("solo el admin puede conmutar de modo", () => {
    expect(canSwitchMode("admin")).toBe(true);
    expect(canSwitchMode("pedidos")).toBe(false);
    expect(canSwitchMode("user")).toBe(false);
  });
});

describe("appMode — resolución y persistencia", () => {
  it("un usuario de ERP resuelve a modo ERP pase lo que pase", () => {
    // Aunque hubiera una preferencia rara guardada, el rol manda.
    storeMode("p-1", "crm");
    expect(resolveMode({ id: "p-1", role: "pedidos" })).toBe("erp");
  });

  it("el admin recuerda su modo entre sesiones (por usuario)", () => {
    const admin = { id: "a-1", role: "admin" as const };
    expect(resolveMode(admin)).toBe("crm"); // por defecto
    storeMode("a-1", "erp");
    expect(readStoredMode("a-1")).toBe("erp");
    expect(resolveMode(admin)).toBe("erp"); // recordado
    // La preferencia es POR usuario: otro admin no la hereda.
    expect(resolveMode({ id: "a-2", role: "admin" })).toBe("crm");
  });

  it("homeForMode", () => {
    expect(homeForMode("erp")).toBe("/erp");
    expect(homeForMode("crm")).toBe("/");
  });
});

describe("appMode — rutas permitidas en modo ERP", () => {
  it("el ERP, la cuenta propia y las anónimas sí; el CRM no", () => {
    expect(pathAllowedInErpMode("/erp")).toBe(true);
    expect(pathAllowedInErpMode("/erp/orders")).toBe(true);
    expect(pathAllowedInErpMode("/account")).toBe(true);
    expect(pathAllowedInErpMode("/account/security")).toBe(true);
    expect(pathAllowedInErpMode("/login")).toBe(true);
    // CRM → fuera.
    expect(pathAllowedInErpMode("/")).toBe(false);
    expect(pathAllowedInErpMode("/contacts")).toBe(false);
    expect(pathAllowedInErpMode("/emails")).toBe(false);
    expect(pathAllowedInErpMode("/marketing/campaigns")).toBe(false);
  });
});
