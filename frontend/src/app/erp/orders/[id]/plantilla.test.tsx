import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { getCurrentUser } from "../../../lib/api";
import { createPlantilla, getOrder } from "../../../lib/erpApi";

/** «Guardar como plantilla» desde la ficha de un pedido: en «⋯ Más acciones
 *  del pedido» para quien tiene `erp.proformas` (no en muestras). Abre
 *  GuardarPlantillaModal con origen {tipo:"pedido", order_id}; el servidor
 *  copia las líneas del pedido (no se toca FACTUSOL). */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "o-1" }),
  useSearchParams: () => new URLSearchParams(""),
}));
jest.mock("../../../components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <><h1>{title}</h1>{actions}</>
  ),
}));
jest.mock("../../../components/erp/EmbalarModal", () => ({ EmbalarModal: () => null }));
jest.mock("../../../components/erp/GeneiShipmentSection", () => ({ GeneiShipmentSection: () => null }));
jest.mock("../../../components/erp/FactusolDocumentDetailModal", () => ({
  PDF_LANGS: [{ value: "es", label: "Español" }, { value: "de", label: "Deutsch" }],
}));
jest.mock("../../../components/erp/InvoiceEmailModal", () => ({ InvoiceEmailModal: () => null }));
jest.mock("../../../components/erp/OrderEmailModal", () => ({ OrderEmailModal: () => null }));
jest.mock("../../../components/erp/CancelOrderModal", () => ({ CancelOrderModal: () => null }));
jest.mock("../../../components/erp/EmitFactusolButton", () => ({ EmitFactusolButton: () => null }));
jest.mock("../../../components/erp/ShippingFilesSection", () => ({
  ShippingFilesSection: () => <div>documentos de envío</div>,
}));
jest.mock("../../../components/erp/OrderFactusolClientPanel", () => ({
  OrderFactusolClientPanel: () => null,
}));
jest.mock("../../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  DOMAIN_LABELS: {
    payment: "Pago", preparation: "Preparación", transport: "Transporte", invoice: "Facturación",
  },
  STATUS_LABELS: {},
  customerHeadline: () => "Escola La Muntanyeta",
  customerLabel: () => "Escola La Muntanyeta",
  resolveOrderCobroStatus: (
    o: { factusol_cobro_status?: string | null },
    live: { status?: string | null } | null | undefined,
  ) => {
    const s = live?.status === "cobrada" || live?.status === "pendiente" ? live.status : null;
    return s ?? o.factusol_cobro_status ?? null;
  },
  getOrder: jest.fn(),
  getOrderTimeline: jest.fn(() => Promise.resolve({ total: 0, items: [] })),
  getFactusolStatus: jest.fn(() => Promise.resolve({ status: "none" })),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  getOrderFactusolInvoiceRef: jest.fn(),
  getOrderFactusolCobro: jest.fn(() => Promise.resolve({ status: "pendiente", invoice: null })),
  downloadOrderFactusolPedidoPdf: jest.fn(),
  downloadFactusolDocumentPdf: jest.fn(),
  createOrderAlbaran: jest.fn(),
  getQuoteJobStatus: jest.fn(),
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
  uncancelOrder: jest.fn(),
  // GuardarPlantillaModal (de verdad, no un doble).
  createPlantilla: jest.fn(),
  getPlantillasMeta: jest.fn(() => Promise.resolve({
    idiomas: ["es", "en", "de", "fr", "nl"], destinos: ["oferta", "proforma", "pedido"],
    max_profundidad: 3, carpeta_defecto: null, gestiona_acceso: false, acceso_restringido: false,
  })),
  listPlantillaCarpetas: jest.fn(() => Promise.resolve({
    items: [{ id: "c-dtf", nombre: "DTF", parent_id: null, num_plantillas: 0, es_defecto: false, children: [] }],
    carpeta_defecto: null,
  })),
  listPlantillaEtiquetas: jest.fn(() => Promise.resolve([])),
}));

function line(over = {}) {
  return {
    id: "l", position: 0, product_sku: "", product_codart: null,
    description: "", quantity: 1, unit_price: 0, tax_rate: 0, line_total: 0,
    notes: null, is_shipping: false, line_kind: null, ...over,
  };
}

function detail(over = {}) {
  return {
    id: "o-1", order_number: "MANUAL-000777", contact_name: null,
    company_name: "Escola La Muntanyeta", external_source: "manual",
    store_id: null, contact_id: null, company_id: "c-1", total_amount: 300,
    currency: "EUR", payment_status: "pending", preparation_status: "pending_review",
    transport_status: "not_shipped", invoice_status: "not_invoiced",
    tracking_number: null, factusol_invoice_number: null,
    factusol_albaran_number: "5-500010", factusol_cobro_status: null,
    serial_number: null, whiterip_license: null, shipping_origin: null, language: "de",
    approved_at: null, placed_at: "2026-09-08T10:00:00",
    created_at: "2026-09-08T10:00:00", externally_processed_at: null,
    externally_processed_note: null, externally_processed_by_user_id: null,
    notes: null, packing: null,
    lines: [
      line({ id: "l1", product_sku: "DTF-60", product_codart: "A60", description: "Impresora DTF 60 cm",
             quantity: 1, unit_price: 300, tax_rate: 21, line_total: 300 }),
    ],
    status_history: [], exceptions: [], blockers: [],
    available_transitions: { payment: [], invoice: [], preparation: [], transport: [] },
    warnings: [], completed: false, completed_at: null, completed_by_user_id: null,
    completed_by_name: null, cancelled: false, cancelled_at: null, cancelled_reason: null,
    cancelled_by_name: null,
    workflow: {
      queue: "por_revisar", queue_label: "Por revisar",
      next_action: "aprobar", next_action_label: "Aprobar", next_action_hint: "Revisa.",
      blocked: false, regime: "nacional", steps: [], alerts: [],
      company: { id: "c-1", name: "Escola La Muntanyeta", country: "ES",
        factusol_id: "3001", regime: "nacional" },
    },
    ...over,
  };
}

