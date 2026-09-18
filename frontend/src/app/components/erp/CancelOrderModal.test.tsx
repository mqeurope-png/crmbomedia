import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CancelOrderModal } from "./CancelOrderModal";
import { cancelOrder, previewCancelOrder } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  previewCancelOrder: jest.fn(),
  cancelOrder: jest.fn(),
}));
const mockPreview = previewCancelOrder as jest.Mock;
const mockCancel = cancelOrder as jest.Mock;

const DOCS = [
  { doc_type: "albaranes", serie: 5, codigo: 500010, numero: "5-500010",
    estado: 0, deletable: true, reason: null },
  { doc_type: "presupuestos", serie: 1, codigo: 9001, numero: "1-009001",
    estado: 1, deletable: false, reason: "Ya no está pendiente en FACTUSOL (ESTPRE=1)." },
];

beforeEach(() => {
  mockPreview.mockReset();
  mockCancel.mockReset();
  mockCancel.mockResolvedValue({ cancelled: true, factusol_delete_job_id: "job-1",
    factusol_docs_to_delete: [DOCS[0]], cancel_warnings: [] });
});

describe("CancelOrderModal", () => {
  it("avisa con los documentos FACTUSOL y anula con confirmación explícita (confirm:true)", async () => {
    mockPreview.mockResolvedValue({ can_cancel: true, blockers: [], warnings: [], factusol_docs: DOCS });
    const user = userEvent.setup();
    const onDone = jest.fn();
    render(<CancelOrderModal orderId="o-1" orderNumber="MANUAL-000777" onClose={jest.fn()} onDone={onDone} />);
    const list = await screen.findByRole("list", { name: "Documentos FACTUSOL del pedido" });
    expect(list).toHaveTextContent("Albarán 5-500010 — se puede borrar");
    expect(list).toHaveTextContent("Presupuesto / proforma 1-009001 — no se borra: Ya no está pendiente");
    // Nada se ha anulado con solo abrir el modal.
    expect(mockCancel).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("Motivo de la anulación"), "se echa atrás");
    await user.click(screen.getByRole("button", { name: "Anular pedido" }));
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith("o-1", {
      confirm: true, reason: "se echa atrás", delete_factusol_docs: true,
    }));
    expect(await screen.findByRole("status")).toHaveTextContent(/Pedido anulado/);
    expect(screen.getByText(/Borrado en FACTUSOL encolado/)).toHaveTextContent("Albarán 5-500010");
    expect(onDone).toHaveBeenCalled();
  });

  it("desmarcar «Borrar también en FACTUSOL» anula sin borrar nada allí", async () => {
    mockPreview.mockResolvedValue({ can_cancel: true, blockers: [], warnings: [], factusol_docs: DOCS });
    const user = userEvent.setup();
    render(<CancelOrderModal orderId="o-1" orderNumber="MANUAL-000777" onClose={jest.fn()} />);
    await screen.findByRole("list", { name: "Documentos FACTUSOL del pedido" });
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Anular pedido" }));
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith("o-1", expect.objectContaining({
      delete_factusol_docs: false,
    })));
  });

  it("web sin factura: el pedido de cliente F_PCL sale como borrable", async () => {
    mockPreview.mockResolvedValue({
      can_cancel: true, blockers: [], warnings: [],
      factusol_docs: [{ doc_type: "pedidos", serie: 5, codigo: 7001,
        numero: "5-007001", estado: 0, deletable: true, reason: null }],
    });
    render(<CancelOrderModal orderId="o-9" orderNumber="FLUXLA-1" onClose={jest.fn()} />);
    const list = await screen.findByRole("list", { name: "Documentos FACTUSOL del pedido" });
    expect(list).toHaveTextContent("Pedido de cliente 5-007001 — se puede borrar");
  });

  it("web con factura: avisa de anulación manual y no bloquea", async () => {
    mockPreview.mockResolvedValue({
      can_cancel: true, blockers: [],
      warnings: ["Este pedido tiene factura en FACTUSOL (260090). BoHub no la "
        + "toca: anúlala o abónala manualmente en FACTUSOL."],
      factusol_docs: [],
    });
    render(<CancelOrderModal orderId="o-9" orderNumber="FLUXLA-2" onClose={jest.fn()} />);
    expect(await screen.findByText(/anúlala o abónala manualmente en FACTUSOL/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Anular pedido" })).not.toBeDisabled();
  });

  it("con bloqueos (factura / web) no deja anular", async () => {
    mockPreview.mockResolvedValue({
      can_cancel: false, blockers: ["Tiene factura en FACTUSOL (260090): la factura se anula desde FACTUSOL."],
      warnings: [], factusol_docs: [],
    });
    render(<CancelOrderModal orderId="o-1" orderNumber="MANUAL-000777" onClose={jest.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Tiene factura en FACTUSOL/);
    expect(screen.getByRole("button", { name: "Anular pedido" })).toBeDisabled();
    expect(mockCancel).not.toHaveBeenCalled();
  });
});
