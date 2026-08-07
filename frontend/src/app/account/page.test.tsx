import { render, screen } from "@testing-library/react";
import AccountPage from "./page";
import { getCurrentUser, type User } from "../lib/api";
import { getMyPreferences } from "../lib/emailTrackingApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
    className,
  }: {
    children: React.ReactNode;
    href: string;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));
jest.mock("../lib/api", () => ({
  getCurrentUser: jest.fn(),
}));
jest.mock("../lib/emailTrackingApi", () => ({
  getMyPreferences: jest.fn(),
  updateMyPreferences: jest.fn(),
}));
jest.mock("../lib/errors", () => ({
  extractErrorMessage: (_err: unknown, fallback: string) => fallback,
}));
// Estas secciones hacen fetch propio al montar — las neutralizamos.
jest.mock("../components/GoogleCalendarSection", () => ({
  GoogleCalendarSection: () => <div data-testid="google-calendar-section" />,
}));
jest.mock("../components/GoogleConnectionBanner", () => ({
  GoogleConnectionBanner: () => null,
}));

const mockedGetUser = getCurrentUser as jest.MockedFunction<typeof getCurrentUser>;
const mockedGetPrefs = getMyPreferences as jest.MockedFunction<
  typeof getMyPreferences
>;

function makeUser(role: User["role"]): User {
  return {
    id: "u-1",
    email: `${role}@bomedia.net`,
    full_name: `${role} Uno`,
    role,
    is_active: true,
  } as User;
}

describe("AccountPage — CRM-PERFIL", () => {
  beforeEach(() => {
    mockedGetPrefs.mockResolvedValue({
      email_include_unsubscribe_default: true,
    } as Awaited<ReturnType<typeof getMyPreferences>>);
  });

  it("comercial: perfil gestionado, solo la firma es editable", async () => {
    mockedGetUser.mockResolvedValue(makeUser("user"));
    render(<AccountPage />);

    // Banner permanente de perfil gestionado por admin.
    expect(
      await screen.findByText(/Este perfil está gestionado por el administrador/i),
    ).toBeInTheDocument();

    // La firma sigue editable.
    expect(
      screen.getByRole("link", { name: /Gestionar firmas/i }),
    ).toBeInTheDocument();

    // No hay botón/enlace para cambiar la contraseña.
    expect(
      screen.queryByRole("link", { name: /Cambiar contraseña/i }),
    ).not.toBeInTheDocument();

    // La preferencia se muestra en solo lectura (Sí/No), sin checkbox.
    expect(
      await screen.findByText(/Incluir opción de baja por defecto:/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();

    // El calendario no se renderiza editable para el comercial.
    expect(
      screen.queryByTestId("google-calendar-section"),
    ).not.toBeInTheDocument();
  });

  it("admin: perfil completamente editable", async () => {
    mockedGetUser.mockResolvedValue(makeUser("admin"));
    render(<AccountPage />);

    expect(
      await screen.findByRole("link", { name: /Cambiar contraseña/i }),
    ).toBeInTheDocument();
    // No aparece el banner de "gestionado por el administrador".
    expect(
      screen.queryByText(/Este perfil está gestionado por el administrador/i),
    ).not.toBeInTheDocument();
    // El checkbox de preferencia es editable.
    expect(await screen.findByRole("checkbox")).toBeInTheDocument();
    // El calendario editable se renderiza.
    expect(screen.getByTestId("google-calendar-section")).toBeInTheDocument();
  });
});
