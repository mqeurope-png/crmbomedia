import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SesionCaducada } from "./SesionCaducada";
import { logout, sesionCaducada, sesionRenovadaEnOtraPestana } from "../lib/api";

const push = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: jest.fn() }),
}));
jest.mock("../lib/api", () => ({
  EVENTO_SESION_CADUCADA: "bohub:sesion-caducada",
  sesionCaducada: jest.fn(() => false),
  sesionRenovadaEnOtraPestana: jest.fn(() => false),
  logout: jest.fn(),
}));

const mockLogout = logout as jest.Mock;
const mockCaducada = sesionCaducada as jest.Mock;
const mockRenovada = sesionRenovadaEnOtraPestana as jest.Mock;

beforeEach(() => {
  mockLogout.mockResolvedValue(undefined);
  mockCaducada.mockReturnValue(false);
  mockRenovada.mockReturnValue(false);
});

function caducar() {
  act(() => { window.dispatchEvent(new CustomEvent("bohub:sesion-caducada")); });
}

test("no pinta nada hasta que la sesión caduca", () => {
  const { container } = render(<SesionCaducada />);
  expect(container).toBeEmptyDOMElement();
});

test("al caducar avisa, sin «×» para cerrarlo; «Entrar de nuevo» borra el token y lleva a /login", async () => {
  const user = userEvent.setup();
  render(<SesionCaducada />);
  caducar();
  const aviso = screen.getByRole("alert");
  expect(aviso).toHaveTextContent(
    "Tu sesión ha caducado. Lo que ves puede no estar al día y lo que guardes no se guardará. Vuelve a entrar.");
  expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Entrar de nuevo"]);
  // No se va sola: lo escrito sin guardar sigue en la pantalla.
  expect(push).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Entrar de nuevo" }));
  expect(mockLogout).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(push).toHaveBeenCalledWith("/login"));
});

test("si el 401 llegó antes de montarse, avisa nada más montarse", () => {
  mockCaducada.mockReturnValue(true);
  render(<SesionCaducada />);
  expect(screen.getByRole("alert")).toHaveTextContent("Tu sesión ha caducado.");
});

// Multipestaña: entrar de nuevo en OTRA pestaña deja en esta una franja que
// miente y no se puede cerrar. Al volver a ella (foco / visibilidad) o al
// cabo de un rato, si el token ya no es el que caducó, se quita.
test("si se ha entrado de nuevo en otra pestaña, al volver a esta se quita el aviso", () => {
  render(<SesionCaducada />);
  caducar();
  // Sin token nuevo, volver a la pestaña no cambia nada.
  act(() => { window.dispatchEvent(new Event("focus")); });
  expect(screen.getByRole("alert")).toBeInTheDocument();
  mockRenovada.mockReturnValue(true);
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("con el aviso a la vista se mira cada poco, aunque la pestaña no reciba foco", () => {
  jest.useFakeTimers();
  try {
    render(<SesionCaducada />);
    caducar();
    mockRenovada.mockReturnValue(true);
    act(() => { jest.advanceTimersByTime(5000); });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});
