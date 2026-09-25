import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GeneiSettingsCard } from "./GeneiSettingsCard";
import { getGeneiConfig, saveGeneiConfig, testGeneiConnection } from "../../lib/geneiApi";

jest.mock("../../lib/geneiApi", () => ({
  getGeneiConfig: jest.fn(),
  saveGeneiConfig: jest.fn(),
  testGeneiConnection: jest.fn(),
}));

const mockGet = getGeneiConfig as jest.Mock;
const mockSave = saveGeneiConfig as jest.Mock;
const mockTest = testGeneiConnection as jest.Mock;

const CONFIG = {
  configured: true,
  username: "sat@bomedia.es",
  base_url: "https://apiv2.genei.es",
  default_address_id: "1304422",
  preferred_couriers: { ES: ["GLS", "Correos"] },
  default_package: { weight: 1, height: 20, width: 20, length: 20 },
  origin: { iso_country: "ES", postal_code: "08201", town: "Sabadell" },
  is_warehouse: true,
  webhook_base_url: "",
  webhook_configured: false,
};

beforeEach(() => {
  mockGet.mockReset();
  mockSave.mockReset();
  mockTest.mockReset();
  mockGet.mockResolvedValue(CONFIG);
  mockSave.mockImplementation((body) => Promise.resolve({ ...CONFIG, ...body, configured: true }));
});

it("carga la config, muestra el email y que hay credenciales guardadas", async () => {
  render(<GeneiSettingsCard canEdit />);
  expect(await screen.findByLabelText("Email de Genei")).toHaveValue("sat@bomedia.es");
  expect(screen.getByText(/Credenciales guardadas/)).toBeInTheDocument();
  // Guardar deshabilitado sin cambios.
  expect(screen.getByRole("button", { name: /Guardar cambios/ })).toBeDisabled();
});

it("edita y guarda: la password en blanco no viaja, los couriers salen de las filas", async () => {
  const user = userEvent.setup();
  render(<GeneiSettingsCard canEdit />);
  await screen.findByLabelText("Email de Genei");

  // Añade un país preferido.
  await user.click(screen.getByRole("button", { name: "+ País" }));
  await user.type(screen.getByLabelText("País fila 2"), "FR");
  await user.type(screen.getByLabelText("Couriers fila 2"), "Chronopost, GLS");
  await user.click(screen.getByRole("button", { name: /Guardar cambios/ }));

  await waitFor(() => expect(mockSave).toHaveBeenCalled());
  const body = mockSave.mock.calls[0][0];
  expect(body.password).toBeUndefined();                 // en blanco → no viaja
  expect(body.preferred_couriers).toEqual({
    ES: ["GLS", "Correos"], FR: ["Chronopost", "GLS"],
  });
  expect(await screen.findByText("Guardado.")).toBeInTheDocument();
});

it("la password escrita sí viaja", async () => {
  const user = userEvent.setup();
  render(<GeneiSettingsCard canEdit />);
  await user.type(await screen.findByLabelText("Password de Genei"), "nueva-pw");
  await user.click(screen.getByRole("button", { name: /Guardar cambios/ }));
  await waitFor(() => expect(mockSave).toHaveBeenCalled());
  expect(mockSave.mock.calls[0][0].password).toBe("nueva-pw");
});

it("sin permiso no deja guardar", async () => {
  render(<GeneiSettingsCard canEdit={false} />);
  await screen.findByLabelText("Email de Genei");
  expect(screen.getByRole("button", { name: /Guardar cambios/ })).toBeDisabled();
  expect(screen.getByText(/Solo un administrador puede guardar/)).toBeInTheDocument();
});

describe("conexión con Genei (la sesión se renueva sola)", () => {
  it("con sesión activa lo dice y no pide volver a meter la password", async () => {
    mockGet.mockResolvedValue({
      ...CONFIG,
      auth: {
        state: "ok", token_valid_until: "2026-10-10T08:00:00+00:00",
        last_login_at: "2026-09-25T08:00:00+00:00", last_error: null, last_error_at: null,
      },
    });
    render(<GeneiSettingsCard canEdit />);
    expect(await screen.findByText(/Conectado\. La sesión se renueva sola/)).toBeInTheDocument();
    expect(screen.getByText(/no hace falta volver a meter la password/)).toBeInTheDocument();
  });

  it("si Genei rechazó las credenciales, enseña el aviso claro", async () => {
    mockGet.mockResolvedValue({
      ...CONFIG,
      auth: {
        state: "error", token_valid_until: null, last_login_at: null,
        last_error: "Genei ha rechazado el usuario o la contraseña guardados: revisa las credenciales de Genei en Ajustes → Envíos.",
        last_error_at: "2026-09-25T08:00:00+00:00",
      },
    });
    render(<GeneiSettingsCard canEdit />);
    expect(await screen.findByText(/revisa las credenciales de Genei/)).toBeInTheDocument();
  });

  it("«Probar conexión» usa las credenciales guardadas (sin password) y pinta el resultado", async () => {
    const user = userEvent.setup();
    mockTest.mockResolvedValue({
      ok: true, detail: null,
      auth: {
        state: "ok", token_valid_until: "2026-10-10T08:00:00+00:00",
        last_login_at: "2026-09-25T08:00:00+00:00", last_error: null, last_error_at: null,
      },
    });
    render(<GeneiSettingsCard canEdit />);
    await user.click(await screen.findByRole("button", { name: "Probar conexión" }));
    expect(mockTest).toHaveBeenCalledWith();
    expect(await screen.findByText("Conexión correcta.")).toBeInTheDocument();
    expect(screen.getByText(/Conectado\. La sesión se renueva sola/)).toBeInTheDocument();
  });

  it("«Probar conexión» con credenciales malas enseña el motivo", async () => {
    const user = userEvent.setup();
    mockTest.mockResolvedValue({
      ok: false, detail: "Genei ha rechazado el usuario o la contraseña guardados.",
      auth: { state: "error", token_valid_until: null, last_login_at: null,
              last_error: "Genei ha rechazado el usuario o la contraseña guardados.",
              last_error_at: null },
    });
    render(<GeneiSettingsCard canEdit />);
    await user.click(await screen.findByRole("button", { name: "Probar conexión" }));
    expect(await screen.findAllByText(/rechazado el usuario o la contraseña/)).not.toHaveLength(0);
  });
});
