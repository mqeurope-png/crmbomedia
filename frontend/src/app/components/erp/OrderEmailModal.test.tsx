import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OrderEmailModal } from "./OrderEmailModal";
import { getOrderEmailPreview, sendOrderEmail } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getOrderEmailPreview: jest.fn(),
  sendOrderEmail: jest.fn(),
}));

const mockPreview = getOrderEmailPreview as jest.Mock;
const mockSend = sendOrderEmail as jest.Mock;

const PREVIEW = {
  order_id: "o1",
  order_number: "PRO-000574",
  cliente: "Duplicoder SL",
  lang: "es" as const,
  to: ["taller@bomedia.net"],
  sat_email: "taller@bomedia.net",
  sat_configured: true,
  subject: "Pedido PRO-000574 — Duplicoder SL",
  body_text: "Adjuntamos el pedido PRO-000574.",
  from_alias: "ventas@bomedia.net",
  attachments: {
    albaran: { available: true, numero: "5-500004", reason: null, code: null },
    pedido: { available: true, numero: "1-574", reason: null, code: null,
              doc_type: "presupuestos" },
    factura: { available: false, numero: null, code: "factura_missing",
               reason: "El pedido aún no tiene factura emitida en FACTUSOL." },
  },
  defaults: { albaran: true, pedido: false, factura: false },
};

const SIN_ALBARAN = {
  ...PREVIEW,
  attachments: {
    ...PREVIEW.attachments,
    albaran: {
      available: false, numero: null, code: "albaran_missing",
      reason: "Este pedido aún no tiene albarán en FACTUSOL. Créalo con «Crear "
        + "albarán en FACTUSOL» o envía el correo sin él.",
    },
  },
  defaults: { albaran: false, pedido: false, factura: false },
};

beforeEach(() => {
  mockPreview.mockReset();
  mockPreview.mockResolvedValue(PREVIEW);
  mockSend.mockReset();
  mockSend.mockResolvedValue({
    sent: true, order_id: "o1", message_id: "msg-1", thread_id: "t1",
    to: ["taller@bomedia.net"], cc: [], bcc: [], lang: "es",
    attachments: ["albaran.pdf"], attachment_kinds: ["albaran"],
  });
});

describe("OrderEmailModal — enviar el pedido al SAT", () => {
  it("precarga el SAT, marca el albarán por defecto y envía con confirmación", async () => {
    const onSent = jest.fn();
    const user = userEvent.setup();
    render(<OrderEmailModal orderId="o1" orderNumber="PRO-000574" onClose={jest.fn()}
                            onSent={onSent} />);
    await waitFor(() => expect(mockPreview).toHaveBeenCalledWith("o1", undefined));

    expect(await screen.findByLabelText("Destinatarios")).toHaveValue("taller@bomedia.net");
    expect(screen.getByLabelText("Asunto")).toHaveValue("Pedido PRO-000574 — Duplicoder SL");
    // El albarán viene marcado; pedido y factura no.
    expect(screen.getByLabelText("Adjuntar albarán")).toBeChecked();
    expect(screen.getByLabelText("Adjuntar PDF del pedido")).not.toBeChecked();
    expect(screen.getByLabelText("Adjuntar factura")).not.toBeChecked();
    // La factura no está emitida: no se puede marcar, y se explica.
    expect(screen.getByLabelText("Adjuntar factura")).toBeDisabled();
    expect(screen.getByText(/aún no tiene factura emitida/)).toBeInTheDocument();
    // Hasta pulsar «Enviar pedido» no se manda nada.
    expect(mockSend).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Enviar pedido" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalled());
    expect(mockSend.mock.calls[0][1]).toMatchObject({
      confirm: true, to: ["taller@bomedia.net"], include_albaran: true,
      include_pedido: false, include_factura: false,
      from_alias: "ventas@bomedia.net",
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Pedido enviado a taller@bomedia.net",
    );
    expect(onSent).toHaveBeenCalled();
  });

  it("permite añadir destinatarios, CC/CCO y marcar el PDF del pedido", async () => {
    const user = userEvent.setup();
    render(<OrderEmailModal orderId="o1" onClose={jest.fn()} />);
    const to = await screen.findByLabelText("Destinatarios");
    await user.clear(to);
    await user.type(to, "taller@bomedia.net, otro@taller.example");
    await user.click(screen.getByRole("button", { name: /CC \/ CCO/ }));
    await user.type(screen.getByLabelText("CC"), "jefe@bomedia.net");
    await user.type(screen.getByLabelText("CCO"), "copia@bomedia.net");
    await user.click(screen.getByLabelText("Adjuntar PDF del pedido"));

    await user.click(screen.getByRole("button", { name: "Enviar pedido" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalled());
    expect(mockSend.mock.calls[0][1]).toMatchObject({
      to: ["taller@bomedia.net", "otro@taller.example"],
      cc: ["jefe@bomedia.net"],
      bcc: ["copia@bomedia.net"],
      include_albaran: true,
      include_pedido: true,
    });
  });

  it("sin albarán avisa, ofrece crearlo y deja enviar sin él", async () => {
    mockPreview.mockResolvedValue(SIN_ALBARAN);
    const onCreateAlbaran = jest.fn();
    const user = userEvent.setup();
    render(<OrderEmailModal orderId="o1" onClose={jest.fn()}
                            onCreateAlbaran={onCreateAlbaran} />);

    expect(await screen.findByText(/aún no tiene albarán en FACTUSOL/))
      .toBeInTheDocument();
    expect(screen.getByLabelText("Adjuntar albarán")).toBeDisabled();
    expect(screen.getByLabelText("Adjuntar albarán")).not.toBeChecked();
    // Sin ningún adjunto no se puede enviar.
    expect(screen.getByRole("button", { name: "Enviar pedido" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent(
      "Marca al menos un documento para adjuntar",
    );

    await user.click(screen.getByRole("button", { name: "Crear albarán en FACTUSOL" }));
    expect(onCreateAlbaran).toHaveBeenCalled();

    // O enviar sin él, con el PDF del pedido.
    await user.click(screen.getByLabelText("Adjuntar PDF del pedido"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Enviar pedido" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Enviar pedido" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalled());
    expect(mockSend.mock.calls[0][1]).toMatchObject({
      include_albaran: false, include_pedido: true,
    });
  });

  it("sin SAT configurado lo dice y no envía con una dirección inválida", async () => {
    mockPreview.mockResolvedValue({
      ...PREVIEW, to: [], sat_email: "", sat_configured: false,
    });
    const user = userEvent.setup();
    render(<OrderEmailModal orderId="o1" onClose={jest.fn()} />);
    expect(await screen.findByRole("note")).toHaveTextContent(
      /No hay email del SAT configurado/,
    );
    expect(screen.getByRole("button", { name: "Enviar pedido" })).toBeDisabled();

    await user.type(screen.getByLabelText("Destinatarios"), "no-es-un-email");
    expect(screen.getByText(/Revisa las direcciones/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enviar pedido" })).toBeDisabled();
    expect(mockSend).not.toHaveBeenCalled();
  });

  it("un fallo del envío se enseña y no se marca como enviado", async () => {
    mockSend.mockRejectedValue(new Error("Gmail no está conectado."));
    const user = userEvent.setup();
    render(<OrderEmailModal orderId="o1" onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatarios");
    await user.click(screen.getByRole("button", { name: "Enviar pedido" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(await dialog.findByText(/Gmail no está conectado/)).toBeInTheDocument();
    expect(screen.queryByText(/Pedido enviado a/)).not.toBeInTheDocument();
  });
});
