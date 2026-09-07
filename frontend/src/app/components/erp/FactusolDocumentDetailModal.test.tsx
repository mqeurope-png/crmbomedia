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
  downloadFactusolDocumentPdf: jest.fn(),
  saveBlob: jest.fn(),
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
  it("presupuesto con albarán: NI botón de crear albarán NI de factura — solo aviso y entrega parcial", async () => {
    // test_quote_with_albaran_has_no_create_albaran_button (E3-B-fix2) +
    // test_quote_with_albaran_hides_primary_create_invoice (E3-B-fix1)
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
    expect(
      await screen.findByText(/La factura se genera\s+desde el albarán/),
    ).toBeInTheDocument();
    // E3-B-fix2: el botón DESAPARECE — duplicar por un clic de más no
    // puede estar disponible como acción normal.
    expect(
      screen.queryByRole("button", { name: "Crear albarán" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Crear factura" }),
    ).not.toBeInTheDocument();
    // La única vía: el enlace discreto de entrega parcial.
    expect(
      screen.getByRole("button", { name: "¿Entrega parcial? Crear otro albarán" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "5-500004" }),
    ).toBeInTheDocument();
  });

  it("la entrega parcial habla de «parcial», nunca «de todos modos», y manda force", async () => {
    // test_partial_delivery_flow_uses_force_and_parcial_wording
    const user = userEvent.setup();
    mockDetail.mockResolvedValue(presupuesto({
      ciclo: {
        albaranes: [{ doc_type: "albaranes", serie: 5, codigo: 500004,
                      numero: "5-500004" }],
        facturas: [], origen: [],
        estado: "con_albaran", estado_label: "Con albarán",
      },
    }));
    mockConvert.mockResolvedValue({ job_id: "job-p1", status: "queued" });
    mockStatus.mockResolvedValue({ status: "pending" });
    render(
      <FactusolDocumentDetailModal
        docType="presupuestos" serie={5} codigo={27} onClose={() => {}}
      />,
    );
    await user.click(await screen.findByRole(
      "button", { name: "¿Entrega parcial? Crear otro albarán" },
    ));
    const dialog = await screen.findByRole(
      "dialog", { name: "Crear albarán parcial" },
    );
    expect(
      within(dialog).getByRole("heading", {
        name: /Crear albarán parcial desde presupuesto 5-000027/,
      }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/varios envíos.*duplicarás el documento/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/de todos modos/)).not.toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Crear albarán parcial" }),
    );
    await waitFor(() =>
      expect(mockConvert).toHaveBeenCalledWith(
        "presupuestos", 5, 27,
        expect.objectContaining({ target: "albaranes", force: true }),
      ),
    );
  });

  it("documento facturado: ni botón ni vía alternativa de duplicado", async () => {
    // test_invoiced_document_offers_no_duplicate_path
    // Presupuesto facturado (directo, sin albarán):
    mockDetail.mockResolvedValue(presupuesto({
      ciclo: {
        albaranes: [],
        facturas: [{ doc_type: "facturas", serie: 5, codigo: 260070,
                     numero: "5-260070" }],
        origen: [], estado: "facturado", estado_label: "Facturado",
      },
    }));
    const { unmount } = render(
      <FactusolDocumentDetailModal
        docType="presupuestos" serie={5} codigo={27} onClose={() => {}}
      />,
    );
    expect(await screen.findByText(/Ya facturado en/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Crear/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Entrega parcial/)).not.toBeInTheDocument();
    unmount();
    // Albarán facturado: igual — sin botón y sin vía.
    mockDetail.mockResolvedValue(albaran({
      ciclo: {
        albaranes: [],
        facturas: [{ doc_type: "facturas", serie: 5, codigo: 260063,
                     numero: "5-260063" }],
        origen: [], estado: "facturado", estado_label: "Facturado",
      },
    }));
    render(
      <FactusolDocumentDetailModal
        docType="albaranes" serie={5} codigo={500004} onClose={() => {}}
      />,
    );
    expect(await screen.findByText(/Ya facturado en/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Crear/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Entrega parcial/)).not.toBeInTheDocument();
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

  it("avisa cuando el origen no quedó marcado como convertido (E3-B-fix3)", async () => {
    // test_conversion_shows_warning_when_origin_not_marked
    jest.useFakeTimers();
    const user = userEvent.setup({
      advanceTimers: jest.advanceTimersByTime,
    });
    try {
      mockDetail.mockResolvedValue(albaran());  // sin facturar
      mockConvert.mockResolvedValue({ job_id: "job-w1", status: "queued" });
      mockStatus.mockResolvedValue({
        status: "finished",
        result: {
          target_type: "facturas", serie: 5, codigo: 260064,
          numero: "5-260064", lines: 1,
          origin_marked: false,
          origin_mark_warning:
            "La factura 5-260064 se creó, pero el albarán 5-500004 no " +
            "quedó marcado como convertido: KO simulado",
        },
      });
      render(
        <FactusolDocumentDetailModal
          docType="albaranes" serie={5} codigo={500004} onClose={() => {}}
        />,
      );
      await user.click(
        await screen.findByRole("button", { name: "Crear factura" }),
      );
      const dialog = await screen.findByRole(
        "dialog", { name: "Crear factura" },
      );
      await user.click(
        within(dialog).getByRole("button", { name: "Crear factura" }),
      );
      await screen.findByText("Creando el documento en FACTUSOL…");
      jest.advanceTimersByTime(1600);
      // La creación se muestra como ÉXITO y el marcado como AVISO aparte.
      expect(
        await screen.findByText(/no\s+quedó marcado como convertido/),
      ).toBeInTheDocument();
      expect(screen.getByText(/Creado factura/)).toBeInTheDocument();
      expect(
        screen.queryByText("La creación falló en FACTUSOL."),
      ).not.toBeInTheDocument();
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

describe("FactusolDocumentDetailModal (E4 — PDF)", () => {
  const { downloadFactusolDocumentPdf, saveBlob } =
    jest.requireMock("../../lib/erpApi");

  it.each(["presupuestos", "pedidos", "albaranes", "facturas"] as const)(
    "el detalle de %s ofrece «Descargar PDF»",
    async (docType) => {
      // test_download_pdf_button_present_per_doc_type
      mockDetail.mockResolvedValue(presupuesto({
        doc_type: docType,
        ciclo: { albaranes: [], facturas: [], origen: [], estado: null },
      }));
      const { unmount } = render(
        <FactusolDocumentDetailModal
          docType={docType} serie={5} codigo={27} onClose={() => {}}
        />,
      );
      expect(
        await screen.findByRole("button", { name: "Descargar PDF" }),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Idioma del PDF")).toBeInTheDocument();
      unmount();
    },
  );

  it("el selector de idioma viaja en la descarga y default por país", async () => {
    // test_language_selector_on_download — cliente belga → EN por defecto;
    // el usuario puede forzar ES y la petición lo lleva.
    const user = userEvent.setup();
    (downloadFactusolDocumentPdf as jest.Mock).mockResolvedValue(
      new Blob(["%PDF"]),
    );
    mockDetail.mockResolvedValue(presupuesto({
      doc_type: "facturas", numero: "2-100001", serie: 2, codigo: 100001,
      cliente_pais: "Belgium",
      ciclo: { albaranes: [], facturas: [], origen: [], estado: null },
    }));
    render(
      <FactusolDocumentDetailModal
        docType="facturas" serie={2} codigo={100001} onClose={() => {}}
      />,
    );
    const selector = await screen.findByLabelText("Idioma del PDF");
    expect(selector).toHaveValue("en");     // deducido del país del cliente
    await user.selectOptions(selector, "es");
    await user.click(screen.getByRole("button", { name: "Descargar PDF" }));
    await waitFor(() =>
      expect(downloadFactusolDocumentPdf).toHaveBeenCalledWith(
        "facturas", 2, 100001, "es",
      ),
    );
    expect(saveBlob).toHaveBeenCalled();
  });
});
