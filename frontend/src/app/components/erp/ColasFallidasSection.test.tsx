import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ColasFallidasSection } from "./ColasFallidasSection";

/** ERP · Cuadre → «Colas». Hasta ahora la única salida para 149.603 trabajos
 *  fallidos era entrar al contenedor. Lo que se comprueba: que la memoria se
 *  ve, que vaciar exige vista previa Y confirmación, y que quien no es admin
 *  no tiene los botones. */

jest.mock("../../lib/erpApi", () => ({
  getColasResumen: jest.fn(),
  listColaFallidos: jest.fn(),
  reencolarCola: jest.fn(),
  vaciarCola: jest.fn(),
}));

import {
  getColasResumen,
  listColaFallidos,
  vaciarCola,
} from "../../lib/erpApi";

const RESUMEN = {
  fallidos_por_cola: { "brevo:push_contact": 146876, "gmail:process_history": 944 },
  fallidos_total: 147820,
  bytes_por_trabajo_medio: 1024,
  bytes_estimados: 147820 * 1024,
  redis_usada_bytes: 500 * 1024 * 1024,
  muestra: 25,
};

beforeEach(() => {
  jest.clearAllMocks();
  (getColasResumen as jest.Mock).mockResolvedValue(RESUMEN);
});

test("enseña cuántos fallidos hay y la memoria que ocupan", async () => {
  render(<ColasFallidasSection puedeOperar />);
  expect(await screen.findByText(/147\.820/)).toBeInTheDocument();
  // 147.820 × 1.024 bytes ≈ 144 MB: la cifra que faltaba para decidir.
  expect(screen.getByText(/144,4 MB/)).toBeInTheDocument();
  expect(screen.getByText("brevo:push_contact")).toBeInTheDocument();
});

test("quien no es admin puede mirar pero no operar", async () => {
  render(<ColasFallidasSection puedeOperar={false} />);
  await screen.findByText("brevo:push_contact");
  expect(screen.queryByRole("button", { name: /Vaciar/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Reencolar/ })).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Ver" }).length).toBe(2);
});

test("vaciar pide vista previa y además confirmación", async () => {
  (vaciarCola as jest.Mock)
    .mockResolvedValueOnce({ cola: "brevo:push_contact", probar: true,
                             en_registro: 146876, descartados: 0, tope: 20000 })
    .mockResolvedValueOnce({ cola: "brevo:push_contact", probar: false,
                             en_registro: 146876, descartados: 20000, quedan: 126876 });
  const confirmar = jest.spyOn(window, "confirm").mockReturnValue(true);
  render(<ColasFallidasSection puedeOperar />);
  await screen.findByText("brevo:push_contact");

  fireEvent.click(screen.getAllByRole("button", { name: "Vaciar…" })[0]);
  // Primero la previa: todavía NO se ha borrado nada.
  await screen.findByText(/No se puede deshacer/);
  expect(vaciarCola).toHaveBeenCalledWith("brevo:push_contact", { probar: true });

  fireEvent.click(screen.getByRole("button", { name: "Vaciar ahora" }));
  await waitFor(() => expect(confirmar).toHaveBeenCalled());
  expect(vaciarCola).toHaveBeenLastCalledWith("brevo:push_contact", { probar: false });
  // Y se dice cuántos se descartaron y cuántos quedan.
  expect(await screen.findByText(/Descartados 20000/)).toBeInTheDocument();
  expect(screen.getByText(/quedan 126876/)).toBeInTheDocument();
  confirmar.mockRestore();
});

test("si se cancela la confirmación no se vacía nada", async () => {
  (vaciarCola as jest.Mock).mockResolvedValueOnce({
    cola: "gmail:process_history", probar: true, en_registro: 944,
    descartados: 0, tope: 20000,
  });
  const confirmar = jest.spyOn(window, "confirm").mockReturnValue(false);
  render(<ColasFallidasSection puedeOperar />);
  await screen.findByText("gmail:process_history");
  fireEvent.click(screen.getAllByRole("button", { name: "Vaciar…" })[1]);
  await screen.findByText(/No se puede deshacer/);
  fireEvent.click(screen.getByRole("button", { name: "Vaciar ahora" }));
  await waitFor(() => expect(confirmar).toHaveBeenCalled());
  expect(vaciarCola).toHaveBeenCalledTimes(1);          // solo la previa
  confirmar.mockRestore();
});

test("«Ver» enseña los argumentos, que es lo que hace falta para repetirlo", async () => {
  (listColaFallidos as jest.Mock).mockResolvedValue({
    cola: "factusol:writes", funcion: null, en_el_registro: 24, mostrados: 1,
    trabajos: [{
      id: "j1", funcion: "app.erp.factusol.jobs.create_quote_job",
      fecha: "2026-10-02T09:14:00+00:00",
      argumentos: "'PRE', 'BOP-1234', 2",
      error: "TypeError: create_quote_job() takes 2 positional arguments but 3 were given",
    }],
  });
  render(<ColasFallidasSection puedeOperar />);
  await screen.findByText("brevo:push_contact");
  fireEvent.click(screen.getAllByRole("button", { name: "Ver" })[0]);
  expect(await screen.findByText("create_quote_job")).toBeInTheDocument();
  expect(screen.getByText("'PRE', 'BOP-1234', 2")).toBeInTheDocument();
});
