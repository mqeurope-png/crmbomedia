import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SatReadyCard } from "./SatReadyCard";
import type { SatQueueItem } from "../../lib/erpApi";
import {
  fireTransition,
  listShippingFiles,
  markPickedUp,
  openShippingFile,
} from "../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));

jest.mock("../../lib/erpApi", () => ({
  fireTransition: jest.fn(),
  listShippingFiles: jest.fn(),
  markPickedUp: jest.fn(),
  openShippingFile: jest.fn(),
}));
const mockFire = fireTransition as jest.Mock;
const mockList = listShippingFiles as jest.Mock;
const mockPicked = markPickedUp as jest.Mock;
const mockOpen = openShippingFile as jest.Mock;

function order(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return {
    id: "o1", order_number: "BOP-1", preparation_status: "packed",
    transport_status: "not_shipped", payment_status: "paid",
    total_amount: 100, currency: "EUR", lines: [],
    has_albaran: true, has_etiqueta: true, ...over,
  };
}

beforeEach(() => {
  mockFire.mockReset();
  mockList.mockReset();
  mockPicked.mockReset();
  mockOpen.mockReset();
  mockList.mockResolvedValue([]);
});

describe("SatReadyCard", () => {
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

  it("sin albarán muestra «Falta albarán» enlazando a la ficha", () => {
    render(<SatReadyCard order={order({ has_albaran: false })} onChanged={() => {}} />);
    const link = screen.getByRole("link", { name: /Falta albarán/ });
    expect(link).toHaveAttribute("href", "/erp/orders/o1");
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
});
