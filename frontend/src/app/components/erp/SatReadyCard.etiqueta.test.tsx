import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SatReadyCard } from "./SatReadyCard";
import type { SatQueueItem } from "../../lib/erpApi";
import { geneiFetchLabel } from "../../lib/geneiApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
jest.mock("../../lib/erpApi", () => ({
  customerLabel: jest.requireActual("../../lib/erpApi").customerLabel,
  downloadOrderFactusolAlbaranPdf: jest.fn(),
  fetchAlbaranFromWoo: jest.fn(),
  fireTransition: jest.fn(),
  listShippingFiles: jest.fn(),
  markPickedUp: jest.fn(),
  openShippingFile: jest.fn(),
  printShippingFile: jest.fn(),
  saveBlob: jest.fn(),
  setOrderTracking: jest.fn(),
  uploadShippingFile: jest.fn(),
  STATUS_LABELS: {},
}));
jest.mock("../../lib/geneiApi", () => ({
  ...jest.requireActual("../../lib/geneiApi"),
  geneiFetchLabel: jest.fn(),
}));
const mockFetchLabel = geneiFetchLabel as jest.Mock;

/** Embalado, envío Genei tramitado y SIN etiqueta adjunta (FLUXLA-5849). */
function order(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return {
    id: "o1", order_number: "FLUXLA-5849", contact_name: null, company_name: null,
    preparation_status: "packed", transport_status: "label_created", payment_status: "paid",
    total_amount: 100, currency: "EUR", lines: [],
    has_albaran: true, albaran_source: "file", has_albaran_file: true,
    albaran_file_source: "manual_upload", is_web_order: false,
    woo_albaran_available: false, woo_albaran_unavailable_reason: null,
    has_etiqueta: false, placed_at: "2026-10-07T10:00:00+00:00", sat_tab: "pendiente_recogida",
    genei: { shipment_code: "3B9QGGHO", state_bucket: "ready", state_label: "Tramitado",
             label_available: true,
             label_auto: { status: "esperando", attempts: 1, max_attempts: 5 } },
    ...over,
  } as SatQueueItem;
}

beforeEach(() => mockFetchLabel.mockReset());

describe("Cola SAT — traer la etiqueta de Genei sin ir a la ficha", () => {
  it("tramitado sin etiqueta: «Traer etiqueta de Genei» junto a «Subir etiqueta», con su estado", async () => {
    const onChanged = jest.fn();
    mockFetchLabel.mockResolvedValue({ state: {}, file: { id: "f1" } });
    const user = userEvent.setup();
    render(<SatReadyCard order={order()} onChanged={onChanged} />);
    expect(screen.getByText("🏷️ Subir etiqueta")).toBeInTheDocument();
    expect(screen.getByLabelText("Estado de la etiqueta")).toHaveTextContent(
      "Esperando a que Genei genere la etiqueta (intento 1 de 5): se trae sola.",
    );
    await user.click(screen.getByRole("button", { name: "📥 Traer etiqueta de Genei" }));
    await waitFor(() => expect(mockFetchLabel).toHaveBeenCalledWith("o1"));
    // Al refrescar la cola, la card pasa a «Imprimir etiqueta» (has_etiqueta).
    expect(onChanged).toHaveBeenCalled();
  });

  it("si Genei aún no la tiene, lo dice bajo la card", async () => {
    mockFetchLabel.mockRejectedValue(new Error("La etiqueta estará disponible tras pagar y tramitar el envío."));
    const user = userEvent.setup();
    render(<SatReadyCard order={order({ genei: { shipment_code: "3B9QGGHO",
      state_bucket: "ready", label_available: true,
      label_auto: { status: "agotada", attempts: 5 } } })} onChanged={jest.fn()} />);
    expect(screen.getByLabelText("Estado de la etiqueta"))
      .toHaveTextContent("Genei no ha dado la etiqueta sola: tráela a mano.");
    await user.click(screen.getByRole("button", { name: "📥 Traer etiqueta de Genei" }));
    expect(await screen.findByText(/disponible tras pagar y tramitar/)).toBeInTheDocument();
  });

  it("con la etiqueta ya adjunta: solo «Imprimir etiqueta», sin traer ni estado", () => {
    render(<SatReadyCard order={order({ has_etiqueta: true })} onChanged={jest.fn()} />);
    expect(screen.getByRole("button", { name: "🖨 Imprimir etiqueta" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "📥 Traer etiqueta de Genei" })).toBeNull();
    expect(screen.queryByLabelText("Estado de la etiqueta")).toBeNull();
  });

  it("envío sin tramitar o de otro courier: no ofrece traerla de Genei", () => {
    render(<SatReadyCard order={order({ genei: { shipment_code: "3B9QGGHO",
      state_bucket: "created", label_available: false } })} onChanged={jest.fn()} />);
    expect(screen.queryByRole("button", { name: "📥 Traer etiqueta de Genei" })).toBeNull();
    expect(screen.getByText("🏷️ Subir etiqueta")).toBeInTheDocument();
  });
});
