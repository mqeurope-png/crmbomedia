import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SatReadyCard } from "./SatReadyCard";
import type { SatQueueItem } from "../../lib/erpApi";
import {
  downloadOrderFactusolAlbaranPdf,
  fetchAlbaranFromWoo,
  fireTransition,
  listShippingFiles,
  markPickedUp,
  openShippingFile,
  saveBlob,
  uploadShippingFile,
} from "../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));

jest.mock("../../lib/erpApi", () => ({
  // customerLabel es helper puro: se usa el real (D-2).
  customerLabel: jest.requireActual("../../lib/erpApi").customerLabel,
  downloadOrderFactusolAlbaranPdf: jest.fn(),
  fetchAlbaranFromWoo: jest.fn(),
  fireTransition: jest.fn(),
  listShippingFiles: jest.fn(),
  markPickedUp: jest.fn(),
  openShippingFile: jest.fn(),
  saveBlob: jest.fn(),
  uploadShippingFile: jest.fn(),
  STATUS_LABELS: {},
}));
const mockFetch = fetchAlbaranFromWoo as jest.Mock;
const mockFire = fireTransition as jest.Mock;
const mockList = listShippingFiles as jest.Mock;
const mockPicked = markPickedUp as jest.Mock;
const mockOpen = openShippingFile as jest.Mock;
const mockFactusolPdf = downloadOrderFactusolAlbaranPdf as jest.Mock;
const mockSave = saveBlob as jest.Mock;
const mockUpload = uploadShippingFile as jest.Mock;

/** Pedido MANUAL embalado con albarán subido a mano y etiqueta. */
function order(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return {
    id: "o1", order_number: "BOP-1", contact_name: null, company_name: null,
    preparation_status: "packed",
    transport_status: "not_shipped", payment_status: "paid",
    total_amount: 100, currency: "EUR", lines: [],
    has_albaran: true, albaran_source: "file", has_albaran_file: true,
    albaran_file_source: "manual_upload", is_web_order: false,
    woo_albaran_available: false, woo_albaran_unavailable_reason: null,
    has_etiqueta: true, ...over,
  };
}

/** Pedido WEB embalado sin fichero aún (Lote 2 A3: el caso ARTISJ-9553). */
function webOrder(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return order({
    order_number: "ARTISJ-9553", store_slug: "artisjet", is_web_order: true,
    has_albaran: true, albaran_source: "woo", has_albaran_file: false,
    albaran_file_source: null, woo_albaran_available: true, ...over,
  });
}

const FILE = {
  id: "f1", kind: "albaran" as const, source: "woo_pdf_plugin" as const,
  filename: "a.pdf", mime_type: "application/pdf", size_bytes: 1,
  uploaded_by_user_id: null, uploaded_at: null, download_url: "/x",
};

beforeEach(() => {
  mockFetch.mockReset();
  mockFire.mockReset();
  mockList.mockReset();
  mockPicked.mockReset();
  mockOpen.mockReset();
  mockFactusolPdf.mockReset();
  mockSave.mockReset();
  mockUpload.mockReset();
  mockList.mockResolvedValue([]);
});

