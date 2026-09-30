import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DesvincularDocumentoModal } from "./DesvincularDocumentoModal";
import { unlinkOrderDocument } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({ unlinkOrderDocument: jest.fn() }));

const FACTURA = {
  kind: "factura" as const, doc_type: "facturas" as const, serie: 2, codigo: 526110,
  numero: "2-526110", label: "factura 2-526110",
};

beforeEach(() => (unlinkOrderDocument as jest.Mock).mockReset());

describe("DesvincularDocumentoModal", () => {
  it("muestra sin más documentos: avisa de que vuelve a modo muestra y desvincula", async () => {
    (unlinkOrderDocument as jest.Mock).mockResolvedValue({ unlinked: { back_to_sample: true } });
    const onDone = jest.fn();
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<DesvincularDocumentoModal orderId="o-3" orderNumber="MUESTRA-000003"
                                      document={FACTURA} bornAsSample otherDocuments={false}
                                      onClose={onClose} onDone={onDone} />);
    const nota = screen.getByRole("note");
    expect(nota).toHaveTextContent("Factura 2-526110 seguirá existiendo en FACTUSOL");
    expect(nota).toHaveTextContent("No se escribe nada en FACTUSOL");
    expect(nota).toHaveTextContent("vuelve a modo muestra (no facturable, 0 €");
    await user.click(screen.getByRole("button", { name: "Desvincular" }));
    await waitFor(() => expect(unlinkOrderDocument).toHaveBeenCalledWith("o-3", "factura"));
    expect(onDone).toHaveBeenCalledWith({ unlinked: { back_to_sample: true } });
    expect(onClose).toHaveBeenCalled();
  });

  it("pedido normal: sin aviso de modo muestra; un error se enseña", async () => {
    (unlinkOrderDocument as jest.Mock).mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    render(<DesvincularDocumentoModal orderId="o-1" orderNumber="MANUAL-000901"
                                      document={FACTURA} bornAsSample={false} otherDocuments={false}
                                      onClose={jest.fn()} />);
    expect(screen.getByRole("note")).not.toHaveTextContent("modo muestra");
    await user.click(screen.getByRole("button", { name: "Desvincular" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
