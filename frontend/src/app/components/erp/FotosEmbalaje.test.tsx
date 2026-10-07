import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FotosEmbalaje, limpiarMiniaturas } from "./FotosEmbalaje";
import { ApiError } from "../../lib/api";
import {
  attachDocument,
  fetchShippingThumb,
  listFotos,
  openShippingFile,
  type ShipmentFile,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  attachDocument: jest.fn(),
  fetchShippingThumb: jest.fn(),
  listFotos: jest.fn(),
  openShippingFile: jest.fn(),
}));
const mockAttach = attachDocument as jest.Mock;
const mockThumb = fetchShippingThumb as jest.Mock;
const mockList = listFotos as jest.Mock;
const mockOpen = openShippingFile as jest.Mock;

function foto(id: string, filename: string, mime = "image/jpeg"): ShipmentFile {
  return {
    id, kind: "foto", source: "manual_upload", filename, mime_type: mime, size_bytes: 10,
    uploaded_by_user_id: null, uploaded_at: "2026-10-07T10:00:00Z",
    download_url: `/api/erp/orders/o1/shipping-files/${id}/download`,
  } as ShipmentFile;
}

beforeEach(() => {
  limpiarMiniaturas();
  [mockAttach, mockThumb, mockList, mockOpen].forEach((m) => m.mockReset());
  mockThumb.mockResolvedValue(new Blob(["x"], { type: "image/jpeg" }));
  global.URL.createObjectURL = jest.fn(() => "blob:thumb");
  global.URL.revokeObjectURL = jest.fn();
});

describe("Fotos del embalaje", () => {
  it("con varias, las enseña todas como miniaturas pulsables que abren la foto", async () => {
    const user = userEvent.setup();
    render(<FotosEmbalaje orderId="o1" canUpload={false}
                          fotos={[foto("f1", "uno.jpg"), foto("f2", "dos.jpg"),
                                  foto("f3", "doc.pdf", "application/pdf")]} />);
    expect(await screen.findByAltText("uno.jpg")).toHaveAttribute("src", "blob:thumb");
    expect(await screen.findByAltText("dos.jpg")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ver doc.pdf" })).toHaveTextContent("📄 doc.pdf");
    // La miniatura pide la versión pequeña, no la foto entera.
    expect(mockThumb).toHaveBeenCalledTimes(2);
    expect(mockThumb).toHaveBeenCalledWith("/api/erp/orders/o1/shipping-files/f1/download");
    await user.click(screen.getByRole("button", { name: "Ver dos.jpg" }));
    expect(mockOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "f2" }));
    // Sin permiso / ya recogido: no se ofrece subir.
    expect(screen.queryByText("📷 Añadir foto")).toBeNull();
  });

  it("un refresco de la cola (objetos nuevos, mismas fotos) no vuelve a pedir las miniaturas", async () => {
    const { rerender } = render(
      <FotosEmbalaje orderId="o1" canUpload={false} fotos={[foto("f1", "uno.jpg")]} />,
    );
    expect(await screen.findByAltText("uno.jpg")).toHaveAttribute("src", "blob:thumb");
    rerender(<FotosEmbalaje orderId="o1" canUpload={false}
                            fotos={[foto("f1", "uno.jpg"), foto("f2", "dos.jpg")]} />);
    expect(await screen.findByAltText("dos.jpg")).toBeInTheDocument();
    expect(screen.getByAltText("uno.jpg")).toHaveAttribute("src", "blob:thumb");
    expect(mockThumb).toHaveBeenCalledTimes(2);
  });

  it("subida correcta: dice «Foto adjuntada» y la enseña", async () => {
    const onChanged = jest.fn();
    mockAttach.mockResolvedValue({ file: foto("f9", "nueva.jpg") });
    const user = userEvent.setup();
    render(<FotosEmbalaje orderId="o1" canUpload fotos={[]} onChanged={onChanged} />);
    await user.upload(screen.getByLabelText("Añadir foto del embalaje"),
                      new File(["abc"], "nueva.jpg", { type: "image/jpeg" }));
    expect(await screen.findByText("Foto adjuntada.")).toBeInTheDocument();
    expect(await screen.findByAltText("nueva.jpg")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it.each([
    [new ApiError("Archivo vacío.", 400, "Archivo vacío."), "Archivo vacío."],
    [new ApiError("El archivo supera el máximo de 15 MB.", 413,
                  "El archivo supera el máximo de 15 MB."), "El archivo supera el máximo de 15 MB."],
    // El proxy corta sin el detalle de la API: el mensaje sigue siendo claro.
    [new ApiError("Error de la API (413)", 413, null), "El archivo es demasiado grande (máximo 12 MB)."],
    [new Error("Failed to fetch"), "Failed to fetch"],
  ])("un fallo se enseña y nunca dice «adjuntada» (%s)", async (err, texto) => {
    mockAttach.mockRejectedValue(err);
    const user = userEvent.setup();
    render(<FotosEmbalaje orderId="o1" canUpload fotos={[]} />);
    await user.upload(screen.getByLabelText("Añadir foto del embalaje"),
                      new File(["abc"], "x.jpg", { type: "image/jpeg" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(texto);
    expect(screen.queryByText("Foto adjuntada.")).toBeNull();
  });

  it("en la ficha las carga y avisa de las que se perdieron en un despliegue", async () => {
    mockList.mockResolvedValue({ items: [foto("f1", "uno.jpg")], fotos_perdidas: [
      { filename: "movil.jpg", uploaded_at: "2026-10-07T08:00:00Z" }] });
    render(<FotosEmbalaje orderId="o1" canUpload />);
    expect(await screen.findByAltText("uno.jpg")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(
      "Una foto se perdió en un despliegue (movil.jpg): vuelve a subirla si aún la tienes.",
    );
    await waitFor(() => expect(mockList).toHaveBeenCalledWith("o1"));
  });

  it("en una tarjeta sin fotos ni permiso no pinta nada", () => {
    const { container } = render(
      <FotosEmbalaje orderId="o1" canUpload={false} fotos={[]} compact />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
