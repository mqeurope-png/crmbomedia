import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SatPreparingCard } from "./SatPreparingCard";
import type { SatQueueItem } from "../../lib/erpApi";
import {
  downloadOrderFactusolAlbaranPdf,
  fetchAlbaranFromWoo,
  listShippingFiles,
  openShippingFile,
  saveBlob,
  updateSeguimientoFields,
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
  listShippingFiles: jest.fn(),
  openShippingFile: jest.fn(),
  saveBlob: jest.fn(),
  updateSeguimientoFields: jest.fn(),
  STATUS_LABELS: {},
}));
const mockFetch = fetchAlbaranFromWoo as jest.Mock;
const mockUpdateSeg = updateSeguimientoFields as jest.Mock;
const mockList = listShippingFiles as jest.Mock;
const mockOpen = openShippingFile as jest.Mock;
const mockFactusolPdf = downloadOrderFactusolAlbaranPdf as jest.Mock;
const mockSave = saveBlob as jest.Mock;

/** Pedido MANUAL sin albarán (ni en FACTUSOL ni subido). */
function order(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return {
    id: "o1", order_number: "BOP-1", contact_name: null, company_name: null,
    preparation_status: "preparing",
    transport_status: "not_shipped", payment_status: "paid",
    total_amount: 100, currency: "EUR", lines: [],
    has_albaran: false, albaran_source: null, has_albaran_file: false,
    albaran_file_source: null, is_web_order: false, woo_albaran_available: false,
    woo_albaran_unavailable_reason: null, has_etiqueta: false, ...over,
  };
}

/** Pedido WEB (Lote 2 A3): su albarán lo genera WooCommerce, aún sin bajar. */
function webOrder(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return order({
    order_number: "ARTISJ-9553", store_slug: "artisjet", is_web_order: true,
    has_albaran: true, albaran_source: "woo", woo_albaran_available: true, ...over,
  });
}

const FILE = {
  id: "f1", kind: "albaran" as const, source: "woo_pdf_plugin" as const,
  filename: "a.pdf", mime_type: "application/pdf", size_bytes: 1,
  uploaded_by_user_id: null, uploaded_at: null, download_url: "/x",
};

beforeEach(() => {
  mockFetch.mockReset();
  mockList.mockReset();
  mockOpen.mockReset();
  mockFactusolPdf.mockReset();
  mockSave.mockReset();
  mockUpdateSeg.mockReset();
  mockList.mockResolvedValue([]);
});

afterEach(() => {
  Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
});

// --- Lote 2 · PR-2: diseño de taller (revisión §8) ---------------------------

