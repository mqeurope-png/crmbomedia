import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminUsersPage from "./page";
import { createUser, getCurrentUser, getUsers, type User } from "../../lib/api";

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


describe("AdminUsersPage — alta de usuario (bug 02/10/2026: «reading 'reset'»)", () => {
  beforeEach(() => {
    mockedGetUser.mockResolvedValue(makeUser({ id: "admin-1", role: "admin" }));
    mockedGetUsers.mockResolvedValue([makeUser()]);
  });

  it("crea el usuario: la lista lo muestra, el formulario queda vacío y no hay cuadro de error", async () => {
    const nuevo = makeUser({ id: "u-99", email: "nuevo@bomedia.net", full_name: "Nuevo SAT", role: "sat" });
    (createUser as jest.Mock).mockResolvedValue(nuevo);
    mockedGetUsers.mockResolvedValueOnce([makeUser()]).mockResolvedValue([makeUser(), nuevo]);
    const user = userEvent.setup();
    render(<AdminUsersPage />);
    await screen.findByRole("button", { name: /Resetear contraseña/i });

    await user.type(screen.getByLabelText("Email"), "nuevo@bomedia.net");
    await user.type(screen.getByLabelText("Nombre"), "Nuevo SAT");
    await user.type(screen.getByLabelText("Contraseña"), "Abcdefg1");
    await user.type(screen.getByLabelText("Confirmar contraseña"), "Abcdefg1");
    await user.click(screen.getByRole("button", { name: "Crear" }));

    await screen.findByText("Usuario creado");
    expect(createUser).toHaveBeenCalledWith(expect.objectContaining({
      email: "nuevo@bomedia.net", full_name: "Nuevo SAT", password: "Abcdefg1",
    }));
    // El creado está en la lista; el formulario, vacío; y la página sigue viva
    // (antes caía en «Error de permisos o carga — Cannot read properties of null»).
    expect(await screen.findByText("nuevo@bomedia.net")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("");
    expect(screen.getByLabelText("Nombre")).toHaveValue("");
    expect(screen.getByLabelText("Contraseña")).toHaveValue("");
    expect(screen.queryByText(/Error de permisos o carga/)).not.toBeInTheDocument();
    expect(screen.queryByText(/reading 'reset'/)).not.toBeInTheDocument();
  });
});
