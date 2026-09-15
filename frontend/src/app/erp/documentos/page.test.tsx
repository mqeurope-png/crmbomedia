import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FactusolDocumentosPage from "./page";
import { getCurrentUser } from "../../lib/api";
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
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
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

/** Mes en curso en la zona del navegador (mismo cálculo que la página). */
function currentMonth() {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth();
  const pad = (n: number) => String(n).padStart(2, "0");
  const last = new Date(y, m + 1, 0).getDate();
  return { desde: `${y}-${pad(m + 1)}-01`, hasta: `${y}-${pad(m + 1)}-${pad(last)}` };
}

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
      // Lote 2 · PR-2: también quita el rango de mes por defecto.
      expect(last?.[1].fecha_desde).toBeUndefined();
      expect(last?.[1].fecha_hasta).toBeUndefined();
    });
  });
});

describe("ERP · Documentos FACTUSOL (Lote 2 · PR-2)", () => {
  it("el chip «Solo sin vincular · N» lleva el contador y filtra con `linked=false`", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({ items: [factura()], total: 7, unlinked_total: 4 });
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    const chip = screen.getByRole("button", { name: /Solo sin vincular/ });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    expect(chip).toHaveTextContent("Solo sin vincular · 4");
    expect(mockList).toHaveBeenCalledWith(
      "facturas", expect.objectContaining({ linked: undefined }),
    );
    await user.click(chip);
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith(
        "facturas", expect.objectContaining({ linked: false }),
      ),
    );
    expect(screen.getByRole("button", { name: /Solo sin vincular/ }))
      .toHaveAttribute("aria-pressed", "true");
    // Se desactiva con otro clic (vuelve a «todos»).
    mockList.mockClear();
    await user.click(screen.getByRole("button", { name: /Solo sin vincular/ }));
    await waitFor(() => expect(mockList.mock.calls.at(-1)?.[1].linked).toBeUndefined());
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
    expect(screen.getByRole("button", { name: /Solo sin vincular/ }))
      .toHaveTextContent("Solo sin vincular · 0");
    expect(mockList).not.toHaveBeenCalled();
  });

  it("en albaranes también se ofrece «Vincular»; en presupuestos sigue siendo «Crear pedido»", async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue({
      items: [factura({ doc_type: "albaranes", codigo: 91, numero: "5-000091",
                        estado: "0", estado_label: "Pendiente", estado_tone: "muted" })],
      total: 1, unlinked_total: 1,
    });
    render(<FactusolDocumentosPage />);
    await user.click(await screen.findByRole("tab", { name: "Albaranes" }));
    expect(await screen.findByRole("button", { name: "Vincular 5-000091 a un pedido" }))
      .toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Crear pedido" })).not.toBeInTheDocument();
    mockList.mockResolvedValue({
      items: [factura({ doc_type: "presupuestos", codigo: 28, numero: "5-000028",
                        estado: "0", estado_label: "Pendiente", estado_tone: "warn" })],
      total: 1, unlinked_total: 1,
    });
    await user.click(screen.getByRole("tab", { name: "Presupuestos" }));
    expect(await screen.findByRole("link", { name: "Crear pedido" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /a un pedido$/ })).not.toBeInTheDocument();
  });

  it("sin permiso de edición no hay «Vincular»: la fila dice «Sin vincular»", async () => {
    (getCurrentUser as jest.Mock).mockResolvedValue({ role: "user" });
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    expect(await screen.findByText("Sin vincular")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /a un pedido$/ })).not.toBeInTheDocument();
  });

  it("el rango de fechas arranca en el mes en curso, se puede borrar y volver a poner", async () => {
    const user = userEvent.setup();
    const { desde, hasta } = currentMonth();
    render(<FactusolDocumentosPage />);
    await screen.findByText("5-260066");
    expect(screen.getByLabelText("Fecha desde")).toHaveValue(desde);
    expect(screen.getByLabelText("Fecha hasta")).toHaveValue(hasta);
    expect(mockList).toHaveBeenCalledWith(
      "facturas", expect.objectContaining({ fecha_desde: desde, fecha_hasta: hasta }),
    );
    expect(screen.queryByRole("button", { name: "Mes en curso" })).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText("Fecha desde"));
    await waitFor(() =>
      expect(mockList.mock.calls.at(-1)?.[1]).toEqual(
        expect.objectContaining({ fecha_desde: undefined, fecha_hasta: hasta }),
      ),
    );
    await user.click(await screen.findByRole("button", { name: "Mes en curso" }));
    await waitFor(() => expect(screen.getByLabelText("Fecha desde")).toHaveValue(desde));
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
