import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResetPasswordModal } from "./ResetPasswordModal";
import { adminResetUserPassword } from "../lib/api";

jest.mock("../lib/api", () => ({
  adminResetUserPassword: jest.fn(),
}));

jest.mock("../lib/errors", () => ({
  extractErrorMessage: (_err: unknown, fallback: string) => fallback,
}));

const mockedReset = adminResetUserPassword as jest.MockedFunction<
  typeof adminResetUserPassword
>;

function renderModal(overrides: Record<string, unknown> = {}) {
  const props = {
    open: true,
    onClose: jest.fn(),
    userId: "u-1",
    userEmail: "comercial@bomedia.net",
    ...overrides,
  };
  return { props, ...render(<ResetPasswordModal {...props} />) };
}

describe("ResetPasswordModal", () => {
  it("confirma, genera la contraseña y la muestra una sola vez con warning", async () => {
    mockedReset.mockResolvedValue({
      password: "Xk7-Temporal-92",
      message: "no se volverá a mostrar",
    });
    const user = userEvent.setup();
    renderModal();

    // Fase de confirmación: nombra al usuario objetivo.
    expect(screen.getByText("comercial@bomedia.net")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: /Resetear contraseña/i }),
    );

    // Fase "done": la contraseña generada aparece una sola vez.
    const value = await screen.findByTestId("reset-pw-value");
    expect(value).toHaveTextContent("Xk7-Temporal-92");
    expect(mockedReset).toHaveBeenCalledWith("u-1");
    // Warning "no se volverá a mostrar".
    expect(screen.getByText(/no se volverá a mostrar/i)).toBeInTheDocument();
  });

  it("copia la contraseña al portapapeles", async () => {
    mockedReset.mockResolvedValue({ password: "Copiame-123", message: "ok" });
    const user = userEvent.setup();
    // userEvent.setup() instala su propio stub de clipboard; lo sobrescribimos
    // DESPUÉS para poder espiar la llamada real del componente.
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    renderModal();

    await user.click(
      screen.getByRole("button", { name: /Resetear contraseña/i }),
    );
    await screen.findByTestId("reset-pw-value");
    await user.click(screen.getByRole("button", { name: /Copiar/i }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Copiame-123"));
    expect(
      await screen.findByRole("button", { name: /Copiada/i }),
    ).toBeInTheDocument();
  });

  it("al cerrar olvida la contraseña (vuelve a la fase de confirmación)", async () => {
    mockedReset.mockResolvedValue({ password: "Secreta-1", message: "ok" });
    const onClose = jest.fn();
    const user = userEvent.setup();
    renderModal({ onClose });

    await user.click(
      screen.getByRole("button", { name: /Resetear contraseña/i }),
    );
    await screen.findByTestId("reset-pw-value");

    await user.click(screen.getByRole("button", { name: /Hecho/i }));
    expect(onClose).toHaveBeenCalled();
    // La contraseña ya no está en el DOM tras cerrar.
    expect(screen.queryByTestId("reset-pw-value")).not.toBeInTheDocument();
  });
});
