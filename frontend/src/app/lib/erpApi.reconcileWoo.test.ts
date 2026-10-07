import { apiFetch } from "./api";
import { describeReconcileProgress, waitForReconcileWoo } from "./erpApi";

jest.mock("./api", () => ({ apiFetch: jest.fn(), apiUpload: jest.fn(),
  apiDownloadBlob: jest.fn(), ApiError: class extends Error {} }));

const mockFetch = apiFetch as jest.Mock;

describe("waitForReconcileWoo", () => {
  it("espera lo que haga falta (más de 150 consultas) y avisa del progreso", async () => {
    let n = 0;
    mockFetch.mockImplementation(() => {
      n += 1;
      return Promise.resolve(n < 200
        ? { status: "pending", progress: { fase: "Pedidos sin estado", hechos: n, total: 200 } }
        : { status: "finished", result: { ok: true } });
    });
    const vistos: unknown[] = [];
    const res = await waitForReconcileWoo("j", { delayMs: 0, onProgress: (p) => vistos.push(p) });
    expect(res.status).toBe("finished");
    expect(n).toBe(200);
    expect(vistos).toHaveLength(199);
  });

  it("para si el trabajo ya no existe", async () => {
    mockFetch.mockResolvedValue({ status: "missing" });
    expect((await waitForReconcileWoo("j", { delayMs: 0 })).status).toBe("missing");
  });

  it("para si se cancela (salir de la pantalla)", async () => {
    mockFetch.mockResolvedValue({ status: "pending" });
    const signal = { cancelled: false };
    const p = waitForReconcileWoo("j", { delayMs: 1, signal });
    signal.cancelled = true;
    expect((await p).status).toBe("pending");
  });

  it("texto del progreso", () => {
    expect(describeReconcileProgress(null)).toBe("En cola…");
    expect(describeReconcileProgress({ fase: "Pedidos sin estado", hechos: 3, total: 86 }))
      .toBe("Pedidos sin estado (3 de 86)");
    expect(describeReconcileProgress({ fase: "Métodos de pago" })).toBe("Métodos de pago");
  });
});
