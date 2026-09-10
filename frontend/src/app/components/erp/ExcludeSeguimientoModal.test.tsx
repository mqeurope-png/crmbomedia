import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExcludeSeguimientoModal } from "./ExcludeSeguimientoModal";
import { previewExcludeSeguimiento } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  EXCLUSION_REASON_CODES: ["cancelado", "duplicado", "prueba", "reembolsado", "otro"],
  previewExcludeSeguimiento: jest.fn(),
}));

const ROW = { id: "ord-1", order_number: "FLUXLA-5749", cliente: "La Rueca" };

function preview(avisos: string[] = [], excluido = false) {
  return {
    ok: true,
    items: [{
      order_id: "ord-1", order_number: "FLUXLA-5749", cliente: "La Rueca",
      woo_status: "refunded", excluido, excluido_motivo: null, avisos,
    }],
    con_avisos: avisos.length ? 1 : 0,
    ya_excluidos: excluido ? 1 : 0,
  };
}

beforeEach(() => {
  (previewExcludeSeguimiento as jest.Mock).mockReset();
});

describe("ExcludeSeguimientoModal — quitar del seguimiento a mano", () => {
  it("sin nada aguas abajo: confirma con motivo rápido + texto", async () => {
    (previewExcludeSeguimiento as jest.Mock).mockResolvedValue(preview());
    const onConfirm = jest.fn();
    const user = userEvent.setup();
    render(<ExcludeSeguimientoModal rows={[ROW]} onConfirm={onConfirm} onCancel={() => {}} />);
    expect(
      screen.getByRole("heading", { name: "Quitar FLUXLA-5749 del seguimiento" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(previewExcludeSeguimiento).toHaveBeenCalledWith(["ord-1"]));
    const confirm = await screen.findByRole("button", { name: "Quitar del seguimiento (1)" });
    await waitFor(() => expect(confirm).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Duplicado" }));
    await user.type(screen.getByLabelText("Motivo"), "  era el 5782 ");
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith("era el 5782", "duplicado");
  });

  it("con factura/cobro: AVISA pero deja continuar («Quitar igualmente»)", async () => {
    (previewExcludeSeguimiento as jest.Mock).mockResolvedValue(
      preview(["facturado", "cobrado/pagado", "escrito en Drive"]),
    );
    const onConfirm = jest.fn();
    const user = userEvent.setup();
    render(<ExcludeSeguimientoModal rows={[ROW]} onConfirm={onConfirm} onCancel={() => {}} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("facturado, cobrado/pagado, escrito en Drive");
    const confirm = screen.getByRole("button", { name: "Quitar igualmente (1)" });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Reembolsado" }));
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith("", "reembolsado");
  });

  it("varios pedidos: título en plural, lista y un motivo común", async () => {
    (previewExcludeSeguimiento as jest.Mock).mockResolvedValue({
      ok: true, items: [], con_avisos: 0, ya_excluidos: 0,
    });
    const onConfirm = jest.fn();
    const user = userEvent.setup();
    render(
      <ExcludeSeguimientoModal
        rows={[ROW, { id: "ord-2", order_number: "BOPRIN-99922", cliente: "Mookase" }]}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("heading", { name: "Quitar 2 pedidos del seguimiento" }))
      .toBeInTheDocument();
    expect(screen.getByText("BOPRIN-99922")).toBeInTheDocument();
    const confirm = await screen.findByRole("button", { name: "Quitar del seguimiento (2)" });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Prueba" }));
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith("", "prueba");
  });

  it("si la comprobación falla, avisa y permite quitar igualmente", async () => {
    (previewExcludeSeguimiento as jest.Mock).mockRejectedValue(new Error("boom"));
    render(<ExcludeSeguimientoModal rows={[ROW]} onConfirm={() => {}} onCancel={() => {}} />);
    await screen.findByText(/No se pudieron comprobar los avisos/);
    expect(screen.getByRole("button", { name: "Quitar del seguimiento (1)" })).toBeEnabled();
  });

  it("cancelar invoca onCancel y usa la caja .modal-dialog", async () => {
    (previewExcludeSeguimiento as jest.Mock).mockResolvedValue(preview());
    const onCancel = jest.fn();
    const user = userEvent.setup();
    const { container } = render(
      <ExcludeSeguimientoModal rows={[ROW]} onConfirm={() => {}} onCancel={onCancel} />,
    );
    expect(container.querySelector(".modal-dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onCancel).toHaveBeenCalled();
  });
});
