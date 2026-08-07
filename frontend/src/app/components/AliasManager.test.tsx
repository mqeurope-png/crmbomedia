import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AliasManager } from "./AliasManager";
import {
  createUserAlias,
  deleteUserAlias,
  listUserAliases,
  updateUserAlias,
  type UserEmailAlias,
} from "../lib/userAliasesApi";

jest.mock("../lib/userAliasesApi", () => ({
  listUserAliases: jest.fn(),
  createUserAlias: jest.fn(),
  updateUserAlias: jest.fn(),
  deleteUserAlias: jest.fn(),
}));

const mockList = listUserAliases as jest.Mock;
const mockCreate = createUserAlias as jest.Mock;
const mockUpdate = updateUserAlias as jest.Mock;
const mockDelete = deleteUserAlias as jest.Mock;

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

describe("AliasManager · CRM-GMAIL Parte F", () => {
  it("lista los alias del usuario", async () => {
    mockList.mockResolvedValue([alias({ id: "norma" })]);
    render(<AliasManager userId="u1" />);
    expect(await screen.findByText("norma@bomedia.net")).toBeInTheDocument();
    expect(screen.getByText("Activo")).toBeInTheDocument();
  });

  it("añade un alias válido (email) y refresca", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValueOnce([]).mockResolvedValueOnce([
      alias({ id: "ventas" }),
    ]);
    mockCreate.mockResolvedValue(alias({ id: "ventas" }));
    render(<AliasManager userId="u1" />);
    await screen.findByText(/Sin alias configurados/);

    const input = screen.getByLabelText("Nuevo alias de email");
    const addBtn = screen.getByRole("button", { name: "+ Añadir alias" });
    // Email inválido → botón deshabilitado.
    await user.type(input, "no-es-email");
    expect(addBtn).toBeDisabled();
    await user.clear(input);
    await user.type(input, "ventas@bomedia.net");
    expect(addBtn).toBeEnabled();
    await user.click(addBtn);
    expect(mockCreate).toHaveBeenCalledWith("u1", "ventas@bomedia.net");
    expect(await screen.findByText("ventas@bomedia.net")).toBeInTheDocument();
  });

  it("desactiva y borra un alias", async () => {
    const user = userEvent.setup();
    mockList
      .mockResolvedValueOnce([alias({ id: "norma", active: true })])
      .mockResolvedValueOnce([alias({ id: "norma", active: false })])
      .mockResolvedValueOnce([]);
    mockUpdate.mockResolvedValue(alias({ id: "norma", active: false }));
    mockDelete.mockResolvedValue(undefined);
    render(<AliasManager userId="u1" />);
    await screen.findByText("norma@bomedia.net");

    await user.click(screen.getByRole("button", { name: "Desactivar" }));
    expect(mockUpdate).toHaveBeenCalledWith("u1", "norma", false);
    expect(await screen.findByText("Inactivo")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: /Borrar alias norma@bomedia.net/ }),
    );
    expect(mockDelete).toHaveBeenCalledWith("u1", "norma");
    expect(
      await screen.findByText(/Sin alias configurados/),
    ).toBeInTheDocument();
  });

  it("muestra el error 409 del backend (alias ya asignado)", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([]);
    mockCreate.mockRejectedValue(new Error("Ese alias ya está asignado"));
    render(<AliasManager userId="u1" />);
    await screen.findByText(/Sin alias configurados/);
    await user.type(
      screen.getByLabelText("Nuevo alias de email"),
      "dup@bomedia.net",
    );
    await user.click(screen.getByRole("button", { name: "+ Añadir alias" }));
    expect(
      await screen.findByText(/ya está asignado/),
    ).toBeInTheDocument();
  });
});
