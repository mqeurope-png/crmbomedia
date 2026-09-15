import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { getOrder } from "../../../lib/erpApi";

/** ERP · rediseño de flujo (Fase 1) — la FICHA como «línea de vida».
 *
 *  Cabecera con el cliente y su régimen, barra de alertas, stepper de 7 pasos
 *  con el actual resaltado, «Siguiente paso» con su botón, resumen económico
 *  con el IVA que toca y bloque FACTUSOL. Todo lo anterior sigue debajo. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: {
    children: React.ReactNode; href: string; className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));
jest.mock("next/navigation", () => ({ useParams: () => ({ id: "o-1" }) }));
// La cabecera del rediseño lleva las acciones del pedido (PDF, email,
// completado, «⋯»): el mock las pinta para que sigan siendo accesibles.
jest.mock("../../../components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <><h1>{title}</h1>{actions}</>
  ),
}));
jest.mock("../../../components/erp/EmbalarModal", () => ({ EmbalarModal: () => null }));
jest.mock("../../../components/erp/FactusolDocumentDetailModal", () => ({
  PDF_LANGS: [{ value: "es", label: "Español" }],
}));
jest.mock("../../../components/erp/InvoiceEmailModal", () => ({ InvoiceEmailModal: () => null }));
jest.mock("../../../components/erp/OrderEmailModal", () => ({ OrderEmailModal: () => null }));
// El botón de emisión sólo tiene que decir si la ficha le pidió abrirse.
jest.mock("../../../components/erp/EmitFactusolButton", () => ({
  EmitFactusolButton: ({ openSignal, buttonHidden }: { openSignal?: number; buttonHidden?: boolean }) => (
    <>
      {buttonHidden ? null : <button type="button">Emitir factura FACTUSOL</button>}
      <span>emision:{openSignal ?? 0}</span>
    </>
  ),
}));
jest.mock("../../../components/erp/ShippingFilesSection", () => ({
  ShippingFilesSection: () => <div>documentos de envío</div>,
}));
jest.mock("../../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  // La fila «Otras acciones de estado» es real: necesita las etiquetas y
  // los estados del cliente de API.
  DOMAIN_LABELS: {
    payment: "Pago", preparation: "Preparación", transport: "Transporte", invoice: "Facturación",
  },
  STATUS_LABELS: {},
  customerLabel: () => "Alexandre · La Maison de la Plaque",
  getOrder: jest.fn(),
  getOrderTimeline: jest.fn(() => Promise.resolve({ total: 0, items: [] })),
  getFactusolStatus: jest.fn(() => Promise.resolve({ status: "none" })),
  getErpSettings: jest.fn(() => Promise.resolve({ shipping_origins: [] })),
  getOrderFactusolInvoiceRef: jest.fn(),
  getOrderFactusolCobro: jest.fn(() => Promise.resolve({ status: "pendiente", invoice: null })),
  downloadOrderFactusolPedidoPdf: jest.fn(),
  createOrderAlbaran: jest.fn(),
  getQuoteJobStatus: jest.fn(),
  fireTransition: jest.fn(),
  saveBlob: jest.fn(),
  updateOrderLanguage: jest.fn(),
  updateOrderSeguimiento: jest.fn(),
  completeOrder: jest.fn(),
  uncompleteOrder: jest.fn(),
}));

const STEPS = [
  { key: "creado", label: "Creado", state: "done", detail: "2026-09-08" },
  { key: "pagado", label: "Pagado", state: "done", detail: "351.52 EUR" },
  { key: "aprobado", label: "Aprobado", state: "done", detail: "2026-09-09" },
  { key: "albaran", label: "Albarán", state: "done", detail: "2-100418" },
  { key: "factura", label: "Factura", state: "now", detail: null },
  { key: "cobro", label: "Cobro", state: "pending", detail: null },
  { key: "enviado", label: "Enviado", state: "pending", detail: null },
];

function detail(over = {}) {
  return {
    id: "o-1", order_number: "ARTISJ-9544", contact_name: "Alexandre",
    company_name: "La Maison de la Plaque", external_source: "woocommerce",
    store_id: null, contact_id: null, company_id: "c-1", total_amount: 351.52,
    currency: "EUR", payment_status: "paid", preparation_status: "in_queue",
    transport_status: "not_shipped", invoice_status: "not_invoiced",
    tracking_number: null, factusol_invoice_number: null,
    factusol_albaran_number: "2-100418", factusol_cobro_status: null,
    serial_number: null, whiterip_license: null, shipping_origin: null, language: "es",
    approved_at: "2026-09-09T10:00:00", placed_at: "2026-09-08T10:00:00",
    created_at: "2026-09-08T10:00:00", externally_processed_at: null,
    externally_processed_note: null, externally_processed_by_user_id: null,
    notes: null, packing: null,
    lines: [{
      id: "l-1", position: 0, product_sku: "99cy", product_codart: "99cy",
      description: "Tinta", quantity: 1, unit_price: 351.52, tax_rate: 21,
      line_total: 351.52, notes: null,
    }],
    status_history: [], exceptions: [], blockers: [],
    available_transitions: {
      payment: [], invoice: [],
      preparation: [{ to_status: "preparing", label: "Empezar preparación", required_evidence: [] }],
      transport: [{ to_status: "label_created", label: "Crear envío", required_evidence: [] }],
    },
    warnings: [], completed: false, completed_at: null, completed_by_user_id: null,
    completed_by_name: null,
    workflow: {
      queue: "por_facturar", queue_label: "Por facturar",
      next_action: "emitir_factura", next_action_label: "Emitir factura",
      next_action_hint: "Emite la factura en FACTUSOL (saldrá exenta).",
      blocked: false, regime: "intracomunitario", steps: STEPS,
      alerts: [{
        code: "cliente_intracomunitario",
        text: "Cliente intracomunitario: la factura debe salir sin IVA.",
        action: null, action_label: null, blocking: false,
      }],
      company: {
        id: "c-1", name: "La Maison de la Plaque", country: "FR",
        factusol_id: "2760", regime: "intracomunitario",
      },
    },
    ...over,
  };
}

beforeEach(() => {
  (getOrder as jest.Mock).mockReset();
  (getOrder as jest.Mock).mockResolvedValue(detail());
});

describe("ERP · Ficha del pedido (rediseño de flujo)", () => {
  it("la cabecera dice quién es el cliente, su régimen y su nº de FACTUSOL", async () => {
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Alexandre · La Maison de la Plaque")).toBeInTheDocument();
    expect(screen.getByText("FR · intracomunitario · exento")).toBeInTheDocument();
    expect(screen.getByText("FACTUSOL nº 2760")).toBeInTheDocument();
  });

  it("el stepper pinta los 7 pasos con el actual resaltado", async () => {
    render(<ErpOrderDetailPage />);
    const steps = within(await screen.findByRole("list", { name: "Ciclo del pedido" }));
    const items = steps.getAllByRole("listitem");
    expect(items).toHaveLength(7);
    expect(steps.getByText("Albarán").closest("li")).toHaveTextContent("2-100418");
    const actual = items.find((li) => li.getAttribute("aria-current") === "step");
    expect(actual).toHaveTextContent("Factura");
  });

  it("la alerta de IVA sale arriba con el enlace al cliente", async () => {
    render(<ErpOrderDetailPage />);
    const bar = within(await screen.findByRole("alert", { name: "Alertas del pedido" }));
    expect(bar.getByText(/la factura debe salir sin IVA/)).toBeInTheDocument();
    expect(bar.getByRole("link", { name: "Ver ficha cliente" }))
      .toHaveAttribute("href", "/companies/c-1");
  });

  it("«Siguiente paso» dice lo que toca y su botón abre la emisión", async () => {
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    const bar = within(await screen.findByRole("region", { name: "Siguiente paso" }));
    expect(bar.getByText(/Emite la factura en FACTUSOL/)).toBeInTheDocument();
    expect(screen.getByText("emision:0")).toBeInTheDocument();
    await user.click(bar.getByRole("button", { name: "Emitir factura" }));
    expect(await screen.findByText("emision:1")).toBeInTheDocument();
  });

  it("el resumen económico aplica el régimen y el bloque FACTUSOL enseña el estado real", async () => {
    render(<ErpOrderDetailPage />);
    const eco = within(await screen.findByRole("region", { name: "Resumen económico" }));
    expect(eco.getByText("Base imponible").nextSibling).toHaveTextContent("351.52 EUR");
    expect(eco.getByText("IVA (intracomunitario)").nextSibling)
      .toHaveTextContent("0.00 EUR · exento");
    expect(eco.getByText("Total").nextSibling).toHaveTextContent("351.52 EUR");

    const fac = within(screen.getByRole("region", { name: "FACTUSOL" }));
    expect(fac.getByText("Cliente").nextSibling).toHaveTextContent("2760 · vinculado");
    expect(fac.getByText("Albarán").nextSibling).toHaveTextContent("2-100418");
    // La fila «Factura» lleva el control de emisión (aquí, su mock); «Cobro»
    // dice que aún no hay factura sobre la que cobrar.
    expect(fac.getByText("Factura").nextSibling).toHaveTextContent("emision:0");
    expect(fac.getByText("Cobro").nextSibling).toHaveTextContent("sin factura");
  });

  it("no se pierde nada de lo que ya había en la ficha", async () => {
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("documentos de envío")).toBeInTheDocument();
    for (const titulo of ["Líneas", "Actividad", "Seguimiento", "FACTUSOL", "Resumen económico"]) {
      expect(screen.getByRole("heading", { name: titulo })).toBeInTheDocument();
    }
    // Cabecera: idioma del PDF, PDF del pedido, email, completado y «⋯»
    // (idioma del pedido, enviar factura por email).
    expect(screen.getByRole("combobox", { name: "Idioma del PDF" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /PDF del pedido \(FACTUSOL\)/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enviar por email" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Marcar completado" })).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Más acciones del pedido" }));
    expect(screen.getByRole("combobox", { name: "Idioma del pedido" })).toBeInTheDocument();
    // Las transiciones de estado siguen (fila compacta), no las 4 tarjetas.
    const estados = screen.getByRole("region", { name: "Otras acciones de estado" });
    expect(estados).toHaveTextContent("Preparación");
    expect(within(estados).getByRole("button", { name: "Crear envío" })).toBeInTheDocument();
    expect(document.querySelector(".erp-states")).toBeNull();
    expect(document.querySelector(".erp-card")).toBeNull();
  });

  it("test_ficha_no_duplica_albaran_ni_acciones: cada acción y el albarán salen una sola vez", async () => {
    render(<ErpOrderDetailPage />);
    await screen.findByText("documentos de envío");
    // «Emitir factura» es el siguiente paso: SOLO en la barra, no repetido en
    // el bloque FACTUSOL. Las demás, una vez cada una.
    expect(screen.getAllByRole("button", { name: /Emitir factura/ })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Emitir factura FACTUSOL" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Marcar completado" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Enviar por email" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /PDF del pedido/ })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /Registrar cobro/ })).toHaveLength(1);
    // El nº de albarán aparece en el stepper y en el bloque FACTUSOL como
    // dato; el albarán como DOCUMENTO (PDF / crear) solo en Documentos de envío.
    expect(screen.queryByText("Albarán y pago FACTUSOL")).toBeNull();
    expect(screen.queryByText("Cobro FACTUSOL:")).toBeNull();
  });

  it("con incidencia bloqueante la barra cambia de tono y lleva a resolverla", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      workflow: {
        ...detail().workflow,
        queue: "incidencias", queue_label: "Incidencias", blocked: true,
        next_action: "revisar_incidencia", next_action_label: "Revisar incidencia",
        next_action_hint: "Excepción abierta: rotura.",
        alerts: [{
          code: "excepcion_abierta", text: "Excepción abierta: rotura.",
          action: "revisar_incidencia", action_label: "Revisar incidencia", blocking: true,
        }],
      },
    }));
    render(<ErpOrderDetailPage />);
    const bar = within(await screen.findByRole("region", { name: "Siguiente paso" }));
    expect(bar.getByText("Hay que resolver esto")).toBeInTheDocument();
    expect(bar.getByRole("link", { name: "Ver excepciones" })).toHaveAttribute("href", "/erp/exceptions");
    // El mapeo de líneas ya no es incidencia ni acción: nada de «Mapear líneas».
    expect(screen.queryByText(/Mapear líneas/)).toBeNull();
  });

  // --- ninguna acción dos veces, en cada estado del flujo ---

  function conSiguientePaso(over: Record<string, unknown>, extra: Record<string, unknown> = {}) {
    return detail({ ...extra, workflow: { ...detail().workflow, alerts: [], ...over } });
  }

  it("«Marcar completado» como siguiente paso: solo en la barra (la cabecera no lo repite)", async () => {
    (getOrder as jest.Mock).mockResolvedValue(conSiguientePaso({
      next_action: "marcar_completado", next_action_label: "Marcar completado",
      next_action_hint: "Todo hecho: márcalo como completado.",
    }));
    render(<ErpOrderDetailPage />);
    const bar = within(await screen.findByRole("region", { name: "Siguiente paso" }));
    expect(bar.getByRole("button", { name: "Marcar completado" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Marcar completado" })).toHaveLength(1);
  });

  it("«Enviar a SAT» como siguiente paso: un solo «Enviar por email»", async () => {
    (getOrder as jest.Mock).mockResolvedValue(conSiguientePaso({
      next_action: "enviar_sat", next_action_label: "Enviar a SAT",
      next_action_hint: "El taller tiene que preparar el pedido.",
    }));
    render(<ErpOrderDetailPage />);
    await screen.findByRole("region", { name: "Siguiente paso" });
    expect(screen.getAllByRole("button", { name: "Enviar por email" })).toHaveLength(1);
  });

  it("«Registrar cobro» como siguiente paso respeta el estado en vivo: cobrada fuera de BoHub → deshabilitado, sin segundo cobro", async () => {
    const { getOrderFactusolCobro } = jest.requireMock("../../../lib/erpApi");
    (getOrderFactusolCobro as jest.Mock).mockResolvedValue({
      order_id: "o-1", status: "cobrada", invoice: { serie: 2, codigo: 526087, numero: "2-526087" },
    });
    (getOrder as jest.Mock).mockResolvedValue(conSiguientePaso({
      next_action: "registrar_cobro", next_action_label: "Registrar cobro",
      next_action_hint: "El pedido consta pagado: registra el cobro en FACTUSOL.",
    }, { factusol_invoice_number: "526087", invoice_status: "invoiced_by_erp", factusol_cobro_status: null }));
    render(<ErpOrderDetailPage />);
    const btn = await screen.findByRole("button", { name: "Cobrado en FACTUSOL" });
    expect(btn).toBeDisabled();
    // Una sola vez: el del panel FACTUSOL se esconde cuando es el siguiente paso.
    expect(screen.getAllByRole("button", { name: /cobr/i })).toHaveLength(1);
    expect(screen.getByText("Cobrado FACTUSOL")).toBeInTheDocument();
  });

  it("«Preparar envío» como siguiente paso dispara la transición de transporte y la fila de estados no la repite", async () => {
    const { fireTransition } = jest.requireMock("../../../lib/erpApi");
    (fireTransition as jest.Mock).mockResolvedValue(detail());
    (getOrder as jest.Mock).mockResolvedValue(conSiguientePaso({
      next_action: "crear_envio", next_action_label: "Preparar envío",
      next_action_hint: "Prepara el envío y marca el transporte.",
    }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    const bar = within(await screen.findByRole("region", { name: "Siguiente paso" }));
    expect(screen.getAllByRole("button", { name: "Crear envío" })).toHaveLength(1);
    await user.click(bar.getByRole("button", { name: "Crear envío" }));
    await waitFor(() => expect(fireTransition).toHaveBeenCalledWith(
      "o-1", expect.objectContaining({ domain: "transport", to_status: "label_created" }),
    ));
  });

  it("incidencia bloqueante: el enlace que la resuelve sale una vez (en «Siguiente paso»), la alerta solo avisa", async () => {
    (getOrder as jest.Mock).mockResolvedValue(conSiguientePaso({
      queue: "incidencias", blocked: true,
      next_action: "revisar_incidencia", next_action_label: "Revisar incidencia",
      next_action_hint: "1 excepción(es) sin resolver",
      alerts: [{
        code: "excepcion_abierta", text: "1 excepción(es) sin resolver",
        action: "revisar_incidencia", action_label: "Revisar incidencia", blocking: true,
      }],
    }));
    render(<ErpOrderDetailPage />);
    await screen.findByRole("region", { name: "Siguiente paso" });
    expect(screen.getAllByRole("link", { name: "Ver excepciones" })).toHaveLength(1);
  });
});
