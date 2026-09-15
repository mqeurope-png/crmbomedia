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
  STATUS_LABELS: {},
}));
const mockFetch = fetchAlbaranFromWoo as jest.Mock;
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
  mockList.mockResolvedValue([]);
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