const MUESTRA = {
  order_number: "MUESTRA-000003", external_source: "manual", order_kind: "sample",
  born_as_sample: true, total_amount: 0, company_id: null, company_name: null,
  factusol_albaran_number: null, linked_documents: [], sample_pending_link: null,
  workflow: {
    queue: "listo", queue_label: "Listo",
    next_action: "marcar_completado", next_action_label: "Marcar completado",
    next_action_hint: "Muestra.", blocked: false, regime: null, steps: [], alerts: [],
    company: null,
  },
};

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
  (getOrder as jest.Mock).mockResolvedValue(detail());
  (createPlantilla as jest.Mock).mockReset();
  (createPlantilla as jest.Mock).mockResolvedValue({
    id: "pl-9", nombre: "Kit DTF feria", descripcion: null, activa: true, idioma: "de",
    carpeta_id: null, carpeta: null, etiquetas: [], origen_tipo: "pedido", origen_serie: null,
    origen_codigo: null, origen_order_id: "o-1", origen_numero: "MANUAL-000777", num_lineas: 1,
    created_at: null, updated_at: null,
  });
  (getCurrentUser as jest.Mock).mockImplementation(() => Promise.resolve({ role: "admin" }));
});

async function abrirMenu(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("documentos de envío");
  await user.click(await screen.findByRole("button", { name: "Más acciones del pedido" }));
}

describe("Ficha · Guardar como plantilla", () => {
  it("«⋯» ofrece «Guardar como plantilla»: abre el modal con el pedido como origen y guarda", async () => {
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await abrirMenu(user);
    await user.click(screen.getByRole("button", { name: "Guardar como plantilla" }));

    const dialog = await screen.findByRole("dialog", {
      name: "Guardar el pedido MANUAL-000777 como plantilla",
    });
    expect(dialog).toHaveTextContent("No se toca nada en FACTUSOL");
    // El idioma del pedido se propone como idioma de la plantilla.
    expect(screen.getByLabelText("Idioma de la plantilla")).toHaveValue("de");
    await screen.findByRole("option", { name: "DTF" });

    await user.type(screen.getByLabelText("Nombre de la plantilla"), "Kit DTF feria");
    await user.click(screen.getByRole("button", { name: "Guardar plantilla" }));
    await waitFor(() => expect(createPlantilla).toHaveBeenCalledWith({
      nombre: "Kit DTF feria",
      descripcion: null,
      idioma: "de",
      carpeta_id: null,
      etiquetas: [],
      origen: { tipo: "pedido", order_id: "o-1" },
    }));
    // Al guardar se cierra y se avisa en la ficha.
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Plantilla «Kit DTF feria» guardada (ERP · Plantillas).",
    );
    expect(screen.queryByRole("dialog", { name: /como plantilla/ })).toBeNull();
  });

  it("Cancelar cierra el modal sin guardar", async () => {
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await abrirMenu(user);
    await user.click(screen.getByRole("button", { name: "Guardar como plantilla" }));
    await screen.findByRole("dialog", { name: "Guardar el pedido MANUAL-000777 como plantilla" });
    await screen.findByRole("option", { name: "DTF" });
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog", { name: /como plantilla/ })).toBeNull();
    expect(createPlantilla).not.toHaveBeenCalled();
  });

  it("con un idioma que no es de los PDF, la plantilla sale en español", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({ language: "pt" }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await abrirMenu(user);
    await user.click(screen.getByRole("button", { name: "Guardar como plantilla" }));
    await screen.findByRole("dialog", { name: "Guardar el pedido MANUAL-000777 como plantilla" });
    expect(screen.getByLabelText("Idioma de la plantilla")).toHaveValue("es");
    await screen.findByRole("option", { name: "DTF" });
  });

  it("sin `erp.proformas` no se ofrece (aunque vea el menú «⋯»)", async () => {
    (getCurrentUser as jest.Mock).mockImplementation(() => Promise.resolve({
      role: "comercial",
      capabilities: ["erp.access", "erp.orders.create", "erp.invoice.emit", "erp.office.view"],
    }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await abrirMenu(user);
    expect(screen.getByRole("button", { name: "Anular pedido" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Guardar como plantilla" })).toBeNull();
  });

  it("un comercial (con `erp.proformas`) sí lo tiene", async () => {
    (getCurrentUser as jest.Mock).mockImplementation(() => Promise.resolve({ role: "comercial" }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await abrirMenu(user);
    expect(screen.getByRole("button", { name: "Guardar como plantilla" })).toBeInTheDocument();
  });

  it("en una muestra no se ofrece", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail(MUESTRA));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await abrirMenu(user);
    expect(screen.getByRole("button", { name: "Anular pedido" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Guardar como plantilla" })).toBeNull();
  });

  it("un pedido sin líneas no lo ofrece", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({ lines: [] }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    await abrirMenu(user);
    expect(screen.queryByRole("button", { name: "Guardar como plantilla" })).toBeNull();
  });
});
