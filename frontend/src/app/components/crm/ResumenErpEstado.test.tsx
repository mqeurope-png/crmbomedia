import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResumenErpEstado } from "./ResumenErpEstado";

jest.mock("../../lib/api", () => ({ apiFetch: jest.fn() }));

const api = jest.requireMock("../../lib/api") as { apiFetch: jest.Mock };

const BIEN = {
  calculado: true, ok_at: "2026-11-01T05:21:00Z", fallo_at: null, fallo: null,
  duracion_ms: 4100, empresas: 1200, ya_cliente_empresas: 900, ya_cliente_contactos: 1234,
  origen_woo_contactos: 56, quitadas_a_mano: 3,
};

beforeEach(() => api.apiFetch.mockReset());

test("enseña de cuándo son los datos y los recuentos, con las quitadas a mano", async () => {
  api.apiFetch.mockResolvedValueOnce(BIEN);
  render(<ResumenErpEstado />);
  const zona = await screen.findByLabelText("Datos de empresa, parque y compras");
  expect(zona).toHaveTextContent("datos calculados el 1/11 a las");
  expect(zona).toHaveTextContent("YA CLIENTE 1234 contactos (900 empresas)");
  expect(zona).toHaveTextContent("ORIGEN WOO 56");
  expect(zona).toHaveTextContent("quitadas a mano 3");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("si el último recálculo falló, lo dice y avisa de que los datos son del anterior", async () => {
  api.apiFetch.mockResolvedValueOnce({
    ...BIEN, fallo_at: "2026-11-02T05:21:00.123456Z", fallo: "OperationalError",
  });
  render(<ResumenErpEstado />);
  const alerta = await screen.findByRole("alert");
  expect(alerta).toHaveTextContent("El último recálculo de empresa, parque y compras falló el 2/11");
  expect(alerta).toHaveTextContent("(OperationalError)");
  expect(alerta).toHaveTextContent("datos del recálculo anterior, el 1/11");
});

test("un fallo ANTERIOR al último recálculo bueno ya no se enseña", async () => {
  api.apiFetch.mockResolvedValueOnce({ ...BIEN, fallo_at: "2026-10-31T05:21:00Z",
                                       fallo: "OperationalError" });
  render(<ResumenErpEstado />);
  await screen.findByLabelText("Datos de empresa, parque y compras");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("sin calcular todavía y con el primero fallado: no hay datos", async () => {
  api.apiFetch.mockResolvedValueOnce({ calculado: false, ok_at: null,
                                       fallo_at: "2026-11-01T05:21:00Z", fallo: "Timeout" });
  render(<ResumenErpEstado />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Aún no hay datos");
  expect(screen.getByText(/aún no se ha calculado/)).toBeInTheDocument();
});

test("«Recalcular ahora» lo programa y lo dice", async () => {
  api.apiFetch.mockResolvedValueOnce(BIEN)
    .mockResolvedValueOnce({ ...BIEN, programado: "programado" });
  render(<ResumenErpEstado />);
  await userEvent.click(await screen.findByRole("button", { name: "Recalcular ahora" }));
  await waitFor(() => expect(api.apiFetch).toHaveBeenLastCalledWith(
    "/api/crm/resumen-erp/recalcular", { method: "POST" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Recálculo en marcha");
});

test("si la cola no responde, lo dice en vez de prometer el recálculo", async () => {
  api.apiFetch.mockResolvedValueOnce(BIEN)
    .mockResolvedValueOnce({ ...BIEN, programado: "sin_cola" });
  render(<ResumenErpEstado />);
  await userEvent.click(await screen.findByRole("button", { name: "Recalcular ahora" }));
  expect(await screen.findByRole("status")).toHaveTextContent("la cola no responde");
});
