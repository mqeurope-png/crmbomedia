import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserMenu } from "./UserMenu";
import type { User } from "../lib/api";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
  } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
jest.mock("../lib/api", () => ({
  logout: jest.fn().mockResolvedValue(undefined),
}));

function makeUser(role: User["role"]): User {
  return {
    id: "u-1",
    email: `${role}@bomedia.net`,
    full_name: "Comercial Uno",
    role,
    is_active: true,
  } as User;
}

async function openMenu() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Comercial Uno/i }));
  return user;
}

describe("UserMenu — CRM-PERFIL", () => {
  it("oculta «Cambiar contraseña» para el comercial (rol user)", async () => {
    render(<UserMenu user={makeUser("user")} />);
    await openMenu();

    expect(
      screen.getByRole("menuitem", { name: /Mi cuenta/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /Cambiar contraseña/i }),
    ).not.toBeInTheDocument();
    // La seguridad/2FA sigue siendo autoservicio.
    expect(
      screen.getByRole("menuitem", { name: /Seguridad/i }),
    ).toBeInTheDocument();
  });

  it("muestra «Cambiar contraseña» para el admin", async () => {
    render(<UserMenu user={makeUser("admin")} />);
    await openMenu();

    expect(
      screen.getByRole("menuitem", { name: /Cambiar contraseña/i }),
    ).toBeInTheDocument();
  });
});
