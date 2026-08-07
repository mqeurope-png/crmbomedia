import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminUsersPage from "./page";
import { getCurrentUser, getUsers, type User } from "../../lib/api";

jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(),
  getUsers: jest.fn(),
  createUser: jest.fn(),
  updateUser: jest.fn(),
  deactivateUser: jest.fn(),
  reactivateUser: jest.fn(),
  adminUpdateUserPassword: jest.fn(),
}));
jest.mock("../../lib/errors", () => ({
  extractErrorMessage: (_err: unknown, fallback: string) => fallback,
}));
// AliasManager hace su propio fetch — lo neutralizamos.
jest.mock("../../components/AliasManager", () => ({
  AliasManager: () => <div data-testid="alias-manager" />,
}));
// Espiamos el modal: nos basta con ver que se abre con el usuario correcto.
jest.mock("../../components/ResetPasswordModal", () => ({
  ResetPasswordModal: ({
    open,
    userEmail,
  }: {
    open: boolean;
    userEmail: string;
  }) =>
    open ? (
      <div data-testid="reset-modal">Reset de {userEmail}</div>
    ) : null,
}));

const mockedGetUser = getCurrentUser as jest.MockedFunction<typeof getCurrentUser>;
const mockedGetUsers = getUsers as jest.MockedFunction<typeof getUsers>;

function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: "u-42",
    email: "comercial@bomedia.net",
    full_name: "Comercial Uno",
    role: "user",
    is_active: true,
    ...overrides,
  } as User;
}

describe("AdminUsersPage — CRM-PERFIL reset de contraseña", () => {
  beforeEach(() => {
    mockedGetUser.mockResolvedValue(makeUser({ id: "admin-1", role: "admin" }));
    mockedGetUsers.mockResolvedValue([makeUser()]);
  });

  it("renderiza un botón «Resetear contraseña» por usuario", async () => {
    render(<AdminUsersPage />);
    expect(
      await screen.findByRole("button", { name: /Resetear contraseña/i }),
    ).toBeInTheDocument();
  });

  it("al pulsar «Resetear contraseña» abre el modal con el usuario elegido", async () => {
    const user = userEvent.setup();
    render(<AdminUsersPage />);

    await user.click(
      await screen.findByRole("button", { name: /Resetear contraseña/i }),
    );

    const modal = await screen.findByTestId("reset-modal");
    expect(modal).toHaveTextContent("comercial@bomedia.net");
  });
});
