import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SatReadyCard, SatShippedCard, satShippedLabel } from "./SatReadyCard";
import type { SatQueueItem } from "../../lib/erpApi";
import {
  downloadOrderFactusolAlbaranPdf,
  fetchAlbaranFromWoo,
  fireTransition,
  listShippingFiles,
  markPickedUp,
  openShippingFile,
  saveBlob,
  setOrderTracking,
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
  setOrderTracking: jest.fn(),
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
const mockSetTracking = setOrderTracking as jest.Mock;

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
    has_etiqueta: true, placed_at: "2026-09-01T10:00:00+00:00", ...over,
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
  mockSetTracking.mockReset();
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
    // Sin Genei: lleva el tracking y el courier del campo (vacíos aquí).
    await waitFor(() => expect(mockPicked).toHaveBeenCalledWith("o1", { tracking: "", courier: "" }));
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
    // Sin nota, sin bloque; las cajas técnicas (solo lectura) quedan con «—».
    // Lote 5 · #2: ya no hay origen, así que solo dos «—» (nº serie y licencia).
    rerender(<SatReadyCard order={order()} onChanged={() => {}} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(2);
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
  });

  // --- Lote 5 · #2/#4/#5: sin origen, fecha y cliente visibles ---------------

  it("#2 · no pinta «Origen» en la card aunque el pedido lo tenga", () => {
    render(<SatReadyCard order={order({ shipping_origin: "SAT" })} onChanged={() => {}} />);
    expect(screen.queryByText("Origen")).not.toBeInTheDocument();
    expect(screen.queryByText("SAT")).not.toBeInTheDocument();
    expect(screen.queryByText(/sat-origin/)).not.toBeInTheDocument();
  });

  it("#4/#5 · la fecha (dd/mm/aaaa), el importe y el cliente se ven en la cabecera", () => {
    const { container } = render(
      <SatReadyCard
        order={order({ contact_name: "Ana Pi", company_name: "Duplicoder SL",
                       placed_at: "2026-09-01T10:00:00+00:00", total_amount: 100 })}
        onChanged={() => {}}
      />,
    );
    expect(container.querySelector(".sat-card-date")).toHaveTextContent(/\d{1,2}\/\d{1,2}\/\d{4}/);
    expect(container.querySelector(".sat-card-amount")).toHaveTextContent("100.00 EUR");
    expect(screen.getByText("Ana Pi · Duplicoder SL")).toHaveClass("sat-card-customer");
  });

  // --- Lote 5 · #3: nº de seguimiento en «Listos» ----------------------------

  it("#3 · guarda el nº de seguimiento con setOrderTracking y refleja el valor guardado", async () => {
    mockSetTracking.mockResolvedValue({ id: "o1", tracking_number: "TRACK-123" });
    const onChanged = jest.fn();
    const user = userEvent.setup();
    render(<SatReadyCard order={order()} onChanged={onChanged} />);
    const input = screen.getByLabelText("Nº de seguimiento");
    await user.type(input, "TRACK-123");
    await user.click(screen.getByRole("button", { name: "Guardar nº de seguimiento" }));
    await waitFor(() => expect(mockSetTracking).toHaveBeenCalledWith("o1", "TRACK-123", ""));
    // Refresca la cola y muestra el estado guardado.
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(input).toHaveValue("TRACK-123");
    expect(await screen.findByRole("button", { name: "Guardar nº de seguimiento" }))
      .toHaveTextContent("Guardado");
  });

  it("#3 · prellena el nº de seguimiento que ya tenía el pedido", () => {
    render(<SatReadyCard order={order({ tracking_number: "PREV-9" })} onChanged={() => {}} />);
    expect(screen.getByLabelText("Nº de seguimiento")).toHaveValue("PREV-9");
  });

  // --- Genei (PR-1 follow-up): crear envío desde la Cola SAT ------------------

  it("con permiso de envío (SAT_SHIPPING) ofrece «Crear envío con Genei»; sin él, no", () => {
    const { rerender } = render(<SatReadyCard order={order()} onChanged={() => {}} canShip />);
    expect(screen.getByRole("button", { name: /Crear envío con Genei/ })).toBeInTheDocument();
    // Sin la capacidad (por defecto) el botón no aparece en la card.
    rerender(<SatReadyCard order={order()} onChanged={() => {}} canShip={false} />);
    expect(screen.queryByRole("button", { name: /Crear envío con Genei/ })).not.toBeInTheDocument();
  });
});

