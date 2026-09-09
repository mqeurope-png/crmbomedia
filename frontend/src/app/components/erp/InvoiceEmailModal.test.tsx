import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InvoiceEmailModal } from "./InvoiceEmailModal";
import {
  getInvoiceEmailPreview,
  sendInvoiceEmail,
  type InvoiceEmailPreview,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getInvoiceEmailPreview: jest.fn(),
  sendInvoiceEmail: jest.fn(),
  // PDF_LANGS lo importa el componente desde FactusolDocumentDetailModal, no
  // desde aquí, así que no hace falta mockearlo.
}));
const mockPreview = getInvoiceEmailPreview as jest.Mock;
const mockSend = sendInvoiceEmail as jest.Mock;

function preview(over: Partial<InvoiceEmailPreview> = {}): InvoiceEmailPreview {
  return {
    serie: 5,
    codigo: 63,
    numero: "5-000063",
    to: "cliente@ejemplo.fr",
    lang: "fr",
    lang_source: "pais_cliente",
    subject: "Facture 5-000063",
    body_text: "Bonjour,\n\nVeuillez trouver ci-joint votre facture.",
    from_alias: "ventas@bomedia.es",
    from_alias_source: "usuario",
    attachment_filename: "Factura_5-000063.pdf",
    reply_to_message_id: null,
    replies_to_thread: false,
    order_id: "ord-1",
    ...over,
  };
}

beforeEach(() => {
  mockPreview.mockReset();
  mockPreview.mockResolvedValue(preview());
  mockSend.mockReset();
  mockSend.mockResolvedValue({
    sent: true, message_id: "m1", thread_id: "t1",
    to: ["cliente@ejemplo.fr"], lang: "fr", numero: "5-000063",
    attachment_filename: "Factura_5-000063.pdf",
  });
});

describe("InvoiceEmailModal", () => {
  it("carga la previsualización: destinatario, idioma+origen, asunto, cuerpo y adjunto", async () => {
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    // destinatario propuesto (editable)
    expect(await screen.findByLabelText("Destinatario")).toHaveValue(
      "cliente@ejemplo.fr",
    );
    // idioma con su procedencia visible
    expect(screen.getByLabelText("Idioma del correo")).toHaveValue("fr");
    expect(screen.getByText(/del país del cliente/i)).toBeInTheDocument();
    // asunto + cuerpo del template
    expect(screen.getByLabelText("Asunto")).toHaveValue("Facture 5-000063");
    expect(screen.getByLabelText("Cuerpo del mensaje")).toHaveValue(
      preview().body_text,
    );
    // adjunto identificado por nombre
    expect(screen.getByText("Factura_5-000063.pdf")).toBeInTheDocument();
  });

  it("el botón de envío es SEPARADO y confirma explícitamente (confirm:true)", async () => {
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatario");
    // Nada se ha enviado con solo abrir la preview (no hay clic-único).
    expect(mockSend).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Enviar factura" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledTimes(1));
    expect(mockSend).toHaveBeenCalledWith(5, 63, expect.objectContaining({
      confirm: true,
      to: ["cliente@ejemplo.fr"],
      lang: "fr",
      from_alias: "ventas@bomedia.es",
    }));
  });

  it("cambiar el idioma re-traduce asunto/cuerpo conservando el destinatario", async () => {
    const user = userEvent.setup();
    mockPreview.mockImplementation((_s: number, _c: number, lang?: string) =>
      Promise.resolve(lang === "es"
        ? preview({ lang: "es", lang_source: "defecto", subject: "Factura 5-000063",
            body_text: "Hola,\n\nAdjuntamos su factura." })
        : preview()),
    );
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatario");
    // el usuario corrige el destinatario ANTES de cambiar idioma
    await user.clear(screen.getByLabelText("Destinatario"));
    await user.type(screen.getByLabelText("Destinatario"), "otro@ejemplo.fr");
    await user.selectOptions(screen.getByLabelText("Idioma del correo"), "es");
    // el asunto/cuerpo se re-traducen al español…
    await waitFor(() =>
      expect(screen.getByLabelText("Asunto")).toHaveValue("Factura 5-000063"));
    expect(screen.getByLabelText("Cuerpo del mensaje")).toHaveValue(
      "Hola,\n\nAdjuntamos su factura.");
    // …pero el destinatario editado NO se pierde
    expect(screen.getByLabelText("Destinatario")).toHaveValue("otro@ejemplo.fr");
  });

  it("tras enviar muestra el éxito y llama onSent", async () => {
    const onSent = jest.fn();
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} onSent={onSent} />);
    await screen.findByLabelText("Destinatario");
    await user.click(screen.getByRole("button", { name: "Enviar factura" }));
    expect(await screen.findByText(/Factura enviada a/i)).toBeInTheDocument();
    expect(onSent).toHaveBeenCalledWith({ to: ["cliente@ejemplo.fr"], lang: "fr" });
  });

  it("si el envío falla, enseña el error y NO marca como enviada", async () => {
    mockSend.mockRejectedValue(new Error("Gmail no conectado"));
    const onSent = jest.fn();
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} onSent={onSent} />);
    await screen.findByLabelText("Destinatario");
    await user.click(screen.getByRole("button", { name: "Enviar factura" }));
    expect(await screen.findByText(/Gmail no conectado/i)).toBeInTheDocument();
    expect(screen.queryByText(/Factura enviada a/i)).not.toBeInTheDocument();
    expect(onSent).not.toHaveBeenCalled();
    // el botón sigue disponible para reintentar
    expect(screen.getByRole("button", { name: "Enviar factura" })).toBeEnabled();
  });

  it("una dirección inválida deshabilita el envío", async () => {
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatario");
    await user.clear(screen.getByLabelText("Destinatario"));
    await user.type(screen.getByLabelText("Destinatario"), "no-es-un-email");
    expect(screen.getByRole("button", { name: "Enviar factura" })).toBeDisabled();
    expect(screen.getByText(/Revisa la dirección/i)).toBeInTheDocument();
  });

  it("cancelar llama onClose sin enviar", async () => {
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={onClose} />);
    await screen.findByLabelText("Destinatario");
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onClose).toHaveBeenCalled();
    expect(mockSend).not.toHaveBeenCalled();
  });
});
