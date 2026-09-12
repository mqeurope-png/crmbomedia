import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FactusolAlbaranPdfButton } from "./FactusolAlbaranPdfButton";
import { downloadOrderFactusolAlbaranPdf, saveBlob } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  downloadOrderFactusolAlbaranPdf: jest.fn(),
  saveBlob: jest.fn(),
}));
const mockDownload = downloadOrderFactusolAlbaranPdf as jest.Mock;
const mockSave = saveBlob as jest.Mock;

beforeEach(() => {
  mockDownload.mockReset();
  mockSave.mockReset();
});

describe("FactusolAlbaranPdfButton — «PDF del albarán (FACTUSOL)»", () => {
  it("descarga el PDF del albarán del pedido con el idioma elegido y lo guarda", async () => {
    const blob = new Blob(["%PDF-"], { type: "application/pdf" });
    mockDownload.mockResolvedValue(blob);
    const user = userEvent.setup();
    render(<FactusolAlbaranPdfButton orderId="o-1" numero="1-100327" lang="en" />);
    await user.click(screen.getByRole("button", { name: "PDF del albarán (FACTUSOL)" }));
    await waitFor(() => expect(mockDownload).toHaveBeenCalledWith("o-1", "en"));
    expect(mockSave).toHaveBeenCalledWith(blob, "Albaran_1-100327.pdf");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("si FACTUSOL no devuelve el PDF enseña el error sin romper nada", async () => {
    mockDownload.mockRejectedValue(new Error("El albarán 1-100327 ya no existe en FACTUSOL"));
    const onError = jest.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <FactusolAlbaranPdfButton orderId="o-1" numero="1-100327" />,
    );
    await user.click(screen.getByRole("button", { name: "PDF del albarán (FACTUSOL)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/ya no existe en FACTUSOL/);
    expect(mockSave).not.toHaveBeenCalled();
    // Con `onError` el mensaje va al padre (línea de error de la sección).
    rerender(<FactusolAlbaranPdfButton orderId="o-1" numero="1-100327" onError={onError} />);
    await user.click(screen.getByRole("button", { name: "PDF del albarán (FACTUSOL)" }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith(expect.stringMatching(/ya no existe/)));
  });
});
