import { Cap, can, canViewWebOrders, effectiveCapabilities } from "./capabilities";
import type { User } from "./api";

function u(role: User["role"], extra: Partial<User> = {}): User {
  return {
    id: "u", email: `${role}@x`, full_name: "U", role, is_active: true, ...extra,
  } as User;
}

describe("capabilities · can()", () => {
  it("el admin puede todo, aunque no traiga el array", () => {
    expect(can(u("admin"), Cap.CONCILIACION)).toBe(true);
    expect(can(u("admin"), Cap.ROLES_ASSIGN)).toBe(true);
  });

  it("prefiere el array `capabilities` de /auth/me cuando viene", () => {
    const user = u("comercial", { capabilities: [Cap.INVOICE_EMIT] });
    expect(can(user, Cap.INVOICE_EMIT)).toBe(true);
    expect(can(user, Cap.SAT_PREPARE)).toBe(false);
  });

  it("deriva de los roles cuando NO viene el array (lista de usuarios)", () => {
    const com = u("comercial");
    expect(can(com, Cap.INVOICE_EMIT)).toBe(true); // trabaja pedidos no web
    expect(can(com, Cap.SAT_SHIPPING)).toBe(true); // sube etiqueta
    expect(can(com, Cap.COBRO_REGISTER)).toBe(false); // NO cobra
    expect(can(com, Cap.ORDERS_VIEW_WEB)).toBe(false); // NO ve web
    expect(canViewWebOrders(com)).toBe(false);
  });

  it("SAT prepara y ve web; no factura ni cobra", () => {
    const sat = u("sat");
    expect(can(sat, Cap.SAT_PREPARE)).toBe(true);
    expect(can(sat, Cap.ORDERS_VIEW_WEB)).toBe(true);
    expect(can(sat, Cap.INVOICE_EMIT)).toBe(false);
    expect(can(sat, Cap.COBRO_REGISTER)).toBe(false);
  });

  it("multi-rol: el permiso es la UNIÓN de los roles (principal + erp_roles)", () => {
    const both = u("comercial", { erp_roles: ["sat"] });
    expect(can(both, Cap.SAT_PREPARE)).toBe(true); // del rol sat
    expect(can(both, Cap.INVOICE_EMIT)).toBe(true); // del rol comercial
    expect(can(both, Cap.COBRO_REGISTER)).toBe(false); // ninguno lo da
    const caps = effectiveCapabilities(both);
    expect(caps).toContain(Cap.SAT_PREPARE);
    expect(caps).toContain(Cap.INVOICE_EMIT);
  });

  it("viewer no tiene ninguna capacidad ERP; usuario nulo tampoco", () => {
    expect(effectiveCapabilities(u("viewer"))).toEqual([]);
    expect(can(null, Cap.ACCESS)).toBe(false);
    expect(can(undefined, Cap.ACCESS)).toBe(false);
  });

  it("un array vacío de /auth/me se respeta (no re-deriva del rol)", () => {
    // p.ej. un rol legacy que el backend dejó sin capacidades ERP.
    const user = u("viewer", { capabilities: [] });
    expect(can(user, Cap.ACCESS)).toBe(false);
  });
});