describe("estado REAL del transportista (Genei /tracking)", () => {
  const CTT = {
    shipment_code: "G2", courier: "Ctt Premium", state_bucket: "in_transit",
    state_label: "Recogida efectuada / en tránsito", label_available: true,
    tracking: "0033260080539700026674",
    carrier_status: "PENDIENTE DE ENTRADA EN RED",
    carrier_status_at: "2026-09-24T18:00:00+00:00", carrier_step: "pre_transit",
    tracking_url: "https://www.cttexpress.com/localizador/",
  };

  it("«Pendiente de recogida»: la card enseña el último escaneo y el enlace de la agencia", () => {
    render(<SatReadyCard order={order({ transport_status: "label_created", genei: CTT })}
                         onChanged={() => {}} />);
    const estado = screen.getByLabelText("Estado según el transportista");
    expect(estado).toHaveTextContent("PENDIENTE DE ENTRADA EN RED");
    expect(estado).toHaveTextContent("Ctt Premium");
    expect(screen.getByRole("link", { name: "Ver en la web de la agencia" }))
      .toHaveAttribute("href", "https://www.cttexpress.com/localizador/");
  });

  it("sin escaneos aún, la card no enseña nada del transportista", () => {
    render(<SatReadyCard order={order({ genei: { shipment_code: "G1", label_available: true } })}
                         onChanged={() => {}} />);
    expect(screen.queryByLabelText("Estado según el transportista")).not.toBeInTheDocument();
  });

  it("«Enviados»: la card enseña el texto de la agencia, su fecha y el tracking enlazado", () => {
    render(<SatShippedCard order={order({ transport_status: "in_transit", genei: CTT,
                                          tracking_number: "0033260080539700026674" })} />);
    const badge = screen.getByText("PENDIENTE DE ENTRADA EN RED");
    expect(badge).toHaveClass("badge", "warn");
    expect(screen.queryByText("Recogido · en tránsito")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "0033260080539700026674" }))
      .toHaveAttribute("href", "https://www.cttexpress.com/localizador/");
    expect(screen.getByText("Ctt Premium")).toBeInTheDocument();
  });

  it("orden del texto: transportista › Genei › transporte", () => {
    expect(satShippedLabel(order({ transport_status: "in_transit", genei: CTT })))
      .toBe("PENDIENTE DE ENTRADA EN RED");
    expect(satShippedLabel(order({ transport_status: "in_transit",
      genei: { shipment_code: "G", label_available: true, state_label: "En reparto" } })))
      .toBe("En reparto");
    expect(satShippedLabel(order({ transport_status: "in_transit" })))
      .toBe("Recogido · en tránsito");
    expect(satShippedLabel(order({ sin_envio: true, genei: CTT }))).toBe("No requiere envío");
  });
});


