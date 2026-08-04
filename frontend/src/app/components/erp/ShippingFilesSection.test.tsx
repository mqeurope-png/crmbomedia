import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ShippingFilesSection } from "./ShippingFilesSection";
import {
  fetchAlbaranFromWoo,
  listShippingFiles,
  openShippingFile,
  uploadShippingFile,
  type ShipmentFile,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  listShippingFiles: jest.fn(),
  uploadShippingFile: jest.fn(),
  fetchAlbaranFromWoo: jest.fn(),
  openShippingFile: jest.fn(),
}));
const mockList = listShippingFiles as jest.Mock;
const mockUpload = uploadShippingFile as jest.Mock;
const mockFetchWoo = fetchAlbaranFromWoo as jest.Mock;
const mockOpen = openShippingFile as jest.Mock;

function file(kind: "albaran" | "etiqueta"): ShipmentFile {
  return {
    id: `f-${kind}`, kind, source: "manual_upload", filename: `${kind}.pdf`,
    mime_type: "application/pdf", size_bytes: 10, uploaded_by_user_id: null,
    uploaded_at: null, download_url: `/api/erp/orders/o1/shipping-files/f-${kind}/download`,
  };
}

beforeEach(() => {
  mockList.mockReset();
  mockUpload.mockReset();
  mockFetchWoo.mockReset();
  mockOpen.mockReset();
  mockList.mockResolvedValue([]);
});

describe("ShippingFilesSection", () => {
  it("pedido NO Woo sin ficheros: solo botones de subida (sin Descargar de Woo)", async () => {
    render(<ShippingFilesSection orderId="o1" isWooOrder={false} />);
    expect(await screen.findByRole("button", { name: "Subir albarán" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Subir etiqueta" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Descargar albarán de Woo/ }),
    ).not.toBeInTheDocument();
  });

  it("pedido Woo sin albarán: ofrece Descargar de Woo", async () => {
    render(<ShippingFilesSection orderId="o1" isWooOrder />);
    expect(
      await screen.findByRole("button", { name: /Descargar albarán de Woo/ }),
    ).toBeInTheDocument();
  });

  it("con albarán presente: Ver albarán + Reemplazar, y abre el PDF al pulsar", async () => {
    mockList.mockResolvedValue([file("albaran")]);
    const user = userEvent.setup();
    render(<ShippingFilesSection orderId="o1" isWooOrder />);
    const ver = await screen.findByRole("button", { name: "Ver albarán" });
    expect(screen.getByRole("button", { name: "Reemplazar albarán" })).toBeInTheDocument();
    await user.click(ver);
    expect(mockOpen).toHaveBeenCalledWith(expect.objectContaining({ kind: "albaran" }));
  });

  it("con etiqueta presente: Ver etiqueta", async () => {
    mockList.mockResolvedValue([file("etiqueta")]);
    render(<ShippingFilesSection orderId="o1" isWooOrder={false} />);
    expect(await screen.findByRole("button", { name: "Ver etiqueta" })).toBeInTheDocument();
  });

  it("descargar de Woo llama al endpoint y refresca", async () => {
    mockFetchWoo.mockResolvedValue({ file: file("albaran"), already_present: false });
    const user = userEvent.setup();
    render(<ShippingFilesSection orderId="o1" isWooOrder />);
    await user.click(await screen.findByRole("button", { name: /Descargar albarán de Woo/ }));
    await waitFor(() => expect(mockFetchWoo).toHaveBeenCalledWith("o1"));
    // Refresca la lista tras descargar.
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });
});
