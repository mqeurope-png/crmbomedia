import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FactusolDocumentosPage from "./page";
import {
  downloadFacturasPdfZip,
  downloadFactusolDocumentPdf,
  getFactusolSeries,
  listFactusolDocuments,
  saveBlob,
} from "../../lib/erpApi";

/** ERP · Documentos FACTUSOL (Fase 5) — explorador con los componentes reales
 *  del ERP: pestañas por tipo (maqueta «docs»), tabla con pastilla país·régimen
 *  y de estado, y por documento la acción que toca (PDF en todos, «Registrar
 *  cobro» F-4-B en facturas pendientes, «Crear/Abrir pedido» en presupuestos /
 *  pedidos). Filtros y orden como en Proformas. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, onClick }: {
    children: React.ReactNode; href: string; onClick?: (e: unknown) => void;
  }) => <a href={href} onClick={onClick}>{children}</a>,
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/erpApi", () => ({
  listFactusolDocuments: jest.fn(),
  getFactusolDocument: jest.fn(),
  getFactusolSeries: jest.fn(),
  downloadFactusolDocumentPdf: jest.fn(),
  downloadFacturasPdfZip: jest.fn(),
  saveBlob: jest.fn(),
  ERP_EDIT_ROLES: ["admin", "pedidos"],
}));
// El detalle y el cobro se prueban en sus propios tests; aquí se mockean para
// aislar la pantalla. `cycleBadge`/`defaultPdfLang` son helpers del módulo.
jest.mock("../../components/erp/FactusolDocumentDetailModal", () => ({
  FactusolDocumentDetailModal: ({ docType }: { docType: string }) => (
    <div>DETALLE {docType}</div>
  ),
  cycleBadge: () => null,
  defaultPdfLang: () => "es",
}));
jest.mock("../../components/erp/RegistrarCobroModal", () => ({
  RegistrarCobroModal: ({ factura, onDone }: {
    factura: { numero: string }; onDone?: (info: unknown) => void;
  }) => (
    <div role="dialog" aria-label="cobro">
      COBRO {factura.numero}
      <button type="button"
              onClick={() => onDone?.({ status: "cobrada", numero: factura.numero })}>
        REGISTRAR
      </button>
    </div>
  ),
}));

const mockList = listFactusolDocuments as jest.Mock;
const mockSeries = getFactusolSeries as jest.Mock;
const mockPdf = downloadFactusolDocumentPdf as jest.Mock;
const mockZip = downloadFacturasPdfZip as jest.Mock;

function factura(over = {}) {
  return {
    doc_type: "facturas", codigo: 260066, serie: 5, numero: "5-260066",
    cliente_codigo: "2458", cliente_nombre: "DUPLICODER",
    fecha: "2026-08-21", total: 186.34, estado: "0",
    estado_label: "Pendiente de cobro", estado_tone: "warn",
    referencia: "BOP-099917", forma_pago: "002", saldo_pendiente: 186.34,
    company: { id: "es", name: "Duplicoder SL", country: "ES", factusol_id: "2458" },
    country_iso2: "ES", regime: "nacional", regime_label: "Nacional (con IVA)",
    regime_source: "empresa", exento: false, order: null, ...over,
  };
}

beforeEach(() => {
  mockList.mockReset();
  mockList.mockResolvedValue({ items: [factura()], total: 1 });
  mockSeries.mockReset();
  mockSeries.mockResolvedValue({
    items: [
      { serie: 5, nombre: "Streamtec", is_default: true, is_known: true },
      { serie: 2, nombre: "MQ Europe", is_default: false, is_known: true },
    ],
    default: 5,
  });
  mockPdf.mockReset();
  mockPdf.mockResolvedValue(new Blob());
  mockZip.mockReset();
  mockZip.mockResolvedValue(new Blob());
  (saveBlob as jest.Mock).mockReset();
});

describe("ERP · Documentos FACTUSOL (Fase 5)", () => {
  it("muestra las 4 pestañas de la maqueta y lista facturas con país·régimen", async () => {
    render(<FactusolDocumentosPage />);
    for (const label of ["Presupuestos", "Pedidos cliente", "Albaranes", "Facturas"]) {
      expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
    }
    expect(await screen.findByText("5-260066")).toBeInTheDocument();
    // Empresa CRM vinculada + pastilla de régimen (país · régimen).
    expect(screen.getByText("Duplicoder SL")).toBeInTheDocument();
    expect(screen.getByText(/ES · nacional/)).toBeInTheDocument();
    // Estado como PASTILLA (badge), no texto pelado.
    const estado = screen.getByText("Pendiente de cobro");
    expect(estado.className).toContain("badge");
    expect(mockList).toHaveBeenCalledWith(
      "facturas", expect.objectContaining({ limit: 100, offset: 0 }),
    );
  });

  it("cada pestaña consulta su tipo", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    await user.click(screen.getByRole("tab", { name: "Albaranes" }));
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith("albaranes", expect.anything()),
    );
  });

  it("una factura pendiente ofrece «Registrar cobro» (F-4-B) y recarga al hacerlo", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    await user.click(screen.getByRole("button", { name: "Registrar cobro" }));
    // Se abre el modal F-4-B con la factura (serie+número), sin pedido.
    const dialog = await screen.findByRole("dialog", { name: "cobro" });
    expect(within(dialog).getByText(/COBRO 5-260066/)).toBeInTheDocument();
    mockList.mockClear();
    await user.click(within(dialog).getByRole("button", { name: "REGISTRAR" }));
    // Tras registrar, la lista se recarga (fresh) y avisa.
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    expect(await screen.findByText(/cobrada en FACTUSOL/)).toBeInTheDocument();
  });

  it("una factura cobrada NO ofrece «Registrar cobro»", async () => {
    mockList.mockResolvedValue({
      items: [factura({ estado: "2", estado_label: "Cobrada", estado_tone: "ok",
                        saldo_pendiente: 0 })],
      total: 1,
    });
    render(<FactusolDocumentosPage />);
    expect(await screen.findByText("Cobrada")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Registrar cobro" }),
    ).not.toBeInTheDocument();
  });

  it("el PDF de una fila descarga por el tipo de la pestaña", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({
      items: [factura({ doc_type: "albaranes", codigo: 500005, numero: "5-500005",
                        estado: "1", estado_label: "Facturado", estado_tone: "ok" })],
      total: 1,
    });
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Albaranes" }));
    await screen.findByText("5-500005");
    await user.click(screen.getByRole("button", { name: "PDF" }));
    await waitFor(() =>
      expect(mockPdf).toHaveBeenCalledWith("albaranes", 5, 500005, "es"),
    );
    expect(saveBlob).toHaveBeenCalled();
  });

  it("un presupuesto ya importado enlaza «Abrir pedido»; sin importar, «Crear pedido»", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({
      items: [
        factura({ doc_type: "presupuestos", codigo: 27, numero: "5-000027",
                  estado: "1", estado_label: "Aceptado", estado_tone: "ok",
                  order: { id: "o27", order_number: "PRO-000027" } }),
        factura({ doc_type: "presupuestos", codigo: 28, numero: "5-000028",
                  estado: "0", estado_label: "Pendiente", estado_tone: "warn",
                  order: null }),
      ],
      total: 2,
    });
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Presupuestos" }));
    await screen.findByText("5-000027");
    // Con pedido → «Abrir pedido» al detalle del pedido.
    const abrir = screen.getByRole("link", { name: /Abrir pedido PRO-000027/ });
    expect(abrir).toHaveAttribute("href", "/erp/orders/o27");
    // Sin pedido → «Crear pedido» reutiliza el alta prefijada por la URL.
    const crear = screen.getByRole("link", { name: "Crear pedido" });
    expect(crear).toHaveAttribute(
      "href", "/erp/orders/new?doc_type=presupuestos&serie=5&codigo=28",
    );
  });

  it("la búsqueda viaja como `q` (número/referencia/cliente), estilo Proformas", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    await user.type(
      screen.getByLabelText("Buscar por número, referencia o cliente"),
      "moviaticos",
    );
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas", expect.objectContaining({ q: "moviaticos" }),
      ),
    );
  });

  it("«Ordenar por» y el sentido viajan al backend", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    // Por defecto: numero desc.
    expect(mockList).toHaveBeenCalledWith(
      "facturas", expect.objectContaining({ sort: "numero", dir: "desc" }),
    );
    await user.selectOptions(screen.getByLabelText("Ordenar por"), "fecha");
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas", expect.objectContaining({ sort: "fecha" }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Orden descendente" }));
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas", expect.objectContaining({ dir: "asc" }),
      ),
    );
  });

  it("el filtro de cobro (solo facturas) viaja como `estado`", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    await user.selectOptions(screen.getByLabelText("Estado de cobro"), "0");
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas", expect.objectContaining({ estado: "0" }),
      ),
    );
  });

  it("la selección múltiple descarga las facturas en un ZIP", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    await user.click(
      screen.getByRole("checkbox", { name: "Seleccionar factura 5-260066" }),
    );
    await user.click(
      await screen.findByRole("button", { name: /Descargar PDF \(ZIP\)/ }),
    );
    await waitFor(() =>
      expect(mockZip).toHaveBeenCalledWith([{ serie: 5, codigo: 260066 }]),
    );
    expect(saveBlob).toHaveBeenCalledWith(expect.anything(), "facturas_pdf.zip");
  });

  it("«Limpiar filtros» resetea y re-consulta sin filtros", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByRole("option", { name: "2 · MQ Europe" });
    await user.selectOptions(screen.getByLabelText("Serie / empresa"), "2");
    await user.click(await screen.findByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => {
      const last = mockList.mock.calls.at(-1);
      expect(last?.[1].serie).toBeUndefined();
    });
  });
});