describe("envío con OTRO courier (no Genei)", () => {
  const UPS_TRACK = "1ZFV12345678901234";
  const CTT_TRACK = "0033260080539700026674";

  it("el formato del tracking propone el courier (1Z → UPS) y «Marcar recogido» lo manda", async () => {
    mockPicked.mockResolvedValue({ order_id: "o1", transport_status: "in_transit", already_picked_up: false });
    const user = userEvent.setup();
    render(<SatReadyCard order={order()} onChanged={() => {}} />);
    await user.type(screen.getByLabelText("Nº de seguimiento"), UPS_TRACK);
    expect(screen.getByLabelText("Courier")).toHaveValue("UPS");
    await user.click(screen.getByRole("button", { name: /Marcar recogido/ }));
    await user.click(screen.getByRole("button", { name: "Sí, recogido" }));
    await waitFor(() => expect(mockPicked).toHaveBeenCalledWith(
      "o1", { tracking: UPS_TRACK, courier: "UPS" },
    ));
  });

  it("0033… → CTT Express, pero se puede cambiar (MRW) y se guarda con el tracking", async () => {
    mockSetTracking.mockResolvedValue({ id: "o1", tracking_number: CTT_TRACK, courier: "MRW" });
    const user = userEvent.setup();
    render(<SatReadyCard order={order()} onChanged={() => {}} />);
    await user.type(screen.getByLabelText("Nº de seguimiento"), CTT_TRACK);
    const select = screen.getByLabelText("Courier");
    expect(select).toHaveValue("CTT Express");
    await user.selectOptions(select, "MRW");
    await user.click(screen.getByRole("button", { name: "Guardar nº de seguimiento" }));
    await waitFor(() => expect(mockSetTracking).toHaveBeenCalledWith("o1", CTT_TRACK, "MRW"));
    expect(screen.getByLabelText("Courier")).toHaveValue("MRW");
  });

  it("«Otro…» deja escribir el courier; sin tracking ni courier también se puede recoger", async () => {
    mockPicked.mockResolvedValue({ order_id: "o1", transport_status: "in_transit", already_picked_up: false });
    const user = userEvent.setup();
    render(<SatReadyCard order={order()} onChanged={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Courier"), "Otro…");
    await user.type(screen.getByLabelText("Otro courier"), "Nacex");
    await user.click(screen.getByRole("button", { name: /Marcar recogido/ }));
    await user.click(screen.getByRole("button", { name: "Sí, recogido" }));
    await waitFor(() => expect(mockPicked).toHaveBeenCalledWith(
      "o1", { tracking: "", courier: "Nacex" },
    ));
  });

  it("prellena el courier ya apuntado; un tracking que no delata nada no propone nada", async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <SatReadyCard order={order({ courier: "GLS", shipment_kind: "externo" })} onChanged={() => {}} />,
    );
    expect(screen.getByLabelText("Courier")).toHaveValue("GLS");
    unmount();
    render(<SatReadyCard order={order()} onChanged={() => {}} />);
    await user.type(screen.getByLabelText("Nº de seguimiento"), "ABC123");
    expect(screen.getByLabelText("Courier")).toHaveValue("");
  });

  it("con envío de Genei no hay desplegable de courier y «Marcar recogido» no manda nada", async () => {
    mockPicked.mockResolvedValue({ order_id: "o1", transport_status: "in_transit", already_picked_up: false });
    const user = userEvent.setup();
    render(<SatReadyCard order={order({ genei: { shipment_code: "G1", label_available: true },
                                        shipment_kind: "genei" })}
                         onChanged={() => {}} />);
    expect(screen.queryByLabelText("Courier")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Marcar recogido/ }));
    await user.click(screen.getByRole("button", { name: "Sí, recogido" }));
    await waitFor(() => expect(mockPicked).toHaveBeenCalledWith("o1", {}));
  });

  it("«Pendiente de recogida»: Genei en azul; otro courier en su color y con su nombre", () => {
    const { rerender } = render(
      <SatReadyCard order={order({ sat_tab: "pendiente_recogida", shipment_kind: "genei",
                                   genei: { shipment_code: "G1", label_available: true } })}
                    onChanged={() => {}} />,
    );
    expect(screen.getByText("Pendiente de recogida")).toHaveClass("badge", "info");
    rerender(
      <SatReadyCard order={order({ sat_tab: "pendiente_recogida", shipment_kind: "externo",
                                   courier: "UPS" })}
                    onChanged={() => {}} />,
    );
    expect(screen.getByText("Pendiente de recogida · UPS")).toHaveClass("badge", "courier-ext");
  });

  const UPS_URL = `https://www.ups.com/track?tracknum=${UPS_TRACK}`;

  it("«Enviados»: «Enviado · UPS» en su color, tracking enlazado a UPS, agencia y sin etiqueta Genei", () => {
    render(<SatShippedCard order={order({ transport_status: "in_transit", sat_tab: "enviados",
                                          shipment_kind: "externo", courier: "UPS",
                                          tracking_number: UPS_TRACK, tracking_url: UPS_URL })}
                           onChanged={() => {}} />);
    expect(screen.getByText("Enviado · UPS")).toHaveClass("badge", "courier-ext");
    expect(screen.queryByText("Recogido · en tránsito")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: UPS_TRACK })).toHaveAttribute("href", UPS_URL);
    expect(screen.getByText("UPS")).toBeInTheDocument();
    expect(screen.queryByText("Genei")).not.toBeInTheDocument();
  });

  it("«Enviados»: courier y tracking se corrigen en línea (sin volver a «Marcar recogido»)", async () => {
    mockSetTracking.mockResolvedValue({ id: "o1", tracking_number: UPS_TRACK, courier: "UPS" });
    const onChanged = jest.fn();
    const user = userEvent.setup();
    render(<SatShippedCard order={order({ transport_status: "in_transit", sat_tab: "enviados",
                                          shipment_kind: "externo", courier: null })}
                           onChanged={onChanged} />);
    expect(screen.getByText("Enviado · otro courier")).toHaveClass("badge", "courier-ext");
    await user.click(screen.getByRole("button", { name: "Editar courier y seguimiento de BOP-1" }));
    const editor = screen.getByRole("group", { name: "Courier y seguimiento" });
    await user.type(within(editor).getByLabelText("Nº de seguimiento"), UPS_TRACK);
    expect(within(editor).getByLabelText("Courier")).toHaveValue("UPS");
    await user.click(within(editor).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(mockSetTracking).toHaveBeenCalledWith("o1", UPS_TRACK, "UPS"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(screen.queryByRole("group", { name: "Courier y seguimiento" })).not.toBeInTheDocument();
  });

  it("un envío de Genei lleva la etiqueta «Genei» y no se edita en línea", () => {
    render(<SatShippedCard order={order({ transport_status: "in_transit", sat_tab: "enviados",
                                          shipment_kind: "genei", courier: "GLS",
                                          genei: { shipment_code: "G1", courier: "GLS",
                                                   label_available: true, state_label: "En reparto" } })}
                           onChanged={() => {}} />);
    expect(screen.getByText("En reparto")).not.toHaveClass("courier-ext");
    expect(screen.getByText("Genei")).toHaveClass("sat-genei-tag");
    expect(screen.queryByRole("button", { name: /Editar courier/ })).not.toBeInTheDocument();
  });

  it("textos: «Enviado · X», «Enviado · otro courier», «Entregado · X»; Genei como siempre", () => {
    const ext = { shipment_kind: "externo" as const };
    expect(satShippedLabel(order({ ...ext, transport_status: "in_transit", courier: "UPS" })))
      .toBe("Enviado · UPS");
    expect(satShippedLabel(order({ ...ext, transport_status: "in_transit" })))
      .toBe("Enviado · otro courier");
    expect(satShippedLabel(order({ ...ext, transport_status: "delivered", courier: "MRW" })))
      .toBe("Entregado · MRW");
    expect(satShippedLabel(order({ ...ext, sin_envio: true }))).toBe("No requiere envío");
    expect(satShippedLabel(order({ shipment_kind: "genei", transport_status: "in_transit",
      genei: { shipment_code: "G", label_available: true, state_label: "En reparto" } })))
      .toBe("En reparto");
  });
});
