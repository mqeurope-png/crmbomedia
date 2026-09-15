import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LinkDocumentOrderModal } from "./LinkDocumentOrderModal";
import { getDocumentLinkCandidates, linkDocumentToOrder } from "../../lib/erpApi";

/** Lote 2 · PR-2 — «Vincular a pedido» de un albarán / factura de FACTUSOL:
 *  sugerencias por referencia (fuerte) y por cliente (débil), búsqueda por nº
 *  de pedido, «Usar este» → confirmación explícita → escritura SOLO en el
 *  pedido de BoHub. Con conflicto conocido se avisa y se manda `force`. */

jest.mock("../../lib/erpApi", () => ({
  getDocumentLinkCandidates: jest.fn(),
  linkDocumentToOrder: jest.fn(),
}));
const mockCandidates = getDocumentLinkCandidates as jest.Mock;
const mockLink = linkDocumentToOrder as jest.Mock;

function candidate(over: Record<string, unknown> = {}) {
  return {
    id: "o1", order_number: "BOPRIN-99919", company_name: "Neon Led SL",
    total_amount: 186.34, placed_at: "2026-08-20T10:00:00+00:00",
    external_source: "woocommerce", current_link: null, match: "referencia",
    ...over,
  };
}

function response(over: Record<string, unknown> = {}) {
  return {
    doc: { doc_type: "facturas", serie: 5, codigo: 260066, numero: "5-260066",
           referencia: "BOP-099919", cliente_codigo: "2458",
           cliente_nombre: "DUPLICODER", fecha: "2026-08-21", total: 186.34 },
    linked_orders: [],
    candidates: [
      candidate(),
      candidate({ id: "o2", order_number: "MAN-7002", match: "cliente", total_amount: 50 }),
    ],
    q: null,
    ...over,
  };
}

beforeEach(() => {
  mockCandidates.mockReset();
  mockCandidates.mockResolvedValue(response());
  mockLink.mockReset();
  mockLink.mockResolvedValue({
    status: "linked", doc: { numero: "5-260066" },
    order: { id: "o1", order_number: "BOPRIN-99919" }, previous: null,
  });
});

function renderModal(props: Partial<React.ComponentProps<typeof LinkDocumentOrderModal>> = {}) {
  const onLinked = jest.fn();
  const onClose = jest.fn();
  render(
    <LinkDocumentOrderModal
      docType="facturas" serie={5} codigo={260066} numero="5-260066"
      onClose={onClose} onLinked={onLinked} {...props}
    />,
  );
  return { onLinked, onClose };
}

describe("LinkDocumentOrderModal", () => {
  it("lista las sugerencias por referencia y por cliente y vincula con confirmación", async () => {
    const user = userEvent.setup();
    const { onLinked } = renderModal();
    const porRef = await screen.findByRole("region", { name: "Por referencia" });
    expect(within(porRef).getByText("BOPRIN-99919")).toBeInTheDocument();
    const porCliente = screen.getByRole("region", { name: "Mismo cliente" });
    expect(within(porCliente).getByText("MAN-7002")).toBeInTheDocument();
    expect(mockCandidates).toHaveBeenCalledWith("facturas", 5, 260066);
    // Nada se escribe con solo abrir.
    expect(mockLink).not.toHaveBeenCalled();

    await user.click(within(porRef).getByRole("button", { name: "Usar este: BOPRIN-99919" }));
    // Confirmación explícita: el botón está deshabilitado hasta marcar.
    const vincular = screen.getByRole("button", { name: "Vincular" });
    expect(vincular).toBeDisabled();
    expect(screen.getByText(/FACTUSOL no se modifica/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("Confirmo el vínculo"));
    await user.click(vincular);
    await waitFor(() => expect(mockLink).toHaveBeenCalledWith(
      "facturas", 5, 260066, { order_id: "o1", confirm: true, force: undefined },
    ));
    expect(onLinked).toHaveBeenCalledWith({ id: "o1", order_number: "BOPRIN-99919" });
  });

  it("busca por nº de pedido y ofrece el resultado", async () => {
    const user = userEvent.setup();
    mockCandidates.mockResolvedValueOnce(response({ candidates: [] }));
    mockCandidates.mockResolvedValueOnce(response({
      q: "man-7", candidates: [candidate({ id: "o9", order_number: "MAN-7009", match: "busqueda" })],
    }));
    renderModal();
    expect(await screen.findByText(/Sin pedidos sugeridos/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Buscar pedido por número"), "man-7{Enter}");
    await waitFor(() => expect(mockCandidates).toHaveBeenLastCalledWith("facturas", 5, 260066, "man-7"));
    const grupo = await screen.findByRole("region", { name: "Por nº de pedido" });
    expect(within(grupo).getByRole("button", { name: "Usar este: MAN-7009" })).toBeInTheDocument();
  });

  it("con conflicto conocido avisa y manda `force`", async () => {
    const user = userEvent.setup();
    mockCandidates.mockResolvedValue(response({
      linked_orders: [{ id: "otro", order_number: "MAN-7777" }],
      candidates: [candidate({ current_link: "5-111111" })],
    }));
    renderModal({ docType: "albaranes", codigo: 91, numero: "5-000091" });
    expect(await screen.findByRole("status")).toHaveTextContent("Ya vinculado al pedido MAN-7777");
    expect(screen.getByText(/ya tiene albarán 5-111111/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Usar este: BOPRIN-99919" }));
    const avisos = screen.getAllByRole("alert");
    expect(avisos.map((a) => a.textContent).join(" ")).toMatch(/ya tiene albarán 5-111111/);
    expect(avisos.map((a) => a.textContent).join(" ")).toMatch(/ya está vinculado al pedido MAN-7777/);
    await user.click(screen.getByLabelText("Confirmo el vínculo"));
    await user.click(screen.getByRole("button", { name: "Vincular de todas formas" }));
    await waitFor(() => expect(mockLink).toHaveBeenCalledWith(
      "albaranes", 5, 91, { order_id: "o1", confirm: true, force: true },
    ));
  });

  it("enseña el error del backend sin cerrar", async () => {
    const user = userEvent.setup();
    mockLink.mockRejectedValue(new Error("El pedido MAN-7001 ya tiene factura 111111."));
    const { onLinked, onClose } = renderModal();
    await user.click(await screen.findByRole("button", { name: "Usar este: BOPRIN-99919" }));
    await user.click(screen.getByLabelText("Confirmo el vínculo"));
    await user.click(screen.getByRole("button", { name: "Vincular" }));
    expect(await screen.findByText(/ya tiene factura 111111/)).toBeInTheDocument();
    expect(onLinked).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
