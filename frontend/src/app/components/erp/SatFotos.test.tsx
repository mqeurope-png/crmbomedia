import { render, screen } from "@testing-library/react";
import { SatPreparingCard } from "./SatPreparingCard";
import { SatReadyCard, SatShippedCard } from "./SatReadyCard";
import type { SatQueueItem, ShipmentFile } from "../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
jest.mock("../../lib/erpApi", () => ({
  customerLabel: () => "Cliente",
  STATUS_LABELS: {},
  attachDocument: jest.fn(),
  fetchShippingThumb: jest.fn(() => Promise.resolve(new Blob(["x"]))),
  listFotos: jest.fn(),
  openShippingFile: jest.fn(),
  listShippingFiles: jest.fn(),
  printShippingFile: jest.fn(),
  fireTransition: jest.fn(),
  markPickedUp: jest.fn(),
  setOrderTracking: jest.fn(),
  uploadShippingFile: jest.fn(),
  downloadOrderFactusolAlbaranPdf: jest.fn(),
  fetchAlbaranFromWoo: jest.fn(),
  openShippingFileBlob: jest.fn(),
  saveBlob: jest.fn(),
}));

beforeAll(() => {
  global.URL.createObjectURL = jest.fn(() => "blob:t");
  global.URL.revokeObjectURL = jest.fn();
});

const FOTO = {
  id: "f1", kind: "foto", source: "manual_upload", filename: "caja.jpg",
  mime_type: "image/jpeg", size_bytes: 1, uploaded_by_user_id: null,
  uploaded_at: "2026-10-07T10:00:00Z", download_url: "/x",
} as ShipmentFile;

function order(over: Partial<SatQueueItem> = {}): SatQueueItem {
  return {
    id: "o1", order_number: "BOPRIN-99977", contact_name: null, company_name: null,
    preparation_status: "packed", transport_status: "not_shipped", payment_status: "paid",
    total_amount: 10, currency: "EUR", lines: [], has_albaran: true, albaran_source: "file",
    has_albaran_file: true, albaran_file_source: "manual_upload", is_web_order: false,
    woo_albaran_available: false, woo_albaran_unavailable_reason: null, has_etiqueta: true,
    placed_at: "2026-10-07T10:00:00Z", fotos: [FOTO], ...over,
  } as SatQueueItem;
}

describe("Cola SAT — fotos del embalaje en todas las fases antes de «recogido»", () => {
  it.each([
    ["por embalar", "in_queue"],
    ["en preparación", "preparing"],
  ])("%s: se ve la foto y se puede añadir otra", async (_n, prep) => {
    render(<SatPreparingCard order={order({ preparation_status: prep as never,
                                            sat_tab: prep === "in_queue" ? "por_embalar"
                                              : "en_preparacion" })}
                             onChanged={jest.fn()} canShip />);
    expect(await screen.findByAltText("caja.jpg")).toBeInTheDocument();
    expect(screen.getByLabelText("Añadir foto del embalaje")).toBeInTheDocument();
  });

  it.each([["embalado", "embalados"], ["pendiente de recogida", "pendiente_recogida"]])(
    "%s: se ve la foto y se puede añadir otra", async (_n, tab) => {
      render(<SatReadyCard order={order({ sat_tab: tab as never })} onChanged={jest.fn()}
                           canShip />);
      expect(await screen.findByAltText("caja.jpg")).toBeInTheDocument();
      expect(screen.getByLabelText("Añadir foto del embalaje")).toBeInTheDocument();
    },
  );

  it("recogido: ya no se ofrece subir, pero lo subido se sigue viendo", async () => {
    render(<SatShippedCard order={order({ sat_tab: "enviados", transport_status: "in_transit" })} />);
    expect(await screen.findByAltText("caja.jpg")).toBeInTheDocument();
    expect(screen.queryByLabelText("Añadir foto del embalaje")).toBeNull();
  });

  it("sin permiso de envíos no se ofrece subir", async () => {
    render(<SatReadyCard order={order({ sat_tab: "embalados" })} onChanged={jest.fn()} />);
    expect(await screen.findByAltText("caja.jpg")).toBeInTheDocument();
    expect(screen.queryByLabelText("Añadir foto del embalaje")).toBeNull();
  });
});
