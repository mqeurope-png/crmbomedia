import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FactusolDocumentosPage from "./page";
import { getCurrentUser } from "../../lib/api";
import {
  createOrderFromDocumentType,
  downloadFacturasPdfZip,
  downloadFactusolDocumentPdf,
  getFactusolSeries,
  listFactusolDocuments,
  previewOrderFromDocumentType,
  saveBlob,
} from "../../lib/erpApi";

/** ERP · Documentos FACTUSOL (Fase 5) — explorador con los componentes reales
 *  del ERP: pestañas por tipo (maqueta «docs»), tabla con pastilla país·régimen
 *  y de estado, y por documento la acción que toca (PDF en todos, «Registrar
 *  cobro» F-4-B en facturas pendientes, «Crear/Abrir pedido» en presupuestos /
 *  pedidos). Filtros y orden como en Proformas.
 *
 *  Lote 2 · PR-2 (revisión de diseño, sección 7): chip «Solo sin vincular ·
 *  N», banda ámbar + «Vincular» en las filas sin pedido, mes en curso por
 *  defecto, «Solo lectura · sincronizado hace X» + «Sincronizar ahora», y
 *  filas como tarjetas en móvil (`data-label`). */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, onClick, ...rest }: {
    children: React.ReactNode; href: string; onClick?: (e: unknown) => void;
  }) => <a href={href} onClick={onClick} {...rest}>{children}</a>,
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title, description, actions }: {
    title: string; description?: string; actions?: React.ReactNode;
  }) => <header><h1>{title}</h1>{description ? <p>{description}</p> : null}{actions}</header>,
}));
jest.mock("../../lib/api", () => {
  class ApiError extends Error {
    status: number; code: string | null; detail: unknown;
    constructor(message: string, status: number, detail: unknown) {
      super(message);
      this.status = status;
      this.detail = detail;
      const d = detail as { code?: unknown } | null;
      this.code = d && typeof d.code === "string" ? d.code : null;
    }
  }
  return {
    ApiError,
    getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
  };
});
jest.mock("../../components/erp/VincularEmpresaFactusolModal", () => ({
  VincularEmpresaFactusolModal: ({ codcli, clienteNombre, contexto, onLinked, onClose }: {
    codcli: string; clienteNombre?: string | null; contexto?: string;
    onLinked: (c: { id: string; name: string }) => void; onClose: () => void;
  }) => (
    <div role="dialog" aria-label="Vincular empresa">
      EMPRESA {codcli} {clienteNombre} · {contexto ?? "sin pedido"}
      <button type="button" onClick={() => onLinked({ id: "jap", name: "JAP EDICIONES S.L." })}>
        ENLAZAR
      </button>
      <button type="button" onClick={onClose}>CERRAR</button>
    </div>
  ),
}));
jest.mock("../../components/erp/LinkDocumentOrderModal", () => ({
  LinkDocumentOrderModal: ({ docType, numero, onLinked, onClose }: {
    docType: string; numero: string;
    onLinked: (o: { id: string; order_number: string }) => void; onClose: () => void;
  }) => (
    <div role="dialog" aria-label="vincular">
      VINCULAR {docType} {numero}
      <button type="button" onClick={() => onLinked({ id: "o-bop", order_number: "BOPRIN-99919" })}>
        USAR
      </button>
      <button type="button" onClick={onClose}>CERRAR</button>
    </div>
  ),
}));
jest.mock("../../lib/erpApi", () => ({
  getEjercicios: jest.fn(() => Promise.resolve({ en_curso: "2026", ejercicios: ["2026", "2025"] })),
  listFactusolDocuments: jest.fn(),
  getFactusolDocument: jest.fn(),
  getFactusolSeries: jest.fn(),
  downloadFactusolDocumentPdf: jest.fn(),
  downloadFacturasPdfZip: jest.fn(),
  createOrderFromDocumentType: jest.fn(),
  // «Crear pedido» abre antes el resumen (ConfirmarPedidoModal, el real).
  previewOrderFromDocumentType: jest.fn(),
  saveBlob: jest.fn(),
  ERP_EDIT_ROLES: ["admin", "pedidos"],
}));
// «Guardar como plantilla»: el modal real se prueba en su test; aquí basta
// ver que se abre con el documento de la fila como origen.
jest.mock("../../components/erp/GuardarPlantillaModal", () => ({
  GuardarPlantillaModal: ({ origen, numero, onClose }: {
    origen: { tipo: string; serie: number; codigo: number }; numero?: string | null;
    onClose: () => void;
  }) => (
    <div role="dialog" aria-label="Guardar como plantilla">
      PLANTILLA {origen.tipo} · serie {origen.serie} · código {origen.codigo} · {numero}
      <button type="button" onClick={onClose}>CERRAR</button>
    </div>
  ),
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
// Copia directa (remates · punto 4): el modal real se prueba en su test; aquí
// basta ver que se abre con el presupuesto de la fila.
jest.mock("../../components/erp/CreateQuoteModal", () => ({
  CreateQuoteModal: ({ companyId, duplicateDirect, onCancel }: {
    companyId: string; onCancel: () => void;
    duplicateDirect?: { codpre: string | null; serie?: number | null; numero?: string | null } | null;
  }) => (
    <div role="dialog" aria-label="Duplicar proforma">
      COPIA {duplicateDirect?.numero} · {duplicateDirect?.codpre} · serie {duplicateDirect?.serie} · empresa {companyId}
      <button type="button" onClick={onCancel}>CANCELAR</button>
    </div>
  ),
}));
jest.mock("../../components/erp/quoteJobs", () => ({ pollQuoteJob: jest.fn() }));
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
const mockCreate = createOrderFromDocumentType as jest.Mock;
const mockPreview = previewOrderFromDocumentType as jest.Mock;

/** Lo que devuelve la vista previa del modal «Crear pedido» (solo LEE). */
function previewDe(docType: string, serie: number, codigo: number, over = {}) {
  const label = docType === "albaranes" ? "albarán" : "factura";
  const numero = `${serie}-${String(codigo).padStart(6, "0")}`;
  return {
    doc_type: docType, serie, codigo, numero, fecha: "2026-08-21", total: 121,
    referencia: null, estado: "0", estado_label: "Pendiente", forma_pago: null,
    forma_pago_nombre: null, cliente_codigo: "2458", cliente_nombre: "DUPLICODER",
    company_id: "srv", company_name: "Empresa del servidor", company_linked: true,
    lines: [], order_number: "", external_id: "", already_imported: null,
    resumen: {
      company_id: "srv", company_name: "Empresa del servidor", fecha: "2026-08-21",
      lineas: [{ product_sku: "DTF-60", description: "Impresora DTF", quantity: 1,
                 unit_price: 100, discount_pct: 0, line_total: 100, tax_rate: 21 }],
      total: 121,
      origen: { doc_type: docType, label, serie, codigo, numero, referencia: null },
    },
    ...over,
  };
}

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

const pad = (n: number) => String(n).padStart(2, "0");

/** Mes en curso en la zona del navegador (mismo cálculo que la página). */
function currentMonth() {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth();
  const last = new Date(y, m + 1, 0).getDate();
  return { desde: `${y}-${pad(m + 1)}-01`, hasta: `${y}-${pad(m + 1)}-${pad(last)}` };
}

/** Rango por defecto: 1 de enero del año en curso → hoy. */
/** La casilla «Incluir … externos» (su etiqueta lleva el contador). */
const casillaExternas = () =>
  screen.getByRole("checkbox", { name: /Incluir (facturas externas|albaranes externos)/ })
    .closest("label") as HTMLElement;

beforeEach(() => {
  (getCurrentUser as jest.Mock).mockResolvedValue({ role: "admin" });
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
  mockCreate.mockReset();
  mockCreate.mockResolvedValue({ id: "o-new", order_number: "ALB-5-000091" });
  mockPreview.mockReset();
  mockPreview.mockImplementation((docType: string, serie: number, codigo: number) =>
    Promise.resolve(previewDe(docType, serie, codigo)));
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

  it("«Limpiar filtros» resetea y re-consulta sin fechas (lo acota el ejercicio)", async () => {
    const user = userEvent.setup();
    render(<FactusolDocumentosPage />);
    await screen.findByRole("option", { name: "2 · MQ Europe" });
    // Sin filtros no hay nada que limpiar.
    expect(screen.queryByRole("button", { name: "Limpiar filtros" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Serie / empresa"), "2");
    // Un «hasta» distinto del defecto (no se usa «Mes en curso»: el 31 de
    // enero el mes y el año en curso coinciden y el atajo no existe).
    await user.clear(screen.getByLabelText("Fecha hasta"));
    await user.type(screen.getByLabelText("Fecha hasta"), "2026-03-31");
    await user.click(await screen.findByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => {
      const last = mockList.mock.calls.at(-1);
      expect(last?.[1].serie).toBeUndefined();
      // Sin fechas: el ejercicio en curso es lo que acota.
      expect(last?.[1].fecha_desde).toBeUndefined();
      expect(last?.[1].fecha_hasta).toBeUndefined();
      expect(last?.[1].ejercicio).toBeUndefined();
    });
    expect(screen.queryByRole("button", { name: "Limpiar filtros" })).not.toBeInTheDocument();
  });
});

describe("ERP · Documentos FACTUSOL (Lote 2 · PR-2)", () => {
  it("por defecto solo facturas con pedido; la casilla de externas las incluye, con su contador", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({ items: [factura()], total: 7, unlinked_total: 4 });
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    // Apagada al entrar: solo las que tienen pedido, y dice cuántas quedan fuera.
    expect(screen.getByRole("checkbox", { name: /Incluir facturas externas/ })).not.toBeChecked();
    expect(casillaExternas()).toHaveTextContent(
      "Incluir facturas externas (solo FACTUSOL, sin pedido) · 4 fuera");
    expect(mockList).toHaveBeenCalledWith("facturas", expect.objectContaining({ linked: true }));
    expect(screen.queryByRole("button", { name: /Solo sin vincular/ })).not.toBeInTheDocument();
    // Encendida: entran (sin `linked`), el contador dice cuántas, y aparece el
    // chip para quedarse solo con ellas.
    await user.click(screen.getByRole("checkbox", { name: /Incluir facturas externas/ }));
    await waitFor(() => expect(mockList.mock.calls.at(-1)?.[1].linked).toBeUndefined());
    expect(casillaExternas()).toHaveTextContent("· 4");
    expect(casillaExternas()).not.toHaveTextContent("fuera");
    const chip = screen.getByRole("button", { name: /Solo sin vincular/ });
    expect(chip).toHaveTextContent("Solo sin vincular · 4");
    await user.click(chip);
    await waitFor(() => expect(mockList.mock.calls.at(-1)?.[1].linked).toBe(false));
    // Recargar la lista (otro filtro) no apaga la casilla.
    await user.selectOptions(screen.getByLabelText("Serie / empresa"), "2");
    await waitFor(() => expect(mockList.mock.calls.at(-1)?.[1].serie).toBe(2));
    expect(screen.getByRole("checkbox", { name: /Incluir facturas externas/ })).toBeChecked();
    // Apagarla vuelve a «solo con pedido».
    await user.click(screen.getByRole("checkbox", { name: /Incluir facturas externas/ }));
    await waitFor(() => expect(mockList.mock.calls.at(-1)?.[1].linked).toBe(true));
  });

  it("una factura externa se distingue de un vistazo y, con la casilla apagada, no ofrece cobrarla", async () => {
    mockList.mockResolvedValue({
      items: [factura(), factura({ codigo: 260067, numero: "5-260067",
                                   order: { id: "o-x", order_number: "MAN-7001" } })],
      total: 2, unlinked_total: 1,
    });
    render(<FactusolDocumentosPage />);
    const externa = (await screen.findByText("5-260066")).closest("tr") as HTMLElement;
    const conPedido = screen.getByText("5-260067").closest("tr") as HTMLElement;
    expect(externa.className).toContain("erp-doc-externa");
    expect(within(externa).getByText("Externa · sin pedido")).toBeInTheDocument();
    expect(conPedido.className).not.toContain("erp-doc-externa");
    expect(within(conPedido).queryByText("Externa · sin pedido")).not.toBeInTheDocument();
  });

  it("una factura sin pedido lleva banda ámbar y «Vincular»; al vincular, la fila y el contador cambian", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({
      items: [
        factura(),
        factura({ codigo: 260067, numero: "5-260067",
                  order: { id: "o-x", order_number: "MAN-7001" } }),
      ],
      total: 2, unlinked_total: 1,
    });
    render(<FactusolDocumentosPage />);
    const num = await screen.findByText("5-260066");
    const row = num.closest("tr") as HTMLElement;
    expect(row.className).toContain("is-unlinked");
    // La vinculada NO lleva banda y enseña el nº del pedido como enlace.
    const linkedRow = screen.getByText("5-260067").closest("tr") as HTMLElement;
    expect(linkedRow.className).not.toContain("is-unlinked");
    expect(within(linkedRow).getByRole("link", { name: "Abrir pedido MAN-7001" }))
      .toHaveAttribute("href", "/erp/orders/o-x");
    expect(within(linkedRow).queryByRole("button", { name: /Vincular/ })).not.toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Vincular 5-260066 a un pedido" }));
    const dialog = await screen.findByRole("dialog", { name: "vincular" });
    expect(dialog).toHaveTextContent("VINCULAR facturas 5-260066");
    mockList.mockClear();
    await user.click(within(dialog).getByRole("button", { name: "USAR" }));
    // La fila pasa a tener pedido sin releer FACTUSOL; el contador baja.
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Factura 5-260066 vinculada al pedido BOPRIN-99919.",
    );
    expect(within(row).getByRole("link", { name: "Abrir pedido BOPRIN-99919" }))
      .toHaveAttribute("href", "/erp/orders/o-bop");
    expect(row.className).not.toContain("is-unlinked");
    expect(casillaExternas()).toHaveTextContent("· 0 fuera");
    expect(mockList).not.toHaveBeenCalled();
  });

  it("en albaranes se ofrece «Crear pedido» Y «Vincular»; en presupuestos solo «Crear pedido» (enlace)", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({
      items: [factura({ doc_type: "albaranes", codigo: 91, numero: "5-000091",
                        estado: "0", estado_label: "Pendiente", estado_tone: "muted" })],
      total: 1, unlinked_total: 1,
    });
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Albaranes" }));
    // Lote 7 · P4 — albaranes / facturas: alta directa desde el documento
    // (botón) además de «Vincular».
    expect(await screen.findByRole("button", { name: "Vincular 5-000091 a un pedido" }))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Crear pedido desde 5-000091" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Crear pedido" })).not.toBeInTheDocument();
    mockList.mockResolvedValue({
      items: [factura({ doc_type: "presupuestos", codigo: 28, numero: "5-000028",
                        estado: "0", estado_label: "Pendiente", estado_tone: "warn" })],
      total: 1, unlinked_total: 1,
    });
    await user.click(screen.getByRole("tab", { name: "Presupuestos" }));
    // Presupuestos: sigue siendo el ENLACE al alta prefijada, sin «Vincular».
    expect(await screen.findByRole("link", { name: "Crear pedido" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /a un pedido$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Crear pedido/ })).not.toBeInTheDocument();
  });

  /** Un albarán con empresa del CRM cruzada en la fila (Acme SL). */
  const albaranAcme = () => factura({
    doc_type: "albaranes", codigo: 91, numero: "5-000091",
    estado: "0", estado_label: "Pendiente", estado_tone: "muted",
    company: { id: "acme", name: "Acme SL", country: "ES", factusol_id: "55555" },
  });

  it("«Crear pedido» en un albarán abre el resumen y NO crea nada todavía", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({ items: [albaranAcme()], total: 1, unlinked_total: 1 });
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Albaranes" }));
    await screen.findByText("5-000091");
    await user.click(screen.getByRole("button", { name: "Crear pedido desde 5-000091" }));
    const dialog = await screen.findByRole("dialog", { name: "Crear pedido desde el albarán 5-000091" });
    // El resumen sale de la vista previa (solo lee) con la empresa del cruce.
    const resumen = await within(dialog).findByRole("region", { name: "Resumen del pedido" });
    expect(mockPreview).toHaveBeenCalledWith("albaranes", 5, 91);
    expect(within(resumen).getByText("Acme SL")).toBeInTheDocument();
    expect(within(resumen).getByText("Impresora DTF", { exact: false })).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: /Abrir pedido/ })).not.toBeInTheDocument();
  });

  it("confirmar en el modal crea UN pedido con la empresa del cruce y la fila lo refleja", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({ items: [albaranAcme()], total: 1, unlinked_total: 1 });
    mockCreate.mockResolvedValue({ id: "o-alb91", order_number: "ALB-5-000091" });
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Albaranes" }));
    await screen.findByText("5-000091");
    mockList.mockClear();
    await user.click(screen.getByRole("button", { name: "Crear pedido desde 5-000091" }));
    const dialog = await screen.findByRole("dialog", { name: "Crear pedido desde el albarán 5-000091" });
    await within(dialog).findByRole("region", { name: "Resumen del pedido" });
    await user.click(within(dialog).getByRole("button", { name: "Crear pedido" }));
    // Alta desde el documento con serie/código y la empresa del cruce, una vez.
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith("albaranes", 5, 91, { company_id: "acme" }),
    );
    expect(mockCreate).toHaveBeenCalledTimes(1);
    // El modal se cierra; la fila pasa a tener pedido (enlace) sin releer
    // FACTUSOL, y el contador baja.
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: /Crear pedido desde/ })).not.toBeInTheDocument(),
    );
    expect(await screen.findByRole("link", { name: "Abrir pedido ALB-5-000091" }))
      .toHaveAttribute("href", "/erp/orders/o-alb91");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Albarán 5-000091: pedido ALB-5-000091 creado.",
    );
    expect(casillaExternas()).toHaveTextContent("· 0 fuera");
    expect(mockList).not.toHaveBeenCalled();
  });

  it("«Cancelar» en el modal no crea nada y la fila sigue sin pedido", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({ items: [albaranAcme()], total: 1, unlinked_total: 1 });
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Albaranes" }));
    await screen.findByText("5-000091");
    await user.click(screen.getByRole("button", { name: "Crear pedido desde 5-000091" }));
    const dialog = await screen.findByRole("dialog", { name: "Crear pedido desde el albarán 5-000091" });
    await within(dialog).findByRole("region", { name: "Resumen del pedido" });
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog", { name: /Crear pedido desde/ })).not.toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: /Abrir pedido/ })).not.toBeInTheDocument();
    expect(casillaExternas()).toHaveTextContent("· 1 fuera");
  });

  it("una factura también ofrece «Crear pedido» y, si el alta falla, avisa en el modal", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({
      items: [factura({ order: null })],
      total: 1, unlinked_total: 1,
    });
    mockCreate.mockRejectedValue(new Error("FACTUSOL no responde."));
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    await user.click(screen.getByRole("button", { name: "Crear pedido desde 5-260066" }));
    const dialog = await screen.findByRole("dialog", { name: "Crear pedido desde la factura 5-260066" });
    await within(dialog).findByRole("region", { name: "Resumen del pedido" });
    expect(mockPreview).toHaveBeenCalledWith("facturas", 5, 260066);
    expect(mockCreate).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Crear pedido" }));
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith("facturas", 5, 260066, { company_id: "es" }),
    );
    // El error del backend se muestra en el modal; la fila sigue sin pedido.
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("FACTUSOL no responde.");
    expect(screen.queryByRole("link", { name: /Abrir pedido/ })).not.toBeInTheDocument();
  });

  it("albarán de un cliente FACTUSOL sin empresa: «Crear pedido» abre «Vincular empresa» y, al vincular, el resumen", async () => {
    const user = userEvent.setup();
    const alb = (codigo: number, numero: string) => factura({
      doc_type: "albaranes", serie: 2, codigo, numero, estado: "0", estado_label: "Pendiente",
      estado_tone: "muted", cliente_codigo: "385", cliente_nombre: "JAP EDICIONES S.L.",
      company: null, order: null,
    });
    mockList.mockResolvedValue({
      items: [alb(200038, "2-200038"), alb(200039, "2-200039")], total: 2, unlinked_total: 2,
    });
    mockCreate.mockResolvedValue({ id: "o-38", order_number: "ALB-2-200038" });
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Albaranes" }));
    await screen.findByText("2-200038");
    // Las dos acciones se distinguen: a un pedido / la empresa del cliente.
    expect(screen.getAllByRole("button", { name: /a un pedido$/ })[0]).toHaveTextContent("Vincular a pedido");
    expect(screen.getAllByRole("button", { name: "Vincular empresa del cliente FACTUSOL 385" }))
      .toHaveLength(2);
    // «Crear pedido» no llama al backend: abre directamente «Vincular empresa».
    await user.click(screen.getByRole("button", { name: "Crear pedido desde 2-200038" }));
    const modal = await screen.findByRole("dialog", { name: "Vincular empresa" });
    expect(modal).toHaveTextContent("EMPRESA 385 JAP EDICIONES S.L.");
    expect(modal).toHaveTextContent("crear el pedido del albarán 2-200038");
    expect(mockPreview).not.toHaveBeenCalled();
    expect(mockCreate).not.toHaveBeenCalled();
    // Al vincular, vuelve al resumen con esa empresa: NO se crea sin confirmar.
    await user.click(within(modal).getByRole("button", { name: "ENLAZAR" }));
    const confirmar = await screen.findByRole("dialog", { name: "Crear pedido desde el albarán 2-200038" });
    expect(screen.queryByRole("dialog", { name: "Vincular empresa" })).not.toBeInTheDocument();
    const resumen = await within(confirmar).findByRole("region", { name: "Resumen del pedido" });
    expect(within(resumen).getByText("JAP EDICIONES S.L.")).toBeInTheDocument();
    expect(mockPreview).toHaveBeenCalledWith("albaranes", 2, 200038);
    expect(mockCreate).not.toHaveBeenCalled();
    await user.click(within(confirmar).getByRole("button", { name: "Crear pedido" }));
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith("albaranes", 2, 200038, { company_id: "jap" }),
    );
    expect(await screen.findByRole("link", { name: "Abrir pedido ALB-2-200038" })).toBeInTheDocument();
    // El otro albarán del mismo cliente ya no pide vincular: va directo al resumen.
    expect(screen.queryByRole("button", { name: /Vincular empresa del cliente/ })).not.toBeInTheDocument();
    mockCreate.mockResolvedValue({ id: "o-39", order_number: "ALB-2-200039" });
    await user.click(screen.getByRole("button", { name: "Crear pedido desde 2-200039" }));
    const otro = await screen.findByRole("dialog", { name: "Crear pedido desde el albarán 2-200039" });
    expect(screen.queryByRole("dialog", { name: "Vincular empresa" })).not.toBeInTheDocument();
    await within(otro).findByRole("region", { name: "Resumen del pedido" });
    await user.click(within(otro).getByRole("button", { name: "Crear pedido" }));
    await waitFor(() =>
      expect(mockCreate).toHaveBeenLastCalledWith("albaranes", 2, 200039, { company_id: "jap" }),
    );
    expect(mockCreate).toHaveBeenCalledTimes(2);
  });

  it("si al confirmar el backend dice que el cliente no está vinculado (409), se abre «Vincular empresa» en vez del error", async () => {
    const user = userEvent.setup();
    const { ApiError } = jest.requireMock("../../lib/api") as {
      ApiError: new (m: string, s: number, d: unknown) => Error;
    };
    mockList.mockResolvedValue({
      items: [factura({ order: null, cliente_codigo: "385" })],
      total: 1, unlinked_total: 1,
    });
    mockCreate.mockRejectedValueOnce(new ApiError("no vinculado", 409, {
      code: "factusol_customer_unlinked", detail: "no vinculado",
      codcli: "385", cliente_nombre: "JAP EDICIONES S.L.",
    }));
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    await user.click(screen.getByRole("button", { name: "Crear pedido desde 5-260066" }));
    const dialog = await screen.findByRole("dialog", { name: "Crear pedido desde la factura 5-260066" });
    await within(dialog).findByRole("region", { name: "Resumen del pedido" });
    await user.click(within(dialog).getByRole("button", { name: "Crear pedido" }));
    expect(await screen.findByRole("dialog", { name: "Vincular empresa" }))
      .toHaveTextContent("EMPRESA 385 JAP EDICIONES S.L.");
    expect(screen.queryByRole("dialog", { name: /Crear pedido desde/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(mockCreate).toHaveBeenCalledTimes(1);
  });

  it("sin permiso de edición no hay «Vincular»: la fila dice «Sin vincular»", async () => {
    (getCurrentUser as jest.Mock).mockResolvedValue({ role: "user" });
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    expect(await screen.findByText("Sin vincular")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /a un pedido$/ })).not.toBeInTheDocument();
  });

  it("arranca en el ejercicio en curso, sin fechas; el selector deja ver uno anterior y avisa", async () => {
    const user = userEvent.setup();
    const mes = currentMonth();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    const selector = await screen.findByLabelText("Ejercicio");
    expect(selector).toHaveValue("2026");
    expect(screen.getByRole("option", { name: "2026 (en curso)" })).toBeInTheDocument();
    expect(screen.getByLabelText("Fecha desde")).toHaveValue("");
    expect(mockList.mock.calls.at(-1)?.[1]).toEqual(expect.objectContaining({
      fecha_desde: undefined, fecha_hasta: undefined, ejercicio: undefined,
    }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // Un ejercicio anterior: se lee ese, se nota y no se ofrece cobrar.
    await user.selectOptions(selector, "2025");
    await waitFor(() => expect(mockList.mock.calls.at(-1)?.[1].ejercicio).toBe("2025"));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Ejercicio 2025, no el en curso (2026). Los documentos son de 2025. Es solo consulta");
    expect(selector.closest("label")?.className).toContain("is-anterior");
    expect(screen.queryByRole("button", { name: "Registrar cobro" })).not.toBeInTheDocument();
    // Ni selección para el ZIP (leería el ejercicio en curso).
    expect(screen.queryByRole("checkbox", { name: /Seleccionar factura/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Volver a 2026" }));
    await waitFor(() => expect(mockList.mock.calls.at(-1)?.[1].ejercicio).toBeUndefined());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // «Mes en curso» sigue siendo un atajo dentro del ejercicio.
    await user.click(screen.getByRole("button", { name: "Mes en curso" }));
    await waitFor(() => expect(screen.getByLabelText("Fecha desde")).toHaveValue(mes.desde));
  });

  it("dice «Solo lectura · sincronizado hace X» y «Sincronizar ahora» relee saltando el cache", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({
      items: [factura()], total: 1, unlinked_total: 1,
      fetched_at: new Date().toISOString(), cycle_index_age_seconds: 0,
    });
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    expect(screen.getByText("Solo lectura · sincronizado hace unos segundos")).toBeInTheDocument();
    expect(mockList).toHaveBeenCalledWith(
      "facturas", expect.objectContaining({ fresh_ciclo: undefined }),
    );
    await user.click(screen.getByRole("button", { name: "Sincronizar ahora" }));
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas", expect.objectContaining({ fresh_ciclo: true, offset: 0 }),
      ),
    );
  });

  it("la tabla es responsive: cada celda lleva su etiqueta y los importes van en `num`", async () => {
    render(<FactusolDocumentosPage />);
    const num = await screen.findByText("5-260066");
    const table = num.closest("table") as HTMLElement;
    expect(table.className).toContain("data-table--responsive");
    const row = num.closest("tr") as HTMLElement;
    const labels = Array.from(row.querySelectorAll("td")).map((td) => td.getAttribute("data-label"));
    expect(labels).toEqual([
      "Seleccionar", "Nº", "Cliente", "Fecha", "Total", "Saldo pend.", "Estado", "Pedido", "Acciones",
    ]);
    // Total y saldo: celdas `num` (mono, a la derecha) de ancho fijo.
    const amounts = within(row).getAllByText("186.34 €", { selector: "td.num" });
    expect(amounts).toHaveLength(2);
    expect(amounts.every((td) => td.className.includes("erp-doc-col-amount"))).toBe(true);
    expect(within(table).getAllByRole("columnheader", { name: /Total|Saldo pend\./ })
      .every((th) => th.className.includes("num"))).toBe(true);
  });
});


describe("Documentos · Presupuestos — «Duplicar» en el «…» (remates · punto 4)", () => {
  const presupuesto = () => factura({
    doc_type: "presupuestos", codigo: 4361, numero: "5-004361",
    estado: "0", estado_label: "Pendiente", estado_tone: "warn", order: null,
  });

  it("abre la copia directa con el presupuesto de la fila (cliente y serie de origen)", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({ items: [presupuesto()], total: 1 });
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Presupuestos" }));
    await screen.findByText("5-004361");
    await user.click(screen.getByRole("button", { name: "Más acciones 5-004361" }));
    await user.click(await screen.findByRole("button", { name: "Duplicar" }));
    expect(await screen.findByRole("dialog", { name: "Duplicar proforma" })).toHaveTextContent(
      "COPIA 5-004361 · 4361 · serie 5 · empresa es",
    );
  });

  it("sin permiso de proformas no aparece; en facturas tampoco", async () => {
    const user = userEvent.setup();
    (getCurrentUser as jest.Mock).mockResolvedValueOnce({ role: "user" });
    mockList.mockResolvedValue({ items: [presupuesto()], total: 1 });
    const { unmount } = render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Presupuestos" }));
    await screen.findByText("5-004361");
    await user.click(screen.getByRole("button", { name: "Más acciones 5-004361" }));
    expect(await screen.findByText("Ver detalle")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Duplicar" })).not.toBeInTheDocument();
    unmount();
    mockList.mockResolvedValue({ items: [factura()], total: 1 });
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    await user.click(screen.getByRole("button", { name: "Más acciones 5-260066" }));
    expect(await screen.findByText("Ver detalle")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Duplicar" })).not.toBeInTheDocument();
  });
});

describe("Documentos · «Guardar como plantilla» en el «…» (plantillas de documento)", () => {
  it("en un albarán abre «Guardar como plantilla» con el albarán como origen", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({
      items: [factura({ doc_type: "albaranes", codigo: 77, numero: "5-000077",
                        estado: "0", estado_label: "Pendiente", estado_tone: "muted" })],
      total: 1,
    });
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Albaranes" }));
    await screen.findByText("5-000077");
    await user.click(screen.getByRole("button", { name: "Más acciones 5-000077" }));
    await user.click(await screen.findByRole("button", { name: "Guardar como plantilla" }));
    expect(await screen.findByRole("dialog", { name: "Guardar como plantilla" })).toHaveTextContent(
      "PLANTILLA albaran · serie 5 · código 77 · 5-000077",
    );
    // Solo abre el modal: no crea pedido ni nada más.
    expect(mockCreate).not.toHaveBeenCalled();
  });
});
