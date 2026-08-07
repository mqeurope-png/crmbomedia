import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AliasFilterDropdown } from "./AliasFilterDropdown";
import {
  listUserAliases,
  type UserEmailAlias,
} from "../../lib/userAliasesApi";

jest.mock("../../lib/userAliasesApi", () => ({
  listUserAliases: jest.fn(),
}));
const mockList = listUserAliases as jest.Mock;

function alias(over: Partial<UserEmailAlias> & { id: string }): UserEmailAlias {
  return {
    user_id: "u1",
    alias_email: `${over.id}@bomedia.net`,
    active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

describe("AliasFilterDropdown · CRM-GMAIL Parte H", () => {
  it("con >1 alias activo, muestra el selector y emite onChange", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([
      alias({ id: "norma" }),
      alias({ id: "ventas" }),
    ]);
    const onChange = jest.fn();
    render(
      <AliasFilterDropdown userId="u1" value="" onChange={onChange} />,
    );
    const select = await screen.findByLabelText("Filtrar por alias");
    // «Todos mis alias» + 2 alias = 3 opciones.
    expect(select.querySelectorAll("option")).toHaveLength(3);
    await user.selectOptions(select, "ventas@bomedia.net");
    expect(onChange).toHaveBeenCalledWith("ventas@bomedia.net");
  });

  it("con 1 alias no renderiza nada (no molesta al comercial)", async () => {
    mockList.mockResolvedValue([alias({ id: "norma" })]);
    const { container } = render(
      <AliasFilterDropdown userId="u1" value="" onChange={jest.fn()} />,
    );
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    expect(
      screen.queryByLabelText("Filtrar por alias"),
    ).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("solo cuenta los alias activos", async () => {
    mockList.mockResolvedValue([
      alias({ id: "norma", active: true }),
      alias({ id: "vieja", active: false }),
    ]);
    render(<AliasFilterDropdown userId="u1" value="" onChange={jest.fn()} />);
    // Solo 1 activo → no se muestra el selector.
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    expect(
      screen.queryByLabelText("Filtrar por alias"),
    ).not.toBeInTheDocument();
  });
});
