import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FactusolDocumentDetailModal } from "./FactusolDocumentDetailModal";
import {
  convertFactusolDocument,
  getFactusolConvertStatus,
  getFactusolDocument,
  getFactusolSeries,
} from "../../lib/erpApi";
import { getCurrentUser } from "../../lib/api";

jest.mock("../../lib/erpApi", () => ({
  getFactusolDocument: jest.fn(),
  getFactusolSeries: jest.fn(),
  convertFactusolDocument: jest.fn(),
  getFactusolConvertStatus: jest.fn(),
  ERP_EDIT_ROLES: ["admin", "pedidos"],
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(),
}));

const mockDetail = getFactusolDocument as jest.Mock;
const mockSeries = getFactusolSeries as jest.Mock;
const mockConvert = convertFactusolDocument as jest.Mock;
const mockStatus = getFactusolConvertStatus as jest.Mock;
const mockUser = getCurrentUser as jest.Mock;

function presupuesto(over = {}) {
  return {
    doc_type: "presupuestos", codigo: 27, serie: 5, numero: "5-000027",
    cliente_codigo: "2458", cliente_nombre: "DUPLICODER, S.L.",
    fecha: "2026-08-01", total: 186.34, estado: "1",
    estado_label: "Aceptado", referencia: "Obra X", forma_pago: "002",
    forma_pago_nombre: "Transferencia", lines: [],
    ciclo: { albaranes: [], facturas: [], origen: [], estado: "pendiente" },
    ...over,
  };
}

beforeEach(() => {
  mockDetail.mockReset();
  mockDetail.mockResolvedValue(presupuesto());
  mockSeries.mockReset();
  mockSeries.mockResolvedValue({
    items: [
      { serie: 5, nombre: "Streamtec", is_default: true, is_known: true },
      { serie: 1, nombre: "Bomedia", is_default: false, is_known: true },
    ],
    default: 5,
  });
  mockConvert.mockReset();
  mockStatus.mockReset();
  mockUser.mockReset();
  mockUser.mockResolvedValue({ role: "pedidos" });
});

