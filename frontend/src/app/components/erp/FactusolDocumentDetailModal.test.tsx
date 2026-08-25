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

function albaran(over = {}) {
  return {
    doc_type: "albaranes", codigo: 500004, serie: 5, numero: "5-500004",
    cliente_codigo: "2458", cliente_nombre: "DUPLICODER, S.L.",
    fecha: "2026-08-20", total: 186.34, estado: "0",
    estado_label: "Pendiente", referencia: null, forma_pago: null,
    forma_pago_nombre: null, lines: [],
    ciclo: {
      albaranes: [], facturas: [],
      origen: [{ doc_type: "presupuestos", serie: 5, codigo: 27,
                 numero: "5-000027" }],
      estado: "pendiente", estado_label: "Sin facturar",
    },
    ...over,
  };
}

describe("FactusolDocumentDetailModal (E3-B-fix1)", () => {
  it("presupuesto con albarán: sin «Crear factura» primaria y con aviso al albarán", async () => {
    // test_quote_with_albaran_hides_primary_create_invoice
    mockDetail.mockResolvedValue(presupuesto({
      ciclo: {
        albaranes: [{ doc_type: "albaranes", serie: 5, codigo: 500004,
                      numero: "5-500004" }],
        facturas: [], origen: [],
        estado: "con_albaran", estado_label: "Con albarán",
      },
    }));
    render(
      <FactusolDocumentDetailModal
        docType="presupuestos" serie={5} codigo={27} onClose={() => {}}
      />,
    );
    // «Crear albarán» queda como acción SECUNDARIA (pasa por confirmación
    // explícita de duplicado); «Crear factura» ni se ofrece.
    const crearAlbaran = await screen.findByRole(
      "button", { name: "Crear albarán" },
    );
    expect(crearAlbaran).toHaveClass("secondary");
    expect(
      screen.queryByRole("button", { name: "Crear factura" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/La factura se genera\s+desde el albarán/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "5-500004" }),
    ).toBeInTheDocument();
  });

  it("albarán con factura: sin «Crear factura» y aviso «Ya facturado»", async () => {
    // test_albaran_with_invoice_hides_create_invoice
    mockDetail.mockResolvedValue(albaran({
      ciclo: {
        albaranes: [],
        facturas: [{ doc_type: "facturas", serie: 5, codigo: 260063,
                     numero: "5-260063" }],
        origen: [{ doc_type: "presupuestos", serie: 5, codigo: 27,
                   numero: "5-000027" }],
        estado: "facturado", estado_label: "Facturado",
      },
    }));
    render(
      <FactusolDocumentDetailModal
        docType="albaranes" serie={5} codigo={500004} onClose={() => {}}
      />,
    );
    expect(await screen.findByText("Facturado")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Crear factura" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Ya facturado en/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "5-260063" }),
    ).toBeInTheDocument();
  });

  it("badge del albarán dice «Sin facturar», nunca «sin albarán»", async () => {
    // test_albaran_badge_says_sin_facturar_not_sin_albaran — incluso sin
    // estado_label del backend, el fallback por tipo etiqueta bien.
    mockDetail.mockResolvedValue(albaran({
      ciclo: {
        albaranes: [], facturas: [], origen: [],
        estado: "pendiente", estado_label: undefined,
      },
    }));
    render(
      <FactusolDocumentDetailModal
        docType="albaranes" serie={5} codigo={500004} onClose={() => {}}
      />,
    );
    expect(await screen.findByText("Sin facturar")).toBeInTheDocument();
    expect(screen.queryByText(/Sin albarán/)).not.toBeInTheDocument();
  });

  it("tras la conversión, badge y botones se repintan sin reabrir", async () => {
    // test_cycle_refreshes_after_conversion
    jest.useFakeTimers();
    const user = userEvent.setup({
      advanceTimers: jest.advanceTimersByTime,
    });
    try {
      const onChanged = jest.fn();
      mockDetail
        .mockResolvedValueOnce(albaran())  // sin facturar
        .mockResolvedValueOnce(albaran({   // recarga tras el job
          ciclo: {
            albaranes: [],
            facturas: [{ doc_type: "facturas", serie: 5, codigo: 260064,
                         numero: "5-260064" }],
            origen: [{ doc_type: "presupuestos", serie: 5, codigo: 27,
                       numero: "5-000027" }],
            estado: "facturado", estado_label: "Facturado",
          },
        }));
      mockConvert.mockResolvedValue({ job_id: "job-9", status: "queued" });
      mockStatus.mockResolvedValue({
        status: "finished",
        result: { target_type: "facturas", serie: 5, codigo: 260064,
                  numero: "5-260064", lines: 1 },
      });
      render(
        <FactusolDocumentDetailModal
          docType="albaranes" serie={5} codigo={500004}
          onClose={() => {}} onChanged={onChanged}
        />,
      );
      expect(await screen.findByText("Sin facturar")).toBeInTheDocument();
      await user.click(
        screen.getByRole("button", { name: "Crear factura" }),
      );
      const dialog = await screen.findByRole(
        "dialog", { name: "Crear factura" },
      );
      await user.click(
        within(dialog).getByRole("button", { name: "Crear factura" }),
      );
      await screen.findByText("Creando el documento en FACTUSOL…");
      jest.advanceTimersByTime(1600);
      // Badge y acciones repintados AL MOMENTO, sin cerrar el modal…
      expect(await screen.findByText("Facturado")).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Crear factura" }),
      ).not.toBeInTheDocument();
      expect(screen.getByText(/Ya facturado en/)).toBeInTheDocument();
      // …la recarga saltó el cache del ciclo y el listado se refrescó.
      expect(mockDetail).toHaveBeenLastCalledWith(
        "albaranes", 5, 500004, { fresh: true },
      );
      expect(onChanged).toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });

  it("presupuesto facturado sin albarán: acciones en secundario con aviso", async () => {
    mockDetail.mockResolvedValue(presupuesto({
      ciclo: {
        albaranes: [],
        facturas: [{ doc_type: "facturas", serie: 5, codigo: 260070,
                     numero: "5-260070" }],
        origen: [], estado: "facturado", estado_label: "Facturado",
      },
    }));
    const user = userEvent.setup();
    render(
      <FactusolDocumentDetailModal
        docType="presupuestos" serie={5} codigo={27} onClose={() => {}}
      />,
    );
    const crearAlbaran = await screen.findByRole(
      "button", { name: "Crear albarán" },
    );
    expect(crearAlbaran).toHaveClass("secondary");
    expect(
      screen.getByRole("button", { name: "Crear factura" }),
    ).toHaveClass("secondary");
    expect(screen.getByText(/Ya facturado en/)).toBeInTheDocument();
    // La confirmación del albarán avisa del facturado (contexto explícito).
    await user.click(crearAlbaran);
    expect(
      await screen.findByText(/ya está facturado en 5-260070/),
    ).toBeInTheDocument();
  });
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
      expect(mockDetail).toHaveBeenLastCalledWith(
        "albaranes", 5, 500004, undefined,
      ),
    );
    expect(
      await screen.findByText(/Creado desde/),
    ).toBeInTheDocument();
  });
});
