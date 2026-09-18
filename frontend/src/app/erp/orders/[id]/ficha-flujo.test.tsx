import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpOrderDetailPage from "./page";
import { getOrder } from "../../../lib/erpApi";

/** ERP · rediseño de flujo (Fase 1) — la FICHA como «línea de vida».
 *
 *  Cabecera con el cliente y su régimen, barra de alertas, los 7 pasos con el
 *  actual resaltado, «Siguiente paso» con su botón, resumen económico con el
 *  IVA que toca y bloque FACTUSOL. Todo lo anterior sigue debajo.
 *
 *  Lote 2 · PR-2: la línea de vida es VERTICAL (el paso actual es la única
 *  tarjeta azul y lleva «Siguiente paso» con su acción dentro), la cabecera
 *  tiene la barra de 6 segmentos obligatorios («Paso 5 de 6 · Factura») más el
 *  hito opcional «Factura enviada», y la miga vuelve a la cola de la bandeja de
 *  la que se llegó (`?from=`). */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: {
    children: React.ReactNode; href: string; className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));
// `?from=<cola>`: la miga de vuelta a la bandeja (Lote 2 · PR-2).
let search = "";
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "o-1" }),
  useSearchParams: () => new URLSearchParams(search),
}));
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
// El modal de la factura solo tiene que decir con qué pedido se abre (#426:
// el destinatario/tienda son los de ESTE pedido, no los de un homónimo).
jest.mock("../../../components/erp/InvoiceEmailModal", () => ({
  InvoiceEmailModal: ({ orderId, numero }: { orderId?: string | null; numero?: string }) => (
    <div role="dialog" aria-label="Enviar factura por email">
      factura:{numero} pedido:{orderId ?? "—"}
    </div>
  ),
}));
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
// El panel real se prueba aparte; aquí solo importa si la ficha le pidió
// abrir el selector de la etiqueta (Lote 2 C) y que, subida, la ficha recargue.
jest.mock("../../../components/erp/ShippingFilesSection", () => ({
  ShippingFilesSection: ({ openEtiquetaSignal, onUploaded }: {
    openEtiquetaSignal?: number;
    onUploaded?: (r: unknown) => void;
  }) => (
    <>
      <div>documentos de envío</div>
      <span>etiqueta:{openEtiquetaSignal ?? 0}</span>
      <button
        type="button"
        onClick={() => onUploaded?.({
          kind: "etiqueta", file: { id: "f-1" }, transition_applied: true,
          transport_status: "label_created", transition_reason: null,
        })}
      >
        simular etiqueta subida
      </button>
    </>
  ),
}));
jest.mock("../../../components/erp/OrderFactusolClientPanel", () => ({
  OrderFactusolClientPanel: () => null,
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
  // El SAT/«Enviado» ya no es un paso obligatorio; en su lugar, el hito
  // OPCIONAL «Factura enviada» (no cuenta para el «Paso N de N»).
  { key: "factura_enviada", label: "Factura enviada", state: "pending", detail: null, optional: true },
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
  search = "";
  (getOrder as jest.Mock).mockReset();
  (getOrder as jest.Mock).mockResolvedValue(detail());
});

describe("ERP · Ficha del pedido (rediseño de flujo)", () => {
  it("la cabecera dice quién es el cliente, su cola, su régimen y su nº de FACTUSOL", async () => {
    render(<ErpOrderDetailPage />);
    expect(await screen.findByText("Alexandre · La Maison de la Plaque")).toBeInTheDocument();
    expect(screen.getByText("Por facturar")).toBeInTheDocument();
    expect(screen.getByText("FR · intracomunitario · exento")).toBeInTheDocument();
    expect(screen.getByText("FACTUSOL nº 2760")).toBeInTheDocument();
  });

  it("la línea de vida es VERTICAL: 7 pasos con su dato, ✓ en los hechos y el actual como única tarjeta con «Siguiente paso» y su acción dentro", async () => {
    render(<ErpOrderDetailPage />);
    const list = await screen.findByRole("list", { name: "Ciclo del pedido" });
    expect(list).toHaveClass("erp-flow-steps", "is-vertical");
    const steps = within(list);
    const items = steps.getAllByRole("listitem");
    expect(items).toHaveLength(7);
    // Cada paso lleva su dato (fecha, importe, nº de albarán) y los hechos, ✓.
    expect(steps.getByText("Creado").closest("li")).toHaveTextContent("✓");
    expect(steps.getByText("Pagado").closest("li")).toHaveTextContent("351.52 EUR");
    expect(steps.getByText("Albarán").closest("li")).toHaveTextContent("2-100418");
    // El SAT/«Enviado» ya no está; el hito opcional «Factura enviada» sí, marcado.
    expect(steps.queryByText("Enviado")).toBeNull();
    expect(steps.getByText("Factura enviada").closest("li")).toHaveTextContent("opcional");
    // El actual: numerado, «Paso actual», y DENTRO la barra «Siguiente paso»
    // con el botón de la acción (la misma que ya usaba la ficha).
    const actual = items.find((li) => li.getAttribute("aria-current") === "step");
    expect(actual).toBeDefined();
    expect(actual).toHaveTextContent("Factura");
    expect(actual).toHaveTextContent("Paso actual");
    expect(actual?.querySelector(".erp-flow-step-ic")).toHaveTextContent("5");
    const bar = within(actual as HTMLElement).getByRole("region", { name: "Siguiente paso" });
    expect(bar).toHaveTextContent(/Emite la factura en FACTUSOL/);
    expect(within(bar).getByRole("button", { name: "Emitir factura" })).toBeInTheDocument();
    // Una sola tarjeta azul en toda la pantalla: la del paso actual.
    expect(document.querySelectorAll(".erp-flow-step-body.is-card")).toHaveLength(1);
    expect(actual?.querySelector(".erp-flow-step-body.is-card")).not.toBeNull();
    expect(screen.getAllByRole("region", { name: "Siguiente paso" })).toHaveLength(1);
    // El stepper horizontal de antes ya no existe en la ficha.
    expect(document.querySelector(".erp-flow-steps:not(.is-vertical)")).toBeNull();
  });

  it("la cabecera lleva la barra de 6 segmentos con la lectura rápida «Paso 5 de 6 · Factura» (el hito opcional no cuenta)", async () => {
    render(<ErpOrderDetailPage />);
    const txt = await screen.findByText(/Paso 5 de 6/);
    expect(txt).toHaveTextContent("Paso 5 de 6 · Factura");
    // El hito opcional «Factura enviada» NO sale en la barra de obligatorios.
    const segs = document.querySelectorAll(".erp-flow-progress-seg");
    expect(segs).toHaveLength(6);
    expect(Array.from(segs).map((s) => s.className.replace("erp-flow-progress-seg ", ""))).toEqual([
      "is-done", "is-done", "is-done", "is-done", "is-now", "is-pending",
    ]);
  });

  it("un paso omitido dice por qué (pedido web sin albarán: lo crea WooCommerce)", async () => {
    (getOrder as jest.Mock).mockResolvedValue(detail({
      factusol_albaran_number: null,
      workflow: {
        ...detail().workflow,
        steps: STEPS.map((s) => (s.key === "albaran"
          ? { ...s, state: "skipped", detail: "lo crea WooCommerce" } : s)),
      },
    }));
    render(<ErpOrderDetailPage />);
    const list = await screen.findByRole("list", { name: "Ciclo del pedido" });
    const albaran = within(list).getByText("Albarán").closest("li");
    expect(albaran).toHaveClass("is-skipped");
    expect(albaran).toHaveTextContent("no aplica · lo crea WooCommerce");
    expect(albaran).toHaveTextContent("No aplica");
    expect(document.querySelector(".erp-flow-progress-seg.is-skipped")).not.toBeNull();
  });

  // --- Lote 2 · PR-2: la miga recuerda la cola de la bandeja ---

  it("con ?from=<cola> la miga es «← Bandeja · Por cobrar» y vuelve a esa cola", async () => {
    search = "from=por_cobrar";
    render(<ErpOrderDetailPage />);
    const back = await screen.findByRole("link", { name: "← Bandeja · Por cobrar" });
    expect(back).toHaveAttribute("href", "/erp/orders?queue=por_cobrar");
  });

  it("sin ?from= (o con una cola que no existe) la miga vuelve a la bandeja a secas", async () => {
    const { unmount } = render(<ErpOrderDetailPage />);
    expect(await screen.findByRole("link", { name: "← Bandeja" })).toHaveAttribute("href", "/erp/orders");
    unmount();
    search = "from=lo-que-sea";
    render(<ErpOrderDetailPage />);
    expect(await screen.findByRole("link", { name: "← Bandeja" })).toHaveAttribute("href", "/erp/orders");
    expect(screen.queryByRole("link", { name: /Bandeja · / })).toBeNull();
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
    // «Actividad» pasa a llamarse «Historial»; «Documentos de envío» y
    // «Seguimiento» viven dentro de «Envío y seguimiento» (paneles plegables).
    for (const titulo of [
      "Línea de vida del pedido", "Líneas", "Envío y seguimiento", "Historial", "Seguimiento",
      "FACTUSOL", "Resumen económico",
    ]) {
      expect(screen.getByRole("heading", { name: titulo })).toBeInTheDocument();
    }
    expect(screen.queryByRole("heading", { name: "Actividad" })).toBeNull();
    // Cabecera: idioma del PDF, PDF del pedido, email, «Enviar factura al
    // cliente» (deshabilitado sin factura emitida, nunca oculto), completado
    // y «⋯» (idioma del pedido).
    expect(screen.getByRole("combobox", { name: "Idioma del PDF" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /PDF del pedido \(FACTUSOL\)/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enviar por email" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enviar factura al cliente" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Marcar completado" })).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Más acciones del pedido" }));
    expect(screen.getByRole("combobox", { name: "Idioma del pedido" })).toBeInTheDocument();
    // La antigua entrada «Enviar factura por email» del «⋯» ya no existe:
    // es el botón de la cabecera.
    expect(screen.queryByRole("button", { name: /Enviar factura por email/ })).toBeNull();
    // Las transiciones de estado siguen (fila compacta), no las 4 tarjetas.
    const estados = screen.getByRole("region", { name: "Otras acciones de estado" });
    expect(estados).toHaveTextContent("Preparación");
    expect(within(estados).getByRole("button", { name: "Empezar preparación" })).toBeInTheDocument();
    // «Crear envío» (transport → label_created) ya NO se pinta: subir la
    // etiqueta es el envío (Lote 2 C). El arco sigue en el backend.
    expect(within(estados).queryByRole("button", { name: "Crear envío" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Crear envío" })).toBeNull();
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
    expect(screen.getAllByRole("button", { name: "Enviar factura al cliente" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /PDF del pedido/ })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /Registrar cobro/ })).toHaveLength(1);
    // El nº de albarán aparece en el stepper y en el bloque FACTUSOL como
    // dato; el albarán como DOCUMENTO (PDF / crear) solo en Documentos de envío.
    expect(screen.queryByText("Albarán y pago FACTUSOL")).toBeNull();
    expect(screen.queryByText("Cobro FACTUSOL:")).toBeNull();
  });

  // --- «Enviar factura al cliente» (factura FACTUSOL en PDF, desde la ficha) ---

  it("«Enviar factura al cliente»: con factura emitida localiza la factura del pedido y abre la previsualización (nunca envía sola)", async () => {
    const { getOrderFactusolInvoiceRef } = jest.requireMock("../../../lib/erpApi");
    (getOrderFactusolInvoiceRef as jest.Mock).mockResolvedValue({
      order_id: "o-1", serie: 5, codigo: 260063, numero: "5-260063",
    });
    (getOrder as jest.Mock).mockResolvedValue(detail({
      factusol_invoice_number: "260063", invoice_status: "invoiced_by_erp",
    }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    // Hay dos «Enviar factura al cliente» (la cabecera y el hito «Factura
    // enviada» de la línea de vida): el de la cabecera es el primero.
    const btns = await screen.findAllByRole("button", { name: "Enviar factura al cliente" });
    const btn = btns[0];
    expect(btn).toBeEnabled();
    // Está junto a «Enviar por email» (SAT), en la misma cabecera.
    expect(screen.getByRole("button", { name: "Enviar por email" })).toBeInTheDocument();
    await user.click(btn);
    // Resuelve la factura del PEDIDO (no una cualquiera) y abre el modal de
    // previsualización — que es quien pide confirmación antes de enviar —
    // pasándole ESTE pedido (#426: nunca el contacto de un homónimo).
    await waitFor(() => expect(getOrderFactusolInvoiceRef).toHaveBeenCalledWith("o-1"));
    const dialog = await screen.findByRole("dialog", { name: "Enviar factura por email" });
    expect(dialog).toHaveTextContent("factura:5-260063 pedido:o-1");
    // Dos accesos a la MISMA acción (misma previsualización): el de la cabecera
    // y el incrustado en el hito «Factura enviada» de la línea de vida.
    expect(screen.getAllByRole("button", { name: "Enviar factura al cliente" })).toHaveLength(2);
  });

  it("«Enviar factura al cliente» sin factura emitida: deshabilitado y no consulta FACTUSOL", async () => {
    const { getOrderFactusolInvoiceRef } = jest.requireMock("../../../lib/erpApi");
    (getOrderFactusolInvoiceRef as jest.Mock).mockClear();
    render(<ErpOrderDetailPage />);
    const btn = await screen.findByRole("button", { name: "Enviar factura al cliente" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "Emite la factura en FACTUSOL primero");
    expect(getOrderFactusolInvoiceRef).not.toHaveBeenCalled();
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

  // --- Lote 2 C: «Crear envío» → «Subir etiqueta» ---

  it("«Subir etiqueta» como siguiente paso abre el selector de la etiqueta (no dispara «Crear envío») y, subida, la ficha recarga", async () => {
    const { fireTransition } = jest.requireMock("../../../lib/erpApi");
    (fireTransition as jest.Mock).mockClear();
    (getOrder as jest.Mock).mockResolvedValue(conSiguientePaso({
      queue: "por_enviar", queue_label: "Por enviar",
      next_action: "crear_envio", next_action_label: "Subir etiqueta",
      next_action_hint: "Sube la etiqueta de envío (o marca el pedido como recogido).",
    }, { preparation_status: "packed" }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    const bar = within(await screen.findByRole("region", { name: "Siguiente paso" }));
    expect(bar.getByText(/Sube la etiqueta de envío/)).toBeInTheDocument();
    expect(screen.getByText("etiqueta:0")).toBeInTheDocument();
    await user.click(bar.getByRole("button", { name: "Subir etiqueta" }));
    // El botón pide al panel «Documentos de envío» que abra su selector…
    expect(await screen.findByText("etiqueta:1")).toBeInTheDocument();
    // …y NADIE dispara ya la transición «Crear envío» a secas: ni la barra
    // ni la fila de estados (el arco sigue en el backend, oculto).
    expect(screen.queryByRole("button", { name: "Crear envío" })).toBeNull();
    expect(fireTransition).not.toHaveBeenCalled();
    // Subida la etiqueta, la ficha vuelve a leer el pedido: el stepper y el
    // «Siguiente paso» reflejan «Etiqueta creada».
    const antes = (getOrder as jest.Mock).mock.calls.length;
    await user.click(screen.getByRole("button", { name: "simular etiqueta subida" }));
    await waitFor(() => expect((getOrder as jest.Mock).mock.calls.length).toBeGreaterThan(antes));
  });

  it("con la etiqueta ya subida (label_created) el siguiente paso es la transición de transporte que toca y la fila no la repite", async () => {
    const { fireTransition } = jest.requireMock("../../../lib/erpApi");
    (fireTransition as jest.Mock).mockResolvedValue(detail());
    (getOrder as jest.Mock).mockResolvedValue(conSiguientePaso({
      queue: "por_enviar", queue_label: "Por enviar",
      next_action: "crear_envio", next_action_label: "Marcar recogido",
      next_action_hint: "Etiqueta subida: marca el pedido como recogido cuando salga.",
    }, {
      preparation_status: "packed", transport_status: "label_created",
      available_transitions: {
        payment: [], invoice: [], preparation: [],
        transport: [{ to_status: "in_transit", label: "Recogido / en tránsito", required_evidence: [] }],
      },
    }));
    const user = userEvent.setup();
    render(<ErpOrderDetailPage />);
    const bar = within(await screen.findByRole("region", { name: "Siguiente paso" }));
    // Las demás transiciones de transporte se conservan, y una sola vez.
    expect(screen.getAllByRole("button", { name: "Recogido / en tránsito" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Subir etiqueta" })).toBeNull();
    await user.click(bar.getByRole("button", { name: "Recogido / en tránsito" }));
    await waitFor(() => expect(fireTransition).toHaveBeenCalledWith(
      "o-1", expect.objectContaining({ domain: "transport", to_status: "in_transit" }),
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

describe("ERP · Ficha del pedido — reequilibrio de columnas (#1)", () => {
  it("«Líneas» y «Envío y seguimiento» pasan a la izquierda; resumen económico, FACTUSOL e historial a la derecha; nada se pierde", async () => {
    render(<ErpOrderDetailPage />);
    await screen.findByRole("heading", { name: "Líneas" });
    const main = document.querySelector(".erp-ficha-main") as HTMLElement;
    const side = document.querySelector(".erp-ficha-side") as HTMLElement;
    expect(document.querySelector(".erp-ficha-cols")).not.toBeNull();
    expect(main).not.toBeNull();
    expect(side).not.toBeNull();
    // Izquierda: la línea de vida y los dos paneles más altos.
    for (const name of ["Línea de vida del pedido", "Líneas", "Envío y seguimiento"]) {
      expect(within(main).getByRole("heading", { name })).toBeInTheDocument();
    }
    // Derecha: resumen económico, FACTUSOL e historial.
    for (const name of ["Resumen económico", "FACTUSOL", "Historial"]) {
      expect(within(side).getByRole("heading", { name })).toBeInTheDocument();
    }
  });
});

describe("ERP · Ficha del pedido — factura enviada al cliente visible (#8)", () => {
  const facturaDone = STEPS.map((s) => (s.key === "factura"
    ? { ...s, state: "done", detail: "5-260063" }
    : s.key === "cobro" ? { ...s, state: "now" } : s));

  function facturado(over = {}) {
    return detail({
      invoice_status: "invoiced_by_erp", factusol_invoice_number: "260063",
      workflow: {
        ...detail().workflow, queue: "por_cobrar", queue_label: "Por cobrar",
        next_action: "registrar_cobro", next_action_label: "Registrar cobro",
        next_action_hint: "El pedido consta pagado: registra el cobro en FACTUSOL.",
        steps: facturaDone,
      },
      ...over,
    });
  }

  it("con invoice_emailed_at: el hito «Factura enviada» dice «Factura enviada al cliente el DD/MM/AAAA» (destinatarios en el tooltip)", async () => {
    (getOrder as jest.Mock).mockResolvedValue(facturado({
      invoice_emailed_at: "2026-09-12T10:30:00Z",
      invoice_emailed_to: ["cliente@example.com", "copia@example.com"],
    }));
    render(<ErpOrderDetailPage />);
    const list = await screen.findByRole("list", { name: "Ciclo del pedido" });
    const hito = within(list).getByText("Factura enviada").closest("li") as HTMLElement;
    expect(hito).toHaveTextContent("Factura enviada al cliente el 12/09/2026");
    const nota = within(hito).getByText(/Factura enviada al cliente el/);
    expect(nota).toHaveAttribute("title", expect.stringContaining("cliente@example.com"));
  });

  it("con factura pero sin enviar: el hito «Factura enviada» dice «Factura sin enviar al cliente» y ofrece «Enviar factura al cliente»", async () => {
    (getOrder as jest.Mock).mockResolvedValue(facturado({ invoice_emailed_at: null }));
    render(<ErpOrderDetailPage />);
    const list = await screen.findByRole("list", { name: "Ciclo del pedido" });
    const hito = within(list).getByText("Factura enviada").closest("li") as HTMLElement;
    expect(hito).toHaveTextContent("Factura sin enviar al cliente");
    expect(within(hito).getByRole("button", { name: "Enviar factura al cliente" })).toBeInTheDocument();
    expect(within(hito).queryByText(/Factura enviada al cliente/)).toBeNull();
  });

  it("sin factura todavía NO se muestra el indicador (el paso va de emitir, no de enviar)", async () => {
    // El pedido por defecto no tiene factura (beforeEach → detail()).
    render(<ErpOrderDetailPage />);
    const list = await screen.findByRole("list", { name: "Ciclo del pedido" });
    const factura = within(list).getByText("Factura").closest("li") as HTMLElement;
    expect(within(factura).queryByText(/Sin enviar al cliente/)).toBeNull();
    expect(within(factura).queryByText(/Factura enviada al cliente/)).toBeNull();
  });
});
