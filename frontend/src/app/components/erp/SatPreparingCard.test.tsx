import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SatPreparingCard } from "./SatPreparingCard";
import type { SatQueueItem } from "../../lib/erpApi";
import {
  fetchAlbaranFromWoo,
  listShippingFiles,
  openShippingFile,
} from "../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));

jest.mock("../../lib/erpApi", () => ({
  fetchAlbaranFromWoo: jest.fn(),
  listShippingFiles: jest.fn(),
  openShippingFile: jest.fn(),
  STATUS_LABELS: {},
}));
const mockFetch = fetchAlbaranFromWoo as jest.Mock;
const mockList = listShippingFiles as jest.Mock;
const mockOpen = openShippingFile as jest.Mock;

function order(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return {
    id: "o1", order_number: "BOP-1", preparation_status: "preparing",
    transport_status: "not_shipped", payment_status: "paid",
    total_amount: 100, currency: "EUR", lines: [],
    has_albaran: false, has_etiqueta: false, ...over,
  };
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
  mockList.mockResolvedValue([]);
});

describe("SatPreparingCard", () => {
  it("sin albarán muestra el chip «Descargar albarán»", () => {
    render(<SatPreparingCard order={order({ has_albaran: false })} onChanged={() => {}} />);
    expect(screen.getByRole("button", { name: /Descargar albarán/ })).toBeInTheDocument();
  });

  it("con albarán muestra «Imprimir albarán» y abre el PDF", async () => {
    mockList.mockResolvedValue([FILE]);
    const user = userEvent.setup();
    render(<SatPreparingCard order={order({ has_albaran: true })} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /Imprimir albarán/ }));
    await waitFor(() => expect(mockList).toHaveBeenCalledWith("o1", "albaran"));
    await waitFor(() => expect(mockOpen).toHaveBeenCalled());
  });

  it("«Descargar albarán» dispara fetch-from-woo, refresca y auto-abre el PDF", async () => {
    mockFetch.mockResolvedValue({ file: FILE, already_present: false });
    const onChanged = jest.fn();
    const user = userEvent.setup();
    render(<SatPreparingCard order={order({ has_albaran: false })} onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: /Descargar albarán/ }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith("o1"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    await waitFor(() => expect(mockOpen).toHaveBeenCalledWith(FILE));
  });

  it("si la descarga falla muestra aviso con enlace a la ficha", async () => {
    mockFetch.mockRejectedValue(new Error("502"));
    const user = userEvent.setup();
    render(<SatPreparingCard order={order({ has_albaran: false })} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /Descargar albarán/ }));
    expect(await screen.findByText(/Sube el albarán a mano/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Ir a la ficha/ })).toHaveAttribute("href", "/erp/orders/o1");
  });
});