describe("SatPreparingCard · Lote 2 PR-2", () => {
  it("las observaciones del comercial van arriba, en ámbar, y solo si hay nota", () => {
    const { container, rerender } = render(
      <SatPreparingCard
        order={order({ notes: "Cliente pide embalaje reforzado.", serial_number: "FLX-1" })}
        onChanged={() => {}}
      />,
    );
    const note = screen.getByRole("note", { name: "Observaciones del comercial" });
    expect(note).toHaveClass("sat-obs");
    expect(note).toHaveTextContent("Cliente pide embalaje reforzado.");
    // Antes de los datos técnicos y de las líneas.
    const tech = container.querySelector(".sat-tech");
    const lines = container.querySelector(".sat-card-lines");
    expect(tech).not.toBeNull();
    expect(note.compareDocumentPosition(tech as Element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(note.compareDocumentPosition(lines as Element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Sin nota (o solo espacios) el bloque no aparece.
    rerender(<SatPreparingCard order={order({ notes: "   " })} onChanged={() => {}} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("datos técnicos en su caja, en mono, con «copiar» que copia el nº de serie; sin dato «—»", async () => {
    const user = userEvent.setup();
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(
      <SatPreparingCard
        order={order({ serial_number: "FLX-7741-2026", whiterip_license: null, shipping_origin: "SAT" })}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByText("FLX-7741-2026")).toHaveClass("sat-tech-value");
    await user.click(screen.getByRole("button", { name: "Copiar nº de serie" }));
    expect(writeText).toHaveBeenCalledWith("FLX-7741-2026");
    expect(await screen.findByText("Copiado")).toBeInTheDocument();
    // La caja de licencia se pinta igual (layout estable) con «—» y sin botón.
    expect(screen.getByText("Licencia WhiteRIP")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copiar licencia WhiteRIP" })).not.toBeInTheDocument();
    expect(screen.getByText("SAT")).toHaveClass("sat-origin-pill");
    // Copiar no navega ni dispara el albarán.
    expect(mockFetch).not.toHaveBeenCalled();
  });

  // --- Lote 3: edición inline de los datos técnicos sin salir de la cola -----

  it("edición inline del nº de serie: PATCH del campo y valor actualizado (canEdit)", async () => {
    const user = userEvent.setup();
    mockUpdateSeg.mockResolvedValue({
      serial_number: "FLX-NEW", whiterip_license: null, shipping_origin: null,
    });
    render(
      <SatPreparingCard
        order={order({ serial_number: null })} onChanged={() => {}} canEdit origins={["OFI", "SAT"]}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Editar nº de serie" }));
    await user.type(screen.getByRole("textbox", { name: "Editar nº de serie" }), "FLX-NEW");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(mockUpdateSeg).toHaveBeenCalledWith("o1", { serial_number: "FLX-NEW" }),
    );
    // Actualización optimista: el nuevo valor se ve sin recargar y ya se copia.
    expect(await screen.findByText("FLX-NEW")).toHaveClass("sat-tech-value");
    expect(screen.getByRole("button", { name: "Copiar nº de serie" })).toBeInTheDocument();
  });

  it("edición inline del origen: combobox con el catálogo y PATCH shipping_origin", async () => {
    const user = userEvent.setup();
    mockUpdateSeg.mockResolvedValue({
      serial_number: null, whiterip_license: null, shipping_origin: "SAT",
    });
    render(
      <SatPreparingCard
        order={order()} onChanged={() => {}} canEdit origins={["OFI", "TER", "SAT"]}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Editar origen del envío" }));
    const input = screen.getByRole("combobox", { name: "Editar origen del envío" });
    expect(input).toHaveAttribute("list"); // desplegable del catálogo de orígenes
    await user.type(input, "SAT");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(mockUpdateSeg).toHaveBeenCalledWith("o1", { shipping_origin: "SAT" }),
    );
    expect(await screen.findByText("SAT")).toHaveClass("sat-origin-pill");
  });

  it("«Cancelar» descarta el cambio sin llamar al PATCH", async () => {
    const user = userEvent.setup();
    render(
      <SatPreparingCard order={order({ serial_number: "FLX-1" })} onChanged={() => {}} canEdit />,
    );
    await user.click(screen.getByRole("button", { name: "Editar nº de serie" }));
    const input = screen.getByRole("textbox", { name: "Editar nº de serie" });
    await user.clear(input);
    await user.type(input, "OTRO");
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(mockUpdateSeg).not.toHaveBeenCalled();
    expect(screen.getByText("FLX-1")).toHaveClass("sat-tech-value");
  });

  it("sin canEdit los datos técnicos siguen siendo de solo lectura", () => {
    render(<SatPreparingCard order={order({ serial_number: "FLX-1" })} onChanged={() => {}} />);
    expect(screen.getByText("FLX-1")).toHaveClass("sat-tech-value");
    expect(screen.queryByRole("button", { name: /Editar/ })).not.toBeInTheDocument();
  });

  it("tres acciones de 48 px en dos filas: abrir (primario, ancho) y debajo albarán + ficha", () => {
    const { container } = render(<SatPreparingCard order={order()} onChanged={() => {}} />);
    const open = screen.getByRole("link", { name: /Abrir modo trabajo/ });
    expect(open).toHaveAttribute("href", "/erp/sat/o1");
    expect(open).toHaveClass("button", "lg");
    expect(open.closest(".sat-card-actions-primary")).not.toBeNull();
    const chip = screen.getByRole("button", { name: /Falta albarán/ });
    expect(chip).toHaveClass("sat-chip-btn", "lg");
    const ficha = screen.getByRole("link", { name: "Ficha" });
    expect(ficha).toHaveAttribute("href", "/erp/orders/o1");
    expect(ficha).toHaveClass("button", "secondary", "lg");
    expect(chip.closest(".sat-card-actions-secondary")).toBe(ficha.closest(".sat-card-actions-secondary"));
    // La card ya no es un enlace entero (con botones dentro se pulsaba el equivocado).
    expect(container.querySelector("a.sat-card")).toBeNull();
  });
});

describe("SatPreparingCard", () => {
  it("muestra el nombre del cliente bajo el número de pedido (D-2)", () => {
    render(
      <SatPreparingCard
        order={order({ contact_name: "Ana Pi", company_name: "Duplicoder SL" })}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByText("Ana Pi · Duplicoder SL")).toBeInTheDocument();
    // El número sigue visible (es lo que escanea el operativo).
    expect(screen.getByText("BOP-1")).toBeInTheDocument();
  });

  it("sin cliente no pinta la línea de cliente", () => {
    render(<SatPreparingCard order={order()} onChanged={() => {}} />);
    expect(screen.queryByText(/·/)).not.toBeInTheDocument();
  });

  it("con albarán guardado muestra «Imprimir albarán» y abre el PDF", async () => {
    mockList.mockResolvedValue([FILE]);
    const user = userEvent.setup();
    render(
      <SatPreparingCard
        order={order({ has_albaran: true, albaran_source: "file", has_albaran_file: true })}
        onChanged={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Imprimir albarán/ }));
    await waitFor(() => expect(mockList).toHaveBeenCalledWith("o1", "albaran"));
    await waitFor(() => expect(mockOpen).toHaveBeenCalled());
  });

  it("con albarán en FACTUSOL descarga ESE PDF (no el fichero subido ni Woo)", async () => {
    const blob = new Blob(["%PDF-"], { type: "application/pdf" });
    mockFactusolPdf.mockResolvedValue(blob);
    const user = userEvent.setup();
    render(
      <SatPreparingCard
        order={order({
          factusol_albaran_number: "1-100327", has_albaran: true, albaran_source: "factusol",
        })}
        onChanged={() => {}}
      />,
    );
    // Con albarán de FACTUSOL el chip ya no dice «Descargar»: hay documento.
    await user.click(screen.getByRole("button", { name: /Imprimir albarán/ }));
    await waitFor(() => expect(mockFactusolPdf).toHaveBeenCalledWith("o1"));
    await waitFor(() =>
      expect(mockSave).toHaveBeenCalledWith(blob, "Albaran_1-100327.pdf"),
    );
    // No se toca el flujo antiguo.
    expect(mockFetch).not.toHaveBeenCalled();
    expect(mockList).not.toHaveBeenCalled();
  });

  it("el albarán de FACTUSOL manda sobre el fichero subido a mano", async () => {
    mockFactusolPdf.mockResolvedValue(new Blob(["%PDF-"]));
    const user = userEvent.setup();
    render(
      <SatPreparingCard
        order={order({
          factusol_albaran_number: "1-100327", has_albaran: true, albaran_source: "factusol",
          has_albaran_file: true, albaran_file_source: "manual_upload",
        })}
        onChanged={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Imprimir albarán/ }));
    await waitFor(() => expect(mockFactusolPdf).toHaveBeenCalled());
    expect(mockList).not.toHaveBeenCalled();
  });

  it("si el PDF de FACTUSOL falla lo dice sin romper la card", async () => {
    mockFactusolPdf.mockRejectedValue(new Error("502"));
    const user = userEvent.setup();
    render(
      <SatPreparingCard
        order={order({
          factusol_albaran_number: "1-100327", has_albaran: true, albaran_source: "factusol",
        })}
        onChanged={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Imprimir albarán/ }));
    expect(await screen.findByText(/No se pudo generar el PDF del albarán de FACTUSOL/))
      .toBeInTheDocument();
    expect(mockSave).not.toHaveBeenCalled();
  });

  // --- Lote 2 A3: pedidos manuales sin albarán ------------------------------

  it("pedido manual sin albarán: «Falta albarán», no intenta Woo y al pulsar explica", async () => {
    const user = userEvent.setup();
    render(<SatPreparingCard order={order()} onChanged={() => {}} />);
    const chip = screen.getByRole("button", { name: /Falta albarán/ });
    expect(chip).toHaveClass("warn");
    expect(screen.queryByRole("button", { name: /Descargar albarán/ })).not.toBeInTheDocument();
    await user.click(chip);
    expect(await screen.findByText(/créalo en FACTUSOL o súbelo a mano desde la ficha/))
      .toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Ir a la ficha/ })).toHaveAttribute("href", "/erp/orders/o1");
    // Un pedido manual no viene de Woo: no se dispara la descarga (400 seguro).
    expect(mockFetch).not.toHaveBeenCalled();
  });

  // --- Lote 2 A3: pedidos web → albarán de WooCommerce -----------------------

  it("pedido web sin fichero: chip «Descargar albarán» de Woo, nunca «Falta albarán»", () => {
    render(<SatPreparingCard order={webOrder()} onChanged={() => {}} />);
    const chip = screen.getByRole("button", { name: /Descargar albarán/ });
    expect(chip).toHaveClass("info");
    expect(chip).toHaveAttribute("title", expect.stringMatching(/WooCommerce.*artisjet/));
    expect(screen.queryByText(/Falta albarán/)).not.toBeInTheDocument();
  });

  it("«Descargar albarán» dispara fetch-from-woo, refresca y auto-abre el PDF", async () => {
    mockFetch.mockResolvedValue({ file: FILE, already_present: false });
    const onChanged = jest.fn();
    const user = userEvent.setup();
    render(<SatPreparingCard order={webOrder()} onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: /Descargar albarán/ }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith("o1"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    await waitFor(() => expect(mockOpen).toHaveBeenCalledWith(FILE));
    expect(mockFactusolPdf).not.toHaveBeenCalled();
  });

  it("si la descarga de Woo falla avisa (sin mandar a FACTUSOL) con enlace a la ficha", async () => {
    mockFetch.mockRejectedValue(new Error("502"));
    const user = userEvent.setup();
    render(<SatPreparingCard order={webOrder()} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /Descargar albarán/ }));
    const aviso = await screen.findByRole("status");
    expect(aviso).toHaveTextContent(/No se pudo descargar el albarán de WooCommerce/);
    expect(aviso).toHaveTextContent(/súbelo a mano desde la ficha/);
    // El albarán de un pedido web nunca se crea en FACTUSOL.
    expect(aviso).not.toHaveTextContent(/FACTUSOL/);
    expect(screen.getByRole("link", { name: /Ir a la ficha/ })).toHaveAttribute("href", "/erp/orders/o1");
  });

  it("pedido web ya descargado: «Imprimir albarán» abre el fichero de la tienda", async () => {
    mockList.mockResolvedValue([FILE]);
    const user = userEvent.setup();
    render(
      <SatPreparingCard
        order={webOrder({
          albaran_source: "file", has_albaran_file: true, albaran_file_source: "woo_pdf_plugin",
        })}
        onChanged={() => {}}
      />,
    );
    const chip = screen.getByRole("button", { name: /Imprimir albarán/ });
    expect(chip).toHaveAttribute("title", expect.stringMatching(/WooCommerce/));
    await user.click(chip);
    await waitFor(() => expect(mockList).toHaveBeenCalledWith("o1", "albaran"));
    await waitFor(() => expect(mockOpen).toHaveBeenCalledWith(FILE));
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("pedido web sin descarga posible: «Albarán de WooCommerce no disponible» + motivo", async () => {
    const user = userEvent.setup();
    const reason = "La tienda «artisjet» no tiene configurada la conexión con WooCommerce.";
    render(
      <SatPreparingCard
        order={webOrder({
          has_albaran: false, albaran_source: null, woo_albaran_available: false,
          woo_albaran_unavailable_reason: reason,
        })}
        onChanged={() => {}}
      />,
    );
    const chip = screen.getByRole("button", { name: /Albarán de WooCommerce no disponible/ });
    expect(chip).toHaveClass("warn");
    // El motivo se ve en la card (en la tablet no hay tooltip).
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.queryByText(/Falta albarán/)).not.toBeInTheDocument();
    await user.click(chip);
    expect(await screen.findByRole("status")).toHaveTextContent(
      `Albarán de WooCommerce no disponible: ${reason}`,
    );
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
