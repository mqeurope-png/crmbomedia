import { render, screen } from "@testing-library/react";
import { RoleSelect } from "./RoleSelect";

describe("RoleSelect", () => {
  // test_role_selector_offers_erp_roles + test_role_labels_are_human_readable
  it("ofrece los roles de ERP con etiquetas legibles", () => {
    render(<RoleSelect name="role" value="viewer" onChange={() => {}} />);
    expect(screen.getByRole("option", { name: "ERP · Pedidos" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "ERP · Taller (SAT)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Administrador" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Solo lectura" })).toBeInTheDocument();
  });

  // test_role_selector_groups_crm_and_erp
  it("agrupa visualmente CRM y ERP", () => {
    const { container } = render(
      <RoleSelect name="role" value="viewer" onChange={() => {}} />,
    );
    const groups = Array.from(container.querySelectorAll("optgroup")).map(
      (g) => g.getAttribute("label"),
    );
    expect(groups).toEqual(["CRM (comercial)", "ERP (operativo)"]);
    // el rol de ERP cuelga del grupo ERP, no del de CRM.
    const pedidos = screen.getByRole("option", { name: "ERP · Pedidos" });
    expect(pedidos.closest("optgroup")?.getAttribute("label")).toBe("ERP (operativo)");
  });

  it("muestra una nota del ámbito del rol elegido", () => {
    const { rerender } = render(
      <RoleSelect name="role" value="pedidos" onChange={() => {}} />,
    );
    expect(screen.getByText(/solo verá el erp/i)).toBeInTheDocument();
    rerender(<RoleSelect name="role" value="user" onChange={() => {}} />);
    expect(screen.queryByText(/solo verá el erp/i)).not.toBeInTheDocument();
  });

  // test_scope_change_warns_before_save
  it("avisa del cambio de ámbito al editar (CRM → ERP)", () => {
    const { rerender } = render(
      <RoleSelect name="role" value="user" originalRole="user" onChange={() => {}} />,
    );
    // sin cambio de ámbito, sin aviso.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // al elegir un rol de ERP, aparece el aviso de cambio de ámbito.
    rerender(
      <RoleSelect name="role" value="pedidos" originalRole="user" onChange={() => {}} />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/CRM → ERP/);
    expect(alert).toHaveTextContent(/dejará de ver el CRM/i);
  });

  it("no avisa si el ámbito no cambia (CRM → CRM)", () => {
    render(
      <RoleSelect name="role" value="manager" originalRole="user" onChange={() => {}} />,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