describe("SatReadyCard", () => {
  it("con albarán en FACTUSOL descarga ESE PDF, no el fichero subido", async () => {
    const blob = new Blob(["%PDF-"], { type: "application/pdf" });
    mockFactusolPdf.mockResolvedValue(blob);
    const user = userEvent.setup();
    render(
      <SatReadyCard
        order={order({ factusol_albaran_number: "1-100327", albaran_source: "factusol" })}
        onChanged={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Imprimir albarán/ }));
    await waitFor(() => expect(mockFactusolPdf).toHaveBeenCalledWith("o1"));
    await waitFor(() =>
      expect(mockSave).toHaveBeenCalledWith(blob, "Albaran_1-100327.pdf"),
    );
    expect(mockList).not.toHaveBeenCalledWith("o1", "albaran");
  });

  it("con albarán/etiqueta muestra «Imprimir» y abre el PDF al pulsar", async () => {
    mockList.mockResolvedValue([{
      id: "f1", kind: "albaran", source: "manual_upload", filename: "a.pdf",
      mime_type: "application/pdf", size_bytes: 1, uploaded_by_user_id: null,
      uploaded_at: null, download_url: "/x",
    }]);
    const user = userEvent.setup();
    render(<SatReadyCard order={order()} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /Imprimir albarán/ }));
    await waitFor(() => expect(mockList).toHaveBeenCalledWith("o1", "albaran"));
    await waitFor(() => expect(mockOpen).toHaveBeenCalled());
  });

  // --- Lote 4 · #5: subir la etiqueta desde la propia Cola SAT ---------------

  it("embalado sin etiqueta: la sube desde la cola (mismo flujo que la ficha) y deja de faltar", async () => {
    // Reutiliza el helper `uploadShippingFile` (no reimplementa la subida): el
    // backend guarda el fichero y aplica el arco `not_shipped → label_created`.
    mockUpload.mockResolvedValue({
      file: { ...FILE, kind: "etiqueta" }, transition_applied: true,
      transport_status: "label_created", transition_reason: null,
    });
    const onChanged = jest.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <SatReadyCard order={order({ has_etiqueta: false })} onChanged={onChanged} />,
    );
    // Sin etiqueta ya NO se manda a la ficha: se sube aquí mismo.
    expect(screen.queryByRole("link", { name: /etiqueta/i })).not.toBeInTheDocument();
    const pdf = new File(["%PDF-"], "gls.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText(/Subir etiqueta/), pdf);
    await waitFor(() => expect(mockUpload).toHaveBeenCalledWith("o1", "etiqueta", pdf));
    // Refresca la cola (has_etiqueta pasará a true en el backend).
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    // Con la etiqueta ya presente la card la imprime; no queda «Subir etiqueta».
    rerender(<SatReadyCard order={order({ has_etiqueta: true })} onChanged={onChanged} />);
    expect(screen.getByRole("button", { name: /Imprimir etiqueta/ })).toBeInTheDocument();
    expect(screen.queryByLabelText(/Subir etiqueta/)).not.toBeInTheDocument();
  });

  it("pedido manual sin albarán muestra «Falta albarán» enlazando a la ficha", () => {
    render(
      <SatReadyCard
        order={order({
          has_albaran: false, albaran_source: null, has_albaran_file: false,
          albaran_file_source: null,
        })}
        onChanged={() => {}}
      />,
    );
    const link = screen.getByRole("link", { name: /Falta albarán/ });
    expect(link).toHaveAttribute("href", "/erp/orders/o1");
    expect(link).toHaveClass("warn");
  });

  // --- Lote 2 A3: pedidos web → albarán de WooCommerce -----------------------

  it("pedido web embalado sin fichero: «Descargar albarán» de Woo, nunca «Falta albarán»", async () => {
    mockFetch.mockResolvedValue({ file: FILE, already_present: false });
    const onChanged = jest.fn();
    const user = userEvent.setup();
    render(<SatReadyCard order={webOrder()} onChanged={onChanged} />);
    expect(screen.queryByText(/Falta albarán/)).not.toBeInTheDocument();
    const chip = screen.getByRole("button", { name: /Descargar albarán/ });
    expect(chip).toHaveAttribute("title", expect.stringMatching(/WooCommerce.*artisjet/));
    await user.click(chip);
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith("o1"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    await waitFor(() => expect(mockOpen).toHaveBeenCalledWith(FILE));
    expect(mockFactusolPdf).not.toHaveBeenCalled();
    // La etiqueta sigue con su chip propio.
    expect(screen.getByRole("button", { name: /Imprimir etiqueta/ })).toBeInTheDocument();
  });

  it("si la descarga de Woo falla lo dice bajo la card sin mandar a FACTUSOL", async () => {
    mockFetch.mockRejectedValue(new Error("502"));
    const user = userEvent.setup();
    render(<SatReadyCard order={webOrder()} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /Descargar albarán/ }));
    const aviso = await screen.findByText(/No se pudo descargar el albarán de WooCommerce/);
    expect(aviso).not.toHaveTextContent(/FACTUSOL/);
  });

  it("pedido web sin descarga posible: chip «no disponible» con motivo, a la ficha", () => {
    const reason = "Falta el id del pedido en WooCommerce.";
    render(
      <SatReadyCard
        order={webOrder({
          has_albaran: false, albaran_source: null, woo_albaran_available: false,
          woo_albaran_unavailable_reason: reason,
        })}
        onChanged={() => {}}
      />,
    );
    const link = screen.getByRole("link", { name: /Albarán de WooCommerce no disponible/ });
    expect(link).toHaveAttribute("href", "/erp/orders/o1");
    // El motivo se ve junto al chip (en la tablet no hay tooltip).
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.queryByText(/Falta albarán/)).not.toBeInTheDocument();
  });

  it("«Marcar recogido» pide confirmación y llama markPickedUp", async () => {
    mockPicked.mockResolvedValue({ order_id: "o1", transport_status: "in_transit", already_picked_up: false });
    const onChanged = jest.fn();
    const user = userEvent.setup();
    render(<SatReadyCard order={order()} onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: /Marcar recogido/ }));
    // Confirmación antes de disparar.
    await user.click(screen.getByRole("button", { name: "Sí, recogido" }));
    await waitFor(() => expect(mockPicked).toHaveBeenCalledWith("o1"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("«Reabrir preparación» dispara la transición a in_queue", async () => {
    mockFire.mockResolvedValue({});
    const user = userEvent.setup();
    render(<SatReadyCard order={order()} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Reabrir preparación" }));
    await waitFor(() => expect(mockFire).toHaveBeenCalledWith(
      "o1", expect.objectContaining({ domain: "preparation", to_status: "in_queue" }),
    ));
  });

  // --- Lote 2 · PR-2: diseño de taller (revisión §8) -------------------------

  it("observaciones arriba en ámbar (solo si hay), datos técnicos con «copiar» y acciones de 48 px en filas", async () => {
    const user = userEvent.setup();
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const { container, rerender } = render(
      <SatReadyCard
        order={order({ notes: "Avisar antes de enviar.", whiterip_license: "WR-4C-88231" })}
        onChanged={() => {}}
      />,
    );
    const note = screen.getByRole("note", { name: "Observaciones del comercial" });
    expect(note).toHaveTextContent("Avisar antes de enviar.");
    const tech = container.querySelector(".sat-tech");
    expect(note.compareDocumentPosition(tech as Element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("WR-4C-88231")).toHaveClass("sat-tech-value");
    await user.click(screen.getByRole("button", { name: "Copiar licencia WhiteRIP" }));
    expect(writeText).toHaveBeenCalledWith("WR-4C-88231");
    expect(await screen.findByText("Copiado")).toBeInTheDocument();
    // Primario solo y ancho; los dos documentos debajo, juntos; reabrir aparte.
    const pick = screen.getByRole("button", { name: /Marcar recogido/ });
    expect(pick).toHaveClass("button", "lg");
    expect(pick.closest(".sat-card-actions-primary")).not.toBeNull();
    const alb = screen.getByRole("button", { name: /Imprimir albarán/ });
    const etq = screen.getByRole("button", { name: /Imprimir etiqueta/ });
    expect(alb).toHaveClass("sat-chip-btn", "lg");
    expect(etq).toHaveClass("sat-chip-btn", "lg");
    expect(alb.closest(".sat-card-actions-secondary")).toBe(etq.closest(".sat-card-actions-secondary"));
    const reopen = screen.getByRole("button", { name: "Reabrir preparación" });
    expect(reopen).toHaveClass("button", "tertiary", "lg");
    expect(reopen.closest(".sat-card-actions-tertiary")).not.toBeNull();
    // Sin nota, sin bloque; las cajas técnicas quedan con «—».
    rerender(<SatReadyCard order={order()} onChanged={() => {}} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(3);
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
  });
});
