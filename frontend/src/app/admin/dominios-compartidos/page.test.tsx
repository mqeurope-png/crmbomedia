import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DominiosCompartidosPage, { type DominioCompartido } from "./page";

jest.mock("../../lib/api", () => ({ apiFetch: jest.fn() }));

const api = jest.requireMock("../../lib/api") as { apiFetch: jest.Mock };

const LISTA: DominioCompartido[] = [
  { dominio: "xtec.cat", tipo: "compartido", nota: "Consejería de educación", por: "migración 0166",
    at: null, contactos: 4083 },
  { dominio: "educa.ejemplo.es", tipo: "compartido", nota: null, por: "admin@ejemplo.test",
    at: null, contactos: 0 },
  { dominio: "gmail.com", tipo: "correo", nota: "Proveedor de correo personal", por: null,
    at: null, contactos: 812 },
];

beforeEach(() => {
  api.apiFetch.mockReset();
  api.apiFetch.mockImplementation((ruta: string, opts?: { method?: string }) =>
    Promise.resolve(!opts?.method ? LISTA : {}));
});

test("enseña los compartidos con sus contactos; los de correo, a petición", async () => {
  render(<DominiosCompartidosPage />);
  expect(await screen.findByText("xtec.cat")).toBeInTheDocument();
  expect(screen.getByText("En la lista: 2 compartidos · 1 proveedores de correo"))
    .toBeInTheDocument();
  const fila = screen.getByText("xtec.cat").closest("tr")!;
  expect(within(fila).getByText("4083")).toBeInTheDocument();
  expect(screen.queryByText("gmail.com")).toBeNull();
  await userEvent.click(screen.getByLabelText(/Ver también los proveedores de correo/));
  expect(screen.getByText("gmail.com")).toBeInTheDocument();
});

test("añadir un dominio lo manda a la API y recarga", async () => {
  render(<DominiosCompartidosPage />);
  await screen.findByText("xtec.cat");
  await userEvent.type(screen.getByLabelText("Dominio"), "educa.otra-region.es");
  await userEvent.type(screen.getByLabelText("Nota (opcional)"), "Consejería figurante");
  await userEvent.click(screen.getByRole("button", { name: "Añadir" }));
  await waitFor(() => expect(api.apiFetch).toHaveBeenCalledWith(
    "/api/admin/dominios-compartidos",
    { method: "POST", body: JSON.stringify({ dominio: "educa.otra-region.es",
                                             tipo: "compartido",
                                             nota: "Consejería figurante" }) }));
  expect(api.apiFetch).toHaveBeenLastCalledWith("/api/admin/dominios-compartidos");
});

test("el error de la API se ve (p. ej. un dominio que ya está)", async () => {
  api.apiFetch.mockImplementation((_ruta: string, opts?: { method?: string }) =>
    opts?.method === "POST" ? Promise.reject(new Error("Ese dominio ya está en la lista."))
      : Promise.resolve(LISTA));
  render(<DominiosCompartidosPage />);
  await screen.findByText("xtec.cat");
  await userEvent.type(screen.getByLabelText("Dominio"), "xtec.cat");
  await userEvent.click(screen.getByRole("button", { name: "Añadir" }));
  expect(await screen.findByText(/ya está en la lista/)).toBeInTheDocument();
});

test("quitar pide confirmación", async () => {
  const confirmar = jest.spyOn(window, "confirm").mockReturnValue(true);
  render(<DominiosCompartidosPage />);
  await screen.findByText("educa.ejemplo.es");
  const fila = screen.getByText("educa.ejemplo.es").closest("tr")!;
  await userEvent.click(within(fila).getByRole("button", { name: "Quitar" }));
  expect(confirmar).toHaveBeenCalled();
  await waitFor(() => expect(api.apiFetch).toHaveBeenCalledWith(
    "/api/admin/dominios-compartidos/educa.ejemplo.es", { method: "DELETE" }));
  confirmar.mockRestore();
});
