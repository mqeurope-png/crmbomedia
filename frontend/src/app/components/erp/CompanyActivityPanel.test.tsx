import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyActivityPanel, buildActivityRows, importe } from "./CompanyActivityPanel";
import { listFactusolDocuments, listFactusolQuotes, listOrders } from "../../lib/erpApi";

/** «Actividad reciente» (Lote 2 · PR-2): las tres listas (pedidos, facturas,
 *  proformas) pasan a UNA tabla de consulta ordenada por fecha, con filtro
 *  por tipo, sin perder nada y diciendo en la propia tabla qué origen falla. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../lib/erpApi", () => ({
  listOrders: jest.fn(),
  listFactusolDocuments: jest.fn(),
  listFactusolQuotes: jest.fn(),
}));

const ORDERS = [
  { id: "o1", order_number: "ARTISJ-9544", placed_at: "2026-09-08T10:00:00", total_amount: 351.52,
    currency: "EUR", workflow: { queue: "por_facturar", queue_label: "Por facturar" } },
  { id: "o2", order_number: "BP-2310", placed_at: "2026-06-14T09:00:00", total_amount: 1150,
    currency: "EUR", cancelled: true, workflow: { queue: "listo", queue_label: "Listo" } },
];
const INVOICES = [
  { numero: "2-526079", fecha: "2026-09-01", total: 120, saldo_pendiente: 0, estado_label: "Cobrada" },
  { numero: "2-526087", fecha: "2026-09-10", total: 4290, saldo_pendiente: 4290, estado_label: "Pendiente" },
];
const QUOTES = [
  { codpre: "2-000071", numero: "2-000071", referencia: "Placas", fecha: "2026-09-04", total: 5059,
    clipre: "2760", cliente_nombre: "LA MAISON", base: 5059, iva: 0,
    queue: "convertidas", estado_label: "Convertida" },
];

beforeEach(() => {
  (listOrders as jest.Mock).mockResolvedValue({ items: ORDERS, queue_counts: {}, queue: null });
  (listFactusolDocuments as jest.Mock).mockResolvedValue({ items: INVOICES, total: 2 });
  (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: QUOTES, unlinked: false });
});

function bodyRows(): HTMLElement[] {
  return Array.from(screen.getByRole("table").querySelectorAll<HTMLElement>("tbody tr"));
}
function firstCells(): (string | null | undefined)[] {
  return bodyRows().map((r) => r.querySelector("td")?.textContent);
}

describe("CompanyActivityPanel · actividad unificada", () => {
  it("una sola tabla Documento · Tipo · Fecha · Importe · Estado con los tres orígenes, por fecha desc", async () => {
    render(<CompanyActivityPanel companyId="c1" factusolCodcli="2760" contactsCount={3} />);
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["Documento", "Tipo", "Fecha", "Importe", "Estado"]);
    await waitFor(() => expect(bodyRows()).toHaveLength(5));
    expect(firstCells()).toEqual([
      "2-526087", "ARTISJ-9544", "2-000071 · Placas", "2-526079", "BP-2310",
    ]);
    const [factura, pedido, proforma, cobrada, anulado] = bodyRows();
    // Pedido: enlace a su ficha, cola del workflow como estado.
    expect(within(pedido).getByRole("link", { name: "ARTISJ-9544" }))
      .toHaveAttribute("href", "/erp/orders/o1");
    expect(within(pedido).getByRole("cell", { name: "Pedido" })).toBeInTheDocument();
    expect(pedido).toHaveTextContent("Por facturar");
    expect(pedido).toHaveTextContent("8/9/26");
    // Factura: sin página propia → número en texto (mono), cobro como estado.
    expect(within(factura).queryByRole("link")).toBeNull();
    expect(factura.querySelector("td .mono")).toHaveTextContent("2-526087");
    expect(factura).toHaveTextContent("Factura");
    expect(factura).toHaveTextContent("Por cobrar");
    expect(cobrada).toHaveTextContent("Cobrada");
    // Proforma: enlace a la pantalla de proformas con su nº y referencia.
    expect(within(proforma).getByRole("link", { name: "2-000071" }))
      .toHaveAttribute("href", "/erp/proformas");
    expect(proforma).toHaveTextContent("Proforma");
    expect(proforma).toHaveTextContent("Convertida");
    // Pedido anulado: se dice, en vez de su cola.
    expect(anulado).toHaveTextContent("Anulado");
    // Importe en td.num (mono a la derecha) y data-label en cada celda para el
    // modo tarjeta (< 768).
    expect(factura.querySelector("td.num")).toHaveTextContent(/4\.?290,00 €/);
    expect(pedido.querySelector("td[data-label='Importe']")).toHaveTextContent("351,52 €");
    expect(pedido.querySelectorAll("td[data-label]")).toHaveLength(5);
    expect(screen.getByText("Contactos").parentElement).toHaveTextContent("3");
    // Hasta 20 por origen.
    expect(listOrders).toHaveBeenCalledWith({ company_id: "c1", limit: 20, show_external: true });
    expect(listFactusolDocuments).toHaveBeenCalledWith("facturas", expect.objectContaining({ codcli: "2760", limit: 20 }));
    expect(listFactusolQuotes).toHaveBeenCalledWith({ company_id: "c1", days_back: 365 });
  });

  it("filtro por tipo: Todo / Pedidos / Facturas / Proformas, con contadores", async () => {
    const user = userEvent.setup();
    render(<CompanyActivityPanel companyId="c1" factusolCodcli="2760" contactsCount={0} />);
    await waitFor(() => expect(bodyRows()).toHaveLength(5));
    const filtro = within(screen.getByRole("group", { name: "Filtrar por tipo" }));
    expect(filtro.getByRole("button", { name: "Todo (5)" })).toHaveAttribute("aria-pressed", "true");
    await user.click(filtro.getByRole("button", { name: "Facturas (2)" }));
    expect(firstCells()).toEqual(["2-526087", "2-526079"]);
    expect(filtro.getByRole("button", { name: "Facturas (2)" })).toHaveAttribute("aria-pressed", "true");
    expect(filtro.getByRole("button", { name: "Todo (5)" })).toHaveAttribute("aria-pressed", "false");
    await user.click(filtro.getByRole("button", { name: "Pedidos (2)" }));
    expect(firstCells()).toEqual(["ARTISJ-9544", "BP-2310"]);
    await user.click(filtro.getByRole("button", { name: "Proformas (1)" }));
    expect(firstCells()).toEqual(["2-000071 · Placas"]);
    await user.click(filtro.getByRole("button", { name: "Todo (5)" }));
    expect(bodyRows()).toHaveLength(5);
  });

  it("FACTUSOL caído: lo dice en la tabla y los pedidos de BoHub siguen saliendo", async () => {
    (listFactusolDocuments as jest.Mock).mockRejectedValue(new Error("FACTUSOL HTTP 502"));
    (listFactusolQuotes as jest.Mock).mockRejectedValue(new Error("FACTUSOL HTTP 502"));
    const user = userEvent.setup();
    render(<CompanyActivityPanel companyId="c1" factusolCodcli="2760" contactsCount={0} />);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "FACTUSOL no responde: faltan las facturas y proformas.",
    );
    expect(await screen.findByRole("link", { name: "ARTISJ-9544" })).toBeInTheDocument();
    // Con el filtro de un tipo, el aviso es el de ese tipo.
    await user.click(screen.getByRole("button", { name: /^Facturas/ }));
    expect(screen.getByRole("status")).toHaveTextContent("FACTUSOL no responde: faltan las facturas.");
    await user.click(screen.getByRole("button", { name: /^Pedidos/ }));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("solo fallan las facturas: aviso solo de facturas; las proformas y los pedidos salen", async () => {
    (listFactusolDocuments as jest.Mock).mockRejectedValue(new Error("FACTUSOL HTTP 502"));
    render(<CompanyActivityPanel companyId="c1" factusolCodcli="2760" contactsCount={0} />);
    expect(await screen.findByRole("status")).toHaveTextContent("FACTUSOL no responde: faltan las facturas.");
    expect(await screen.findByRole("link", { name: "2-000071" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ARTISJ-9544" })).toBeInTheDocument();
    expect(screen.queryByText("2-526087")).toBeNull();
  });

  it("fallan los pedidos de BoHub: se dice y las facturas y proformas siguen", async () => {
    (listOrders as jest.Mock).mockRejectedValue(new Error("500"));
    render(<CompanyActivityPanel companyId="c1" factusolCodcli="2760" contactsCount={0} />);
    expect(await screen.findByRole("status")).toHaveTextContent("No se pudieron leer los pedidos de BoHub.");
    expect(await screen.findByText("2-526087")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "2-000071" })).toBeInTheDocument();
  });

  it("sin cliente FACTUSOL: no consulta facturas ni proformas, y el filtro de esos tipos lo explica", async () => {
    const user = userEvent.setup();
    render(<CompanyActivityPanel companyId="c1" factusolCodcli={null} contactsCount={0} />);
    await waitFor(() => expect(bodyRows()).toHaveLength(2));
    expect(listFactusolDocuments).not.toHaveBeenCalled();
    expect(listFactusolQuotes).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
    await user.click(screen.getByRole("button", { name: /^Facturas/ }));
    expect(screen.getByRole("status")).toHaveTextContent("Sin cliente FACTUSOL vinculado");
    expect(screen.getByText("Sin facturas recientes.")).toBeInTheDocument();
  });

  it("sin nada: lo dice en la tabla", async () => {
    (listOrders as jest.Mock).mockResolvedValue({ items: [], queue_counts: {}, queue: null });
    (listFactusolDocuments as jest.Mock).mockResolvedValue({ items: [], total: 0 });
    (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: [], unlinked: false });
    render(<CompanyActivityPanel companyId="c1" factusolCodcli="2760" contactsCount={0} />);
    expect(await screen.findByText("Sin pedidos, facturas ni proformas recientes.")).toBeInTheDocument();
  });

  it("buildActivityRows: sin fecha al final, y importe al estilo español", () => {
    const rows = buildActivityRows(
      [{ id: "o9", order_number: "X-1", placed_at: null, created_at: "2026-01-02T00:00:00",
         total_amount: 10, currency: "USD" } as never],
      [{ numero: "F-0", fecha: null, total: null, estado_label: "—" } as never],
      [],
    );
    expect(rows.map((r) => r.numero)).toEqual(["X-1", "F-0"]);
    expect(rows[0].fecha).toBe("2026-01-02T00:00:00");   // sin placed_at cae a created_at
    expect(importe(rows[0].importe, rows[0].moneda)).toBe("10,00 USD");
    expect(importe(null)).toBe("—");
    expect(importe(1234.5)).toMatch(/^1\.?234,50 €$/);
  });
});