describe("FactusolDocumentDetailModal (E3-B)", () => {
  it("un presupuesto ofrece «Crear albarán» y «Crear factura» al rol editor", async () => {
    render(
      <FactusolDocumentDetailModal
        docType="presupuestos" serie={5} codigo={27} onClose={() => {}}
      />,
    );
    expect(
      await screen.findByRole("button", { name: "Crear albarán" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Crear factura" }),
    ).toBeInTheDocument();
  });

  it("sin rol de edición no hay botones de crear", async () => {
    mockUser.mockResolvedValue({ role: "user" });
    render(
      <FactusolDocumentDetailModal
        docType="presupuestos" serie={5} codigo={27} onClose={() => {}}
      />,
    );
    await screen.findByText("Aceptado");
    expect(
      screen.queryByRole("button", { name: "Crear albarán" }),
    ).not.toBeInTheDocument();
  });

  it("una factura no ofrece conversiones", async () => {
    mockDetail.mockResolvedValue(presupuesto({
      doc_type: "facturas", numero: "5-260063",
      ciclo: { albaranes: [], facturas: [], origen: [], estado: null },
    }));
    render(
      <FactusolDocumentDetailModal
        docType="facturas" serie={5} codigo={260063} onClose={() => {}}
      />,
    );
    await screen.findByText("Aceptado");
    expect(
      screen.queryByRole("button", { name: /^Crear/ }),
    ).not.toBeInTheDocument();
  });

  it("confirmar llama al endpoint (serie heredada, sin force) y avisa del job", async () => {
    const user = userEvent.setup();
    mockConvert.mockResolvedValue({ job_id: "job-1", status: "queued" });
    mockStatus.mockResolvedValue({ status: "pending" });
    render(
      <FactusolDocumentDetailModal
        docType="presupuestos" serie={5} codigo={27} onClose={() => {}}
      />,
    );
    await user.click(
      await screen.findByRole("button", { name: "Crear albarán" }),
    );
    // Modal de confirmación al estilo E2: total + aviso irreversible.
    const dialog = await screen.findByRole("dialog", { name: "Crear albarán" });
    expect(within(dialog).getByText("186.34 €")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/no es\s+reversible/),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("option", {
        name: /Heredar la del origen \(serie 5\)/,
      }),
    ).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Crear albarán" }),
    );
    await waitFor(() =>
      expect(mockConvert).toHaveBeenCalledWith(
        "presupuestos", 5, 27,
        expect.objectContaining({ target: "albaranes", force: false }),
      ),
    );
    expect(
      await screen.findByText("Creando el documento en FACTUSOL…"),
    ).toBeInTheDocument();
  });

  it("con un albarán existente avisa del duplicado y manda force", async () => {
    const user = userEvent.setup();
    mockDetail.mockResolvedValue(presupuesto({
      ciclo: {
        albaranes: [{ doc_type: "albaranes", serie: 5, codigo: 500004,
                      numero: "5-500004" }],
        facturas: [], origen: [], estado: "con_albaran",
      },
    }));
    mockConvert.mockResolvedValue({ job_id: "job-2", status: "queued" });
    mockStatus.mockResolvedValue({ status: "pending" });
    render(
      <FactusolDocumentDetailModal
        docType="presupuestos" serie={5} codigo={27} onClose={() => {}}
      />,
    );
    await user.click(
      await screen.findByRole("button", { name: "Crear albarán" }),
    );
    expect(
      await screen.findByText(/ya tiene\s+albarán/),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Crear albarán de todos modos" }),
    );
    await waitFor(() =>
      expect(mockConvert).toHaveBeenCalledWith(
        "presupuestos", 5, 27,
        expect.objectContaining({ force: true }),
      ),
    );
  });

  it("un job fallido enseña el error (nada de «Creando…» eterno)", async () => {
    jest.useFakeTimers();
    const user = userEvent.setup({
      advanceTimers: jest.advanceTimersByTime,
    });
    try {
      mockConvert.mockResolvedValue({ job_id: "job-3", status: "queued" });
      mockStatus.mockResolvedValue({
        status: "failed", error: "BDExisteRegistro en F_ALB",
      });
      render(
        <FactusolDocumentDetailModal
          docType="presupuestos" serie={5} codigo={27} onClose={() => {}}
        />,
      );
      await user.click(
        await screen.findByRole("button", { name: "Crear albarán" }),
      );
      const dialog = await screen.findByRole(
        "dialog", { name: "Crear albarán" },
      );
      await user.click(
        within(dialog).getByRole("button", { name: "Crear albarán" }),
      );
      await screen.findByText("Creando el documento en FACTUSOL…");
      jest.advanceTimersByTime(1600);
      expect(
        await screen.findByText("BDExisteRegistro en F_ALB"),
      ).toBeInTheDocument();
      expect(
        screen.queryByText("Creando el documento en FACTUSOL…"),
      ).not.toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  it("al terminar el job enseña el nº creado y refresca (onChanged)", async () => {
    jest.useFakeTimers();
    const user = userEvent.setup({
      advanceTimers: jest.advanceTimersByTime,
    });
    try {
      const onChanged = jest.fn();
      mockConvert.mockResolvedValue({ job_id: "job-4", status: "queued" });
      mockStatus.mockResolvedValue({
        status: "finished",
        result: { target_type: "albaranes", serie: 5, codigo: 500004,
                  numero: "5-500004", lines: 2 },
      });
      render(
        <FactusolDocumentDetailModal
          docType="presupuestos" serie={5} codigo={27}
          onClose={() => {}} onChanged={onChanged}
        />,
      );
      await user.click(
        await screen.findByRole("button", { name: "Crear albarán" }),
      );
      const dialog = await screen.findByRole(
        "dialog", { name: "Crear albarán" },
      );
      await user.click(
        within(dialog).getByRole("button", { name: "Crear albarán" }),
      );
      await screen.findByText("Creando el documento en FACTUSOL…");
      jest.advanceTimersByTime(1600);
      expect(
        await screen.findByRole("button", { name: "5-500004" }),
      ).toBeInTheDocument();
      expect(onChanged).toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });

  it("los enlaces del ciclo navegan dentro del modal", async () => {
    const user = userEvent.setup();
    mockDetail
      .mockResolvedValueOnce(presupuesto({
        ciclo: {
          albaranes: [{ doc_type: "albaranes", serie: 5, codigo: 500004,
                        numero: "5-500004" }],
          facturas: [], origen: [], estado: "con_albaran",
        },
      }))
      .mockResolvedValueOnce(presupuesto({
        doc_type: "albaranes", codigo: 500004, numero: "5-500004",
        estado_label: "Estado 1",
        ciclo: {
          albaranes: [], facturas: [],
          origen: [{ doc_type: "presupuestos", serie: 5, codigo: 27,
                     numero: "5-000027" }],
          estado: "pendiente",
        },
      }));
    render(
      <FactusolDocumentDetailModal
        docType="presupuestos" serie={5} codigo={27} onClose={() => {}}
      />,
    );
    await user.click(await screen.findByRole("button", { name: "5-500004" }));
    await waitFor(() =>
      expect(mockDetail).toHaveBeenLastCalledWith("albaranes", 5, 500004),
    );
    expect(
      await screen.findByText(/Creado desde/),
    ).toBeInTheDocument();
  });
});
