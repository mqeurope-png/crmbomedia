import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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
  it("con FACTURA se puede anular: avisa de que se desvincula y sigue en FACTUSOL", async () => {
    mockPreview.mockResolvedValue({
      can_cancel: true, blockers: [], warnings: [], factusol_docs: [],
      documents_to_unlink: [{
        kind: "factura", doc_type: "facturas", serie: 2, codigo: 526110,
        numero: "2-526110", label: "factura 2-526110",
        message: "La factura 2-526110 seguirá existiendo en FACTUSOL y dejará de estar "
          + "vinculada a este pedido. Si hay que anularla o abonarla, hazlo en FACTUSOL.",
      }],
    });
    mockCancel.mockResolvedValue({
      cancelled: true, factusol_delete_job_id: null, cancel_warnings: [],
      unlinked_documents: [{ kind: "factura", doc_type: "facturas", serie: 2, codigo: 526110,
        numero: "2-526110", label: "factura 2-526110" }],
    });
    const user = userEvent.setup();
    render(<CancelOrderModal orderId="o-3" orderNumber="MUESTRA-000003" onClose={jest.fn()} />);
    const aviso = await screen.findByRole("note", { name: "Documentos que se desvincularán" });
    expect(aviso).toHaveTextContent(
      "La factura 2-526110 seguirá existiendo en FACTUSOL y dejará de estar vinculada a "
      + "este pedido. Si hay que anularla o abonarla, hazlo en FACTUSOL.",
    );
    // Con factura no se ofrece borrar nada en FACTUSOL.
    expect(screen.queryByRole("checkbox")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Anular pedido" }));
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith("o-3", {
      confirm: true, reason: null, delete_factusol_docs: false,
    }));
    expect(await screen.findByText(/Desvinculado del pedido \(sigue en FACTUSOL\)/))
      .toHaveTextContent("factura 2-526110");
  });

  describe("✕, Esc, clic fuera y foco", () => {
    function Abridor() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Anular…</button>
          {open ? (
            <CancelOrderModal orderId="o-1" orderNumber="MANUAL-000777" onClose={() => setOpen(false)} />
          ) : null}
        </>
      );
    }

    it("el ✕ es visible y cierra; Esc cierra y el foco vuelve al botón que lo abrió", async () => {
      mockPreview.mockResolvedValue({ can_cancel: true, blockers: [], warnings: [], factusol_docs: [] });
      const user = userEvent.setup();
      render(<Abridor />);
      const abrir = screen.getByRole("button", { name: "Anular…" });
      await user.click(abrir);
      const dialog = screen.getByRole("dialog", { name: "Anular pedido MANUAL-000777" });
      expect(within(dialog).getByRole("button", { name: "Cerrar" })).toBeVisible();
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(abrir).toHaveFocus();

      await user.click(abrir);
      await user.click(screen.getByRole("button", { name: "Cerrar" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(abrir).toHaveFocus();
    });

    it("pulsar fuera cierra; mientras anula, ni Esc ni el ✕ ni la capa cierran", async () => {
      mockPreview.mockResolvedValue({ can_cancel: true, blockers: [], warnings: [], factusol_docs: [] });
      let resolve: (v: unknown) => void = () => undefined;
      mockCancel.mockReturnValue(new Promise((r) => { resolve = r; }));
      const onClose = jest.fn();
      const user = userEvent.setup();
      render(<CancelOrderModal orderId="o-1" orderNumber="MANUAL-000777" onClose={onClose} />);
      await user.click(await screen.findByRole("button", { name: "Anular pedido" }));
      const dialog = screen.getByRole("dialog", { name: "Anular pedido MANUAL-000777" });
      expect(within(dialog).getByRole("button", { name: "Cerrar" })).toBeDisabled();
      await user.keyboard("{Escape}");
      fireEvent.mouseDown(dialog);
      expect(onClose).not.toHaveBeenCalled();
      resolve({ cancelled: true, factusol_delete_job_id: null, factusol_docs_to_delete: [], cancel_warnings: [] });
      expect(await screen.findByRole("status")).toHaveTextContent(/Pedido anulado/);
      fireEvent.mouseDown(dialog);
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });
});
